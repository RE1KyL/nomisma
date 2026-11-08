import {ipcMain} from 'electron';
import {workspaceStore,registeredContext} from '../lib/project-context';
import {researchCall,planningInstruction,planningStatus,clearPlanning} from '../lib/research-task-service';
const ALLOWED=new Set(['list','create','confirm','revise','pause','cancel','resume','context','deliver','retry']);
export function registerResearchTasks():void {
  ipcMain.handle('research-tasks:request',async(_event,projectId:string,sessionId:string,action:string,payload:Record<string,unknown>)=>{
    const project=workspaceStore().get(projectId,true);const context=registeredContext(projectId,project.path,sessionId);
    if(action==='status')return planningStatus(sessionId);
    if(action==='cancel-planning'){clearPlanning(sessionId);return planningStatus(sessionId);}
    if(!ALLOWED.has(action))throw new Error('Research action is not exposed to the renderer');
    return researchCall(context,action,payload);
  });
  ipcMain.handle('research-tasks:planning',async(_event,projectId:string,sessionId:string,objective:string)=>{
    const project=workspaceStore().get(projectId,true);return planningInstruction(registeredContext(projectId,project.path,sessionId),objective);
  });
}
