"""Immutable, dataset-bound shared/economic/finance analyses. Dataset publisher unchanged.

Internal synchronous API only: it does not provide an Agent job/cancel service.
metadata.json is the commit marker. Readers never accept incomplete folders.
"""
import hashlib,json,os,re,uuid
import pandas as pd
from analysis_result import encode_result,normalize_result,build_summary
from dataset_io import load_dataset,write_canonical_csv,read_canonical_csv
from dataset_store import DatasetError,file_sha256,io_boundary,utc_now,MAX_ROWS,MAX_COLUMNS,MAX_CELLS

ANALYSIS_ID=re.compile(r'an_[a-f0-9]{32}\Z')
MAX_RESULT_BYTES=8_000_000


def _prefix(analysis_id,namespace='shared'):
    if namespace not in ('shared','economic','finance'):raise DatasetError('INVALID_PATH','Unknown analysis namespace')
    if not isinstance(analysis_id,str) or not ANALYSIS_ID.fullmatch(analysis_id):
        raise DatasetError('INVALID_ARGUMENT','Invalid Analysis ID')
    return 'analyses/'+namespace+'/'+analysis_id


def _validate_result(store,result,namespace='shared'):
    required={'schema_version','analysis_id','execution_id','project_context','dataset_id','input_sha256',
              'method','actual_sample_size','sample_positions','sample_summary','results','software_versions'}
    if not isinstance(result,dict) or not required<=result.keys() or result['schema_version']!=1:
        raise DatasetError('ANALYSIS_INVALID','Analysis result is incomplete')
    _prefix(result['analysis_id'],namespace)
    if (result.get('analysis_domain','shared')!=namespace
            or namespace!='shared' and store.context['mode']!=namespace):
        raise DatasetError('MODE_NOT_ALLOWED','Analysis namespace must match the trusted project mode')
    methods={'shared':{'describe','pearson','spearman'},'economic':{'ols','ols_diagnostics','twfe'},
             'finance':{'fin_returns','fin_risk','fin_timeseries','fin_factor','fin_portfolio'}}
    if not isinstance(result.get('method'),str) or result['method'] not in methods[namespace]:
        raise DatasetError('MODE_NOT_ALLOWED','Method is not permitted in this analysis namespace')
    computation=result['results']
    if (not isinstance(computation,dict) or computation.get('method')!=result['method']
            or computation.get('analysis_domain','shared')!=namespace):
        raise DatasetError('ANALYSIS_INVALID','Result envelope and computation identity disagree')
    context=result['project_context']
    if not isinstance(context,dict) or any(context.get(k)!=store.context[k] for k in ('mode','project_id')):
        raise DatasetError('PROJECT_CONTEXT_INVALID','Analysis belongs to another project or mode')
    if not isinstance(result['execution_id'],str) or not re.fullmatch(r'[a-f0-9]{32}',result['execution_id']):
        raise DatasetError('ANALYSIS_INVALID','Invalid execution identity')
    positions=result['sample_positions'];n=result['actual_sample_size'];summary=result['sample_summary']
    if (type(n)!=int or n<1 or not isinstance(positions,list) or len(positions)!=n
            or any(type(p)!=int or p<0 for p in positions) or positions!=sorted(set(positions))
            or not isinstance(summary,dict) or summary.get('actual_sample_size')!=n
            or summary.get('positions_sha256')!=hashlib.sha256(json.dumps(positions,separators=(',',':')).encode()).hexdigest()):
        raise DatasetError('ANALYSIS_INVALID','Analysis sample identity is inconsistent')
    frame,metadata=load_dataset(store,result['dataset_id'],expected_sha256=result['input_sha256'])
    if positions[-1]>=len(frame):raise DatasetError('ANALYSIS_INVALID','Sample positions exceed the source')
    return metadata


