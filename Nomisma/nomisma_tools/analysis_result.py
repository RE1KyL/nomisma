"""Strict full-fidelity analysis JSON and separately bounded presentation."""
from datetime import date,datetime
import importlib.metadata as md
import json,math,platform,uuid
import numpy as np
import pandas as pd
from dataset_store import DatasetError,utc_now


def normalize_result(value):
    if value is None:return None
    if value is pd.NA or value is pd.NaT:raise DatasetError('RESULT_NOT_FINITE','Missing statistical values need an explicit null and reason')
    if isinstance(value,np.generic):value=value.item()
    if isinstance(value,(datetime,date,pd.Timestamp)):return value.isoformat()
    if isinstance(value,bool):return value
    if isinstance(value,int):return {'type':'integer','value':str(value)} if abs(value)>2**53 else value
    if isinstance(value,float):
        if not math.isfinite(value):raise DatasetError('RESULT_NOT_FINITE','Non-finite results need an explicit mathematical reason')
        return value
    if isinstance(value,str):return value
    if isinstance(value,(list,tuple)):return [normalize_result(v) for v in value]
    if isinstance(value,dict):
        if any(not isinstance(k,str) for k in value):raise DatasetError('RESULT_TYPE_UNSUPPORTED','Result keys must be text')
        return {k:normalize_result(v) for k,v in value.items()}
    raise DatasetError('RESULT_TYPE_UNSUPPORTED','Unsupported result object')


def encode_result(result):
    return json.dumps(normalize_result(result),ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf8')


def make_analysis_result(sample,computation,*,execution_id=None,request_id=None,parameters=None):
    if execution_id is None:execution_id=uuid.uuid4().hex
    import re
    if not isinstance(execution_id,str) or not re.fullmatch(r'[a-f0-9]{32}',execution_id):raise DatasetError('INVALID_ARGUMENT','Invalid execution identity')
    if request_id is not None:
        try:uuid.UUID(request_id)
        except (ValueError,TypeError,AttributeError) as e:raise DatasetError('INVALID_ARGUMENT','Invalid request identity') from e
    metadata=sample.metadata
    domain=computation.get('analysis_domain','shared')
    if domain not in ('shared','economic','finance') or domain!='shared' and metadata['mode']!=domain:
        raise DatasetError('MODE_NOT_ALLOWED','Analysis domain does not match the trusted project mode')
    return normalize_result({'schema_version':1,'analysis_id':'an_'+uuid.uuid4().hex,'execution_id':execution_id,
        'request_id':request_id,'project_context':{k:metadata[k] for k in ['mode','project_id','session_id']},
        'dataset_id':metadata['dataset_id'],'input_sha256':metadata['sha256'],'method':computation['method'],
        'analysis_domain':domain,'algorithm_version':computation.get('algorithm_version','nomisma-shared-1'),
        'parameters':parameters or {},'sample_summary':sample.summary,
        'actual_sample_size':len(sample.frame),'sample_positions':list(sample.positions),'computed_at':utc_now(),
        'software_versions':{'python':platform.python_version(),**{n:md.version(n) for n in
            ['numpy','pandas','scipy',*(['statsmodels'] if domain=='economic' else [])]}},
        'results':computation,'warnings':computation.get('warnings',[])})


def build_summary(result,artifacts,*,preview=None):
    # Full result is separately immutable; only the explicitly supplied preview can shrink.
    row={'ok':True,'summary':'Nomisma analysis computed and verified','data':{
        'analysis_id':result['analysis_id'],'execution_id':result['execution_id'],'dataset_id':result['dataset_id'],
        'input_sha256':result['input_sha256'],'method':result['method'],'actual_sample_size':result['actual_sample_size'],
        'request_id':result.get('request_id'),'project_context':result['project_context'],
        'algorithm_version':result['algorithm_version'],'sample_summary':result['sample_summary'],
        'preview':normalize_result([] if preview is None else preview),'preview_omitted':0,
        'preview_cells_truncated':0,'committed':True},'artifacts':normalize_result(artifacts),
        'warnings':normalize_result(result.get('warnings',[])),'error':None}
    if not isinstance(row['data']['preview'],list):raise DatasetError('INVALID_ARGUMENT','Preview must be an explicit list')
    row['data']['preview_omitted']=max(0,len(row['data']['preview'])-20)
    row['data']['preview']=row['data']['preview'][:20]
    def bounded(value):
        if isinstance(value,str) and len(value)>128:
            row['data']['preview_cells_truncated']+=1;return value[:128]+'…'
        if isinstance(value,dict):return {k:bounded(v) for k,v in value.items()}
        if isinstance(value,list):return [bounded(v) for v in value]
        return value
    row['data']['preview']=bounded(row['data']['preview'])
    while True:
        raw=encode_result(row)
        wire=encode_result({'content':[{'type':'text','text':raw.decode()}],'structuredContent':row,'isError':False})
        if len(raw)<=8192 and len(wire)<=20480:return row
        if not row['data']['preview']:raise DatasetError('RESOURCE_LIMIT','Minimal analysis receipt exceeds its output budget')
        row['data']['preview'].pop();row['data']['preview_omitted']+=1
