import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, ArrowUpRight, ChevronDown, ChevronRight, FolderOpen, History, LayoutGrid, PanelLeftClose, Plus, Settings } from 'lucide-react';
import { toast } from 'sonner';
import { useAppOrchestrator } from '@/hooks/useAppOrchestrator';
import { useWorkspaceNavigation } from '@/hooks/useWorkspaceNavigation';
import { AppLayout } from '../AppLayout';
import { ResearchWorkspace } from '../ResearchWorkspace';
import { WorkspaceDivider } from '../WorkspaceDivider';
import { ResearchProjectContext } from './ProjectContext';
import { ProjectForm } from './ProjectForm';
import { MODE_DETAILS, ProjectCards, projectDate } from './ProjectCards';
import type { HistoryEntry, ResearchMode, WorkspaceProject } from '@shared/types/workspace';
import type { WorkspaceRoute } from '@/lib/workspace-routes';
import './workspace.css';

const noProjectChange=()=>{};
export function WorkspaceApp(){
  const controller=useAppOrchestrator({projectNavigation:true});
  const [excelDirty,setExcelDirty]=useState(false);const [composerDirty,setComposerDirty]=useState(false);const [fileBusy,setFileBusy]=useState(false);
  const [resetToken,setResetToken]=useState(0);
  useEffect(()=>{
    const beforeUnload=(event:BeforeUnloadEvent)=>{if(excelDirty||composerDirty||fileBusy){event.preventDefault();event.returnValue='';}};
    window.addEventListener('beforeunload',beforeUnload);return()=>window.removeEventListener('beforeunload',beforeUnload);
  },[excelDirty,composerDirty,fileBusy]);
  const guard=useCallback(()=>{
    if(fileBusy){toast.info('正在读取或保存文件，请稍候');return false;}
    if(excelDirty||composerDirty){if(!window.confirm('有未保存的显示设置或尚未发送的内容，确定放弃并离开？'))return false;setResetToken(value=>value+1);setExcelDirty(false);setComposerDirty(false);}
    return true;
  },[excelDirty,composerDirty,fileBusy]);
  const navigation=useWorkspaceNavigation(guard);const {route,navigate}=navigation;
  const projects=controller.projectManager.projects as WorkspaceProject[];
  const [collapsed,setCollapsed]=useState(false);const [groups,setGroups]=useState({economic:true,finance:true,big_data_explore:true});
  const [width,setWidth]=useState(()=>{const value=Number(localStorage.getItem('nomisma-shell-width')??242);return Math.max(120,Math.min(260,Number.isFinite(value)?value:242));});
  const root=useRef<HTMLDivElement>(null);useEffect(()=>localStorage.setItem('nomisma-shell-width',String(width)),[width]);
  const [form,setForm]=useState<{mode:ResearchMode;project?:WorkspaceProject}|null>(null);
  const [search,setSearch]=useState('');const [status,setStatus]=useState<'active'|'archived'>('active');
  const [history,setHistory]=useState<HistoryEntry[]>([]);const [historyProject,setHistoryProject]=useState('');const [historyError,setHistoryError]=useState('');
  const [routeError,setRouteError]=useState('');const touched=useRef('');const queried=useRef(new Set<string>());
  const selected=route.page==='project'?projects.find(project=>project.id===route.projectId&&project.mode===route.mode)??null:null;
  const mode='mode' in route?route.mode:null;
  const sessionId=route.page==='project'?route.sessionId:undefined;
  const refresh=controller.projectManager.refresh;
  const go=(next:WorkspaceRoute)=>{navigate(next);};
  const open=(project:WorkspaceProject)=>go({page:'project',mode:project.mode,projectId:project.id});
  const create=(value:ResearchMode='economic')=>setForm({mode:value});
  const archive=async(project:WorkspaceProject)=>{
    if(project.status==='active'&&!window.confirm(`归档“${project.name}”？项目文件和研究记录会保留，之后可以恢复。`))return;
    try{await window.workspace.update(project.id,{status:project.status==='active'?'archived':'active'});await refresh();}catch(error){toast.error('无法更新项目',{description:String(error)});}
  };
  const start=async()=>{
    if(!selected||selected.status!=='active')return;
    if(!guard())return;
    try{await controller.handleNewChat(selected.id);if(controller.agents.some(agent=>agent.id==='nomisma-dsh-acp'))navigate({page:'project',mode:selected.mode,projectId:selected.id,sessionId:'__draft__'},false,true);}
    catch(error){toast.error('无法创建工作会话',{description:String(error)});}
  };
  useEffect(()=>{
    setRouteError('');
    if(route.page!=='project'){touched.current='';return;}
    if(controller.projectManager.loading)return;
    if(!selected){setRouteError('项目不存在或不属于当前模式。原有数据未更改。');return;}
    let cancelled=false;
    if(touched.current!==selected.id){touched.current=selected.id;void window.workspace.open(selected.id).then(()=>refresh()).catch(error=>{if(!cancelled)setRouteError(String(error));});}
    if(sessionId&&sessionId!=='__draft__'){
      const session=controller.manager.sessions.find(item=>item.id===sessionId);
      if(session&&session.projectId!==selected.id){setRouteError('会话不属于当前项目');return;}
      if(session&&controller.manager.activeSessionId!==sessionId)void controller.manager.switchSession(sessionId).catch(error=>{if(!cancelled)setRouteError(String(error));});
      if(!session&&!queried.current.has(`${selected.id}/${sessionId}`)){
        queried.current.add(`${selected.id}/${sessionId}`);
        void window.claude.sessions.load(selected.id,sessionId).then(async found=>{if(!found)throw new Error('研究会话不存在');if(!cancelled)await controller.manager.refreshSessions([selected.id]);}).catch(error=>{if(!cancelled)setRouteError(String(error));});
      }
    }
    return()=>{cancelled=true;};
  },[route,selected?.id,controller.projectManager.loading,controller.manager.sessions,controller.manager.activeSessionId]);
  useEffect(()=>{
    const active=controller.manager.activeSession;
    if(route.page==='project'&&route.sessionId==='__draft__'&&active&&active.id!=='__draft__'&&active.projectId===route.projectId){
      navigate({...route,sessionId:active.id},true,true);
    }
  },[controller.manager.activeSessionId,controller.manager.activeSession,route,navigate]);
  useEffect(()=>{
    if(route.page!=='history')return;let cancelled=false;setHistoryError('');setHistory([]);
    const load=()=>window.workspace.history(route.mode,historyProject||undefined).then(items=>{if(!cancelled)setHistory(items);}).catch(error=>{if(!cancelled)setHistoryError(String(error));});
    void load();const timer=setInterval(()=>void load(),3000);return()=>{cancelled=true;clearInterval(timer);};
  },[route.page,mode,historyProject,projects]);
  useEffect(()=>{setHistoryProject('');setSearch('');setStatus('active');},[mode]);
  const activeSession=sessionId&&sessionId!=='__draft__'&&selected?{mode:selected.mode,project_id:selected.id,session_id:sessionId}:null;
  const context=useMemo(()=>({project:selected,session:activeSession}),[selected,sessionId]);
  const inProject=route.page==='project'&&!!selected&&!routeError;
  const assistantReady=inProject&&!!sessionId&&(sessionId==='__draft__'?controller.manager.isDraft&&controller.manager.draftProjectId===selected?.id:controller.manager.activeSessionId===sessionId);
  const cards=(items:WorkspaceProject[],cardMode?:ResearchMode)=>controller.projectManager.loading?<p className="w-help" role="status">正在加载项目…</p>:<ProjectCards projects={items} mode={cardMode} onOpen={open} onEdit={project=>setForm({mode:project.mode,project})} onArchive={project=>void archive(project)} onCreate={()=>create(cardMode??'economic')}/>;
  const summary=selected?<section className="w-project-summary"><span className="w-eyebrow">{MODE_DETAILS[selected.mode].name} Workspace</span><h2>{selected.name}</h2><p>{selected.description||'为这个项目补充研究问题、范围和目标，让工作更有方向。'}</p>
    <dl><dt>工作目录</dt><dd>{selected.path}</dd><dt>项目状态</dt><dd>{selected.status==='active'?'Active':'Archived · 历史与文件只读浏览'}</dd></dl>
    <div className="w-summary-actions"><button className="w-primary" onClick={()=>void start()} disabled={selected.status!=='active'}>新建工作会话</button><button onClick={()=>go({page:'project',mode:selected.mode,projectId:selected.id,section:'datasets'})}>数据集入口</button></div>
    <p className="w-help">项目文件位于右侧，可使用已有的预览与表格功能。数据导入与处理功能尚未开放。</p></section>:null;
  const idle=route.page==='project'&&route.section==='datasets'?<section className="w-project-summary"><h2>Datasets</h2><p>数据集管理入口已预留。此阶段不提供数据获取、分析或回测。</p><p>如项目已有 datasets 文件夹，可通过右侧文件树使用现有文件预览。</p></section>:route.page==='project'&&(route.section==='assistant'||sessionId)?<section className="w-project-summary"><h2>Nomisma Assistant</h2><p>{sessionId&&sessionId!=='__draft__'?'正在载入该项目的研究会话…':'研究任务始终归属于当前项目。开始新的工作会话后再提出问题。'}</p><button className="w-primary" onClick={()=>void start()} disabled={selected?.status==='archived'}>新建工作会话</button></section>:summary;
  return <ResearchProjectContext.Provider value={context}><div className="w-shell" ref={root} data-mode={mode??''} data-project-id={selected?.id??''} data-session-id={activeSession?.session_id??''}>
    <aside className={`w-sidebar${collapsed?' collapsed':''}`} style={{width:collapsed?68:width}}>
      <button className="w-brand" onClick={()=>go({page:'overview'})} aria-label="Nomisma Project Overview"><img src={`${import.meta.env.BASE_URL}brand/nomisma-symbol.png`} alt=""/>{!collapsed&&<span>NOMISMA<small>RESEARCH WORKSPACE</small></span>}</button>
      <button className={route.page==='overview'?'w-selected':''} onClick={()=>go({page:'overview'})} title="Project Overview"><LayoutGrid size={18}/>{!collapsed&&'Project Overview'}</button>
      {(['economic','finance','big_data_explore'] as const).map(value=>{const detail=MODE_DETAILS[value];const Icon=detail.Icon;return <section key={value} className={mode===value?'w-mode active':'w-mode'}>
        <button className="w-mode-heading" title={`${detail.name}: ${detail.description}`} onClick={()=>setGroups(current=>({...current,[value]:!current[value]}))} aria-expanded={groups[value]}><Icon size={20}/><span>{collapsed?detail.name.slice(0,1):detail.name}</span>{!collapsed&&(groups[value]?<ChevronDown size={14}/>:<ChevronRight size={14}/>)}</button>
        {!collapsed&&<p>{detail.description}</p>}
        {groups[value]&&<div className="w-mode-links">{(['projects','history'] as const).map(page=><button key={page} title={`${detail.name} ${page==='projects'?'Projects':'History'}`} className={mode===value&&(route.page===page||(page==='projects'&&route.page==='project'))?'w-selected':''} onClick={()=>go({page,mode:value})}>{page==='projects'?<FolderOpen size={17}/>:<History size={17}/>} {!collapsed&&(page==='projects'?'Projects':'History')}</button>)}</div>}
      </section>;})}
      <div className="w-sidebar-bottom"><button onClick={()=>go({page:'settings'})} className={route.page==='settings'?'w-selected':''} title="Settings"><Settings size={18}/>{!collapsed&&'Settings'}</button><button onClick={()=>setCollapsed(!collapsed)} aria-label="收起或展开侧栏"><PanelLeftClose size={18}/>{!collapsed&&'收起侧栏'}</button>{!collapsed&&<small>Built on Harnss · MIT</small>}</div>
    </aside>
    {!collapsed&&<WorkspaceDivider side="left" width={width} otherWidth={0} container={root} onResize={setWidth}/>}
    <main className="w-content">
      <header className="w-toolbar"><div className="w-navigation"><button onClick={navigation.back} aria-label="返回"><ArrowLeft size={18}/></button><button onClick={navigation.forward} disabled={!navigation.canForward} aria-label="前进"><ArrowRight size={18}/></button><nav aria-label="面包屑"><button onClick={()=>go({page:'overview'})}>Project Overview</button>{mode&&<><ChevronRight size={13}/><button onClick={()=>go({page:'projects',mode})}>{MODE_DETAILS[mode].name}</button></>}{selected&&<><ChevronRight size={13}/><button onClick={()=>open(selected)}>{selected.name}</button></>}{route.page==='history'&&<span> / History</span>}{sessionId&&<span> / 工作会话</span>}{route.page==='settings'&&<span> / Settings</span>}</nav></div><span className="w-toolbar-brand">FROM DATA TO DISCOVERY</span></header>
      {controller.projectManager.error&&<div role="alert" className="w-error">{controller.projectManager.error}<button onClick={()=>void refresh().catch(()=>{})}>重试加载</button></div>}
      {routeError&&<div role="alert" className="w-error">{routeError}<button onClick={()=>go(mode?{page:'projects',mode}:{page:'overview'})}>返回项目列表</button></div>}
      {route.page==='overview'&&<div className="w-page"><section className="w-welcome"><div><span className="w-eyebrow">NOMISMA · RESEARCH WORKSPACE</span><h1>Project Overview</h1><p>From Data to Discovery — 为每一个研究问题建立独立的工作空间。</p></div><button className="w-primary" onClick={()=>create()}><Plus size={17}/>Create Project</button></section>
        <section><div className="w-section-title"><h2>Recent Projects</h2></div>{cards(projects.filter(p=>p.last_opened_at&&p.status==='active').sort((a,b)=>(b.last_opened_at??0)-(a.last_opened_at??0)).slice(0,4))}</section>
        {(['economic','finance','big_data_explore'] as const).map(value=><section key={value}><div className="w-section-title"><div><h2>{MODE_DETAILS[value].name} Projects</h2><p>{MODE_DETAILS[value].description}</p></div><button onClick={()=>go({page:'projects',mode:value})}>查看全部 <ArrowRight size={15}/></button></div>{cards(projects.filter(p=>p.mode===value&&p.status==='active'),value)}</section>)}
      </div>}
      {route.page==='projects'&&<div className="w-page"><div className="w-page-title"><div><span className="w-eyebrow">{MODE_DETAILS[route.mode].description}</span><h1>{MODE_DETAILS[route.mode].name} Projects</h1></div><button className="w-primary" onClick={()=>create(route.mode)}><Plus size={17}/>Create Project</button></div><div className="w-filters"><input aria-label="搜索项目" placeholder="搜索项目名称或说明…" value={search} onChange={event=>setSearch(event.target.value)}/><div>{(['active','archived'] as const).map(value=><button key={value} className={status===value?'w-selected':''} onClick={()=>setStatus(value)}>{value==='active'?'Active':'Archived'}</button>)}</div></div>{cards(projects.filter(p=>p.mode===route.mode&&p.status===status&&`${p.name} ${p.description}`.toLowerCase().includes(search.toLowerCase())),route.mode)}</div>}
      {route.page==='history'&&<div className="w-page"><div className="w-page-title"><div><span className="w-eyebrow">Project-linked research activity</span><h1>{MODE_DETAILS[route.mode].name} History</h1></div><select aria-label="按项目筛选历史" value={historyProject} onChange={event=>setHistoryProject(event.target.value)}><option value="">全部项目</option>{projects.filter(p=>p.mode===route.mode).map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></div>{historyError?<p className="w-error" role="alert">{historyError}</p>:history.length===0?<div className="w-empty"><strong>还没有研究活动</strong><p>进入这个模式的项目并开始工作会话，历史会在这里按项目归档。</p><button onClick={()=>go({page:'projects',mode:route.mode})}>进入项目列表</button></div>:<div className="w-history-list">{history.map(item=><button key={item.history_id} onClick={()=>go({page:'project',mode:item.mode,projectId:item.project_id,sessionId:item.session_id})}><span className="w-badge">{projects.find(p=>p.id===item.project_id)?.name??item.project_id}</span><h3>{item.title}</h3><p>{item.summary}</p><footer><span>{projectDate(item.updated_at)}</span><span>{item.status}</span><ArrowUpRight size={16}/></footer></button>)}</div>}</div>}
      {inProject&&selected&&<div className="w-project-heading"><div><span className="w-badge">{MODE_DETAILS[selected.mode].name}</span><h1>{selected.name}</h1><p>{selected.description||'尚未填写研究说明'}</p></div><div><button onClick={()=>open(selected)}>项目概况</button><button onClick={()=>go({page:'project',mode:selected.mode,projectId:selected.id,section:'datasets'})}>Datasets</button><button className="w-primary" onClick={()=>void start()} disabled={selected.status==='archived'}><Plus size={16}/>工作会话</button></div></div>}
      {route.page==='settings'&&<div className="w-settings-note">Settings · 应用偏好与连接设置</div>}
      <div className="w-engine-host" hidden={!inProject&&route.page!=='settings'}>
        <ResearchWorkspace embedded projectId={selected?.id??null} projectMode={selected?.mode} sessionId={controller.manager.activeSessionId} projectReadOnly={selected?.status==='archived'} navigation={route.page==='settings'?'settings':'chat'} onNavigate={noProjectChange}
          showAssistant={route.page==='settings'||assistantReady} idleContent={idle} onDirtyChange={setExcelDirty} onBusyChange={setFileBusy} resetToken={resetToken}
          onAssistantRequest={()=>{if(selected)go({page:'project',mode:selected.mode,projectId:selected.id,section:'assistant',...(sessionId?{sessionId}: {})});}}>
          <AppLayout controller={controller} onProjectChange={noProjectChange} navigation={route.page==='settings'?'settings':'chat'} onDraftDirtyChange={setComposerDirty} draftResetToken={resetToken} projectReadOnly={selected?.status==='archived'}/>
        </ResearchWorkspace>
      </div>
      {route.page==='invalid'&&<div className="w-empty"><h1>无法识别这个页面地址</h1><button onClick={()=>go({page:'overview'})}>返回 Project Overview</button></div>}
    </main>
    {form&&<ProjectForm key={form.project?.id??`new-${form.mode}`} project={form.project} mode={form.mode} onClose={()=>setForm(null)} onSubmit={async input=>{
      const project=form.project?await window.workspace.update(form.project.id,{name:input.name,description:input.description}):await window.workspace.create(input);
      if(!project)return;await refresh();setForm(null);if(!form.project)open(project);
    }}/>}
  </div></ResearchProjectContext.Provider>;
}
