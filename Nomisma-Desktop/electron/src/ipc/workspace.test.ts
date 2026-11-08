import { afterEach,beforeEach,expect,it,vi } from 'vitest';
import fs from 'fs';import os from 'os';import path from 'path';
import type { ProjectContext, WorkspaceProject } from '../../../shared/types/workspace';
const mocked=vi.hoisted(()=>({data:'',handlers:new Map<string,(...args:unknown[])=>unknown>(),sessions:new Map<string,{projectContext?:ProjectContext;promptRunning?:boolean;lastStopReason?:string}>()}));
vi.mock('electron',()=>({app:{getAppPath:()=>process.cwd()},dialog:{showOpenDialog:vi.fn()},ipcMain:{handle:(name:string,handler:(...args:unknown[])=>unknown)=>mocked.handlers.set(name,handler)}}));
vi.mock('../lib/data-dir',()=>({getDataDir:()=>mocked.data,getProjectSessionsDir:(id:string)=>{const folder=path.join(mocked.data,'sessions',id);fs.mkdirSync(folder,{recursive:true});return folder;},getSessionFilePath:(id:string,sid:string)=>{const folder=path.join(mocked.data,'sessions',id);fs.mkdirSync(folder,{recursive:true});return path.join(folder,`${sid}.json`);}}));
vi.mock('./acp-sessions',()=>({acpSessions:mocked.sessions}));
vi.mock('../lib/logger',()=>({log:vi.fn()}));
vi.mock('../lib/error-utils',()=>({reportError:(_label:string,error:Error)=>error.message}));
import { registerWorkspace,projectHistory } from './workspace';
import { register as registerSessions } from './sessions';
import { workspaceStore } from '../lib/project-context';
import { projectAgentEnvironment,projectMcpServers,assertProjectResume } from '../lib/nomisma-project-agent';
let base:string;let economic:WorkspaceProject;let finance:WorkspaceProject;
const invoke=(name:string,...args:unknown[])=>mocked.handlers.get(name)!(null,...args);
beforeEach(async()=>{
  base=fs.mkdtempSync(path.join(os.tmpdir(),'nomisma-context-'));mocked.data=path.join(base,'Nomisma/desktop-dev/openacpui-data');mocked.handlers.clear();mocked.sessions.clear();
  vi.stubEnv('NOMISMA_STATE_ROOT',path.join(base,'Nomisma'));vi.stubEnv('NOMISMA_PROJECT_ROOT',path.join(base,'source'));vi.stubEnv('NOMISMA_DESKTOP_MODE','isolated');vi.stubEnv('NOMISMA_FILE_TEST','0');
  registerWorkspace(()=>null);registerSessions();
  economic=await invoke('workspace:create',{name:'Economic actual store',mode:'economic',description:'Macro study',location:'new'}) as WorkspaceProject;
  finance=await invoke('workspace:create',{name:'Finance actual store',mode:'finance',description:'Quant study',location:'new'}) as WorkspaceProject;
});
afterEach(()=>{vi.unstubAllEnvs();if(path.dirname(base)!==path.resolve(os.tmpdir()))throw new Error('Unsafe cleanup');fs.rmSync(base,{recursive:true,force:true});});
it('persists mode-bound work sessions and derives independent project histories',async()=>{
  for(const p of [economic,finance]){
    const sid=`session-${p.mode}`;mocked.sessions.set(sid,{projectContext:{mode:p.mode,project_id:p.id,session_id:sid},lastStopReason:'end_turn'});
    expect(await invoke('sessions:save',{projectId:p.id,id:sid,agentSessionId:`provider-${sid}`,agentId:'nomisma-dsh-acp',engine:'acp',title:p.name,createdAt:10,messages:[{role:'user',content:'Research task',timestamp:12},{role:'assistant',content:'Verified answer',timestamp:13,isStreaming:false}]})).toEqual({ok:true});
  }
  expect(projectHistory('economic').map(h=>h.project_id)).toEqual([economic.id]);
  expect(projectHistory('finance').map(h=>h.session_id)).toEqual(['session-finance']);
  expect(projectHistory('economic')[0].status).toBe('completed');
  mocked.sessions.clear();const saved=await invoke('sessions:load',economic.id,'session-economic') as {project_context:ProjectContext};
  expect(saved.project_context).toEqual({mode:'economic',project_id:economic.id,session_id:'session-economic'});
  expect(projectHistory('finance')[0].title).toBe(finance.name);
});
it('rejects cross-mode history, session reassignment, forged cwd and unregistered IDs',async()=>{
  expect(()=>projectHistory('finance',economic.id)).toThrow('mode');
  await invoke('sessions:save',{projectId:economic.id,id:'stable-session',createdAt:1,messages:[]});
  expect(await invoke('sessions:save',{projectId:finance.id,id:'stable-session',createdAt:1,messages:[]})).toHaveProperty('error');
  expect(fs.existsSync(path.join(mocked.data,'sessions',finance.id,'stable-session.json'))).toBe(false);
  expect(()=>projectAgentEnvironment(economic.id,finance.path,'other-session')).toThrow('do not match');
  expect(()=>workspaceStore().get('../outside')).toThrow();
  await expect(invoke('sessions:load',economic.id,'../../outside')).rejects.toThrow();
});
it('builds a fixed per-project Python service and checks resume ownership without changing session ID',async()=>{
  const context=projectAgentEnvironment(finance.id,finance.path,'stable-session');
  expect(context.env.NOMISMA_MODE).toBe('finance');expect(context.env.NOMISMA_WORKSPACE).toBe(finance.path);
  const [mcp]=projectMcpServers(context.context);expect(mcp.name).toBe('nomisma');expect(mcp.env.NOMISMA_PROJECT_ID).toBe(finance.id);expect(mcp.command).toBe('D:/AKSHARE/envs/nomisma/python.exe');
  await invoke('sessions:save',{projectId:finance.id,id:'stable-session',agentSessionId:'provider-original',createdAt:1,messages:[]});
  expect(()=>assertProjectResume(context.context,'provider-original')).not.toThrow();
  expect(()=>assertProjectResume(context.context,'provider-other')).toThrow('ownership');
  expect(()=>assertProjectResume({...context.context,project_id:economic.id},'provider-original')).toThrow('another project');
});
it('archives without deleting research records and refuses archiving a running task',async()=>{
  const context={mode:economic.mode,project_id:economic.id,session_id:'running-session'};
  mocked.sessions.set(context.session_id,{projectContext:context,promptRunning:true});
  expect(()=>invoke('workspace:update',economic.id,{status:'archived'})).toThrow('正在运行');
  mocked.sessions.clear();await invoke('workspace:update',economic.id,{status:'archived'});
  expect(fs.existsSync(economic.path)).toBe(true);expect(()=>projectAgentEnvironment(economic.id,economic.path,'next-session')).toThrow('Archived');
  await invoke('workspace:update',economic.id,{status:'active'});expect(workspaceStore().get(economic.id).mode).toBe('economic');
});
it('retains provider identity on background saves and rejects mismatched metadata',async()=>{
  await invoke('sessions:save',{projectId:economic.id,id:'resume-session',agentSessionId:'provider-stable',agentId:'nomisma-dsh-acp',createdAt:1,messages:[]});
  await invoke('sessions:save',{projectId:economic.id,id:'resume-session',createdAt:1,messages:[{role:'assistant',content:'Background completion'}]});
  const loaded=await invoke('sessions:load',economic.id,'resume-session') as {agentSessionId:string};
  expect(loaded.agentSessionId).toBe('provider-stable');
  const listed=await invoke('sessions:list',economic.id) as Array<{agentId:string;agentSessionId:string}>;
  expect(listed[0]).toMatchObject({agentId:'nomisma-dsh-acp',agentSessionId:'provider-stable'});
  expect(()=>assertProjectResume({mode:'economic',project_id:economic.id,session_id:'resume-session'},'provider-stable')).not.toThrow();
  const sidecar=path.join(mocked.data,'sessions',economic.id,'resume-session.meta.json');
  const metadata=JSON.parse(fs.readFileSync(sidecar,'utf8'));metadata.projectId=finance.id;fs.writeFileSync(sidecar,JSON.stringify(metadata));
  expect(await invoke('sessions:list',economic.id)).toEqual([]);
  expect(await invoke('sessions:load',economic.id,'resume-session')).toHaveProperty('projectId',economic.id);
});
