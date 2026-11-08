"""Deterministic claim extraction and immutable research delivery.

Language-model prose is not accepted as a numerical source. Original machine
results and warnings are retained, including nonsignificant coefficients and
unavailable values. This verifies provenance, not causal identification.
"""
import math,re,uuid
from analysis_result import encode_result
from analysis_store import read_analysis
from dataset_store import DatasetError,file_sha256,utc_now

def numeric_claims(value,path=''):
    out=[]
    if type(value) in (int,float):
        if not math.isfinite(value):raise DatasetError('CLAIM_INVALID','Nonfinite numeric claim')
        out.append({'pointer':path,'value':value,'display':str(value),'rounding':'none','unit':'as_defined_in_machine_result'})
    elif isinstance(value,dict):
        for k,v in value.items():out+=numeric_claims(v,path+'/'+k.replace('~','~0').replace('/','~1'))
    elif isinstance(value,list):
        for i,v in enumerate(value):out+=numeric_claims(v,path+'/'+str(i))
    if len(out)>5000:raise DatasetError('RESOURCE_LIMIT','Report exceeds claim budget; split the research delivery')
    return out

def verify_claim(document,claim):
    value=document
    try:
        for part in claim['pointer'].strip('/').split('/') if claim['pointer'] else []:
            value=value[int(part)] if isinstance(value,list) else value[part.replace('~1','/').replace('~0','~')]
        return type(value) in (int,float) and value==claim['value'] and str(value)==claim['display']
    except (ValueError,KeyError,IndexError,TypeError):return False

def fenced(document):
    raw=encode_result(document).decode('utf8')
    fence='`'*(max([3,*[len(m.group())+1 for m in re.finditer(r'`+',raw)]]))
    return fence+'json\n'+raw+'\n'+fence+'\n'

def deliver_research(store,state):
    from research_control import resolve,verify_receipt,sha
    from mcp_jobs import read_json
    if not state['tasks'] or any(t['status']!='succeeded' for t in state['tasks'].values()):
        raise DatasetError('RESEARCH_INCOMPLETE','Only verified successful research steps can be delivered')
    name='rd_'+uuid.uuid5(uuid.NAMESPACE_URL,state['research_id']+':'+str(state['plan_version'])).hex
    destination='datasets/metadata/artifacts/'+name+'.json'
    evidence_hash=sha({k:{'arguments':t['arguments'],'receipt':t['receipt']} for k,t in state['tasks'].items()})
    existing=store.path(destination,allow_missing=True)
    if existing.exists():
        prior=read_json(existing,2_000_000)
        if prior.get('evidence_sha256')!=evidence_hash:raise DatasetError('DELIVERY_CONFLICT','Existing delivery belongs to different evidence')
        # Revalidate sources even on a replay; a saved report cannot hide changed inputs.
        for t in state['tasks'].values():verify_receipt(store,t['receipt'],name=t['tool'],args=resolve(t['arguments'],state['tasks']))
        for f in prior['files']:
            if file_sha256(store.path(f['path']))!=f['sha256']:raise DatasetError('DELIVERY_CHANGED','Delivered file changed')
        return {'manifest_path':destination,'sha256':file_sha256(existing),'files':prior['files'],'claim_count':len(prior['claims'])}
    checks=[];claims=[];sections=[];all_warnings=[]
    for task in state['tasks'].values():
        args=resolve(task['arguments'],state['tasks']);checked=verify_receipt(store,task['receipt'],name=task['tool'],args=args)
        warnings=list(task['receipt']['warnings']);analysis=checked.get('verified',{}).get('analysis')
        document={'receipt':task['receipt'],'actual_arguments':args}
        if analysis:
            result,manifest=read_analysis(store,analysis['analysis_id'],expected_sha256=analysis['sha256'],namespace=analysis['namespace'])
            document.update({'method':result['method'],'actual_sample_size':result['actual_sample_size'],
                'parameters':result.get('parameters',{}),'sample_summary':result['sample_summary'],'results':result['results']})
            warnings+=result['results'].get('warnings',[])
        if checked.get('verified',{}).get('scenario'):
            from quant_experiments import read_result
            _,scenario=read_result(store,checked['verified']['scenario']['result'])
            document.update(method='finance_research_scenario',parameters=args['configuration'],results=scenario['summary'],tables=scenario['tables'],strict_backtest_ready=False)
            warnings+=scenario['summary'].get('warnings',[])
        for claim in numeric_claims(document):
            if not verify_claim(document,claim):raise DatasetError('CLAIM_INVALID','Claim differs from machine result')
            claims.append({'task_id':task['task_id'],'execution_id':task['receipt']['data']['execution_id'],**claim})
            if len(claims)>5000:raise DatasetError('RESOURCE_LIMIT','Research delivery exceeds claim budget')
        all_warnings+=warnings
        checks.append({'task_id':task['task_id'],'input_hashes_checked':True,'artifacts_checked':True,
           'machine_numbers_checked':True,'scientific_identification_proven':False,'warnings':warnings})
        sections.append('## '+task['task_id']+'\n\n'+fenced(document))
    report=('# Nomisma research delivery\n\nResearch: '+state['research_id']+'\n\nPlan version: '+str(state['plan_version'])+
       '\n\nMachine-verified results, actual parameters and all recorded warnings follow. '+
       'No causal or profitability claim is inferred from provenance checks. Charts were not generated by this delivery.\n\n'+
       fenced({'objective':state['objective'],'locked_constraints':state['locked_constraints'],'knowledge_sha256':state['knowledge_sha256']})+'\n'+
       '\n'.join(sections)+'\n## Limitations\n\n'+encode_result(all_warnings).decode('utf8')+'\n')
    if len(report.encode('utf8'))>2_000_000:raise DatasetError('RESOURCE_LIMIT','Research report exceeds 2 MB; split deliverables')
    prefix='datasets/exports/'+name
    manifest={'schema_version':'research-delivery-1','research_id':state['research_id'],'project_id':store.context['project_id'],
        'plan_version':state['plan_version'],'created_at':utc_now(),'checks':checks,'claims':claims,'evidence_sha256':evidence_hash,
        'verification_scope':'machine_provenance_and_exact_numeric_report_consistency_not_independent_scientific_truth',
        'warnings':all_warnings,'files':[]}
    with store.transaction() as t:
        text=t.file('report.json');text.write_bytes(report.encode('utf8'))
        facts=t.file('claims.json');facts.write_bytes(encode_result({'claims':claims,'checks':checks}))
        for p,dest in [(text,prefix+'.md'),(facts,prefix+'_claims.json')]:
            manifest['files'].append({'path':dest,'sha256':file_sha256(p),'bytes':p.stat().st_size})
        marker=t.file('delivery.json');marker.write_bytes(encode_result(manifest))
        if max(facts.stat().st_size,marker.stat().st_size)>2_000_000:raise DatasetError('RESOURCE_LIMIT','Delivery evidence exceeds two MB')
        digest=file_sha256(marker)
        store.publish_artifacts(t,{'report.json':prefix+'.md','claims.json':prefix+'_claims.json','delivery.json':destination})
    for f in manifest['files']:
        if file_sha256(store.path(f['path']))!=f['sha256']:raise DatasetError('DELIVERY_CHANGED','Delivered file changed')
    return {'manifest_path':destination,'sha256':digest,'files':manifest['files'],'claim_count':len(claims)}
