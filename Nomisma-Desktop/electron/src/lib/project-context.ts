import path from 'path';
import { getDataDir } from './data-dir';
import { installedNomisma, nomismaProjectRoot } from './nomisma-runtime';
import { WorkspaceStore, checkedId } from './workspace-store';
import type { ProjectContext } from '../../../shared/types/workspace';

export {nomismaProjectRoot};
export const workspaceStore=()=>new WorkspaceStore(getDataDir(),(installedNomisma()||process.env.NOMISMA_FILE_TEST==='1')&&process.env.NOMISMA_STATE_ROOT
  ?path.join(process.env.NOMISMA_STATE_ROOT,'workspace/projects'):path.join(nomismaProjectRoot(),'workspace/projects'));
export function registeredContext(projectId:string|undefined,cwd:string,sessionId:string):ProjectContext {
  const store=workspaceStore();
  const project=projectId?store.get(projectId,true):store.read().find(p=>path.resolve(p.path).toLowerCase()===path.resolve(cwd).toLowerCase());
  if(!project)throw new Error('Unknown research project');
  store.assertSessionOwner(project.id,sessionId);
  return store.context(project.id,checkedId(sessionId),cwd);
}
export function sessionContext(data:{projectId:string;id:string}):ProjectContext {
  const store=workspaceStore();store.assertSessionOwner(data.projectId,data.id);
  const project=store.get(data.projectId);
  return {mode:project.mode,project_id:project.id,session_id:checkedId(data.id)};
}
