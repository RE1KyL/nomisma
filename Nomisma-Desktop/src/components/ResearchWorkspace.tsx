import { useEffect, useRef, useState, type ReactNode } from 'react';
import { MessageSquare, FolderOpen, History, Settings, PanelLeftClose, PanelRightClose, RefreshCw } from 'lucide-react';
import type { ResearchFile } from '@shared/types/research';
import type {OpenChart} from '@shared/types/infrastructure';
import {SpreadsheetInspector} from './SpreadsheetInspector';
import {VisualizationWorkbench} from './VisualizationWorkbench';
import {SharedResearchRecords} from './SharedResearchRecords';
import {ExploreDataPanel} from './ExploreDataPanel';
import './research-workspace.css';
import ReactMarkdown from 'react-markdown';
import { storedPanelWidths } from '@/lib/workspace-panels';
import { WorkspaceDivider } from './WorkspaceDivider';

export function ResearchWorkspace({ children, projectId, navigation, onNavigate, embedded=false, showAssistant=true, idleContent,
  onDirtyChange,onBusyChange,resetToken=0,onAssistantRequest,projectReadOnly=false,sessionId,projectMode }: {
  children: ReactNode; projectId: string | null; navigation: string; onNavigate: (page:string)=>void;
  embedded?:boolean;showAssistant?:boolean;idleContent?:ReactNode;onDirtyChange?:(dirty:boolean)=>void;
  onBusyChange?:(busy:boolean)=>void;resetToken?:number;onAssistantRequest?:()=>void;
  projectReadOnly?:boolean;
  sessionId?:string|null;
  projectMode?:'economic'|'finance'|'big_data_explore';
}) {
  const [collapsed,setCollapsed]=useState(false); const [right,setRight]=useState(true);
  const shell=useRef<HTMLDivElement>(null);
  const [widths,setWidths]=useState(()=>storedPanelWidths(localStorage.getItem('nomisma-workspace-widths')));
  const [fileTest,setFileTest]=useState(false);
  useEffect(()=>{void window.research.context().then(context=>setFileTest(context.fileTest)).catch(()=>{});},[]);
  useEffect(()=>{localStorage.setItem('nomisma-workspace-widths',JSON.stringify(widths));},[widths]);
  const [files,setFiles]=useState<ResearchFile[]>([]); const [file,setFile]=useState('');
  const [text,setText]=useState('');const [chart,setChart]=useState<OpenChart|null>(null),[imageZoom,setImageZoom]=useState(100);
  const [error,setError]=useState(''); const [busy,setBusy]=useState(false);
  const [visible,setVisible]=useState(false); const [documentProject,setDocumentProject]=useState<string|null>(null);
  const [chartDirty,setChartDirty]=useState(false);const dirty=chartDirty;
  useEffect(()=>{onDirtyChange?.(dirty);return()=>onDirtyChange?.(false);},[dirty,onDirtyChange]);
  useEffect(()=>{onBusyChange?.(busy);return()=>onBusyChange?.(false);},[busy,onBusyChange]);
  useEffect(()=>{if(resetToken){setFile('');setChart(null);setText('');setVisible(false);}},[resetToken]);
  async function refresh(){if(projectId)try{setFiles(await window.research.list(projectId));}catch(e){setError(String(e));}}
  useEffect(()=>{setFiles([]);setVisible(false);void refresh();const timer=setInterval(()=>void refresh(),3000);return()=>clearInterval(timer);},[projectId]);
  useEffect(()=>{if(navigation!=='chat')setVisible(false);},[navigation]);
  async function open(relative:string){
    if(dirty&&!window.confirm('有未保存修改，确定放弃并打开其他文件？'))return;
    if(!projectId)return;setBusy(true);setError('');
    try{
      const content=(/\.(xlsx|csv|tsv)$/i.test(relative)||relative.endsWith('/manifest.json'))||relative.endsWith('.chart.json')?'':await window.research.read(projectId,relative);
      setDocumentProject(projectId);setChart(null);setText(content);setFile(relative);setImageZoom(100);setVisible(true);onNavigate('chat');
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  useEffect(()=>{const guard=(e:BeforeUnloadEvent)=>{if(dirty){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',guard);return()=>window.removeEventListener('beforeunload',guard);},[dirty]);
  async function createChart(){if(!projectId)return;setBusy(true);setError('');try{
    const inspected=await window.infrastructure.request(projectId,'artifact.inspect',{path:file}) as {sha256:string};
    const result=await window.infrastructure.request(projectId,'chart.create_from_file',{path:file,sha256:inspected.sha256});
    if(result){setChart(result as OpenChart);void refresh();}
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  const navigationItems=[{id:'chat',label:'对话',Icon:MessageSquare},{id:'projects',label:'项目',Icon:FolderOpen},{id:'history',label:'历史记录',Icon:History},{id:'settings',label:'设置',Icon:Settings}];
  function tree(prefix=''):ReactNode{return files.filter(f=>{const p=f.path.split('/');p.pop();return p.join('/')===prefix;}).map(f=>f.directory?<details key={f.path} open={undefined}><summary>{f.path.split('/').pop()}</summary>{tree(f.path)}</details>:<button key={f.path} className={file===f.path?'selected':''} title={f.path} onClick={()=>void open(f.path)}>{f.path.split('/').pop()}</button>);}
  return <div ref={shell} className="nomisma-shell" data-section={navigation}>
    {!embedded&&<nav className={collapsed?'n-nav collapsed':'n-nav'} style={{width:collapsed?64:widths.left}} aria-label="主导航">
      <img src={`${import.meta.env.BASE_URL}brand/nomisma-symbol.png`} alt="NOMISMA" className="n-symbol"/>
      {!collapsed&&<strong>NOMISMA<small>RESEARCH WORKSPACE</small></strong>}
      {navigationItems.map(({id,label,Icon})=><button key={id} title={label} aria-label={label} aria-current={navigation===id?'page':undefined} className={navigation===id?'selected':''} onClick={()=>onNavigate(id)}><Icon size={19}/>{!collapsed&&label}</button>)}
      <button aria-label="展开或收起导航" onClick={()=>setCollapsed(!collapsed)}><PanelLeftClose size={18}/></button>
      {!collapsed&&<small className="n-credit">Built on Harnss · MIT</small>}
    </nav>}
    {!embedded&&!collapsed&&<WorkspaceDivider side="left" width={widths.left} otherWidth={right?widths.right:0} container={shell} onResize={left=>setWidths(current=>({...current,left}))}/>}
    <main className="n-main">
      <header className="n-header"><span>{embedded?'项目工作区':'RESEARCH WORKSPACE'}</span><span>{projectId?'当前研究项目':'请在“项目”中选择研究项目'}</span><button aria-label="展开或收起项目文件" onClick={()=>setRight(!right)}><PanelRightClose size={18}/></button></header>
      {fileTest&&<div role="status" className="n-file-test">文件界面测试模式 · 不连接模型。发送研究对话请关闭此窗口后使用正式 Nomisma 启动入口。</div>}
      <div className="n-tabs"><button className={!visible?'selected':''} onClick={()=>{setVisible(false);onAssistantRequest?.();}}>Nomisma Assistant</button>{file&&documentProject===projectId&&<button className={visible?'selected':''} onClick={()=>setVisible(true)}>{(/\.(xlsx|csv|tsv)$/i.test(file)||file.endsWith('/manifest.json'))?'Spreadsheet Inspector':chart||file.endsWith('.chart.json')?'Visualization Workbench':file.endsWith('.py')?'Python Script Viewer':/\.(png|jpg|jpeg|webp|svg)$/i.test(file)?'Chart Viewer':'Report Viewer'} · {file.split('/').pop()}</button>}</div>
      {error&&<div role="alert" className="n-error">{error}</div>}
      <div className="n-agent" hidden={visible||!showAssistant}>{children}</div>
      {!visible&&!showAssistant&&<div className="w-idle-content">{idleContent}</div>}
      {visible&&<section className="n-document" aria-label="文件工作区">
        <div className="n-filebar"><span>{file}</span><span>{projectReadOnly?'归档项目 · 只读':'检查视图 · 原始文件只读'}</span>{!projectReadOnly&&(/\.csv$/i.test(file)||file.endsWith('/results.json'))&&!chart&&<button disabled={busy} onClick={()=>void createChart()}>基于真实数据创建图表</button>}</div>
        {chart||file.endsWith('.chart.json')?<VisualizationWorkbench key={projectId+file} projectId={projectId!} path={file} initial={chart??undefined} readOnly={projectReadOnly} onDirtyChange={setChartDirty} onSaved={()=>void refresh()}/>:
          (/\.(xlsx|csv|tsv)$/i.test(file)||file.endsWith('/manifest.json'))?<SpreadsheetInspector key={projectId+file} projectId={projectId!} path={file} onBusy={onBusyChange}/>:
          /\.(png|jpg|jpeg|webp|svg)$/i.test(file)?<div className="n-preview"><div className="si-toolbar"><button onClick={()=>setImageZoom(v=>Math.max(25,v-25))}>缩小</button><button onClick={()=>setImageZoom(v=>Math.min(300,v+25))}>放大</button><button onClick={()=>setImageZoom(100)}>重置</button><span>静态图像 · 无原数据点时不推测悬停值</span></div><img src={text} alt={file} style={{maxWidth:'none',width:imageZoom+'%'}}/></div>:
          /\.pdf$/i.test(file)?<iframe title={file} src={text} style={{flex:1,minHeight:400,border:0}}/>:
          file.endsWith('.md')?<article className="n-text prose"><ReactMarkdown>{text}</ReactMarkdown></article>:<pre className="n-text">{text}</pre>}
      </section>}
    </main>
    {right&&projectId&&<><WorkspaceDivider side="right" width={widths.right} otherWidth={embedded?0:collapsed?64:widths.left} container={shell} onResize={right=>setWidths(current=>({...current,right}))}/><aside className="n-explorer" style={{width:widths.right}}><header>Workspace Explorer <button aria-label="刷新文件" onClick={()=>void refresh()}><RefreshCw size={16}/></button></header>{window.infrastructure&&<SharedResearchRecords projectId={projectId} sessionId={sessionId} readOnly={projectReadOnly} onReplay={()=>{setVisible(false);onAssistantRequest?.();}}/>}
      {window.infrastructure&&projectMode==='big_data_explore'&&<ExploreDataPanel key={projectId} projectId={projectId} sessionId={sessionId}
        readOnly={projectReadOnly} paths={files.filter(item=>!item.directory&&/\.(csv|tsv|xlsx|json|jsonl|parquet|sqlite|db|txt)$/i.test(item.path)&&!item.path.startsWith('datasets/')).map(item=>item.path)}
        onOpen={relative=>void open(relative)}/>}
      {busy&&<p>正在读取…</p>}{tree()}</aside></>}
  </div>;
}
