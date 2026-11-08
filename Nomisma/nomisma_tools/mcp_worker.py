"""Private fixed-action worker; stdin is an owned relative job identity, not code."""
import json,os,re,sys
from dataset_store import ProjectDatasetStore,DatasetError


def main():
    credential_root=os.environ.get('NOMISMA_STATE_ROOT')
    relative=sys.stdin.buffer.readline(120).decode('utf8').strip()
    if not re.fullmatch(r'\.nomisma/jobs/job_[a-f0-9]{32}',relative):raise ValueError('Invalid job identity')
    authority=ProjectDatasetStore();job=authority.path(relative)
    argsfile=authority.path(relative+'/request.json')
    if argsfile.stat().st_size>32768:raise ValueError('Oversize job request')
    request=json.loads(argsfile.read_text('utf8'))
    os.environ['NOMISMA_STATE_ROOT']=str(job/'Nomisma')
    os.environ['NOMISMA_WORKSPACE']=str(job/'workspace')
    from mcp_contract import validate
    from mcp_operations import execute
    store=ProjectDatasetStore();validate(request['tool'],request['arguments'],store.context['mode'])
    from shared_store import environment
    from tool_registry import require_tool
    provenance={'execution_id':request['execution_id'],'tool_id':request['tool'],'environment':environment(),
                'tool_version':require_tool(store.context['mode'],request['tool'],request['arguments'])['version']}
    with (job/'environment.json').open('xb') as stream:
        stream.write(json.dumps(provenance,allow_nan=False).encode('utf8'));stream.flush();os.fsync(stream.fileno())
    try:reply={'ok':True,'result':execute(store,request['tool'],request['arguments'],request['execution_id'],credential_root=credential_root)}
    except DatasetError as error:reply={'ok':False,'code':error.code}
    except Exception:reply={'ok':False,'code':'TOOL_EXECUTION_FAILED'}
    raw=json.dumps(reply,ensure_ascii=False,allow_nan=False).encode('utf8')
    if len(raw)>16384:raw=b'{"ok":false,"code":"RESOURCE_LIMIT"}'
    with (job/'reply.json').open('xb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())


if __name__=='__main__':main()
