"""Additive v1 contracts. Legacy envelopes remain full fidelity in extension fields."""
import datetime as dt,re
from jsonschema import Draft202012Validator
from dataset_store import DatasetError
from analysis_result import encode_result

ID={'project_id':r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$','dataset_id':r'^ds_[a-f0-9]{32}$',
 'artifact_id':r'^ar_[a-f0-9]{32}$','experiment_id':r'^ex_[a-f0-9]{32}$','chart_id':r'^ch_[a-f0-9]{32}$'}
STATES=('CREATED','PLANNED','WAITING_APPROVAL','QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED','REJECTED','BLOCKED','RECOVERY_REQUIRED')
TASK_STATES={'pending':'CREATED','ready':'QUEUED','waiting_approval':'WAITING_APPROVAL','running':'RUNNING',
 'succeeded':'SUCCEEDED','failed':'FAILED','cancelled':'CANCELLED','blocked':'BLOCKED'}
def object_schema(properties,required):return {'type':'object','properties':properties,'required':required,'additionalProperties':False}
TEXT={'type':'string','maxLength':2000};HASH={'type':'string','pattern':r'^[a-f0-9]{64}$'}
CONTEXT=object_schema({'project_id':{'type':'string','pattern':ID['project_id']},'session_id':{'type':'string','pattern':ID['project_id']},'mode':{'enum':['economic','finance','big_data_explore']}},['project_id','session_id','mode'])
DATASET=object_schema({'dataset_id':{'type':'string','pattern':ID['dataset_id']},'sha256':HASH},['dataset_id','sha256'])
SCHEMAS={'ProjectContext':CONTEXT,'DatasetRef':DATASET}
STRINGS={'type':'array','items':TEXT,'maxItems':1000};OBJ={'type':'object'}
SCHEMAS.update({
 'ToolDescriptor':{'type':'object','required':['tool_id','domain','name','description','use_cases','input_schema','output_schema','prerequisites','dependencies','permission','version','available'],
   'properties':{'tool_id':TEXT,'domain':{'enum':['shared','economic','finance','big_data_explore']},'name':TEXT,'description':TEXT,'use_cases':STRINGS,'input_schema':OBJ,'output_schema':OBJ,'prerequisites':STRINGS,'dependencies':STRINGS,'version':TEXT,'available':{'type':'boolean'}}},
 'TaskSpec':{'type':'object','required':['task_id','project_context','dependencies','status','tool_id','parameters','approval_requests'],
   'properties':{'task_id':TEXT,'project_context':CONTEXT,'dependencies':STRINGS,'status':{'enum':list(STATES)},'tool_id':TEXT,'parameters':OBJ,'approval_requests':{'type':'array'}}},
 'ApprovalRequest':{'type':'object','required':['permission_id','tool_call_id','arguments','arguments_sha256','status','at'],
   'properties':{'permission_id':TEXT,'tool_call_id':TEXT,'arguments':OBJ,'arguments_sha256':HASH,'status':TEXT,'at':TEXT}},
 'ExecutionResult':object_schema({'ok':{'type':'boolean'},'summary':TEXT,'data':OBJ,'artifacts':{'type':'array'},'warnings':{'type':'array'},'error':{'type':['object','null']}},['ok','summary','data','artifacts','warnings','error']),
 'ExperimentRecord':{'type':'object','required':['experiment_id','project_id','research_goal','domain','dataset_version','method','parameters','tool_version','code_version','runtime_environment','random_seed','task_references','artifacts','execution_status','created_at'],
   'properties':{'experiment_id':{'type':'string','pattern':ID['experiment_id']},'project_id':{'type':'string','pattern':ID['project_id']},'domain':{'enum':['economic','finance','big_data_explore']},'dataset_version':{'type':'array','items':DATASET},'parameters':OBJ,'runtime_environment':OBJ,'task_references':{'type':'array'},'execution_status':{'enum':['SUCCEEDED','FAILED','CANCELLED','RUNNING','RECOVERY_REQUIRED']}}},
 'ArtifactRef':{'type':'object','required':['artifact_id','project_id','experiment_id','task_id','artifact_type','file_path','file_hash','created_at','metadata'],
   'properties':{'artifact_id':{'type':'string','pattern':ID['artifact_id']},'project_id':{'type':'string','pattern':ID['project_id']},'file_path':TEXT,'file_hash':HASH,'metadata':OBJ,'created_at':TEXT}}
})
def validate(name,value):
    if name=='ChartConfig':
        from chart_store import schema
        SCHEMAS[name]=schema()
    if name not in SCHEMAS:raise DatasetError('CONTRACT_UNKNOWN','Unknown shared contract')
    if len(encode_result(value))>2_000_000 or not Draft202012Validator(SCHEMAS[name]).is_valid(value):
        raise DatasetError('CONTRACT_INVALID',name+' does not match shared v1 contract')
    if isinstance(value,dict):
        for key in ('created_at','at'):
            if key in value:timestamp(value[key])
    return value
def identifier(kind,value):
    if kind not in ID or not isinstance(value,str) or not re.fullmatch(ID[kind],value):raise DatasetError('INVALID_REFERENCE','Invalid '+kind)
    return value
def timestamp(value):
    try:
        parsed=dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:raise ValueError()
        return value
    except (TypeError,ValueError):raise DatasetError('CONTRACT_INVALID','Timestamp must include timezone')
