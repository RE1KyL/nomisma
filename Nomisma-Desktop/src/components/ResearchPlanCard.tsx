import {useCallback,useEffect,useRef,useState} from 'react';
import type {ResearchState} from '@shared/types/research-task';

const LABELS:Record<string,string>={proposed:'待确认方案',active:'研究进行中',computed:'计算已完成，待核验交付',paused:'已暂停',
  cancelling:'正在取消并核对回执',cancelled:'已取消',completed:'已交付',recovery_required:'需要核对执行状态',
  pending:'等待前置步骤',ready:'可执行',waiting_approval:'等待本次工具审批',running:'正在计算',succeeded:'成果已核验',failed:'执行失败',blocked:'已阻断'};

export function ResearchPlanCard({projectId,sessionId,busy,onSend}:{projectId:string;sessionId:string;busy:boolean;onSend:(text:string)=>void}){
  const [open,setOpen]=useState(false);const [states,setStates]=useState<ResearchState[]>([]);const [goal,setGoal]=useState('');
  const [error,setError]=useState('');const [working,setWorking]=useState(false);const reading=useRef(false);
  const [planningOpen,setPlanningOpen]=useState(false);
  const refresh=useCallback(async()=>{
    if(!window.researchTasks||reading.current)return;reading.current=true;
    try{
      setStates(await window.researchTasks.request(projectId,sessionId,'list',{}) as ResearchState[]);
      const status=await window.researchTasks.request(projectId,sessionId,'status',{}) as {pending:boolean;error?:string};setPlanningOpen(status.pending);if(status.error)setError(status.error);
    }
    catch(e){setError(String(e));}finally{reading.current=false;}
  },[projectId,sessionId]);
  useEffect(()=>{setStates([]);setError('');setOpen(false);setPlanningOpen(false);},[projectId,sessionId]);
  useEffect(()=>{if(!open)return;void refresh();const t=setInterval(()=>void refresh(),2500);return()=>clearInterval(t);},[open,refresh]);
  const state=states.filter(s=>s.session_id===sessionId).at(-1);
  async function act(action:string,extra:Record<string,unknown>={}){
    if(!state)return;setWorking(true);setError('');
    try{
      await window.researchTasks.request(projectId,sessionId,action,{research_id:state.research_id,version:state.version,...extra});
      if((action==='pause'||action==='cancel')&&busy){const r=await window.claude.acp.cancel(sessionId);if(r?.error)throw new Error(r.error);}
      if(action==='confirm'||action==='resume'||action==='retry')onSend('继续已确认的研究。读取当前任务状态，只执行可执行步骤；逐次展示真实参数并等待工具审批，遇到研究定义变更先说明。');
      await refresh();
    }catch(e){setError(String(e));}finally{setWorking(false);}
  }
  async function planning(){
    setWorking(true);setError('');
    try{const prompt=await window.researchTasks.planning(projectId,sessionId,goal);onSend(prompt);}
    catch(e){setError(String(e));}finally{setWorking(false);}
  }
  if(!window.researchTasks)return null;
  return <section className="mx-4 mt-14 mb-2 rounded-lg border border-border/60 bg-background p-3 text-sm" aria-label="研究计划">
    <button type="button" className="font-medium" aria-expanded={open} onClick={()=>setOpen(v=>!v)}>研究计划 · {state?(LABELS[state.status]??state.status):'提出目标，审阅方案与成果'}</button>
    {open&&<div className="mt-3 space-y-3 max-h-72 overflow-auto">
      {error&&<p role="alert" className="text-red-600">{error}</p>}
      {planningOpen&&!busy&&<button disabled={working} onClick={()=>void window.researchTasks.request(projectId,sessionId,'cancel-planning',{}).then(()=>{setError('');void refresh();}).catch(e=>setError(String(e)))}>结束本次方案规划</button>}
      {!state||['completed','cancelled'].includes(state.status)?<>
        <textarea aria-label="研究目标" className="w-full rounded border p-2 bg-transparent" rows={2} value={goal} onChange={e=>setGoal(e.target.value)} placeholder="描述研究目标、样本与限制条件" maxLength={2000}/>
        <button type="button" disabled={busy||working||!goal.trim()} onClick={()=>void planning()}>制定研究方案</button>
        <p className="text-xs opacity-70">方案先供审阅，计算仍逐次审批。信息不足时请先回答对话中的问题；未出现有效计划时可补充目标后重新规划。</p>
      </>:<>
        <p>{state.objective}</p>
        {!busy&&<details><summary>补充目标或调整研究方案</summary>
          <textarea aria-label="调整研究目标" className="w-full rounded border p-2 bg-transparent" rows={2} value={goal} onChange={e=>setGoal(e.target.value)} maxLength={2000}/>
          <button disabled={working||!goal.trim()} onClick={()=>void planning()}>提出修订方案（需重新确认）</button>
        </details>}
        <details><summary>研究定义与方案版本 {state.plan_version}</summary><pre className="whitespace-pre-wrap text-xs">{JSON.stringify(state.locked_constraints,null,2)}</pre></details>
        <ol className="space-y-2">{Object.values(state.tasks).map(t=><li key={t.task_id}>
          <strong>{t.task_name}</strong> · {LABELS[t.status]??t.status}{t.reason&&<p className="text-xs">{t.reason}</p>}
          <details><summary className="text-xs">方法、参数与依赖</summary><pre className="whitespace-pre-wrap text-xs">{JSON.stringify({tool:t.tool,arguments:t.arguments,dependencies:t.dependencies},null,2)}</pre></details>
          {['TASK_CANCELLED','USER_DENIED','NETWORK_TIMEOUT','NETWORK_UNREACHABLE','PROJECT_BUSY','APPROVAL_EXPIRED'].includes(t.reason??'')&&
            <button type="button" disabled={busy||working} onClick={()=>void act('retry',{task_id:t.task_id})}>核对后重试此步骤（仍需工具审批）</button>}
        </li>)}</ol>
        <div className="flex flex-wrap gap-3">
          {state.status==='proposed'&&<button disabled={busy||working} onClick={()=>void act('confirm')}>确认方案并开始</button>}
          {state.status==='active'&&<button disabled={busy||working} onClick={()=>onSend('继续当前已确认研究，检查真实回执后执行剩余可执行步骤。')}>继续研究</button>}
          {state.status==='active'&&<button disabled={working} onClick={()=>void act('pause')}>暂停</button>}
          {['paused','recovery_required'].includes(state.status)&&<button disabled={busy||working} onClick={()=>void act('resume')}>核对回执并恢复</button>}
          {!['completed','cancelled','computed'].includes(state.status)&&<button disabled={working} onClick={()=>void act('cancel')}>取消研究</button>}
          {state.status==='computed'&&<button disabled={busy||working} onClick={()=>void act('deliver')}>核验并生成报告</button>}
        </div>
        <p className="text-xs opacity-70">工具调用 {state.calls}/40 · 模型提示 {state.prompts}/10。方向确认不会自动允许工具执行。</p>
      </>}
      {state?.delivery&&<p>报告已保存：{state.delivery.files.map(f=>f.path).join('、')}。可在项目文件中查看。</p>}
    </div>}
  </section>;
}
