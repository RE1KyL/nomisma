"""Persistent research coordination, not a second Agent runtime.

Only the desktop's trusted application bridge calls this finite CLI. Scientific
operations continue through DSH -> ACP approval -> existing MCP/Worker. No code,
URLs, roots, environment or arbitrary functions are accepted from model plans.
"""
import copy,hashlib,json,os,re,sys,time,uuid
from pathlib import Path
from jsonschema import Draft202012Validator
from analysis_result import encode_result
from dataset_store import ProjectDatasetStore,DatasetError,file_sha256,utc_now
from dataset_io import load_dataset
from analysis_store import read_analysis
from mcp_contract import SPECS,visible,validate,bounded
from mcp_jobs import lock,read_json

ID=re.compile(r'rs_[a-f0-9]{32}\Z')
TASK=re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
POINTERS={'/verified/source/sha256':'a'*64,'/verified/dataset/dataset_id':'ds_'+'a'*32,'/verified/dataset/sha256':'a'*64,
 '/verified/analysis/analysis_id':'an_'+'a'*32,'/verified/analysis/sha256':'a'*64,
 '/verified/quality_report/quality_report_id':'qr_'+'a'*32,'/verified/quality_report/sha256':'a'*64}
TERMINAL={'succeeded','failed','cancelled','blocked'}
READ_ONLY={'nomisma_dataset_inspect','nomisma_akshare_catalog','nomisma_health_check'}

def error(code,msg):raise DatasetError(code,msg)
def sha(value):return hashlib.sha256(encode_result(value)).hexdigest()
def now():return time.time()
def knowledge_hashes(mode):
    names=['research_planning.md']+(['explore_research_planning.md'] if mode=='big_data_explore' else ['economic_research_planning.md'] if mode=='economic' else
       ['finance_factor_research.md','finance_trading_research.md','finance_validation.md'])
    return {n:file_sha256(Path(__file__).resolve().parents[1]/'knowledge'/n) for n in names}

def resolve(value,tasks,*,preview=False):
    if isinstance(value,dict):
        if '$from' in value:
            if set(value)!={'$from','pointer'} or value['pointer'] not in POINTERS or value['$from'] not in tasks:
                error('PLAN_REFERENCE_INVALID','Unknown task result reference')
            if preview:return POINTERS[value['pointer']]
            source=tasks[value['$from']]
            if source['status']!='succeeded':error('DEPENDENCY_NOT_READY','Referenced result is not verified')
            result=source
            for p in value['pointer'].strip('/').split('/'):
                if not isinstance(result,dict) or p not in result:error('OUTPUT_UNAVAILABLE','Expected verified output is absent')
                result=result[p]
            return result
        return {k:resolve(v,tasks,preview=preview) for k,v in value.items()}
    if isinstance(value,list):return [resolve(v,tasks,preview=preview) for v in value]
    return value

def references(v):
    if isinstance(v,dict):
        if '$from' in v:return {v['$from']}
        return set().union(*(references(x) for x in v.values())) if v else set()
    if isinstance(v,list):return set().union(*(references(x) for x in v)) if v else set()
    return set()

def checked_dataset(store,dataset_id,expected_sha256):
    if store.metadata(dataset_id).get('schema_version')==2:
        from explore_store import read
        m,_=read(store,{'dataset_id':dataset_id,'sha256':expected_sha256});return None,m
    return load_dataset(store,dataset_id,expected_sha256=expected_sha256)

