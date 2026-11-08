import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import type { ResearchMode, WorkspaceProject } from '@shared/types/workspace';
export function ProjectForm({project,mode,onClose,onSubmit}:{project?:WorkspaceProject;mode:ResearchMode;onClose:()=>void;
  onSubmit:(value:{name:string;mode:ResearchMode;description:string;location:'new'|'existing'})=>Promise<void>}){
  const [name,setName]=useState(project?.name??'');const [description,setDescription]=useState(project?.description??'');
  const [selectedMode,setMode]=useState(mode);const [location,setLocation]=useState<'new'|'existing'>('new');
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  return <Dialog open onOpenChange={open=>{if(!open&&!busy){if((name!==(project?.name??'')||description!==(project?.description??''))&&!window.confirm('放弃未保存的项目信息？'))return;onClose();}}}>
    <DialogContent className="w-form"><DialogHeader><DialogTitle>{project?'编辑项目信息':'Create Project'}</DialogTitle></DialogHeader>
    <form onSubmit={async event=>{event.preventDefault();setBusy(true);setError('');try{await onSubmit({name,mode:selectedMode,description,location});}catch(failure){setError(String(failure));}finally{setBusy(false);}}}>
      <label>项目名称<input autoFocus maxLength={100} required value={name} onChange={event=>setName(event.target.value)} disabled={busy}/></label>
      <label>工作模式<select value={selectedMode} disabled={!!project||busy} onChange={event=>setMode(event.target.value as ResearchMode)}><option value="economic">Economic</option><option value="finance">Finance</option><option value="big_data_explore">Big Data Explore</option></select></label>
      <p className="w-help">创建后所属模式固定；三个研究模式使用各自的项目及历史。</p>
      <label>研究说明<textarea maxLength={2000} rows={3} value={description} onChange={event=>setDescription(event.target.value)} disabled={busy}/></label>
      {!project&&<label>工作目录<select value={location} onChange={event=>setLocation(event.target.value as 'new'|'existing')} disabled={busy}><option value="new">在 Nomisma 工作区新建独立目录</option><option value="existing">关联现有文件夹（下一步选择）</option></select></label>}
      {error&&<p role="alert" className="w-error">{error}</p>}
      <div className="w-form-actions"><button type="button" disabled={busy} onClick={onClose}>取消</button><button className="w-primary" disabled={busy||!name.trim()}>{busy?'正在保存…':project?'保存修改':'创建项目'}</button></div>
    </form></DialogContent>
  </Dialog>;
}
