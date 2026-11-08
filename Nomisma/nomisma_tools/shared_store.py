"""Project-local metadata indexes over existing data, analyses and research tasks."""
import hashlib,importlib.metadata as md,json,platform,re,uuid
from pathlib import Path
from analysis_result import encode_result
from dataset_store import DatasetError,file_sha256,utc_now
from dataset_io import load_dataset
from analysis_store import read_analysis
from mcp_jobs import read_json
from shared_contract import identifier,TASK_STATES

def digest(v):return hashlib.sha256(encode_result(v)).hexdigest()
def environment():
    root=Path(__file__).parent
    names=('numpy','pandas','scipy','statsmodels','openpyxl','akshare','pyarrow','duckdb','polars')
    return {'python':platform.python_version(),'packages':{n:md.version(n) for n in names},
      'code_sha256':{p.name:file_sha256(p) for p in sorted(root.glob('*.py'))}}
def publish_json(store,path,value):
    raw=encode_result(value)
    if len(raw)>2_000_000:raise DatasetError('RESOURCE_LIMIT','Shared record exceeds 2 MB')
    with store.transaction() as t:
        p=t.file('record.json');p.write_bytes(raw)
        if path.startswith('.nomisma/shared/'):
            store.directory(str(Path(path).parent).replace('\\','/'));target=store.path(path,allow_missing=True)
            with p.open('r+b') as stream:__import__('os').fsync(stream.fileno())
            store._publish_file(p,target);t.published.append((target,target.stat()))
            if file_sha256(target)!=hashlib.sha256(raw).hexdigest():raise DatasetError('ROUNDTRIP_MISMATCH','Metadata bytes changed')
            t.committed=True
        else:store.publish_artifacts(t,{'record.json':path})
    return {'path':path,'sha256':file_sha256(store.path(path))}
def audit(store,action,parameters,authorization,result,error=None):
    event={'schema_version':'audit-1','audit_id':'au_'+uuid.uuid4().hex,'actor':{'kind':'desktop_user','session_id':store.context['session_id']},
        'project_id':store.context['project_id'],'domain':store.context['mode'],'operation':action,'parameters':parameters,
        'authorization':authorization,'result':result,'at':utc_now(),'error':error}
    return publish_json(store,'.nomisma/shared/audit/'+event['audit_id']+'.json',event)
def data_catalog(store):
    directory=store.path('datasets/metadata',allow_missing=True)
    if not directory.exists():return []
    rows=[]
    for p in sorted(directory.glob('ds_*.json'))[:200]:
        try:
            meta=store.metadata(p.stem)
            frame=None
            if meta.get('schema_version')==2:
                from explore_store import read
                read(store,{'dataset_id':p.stem,'sha256':meta['sha256']})
            else:frame,meta=load_dataset(store,p.stem,expected_sha256=meta['sha256'])
            rows.append({'dataset_id':p.stem,'project_id':store.context['project_id'],'domain':store.context['mode'],
               'source':{k:meta.get(k) for k in ('source_type','source_name','source_function','request_parameters','imported_at','fetched_at')},
               'created_at':meta.get('created_at',meta.get('imported_at')),'observation_period':[meta.get('data_start'),meta.get('data_end')],
               'frequency':meta.get('frequency','unknown'),'schema':meta['column_schema'],'version':meta['sha256'],'file_hash':meta['sha256'],
               'rows':meta['row_count'],'file_path':meta['current_file_path'],'lineage':{'parent_dataset_id':meta.get('parent_dataset_id'),'processing_history':meta.get('processing_history',[])},
               'quality_reports':[p.relative_to(store.root).as_posix() for p in store.path('datasets/metadata/quality',allow_missing=True).glob('qr_*.json') if read_json(store.path(p.relative_to(store.root).as_posix())).get('dataset_id')==meta['dataset_id']],
               'legacy_metadata':meta})
        except DatasetError as exc:rows.append({'dataset_id':p.stem,'status':'STALE','error_code':exc.code})
    return rows

def artifact_type(path):
    extension=Path(path).suffix.lower()
    types={'.csv':'csv','.tsv':'csv','.xlsx':'xlsx','.png':'png','.svg':'svg','.pdf':'pdf','.py':'python','.md':'report','.json':'model_result','.log':'execution_log','.txt':'text'}
    if extension not in types:raise DatasetError('ARTIFACT_UNSUPPORTED','File type has no registered viewer')
    return types[extension]