def check_inputs(store,args):
    right=args.get('parameters',{}).get('right_dataset')
    if right:checked_dataset(store,right['dataset_id'],right['sha256'])
    if 'inputs' in args:
        for ref in args['inputs'].values():checked_dataset(store,ref['dataset_id'],ref['sha256'])
    if 'dataset' in args:checked_dataset(store,args['dataset']['dataset_id'],args['dataset']['sha256'])
    if 'analysis' in args:read_analysis(store,args['analysis']['analysis_id'],expected_sha256=args['analysis']['sha256'],namespace=store.context['mode'])
    if 'quality_report' in args:
        q=args['quality_report'];p=store.path('datasets/metadata/quality/'+q['quality_report_id']+'.json');report=read_json(p)
        if (file_sha256(p)!=q['sha256'] or report.get('dataset_id')!=args['dataset']['dataset_id']
            or report.get('input_sha256')!=args['dataset']['sha256']):error('STALE_QUALITY_REPORT','Quality report must describe the same dataset version')
    p=args.get('parameters',{})
    if 'source_path' in p:
        if p.get('expected_sha256') and file_sha256(store.path(p['source_path']))!=p['expected_sha256']:error('STALE_SOURCE','Import file changed')

def plan_validate(store,plan):
    from tool_registry import descriptors
    versions={d['tool_id']:d['version'] for d in descriptors(store.context['mode'])}
    if (not isinstance(plan,dict) or set(plan)!={'objective','mode','locked_constraints','tasks'}
        or plan['mode']!=store.context['mode'] or not isinstance(plan['objective'],str) or not 1<=len(plan['objective'])<=2000
        or not isinstance(plan['locked_constraints'],dict) or not plan['locked_constraints']):
        error('PLAN_INVALID','Goal, trusted mode and explicit research definitions required')
    if len(encode_result(plan))>100000 or not isinstance(plan['tasks'],list) or not 1<=len(plan['tasks'])<=20:
        error('PLAN_LIMIT','Plan must contain one to twenty bounded tasks')
    tasks={}
    required={'task_id','task_name','objective','tool','arguments','dependencies','expected_outputs','validation_criteria'}
    for item in plan['tasks']:
        if (not isinstance(item,dict) or set(item)!=required or not isinstance(item['task_id'],str)
            or not TASK.fullmatch(item['task_id']) or item['task_id'] in tasks):error('PLAN_INVALID','Task identity or fields invalid')
        for key in ('task_name','objective'):
            if not isinstance(item[key],str) or not 1<=len(item[key])<=1000:error('PLAN_INVALID','Task description invalid')
        if item['tool'] not in visible(store.context['mode']):error('CAPABILITY_UNAVAILABLE','Tool is not enabled in this project mode')
        for key in ('dependencies','expected_outputs','validation_criteria'):
            if not isinstance(item[key],list) or len(item[key])>20 or any(not isinstance(s,str) for s in item[key]):error('PLAN_INVALID','Task lists must be bounded text arrays')
        if not set(item['validation_criteria'])<={'receipt','artifacts','dataset','analysis','quality_report','warnings'}:
            error('PLAN_INVALID','Unknown validation rule')
        if not set(item['expected_outputs'])<={'receipt','dataset','analysis','quality_report'}:error('PLAN_INVALID','Unknown expected output role')
        args=copy.deepcopy(item['arguments'])
        if not isinstance(args,dict):error('PLAN_INVALID','Task arguments must be an object')
        # The controller, not the model, owns each side-effecting request identity.
        spec=SPECS[item['tool']]['inputSchema']
        if 'request_id' in spec.get('required',[]) or 'oneOf' in spec:
            if 'request_id' not in args:args['request_id']=str(uuid.uuid4())
        tasks[item['task_id']]={**copy.deepcopy(item),'arguments':args,'mode':store.context['mode'],
            'schema_sha256':sha(spec),'tool_version':versions[item['tool']],'required_tools':[item['tool']],
            'required_inputs':[{k:copy.deepcopy(v)} for k,v in args.items() if k in ('dataset','analysis','quality_report')],
            'approval_requirements':{'plan':'confirm','tool':'ask_each_call'},'status':'pending',
            'attempts':[],'verified':{},'reason':None}
    visited=set()
    def visit(key,path):
        if key not in tasks:error('DEPENDENCY_MISSING','Unknown prerequisite task')
        if key in path:error('DEPENDENCY_CYCLE','Task dependencies form a cycle')
        if key in visited:return
        for d in tasks[key]['dependencies']:visit(d,path|{key})
        visited.add(key)
    for key,t in tasks.items():
        visit(key,set())
        if not references(t['arguments'])<=set(t['dependencies']):error('PLAN_REFERENCE_INVALID','Result references require direct dependencies')
        preview=resolve(t['arguments'],tasks,preview=True);validate(t['tool'],preview,store.context['mode'])
        if not references(t['arguments']):check_inputs(store,preview)
    return tasks

