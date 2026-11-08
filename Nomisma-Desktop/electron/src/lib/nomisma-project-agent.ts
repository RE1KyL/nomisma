import path from 'path';
import fs from 'fs';
import { nomismaProjectRoot, registeredContext, workspaceStore } from './project-context';
import { nomismaPython } from './nomisma-runtime';
import { isPathRedirected } from './windows-runtime.cjs';
import type { ProjectContext } from '../../../shared/types/workspace';

function independentRoot():string{
  const root=process.env.NOMISMA_STATE_ROOT;
  if(!root||!path.isAbsolute(root)||path.basename(root).toLowerCase()!=='nomisma')throw new Error('Independent Nomisma state root is required');
  if(path.resolve(workspaceStore().dataDir).toLowerCase()!==path.resolve(root,'desktop-dev/openacpui-data').toLowerCase())throw new Error('Workspace registry and runtime state do not match');
  return root;
}
export function assertProjectResume(context:ProjectContext,agentSessionId:string|undefined):void{
  const store=workspaceStore();store.assertSessionOwner(context.project_id,context.session_id);
  const file=path.join(store.dataDir,'sessions',context.project_id,`${context.session_id}.json`);
  for(const target of [path.join(store.dataDir,'sessions'),path.dirname(file),file]){
    if(isPathRedirected(target))throw new Error('Session links are not allowed');
  }
  const prior=JSON.parse(fs.readFileSync(file,'utf8'));
  if(prior.projectId!==context.project_id||prior.id!==context.session_id||prior.agentSessionId!==agentSessionId||!agentSessionId)throw new Error('Resume session ownership mismatch');
}

export function projectAgentEnvironment(projectId:string|undefined,cwd:string,sessionId:string):{context:ProjectContext;env:Record<string,string>} {
  const context=registeredContext(projectId,cwd,sessionId);
  return {context,env:{NOMISMA_STATE_ROOT:independentRoot(),NOMISMA_PROJECT_ID:context.project_id,NOMISMA_MODE:context.mode,NOMISMA_SESSION_ID:context.session_id,NOMISMA_WORKSPACE:workspaceStore().get(context.project_id).path}};
}
export function projectMcpServers(context:ProjectContext){
  const project=workspaceStore().get(context.project_id,true);
  return [{name:'nomisma',transport:'stdio',command:nomismaPython(),args:['-B',path.join(nomismaProjectRoot(),'nomisma_tools/server.py')],env:{
    NOMISMA_PRIVACY_SETTINGS:path.join(independentRoot(),'privacy.v1.json'),
    PYTHONUTF8:'1',NOMISMA_WORKSPACE:project.path,NOMISMA_PROJECT_ID:project.id,NOMISMA_MODE:project.mode,NOMISMA_SESSION_ID:context.session_id,
    NOMISMA_STATE_ROOT:independentRoot(),NOMISMA_LOG_DIR:path.join(independentRoot(), 'logs/python',project.id),
  }}];
}
