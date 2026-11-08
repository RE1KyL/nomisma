import {useState} from 'react';

interface DataVersion {
  dataset_id:string;
  version:string;
  file_path:string;
  rows:number;
  schema:Array<{name:string;pandas_dtype?:string}>;
  legacy_metadata?:{dataset_name?:string};
}
interface OperationReceipt {
  data:{source_sha256?:string;result_path?:string;output_file?:string;facts?:Record<string,unknown>};
}

export function ExploreDataPanel({projectId,sessionId,readOnly,paths,onOpen}:{
  projectId:string;sessionId?:string|null;readOnly:boolean;paths:string[];onOpen:(path:string)=>void;
}) {
  const [open,setOpen]=useState(false),[versions,setVersions]=useState<DataVersion[]>([]);
  const [path,setPath]=useState(''),[table,setTable]=useState(''),[sheet,setSheet]=useState('');
  const [probe,setProbe]=useState<{source:string;sha:string}|null>(null);
  const [selection,select]=useState(''),[sql,setSql]=useState('SELECT * FROM input LIMIT 100');
  const [receipt,setReceipt]=useState<OperationReceipt|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const chosen=versions.find(row=>row.dataset_id===selection);
  async function refresh(){
    const rows=await window.infrastructure.request(projectId,'datasets',{}) as DataVersion[];
    setVersions(rows.filter(row=>row.file_path?.endsWith('/manifest.json')));
  }
  async function run(operation:()=>Promise<void>){
    setBusy(true);setError('');
    try{await operation();}catch(e){setError(e instanceof Error?e.message:String(e));}
    finally{setBusy(false);}
  }
  const invoke=(action:string,args:Record<string,unknown>)=>window.infrastructure.request(projectId,action,args,sessionId??undefined);
  const importArgs=()=>({source_path:path,...table.trim()?{table:table.trim()}:{},...sheet.trim()?{sheet:sheet.trim()}:{},
    .../\.txt$/i.test(path)?{text_records:'lines'}:{}});
  async function inspectSource(){
    setProbe(null);
    const result=await invoke('explore.probe',{parameters:importArgs()}) as OperationReceipt|null;
    if(result?.data.source_sha256)setProbe({source:path,sha:result.data.source_sha256});
  }
  async function importSource(){
    if(!probe||probe.source!==path)return;
    const result=await invoke('explore.import',{parameters:{...importArgs(),expected_sha256:probe.sha}});
    if(result){setProbe(null);await refresh();}
  }
  async function analyze(action:'explore.query'|'explore.quality'|'explore.export',format?:'csv'|'parquet'){
    if(!chosen)return;
    setReceipt(null);
    const result=await invoke(action,{dataset:{dataset_id:chosen.dataset_id,sha256:chosen.version},
      parameters:action==='explore.query'?{sql,max_rows:1000}:action==='explore.export'?{format}:{empty_strings_are_missing:true}});
    if(result)setReceipt(result as OperationReceipt);
  }
  return <details className="sr-records" onToggle={event=>{
    const visible=event.currentTarget.open;
    setOpen(visible);
    if(visible&&!versions.length)void run(refresh);
  }}><summary>数据集与 SQL</summary>
    {open&&<div>
      <button disabled={busy} onClick={()=>void run(refresh)}>刷新数据集</button>
      {!readOnly&&<details><summary>导入项目中的文件</summary>
        <label>源文件<select value={path} onChange={event=>{setPath(event.target.value);setProbe(null);setTable('');setSheet('');}}><option value="">选择项目文件</option>{paths.map(name=><option key={name}>{name}</option>)}</select></label>
        {/\.(db|sqlite)$/i.test(path)&&<label>SQLite 表名<input value={table} onChange={event=>{setTable(event.target.value);setProbe(null);}}/></label>}
        {/\.xlsx$/i.test(path)&&<label>工作表（可选）<input value={sheet} onChange={event=>{setSheet(event.target.value);setProbe(null);}}/></label>}
        <button disabled={busy||!path||(/\.(db|sqlite)$/i.test(path)&&!table.trim())} onClick={()=>void run(inspectSource)}>检查源文件</button>
        <button disabled={busy||!probe} onClick={()=>void run(importSource)}>导入已检查版本</button>
        {probe&&<p>已锁定版本 {probe.sha.slice(0,12)}</p>}
      </details>}
      <label>已注册的数据集<select value={selection} onChange={event=>{select(event.target.value);setReceipt(null);}}><option value="">选择数据集</option>{versions.map(row=><option key={row.dataset_id} value={row.dataset_id}>{row.legacy_metadata?.dataset_name??row.dataset_id} · {row.version.slice(0,8)}</option>)}</select></label>
      {chosen&&<><p>{chosen.rows.toLocaleString()} 行 · {chosen.schema.length} 列</p>
        <details><summary>字段和类型</summary><ul>{chosen.schema.map(field=><li key={field.name}>{field.name} · {field.pandas_dtype??'未标注'}</li>)}</ul></details>
        <button onClick={()=>onOpen(chosen.file_path)}>预览数据</button>
        {!readOnly&&<><button disabled={busy} onClick={()=>void run(()=>analyze('explore.quality'))}>检查数据质量</button>
          <button disabled={busy} onClick={()=>void run(()=>analyze('explore.export','csv'))}>导出 CSV</button>
          <button disabled={busy} onClick={()=>void run(()=>analyze('explore.export','parquet'))}>导出 Parquet</button>
          <label>DuckDB SQL<textarea value={sql} maxLength={8000} rows={5} onChange={event=>setSql(event.target.value)}/></label>
          <p>对已选数据集的 input 表运行单条 SELECT。操作须先确认，输出最多 1,000 行。</p>
          <button disabled={busy||!sql.trim()} onClick={()=>void run(()=>analyze('explore.query'))}>运行查询</button>
        </>}
      </>}
      {busy&&<p role="status">正在处理本地数据…</p>}
      {error&&<p role="alert" className="sr-error">{error}</p>}
      {receipt?.data.result_path&&<div>{receipt.data.output_file&&<p>已保存 {receipt.data.output_file}</p>}
        {!receipt.data.output_file?.endsWith('.parquet')&&<button onClick={()=>onOpen(receipt.data.output_file??receipt.data.result_path!.replace(/\.json$/,'.csv'))}>查看结果表 / 创建图表</button>}
        <button onClick={()=>onOpen(receipt.data.result_path!)}>查看参数和报告</button></div>}
    </div>}
  </details>;
}