class ResearchStore:
    def __init__(self,store):self.store=store
    def prefix(self,rid):
        if not isinstance(rid,str) or not ID.fullmatch(rid):error('RESEARCH_ID_INVALID','Invalid research identity')
        return '.nomisma/research/'+rid
    def read(self,rid):
        prefix=self.prefix(rid);directory=self.store.path(prefix+'/events')
        files=sorted(directory.iterdir())
        if not 1<=len(files)<=300:error('RESEARCH_LOG_INVALID','Research event count invalid')
        previous=None;state=None;total=0
        for i,p in enumerate(files):
            self.store.path(p.relative_to(self.store.root).as_posix())
            if p.name!=f'{i:06}.json':error('RESEARCH_LOG_INVALID','Research journal has a gap or unexpected file')
            total+=p.stat().st_size
            if total>20_000_000:error('RESEARCH_LIMIT','Research journal exceeds budget')
            event=read_json(p,1_000_000)
            if event.get('previous_sha256')!=previous or event.get('version')!=i:error('RESEARCH_LOG_INVALID','Research event chain changed')
            state=event['state']
            if state.get('version')!=i:error('RESEARCH_LOG_INVALID','Snapshot version differs from event')
            previous=file_sha256(p)
        if (state['research_id']!=rid or state['project_id']!=self.store.context['project_id'] or state['mode']!=self.store.context['mode']):
            error('RESEARCH_OWNER_INVALID','Research belongs to another project or mode')
        return state,previous
    def append(self,state,event,previous=None):
        prefix=self.prefix(state['research_id']);version=state['version']
        if version>=300:error('RESEARCH_LIMIT','Research event budget exceeded')
        record={'version':version,'previous_sha256':previous,'at':utc_now(),'event':event,'state':state}
        raw=encode_result(record)
        if len(raw)>1_000_000:error('RESEARCH_LIMIT','Research snapshot exceeds one MB')
        existing=self.store.path(prefix+'/events',allow_missing=True)
        if existing.exists() and sum(p.stat().st_size for p in existing.iterdir())+len(raw)>20_000_000:
            error('RESEARCH_LIMIT','Research journal budget reached')
        dest=self.store.directory(prefix+'/events')/f'{version:06}.json'
        # Fixed owned staging and exclusive publish; last event is the commit marker.
        stage=self.store.directory(prefix+'/staging')/('event_'+uuid.uuid4().hex+'.json')
        try:
            with stage.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            self.store.check_context();self.store._publish_file(stage,dest)
        finally:
            if stage.exists():stage.unlink()
    def list(self):
        p=self.store.path('.nomisma/research',allow_missing=True)
        if not p.exists():return []
        out=[]
        for child in sorted(p.iterdir()):
            if ID.fullmatch(child.name):out.append(self.read(child.name)[0])
        return sorted(out,key=lambda s:(s['created_at'],s['research_id']))
    def active(self,session):
        found=[s for s in self.list() if s['session_id']==session and s['status'] not in ('completed','cancelled')]
        if len(found)>1:error('RESEARCH_SESSION_CONFLICT','More than one research owns this session')
        return found[0] if found else None

def refresh(state):
    tasks=state['tasks']
    for t in tasks.values():
        if t['status'] not in ('pending','ready'):continue
        deps=[tasks[d]['status'] for d in t['dependencies']]
        if any(d in ('failed','cancelled','blocked') for d in deps):t['status']='blocked';t['reason']='DEPENDENCY_FAILED'
        elif all(d=='succeeded' for d in deps):t['status']='ready'
    if all(t['status']=='succeeded' for t in tasks.values()):state['status']='computed'

