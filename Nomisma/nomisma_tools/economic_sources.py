"""Registered macro series, immutable source snapshots and canonical observations."""
import datetime as dt
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

from dataset_store import DatasetError, utc_now
from outbound_policy import bls_series, public_get, world_bank
from privacy_policy import require_external_network

SOURCES = {
    'nbs': ('https://data.stats.gov.cn/', 'official_export'),
    'fred': ('https://fred.stlouisfed.org/', 'credential'),
    'bea': ('https://apps.bea.gov/API/docs/index.htm', 'credential'),
    'bls': ('https://www.bls.gov/developers/', 'public'),
    'world_bank': ('https://data.worldbank.org/', 'public'),
}
SERIES = {
    'nbs': {
        'CPI': ('居民消费价格指数 CPI 通胀', 'monthly', None), 'PPI': ('工业生产者出厂价格指数 PPI', 'monthly', None),
        'GDP': ('GDP 三次产业 最终消费 资本形成', 'quarterly', None),
        'INDUSTRY': ('规模以上工业增加值 工业生产', 'monthly', None),
        'INVESTMENT': ('固定资产投资 房地产开发投资', 'monthly', None),
        'RETAIL': ('社会消费品零售总额', 'monthly', None),
        'UNEMPLOYMENT': ('城镇调查失业率 就业', 'monthly', None),
        'TRADE': ('进出口总额 贸易差额', 'monthly', None),
        'INCOME': ('人均可支配收入 居民消费支出', 'quarterly', None),
    },
    'fred': {
        'GDP': ('Nominal GDP 国内生产总值', 'quarterly', None),
        'CPIAUCSL': ('CPI 消费价格 通胀 inflation', 'monthly', None),
        'PCEPI': ('PCE 价格 通胀 inflation', 'monthly', None),
        'FEDFUNDS': ('Federal funds rate 联邦基金利率 货币政策', 'monthly', None),
        'UNRATE': ('Unemployment 失业率', 'monthly', None),
        'PAYEMS': ('Nonfarm payrolls 非农就业', 'monthly', None),
        'DGS10': ('10-year Treasury yield 国债收益率', 'daily', None),
        'M2SL': ('M2 money supply 货币供应', 'monthly', None),
        'INDPRO': ('Industrial production 工业生产', 'monthly', None),
    },
    'bea': {
        'T10105:1': ('Nominal GDP 国内生产总值', 'quarterly', None),
        'T10101:1': ('Real GDP growth 经济增长', 'quarterly', None),
        'T20600:1': ('Personal income 个人收入', 'monthly', None),
    },
    'bls': {
        'CUUR0000SA0': ('CPI-U 美国消费者价格指数 通胀', 'monthly', 'Index 1982–84=100'),
        'WPUFD4': ('PPI final demand 生产者价格', 'monthly', 'Index Nov 2009=100'),
        'LNS14000000': ('Unemployment rate 失业率', 'monthly', 'Percent'),
        'CES0000000001': ('Total nonfarm employment 非农就业', 'monthly', 'Thousands of persons'),
        'CES0500000003': ('Average hourly earnings 平均时薪', 'monthly', 'USD per hour'),
        'PRS85006092': ('Nonfarm business labor productivity growth 劳动生产率', 'quarterly', None),
    },
    'world_bank': {
        'NY.GDP.MKTP.CD': ('GDP current US$ 国内生产总值', 'annual', 'Current USD'),
        'SP.POP.TOTL': ('Population 人口', 'annual', 'Persons'),
        'FP.CPI.TOTL.ZG': ('Inflation, consumer prices 通胀', 'annual', 'Annual percent'),
        'SL.UEM.TOTL.ZS': ('Unemployment 失业率', 'annual', 'Percent of labor force, modeled ILO estimate'),
        'NE.TRD.GNFS.ZS': ('Trade 贸易', 'annual', 'Percent of GDP'),
    },
}
COUNTRIES = {'nbs':'CHN','fred':'USA','bea':'USA','bls':'USA'}
COUNTRY_WORDS={'CHN':'CHN China 中国','USA':'USA US United States 美国'}


def discover(query='', source=None, country=None):
    tokens=query.casefold().split()
    return [describe(provider, code, country) for provider, entries in SERIES.items()
            if (not source or source==provider) and (not country or provider not in COUNTRIES or COUNTRIES[provider]==country)
            for code, (title, _, _) in entries.items()
            if all(token in (code+' '+title+' '+COUNTRY_WORDS.get(COUNTRIES.get(provider),'')).casefold()
                   for token in tokens)][:12]