def artifact_viewer(kind):return 'spreadsheet' if kind in ('csv','xlsx') else 'visualization' if kind in ('png','svg','chart') else 'pdf' if kind=='pdf' else 'text'
def register_artifact(store,path,sha256,*,experiment_id=None,task_id=None,metadata=None):
    if not isinstance(path,str) or path.startswith('.') or '/.' in path:raise DatasetError('INVALID_PATH','Internal state is not an importable artifact')
    p=store.path(path)
    if not p.is_file() or p.stat().st_size>20_000_000:raise DatasetError('RESOURCE_LIMIT','Artifact must be a bounded regular file')
    if file_sha256(p)!=sha256:raise DatasetError('STALE_ARTIFACT','Artifact bytes changed')
    if experiment_id is not None:identifier('experiment_id',experiment_id)
    kind=artifact_type(path);aid='ar_'+uuid.uuid5(uuid.NAMESPACE_URL,store.context['project_id']+':'+path+':'+sha256).hex
    with p.open('rb') as stream:head=stream.read(16)
    if (kind=='png' and not head.startswith(b'\x89PNG\r\n\x1a\n') or kind=='pdf' and not head.startswith(b'%PDF-')
        or kind=='xlsx' and not head.startswith(b'PK\x03\x04')):raise DatasetError('ARTIFACT_TYPE_MISMATCH','File signature differs from the registered extension')
    if kind=='svg':
        from xml.etree import ElementTree as E
        try:root=E.parse(p).getroot()
        except E.ParseError:raise DatasetError('ARTIFACT_TYPE_MISMATCH','SVG must be valid XML')
        if root.tag.rsplit('}',1)[-1]!='svg':raise DatasetError('ARTIFACT_TYPE_MISMATCH','Expected SVG root element')
    row={'schema_version':'artifact-1','artifact_id':aid,'project_id':store.context['project_id'],'domain':store.context['mode'],
        'experiment_id':experiment_id,'task_id':task_id,'artifact_type':kind,'file_path':path,'file_hash':sha256,'created_at':utc_now(),
        'metadata':metadata or {},'viewer':artifact_viewer(kind)}
    marker='.nomisma/shared/artifacts/'+aid+'.json';dest=store.path(marker,allow_missing=True)
    if dest.exists():return read_artifact(store,aid)
    publish_json(store,marker,row);return row
def read_artifact(store,aid):
    identifier('artifact_id',aid);marker=store.path('.nomisma/shared/artifacts/'+aid+'.json',allow_missing=True)
    if marker.exists():row=read_json(marker,2_000_000)
    else:
        row=next((a for e in legacy_experiments(store) for a in e['artifacts'] if a['artifact_id']==aid),None)
        if row is None:raise DatasetError('ARTIFACT_NOT_FOUND','Artifact is not registered in this project')
    if row.get('project_id')!=store.context['project_id'] or row.get('domain')!=store.context['mode']:raise DatasetError('PROJECT_CONTEXT_INVALID','Artifact belongs to another project')
    if file_sha256(store.path(row['file_path']))!=row['file_hash']:raise DatasetError('STALE_ARTIFACT','Artifact no longer matches its registered version')
    return row
def artifacts(store):
    p=store.path('.nomisma/shared/artifacts',allow_missing=True)
    rows=[]
    for f in sorted(p.glob('ar_*.json'))[:500] if p.exists() else []:
        try:rows.append(read_artifact(store,f.stem))
        except DatasetError as exc:rows.append({'artifact_id':f.stem,'status':'STALE','error_code':exc.code})
    return rows+[a for e in legacy_experiments(store) for a in e['artifacts'] if a['artifact_id'] not in {r['artifact_id'] for r in rows}]

def task_catalog(store):
    from research_control import ResearchStore
    rows=[]
    for state in ResearchStore(store).list():
        for t in state['tasks'].values():
            rows.append({'task_id':state['research_id']+':'+t['task_id'],'research_id':state['research_id'],
              'project_context':{**store.context,'session_id':state['session_id']},'dependencies':[state['research_id']+':'+d for d in t['dependencies']],
              'status':'REJECTED' if t['reason']=='USER_DENIED' else 'PLANNED' if state['status']=='proposed' else TASK_STATES[t['status']],
              'tool_id':t['tool'],'parameters':t['arguments'],'approval_requests':t['attempts'],'execution_result':t.get('receipt'),
              'error_code':t['reason'],'legacy_task':t})
    return rows