def verify_receipt(store,receipt,*,name,args):
    bounded(receipt)
    if (not isinstance(receipt,dict) or type(receipt.get('ok')) is not bool or not isinstance(receipt.get('data'),dict)
        or not isinstance(receipt.get('warnings'),list) or not isinstance(receipt.get('artifacts'),list)):
        error('RECEIPT_INVALID','Tool receipt structure is incomplete')
    if not receipt['ok'] and (not isinstance(receipt.get('error'),dict) or not isinstance(receipt['error'].get('code'),str)):
        error('RECEIPT_INVALID','Failed tool receipt has no stable error')
    if not receipt['ok']:return {'receipt':receipt,'error_code':receipt['error']['code']}
    # Receipts are accepted only from the matching durable Python request record
    # (read-only calls without request_id are checked via artifacts/data/audit).
    verified={};data=receipt['data']
    if not re.fullmatch(r'[a-f0-9]{32}',data.get('execution_id','')):error('RECEIPT_INVALID','Missing execution identity')
    if args.get('request_id'):
        p=store.path('.nomisma/requests/'+args['request_id']+'/committed.json')
        if read_json(p,16384)!=receipt:error('RECEIPT_INVALID','Returned receipt differs from committed request')
    check_inputs(store,args)
    for artifact in receipt['artifacts']:
        if not isinstance(artifact,dict) or file_sha256(store.path(artifact['path']))!=artifact['sha256']:
            error('ARTIFACT_INVALID','Expected artifact missing or changed')
    if data.get('quality_report_id'):
        q={'quality_report_id':data['quality_report_id'],'sha256':data['sha256']}
        check_inputs(store,{'dataset':{'dataset_id':data['dataset_id'],'sha256':data['input_sha256']},'quality_report':q})
        verified['quality_report']=q
    if name=='nomisma_explore_probe':
        digest=file_sha256(store.path(args['parameters']['source_path']))
        if data.get('source_sha256')!=digest:error('STALE_SOURCE','Inspected source changed')
        verified['source']={'sha256':digest}
    if data.get('dataset_id'):
        digest=data.get('input_sha256',data.get('sha256'))
        _,m=checked_dataset(store,data['dataset_id'],digest)
        verified['dataset']={'dataset_id':m['dataset_id'],'sha256':m['sha256']}
    if data.get('analysis_id'):
        namespace='shared' if name.startswith('nomisma_analysis_') else store.context['mode']
        result,m=read_analysis(store,data['analysis_id'],namespace=namespace)
        if result['execution_id']!=data['execution_id']:error('RECEIPT_INVALID','Analysis execution does not match')
        verified['analysis']={'analysis_id':result['analysis_id'],'sha256':m['files']['results.json']['sha256'],'namespace':namespace,
          'sample_size':result['actual_sample_size'],'method':result['method']}
    if name=='nomisma_fin_scenario':
        from quant_experiments import read_result
        _,report=read_result(store,data['result'])
        verified['scenario']={'result':data['result'],'experiment_id':report['experiment_id'],'strict_backtest_ready':False}
    return {'receipt':receipt,'verified':verified}

def receipt_from_request(store,args):
    request=args.get('request_id')
    if not request:return None
    prefix='.nomisma/requests/'+request
    for marker in ('committed.json','failed.json','cancelled.json'):
        path=store.path(prefix+'/'+marker,allow_missing=True)
        if path.exists():return read_json(path,16384)
    return None

def adaptation(code):
    return {'category':('data' if code in ('STALE_DATASET','FILE_NOT_FOUND','MISSING_VALUES','INVALID_COLUMN','SAMPLE_TOO_SMALL') else
       'method' if code in ('RANK_DEFICIENT','NUMERICAL_FAILURE') else 'permission' if code in ('USER_DENIED','CANCELLED') else 'execution'),
      'recommendation':'Inspect the recorded evidence; changes to variables, sample, identification or strategy require a revised confirmed plan',
      'automatic_strategy_change':False,'retryable':code in ('NETWORK_TIMEOUT','NETWORK_UNREACHABLE','PROJECT_BUSY')}

