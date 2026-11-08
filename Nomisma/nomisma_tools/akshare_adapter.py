"""Internal, fixed Sina acquisition. No MCP registration or arbitrary dispatch."""
from __future__ import annotations

from datetime import date
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import pandas as pd

from akshare_registry import SINA_ID, SINA_COLUMNS, STANDARD_COLUMN_MAPPING, verified_source
from dataset_io import _read_table
from dataset_store import DatasetError, ProjectDatasetStore, check_shape, frame_schema, utc_now, io_boundary

WORKER_TIMEOUT_SECONDS = 25
MAX_ATTEMPTS = 2


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
            raise ValueError('Invalid format')
        return date.fromisoformat(value)
    except ValueError as error:
        raise DatasetError('INVALID_DATE', 'Dates must be valid ISO YYYY-MM-DD values') from error


def _validate_sina_frame(frame: pd.DataFrame) -> None:
    if not isinstance(frame,pd.DataFrame):
        raise DatasetError('UPSTREAM_SCHEMA_CHANGED', 'Sina did not return a DataFrame')
    check_shape(frame)
    if tuple(frame.columns) != SINA_COLUMNS:
        raise DatasetError('UPSTREAM_SCHEMA_CHANGED', 'Sina columns no longer match the reviewed schema')
    dates = pd.to_datetime(frame['date'], errors='coerce')
    if dates.isna().any():
        raise DatasetError('UPSTREAM_DATA_INVALID', 'Sina returned missing or unparseable dates')
    for name in SINA_COLUMNS[1:]:
        if not pd.api.types.is_numeric_dtype(frame[name].dtype) or pd.api.types.is_bool_dtype(frame[name].dtype):
            raise DatasetError('UPSTREAM_SCHEMA_CHANGED', 'Sina numeric column dtype changed')
        if not frame[name].dropna().map(lambda value: math.isfinite(float(value))).all():
            raise DatasetError('UPSTREAM_DATA_INVALID', 'Sina returned an infinite numeric value')


def _worker(target_relative: str) -> dict:
    """Private child action, fixed symbol/function; only a fresh owned staging path."""
    store = ProjectDatasetStore()
    if not re.fullmatch(r'datasets/\.staging/ds_[a-f0-9]{32}/original\.csv',target_relative):
        raise DatasetError('INVALID_PATH', 'Worker output is not an acquisition staging file')
    target = store.path(target_relative,allow_missing=True)
    if not target.parent.is_dir():
        raise DatasetError('STAGING_INVALID', 'Acquisition staging directory is missing')
    import akshare as ak
    from akshare.utils import http
    from types import SimpleNamespace
    from outbound_policy import SINA_HS300, public_get
    source = verified_source(SINA_ID)
    if importlib.metadata.version('akshare') != source.package_version:
        raise DatasetError('VERSION_MISMATCH', 'Installed AKShare differs from the reviewed version')
    def get_reviewed_index(url, *, params):
        if url != SINA_HS300 or params != {'d': '2020_2_4'}:
            raise DatasetError('NETWORK_DENIED', 'AKShare requested an unregistered destination')
        raw = public_get('sina_hs300', params)
        return SimpleNamespace(text=raw.decode('utf-8-sig'))

    fetched_at = utc_now()
    # Only the pinned adapter in the dedicated worker uses this restricted transport.
    original_get = http.get
    try:
        http.get = get_reviewed_index
        frame = ak.stock_zh_index_daily(symbol='sh000300')
    finally:
        http.get = original_get
    _validate_sina_frame(frame)
    with target.open('x',encoding='utf-8-sig',newline='') as stream:
        frame.to_csv(stream,index=False)
        stream.flush();os.fsync(stream.fileno())
    return {'fetched_at':fetched_at,'function_name':'stock_zh_index_daily',
            'signature':str(inspect.signature(ak.stock_zh_index_daily)),
            'package_version':ak.__version__,'original_row_count':len(frame),
            'original_column_schema':frame_schema(frame),
            'original_missing_counts':{str(name):int(count) for name,count in frame.isna().sum().items()}}


