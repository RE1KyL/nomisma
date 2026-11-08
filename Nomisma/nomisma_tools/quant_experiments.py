"""Immutable experiment attempts/results over existing project artifact publisher.

An attempt marker precedes computation. Failure/cancellation is a persisted
outcome, never silently removed. No installation, Shell or model API is exposed.
"""
import hashlib,json,re,uuid
from pathlib import Path
import pandas as pd
from dataset_store import DatasetError,file_sha256,utc_now
from dataset_io import write_canonical_csv,read_canonical_csv
from analysis_result import encode_result
from research_data_store import resolve_table,read_bundle,_artifact,_json,fingerprint_environment
from ashare_intake import require_finance

MODULES=('quant_factors.py','quant_portfolio.py','quant_backtest.py','quant_validation.py','quant_experiments.py','quant_research.py')
ID=re.compile(r'qr_[a-f0-9]{32}\Z')

def digest(value):return hashlib.sha256(encode_result(value)).hexdigest()

def environment():
    value=fingerprint_environment();value['quant_code_sha256']={n:file_sha256(Path(__file__).with_name(n)) for n in MODULES}
    return value

def capture_inputs(store,inputs):
    if not isinstance(inputs,dict) or not 1<=len(inputs)<=20:raise DatasetError('INVALID_REFERENCE','Bounded named input refs required')
    snapshots={}
    for name,ref in inputs.items():
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',name):raise DatasetError('INVALID_REFERENCE','Invalid input role')
        frame,meta=resolve_table(store,ref)
        snapshots[name]={'dataset':ref,'metadata_sha256':file_sha256(store.path('datasets/metadata/'+meta['dataset_id']+'.json'))}
    return snapshots

def validate_inputs(store,snapshots):
    for s in snapshots.values():
        _,meta=resolve_table(store,s['dataset'])
        if file_sha256(store.path('datasets/metadata/'+meta['dataset_id']+'.json'))!=s['metadata_sha256']:
            raise DatasetError('STALE_DATASET','Input metadata changed during the experiment')

def begin_attempt(store,*,plan,inputs,experiment_id=None,parent_run=None):
    require_finance(store)
    if experiment_id is None:experiment_id='ex_'+uuid.uuid4().hex
    if not isinstance(experiment_id,str) or not re.fullmatch(r'ex_[a-f0-9]{32}',experiment_id):raise DatasetError('INVALID_ARGUMENT','Invalid experiment ID')
    run_id='qr_'+uuid.uuid4().hex;prefix='datasets/metadata/artifacts/'+run_id
    snapshots=capture_inputs(store,inputs)
    record={'schema_version':'quant-attempt-1','experiment_id':experiment_id,'run_id':run_id,
        'project_id':store.context['project_id'],'mode':'finance','created_at':utc_now(),'plan':plan,
        'plan_hash':digest(plan),'inputs':snapshots,'environment':environment(),'parent_run':parent_run,
        'status':'started','approval_scope':'trusted_caller_only_not_an_Agent_permission_grant'}
    if len(encode_result(record))>1_000_000:raise DatasetError('RESOURCE_LIMIT','Attempt record exceeds budget')
    with store.transaction() as t:
        p=t.file('attempt.json');p.write_bytes(encode_result(record));sha=file_sha256(p)
        store.publish_artifacts(t,{'attempt.json':prefix+'_attempt.json'})
    return {'run_id':run_id,'attempt_path':prefix+'_attempt.json','sha256':sha}

def read_attempt(store,ref,*,verify_inputs=True):
    require_finance(store)
    if not isinstance(ref,dict) or set(ref)!={'run_id','attempt_path','sha256'} or not ID.fullmatch(ref.get('run_id','')):
        raise DatasetError('INVALID_REFERENCE','Invalid experiment attempt ref')
    path='datasets/metadata/artifacts/'+ref['run_id']+'_attempt.json'
    if ref['attempt_path']!=path or file_sha256(store.path(path))!=ref['sha256']:raise DatasetError('STALE_ATTEMPT','Attempt bytes changed')
    m=_json(store,path)
    if (not isinstance(m,dict) or m.get('schema_version')!='quant-attempt-1' or m.get('project_id')!=store.context['project_id']
        or m.get('mode')!='finance' or m.get('run_id')!=ref['run_id'] or digest(m.get('plan'))!=m.get('plan_hash')):
        raise DatasetError('INVALID_ATTEMPT','Attempt identity mismatch')
    if verify_inputs:validate_inputs(store,m['inputs'])
    return m