def execution_environment(store,args,receipt):
    if receipt is None:return None
    execution=receipt.get('data',{}).get('execution_id')
    root=store.path('.nomisma/requests',allow_missing=True)
    if not root.exists():return None
    dirs=[root/args['request_id']] if args.get('request_id') else sorted(root.iterdir())[-500:]
    for directory in dirs:
        rel=directory.relative_to(store.root).as_posix()+'/environment.json';p=store.path(rel,allow_missing=True)
        if p.exists():
            record=read_json(p,64000)
            if record.get('execution_id')==execution:return record
    return None
def capture_experiment(store,research_id):
    from research_control import ResearchStore,resolve,verify_receipt
    state,_=ResearchStore(store).read(research_id)
    if state['status']=='proposed':raise DatasetError('EXPERIMENT_INCOMPLETE','Confirm the research direction before recording an experiment')
    eid='ex_'+uuid.uuid5(uuid.NAMESPACE_URL,store.context['project_id']+research_id+':'+str(state['plan_version'])).hex
    dest='.nomisma/shared/experiments/'+eid+f'/v_{state["version"]:06}.json'
    if store.path(dest,allow_missing=True).exists():return read_experiment(store,eid)
    tasks=[];refs=[];files=[];execution_environments={}
    for key,t in state['tasks'].items():
        args=t['attempts'][-1]['arguments'] if t['attempts'] else t['arguments']
        if t['status']=='succeeded':verify_receipt(store,t['receipt'],name=t['tool'],args=resolve(t['arguments'],state['tasks']))
        if 'dataset' in args and isinstance(args['dataset'].get('dataset_id'),str):refs.append(args['dataset'])
        refs.extend(args.get('inputs',{}).values())
        if args.get('parameters',{}).get('right_dataset'):refs.append(args['parameters']['right_dataset'])
        from tool_registry import descriptors
        descriptor=next(d for d in descriptors(store.context['mode']) if d['tool_id']==t['tool'])
        provenance=execution_environment(store,args,t.get('receipt'))
        execution_environments[research_id+':'+key]=provenance
        tasks.append({'task_id':research_id+':'+key,'tool':t['tool'],'parameters':args,'tool_version':provenance['tool_version'] if provenance else 'not_recorded_legacy',
          'current_registered_tool_version':descriptor['version'],'execution_result':t.get('receipt'),
          'status':TASK_STATES[t['status']],'error_code':t['reason'],'approval_evidence':t['attempts']})
        for a in (t.get('receipt') or {}).get('artifacts',[]):
            files.append(register_artifact(store,a['path'],a['sha256'],experiment_id=eid,task_id=research_id+':'+key))
    actual_environment=next((p['environment'] for p in execution_environments.values() if p is not None),None)
    record={'schema_version':'experiment-1','experiment_id':eid,'project_id':store.context['project_id'],'domain':store.context['mode'],
       'research_goal':state['objective'],'dataset_version':list({digest(r):encode_result(r).decode() for r in refs}.values()),
       'method':[t['tool'] for t in tasks],'parameters':state['locked_constraints'],'tool_version':{t['tool']:t['tool_version'] for t in tasks},
       'code_version':actual_environment['code_sha256'] if actual_environment else {},'runtime_environment':actual_environment or {},
       'execution_environments':execution_environments,'captured_environment':environment(),
       'random_seed':{t['task_id']:t['parameters'].get('parameters',{}).get('seed') for t in tasks},
       'task_references':tasks,'artifacts':files,'execution_status':'SUCCEEDED' if all(t['status']=='succeeded' for t in state['tasks'].values()) else 'CANCELLED' if state['status']=='cancelled' else 'FAILED' if any(t['status'] in ('failed','blocked') for t in state['tasks'].values()) else 'RUNNING',
       'created_at':utc_now(),'research_id':research_id,'plan_version':state['plan_version'],'version':state['version'],'parent_experiment_id':state.get('parent_experiment_id'),
       'replay_plan':{'objective':state['objective'],'mode':state['mode'],'locked_constraints':state['locked_constraints'],'tasks':[
          {k:t[k] for k in ('task_id','task_name','objective','tool','arguments','dependencies','expected_outputs','validation_criteria')} for t in state['tasks'].values()]}}
    record['dataset_version']=[json.loads(r) for r in record['dataset_version']]
    publish_json(store,dest,record);return record
