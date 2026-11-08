import { dialog, ipcMain, type BrowserWindow } from 'electron';
import fs from 'fs';
import path from 'path';
import { workspaceStore } from '../lib/project-context';
import { checkedId, checkedMode } from '../lib/workspace-store';
import { isPathRedirected } from '../lib/windows-runtime.cjs';
import { acpSessions } from './acp-sessions';
import type { HistoryEntry, ResearchMode } from '../../../shared/types/workspace';

export function projectHistory(mode:ResearchMode,projectId?:string):HistoryEntry[]{
  checkedMode(mode);const store=workspaceStore();
  if(projectId&&store.get(projectId).mode!==mode)throw new Error('Project does not belong to this mode');
  const projects=store.read().filter(p=>p.mode===mode&&(!projectId||p.id===projectId));const entries:HistoryEntry[]=[];
  for(const project of projects){
    const directory=path.join(store.dataDir,'sessions',project.id);if(!fs.existsSync(directory))continue;
    if(isPathRedirected(directory))throw new Error('Session directory cannot be a link');
    for(const file of fs.readdirSync(directory)){
      if(!file.endsWith('.json')||file.endsWith('.meta.json'))continue;
      const sessionId=checkedId(file.slice(0,-5));store.assertSessionOwner(project.id,sessionId);const full=path.join(directory,file);
      if(isPathRedirected(full))throw new Error('Session file cannot be a link');
      const session=JSON.parse(fs.readFileSync(full,'utf8'));
      if(session.projectId!==project.id||session.id!==sessionId)throw new Error('Invalid session ownership');
      if(session.mode!==undefined&&session.mode!==mode)throw new Error('Session mode mismatch');
      const messages:Array<{role:string;content?:string;timestamp?:number;isStreaming?:boolean;isError?:boolean;toolError?:boolean}>=session.messages??[];
      const assistant=messages.filter(m=>m.role==='assistant').at(-1);
      const running=acpSessions.get(sessionId)?.promptRunning===true;
      const status:HistoryEntry['status']=running?'running':session.execution_status==='running'?'interrupted':
        ['completed','cancelled','error'].includes(session.execution_status)?session.execution_status:
        messages.some(m=>m.isError||m.toolError)?'error':assistant?.isStreaming?'interrupted':assistant?.content?'recorded':'draft';
      entries.push({history_id:sessionId,project_id:project.id,mode,session_id:sessionId,title:session.title||'未命名工作会话',activity_type:'conversation',status,
        created_at:session.createdAt??0,updated_at:Math.max(session.createdAt??0,...messages.map(m=>m.timestamp??0)),
        summary:(messages.find(m=>m.role==='user')?.content??'尚未提交研究任务').slice(0,160)});
    }
  }
  return entries.sort((a,b)=>b.updated_at-a.updated_at);
}
export function registerWorkspace(getWindow:()=>BrowserWindow|null):void {
  ipcMain.handle('workspace:create',async(_event,input:{name:string;mode:ResearchMode;description:string;location:'new'|'existing'})=>{
    checkedMode(input.mode);
    if(input.location!=='new'&&input.location!=='existing')throw new Error('Invalid project location option');
    let selected:string|undefined;
    if(input.location==='existing'){
      const window=getWindow();if(!window)throw new Error('Application window is not ready');
      const result=await dialog.showOpenDialog(window,{title:'选择项目工作目录',properties:['openDirectory']});
      if(result.canceled)return null;selected=result.filePaths[0];if(!selected)throw new Error('No project directory selected');
    }
    return workspaceStore().create(input,selected);
  });
  ipcMain.handle('workspace:update',(_event,id:string,patch:Record<string,unknown>)=>{
    if(patch.status==='archived'&&[...acpSessions.values()].some(s=>s.projectContext?.project_id===id&&s.promptRunning))throw new Error('请先结束或取消项目中正在运行的任务，再归档');
    return workspaceStore().update(id,patch);
  });
  ipcMain.handle('workspace:open',(_event,id:string)=>workspaceStore().open(id));
  ipcMain.handle('workspace:history',(_event,mode:ResearchMode,id?:string)=>projectHistory(mode,id));
}
