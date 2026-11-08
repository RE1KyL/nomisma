"""Bounded candidate-only acquisition; never added to production source registry.

One owned child per call, finite dispatch, official scoped AKShare settings,
wall deadline and cancellation. No model-supplied functions, URLs, code or shell.
"""
import hashlib,importlib.metadata as md,json,os,re,subprocess,sys,tempfile,time,uuid
from contextlib import contextmanager
from pathlib import Path
from dataset_store import DatasetError

ALLOWED_FUNCTIONS=frozenset({'stock_zh_a_hist','stock_zh_a_daily','stock_financial_report_sina',
 'tool_trade_date_hist_sina','stock_info_sh_delist','stock_tfp_em'})
SYMBOLS={'000001':'sz000001','600519':'sh600519','300750':'sz300750','688981':'sh688981'}
FUNCTION_BY_KIND={'eastmoney_bars':'stock_zh_a_hist','sina_bars':'stock_zh_a_daily',
  'sina_balance':'stock_financial_report_sina','sina_calendar':'tool_trade_date_hist_sina',
  'sse_delist':'stock_info_sh_delist','eastmoney_suspensions':'stock_tfp_em'}
VERSION='1.20.18'

def validate_request(request):
    if not isinstance(request,dict) or set(request)!={'kind','code','start','end'}:
        raise DatasetError('INVALID_ARGUMENT','Expected a finite candidate acquisition request')
    kind=request['kind'];code=request['code']
    if kind not in ('eastmoney_bars','sina_bars','sina_balance','sina_calendar','sse_delist','eastmoney_suspensions'):
        raise DatasetError('SOURCE_NOT_ALLOWED','Unknown candidate adapter')
    if kind in ('eastmoney_bars','sina_bars','sina_balance'):
        if code not in SYMBOLS:raise DatasetError('SOURCE_NOT_ALLOWED','Security is outside the initial reviewed candidate scope')
    elif code is not None:raise DatasetError('INVALID_ARGUMENT','This adapter does not take a security code')
    from ashare_data import day
    start=request['start'];end=request['end']
    if kind in ('eastmoney_bars','sina_bars'):
        from datetime import date
        day(start);day(end)
        if not 0<=(date.fromisoformat(end)-date.fromisoformat(start)).days<=370:
            raise DatasetError('RESOURCE_LIMIT','Candidate bars are limited to 371 calendar days')
    elif kind=='eastmoney_suspensions':
        day(start)
        if end is not None:raise DatasetError('INVALID_ARGUMENT','Suspension query takes one requested date')
    elif start is not None or end is not None:raise DatasetError('INVALID_ARGUMENT','Snapshot adapter does not accept dates')
    return request.copy()

def _child(request,file):
    import contextlib,io
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
        import akshare as ak
        from akshare.utils.context import set_proxies,set_timeout
        set_timeout((5,10));set_proxies({'http':'','https':''})
        if md.version('akshare')!=VERSION:raise DatasetError('VERSION_MISMATCH','Unreviewed AKShare version')
        kind=request['kind'];code=request['code'];start=request['start'];end=request['end']
        if kind=='eastmoney_bars':
            function='stock_zh_a_hist';params={'symbol':code,'period':'daily','start_date':start.replace('-',''),'end_date':end.replace('-',''),'adjust':''};frame=ak.stock_zh_a_hist(**params)
        elif kind=='sina_bars':
            function='stock_zh_a_daily';params={'symbol':SYMBOLS[code],'start_date':start.replace('-',''),'end_date':end.replace('-',''),'adjust':''};frame=ak.stock_zh_a_daily(**params)
        elif kind=='sina_balance':
            function='stock_financial_report_sina';params={'stock':SYMBOLS[code],'symbol':'资产负债表'};frame=ak.stock_financial_report_sina(**params)
        elif kind=='sina_calendar':function='tool_trade_date_hist_sina';params={};frame=ak.tool_trade_date_hist_sina()
        elif kind=='sse_delist':function='stock_info_sh_delist';params={'symbol':'全部'};frame=ak.stock_info_sh_delist(**params)
        else:function='stock_tfp_em';params={'date':start.replace('-','')};frame=ak.stock_tfp_em(**params)
        if frame.empty:raise DatasetError('EMPTY_DATA','No candidate rows')
        if len(frame)>20000 or len(frame.columns)>500 or frame.size>2_000_000:
            raise DatasetError('RESOURCE_LIMIT','Candidate response shape exceeds budget')
        if not frame.columns.is_unique:raise DatasetError('SCHEMA_MISMATCH','Candidate headers repeat')
        # Bound each field before serializing. CSV preserves exact returned values, not HTTP bytes.
        if any(isinstance(v,str) and len(v)>32767 for row in frame.itertuples(index=False,name=None) for v in row):
            raise DatasetError('RESOURCE_LIMIT','Candidate cell exceeds budget')
        raw=frame.to_csv(index=False).encode('utf-8')
        if len(raw)>20_000_000:raise DatasetError('RESOURCE_LIMIT','Candidate payload exceeds budget')
        with file.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    from dataset_store import utc_now
    return {'function':function,'parameters':params,'rows':len(frame),'columns':list(map(str,frame.columns)),
      'sha256':hashlib.sha256(raw).hexdigest(),'fetched_at':utc_now(),'akshare':VERSION,
      'status':'candidate_not_production_verified','payload_kind':'serialized_provider_dataframe'}

