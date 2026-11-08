import { Archive, ArchiveRestore, ArrowUpRight, Landmark, ChartNoAxesCombined, Pencil } from 'lucide-react';
import type { ResearchMode, WorkspaceProject } from '@shared/types/workspace';
export const MODE_DETAILS={big_data_explore:{name:'Big Data Explore',description:'Data · Discovery · Exploration',Icon:ChartNoAxesCombined},economic:{name:'Economic',description:'Research · Modeling · Exploration',Icon:Landmark},finance:{name:'Finance',description:'Scripting · Quant Research · Backtesting',Icon:ChartNoAxesCombined}};
export const projectDate=(value:number|null)=>value?new Date(value).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'}):'尚未访问';
export function ProjectCards({projects,onOpen,onEdit,onArchive,onCreate,mode}:{projects:WorkspaceProject[];onOpen:(project:WorkspaceProject)=>void;
  onEdit:(project:WorkspaceProject)=>void;onArchive:(project:WorkspaceProject)=>void;onCreate:()=>void;mode?:ResearchMode}){
  if(projects.length===0)return <div className="w-empty"><strong>{mode?`还没有 ${MODE_DETAILS[mode].name} 项目`:'这里还没有项目'}</strong><p>从一个研究问题开始，创建独立的项目工作空间。</p><button onClick={onCreate}>Create Project</button></div>;
  return <div className="w-project-grid">{projects.map(project=>{
    const detail=MODE_DETAILS[project.mode];const Icon=detail.Icon;
    return <article className={`w-project-card ${project.mode}`} key={project.id}>
      <button className="w-card-open" onClick={()=>onOpen(project)} aria-label={`打开项目 ${project.name}`}>
        <div className="w-card-cover"><Icon size={32}/><span>{detail.name}</span><ArrowUpRight size={18}/></div>
        <h3>{project.name}</h3><p>{project.description||'尚未填写研究说明'}</p>
      </button>
      <div className="w-card-meta"><span className="w-badge">{project.status==='active'?'Active':'Archived'}</span><span>{projectDate(project.last_opened_at??project.updated_at)}</span></div>
      <div className="w-card-actions"><button onClick={()=>onEdit(project)}><Pencil size={14}/>重命名 / 说明</button><button onClick={()=>onArchive(project)}>{project.status==='active'?<Archive size={14}/>:<ArchiveRestore size={14}/>} {project.status==='active'?'归档':'恢复'}</button></div>
    </article>;
  })}</div>;
}
