import {useEffect,useMemo,useRef,useState} from 'react';
import type {ChartConfig,OpenChart,AxisConfig,ArtifactRef} from '@shared/types/infrastructure';
import {chartSvg} from '@shared/lib/research-chart';
import './shared-research.css';
const TYPE_NAMES:Record<string,string>={line:'折线图',bar:'柱状图',scatter:'散点图',histogram:'直方图',box:'箱线图（min/max须）',heatmap:'热力图',coefficient:'系数与置信区间'};
export function VisualizationWorkbench({projectId,path,initial,readOnly=false,onSaved,onDirtyChange}:{projectId:string;path?:string;initial?:OpenChart;readOnly?:boolean;onSaved?:()=>void;onDirtyChange?:(dirty:boolean)=>void}){
  const [opened,setOpened]=useState<OpenChart|null>(initial??null),[config,setConfig]=useState<ChartConfig|null>(initial?.chart.chart_config??null);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[zoom,setZoom]=useState(100),[wide,setWide]=useState(false),[trace,setTrace]=useState<unknown>();const host=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(initial){setOpened(initial);setConfig(initial.chart.chart_config);return;}let live=true;
    if(path){setBusy(true);void window.infrastructure.request(projectId,'chart.from_file',{path}).then(r=>{if(live){setOpened(r as OpenChart);setConfig((r as OpenChart).chart.chart_config);}}).catch(e=>{if(live)setError(String(e));}).finally(()=>{if(live)setBusy(false);});}return()=>{live=false;};},[projectId,path,initial]);
  const preview=useMemo(()=>{if(!opened||!config)return {svg:'',error:''};try{return {svg:chartSvg(opened.data,config),error:''};}catch(e){return {svg:'',error:String(e)};}},[opened,config]);
  const dirty=!!opened&&!!config&&JSON.stringify(opened.chart.chart_config)!==JSON.stringify(config);
  useEffect(()=>{onDirtyChange?.(dirty&&!readOnly);return()=>onDirtyChange?.(false);},[dirty,readOnly,onDirtyChange]);
  function patch(p:Partial<ChartConfig>){setConfig(c=>c?{...c,...p}:c);setNotice('');}
  function axis(which:'x_axis'|'y_axis',key:keyof AxisConfig,value:unknown){if(config)patch({[which]:{...config[which],[key]:value}});}
  async function save(){if(!opened||!config)return;setBusy(true);setError('');try{
    const r=await window.infrastructure.request(projectId,'chart.save',{chart_id:opened.chart.chart_id,config,expected_version:opened.versions.length});
    if(r){setOpened(r as OpenChart);setConfig((r as OpenChart).chart.chart_config);setNotice('已保存新显示版本；原始数据与计算结果不变。');onSaved?.();}
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  async function exportChart(format:'svg'|'png'){if(!opened)return;setBusy(true);setError('');try{
    const artifact=await window.infrastructure.request(projectId,'chart.export',{chart_id:opened.chart.chart_id,version:opened.chart.version,format}) as ArtifactRef|null;
    if(artifact){setNotice('已导出：'+artifact.file_path);onSaved?.();}
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  async function version(v:number){if(!opened)return;if(dirty&&!window.confirm('放弃当前未保存的图表显示设置并打开历史版本？'))return;setError('');setBusy(true);try{const next=await window.infrastructure.request(projectId,'chart.open',{chart_id:opened.chart.chart_id,version:v}) as OpenChart;setOpened(next);setConfig(next.chart.chart_config);}catch(e){setError(String(e));}finally{setBusy(false);}}
  if(!opened||!config)return <div role="status" className="sr-empty">{error||'正在核对图表来源…'}</div>;
  return <div ref={host} className={'vw-workbench'+(wide?' vw-wide':'')} aria-label="Visualization Workbench">
    <div className="si-toolbar"><strong>图表检查</strong><button onClick={()=>setZoom(z=>Math.max(50,z-10))}>缩小</button><span>{zoom}%</span><button onClick={()=>setZoom(z=>Math.min(250,z+10))}>放大</button><button onClick={()=>{setZoom(100);setWide(false);}}>适应</button>
      <button onClick={()=>setWide(v=>!v)}>{wide?'退出宽屏':'宽屏'}</button><button onClick={()=>void host.current?.requestFullscreen().catch(e=>setError(String(e)))}>全屏</button>
      <button onClick={()=>setConfig(structuredClone(opened.default_config))}>恢复初始显示</button><button disabled={readOnly||busy||!dirty||!!preview.error} onClick={()=>void save()}>保存新版本</button>
      <button disabled={readOnly||busy||dirty||!!preview.error} onClick={()=>void exportChart('svg')}>导出 SVG</button><button disabled={readOnly||busy||dirty||!!preview.error} onClick={()=>void exportChart('png')}>导出 PNG</button>
      <label>显示版本 <select aria-label="图表历史版本" disabled={busy} value={opened.chart.version} onChange={e=>void version(Number(e.target.value))}>{opened.versions.map(v=><option key={v.version} value={v.version}>v{v.version} · {v.created_at.slice(0,19)}</option>)}</select></label>
    </div>
    {(error||preview.error)&&<p role="alert" className="sr-error">{error||preview.error}</p>}{notice&&<p role="status" className="sr-success">{notice}</p>}
    <div className="vw-body"><div className="vw-canvas"><div className="vw-svg" style={{width:zoom===100?'100%':config.width*zoom/100,minWidth:zoom===100?480:undefined}} dangerouslySetInnerHTML={{__html:preview.svg}}/>
      <div className="vw-series" aria-label="数据系列">{config.series.map((s,i)=><button key={s.column} aria-pressed={s.visible} style={{borderColor:s.color}} onClick={()=>patch({series:config.series.map((v,j)=>j===i?{...v,visible:!v.visible}:v)})}>{s.visible?'显示':'隐藏'} · {s.label}</button>)}</div>
      <details><summary>真实源数据表（前100行）</summary><div className="vw-data"><table><thead><tr>{opened.data.columns.map(c=><th key={c}>{c}</th>)}</tr></thead><tbody>{opened.data.rows.slice(0,100).map((r,i)=><tr key={i}>{opened.data.columns.map(c=><td key={c}>{r[c]===null?'缺失':String(r[c])}</td>)}</tr>)}</tbody></table></div></details>
    </div>
    <aside className="vw-options" aria-label="图表显示参数">
      <p className="sr-note">仅调整显示。样本、变量、因子和回测规则必须另建研究任务。{dirty?'当前预览未保存；导出前请保存。':''}</p>
      <label>图表类型<select aria-label="图表类型" value={config.chart_type} onChange={e=>{const type=e.target.value as ChartConfig['chart_type'];patch({chart_type:type,...(type==='histogram'?{x_axis:{...config.x_axis,title:config.series[0].label,unit:config.y_axis.unit,format:'number',scale:'linear',min:null,max:null},y_axis:{...config.y_axis,title:'频数',unit:'observations',format:'number',scale:'linear',min:null,max:null}}:{})});}}>{opened.allowed_types.map(t=><option key={t} value={t}>{TYPE_NAMES[t]}</option>)}</select></label>
      <label>标题<input aria-label="图表标题" maxLength={200} value={config.title} onChange={e=>patch({title:e.target.value})}/></label>
      <label>副标题<input aria-label="图表副标题" maxLength={200} value={config.subtitle} onChange={e=>patch({subtitle:e.target.value})}/></label>
      <label>横轴字段<select value={config.x_column} disabled={opened.data.semantic==='coefficient'||opened.data.semantic==='matrix'} onChange={e=>patch({x_column:e.target.value})}>{opened.data.columns.map(c=><option key={c}>{c}</option>)}</select></label>
      <label>图例<select aria-label="图例位置" value={config.legend} onChange={e=>patch({legend:e.target.value as ChartConfig['legend']})}>{['top','bottom','right','none'].map(v=><option key={v}>{v}</option>)}</select></label>
      <label><input type="checkbox" checked={config.grid} onChange={e=>patch({grid:e.target.checked})}/>网格线</label>
      <div className="vw-pair">{(['width','height','font_size'] as const).map(k=><label key={k}>{k==='width'?'宽度':k==='height'?'高度':'字号'}<input type="number" aria-label={'图表'+k} min={k==='width'?480:k==='height'?320:10} max={k==='width'?2400:k==='height'?1600:24} value={config[k]} onChange={e=>patch({[k]:Number(e.target.value)})}/></label>)}</div>
      {(['x_axis','y_axis'] as const).map(which=><details key={which} open><summary>{which==='x_axis'?'横轴':'纵轴'}名称、范围与单位</summary>
        {(['title','unit'] as const).map(k=><label key={k}>{k==='title'?'名称':'源单位'}<input aria-label={which+' '+k} maxLength={200} value={config[which][k]} onChange={e=>axis(which,k,e.target.value)}/></label>)}
        <div className="vw-pair">{(['min','max'] as const).map(k=><label key={k}>{k==='min'?'下限':'上限'}<input type="number" aria-label={which+' '+k} placeholder="自动" value={config[which][k]??''} onChange={e=>axis(which,k,e.target.value===''?null:Number(e.target.value))}/></label>)}</div>
        <label>坐标<select aria-label={which+' scale'} value={config[which].scale} onChange={e=>axis(which,'scale',e.target.value)}><option value="linear">线性</option><option value="log">对数（正数）</option></select></label>
        <label>格式<select aria-label={which+' format'} value={config[which].format} onChange={e=>axis(which,'format',e.target.value)}>{['number','percent','currency','date'].map(v=><option key={v}>{v}</option>)}</select></label>
        <div className="vw-pair"><label>精度<input type="number" min={0} max={10} value={config[which].precision} onChange={e=>axis(which,'precision',Number(e.target.value))}/></label><label>刻度<input type="number" min={2} max={12} value={config[which].ticks} onChange={e=>axis(which,'ticks',Number(e.target.value))}/></label></div>
      </details>)}
      {config.chart_type==='histogram'&&<label>显示分箱数<input type="number" min={2} max={100} value={config.bins} onChange={e=>patch({bins:Number(e.target.value)})}/></label>}
      {config.series.map((s,i)=><details key={s.column}><summary>{s.label} · {s.column}</summary><label>显示名称<input value={s.label} maxLength={200} onChange={e=>patch({series:config.series.map((v,j)=>j===i?{...v,label:e.target.value}:v)})}/></label>
        <label>颜色<input type="color" value={s.color} onChange={e=>patch({series:config.series.map((v,j)=>j===i?{...v,color:e.target.value}:v)})}/></label>
        {(['width','marker'] as const).map(k=><label key={k}>{k==='width'?'线宽':'点大小'}<input type="number" min={k==='width'?.5:0} max={k==='width'?8:12} step={.5} value={s[k]} onChange={e=>patch({series:config.series.map((v,j)=>j===i?{...v,[k]:Number(e.target.value)}:v)})}/></label>)}
      </details>)}
      {!['coefficient','matrix'].includes(opened.data.semantic)&&<details><summary>选择更多数值系列</summary>{opened.data.numeric_columns.filter(c=>!config.series.some(s=>s.column===c)).map(column=><button key={column} disabled={config.series.length>=12} onClick={()=>patch({series:[...config.series,{column,label:column,visible:true,color:'#0f766e',width:2,marker:3}]})}>添加 {column}</button>)}</details>}
    </aside></div>
    <footer className="vw-source"><span>{opened.chart.chart_id} · v{opened.chart.version} · {opened.source.file_path}</span><span>实验：{opened.chart.experiment_id??'独立文件，未关联实验'} · 任务：{opened.chart.source_task_id??'未记录'}</span><span>源Hash：{opened.source.file_hash}</span>
      <p>{opened.data.warnings.join(' ')}</p><p>百分比显示按原值×100；金额仅格式化标签，不换币种。缺失点不填补。箱线图须为min/max。</p>
      <details><summary onClick={()=>{if(!trace)void window.infrastructure.request(projectId,'trace',{chart_id:opened.chart.chart_id,version:opened.chart.version}).then(setTrace).catch(e=>setError(String(e)));}}>来源、实际参数与审批记录</summary><pre>{trace?JSON.stringify(trace,null,2):'正在读取来源…'}</pre></details>
    </footer>
  </div>;
}