@contextmanager
def owned_temporary_directory(root=None):
    parent=Path(tempfile.gettempdir() if root is None else root).resolve(strict=True)
    directory=Path(tempfile.mkdtemp(prefix='nomisma-a1-',dir=parent))
    if directory.resolve().parent!=parent:raise DatasetError('INVALID_PATH','Temporary directory escaped its owner')
    try:yield directory
    finally:
        # Fixed flat owned directory only. No recursive deletion of computed paths.
        for name in ('payload.csv','receipt.json'):
            child=directory/name
            if child.exists():
                if child.is_symlink():raise DatasetError('CLEANUP_CONFLICT','Temporary artifact was redirected')
                child.unlink()
        directory.rmdir()  # Unexpected contents are preserved and cause a visible failure.

def acquire_candidate(request,*,cancelled=None,timeout=25,temporary_root=None):
    from privacy_policy import require_external_network
    require_external_network()
    """Return bytes+receipt in memory. No registered project or output destination is changed."""
    request=validate_request(request)
    if type(timeout) not in (int,float) or not 0<timeout<=30:raise DatasetError('RESOURCE_LIMIT','Invalid acquisition deadline')
    if cancelled and cancelled():raise DatasetError('CANCELLED','Acquisition cancelled before launch')
    with owned_temporary_directory(temporary_root) as directory:
        file=Path(directory)/'payload.csv';receipt=Path(directory)/'receipt.json'
        env=os.environ.copy();env['PYTHONUTF8']='1'
        for k in list(env):
            if k.upper() in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NODE_OPTIONS'):del env[k]
        p=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--candidate-worker',
            json.dumps(request,ensure_ascii=True),str(file),str(receipt)],
            env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+timeout
        try:
            while p.poll() is None:
                if cancelled and cancelled():raise DatasetError('CANCELLED','Candidate acquisition cancelled')
                if time.monotonic()>deadline:raise DatasetError('SOURCE_TIMEOUT','Candidate acquisition deadline exceeded')
                time.sleep(.025)
            if cancelled and cancelled():raise DatasetError('CANCELLED','Cancelled before candidate acceptance')
            if not receipt.is_file() or receipt.stat().st_size>65536:
                raise DatasetError('SOURCE_FAILED','Candidate worker returned no bounded receipt')
            try:result=json.loads(receipt.read_text('utf8'))
            except (ValueError,UnicodeError):raise DatasetError('SOURCE_PROTOCOL_ERROR','Candidate receipt JSON is invalid')
            if not isinstance(result,dict):raise DatasetError('SOURCE_PROTOCOL_ERROR','Candidate receipt is invalid')
            if not result.get('ok'):raise DatasetError(result.get('code','SOURCE_FAILED'),'Candidate acquisition failed')
            info=result.get('receipt')
            if not isinstance(info,dict):raise DatasetError('SOURCE_PROTOCOL_ERROR','Candidate receipt is missing')
            if not file.is_file() or file.stat().st_size>20_000_000:raise DatasetError('RESOURCE_LIMIT','Candidate payload exceeds limit')
            raw=file.read_bytes()
            if (hashlib.sha256(raw).hexdigest()!=info.get('sha256') or info.get('function')!=FUNCTION_BY_KIND[request['kind']]
                or info.get('akshare')!=VERSION or info.get('status')!='candidate_not_production_verified'):
                raise DatasetError('SOURCE_PROTOCOL_ERROR','Candidate receipt does not match its bytes')
            return raw,info
        finally:
            if p.poll() is None:p.kill()
            p.wait()

if __name__=='__main__':
    if len(sys.argv)!=5 or sys.argv[1]!='--candidate-worker':raise SystemExit(2)
    target=Path(sys.argv[3]);receipt=Path(sys.argv[4])
    # Private CLI is never exposed as a tool. Only the parent chooses temporary paths.
    try:
        request=validate_request(json.loads(sys.argv[2]));info=_child(request,target)
        result={'ok':True,'receipt':info}
    except DatasetError as exc:result={'ok':False,'code':exc.code}
    except Exception:result={'ok':False,'code':'SOURCE_FAILED'}
    with receipt.open('x',encoding='utf8') as stream:json.dump(result,stream,ensure_ascii=False)
