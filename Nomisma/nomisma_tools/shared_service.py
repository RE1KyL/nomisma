"""Finite trusted desktop service. No model code, shell, arbitrary function or roots."""
import base64,json,re,sys,uuid
from dataset_store import ProjectDatasetStore,DatasetError,file_sha256,utc_now
from analysis_result import encode_result
from mcp_jobs import lock,read_json
from shared_store import (audit,artifacts,capture_experiment,data_catalog,environment,experiments,read_artifact,
 read_experiment,register_artifact,replay_plan,reproduction_check,task_catalog,publish_json)

EXPLORE_OPERATIONS={'explore.query':'nomisma_explore_query','explore.quality':'nomisma_explore_quality',
                    'explore.import':'nomisma_explore_import','explore.probe':'nomisma_explore_probe',
                    'explore.export':'nomisma_explore_export'}
MUTATIONS={'artifact.register','experiment.capture','chart.create','chart.create_from_file','chart.save','chart.publish_export',*EXPLORE_OPERATIONS}
ACTIONS={'catalog','datasets','tasks','experiments','experiment.open','experiment.compare','experiment.check','experiment.replay',
 'experiment.capture','artifacts','artifact.register','artifact.inspect','chart.create','chart.open','chart.save','chart.from_file',
 'chart.publish_export','chart.create_from_file','sheet.page','trace','audit','audit.decision',*EXPLORE_OPERATIONS}
