import {describe,expect,it} from 'vitest';
import {chartSvg,chartNumber} from '@shared/lib/research-chart';
import type {ChartConfig,ChartData} from '@shared/types/infrastructure';
const axis={title:'value',unit:'source',min:null,max:null,scale:'linear' as const,format:'number' as const,precision:2,ticks:5};
const config=():ChartConfig=>({chart_type:'line',title:'actual fixture',subtitle:'',x_column:'x',x_axis:{...axis},y_axis:{...axis},legend:'bottom',grid:true,width:1000,height:560,font_size:13,bins:4,series:[{column:'y',label:'series',visible:true,color:'#2563eb',width:2,marker:3}]});
const data:ChartData={columns:['x','y'],numeric_columns:['x','y'],rows:[{x:0,y:1},{x:1,y:2},{x:2,y:null},{x:3,y:4}],semantic:'table',warnings:[]};
describe('one deterministic renderer for preview and export',()=>{
  it('draws only actual points and preserves missing gaps',()=>{const svg=chartSvg(data,config());expect((svg.match(/<circle/g)??[]).length).toBe(3);expect(svg).toContain('series · 1: 2');expect(svg).not.toContain('series · 2:');expect((svg.match(/d="M[^\"]*M/g)??[]).length).toBe(1);});
  it('escapes source labels and titles without active SVG content',()=>{const c=config();c.title='<script>alert(1)</script>';c.series[0].label='" onload="bad';const svg=chartSvg(data,c);expect(svg).not.toContain('<script>');expect(svg).toContain('&lt;script&gt;');expect(svg).not.toContain(' onload="bad');});
  it('reports explicit percent scaling and rejects invalid log/range',()=>{expect(chartNumber(.125,{...axis,format:'percent'})).toBe('12.50%');const c=config();c.y_axis.min=10;c.y_axis.max=1;expect(()=>chartSvg(data,c)).toThrow('范围');c.y_axis.min=null;c.y_axis.max=null;c.x_axis.scale='log';expect(()=>chartSvg(data,c)).toThrow('对数');});
  it('uses real counts for histogram and min/max whiskers for box plots',()=>{const c=config();c.chart_type='histogram';const svg=chartSvg(data,c);expect(svg).toContain('count=1');expect((svg.match(/<rect x=/g)??[]).length).toBeGreaterThanOrEqual(4);c.chart_type='box';expect(chartSvg(data,c)).toContain('min=1, Q1=1.5, median=2, Q3=3, max=4; min/max whiskers');});
  it('renders coefficient intervals from machine endpoints',()=>{const c=config();c.chart_type='coefficient';c.x_column='term';c.series[0].column='estimate';const d={...data,rows:[{term:'x',estimate:.9,ci_low:.1,ci_high:1.7}],columns:['term','estimate','ci_low','ci_high'],numeric_columns:['estimate','ci_low','ci_high'],semantic:'coefficient'};expect(chartSvg(d,c)).toContain('CI=[0.1, 1.7]');});
  it('hidden series cannot leak into the exported geometry',()=>{const c=config();c.series[0].visible=false;expect(chartSvg(data,c)).not.toContain('<circle');expect(JSON.stringify(data)).toContain('"y":4');});
});
