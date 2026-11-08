"""Registered, read-only public connectors with one request boundary."""
import http.client
import ipaddress
import json
import re
import socket
import ssl
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from dataset_store import DatasetError
from privacy_policy import require_external_network

SINA_HS300 = 'https://finance.sina.com.cn/realstock/company/sh000300/hisdata/klc_kl.js'
OPEN_METEO = 'https://archive-api.open-meteo.com/v1/archive'
GLOBAL_V6 = ipaddress.ip_network('2000::/3')
RESERVED_V6 = tuple(map(ipaddress.ip_network, ('2001::/23', '2001:db8::/32', '2002::/16', '3fff::/20')))
CONNECTORS = {
    'sina_hs300': (SINA_HS300, {'d'}, 8_000_000),
    'open_meteo_era5_daily': (
        OPEN_METEO,
        {'latitude', 'longitude', 'start_date', 'end_date', 'daily', 'models', 'timezone'},
        2_000_000,
    ),
    'fred_series': ('https://api.stlouisfed.org/fred/series', {'series_id','api_key','file_type'}, 1_000_000),
    'fred_observations': ('https://api.stlouisfed.org/fred/series/observations',
        {'series_id','api_key','file_type','observation_start','observation_end'}, 4_000_000),
    'bea_nipa': ('https://apps.bea.gov/api/data/',
        {'UserID','method','DataSetName','TableName','Frequency','Year','ResultFormat'}, 4_000_000),
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise DatasetError('SOURCE_REDIRECT', 'Connector does not follow redirects')


class PublicConnection(http.client.HTTPSConnection):
    def connect(self):
        require_external_network()
        candidates = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        if not candidates:
            raise DatasetError('NETWORK_UNREACHABLE', 'No public connector address')
        for _, _, _, _, address in candidates:
            ip = ipaddress.ip_address(address[0])
            if (not ip.is_global or ip.is_multicast or
                    ip.version == 6 and (ip not in GLOBAL_V6 or any(ip in block for block in RESERVED_V6))):
                raise DatasetError('NETWORK_DENIED', 'Connector resolved to a non-public address')

        def connect_pinned(_address, timeout, source_address):
            require_external_network()
            last_error = None
            for family, socktype, proto, _, sockaddr in candidates:
                stream = socket.socket(family, socktype, proto)
                try:
                    stream.settimeout(timeout)
                    if source_address:
                        stream.bind(source_address)
                    stream.connect(sockaddr)
                    return stream
                except OSError as error:
                    stream.close()
                    last_error = error
            raise last_error

        self._create_connection = connect_pinned
        # The original hostname remains in the TLS handshake and certificate check.
        super().connect()


class PublicHTTPS(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(PublicConnection, request, context=ssl.create_default_context())


def public_get(connector, parameters, *, opener=None):
    require_external_network()
    if connector not in CONNECTORS:
        raise DatasetError('NETWORK_DENIED', 'External source is not registered')
    endpoint, fields, limit = CONNECTORS[connector]
    if set(parameters) != fields:
        raise DatasetError('INVALID_ARGUMENT', 'Unexpected connector parameters')
    if connector.startswith('fred_'):
        if (not re.fullmatch(r'[A-Z0-9_]{1,64}',parameters['series_id']) or
            not re.fullmatch(r'[a-z0-9]{32}',parameters['api_key']) or parameters['file_type']!='json' or
            any(not re.fullmatch(r'\d{4}-\d{2}-\d{2}',parameters[key])
                for key in ('observation_start','observation_end') if key in parameters)):
            raise DatasetError('INVALID_ARGUMENT','Invalid registered FRED request')
    if connector=='bea_nipa':
        if (not re.fullmatch(r'[a-fA-F0-9-]{36}',parameters['UserID']) or
            not re.fullmatch(r'T\d{5}',parameters['TableName']) or
            not re.fullmatch(r'\d{4}(?:,\d{4}){0,70}',parameters['Year']) or
            parameters['Frequency'] not in ('M','Q','A') or
            (parameters['method'],parameters['DataSetName'],parameters['ResultFormat'])!=('GetData','NIPA','JSON')):
            raise DatasetError('INVALID_ARGUMENT','Invalid registered BEA request')
    return _request(endpoint,parameters,limit,opener=opener)


def world_bank(indicator, country=None, years=None, *, opener=None):
    if (len(indicator)>40 or not re.fullmatch(r'[A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)+',indicator) or
        country and not re.fullmatch(r'[A-Z]{2,3}',country)):
        raise DatasetError('INVALID_ARGUMENT','Invalid World Bank indicator or country code')
    if country and (not isinstance(years,str) or not re.fullmatch(r'\d{4}:\d{4}',years)):
        raise DatasetError('INVALID_ARGUMENT','Invalid World Bank time interval')
    endpoint='https://api.worldbank.org/v2/'+(f'country/{country}/' if country else '')+f'indicator/{indicator}'
    return _request(endpoint,{'format':'json','per_page':100,**({'date':years} if country else {})},4_000_000,opener=opener)


def bls_series(indicator,start,end,*,opener=None):
    if not re.fullmatch(r'[A-Z0-9]{3,30}',indicator) or not 1900<=start<=end<=2100 or end-start>=10:
        raise DatasetError('INVALID_ARGUMENT','BLS requests require a series and at most ten calendar years')
    body={'seriesid':[indicator],'startyear':str(start),'endyear':str(end)}
    return _request('https://api.bls.gov/publicAPI/v2/timeseries/data/',{},4_000_000,body=body,opener=opener)


def _request(endpoint,parameters,limit,*,body=None,opener=None):
    require_external_network()
    url=endpoint+('?' + urlencode(parameters) if parameters else '')
    headers={'User-Agent':'Nomisma local research'}
    if body is not None:headers['Content-Type']='application/json'
    request=Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    client = opener or build_opener(ProxyHandler({}), NoRedirect(), PublicHTTPS())
    try:
        with client.open(request, timeout=15) as response:
            raw = response.read(limit + 1)
    except DatasetError:
        raise
    except HTTPError as error:
        code='SOURCE_AUTH' if error.code in (401,403) else 'SOURCE_RATE_LIMIT' if error.code==429 else 'UPSTREAM_RESPONSE_INVALID'
        raise DatasetError(code, 'Public source HTTP request failed') from None
    except TimeoutError as error:
        raise DatasetError('NETWORK_TIMEOUT', 'Public source request timed out') from error
    except OSError as error:
        raise DatasetError('NETWORK_UNREACHABLE', 'Public source request failed') from error
    if len(raw) > limit:
        raise DatasetError('RESOURCE_LIMIT', 'Public source response exceeds budget')
    return raw