def dispatch(store,action,p,authorization):
    store.check_context()
    if action not in ACTIONS:raise DatasetError('ACTION_INVALID','Shared action is not supported')
    if action in MUTATIONS and authorization!='user_confirmed':raise DatasetError('APPROVAL_REQUIRED','This write requires explicit native user confirmation')
    if action=='catalog':
        from tool_registry import descriptors
        return descriptors(store.context['mode'],p.get('query',''),p.get('include_unavailable',True))
    if action=='datasets':return data_catalog(store)
    if action=='tasks':return task_catalog(store)[-200:]
    if action=='sheet.page':
        from spreadsheet_inspector import inspect
        return inspect(store,p)
    if action=='experiments':return [{k:v for k,v in r.items() if k in ('experiment_id','research_goal','domain','created_at','execution_status','method','research_id','plan_version','legacy_run_id','version','saved_versions')} for r in experiments(store)]
    if action=='experiment.capture':return capture_experiment(store,p['research_id'])
    if action=='experiment.open':return read_experiment(store,p['experiment_id'],p.get('version'))
    if action=='experiment.check':return reproduction_check(store,p['experiment_id'])
    if action=='experiment.replay':return replay_plan(store,p['experiment_id'])
    if action=='experiment.compare':
        a=read_experiment(store,p['left']);b=read_experiment(store,p['right'])
        return {'left':a['experiment_id'],'right':b['experiment_id'],'same_data':a['dataset_version']==b['dataset_version'],
           'same_environment':a['runtime_environment']==b['runtime_environment'],'parameters':{'left':a['parameters'],'right':b['parameters']},
           'methods':{'left':a['method'],'right':b['method']},'winner':None,'note':'Parameter comparison does not select a significant or profitable winner.'}
    if action=='artifacts':return artifacts(store)
    if action=='artifact.inspect':
        path=p['path'];file=store.path(path)
        from shared_store import artifact_type,artifact_viewer
        kind=artifact_type(path)
        return {'path':path,'sha256':file_sha256(file),'bytes':file.stat().st_size,'type':kind,'viewer':artifact_viewer(kind)}
    if action=='artifact.register':return register_artifact(store,p['path'],p['sha256'])
    if action in ('chart.create','chart.create_from_file','chart.open','chart.save','chart.from_file','chart.publish_export'):
        from chart_store import create_chart,open_chart,save_chart
        if action=='chart.create':return create_chart(store,p['source_artifact_id'],p.get('config'))
        if action=='chart.create_from_file':
            a=register_artifact(store,p['path'],p['sha256']);return create_chart(store,a['artifact_id'])
        if action=='chart.open':return open_chart(store,p['chart_id'],p.get('version'))
        if action=='chart.save':return save_chart(store,p['chart_id'],p['config'],p['expected_version'])
        if action=='chart.from_file':
            if not p['path'].endswith('.chart.json'):raise DatasetError('CHART_SOURCE_INVALID','Expected chart reference file')
            pointer=read_json(store.path(p['path']))
            if pointer.get('project_id')!=store.context['project_id']:raise DatasetError('PROJECT_CONTEXT_INVALID','Chart file belongs to another project')
            return open_chart(store,pointer['chart_id'],p.get('version'))
        opened=open_chart(store,p['chart_id'],p['version']);fmt=p['format']
        if fmt not in ('png','svg'):raise DatasetError('EXPORT_INVALID','Only PNG/SVG exports are supported')
        raw=base64.b64decode(p['content_base64'],validate=True)
        if len(raw)>12_000_000:raise DatasetError('RESOURCE_LIMIT','Chart export exceeds 12 MB')
        if fmt=='png':
            import struct
            if raw[:8]!=b'\x89PNG\r\n\x1a\n' or len(raw)<33:raise DatasetError('EXPORT_INVALID','Expected real PNG bytes')
            width,height=struct.unpack('>II',raw[16:24])
            if (width,height)!=(opened['chart']['chart_config']['width'],opened['chart']['chart_config']['height']):raise DatasetError('EXPORT_INVALID','PNG dimensions do not match the saved chart')
        else:
            from xml.etree import ElementTree as E
            root=E.fromstring(raw)
            if root.tag.rsplit('}',1)[-1]!='svg':raise DatasetError('EXPORT_INVALID','Expected SVG')
            if any(n.tag.rsplit('}',1)[-1] in ('script','foreignObject','image','use') or any(k.lower().startswith('on') or k.rsplit('}',1)[-1]=='href' for k in n.attrib) for n in root.iter()):raise DatasetError('EXPORT_INVALID','SVG contains active or external content')
        name=p['chart_id']+'_v'+str(p['version'])+'_'+uuid.uuid4().hex[:10]+'.'+fmt;dest='datasets/exports/'+name
        with store.transaction() as tx:
            f=tx.file('render.json');f.write_bytes(raw);store.publish_artifacts(tx,{f.name:dest})
        a=register_artifact(store,dest,file_sha256(store.path(dest)),experiment_id=opened['chart']['experiment_id'],task_id=opened['chart']['source_task_id'],metadata={
           'chart_id':p['chart_id'],'chart_version':p['version'],'source_artifact_id':opened['source']['artifact_id'],'chart_config':opened['chart']['chart_config'],
           'rendering':'fixed desktop SVG renderer; no study recomputation'})
        return a
    if action=='trace':
        from chart_store import open_chart
        chart=open_chart(store,p['chart_id'],p.get('version')) if p.get('chart_id') else None
        artifact=chart['source'] if chart else read_artifact(store,p['artifact_id'])
        matching=[t for t in task_catalog(store) if any(a.get('path')==artifact['file_path'] and a.get('sha256')==artifact['file_hash'] for a in (t['execution_result'] or {}).get('artifacts',[]))]
        return {'project_context':store.context,'artifact':artifact,'experiment':read_experiment(store,artifact['experiment_id']) if artifact['experiment_id'] else None,
           'tasks':matching,'chart':chart['chart'] if chart else None,
           'authorization':'See original matching attempt/decision evidence; legacy standalone files have no inferred tool approval.',
           'datasets':[{k:v for k,v in r.items() if k in ('dataset_id','version','source','lineage')} for r in data_catalog(store) if r.get('status')!='STALE'],
           'viewer_configuration_modified':bool(chart and chart['chart']['version']>1)}
    if action=='audit':
        directory=store.path('.nomisma/shared/audit',allow_missing=True)
        events=[read_json(store.path(f.relative_to(store.root).as_posix())) for f in sorted(directory.glob('au_*.json'),key=lambda f:f.stat().st_mtime)[-100:]] if directory.exists() else []
        for task in task_catalog(store)[-100:]:
            for i,a in enumerate(task['approval_requests']):
                events.append({'source_kind':'persisted_research_state_projection','actor':{'kind':'agent','session_id':task['project_context']['session_id']},
                 'project_id':store.context['project_id'],'domain':store.context['mode'],'operation':task['tool_id'],'parameters':a['arguments'],
                 'authorization':'allow_once' if a.get('started_at') is not None else 'denied' if a['status']=='denied' else 'not_granted',
                 'result':task['execution_result'] if i==len(task['approval_requests'])-1 else {'recorded_attempt_status':a['status']},
                 'at':a['at'],'error':task['error_code'] if i==len(task['approval_requests'])-1 else None,'task_id':task['task_id']})
        return sorted(events,key=lambda e:e['at'])[-200:]
    if action=='audit.decision':
        if set(p)!={'operation','parameters','decision'} or p['decision'] not in ('denied','cancelled'):raise DatasetError('INVALID_ARGUMENT','Invalid native decision audit')
        return audit(store,p['operation'],p['parameters'],p['decision'],'not_executed')