def finish_attempt(store,attempt,*,status,tables,summary,error_code=None):
    require_finance(store);m=read_attempt(store,attempt,verify_inputs=status=='completed')
    if status not in ('completed','failed','cancelled'):raise DatasetError('INVALID_ARGUMENT','Invalid terminal experiment status')
    if not isinstance(tables,dict) or len(tables)>32:raise DatasetError('RESOURCE_LIMIT','Too many experiment tables')
    if status!='completed' and tables:raise DatasetError('INVALID_ARGUMENT','Failed attempts cannot publish success tables')
    prefix='datasets/exports/'+m['run_id'];marker='datasets/metadata/artifacts/'+m['run_id']+'_result.json'
    with store.transaction() as t:
        files={};descriptors={};total=0
        for name,frame in tables.items():
            if not re.fullmatch(r'[a-z][a-z0-9_]{0,30}',name) or not isinstance(frame,pd.DataFrame):
                raise DatasetError('INVALID_ARGUMENT','Bounded named result tables required')
            if not len(frame.columns):continue
            source=t.file(name+'.csv');serialization=write_canonical_csv(frame,source)
            restored=read_canonical_csv(source,serialization)
            try:pd.testing.assert_frame_equal(frame,restored)
            except AssertionError as exc:raise DatasetError('ROUNDTRIP_MISMATCH','Experiment table readback failed') from exc
            total+=source.stat().st_size
            if total>64_000_000:raise DatasetError('RESOURCE_LIMIT','Experiment artifacts exceed existing 64 MB job budget')
            dest=prefix+'_'+name+'.csv';files[source.name]=dest
            descriptors[name]={'path':dest,'sha256':file_sha256(source),'serialization':serialization,'rows':len(frame),'columns':list(frame.columns)}
        report={'schema_version':'quant-result-1','run_id':m['run_id'],'experiment_id':m['experiment_id'],
            'project_id':store.context['project_id'],'mode':'finance','status':status,'finished_at':utc_now(),
            'attempt':attempt,'plan_hash':m['plan_hash'],'tables':descriptors,'summary':summary,'error_code':error_code,
            'strict_backtest_ready':False}
        r=t.file('result.json');r.write_bytes(encode_result(report));sha=file_sha256(r)
        if r.stat().st_size>8_000_000:raise DatasetError('RESOURCE_LIMIT','Experiment result exceeds 8 MB result budget')
        if status=='completed':validate_inputs(store,m['inputs'])
        files['result.json']=marker;store.publish_artifacts(t,files)
    return {'run_id':m['run_id'],'path':marker,'sha256':sha}

def read_result(store,ref):
    require_finance(store)
    if not isinstance(ref,dict) or set(ref)!={'run_id','path','sha256'} or not ID.fullmatch(ref.get('run_id','')):
        raise DatasetError('INVALID_REFERENCE','Invalid experiment result ref')
    if ref['path']!='datasets/metadata/artifacts/'+ref['run_id']+'_result.json' or file_sha256(store.path(ref['path']))!=ref['sha256']:
        raise DatasetError('STALE_RESULT','Experiment result bytes changed')
    m=_json(store,ref['path'],8_000_000)
    if (not isinstance(m,dict) or m.get('schema_version')!='quant-result-1' or m.get('project_id')!=store.context['project_id']
        or m.get('mode')!='finance' or m.get('run_id')!=ref['run_id']):raise DatasetError('INVALID_RESULT','Result ownership mismatch')
    attempt=read_attempt(store,m['attempt'],verify_inputs=m['status']=='completed')
    if m['plan_hash']!=attempt['plan_hash']:raise DatasetError('STALE_RESULT','Plan/result mismatch')
    frames={}
    for name,d in m['tables'].items():
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,30}',name) or d['path']!='datasets/exports/'+ref['run_id']+'_'+name+'.csv':
            raise DatasetError('INVALID_PATH','Experiment table namespace mismatch')
        p=store.path(d['path'])
        if file_sha256(p)!=d['sha256']:raise DatasetError('STALE_RESULT','Experiment table changed')
        f=read_canonical_csv(p,d['serialization'])
        if len(f)!=d['rows'] or list(f.columns)!=d['columns']:raise DatasetError('STALE_RESULT','Experiment table shape changed')
        frames[name]=f
    return frames,m

def reproduce_plan(store,result_ref):
    _,m=read_result(store,result_ref);attempt=read_attempt(store,m['attempt'])
    if m['status']!='completed':raise DatasetError('RUN_NOT_COMPLETED','Only completed results can be reproduced as successful research')
    if attempt['environment']['quant_code_sha256']!=environment()['quant_code_sha256']:
        raise DatasetError('ENVIRONMENT_CHANGED','Exact reproduction requires the captured code version')
    if attempt['environment']['packages']!=environment()['packages']:
        raise DatasetError('ENVIRONMENT_CHANGED','Exact reproduction requires the captured package versions')
    return {'plan':attempt['plan'],'inputs':{n:s['dataset'] for n,s in attempt['inputs'].items()},
             'experiment_id':m['experiment_id'],'parent_run':result_ref,
             'requires_new_execution_authorization':True}

def list_runs(store):
    require_finance(store);directory=store.path('datasets/metadata/artifacts',allow_missing=True)
    if not directory.exists():return []
    rows=[]
    for file in sorted(directory.iterdir()):
        if not re.fullmatch(r'qr_[a-f0-9]{32}_attempt\.json',file.name):continue
        run_id=file.name.removesuffix('_attempt.json');path=file.relative_to(store.root).as_posix()
        try:
            ref={'run_id':run_id,'attempt_path':path,'sha256':file_sha256(store.path(path))}
            attempt=read_attempt(store,ref,verify_inputs=False);outcome=directory/(run_id+'_result.json')
            status='started_or_interrupted';result_ref=None
            if outcome.exists():
                relative=outcome.relative_to(store.root).as_posix();result_ref={'run_id':run_id,'path':relative,'sha256':file_sha256(store.path(relative))}
                _,result=read_result(store,result_ref);status=result['status']
            rows.append({'run_id':run_id,'experiment_id':attempt['experiment_id'],'created_at':attempt['created_at'],
                         'status':status,'plan_hash':attempt['plan_hash'],'result':result_ref})
        except DatasetError as exc:rows.append({'run_id':run_id,'status':'unreadable_or_stale','error_code':exc.code})
    return rows