def read_experiment(store,eid,version=None):
    identifier('experiment_id',eid);base='.nomisma/shared/experiments/'+eid;p=store.path(base,allow_missing=True)
    if p.is_dir():
        files=sorted(p.glob('v_*.json'))
        if not 1<=len(files)<=300:raise DatasetError('RESOURCE_LIMIT','Experiment snapshot count exceeds budget')
        if version is not None and (type(version)!=int or not 0<=version<300):raise DatasetError('INVALID_REFERENCE','Invalid experiment snapshot version')
        selected=p/f'v_{version:06}.json' if version is not None else files[-1]
        if selected not in files:raise DatasetError('EXPERIMENT_NOT_FOUND','Experiment snapshot is not recorded')
        r=read_json(store.path(selected.relative_to(store.root).as_posix()),2_000_000)
        r['saved_versions']=[int(f.stem[2:]) for f in files if re.fullmatch(r'v_[0-9]{6}',f.stem)]
    elif store.path(base+'.json',allow_missing=True).exists():r=read_json(store.path(base+'.json'),2_000_000)
    else:
        r=next((r for r in legacy_experiments(store) if r['experiment_id']==eid),None)
        if r is None:raise DatasetError('EXPERIMENT_NOT_FOUND','Experiment is not recorded in this project')
    if r['project_id']!=store.context['project_id'] or r['domain']!=store.context['mode']:raise DatasetError('PROJECT_CONTEXT_INVALID','Experiment owner mismatch')
    return r
def experiments(store):
    p=store.path('.nomisma/shared/experiments',allow_missing=True)
    rows=[read_experiment(store,f.stem if f.is_file() else f.name) for f in sorted(p.iterdir())[:200] if re.fullmatch(r'ex_[a-f0-9]{32}(\.json)?',f.name)] if p.exists() else []
    return rows+[r for r in legacy_experiments(store) if r['experiment_id'] not in {v['experiment_id'] for v in rows}]
def legacy_experiments(store):
    if store.context['mode']!='finance':return []
    from quant_experiments import list_runs,read_result,read_attempt
    rows=[]
    for run in list_runs(store)[-100:]:
        run_id=run['run_id'];attempt_path='datasets/metadata/artifacts/'+run_id+'_attempt.json'
        fallback=None
        try:
            ref={'run_id':run_id,'attempt_path':attempt_path,'sha256':file_sha256(store.path(attempt_path))}
            a=read_attempt(store,ref,verify_inputs=False)
            fallback={'schema_version':'experiment-1','experiment_id':a['experiment_id'],'project_id':store.context['project_id'],'domain':'finance',
              'research_goal':a['plan']['intent']['research_question'],'dataset_version':[s['dataset'] for s in a['inputs'].values()],
              'method':[a['plan']['intent']['objective']],'parameters':a['plan']['locked_constraints'],'tool_version':{'quant_research':'internal_3a'},
              'code_version':a['environment']['quant_code_sha256'],'runtime_environment':a['environment'],'random_seed':None,
              'task_references':[{'task_id':run_id,'approval_scope':a['approval_scope']}],'artifacts':[],
              'execution_status':'RECOVERY_REQUIRED','created_at':a['created_at'],'legacy_run_id':run_id,'legacy_attempt':ref,
              'error_code':run.get('error_code','RUN_INCOMPLETE_OR_STALE')}
        except DatasetError:pass
        if not run.get('result'):
            if fallback:rows.append(fallback)
            continue
        try:
            frames,result=read_result(store,run['result']);attempt=read_attempt(store,result['attempt'],verify_inputs=result['status']=='completed');eid=result['experiment_id']
            files=[]
            for d in result['tables'].values():
                aid='ar_'+uuid.uuid5(uuid.NAMESPACE_URL,store.context['project_id']+':'+d['path']+':'+d['sha256']).hex
                files.append({'schema_version':'artifact-1','artifact_id':aid,'project_id':store.context['project_id'],'domain':'finance','experiment_id':eid,'task_id':run['run_id'],
                  'artifact_type':'csv','file_path':d['path'],'file_hash':d['sha256'],'created_at':attempt['created_at'],'metadata':{'legacy_quant_run':run['run_id']},'viewer':'spreadsheet'})
            cfg=attempt['plan']['locked_constraints'];refs=[s['dataset'] for s in attempt['inputs'].values()]
            rows.append({'schema_version':'experiment-1','experiment_id':eid,'project_id':store.context['project_id'],'domain':'finance',
               'research_goal':attempt['plan']['intent']['research_question'],'dataset_version':refs,'method':[attempt['plan']['intent']['objective']],
               'parameters':cfg,'tool_version':{'quant_research':'internal_3a'},'code_version':attempt['environment']['quant_code_sha256'],
               'runtime_environment':attempt['environment'],'random_seed':None,'task_references':[{'task_id':run['run_id'],'approval_scope':attempt['approval_scope']}],
               'artifacts':files,'execution_status':{'completed':'SUCCEEDED','failed':'FAILED','cancelled':'CANCELLED'}[result['status']],
               'created_at':attempt['created_at'],'legacy_run_id':run['run_id'],'legacy_result':run['result'],'legacy_attempt':result['attempt']})
        except DatasetError as exc:
            if fallback:fallback['error_code']=exc.code;rows.append(fallback)
    # Several attempts can belong to one legacy experiment. Retain all run references.
    grouped={}
    for r in rows:
        if r['experiment_id'] in grouped:r['prior_run_references']=grouped[r['experiment_id']].get('prior_run_references',[])+[grouped[r['experiment_id']].get('legacy_result',grouped[r['experiment_id']]['legacy_attempt'])]
        grouped[r['experiment_id']]=r
    return list(grouped.values())
