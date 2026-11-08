"""A finite public connector: no model-provided URL, credentials or redirects."""
import datetime as dt,json,math
from dataset_store import DatasetError,file_sha256
from explore_store import ingest
from outbound_policy import OPEN_METEO as ENDPOINT, public_get

def fetch(store,p,execution_id,request_id,*,opener=None):
    from privacy_policy import require_external_network
    require_external_network()
    start=dt.date.fromisoformat(p['start_date']);end=dt.date.fromisoformat(p['end_date'])
    if not 0<=(end-start).days<=365:raise DatasetError('RESOURCE_LIMIT','Connector permits at most 366 dates')
    query={key:p[key] for key in ('latitude','longitude','start_date','end_date')}
    raw=public_get('open_meteo_era5_daily',{**query,'daily':'temperature_2m_mean','models':'era5','timezone':'GMT'},opener=opener)
    document=json.loads(raw);daily=document.get('daily',{});dates=daily.get('time');values=daily.get('temperature_2m_mean')
    if not isinstance(dates,list) or not isinstance(values,list) or len(dates)!=len(values) or not dates or len(dates)>366:raise DatasetError('SOURCE_SCHEMA','Unexpected daily structure')
    if dates!=[(start+dt.timedelta(days=i)).isoformat() for i in range((end-start).days+1)]:raise DatasetError('SOURCE_SCHEMA','API dates differ from requested interval')
    if any(v is not None and (type(v) not in (int,float) or not math.isfinite(v)) for v in values):raise DatasetError('SOURCE_SCHEMA','Invalid numeric values')
    units=document.get('daily_units',{})
    if units.get('temperature_2m_mean')!='°C':raise DatasetError('SOURCE_SCHEMA','Unexpected temperature units')
    with store.transaction() as t:
        source=t.file('response.json');source.write_bytes(raw);table=t.file('weather.jsonl')
        with table.open('w',encoding='utf8') as stream:
            for date,value in zip(dates,values):stream.write(json.dumps({'date':date,'temperature_c':value},allow_nan=False)+'\n')
        provenance={'operation':'authorized_api_import','connector':'open_meteo_era5_daily','endpoint':ENDPOINT,'parameters':p,'response_sha256':file_sha256(source),'response_document':document,'units':units,'limitations':'ERA5 reanalysis estimates, not station measurements; historical values may be revised'}
        result=ingest(store,{'source_path':table.relative_to(store.root).as_posix(),'expected_sha256':file_sha256(table),'dataset_name':'ERA5 daily mean temperature'},execution_id,request_id,lineage=provenance)
    return result