def control(store,action,payload):
    manager=ResearchStore(store);store.check_context()
    if action=='catalog':
        from tool_registry import descriptors
        registry={d['tool_id']:{k:v for k,v in d.items() if k not in ('input_schema','output_schema','description')} for d in descriptors(store.context['mode'])}
        return {'mode':store.context['mode'],'tools':{n:{'description':s['description'],'inputSchema':s['inputSchema'],'schema_sha256':sha(s['inputSchema'])} for n,s in visible(store.context['mode']).items()},
           'tool_registry':registry,
           'datasets':[{'dataset_id':m['dataset_id'],'sha256':m['sha256'],'row_count':m['row_count'],'fields':[c['name'] for c in m['column_schema']]} for p in store.directory('datasets/metadata').glob('ds_*.json') for m in [store.metadata(p.stem)]][:30]}
    # One project-wide controller writer; never held across model/approval/worker execution.
    with lock(store,'.nomisma/research/control.lock'):
        if action=='list':return manager.list()
        if action=='create':
            if manager.active(store.context['session_id']):error('RESEARCH_SESSION_CONFLICT','This session already has an unfinished research')
            tasks=plan_validate(store,payload['plan']);rid='rs_'+uuid.uuid4().hex
            state={'research_id':rid,'project_id':store.context['project_id'],'mode':store.context['mode'],
              'session_id':store.context['session_id'],'version':0,'plan_version':1,'objective':payload['plan']['objective'],
              'locked_constraints':payload['plan']['locked_constraints'],'tasks':tasks,'status':'proposed','calls':0,'prompts':0,
              'replans':0,'active_seconds':0.,'decisions':[],'created_at':utc_now(),'tokens':None,'cost':None}
            state['knowledge_sha256']=knowledge_hashes(store.context['mode'])
            if payload.get('parent_experiment_id'):
                from shared_store import replay_plan
                replay=replay_plan(store,payload['parent_experiment_id'])
                if sha(replay['plan'])!=sha(payload['plan']):error('REPLAY_CHANGED','Reproduction plan differs from captured configuration')
                state['parent_experiment_id']=payload['parent_experiment_id']
            manager.append(state,'plan_proposed');return state
        if action in ('admit','context','prompt','result','decision','interrupted'):
            state=manager.active(store.context['session_id'])
            if state is None:return {'managed':False}
        else:state=manager.read(payload['research_id'])[0]
        if state['session_id']!=store.context['session_id']:error('RESEARCH_OWNER_INVALID','Only the owning work session can control this research')
        if state['status'] in ('completed','cancelled') and action!='deliver':error('STATE_INVALID','Finished research cannot be restarted; propose a new research')
        _,previous=manager.read(state['research_id'])
        if 'version' in payload and payload['version']!=state['version']:error('STATE_CONFLICT','Research changed; refresh before deciding')
        event=action
        if action=='confirm':
            if state['status']!='proposed':error('STATE_INVALID','Only a proposed plan can be confirmed')
            state['status']='active';state['decisions'].append({'kind':'plan_direction_confirmed','at':utc_now()});refresh(state)
        elif action=='revise':
            if any(t['status'] in ('running','waiting_approval') for t in state['tasks'].values()):error('STATE_INVALID','Pause/cancel the current call before revising')
            if state['replans']>=2:error('BUDGET_EXHAUSTED','Plan revision budget reached')
            state['tasks']=plan_validate(store,payload['plan']);state['objective']=payload['plan']['objective'];state['locked_constraints']=payload['plan']['locked_constraints']
            state['plan_version']+=1;state['replans']+=1;state['status']='proposed';state['decisions'].append({'kind':'revision_requires_direction_confirmation','at':utc_now()})
            state['cancel_requested']=False
            state['knowledge_sha256']=knowledge_hashes(store.context['mode'])
        elif action in ('pause','cancel'):
            if action=='cancel':state['cancel_requested']=True
            if state['status']!='cancelling':state['status']='paused' if action=='pause' else 'cancelling'
            state['decisions'].append({'kind':action+'_requested','at':utc_now()})
            # Actual pending/running calls are reconciled after the existing ACP cancel.
            for t in state['tasks'].values():
                if action=='cancel' and t['status'] in ('pending','ready'):t['status']='cancelled';t['reason']='USER_CANCELLED'
            if action=='cancel' and not any(t['status'] in ('running','waiting_approval') for t in state['tasks'].values()):
                state['status']='recovery_required' if any(t['reason']=='OUTCOME_UNKNOWN' for t in state['tasks'].values()) else 'cancelled'
        elif action=='resume':
            if state['status'] not in ('paused','recovery_required'):error('STATE_INVALID','Research is not paused for recovery')
            if state['knowledge_sha256']!=knowledge_hashes(store.context['mode']):error('KNOWLEDGE_CHANGED','Research guidance changed; propose and confirm a revised plan')
            for t in state['tasks'].values():
                if t['status'] in ('running','waiting_approval','blocked') and t['reason'] in (None,'OUTCOME_UNKNOWN','APPROVAL_EXPIRED'):
                    args=resolve(t['arguments'],state['tasks']);receipt=receipt_from_request(store,args)
                    if receipt and receipt['ok']:
                        checked=verify_receipt(store,receipt,name=t['tool'],args=args);t.update(status='succeeded',verified=checked['verified'],receipt=receipt,reason=None)
                    elif receipt:t.update(status='cancelled' if receipt['error']['code']=='TASK_CANCELLED' else 'failed',reason=receipt['error']['code'],receipt=receipt)
                    elif t['status']=='waiting_approval' or t['reason']=='APPROVAL_EXPIRED':t.update(status='pending',reason=None)
                    else:t.update(status='blocked',reason='OUTCOME_UNKNOWN')
                if t['status']=='succeeded':
                    try:verify_receipt(store,t['receipt'],name=t['tool'],args=resolve(t['arguments'],state['tasks']))
                    except DatasetError:t.update(status='blocked',reason='STALE_RESULT')
            state['status']='active'
            if state.get('cancel_requested'):
                state['status']='recovery_required' if any(t['reason']=='OUTCOME_UNKNOWN' for t in state['tasks'].values()) else 'cancelled'
            refresh(state)
        elif action=='retry':
            t=state['tasks'].get(payload.get('task_id'))
            if t is None or t['status'] not in ('failed','cancelled','blocked'):error('STATE_INVALID','Task is not eligible for explicit retry')
            if len(t['attempts'])>=2:error('BUDGET_EXHAUSTED','At most one retry per task is allowed')
            if t['reason'] not in ('TASK_CANCELLED','USER_DENIED','NETWORK_TIMEOUT','NETWORK_UNREACHABLE','PROJECT_BUSY','APPROVAL_EXPIRED'):
                error('RETRY_UNSAFE','Uncertain outcome or changed scientific definitions require recovery/replanning')
            if t['reason'] not in ('USER_DENIED','APPROVAL_EXPIRED'):
                prior=receipt_from_request(store,resolve(t['arguments'],state['tasks']))
                if prior is None or prior['ok']:error('RETRY_UNSAFE','No proven terminal failure; reconcile before retrying')
            if 'request_id' in t['arguments']:t['arguments']['request_id']=str(uuid.uuid4())
            t.update(status='pending',reason=None,verified={});state['status']='active'
            for other in state['tasks'].values():
                if other['reason']=='DEPENDENCY_FAILED':other.update(status='pending',reason=None)
            state['decisions'].append({'kind':'explicit_task_retry','task_id':t['task_id'],'at':utc_now()});refresh(state)
        elif action in ('context','prompt'):
            if action=='prompt':
                if state['prompts']>=10:error('BUDGET_EXHAUSTED','Research model prompt budget reached')
                state['prompts']+=1
            refresh(state)
            resolved=[]
            if state['status']=='active':
                for t in state['tasks'].values():
                    if t['status']=='ready':resolved.append({'task_id':t['task_id'],'tool':t['tool'],'arguments':resolve(t['arguments'],state['tasks'])})
            context={'managed':True,'research_id':state['research_id'],'status':state['status'],'objective':state['objective'],
              'locked_constraints':state['locked_constraints'],'ready_tasks':resolved,'tasks':[{k:t[k] for k in ('task_id','task_name','status','reason')} for t in state['tasks'].values()],
              'verified_evidence':[{'task_id':t['task_id'],'verified':t['verified'],'receipt':t.get('receipt')} for t in state['tasks'].values() if t['status']=='succeeded'],
              'problems':[{'task_id':t['task_id'],'reason':t['reason'],'receipt':t.get('receipt'),'adaptation':t.get('adaptation')} for t in state['tasks'].values() if t['status'] in ('failed','blocked','cancelled')],
              'instruction':'Execute only ready task arguments exactly. Ask First remains mandatory. Stop on unknown evidence or required research-definition changes; never invent results or skip diagnostics.'}
            if len(encode_result(context))>48000:error('RESEARCH_CONTEXT_LIMIT','Research evidence exceeds prompt-context budget; split the project task or deliver existing results')
            if action=='context':return context
        elif action=='admit':
            if state['status']!='active':error('RESEARCH_NOT_ACTIVE','Plan confirmation or resume is required before any tool call')
            if state['calls']>=40 or state['active_seconds']>=1800:error('BUDGET_EXHAUSTED','Research execution budget reached')
            inflight=[t for t in state['tasks'].values() if t['status'] in ('running','waiting_approval')]
            if inflight and (len(inflight)>=2 or payload['tool'] not in READ_ONLY or any(t['tool'] not in READ_ONLY for t in inflight)):
                error('TASK_CONFLICT','Only two independent read-only operations may overlap; calculations and publication are serialized')
            matches=[]
            for t in state['tasks'].values():
                if t['status']=='ready' and t['tool']==payload['tool']:
                    args=resolve(t['arguments'],state['tasks'])
                    if sha(args)==sha(payload['arguments']):matches.append((t,args))
            if len(matches)!=1:error('CALL_OUTSIDE_PLAN','Actual tool/parameters do not uniquely match a ready task')
            t,args=matches[0];validate(t['tool'],args,store.context['mode']);check_inputs(store,args)
            if sha(SPECS[t['tool']]['inputSchema'])!=t['schema_sha256']:error('SCHEMA_CHANGED','Tool schema changed since plan review')
            from tool_registry import require_tool
            if t.get('tool_version') and t['tool_version']!=require_tool(store.context['mode'],t['tool'],args)['version']:error('TOOL_VERSION_CHANGED','Tool code changed; revise and confirm the plan')
            attempt={'tool_call_id':payload['tool_call_id'],'arguments':args,'arguments_sha256':sha(args),'permission_id':payload['permission_id'],'status':'waiting_approval','at':utc_now(),'requested_at':now()}
            t['attempts'].append(attempt);t['status']='waiting_approval';state['calls']+=1
        elif action=='decision':
            found=[t for t in state['tasks'].values() if t['attempts'] and t['attempts'][-1]['permission_id']==payload['permission_id'] and t['status']=='waiting_approval']
            if len(found)!=1:error('APPROVAL_EXPIRED','This approval is no longer pending')
            t=found[0];a=t['attempts'][-1]
            if payload['allow'] is True:
                if state['status']!='active' or now()-a['requested_at']>86400:error('APPROVAL_EXPIRED','Research paused or approval expired')
                from tool_registry import require_tool
                if t.get('tool_version') and t['tool_version']!=require_tool(store.context['mode'],t['tool'],a['arguments'])['version']:error('TOOL_VERSION_CHANGED','Tool changed while approval was pending')
                check_inputs(store,a['arguments']);validate(t['tool'],a['arguments'],store.context['mode']);t['status']='running';a['status']='running';a['started_at']=now()
            else:t.update(status='blocked',reason='USER_DENIED');a['status']='denied';refresh(state)
        elif action=='result':
            found=[t for t in state['tasks'].values() if t['attempts'] and t['attempts'][-1]['tool_call_id']==payload['tool_call_id']]
            if len(found)!=1:return {'managed':True,'ignored':True}
            t=found[0];a=t['attempts'][-1]
            if t['status'] not in ('running','waiting_approval'):return {'managed':True,'ignored':True}
            receipt=payload.get('receipt') or receipt_from_request(store,a['arguments'])
            if receipt is None:
                reason='APPROVAL_EXPIRED' if t['status']=='waiting_approval' else 'OUTCOME_UNKNOWN'
                t.update(status='blocked',reason=reason)
                if state['status']!='cancelling':state['status']='paused' if reason=='APPROVAL_EXPIRED' else 'recovery_required'
            else:
                try:
                    checked=verify_receipt(store,receipt,name=t['tool'],args=a['arguments'])
                    if receipt['ok']:
                        if any(role not in checked['verified'] for role in t['expected_outputs'] if role!='receipt'):
                            error('OUTPUT_UNAVAILABLE','Expected output role was not produced')
                        t.update(status='succeeded',receipt=receipt,verified=checked['verified'],reason=None)
                    else:t.update(status='cancelled' if checked['error_code']=='TASK_CANCELLED' else 'failed',receipt=receipt,reason=checked['error_code'],adaptation=adaptation(checked['error_code']))
                except DatasetError as exc:t.update(status='failed',reason=exc.code,adaptation=adaptation(exc.code))
                a['status']=t['status'];state['active_seconds']+=max(0,now()-a.get('started_at',now()));refresh(state)
        elif action=='interrupted':
            for t in state['tasks'].values():
                if t['status'] in ('running','waiting_approval') or (t['status']=='blocked' and t['reason'] in ('OUTCOME_UNKNOWN','APPROVAL_EXPIRED')):
                    receipt=receipt_from_request(store,t['attempts'][-1]['arguments'])
                    if receipt and receipt['ok']:
                        checked=verify_receipt(store,receipt,name=t['tool'],args=t['attempts'][-1]['arguments']);t.update(status='succeeded',verified=checked['verified'],receipt=receipt,reason=None)
                    elif receipt:t.update(status='cancelled' if receipt['error']['code']=='TASK_CANCELLED' else 'failed',reason=receipt['error']['code'],receipt=receipt)
                    else:t.update(status='blocked',reason='APPROVAL_EXPIRED' if t['status']=='waiting_approval' or t['reason']=='APPROVAL_EXPIRED' else 'OUTCOME_UNKNOWN')
            if state.get('cancel_requested'):state['status']='recovery_required' if any(t['reason']=='OUTCOME_UNKNOWN' for t in state['tasks'].values()) else 'cancelled'
            else:state['status']='paused'
            if all(t['status']=='succeeded' for t in state['tasks'].values()):state['status']='computed'
        elif action=='deliver':
            from research_verification import deliver_research
            report=deliver_research(store,state);state['delivery']=report;state['status']='completed'
        else:error('ACTION_INVALID','Unknown research control action')
        state['version']+=1;manager.append(state,event,previous)
        if action=='prompt':return context
        return state

def main():
    try:
        raw=sys.stdin.buffer.read(200001)
        if len(raw)>200000:error('RESOURCE_LIMIT','Research request too large')
        request=json.loads(raw)
        if not isinstance(request,dict) or set(request)!={'action','payload'}:error('INVALID_ARGUMENT','Expected a finite application control request')
        result=control(ProjectDatasetStore(),request['action'],request['payload']);reply={'ok':True,'data':result}
    except DatasetError as exc:reply={'ok':False,'error':{'code':exc.code,'message':str(exc)}}
    except Exception:reply={'ok':False,'error':{'code':'RESEARCH_CONTROL_FAILED','message':'Research state could not be updated; inspect project evidence'}}
    sys.stdout.buffer.write(encode_result(reply))
if __name__=='__main__':main()