def _write(file,data):
    with file.open('xb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())


def _read_json(file,limit):
    if file.stat().st_size>limit:raise DatasetError('RESOURCE_LIMIT','Analysis JSON exceeds its limit')
    try:
        def unique(items):
            result={}
            for key,value in items:
                if key in result:raise ValueError('Duplicate JSON key')
                result[key]=value
            return result
        return json.loads(file.read_text(encoding='utf8'),object_pairs_hook=unique,
                          parse_constant=lambda v:(_ for _ in ()).throw(ValueError(v)))
    except (ValueError,UnicodeError) as error:raise DatasetError('ANALYSIS_INVALID','Analysis JSON is invalid') from error


def _markdown(result):
    # Machine-generated report; never insert an unverified model explanation.
    header=(f"# Nomisma analysis\n\nMethod: {result['method']}\n\n"
            f"Dataset: {result['dataset_id']}\n\nInput SHA-256: {result['input_sha256']}\n\n"
            f"Observations: {result['actual_sample_size']}\n\nFull machine result: results.json\n\n")
    body=encode_result(result['results']).decode('utf8')
    fence='`'*(max([3,*[len(m.group())+1 for m in re.finditer(r'`+',body)]]))
    return (header+fence+'json\n'+body+'\n'+fence+'\n').encode('utf8')


@io_boundary
def read_analysis(store,analysis_id,*,expected_sha256=None,namespace='shared'):
    store.check_context();prefix=_prefix(analysis_id,namespace)
    if namespace!='shared' and store.context['mode']!=namespace:raise DatasetError('MODE_NOT_ALLOWED','Analysis domain does not match this project mode')
    manifest=_read_json(store.path(prefix+'/metadata.json'),1_000_000)
    if (not isinstance(manifest,dict) or manifest.get('schema_version')!=1 or manifest.get('analysis_id')!=analysis_id
            or manifest.get('committed') is not True or manifest.get('analysis_domain','shared')!=namespace
            or not isinstance(manifest.get('files'),dict)):
        raise DatasetError('ANALYSIS_INVALID','Analysis commit marker is invalid')
    files=manifest['files']
    if not {'results.json','analysis.md'}<=files.keys() or len(files)>10:
        raise DatasetError('ANALYSIS_INVALID','Analysis file set is invalid')
    for name,info in files.items():
        if name not in ('results.json','analysis.md') and not re.fullmatch(r'table_[a-z][a-z0-9_]{0,30}\.csv',name):
            raise DatasetError('ANALYSIS_INVALID','Unrecognized analysis artifact')
        file=store.path(prefix+'/'+name)
        if (not isinstance(info,dict) or info.get('path')!=prefix+'/'+name
                or file_sha256(file)!=info.get('sha256') or file.stat().st_size!=info.get('bytes')):
            raise DatasetError('STALE_ANALYSIS','Analysis file no longer matches its manifest')
        if name.endswith('.csv'):
            if (not isinstance(info.get('serialization'),dict) or not isinstance(info.get('shape'),list)):
                raise DatasetError('ANALYSIS_INVALID','Analysis table descriptor is invalid')
            try:frame=read_canonical_csv(file,info['serialization'])
            except (KeyError,TypeError,ValueError) as error:raise DatasetError('ANALYSIS_INVALID','Analysis table schema is invalid') from error
            if list(frame.shape)!=info['shape']:raise DatasetError('ROUNDTRIP_MISMATCH','Analysis table shape changed')
    if expected_sha256 is not None and files['results.json']['sha256']!=expected_sha256:
        raise DatasetError('STALE_ANALYSIS','Expected analysis version differs')
    result=_read_json(store.path(prefix+'/results.json'),MAX_RESULT_BYTES)
    _validate_result(store,result,namespace)
    if (result['analysis_id']!=analysis_id or manifest.get('project_context')!=result['project_context']
            or manifest.get('dataset_id')!=result['dataset_id'] or manifest.get('input_sha256')!=result['input_sha256']):
        raise DatasetError('ANALYSIS_INVALID','Analysis manifest and result disagree')
    return result,manifest


@io_boundary
def save_analysis(store,result,*,tables=None,preview=None,before_commit=None):
    """Publish results/MD/optional typed CSV, verify, publish metadata last.

    before_commit is a trusted Python callback, not a model argument. Callers
    must supply a real job boundary before exposing persistence through MCP.
    """
    result=normalize_result(result)
    if not isinstance(result,dict):raise DatasetError('ANALYSIS_INVALID','An analysis result object is required')
    namespace=result.get('analysis_domain','shared')
    metadata=_validate_result(store,result,namespace)
    if result['project_context']!=store.context:raise DatasetError('PROJECT_CONTEXT_INVALID','Save requires the current session binding')
    tables={} if tables is None else tables
    if not isinstance(tables,dict) or len(tables)>8:raise DatasetError('RESOURCE_LIMIT','At most eight named analysis tables')
    for name,frame in tables.items():
        if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,30}',name):raise DatasetError('INVALID_PATH','Table name must be a safe internal identifier')
        if (not isinstance(frame,pd.DataFrame) or not 1<=len(frame.columns)<=MAX_COLUMNS
                or len(frame)>MAX_ROWS or frame.size>MAX_CELLS):raise DatasetError('RESOURCE_LIMIT','Analysis table exceeds shape limits')
    raw=encode_result(result)
    if len(raw)>MAX_RESULT_BYTES:raise DatasetError('RESOURCE_LIMIT','Full analysis exceeds 8 MB')
    prefix=_prefix(result['analysis_id'],namespace);final=store.path(prefix,allow_missing=True)
    if final.exists():raise DatasetError('FILE_EXISTS','Analysis destination exists; overwrite is forbidden')
    staging_parent=store.directory('analyses/'+namespace+'/.staging')
    stage=staging_parent/('job_'+uuid.uuid4().hex);stage.mkdir()
    staged=[];published=[];owned_directory=None;committed=False
    try:
        def stage_file(name):
            path=store.path(stage.relative_to(store.root).as_posix()+'/'+name,allow_missing=True)
            # Names are generated above/below, never supplied as paths by a caller.
            staged.append(path);return path
        _write(stage_file('results.json'),raw)
        if encode_result(_read_json(stage/'results.json',MAX_RESULT_BYTES))!=raw:
            raise DatasetError('ROUNDTRIP_MISMATCH','Analysis JSON round trip failed')
        _write(stage_file('analysis.md'),_markdown(result))
        descriptors={}
        for name,frame in tables.items():
            basename='table_'+name+'.csv';file=stage_file(basename)
            options=write_canonical_csv(frame,file)
            try:pd.testing.assert_frame_equal(read_canonical_csv(file,options),frame)
            except AssertionError as error:raise DatasetError('ROUNDTRIP_MISMATCH','Analysis table round trip failed') from error
            descriptors[basename]={'serialization':options,'shape':list(frame.shape)}
        files={p.name:{'path':prefix+'/'+p.name,'sha256':file_sha256(p),'bytes':p.stat().st_size,
                       **descriptors.get(p.name,{})} for p in staged}
        manifest={'schema_version':1,'analysis_domain':namespace,'analysis_id':result['analysis_id'],'execution_id':result['execution_id'],
                  'project_context':store.context,'dataset_id':result['dataset_id'],'input_sha256':result['input_sha256'],
                  'files':files,'committed':True,'committed_at':utc_now()}
        manifest_file=stage_file('metadata.json');_write(manifest_file,encode_result(manifest))
        if _read_json(manifest_file,1_000_000)!=manifest:raise DatasetError('ROUNDTRIP_MISMATCH','Manifest round trip failed')
        artifacts=[{'path':v['path'],'sha256':v['sha256']} for v in files.values()]
        artifacts.append({'path':prefix+'/metadata.json','sha256':file_sha256(manifest_file)})
        receipt=build_summary(result,artifacts,preview=preview)
        if before_commit is not None:before_commit()
        store.check_context()
        if store.metadata(result['dataset_id'])!=metadata:raise DatasetError('STALE_DATASET','Input metadata changed before publication')
        # Exclusive folder ownership; an existing user's folder is never adopted.
        store.directory('analyses/'+namespace)
        final=store.path(prefix,allow_missing=True)
        try:final.mkdir()
        except FileExistsError as error:raise DatasetError('FILE_EXISTS','Analysis destination exists') from error
        owned_directory=(final,final.stat())
        for source in staged:  # metadata.json is deliberately last.
            store.check_context();destination=store.path(prefix+'/'+source.name,allow_missing=True)
            store._publish_file(source,destination);published.append((destination,destination.stat()))
        restored,_=read_analysis(store,result['analysis_id'],expected_sha256=files['results.json']['sha256'],namespace=namespace)
        if encode_result(restored)!=raw:raise DatasetError('ROUNDTRIP_MISMATCH','Published analysis readback changed')
        committed=True
        return receipt
    finally:
        if not committed:
            for path,identity in reversed(published):
                store.path(path.relative_to(store.root).as_posix())
                current=path.stat()
                if (current.st_dev,current.st_ino)!=(identity.st_dev,identity.st_ino):
                    raise DatasetError('CLEANUP_CONFLICT','Published file identity changed; cleanup stopped')
                path.unlink()
            if owned_directory is not None:
                path,identity=owned_directory;store.path(prefix)
                current=path.stat()
                if (current.st_dev,current.st_ino)!=(identity.st_dev,identity.st_ino):raise DatasetError('CLEANUP_CONFLICT','Analysis directory changed identity')
                path.rmdir()  # refuses unexpected files
        for path in staged:
            if path.exists():
                store.path(path.relative_to(store.root).as_posix());path.unlink()
        store.path(stage.relative_to(store.root).as_posix());stage.rmdir()
