"""Real Python MCP processes, with registry/roots strictly inside this test run."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[1]
INSTALLED_RESEARCH=os.environ.get('NOMISMA_TEST_INSTALLED_RESEARCH_ROOT')
RUNTIME=Path(INSTALLED_RESEARCH).resolve(strict=True) if INSTALLED_RESEARCH else PROJECT
NODE=RUNTIME.parent/'node/node.exe' if INSTALLED_RESEARCH else Path('D:/node.exe')
if INSTALLED_RESEARCH and Path(sys.executable).resolve() != (RUNTIME.parent/'python/python.exe').resolve(strict=True):
    raise RuntimeError('The installation fixture must use its own Python')
TOOL_MANIFEST=json.loads((RUNTIME/'launcher/research_tool_manifest.json').read_text(encoding='utf8'))
RUN=HERE/'python-runs'/uuid.uuid4().hex
STATE=RUN/'Nomisma'
DATA=STATE/'desktop-dev/openacpui-data'
DATA.mkdir(parents=True,exist_ok=False)
entries=[]
for mode in ['economic','finance']:
    folder=RUN/mode;folder.mkdir()
    entries.append({'id':f'project_{mode}','project_id':f'project_{mode}','name':mode,'mode':mode,'path':str(folder),'createdAt':1,'status':'active'})
REGISTRY=DATA/'projects.json'
REGISTRY.write_text(json.dumps(entries),encoding='utf8')


def environment(mode):
    env=os.environ.copy()
    env.update(PYTHONUTF8='1',NOMISMA_STATE_ROOT=str(STATE),NOMISMA_PROJECT_ID=f'project_{mode}',NOMISMA_MODE=mode,
               NOMISMA_SESSION_ID=f'session_{mode}',NOMISMA_WORKSPACE=str(RUN/mode),NOMISMA_LOG_DIR=str(RUN/'audit'/mode))
    return env


async def request(mode,override=None):
    env=environment(mode);env.update(override or {})
    params=StdioServerParameters(command=sys.executable,args=['-B',str(RUNTIME/'nomisma_tools/server.py')],env=env)
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            tools=await session.list_tools()
            expected_tools=['nomisma_health_check'] if override else TOOL_MANIFEST[mode]
            if sorted(tool.name for tool in tools.tools)!=expected_tools:
                raise AssertionError('MCP tool exposure does not match the verified project scope')
            result=await session.call_tool('nomisma_health_check',{})
            return result.structured_content


async def bounded_request(mode,override=None):
    return await asyncio.wait_for(request(mode,override),timeout=25)


class ProjectMcpTests(unittest.TestCase):
    def test_01_two_real_servers_report_their_own_mode_project_and_session(self):
        receipts=[]
        for mode in ['economic','finance']:
            result=asyncio.run(bounded_request(mode));self.assertTrue(result['ok'])
            self.assertEqual(result['data']['project_context'],{'mode':mode,'project_id':f'project_{mode}','session_id':f'session_{mode}'})
            self.assertEqual(Path(result['data']['workspace']),RUN/mode)
            receipts.append(result['data']['execution_id'])
            with (RUN/f'{mode}-evidence.json').open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
        self.assertNotEqual(*receipts)

    def test_02_spoofed_mode_and_workspace_fail_without_returning_other_project_data(self):
        for override in [{'NOMISMA_MODE':'finance'},{'NOMISMA_WORKSPACE':str(RUN/'finance')},{'NOMISMA_PROJECT_ID':'unknown'}]:
            with self.subTest(override=override):
                result=asyncio.run(bounded_request('economic',override))
                self.assertFalse(result['ok'])
                self.assertEqual(set(result['data']),{'execution_id','status'})
                self.assertRegex(result['data']['execution_id'],r'^[a-f0-9]{32}$')
                self.assertEqual(result['data']['status'],'failed')
                self.assertEqual(result['artifacts'],[])
                self.assertNotIn(str(RUN),result['error']['message'])

    def test_03_launcher_context_checks_the_same_real_registry(self):
        module=RUNTIME/'launcher/project_context.cjs'
        code="const {contextWorkspace}=require(process.argv[1]);console.log(contextWorkspace(process.env,process.argv[2]));"
        good=subprocess.run([str(NODE),'-e',code,str(module),str(RUN/'legacy')],env=environment('finance'),capture_output=True,text=True)
        self.assertEqual(good.returncode,0,good.stderr);self.assertEqual(Path(good.stdout.strip()),RUN/'finance')
        env=environment('finance');env['NOMISMA_MODE']='economic'
        bad=subprocess.run([str(NODE),'-e',code,str(module),str(RUN/'legacy')],env=env,capture_output=True,text=True)
        self.assertNotEqual(bad.returncode,0)


if __name__=='__main__':
    print('Independent test run:',RUN,flush=True)
    unittest.main(verbosity=2)