def handle(store,action,payload,authorization='read_only'):
    # One metadata writer. Never held across a user prompt, Worker or native dialog.
    if not isinstance(payload,dict):raise DatasetError('INVALID_ARGUMENT','Shared payload must be an object')
    if action in EXPLORE_OPERATIONS:
        if authorization!='user_confirmed':raise DatasetError('APPROVAL_REQUIRED','Confirm this local action in the desktop')
        import anyio
        from mcp_contract import validate
        from mcp_jobs import run_job
        store.check_context()
        tool=EXPLORE_OPERATIONS[action]
        args={**payload,'request_id':str(uuid.uuid4())}
        validate(tool,args,store.context['mode'])
        return anyio.run(run_job,store,tool,args,uuid.uuid4().hex)
    from contextlib import nullcontext
    writer=action in MUTATIONS or action=='audit.decision'
    with lock(store,'.nomisma/shared/control.lock') if writer else nullcontext():
        try:
            result=dispatch(store,action,payload,authorization)
            if action not in ('audit','audit.decision','sheet.page','catalog','datasets','tasks','experiments','artifacts','artifact.inspect','chart.open','chart.from_file','experiment.open','experiment.check','experiment.compare','experiment.replay','trace'):
                clean={k:v for k,v in payload.items() if k!='content_base64'}
                if 'content_base64' in payload:clean['render_payload_sha256']=__import__('hashlib').sha256(payload['content_base64'].encode()).hexdigest()
                audit(store,action,clean,authorization,'succeeded')
            return result
        except DatasetError as exc:
            if writer and action!='audit':audit(store,action,{k:v for k,v in payload.items() if k!='content_base64'},authorization,'failed',exc.code)
            raise
def main():
    try:
        raw=sys.stdin.buffer.read(18_000_001)
        if len(raw)>18_000_000:raise DatasetError('RESOURCE_LIMIT','Shared request exceeds budget')
        r=json.loads(raw)
        if not isinstance(r,dict) or set(r)-{'action','payload','authorization'}:raise DatasetError('INVALID_ARGUMENT','Invalid fixed service request')
        from viewer_store import ViewerStore
        store=ProjectDatasetStore() if r['action'] in MUTATIONS or r['action']=='audit.decision' else ViewerStore()
        result=handle(store,r['action'],r['payload'],r.get('authorization','read_only'));out={'ok':True,'data':result}
        if len(encode_result(out))>12_000_000:raise DatasetError('RESOURCE_LIMIT','Shared response exceeds 12 MB; narrow the query')
    except DatasetError as exc:out={'ok':False,'error':{'code':exc.code,'message':str(exc)}}
    except Exception:out={'ok':False,'error':{'code':'SHARED_SERVICE_FAILED','message':'Shared operation failed; original research files were not overwritten'}}
    sys.stdout.buffer.write(encode_result(out))
if __name__=='__main__':main()