def reproduction_check(store,eid):
    record=read_experiment(store,eid);current=environment();changes=[]
    if record.get('legacy_attempt') and not record.get('legacy_result'):
        return {'experiment_id':eid,'conditions_match':False,'changes':[{'kind':'incomplete_or_stale_legacy_run','error_code':record.get('error_code')}],'requires_new_approval':True}
    if record.get('legacy_result'):
        from quant_experiments import reproduce_plan
        try:reproduce_plan(store,record['legacy_result'])
        except DatasetError as exc:changes.append({'kind':'legacy_conditions','error_code':exc.code})
        return {'experiment_id':eid,'conditions_match':not changes,'changes':changes,'requires_new_approval':True,'meaning':'Original 3A data/code/package checks, not an independent scientific replication'}
    for r in record['dataset_version']:
        try:load_dataset(store,r['dataset_id'],expected_sha256=r['sha256'])
        except DatasetError as exc:changes.append({'kind':'dataset','id':r['dataset_id'],'error_code':exc.code})
    for key in ('python','packages','code_sha256'):
        if current[key]!=record['runtime_environment'].get(key):changes.append({'kind':key})
    for task,provenance in record.get('execution_environments',{}).items():
        if provenance is None:changes.append({'kind':'execution_environment_not_recorded','task_id':task})
        elif provenance['environment']!=current:changes.append({'kind':'execution_environment_changed','task_id':task})
    for a in record['artifacts']:
        try:read_artifact(store,a['artifact_id'])
        except DatasetError as exc:changes.append({'kind':'artifact','id':a['artifact_id'],'error_code':exc.code})
    return {'experiment_id':eid,'conditions_match':not changes,'changes':changes,'requires_new_approval':True,
       'meaning':'Matching inputs/code/environment are reproduction conditions, not proof of equivalent scientific conclusions'}
def replay_plan(store,eid):
    import copy
    check=reproduction_check(store,eid)
    if not check['conditions_match']:raise DatasetError('REPRODUCTION_CHANGED','Data, code, dependencies or artifacts changed')
    record=read_experiment(store,eid)
    if record['execution_status']!='SUCCEEDED':raise DatasetError('REPLAY_UNSAFE','Incomplete/failed experiments require inspection; never repeat an uncertain outcome automatically')
    if record.get('legacy_result'):
        from quant_experiments import read_attempt
        a=read_attempt(store,record['legacy_attempt']);intent=a['plan']['intent']
        plan={'objective':record['research_goal'],'mode':store.context['mode'],'locked_constraints':record['parameters'],
          'tasks':[{'task_id':'reproduce','task_name':'Reproduce recorded 3A scenario','objective':record['research_goal'],'tool':'nomisma_fin_scenario',
           'arguments':{'objective':intent['objective'],'research_question':intent['research_question'],'inputs':{n:s['dataset'] for n,s in a['inputs'].items()},'configuration':record['parameters']},
           'dependencies':[],'expected_outputs':['receipt'],'validation_criteria':['receipt','artifacts','warnings']}]}
    else:plan=copy.deepcopy(record['replay_plan'])
    for t in plan['tasks']:t['arguments'].pop('request_id',None)
    return {'plan':plan,'requires_plan_confirmation':True,'requires_tool_approval':True,'parent_experiment_id':eid}
