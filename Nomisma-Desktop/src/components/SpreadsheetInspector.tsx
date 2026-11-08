import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import '@/lib/grid-compat';
import {DataGrid,type Column,type DataGridHandle} from 'react-data-grid';
import 'react-data-grid/lib/styles.css';
import type {InspectorCell,SheetPage} from '@shared/types/infrastructure';
import './shared-research.css';
type Row={source_row:number;cells:InspectorCell[];first_cell?:InspectorCell|null};
export function columnLetter(index:number):string{let s='';for(let n=index;n>0;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
const flagNames:Record<string,string>={missing:'缺失',empty_text:'空文本（区别于空值）',duplicate:'重复（报告样本）',outlier_candidate:'异常候选（报告样本）',type_error:'类型不符',date_error:'日期不符',invalid_number:'非法数值'};
export function SpreadsheetInspector({projectId,path,onBusy}:{projectId:string;path:string;onBusy?:(busy:boolean)=>void}){
  const [page,setPage]=useState<SheetPage|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
  const [sheet,setSheet]=useState(''),[start,setStart]=useState(1),[colStart,setColStart]=useState(1),[zoom,setZoom]=useState(100);
  const [freezeRow,setFreezeRow]=useState(false),[freezeCol,setFreezeCol]=useState(true),[quality,setQuality]=useState(true);
  const [query,setQuery]=useState(''),[search,setSearch]=useState(''),[filterColumn,setFilterColumn]=useState(1),[filterText,setFilterText]=useState('');
  const [filter,setFilter]=useState<{column:number;contains:string}|undefined>(),[sort,setSort]=useState<{column:number;direction:'ascending'|'descending'}|undefined>();
  const [selected,setSelected]=useState<InspectorCell|null>(null),[jump,setJump]=useState('A1'),[rowHeight,setRowHeight]=useState(32);
  const [expected,setExpected]=useState<Record<string,string>>({});const grid=useRef<DataGridHandle>(null),host=useRef<HTMLDivElement>(null),version=useRef<string|undefined>(undefined);
  const [reload,setReload]=useState(0);
  const pendingJump=useRef<{row:number;column:number}|null>(null);
  const request=useCallback(async()=>{
    setLoading(true);onBusy?.(true);setError('');
    return window.infrastructure.request(projectId,'sheet.page',{path,...(version.current?{version:version.current}:{}),...(sheet?{sheet}:{}),start_row:start,row_count:200,start_column:colStart,column_count:50,
      search,...(filter?{filter}:{}),...(sort?{sort}:{}),expected_types:expected}) as Promise<SheetPage>;
  },[projectId,path,sheet,start,colStart,search,filter,sort,expected,onBusy,reload]);
  useEffect(()=>{let current=true;void request().then(p=>{if(current){version.current=p.version;setPage(p);setSelected(null);}}).catch(e=>{if(current)setError(String(e));}).finally(()=>{if(current){setLoading(false);onBusy?.(false);}});return()=>{current=false;onBusy?.(false);};},[request]);
  const active=page?.sheets.find(s=>s.name===page.sheet),scale=zoom/100;
  function choose(cell:InspectorCell|undefined){if(cell){setSelected(cell);setJump(cell.address);}}
  useEffect(()=>{const target=pendingJump.current;if(!target||!page)return;const rows=page.rows.filter(r=>!freezeRow||r.source_row!==1),rowIdx=rows.findIndex(r=>r.source_row===target.row);
    const cell=rows[rowIdx]?.cells.find(c=>c.column===target.column)??(target.column===1?rows[rowIdx]?.first_cell:undefined);
    if(rowIdx>=0&&cell){const idx=target.column===1&&freezeCol?1:1+target.column-colStart+(freezeCol&&colStart>1?1:0);grid.current?.scrollToCell({rowIdx,idx});grid.current?.setActivePosition({rowIdx,idx});choose(cell);pendingJump.current=null;}
  },[page,freezeRow,freezeCol,colStart]);
  const columns=useMemo<Column<Row,Row>[]>(()=>{
    const makeColumn=(n:number):Column<Row,Row>=>{const render=({row}:{row:Row})=>{const c=row.cells.find(c=>c.column===n)??(n===1?row.first_cell:undefined);return <span onClick={()=>choose(c??undefined)} title={c?.flags.map(f=>flagNames[f]??f).join('、')} className={`si-value ${c?.kind==='number'?'si-number':''} ${quality&&c?.flags.length?'si-mark si-'+c.flags[0]:''}`}>{c?.display??''}</span>;};
      return {key:String(n),name:columnLetter(n),width:Math.round(136*scale),resizable:true,frozen:freezeCol&&n===1,renderCell:render,renderSummaryCell:render};};
    return [{key:'row',name:'#',width:60,frozen:true,resizable:false,renderCell:({row})=><span className="si-row-number">{row.source_row}</span>,renderSummaryCell:()=> <span className="si-row-number">1 · 冻结</span>},
      ...(freezeCol&&colStart>1?[makeColumn(1)]:[]),...Array.from({length:Math.min(50,Math.max(0,(active?.columns??0)-colStart+1))},(_,j)=>makeColumn(colStart+j))];},
      [active?.columns,colStart,scale,freezeCol,quality]);
  function go(){const m=/^([A-Za-z]{1,3})([1-9]\d*)$/.exec(jump.trim());if(!m){setError('请输入有效源坐标，例如 C1200');return;}let n=0;for(const c of m[1].toUpperCase())n=n*26+c.charCodeAt(0)-64;
    if(n>1000||Number(m[2])>Math.max(100001,active?.rows??0)){setError('超出检查范围');return;}pendingJump.current={row:Number(m[2]),column:n};setSearch('');setFilter(undefined);setSort(undefined);setStart(Math.floor((Number(m[2])-1)/200)*200+1);setColStart(Math.floor((n-1)/50)*50+1);
    const target=page?.rows.find(r=>r.source_row===Number(m[2]));choose(target?.cells.find(c=>c.column===n));setPage(p=>p?{...p}:p);
  }
  return <div className="si-inspector" ref={host} aria-label="Spreadsheet Inspector">
    <div className="si-toolbar">
      <span>{page?.sheets.length??'…'} 工作表 · 只读检查</span>
      <label>缩放 <select aria-label="表格缩放" value={zoom} onChange={e=>setZoom(Number(e.target.value))}>{[75,90,100,110,125,150].map(z=><option key={z}>{z}</option>)}</select>%</label>
      <button onClick={()=>setZoom(Math.max(75,Math.min(150,Math.round(((host.current?.clientWidth??900)-60)/Math.max(1,Math.min(active?.columns??1,50))/136*100))))}>适应宽度</button>
      <label><input type="checkbox" checked={freezeRow} onChange={e=>setFreezeRow(e.target.checked)}/>冻结源首行</label>
      <label><input type="checkbox" checked={freezeCol} onChange={e=>setFreezeCol(e.target.checked)}/>冻结源首列</label>
      <label>行高 <input aria-label="行高" type="number" min={26} max={80} value={rowHeight} onChange={e=>setRowHeight(Math.max(26,Math.min(80,Number(e.target.value)||32)))}/></label>
      <label><input type="checkbox" checked={quality} onChange={e=>setQuality(e.target.checked)}/>质量覆盖</label>
    </div>
    <div className="si-toolbar">
      <input aria-label="定位单元格" value={jump} onChange={e=>setJump(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')go();}}/><button onClick={go}>定位</button>
      <input aria-label="查找单元格内容" placeholder="全表查找…" value={query} onChange={e=>setQuery(e.target.value)}/><button disabled={loading} onClick={()=>{setSearch(query);setStart(1);}}>查找</button>
      <label>列 <input aria-label="筛选列编号" type="number" min={1} max={active?.columns??1000} value={filterColumn} onChange={e=>setFilterColumn(Number(e.target.value))}/></label>
      <input aria-label="列筛选内容" placeholder="列包含…" value={filterText} onChange={e=>setFilterText(e.target.value)}/>
      <button disabled={loading} onClick={()=>{setFilter(filterText?{column:filterColumn,contains:filterText}:undefined);setStart(1);}}>筛选</button>
      <button disabled={loading} onClick={()=>{setSort({column:filterColumn,direction:sort?.direction==='ascending'?'descending':'ascending'});setStart(1);}}>排序 {sort?.direction==='ascending'?'↑':'↓'}</button>
      <button disabled={loading} onClick={()=>{setSearch('');setQuery('');setFilter(undefined);setFilterText('');setSort(undefined);setStart(1);}}>清除查看条件</button>
    </div>
    {error&&<p role="alert" className="sr-error">{error}<button onClick={()=>{version.current=undefined;setStart(1);setSheet('');setPage(null);setReload(v=>v+1);}}>重新读取</button></p>}
    <div className="si-formula" role="status"><strong>{selected?.address??'选择单元格'}</strong><span>{selected?.kind??'类型'} · {selected?.number_format??'格式'}</span>
      <input readOnly aria-label="完整单元格内容" value={selected?.value==null?'':String(selected.value)}/><button disabled={!selected} onClick={()=>void navigator.clipboard.writeText(selected?.value==null?'':String(selected.value)).catch(e=>setError(String(e)))}>复制完整值</button>
    </div>
    {selected?.formula&&<div className="si-formula-info">公式：{selected.formula} · 缓存值：{selected.cached_value==null?'文件中无可用缓存；未重新计算':String(selected.cached_value)}</div>}
    <div className="si-grid" style={{fontSize:13*scale}}>
      <DataGrid<Row,Row> ref={grid} className="si-data-grid rdg-light" columns={columns} rows={(page?.rows??[]).filter(r=>!freezeRow||r.source_row!==1)} rowHeight={Math.round(rowHeight*scale)} headerRowHeight={Math.round(34*scale)} rowKeyGetter={r=>r.source_row}
        topSummaryRows={freezeRow&&page?[{source_row:1,cells:page.first_row,first_cell:page.first_column_header}]:undefined}
        onCellClick={({row,column})=>choose(row.cells.find(c=>String(c.column)===column.key)??(column.key==='1'?row.first_cell??undefined:undefined))}
        onActivePositionChange={({row,column})=>{if(row&&column)choose(row.cells.find(c=>String(c.column)===column.key)??(column.key==='1'?row.first_cell??undefined:undefined));}}/>
      {loading&&<div className="si-loading" role="status">正在读取源文件分块…</div>}
    </div>
    <div className="si-footer">
      <div className="si-sheets">{page?.sheets.map(s=><button key={s.name} aria-pressed={page.sheet===s.name} onClick={()=>{setSheet(s.name);setStart(1);setColStart(1);setSelected(null);setSearch('');setFilter(undefined);setSort(undefined);}}>{s.name}</button>)}</div>
      <span>{active?.rows??0} 行 × {active?.columns??0} 列 · 查看结果 {page?.total_view_rows??0} 行</span>
      <button disabled={loading||start<=1} onClick={()=>setStart(Math.max(1,start-200))}>上一分块</button><span>结果行 {start}–{Math.min(start+199,page?.total_view_rows??0)}</span><button disabled={loading||start+200>(page?.total_view_rows??0)} onClick={()=>setStart(start+200)}>下一分块</button>
      <button disabled={colStart<=1||loading} onClick={()=>setColStart(Math.max(1,colStart-50))}>前50列</button><button disabled={loading||colStart+50>(active?.columns??0)} onClick={()=>setColStart(colStart+50)}>后50列</button>
    </div>
    <details className="si-quality"><summary>质量图例、完整内容与检查条件</summary><div className="si-legend">{Object.entries(flagNames).map(([k,v])=><span key={k} className={'si-mark si-'+k}>{v}</span>)}</div>
      <p>{page?.quality_scope} 报告：{page?.quality_reports.length??0} 份。标记不修改数据，异常候选不是错误。</p>
      <label>将当前筛选列按指定类型检查 <select aria-label="声明列检查类型" value={expected[columnLetter(filterColumn)]??''} onChange={e=>setExpected(v=>{const next={...v};if(e.target.value)next[columnLetter(filterColumn)]=e.target.value;else delete next[columnLetter(filterColumn)];return next;})}><option value="">不声明</option><option value="number">数值</option><option value="date">ISO日期</option><option value="text">文本</option></select></label>
      {selected&&<pre>{String(selected.value??'')}</pre>}<p>Hash：{page?.version}。仅显示公式及原缓存，不执行宏或公式。所有排序/筛选只作用于检查视图。</p>
    </details>
  </div>;
}
