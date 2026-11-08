import {useEffect,useState} from 'react';
export function SharedResearchRecords({projectId,sessionId,onReplay,readOnly=false}:{projectId:string;sessionId?:string|null;onReplay?:()=>void;readOnly?:boolean}){
  const [open,setOpen]=useState(false),[rows,setRows]=useState<Record<string,unknown>[]>([]),[studies,setStudies]=useState<Record<string,unknown>[]>([]),[detail,setDetail]=useState<unknown>(),[error,setError]=useState(''),[busy,setBusy]=useState(false),[left,setLeft]=useState(''),[right,setRight]=useState('');
  async function refresh(){setBusy(true);setError('');try{const [e,t]=await Promise.all([window.infrastructure.request(projectId,'experiments'),window.infrastructure.request(projectId,'tasks')]);setRows(e as Record<string,unknown>[]);setStudies(t as Record<string,unknown>[]);}catch(e){setError(String(e));}finally{setBusy(false);}}
  useEffect(()=>{setRows([]);setStudies([]);setDetail(undefined);setError('');setOpen(false);},[projectId]);
  async function action(name:string,payload:Record<string,unknown>){setBusy(true);setError('');try{const r=await window.infrastructure.request(projectId,name,payload,sessionId??undefined);if(r)setDetail(r);if(name==='experiment.replay.start'&&r)onReplay?.();if(name==='experiment.capture')await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}}
  const research=[...new Set(studies.map(t=>String(t.research_id)))];
  return <details className="sr-records" open={open} onToggle={e=>{const value=e.currentTarget.open;setOpen(value);if(value&&!rows.length)void refresh();}}><summary>实验、任务与研究追踪</summary>
    {open&&<div><button disabled={busy} onClick={()=>void refresh()}>刷新研究记录</button>{error&&<p role="alert" className="sr-error">{error}</p>}
      <p>任务 {studies.length} · 实验 {rows.length}。计算审批仍在原会话中进行。</p>
      {!readOnly&&research.map(r=><button disabled={busy} key={r} title={r} onClick={()=>void action('experiment.capture',{research_id:r})}>保存实验快照 · {r.slice(-8)}</button>)}
      {rows.map(r=><div className="sr-record" key={String(r.experiment_id)}><strong>{String(r.research_goal)}</strong><span>{String(r.execution_status)}</span>
        <button disabled={busy} onClick={()=>void action('experiment.open',{experiment_id:r.experiment_id})}>查看实验</button><button disabled={busy} onClick={()=>void action('experiment.check',{experiment_id:r.experiment_id})}>检查复现条件</button>
        {Array.isArray(r.saved_versions)&&r.saved_versions.length>1&&<label>实验快照<select aria-label="历史实验快照" defaultValue="" onChange={e=>{if(e.target.value)void action('experiment.open',{experiment_id:r.experiment_id,version:Number(e.target.value)});}}><option value="">选择保存版本</option>{r.saved_versions.map(v=><option key={String(v)} value={String(v)}>v{String(v)}</option>)}</select></label>}
        <button disabled={busy||readOnly||!sessionId} title={!sessionId?'先打开同项目研究会话':''} onClick={()=>void action('experiment.replay.start',{experiment_id:r.experiment_id})}>生成复现方案（重新审批）</button>
      </div>)}
      <label>比较实验<select value={left} onChange={e=>setLeft(e.target.value)}><option value="">选择实验</option>{rows.map(r=><option key={String(r.experiment_id)} value={String(r.experiment_id)}>{String(r.experiment_id).slice(-8)}</option>)}</select></label>
      <select aria-label="比较右侧实验" value={right} onChange={e=>setRight(e.target.value)}><option value="">选择另一个实验</option>{rows.map(r=><option key={String(r.experiment_id)} value={String(r.experiment_id)}>{String(r.experiment_id).slice(-8)}</option>)}</select><button disabled={busy||!left||!right} onClick={()=>void action('experiment.compare',{left,right})}>比较参数与环境</button>
      <details><summary>任务依赖、实际参数及权限记录</summary><pre>{JSON.stringify(studies,null,2)}</pre></details>
      <button disabled={busy} onClick={()=>void action('datasets',{})}>数据版本与血缘</button><button disabled={busy} onClick={()=>void action('artifacts',{})}>成果索引</button><button disabled={busy} onClick={()=>void action('catalog',{})}>工具目录</button><button disabled={busy} onClick={()=>void action('audit',{})}>操作审计</button>
      {detail!==undefined&&<pre className="sr-record-detail">{JSON.stringify(detail,null,2)}</pre>}
    </div>}
  </details>;
}