def _download_once(store: ProjectDatasetStore, target: Path) -> dict:
    allowed = {'PATH','SYSTEMROOT','WINDIR','SYSTEMDRIVE','TEMP','TMP','USERPROFILE','LOCALAPPDATA'}
    environment = {key:value for key,value in os.environ.items() if key.upper() in allowed}
    environment.update({key:os.environ[key] for key in ('NOMISMA_STATE_ROOT','NOMISMA_PROJECT_ID',
                         'NOMISMA_MODE','NOMISMA_SESSION_ID','NOMISMA_WORKSPACE')})
    environment['PYTHONUTF8']='1'
    if 'NOMISMA_PRIVACY_SETTINGS' in os.environ:
        environment['NOMISMA_PRIVACY_SETTINGS']=os.environ['NOMISMA_PRIVACY_SETTINGS']
    relative = target.relative_to(store.root).as_posix()
    try:
        result = subprocess.run([sys.executable,'-X','utf8','-B',str(Path(__file__).resolve()),'--sina-worker'],
            input=json.dumps({'target':relative}),capture_output=True,text=True,encoding='utf8',
            env=environment,cwd=store.root,timeout=WORKER_TIMEOUT_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    except subprocess.TimeoutExpired as error:
        # subprocess.run kills and waits for its own child before raising.
        raise DatasetError('NETWORK_TIMEOUT', 'Sina worker exceeded its 25-second wall-clock deadline') from error
    if len(result.stdout.encode('utf8')) > 32768:
        raise DatasetError('WORKER_PROTOCOL_ERROR', 'Sina receipt exceeds its bounded protocol')
    try:
        reply = json.loads(result.stdout)
    except (ValueError,TypeError) as error:
        raise DatasetError('WORKER_PROTOCOL_ERROR', 'Sina worker did not return a valid receipt') from error
    if not reply.get('ok'):
        raise DatasetError(reply.get('code','UPSTREAM_RESPONSE_INVALID'),'Sina acquisition failed; source was not switched')
    if result.returncode or not target.is_file():
        raise DatasetError('WORKER_PROTOCOL_ERROR', 'Sina worker did not finish saving its raw response')
    return reply['receipt']


@io_boundary
def fetch_hs300(store: ProjectDatasetStore, *, symbol: str = 'sh000300',
                start_date: str | None = None, end_date: str | None = None,
                dataset_name: str = '沪深300历史行情', interface_name: str = SINA_ID,
                attempts: int = 1) -> dict:
    """Fetch one reviewed source; return bounded receipt, not its full DataFrame."""
    from privacy_policy import require_external_network
    require_external_network()
    store.check_context()
    source = verified_source(interface_name)
    if not isinstance(symbol,str) or symbol not in source.supported_symbols:
        raise DatasetError('INVALID_SYMBOL', 'Only the verified sh000300 index is supported')
    start,end = _parse_date(start_date),_parse_date(end_date)
    if start and end and start>end:
        raise DatasetError('INVALID_DATE', 'Start date must not be after end date')
    if isinstance(attempts,bool) or not isinstance(attempts,int) or not 1<=attempts<=MAX_ATTEMPTS:
        raise DatasetError('INVALID_ARGUMENT', 'Acquisition permits at most two attempts')
    if importlib.metadata.version('akshare')!=source.package_version:
        raise DatasetError('VERSION_MISMATCH', 'Installed AKShare differs from the reviewed version')
    with store.transaction() as transaction:
        original = transaction.file('original.csv')
        for attempt in range(attempts):
            try:
                receipt = _download_once(store,original)
                break
            except DatasetError as error:
                if error.code not in ('NETWORK_TIMEOUT','NETWORK_UNREACHABLE') or attempt+1==attempts:
                    raise
                if original.exists():
                    store.path(original.relative_to(store.root).as_posix()).unlink()
                time.sleep(0.5)
        table = _read_table(original,encoding='utf-8-sig',delimiter='comma')
        _validate_sina_frame(table.frame)
        if receipt.get('package_version')!=source.package_version or receipt.get('original_row_count')!=len(table.frame):
            raise DatasetError('WORKER_PROTOCOL_ERROR', 'Raw response does not match its acquisition receipt')
        selected = table.frame.copy()
        dates = pd.to_datetime(selected['date']).dt.date
        mask = pd.Series(True,index=selected.index)
        if start:mask &= dates>=start
        if end:mask &= dates<=end
        selected = selected.loc[mask].reset_index(drop=True)
        check_shape(selected)
        current = transaction.file('selection.csv')
        with current.open('x',encoding='utf-8-sig',newline='') as stream:
            selected.to_csv(stream,index=False,date_format='%Y-%m-%d')
            stream.flush();os.fsync(stream.fileno())
        # Re-read the actual selected bytes; metadata describes this file, not a
        # different frame or the unfiltered original row count.
        saved = _read_table(current,encoding='utf-8-sig',delimiter='comma')
        try:
            pd.testing.assert_frame_equal(saved.frame,selected,check_dtype=False)
        except AssertionError as error:
            raise DatasetError('ROUNDTRIP_MISMATCH', 'Saved selection differs from its computed values') from error
        when = utc_now()
        provenance = {'source_type':'akshare','source_name':'sina','source_function':source.function_name,
            'request_parameters':{'actual_function_parameters':{'symbol':'sh000300'},
                                  'local_selection':{'start_date':start_date,'end_date':end_date}},
            'fetched_at':receipt['fetched_at'],'imported_at':when,
            'import_history':[{'operation':'fetch','at':receipt['fetched_at'],'function':source.function_name},
                              {'operation':'select_date_range','at':when,'input_rows':len(table.frame),
                               'output_rows':len(saved.frame),'start_date':start_date,'end_date':end_date}]}
        result = store.commit(transaction,saved.frame,dataset_name=dataset_name,
            original_name='original.csv',current_name='selection.csv',provenance=provenance,
            parser_options=saved.parser_options,inference=saved.inference,
            extra={'original_row_count':receipt['original_row_count'],
                   'original_column_schema':receipt['original_column_schema'],
                   'original_missing_counts':receipt['original_missing_counts'],
                   'source_signature':receipt['signature'],'download_package_version':receipt['package_version'],
                   'raw_representation':'akshare_dataframe_csv',
                   'standard_column_mapping':dict(STANDARD_COLUMN_MAPPING),
                   'source_registry':{'interface_name':source.interface_name,'status':source.status.value,
                                      'verified_at':source.verified_at},'network_attempts':attempt+1})
        result.update(original_row_count=len(table.frame),network_attempts=attempt+1,
                      warnings=['Volume unit remains unknown; raw columns were not renamed or cleaned.'])
        return result


def _worker_main() -> int:
    try:
        raw = sys.stdin.read(16385)
        if len(raw)>16384:
            raise DatasetError('INVALID_ARGUMENT','Worker request is too large')
        request=json.loads(raw)
        if not isinstance(request,dict) or set(request)!={'target'}:
            raise DatasetError('INVALID_ARGUMENT','Unknown worker arguments')
        receipt=_worker(request['target'])
        print(json.dumps({'ok':True,'receipt':receipt},ensure_ascii=False,allow_nan=False))
        return 0
    except Exception as error:
        code=error.code if isinstance(error,DatasetError) else 'UPSTREAM_RESPONSE_INVALID'
        # Classify without including raw exception/URL/proxy values in public output.
        import requests
        if type(error).__name__=='Push2RateLimitError':code='RATE_LIMITED_OR_UPSTREAM_CLOSED'
        elif isinstance(error,requests.exceptions.ProxyError):code='NETWORK_PROXY_ERROR'
        elif isinstance(error,requests.exceptions.Timeout):code='NETWORK_TIMEOUT'
        elif isinstance(error,requests.exceptions.ConnectionError):code='NETWORK_UNREACHABLE'
        elif isinstance(error,PermissionError):code='PERMISSION_DENIED'
        print(json.dumps({'ok':False,'code':code,'error_type':type(error).__name__}))
        return 1


if __name__=='__main__':
    if sys.argv[1:]!=['--sina-worker']:
        raise SystemExit('Private fixed acquisition worker; not an Agent tool')
    raise SystemExit(_worker_main())