def describe(source, code, country=None):
    try:title, frequency, unit=SERIES[source][code]
    except KeyError:raise DatasetError('UNKNOWN_INDICATOR','Choose a series from the economic catalog') from None
    fixed=COUNTRIES.get(source)
    if fixed and country and country!=fixed:
        raise DatasetError('INVALID_ARGUMENT','The chosen series belongs to another country')
    return {'source':source,'indicator_id':code,'indicator_name':title,'description':None,'frequency':frequency,
            'unit':unit,'country_or_region':fixed or country,'source_url':SOURCES[source][0],
            'access':SOURCES[source][1],'seasonal_adjustment':None,'last_updated':None}


def status(source, root):
    url,access=SOURCES[source]
    if access=='credential':
        try:_credential(root,source)
        except DatasetError:return {'source':source,'status':'credential_required','source_url':url}
    return {'source':source,'status':access if access=='official_export' else 'ready_to_request',
            'source_url':url,'live_connection_verified':False}


def _credential(root, source):
    if not root or sys.platform!='win32':
        raise DatasetError('SOURCE_CREDENTIAL_REQUIRED','Configure the data source credential in Settings')
    directory=Path(root)/'data-source-credentials'
    file=directory/(source+'.secret')
    try:
        if directory.is_symlink() or file.is_symlink() or not file.is_file() or file.stat().st_size>16384:
            raise ValueError()
        helper=Path(__file__).resolve().parents[1]/'launcher/credential_dpapi.py'
        proc=subprocess.run([sys.executable,'-I','-S','-B',str(helper),'decrypt'],input=file.read_bytes(),
                            capture_output=True,timeout=10,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
        key=proc.stdout.decode('ascii')
        pattern=r'[a-z0-9]{32}' if source=='fred' else r'[a-fA-F0-9-]{36}'
        if not re.fullmatch(pattern,key):raise ValueError()
        return key
    except (OSError, ValueError, subprocess.SubprocessError):
        raise DatasetError('SOURCE_CREDENTIAL_REQUIRED','Configure the data source credential in Settings') from None


def _json(raw):
    try:return json.loads(raw)
    except (ValueError, UnicodeError):raise DatasetError('SOURCE_SCHEMA','Expected a JSON data response') from None


def _date(value):
    try:return dt.date.fromisoformat(value)
    except (TypeError, ValueError):raise DatasetError('INVALID_ARGUMENT','Enter an ISO date') from None


def _number(value):
    if value in (None,'','.','NA','(NA)','(D)'):return None
    if isinstance(value,bool):raise ValueError()
    parsed=float(str(value).replace(',',''))
    if not math.isfinite(parsed):raise ValueError()
    return parsed


def _fetch(source, code, country, start, end, root, opener):
    """Return separate original response bodies, dated values and metadata updates."""
    if source=='fred':
        key=_credential(root,source)
        query={'series_id':code,'api_key':key,'file_type':'json'}
        metadata=_without_key(public_get('fred_series',query,opener=opener),key)
        record=_json(metadata)['seriess'][0]
        if record['id']!=code:raise ValueError()
        body=_without_key(public_get('fred_observations',{**query,'observation_start':str(start),'observation_end':str(end)},opener=opener),key)
        data=_json(body)
        if data.get('count',len(data['observations']))!=len(data['observations']):raise ValueError()
        return [body,metadata],[(row['date'],row['value']) for row in data['observations']], {
            'indicator_name':record['title'],'unit':record['units'],'description':record.get('notes'),
            'seasonal_adjustment':record.get('seasonal_adjustment'),'last_updated':record.get('last_updated'),
            'revision_status':'latest_available_vintage','vintage_date':data.get('realtime_start')}
    if source=='bea':
        table,line=code.split(':')
        frequency='M' if table=='T20600' else 'Q'
        key=_credential(root,source)
        body=_without_key(public_get('bea_nipa',{'UserID':key,'method':'GetData','DataSetName':'NIPA',
            'TableName':table,'Frequency':frequency,'Year':','.join(map(str,range(start.year,end.year+1))),
            'ResultFormat':'JSON'},opener=opener),key)
        response=_json(body)['BEAAPI']['Results']
        if 'Error' in response:raise DatasetError('SOURCE_REJECTED','BEA rejected the request or credential')
        chosen=[row for row in response['Data'] if str(row['LineNumber'])==line]
        def period(text):
            year,typ,num=re.fullmatch(r'(\d{4})([QM])(\d{1,2})',text).groups()
            return f'{year}-{(int(num)-1)*3+1 if typ=="Q" else int(num):02d}-01'
        return [body],[(period(row['TimePeriod']),row['DataValue']) for row in chosen], {
            'unit':chosen[0].get('CL_UNIT') if chosen else None,'scale_exponent':chosen[0].get('UNIT_MULT') if chosen else None,
            'description':chosen[0].get('LineDescription') if chosen else None}
    if source=='bls':
        if end.year-start.year>=10:
            raise DatasetError('RESOURCE_LIMIT','BLS public requests support up to ten calendar years at a time')
        body=bls_series(code,start.year,end.year,opener=opener)
        doc=_json(body)
        if doc.get('status')!='REQUEST_SUCCEEDED':raise DatasetError('SOURCE_REJECTED','BLS rejected the public series request')
        series=doc['Results']['series'][0]
        if series['seriesID']!=code:raise ValueError()
        quarterly=SERIES[source][code][1]=='quarterly'
        rows=[]
        for row in series['data']:
            period=row['period']
            if not re.fullmatch(r'Q0[1-4]' if quarterly else r'M(0[1-9]|1[0-2])',period):continue
            month=int(period[1:]);month=(month-1)*3+1 if quarterly else month
            rows.append((f"{row['year']}-{month:02d}-01",row['value']))
        return [body],rows,{}
    if source=='world_bank':
        if not country:raise DatasetError('INVALID_ARGUMENT','Choose a World Bank country or region code')
        body=world_bank(code,country,f'{start.year}:{end.year}',opener=opener)
        metadata=world_bank(code,opener=opener)
        pages,rows=_json(body)
        entry=_json(metadata)[1][0]
        if str(pages['pages'])!='1' or entry['id']!=code or any(row['indicator']['id']!=code for row in rows):raise ValueError()
        return [body,metadata],[(row['date']+'-01-01',row['value']) for row in rows],{
            'indicator_name':entry['name'],'description':entry.get('sourceNote'),'last_updated':pages.get('lastupdated'),
            'unit':entry.get('unit') or SERIES[source][code][2]}
    raise DatasetError('OFFICIAL_EXPORT_REQUIRED','Download the source table from the NBS official portal and import it')


def _without_key(body,key):
    # Round-trip the JSON escape sequences so an echoed credential cannot be
    # retained even when the provider escaped ASCII as Unicode.
    decoded=json.dumps(_json(body),ensure_ascii=True).encode('ascii')
    if not re.search(re.escape(key.encode('ascii')),decoded,flags=re.IGNORECASE):return body
    return re.sub(re.escape(key.encode('ascii')),b'[REDACTED]',decoded,flags=re.IGNORECASE)


def acquire(store,p,*,credential_root=None,opener=None):
    source=p['source'];code=p['indicator_id']
    meta=describe(source,code,p.get('country_or_region'))
    start,end=_date(p['start_date']),_date(p['end_date'])
    if not 0<=(end-start).days<=366*70:raise DatasetError('INVALID_ARGUMENT','Use an ordered interval of up to 70 years')
    require_external_network()
    for attempt in range(2):
        try:
            originals,observations,updates=_fetch(source,code,meta['country_or_region'],start,end,credential_root,opener)
            break
        except DatasetError as error:
            if attempt or error.code not in ('NETWORK_TIMEOUT','NETWORK_UNREACHABLE'):raise
            time.sleep(.25)
        except (KeyError,IndexError,AttributeError,TypeError,ValueError):
            raise DatasetError('SOURCE_SCHEMA','The provider response does not match the selected indicator') from None
    return _publish(store,meta|updates,p,start,end,observations,originals)


def _publish(store,meta,p,start,end,observations,originals):
    from dataset_io import read_canonical_csv,write_canonical_csv
    import pandas as pd
    dates={}
    try:
        for when,value in observations:
            date=_date(when)
            if not start<=date<=end:continue
            if when in dates:raise ValueError()
            dates[when]=_number(value)
    except (TypeError,ValueError):
        raise DatasetError('SOURCE_SCHEMA','Duplicate period or nonnumeric observation') from None
    if not dates or not any(v is not None for v in dates.values()):
        raise DatasetError('EMPTY_DATA','No nonmissing observations in the requested interval')
    if len(dates)>30_000:raise DatasetError('RESOURCE_LIMIT','Series exceeds 30,000 observations')
    now=utc_now()
    frame=pd.DataFrame(sorted(dates.items()),columns=['observation_date','value'])
    frame['observation_date']=pd.to_datetime(frame['observation_date'])
    meta=meta|{'retrieval_time':now,'release_date':None,'revision_status':meta.get('revision_status'),
               'raw_representation':'upstream_response_with_credentials_redacted' if meta['source'] in ('bea','fred') else 'original_bytes',
               'date_range':[min(dates),max(dates)],'missing_values':sum(v is None for v in dates.values())}
    with store.transaction() as tx:
        extension=p.get('export_extension','.json')
        names=['original'+extension]+[f'upstream_metadata_{i}.json' for i in range(1,len(originals))]
        for filename,content in zip(names,originals):tx.file(filename).write_bytes(content)
        csvfile=tx.file('observations.csv')
        options=write_canonical_csv(frame,csvfile)
        restored=read_canonical_csv(csvfile,options)
        pd.testing.assert_frame_equal(restored,frame)
        source=meta['source']
        provenance={'source_type':'official_economic','source_name':source,'source_function':'registered_economic_series',
            'request_parameters':{k:v for k,v in p.items() if k!='source_path'},'fetched_at':None if source=='nbs' else now,
            'imported_at':now,'import_history':[{'operation':'normalize_period_start','at':now,'unit_transformation':'none'},
                {'operation':'inclusive_period_start_filter','at':now,'start_date':str(start),'end_date':str(end)}]}
        result=store.commit(tx,restored,dataset_name=p.get('dataset_name',meta['indicator_name'][:120]),
            original_name=names[0],current_name='observations.csv',additional_files=names[1:],provenance=provenance,
            parser_options=options,inference={'observation_date':{'semantic_type':'date'},'value':{'semantic_type':'numeric'}},extra={'economic_source':meta})
    return {**result,'economic_source':{k:v for k,v in meta.items() if k!='description'},
            'warnings':['Observation dates are period starts. Publication dates are unavailable. No unit or frequency conversion.']}


def import_nbs(store,p):
    from dataset_io import _read_table
    from dataset_store import file_sha256
    source=store.path(p['source_path'])
    if source.suffix.lower() not in ('.csv','.xlsx') or source.stat().st_size>20_000_000 or file_sha256(source)!=p['expected_sha256']:
        raise DatasetError('STALE_SOURCE','Use a hash-bound NBS CSV or XLSX export')
    raw=source.read_bytes()
    table=_read_table(source,sheet=p.get('sheet'),header_row=p.get('header_row',1))
    if file_sha256(source)!=p['expected_sha256']:raise DatasetError('STALE_SOURCE','NBS export changed')
    meta=describe('nbs',p['indicator_id'])
    meta.update(unit=p['unit'],frequency=p['frequency'],provenance='user_identified_official_export')
    try:
        import pandas as pd
        if 'series_label' in p:
            selected=table.frame[table.frame[p['value_column']].astype(str)==p['series_label']]
            if len(selected)!=1:raise ValueError()
            pairs=((column,selected.iloc[0][column]) for column in table.frame if column!=p['value_column'])
        else:
            pairs=table.frame[[p['date_column'],p['value_column']]].itertuples(index=False,name=None)
        rows=[(_nbs_period(when,p['frequency']),None if pd.isna(value) else value) for when,value in pairs]
    except (KeyError,ValueError):
        raise DatasetError('SOURCE_SCHEMA','Choose the date and value columns with ISO observation dates') from None
    start,end=_date(p['start_date']),_date(p['end_date'])
    if end<start:raise DatasetError('INVALID_ARGUMENT','Use an ordered interval')
    return _publish(store,meta,{**p,'export_extension':source.suffix.lower()},start,end,rows,[raw])


def _nbs_period(value,frequency):
    value=str(value).strip()
    if frequency=='annual' and (match:=re.fullmatch(r'(\d{4})年?',value)):
        return str(dt.date(int(match[1]),1,1))
    if frequency=='monthly' and (match:=re.fullmatch(r'(\d{4})(?:年|-)(\d{1,2})月?',value)):
        return str(dt.date(int(match[1]),int(match[2]),1))
    if frequency=='quarterly' and (match:=re.fullmatch(r'(\d{4})(?:年|[- ]?Q)([1-4])(?:季度)?',value)):
        return str(dt.date(int(match[1]),(int(match[2])-1)*3+1,1))
    return str(dt.date.fromisoformat(value[:10]))
