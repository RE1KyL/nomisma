import type {AxisConfig,ChartConfig,ChartData} from '../types/infrastructure';
export const xml=(value:unknown)=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]!));
export function chartNumber(value:number,axis:AxisConfig):string {
  if(axis.format==='date'){const d=new Date(value);return Number.isNaN(d.getTime())?'':d.toISOString().slice(0,10);}
  const v=axis.format==='percent'?value*100:value;
  return (axis.format==='currency'?'¥':'')+v.toLocaleString('en-US',{maximumFractionDigits:axis.precision,minimumFractionDigits:axis.precision})+(axis.format==='percent'?'%':'');
}
const numeric=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
export function chartSvg(data:ChartData,c:ChartConfig):string {
  if(c.width<480||c.width>2400||c.height<320||c.height>1600||c.font_size<10||c.font_size>24||!Number.isInteger(c.bins)||c.bins<2||c.bins>100)throw new Error('图表尺寸、字号或分箱数超出范围');
  const w=c.width,h=c.height,f=c.font_size,pad={left:85,right:c.legend==='right'?180:40,top:90,bottom:100};
  const pw=w-pad.left-pad.right,ph=h-pad.top-pad.bottom,series=c.series.filter(s=>s.visible),rows=data.rows;
  if(!series.length)return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><text x="30" y="40">请选择至少一个数据系列</text></svg>`;
  const text=(x:number,y:number,t:unknown,extra='')=>`<text x="${x}" y="${y}" ${extra}>${xml(t)}</text>`;
  const line=(x1:number,y1:number,x2:number,y2:number,extra='')=>`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" ${extra}/>`;
  const tip=(label:string)=>`<title>${xml(label)}</title>`;
  const dateX=data.semantic==='time_series'||c.x_axis.format==='date';
  const xv=rows.map((r,i)=>numeric(r[c.x_column])?Number(r[c.x_column]):dateX&&typeof r[c.x_column]==='string'&&/^\d{4}-\d{2}-\d{2}/.test(String(r[c.x_column]))?Date.parse(String(r[c.x_column])):i);
  const vals=series.flatMap(s=>rows.map(r=>r[s.column]).filter(numeric));
  if(!vals.length)throw new Error('所选系列没有可绘制的真实数值');
  if(c.chart_type==='coefficient')for(const r of rows)for(const k of ['ci_low','ci_high'])if(numeric(r[k]))vals.push(Number(r[k]));
  let hist:{lo:number;hi:number;counts:number[];series:typeof series[number]}[]=[];
  if(c.chart_type==='histogram')hist=series.map(s=>{
    const a=rows.map(r=>r[s.column]).filter(numeric),lo=Math.min(...a),hi=Math.max(...a);const span=hi===lo?1:hi-lo,counts=Array<number>(c.bins).fill(0);
    for(const v of a)counts[Math.min(c.bins-1,Math.floor((v-lo)/span*c.bins))]++;
    return {lo,hi:lo+span,counts,series:s};
  });
  let xmin=Math.min(...xv),xmax=Math.max(...xv),ymin=Math.min(...vals),ymax=Math.max(...vals);
  if(c.chart_type==='bar'){ymin=Math.min(0,ymin);ymax=Math.max(0,ymax);}
  if(hist.length){xmin=Math.min(...hist.map(t=>t.lo));xmax=Math.max(...hist.map(t=>t.hi));ymin=0;ymax=Math.max(...hist.flatMap(t=>t.counts));}
  if(c.chart_type==='box'){xmin=-.5;xmax=series.length-.5;}
  if(xmin===xmax){xmin-=.5;xmax+=.5;}if(ymin===ymax){const d=Math.abs(ymin)*.05||1;ymin-=d;ymax+=d;}
  if(c.x_axis.min!==null)xmin=c.x_axis.min;if(c.x_axis.max!==null)xmax=c.x_axis.max;
  if(c.y_axis.min!==null)ymin=c.y_axis.min;if(c.y_axis.max!==null)ymax=c.y_axis.max;
  if(!(xmin<xmax&&ymin<ymax))throw new Error('坐标范围必须递增');
  if((c.y_axis.scale==='log'&&(ymin<=0||vals.some(v=>v<=0)))||(c.x_axis.scale==='log'&&(xmin<=0||xv.some(v=>v<=0))))throw new Error('对数坐标仅适用于正数');
  const transform=(v:number,a:AxisConfig)=>a.scale==='log'?Math.log(v):v;
  const sx=(v:number)=>pad.left+(transform(v,c.x_axis)-transform(xmin,c.x_axis))/(transform(xmax,c.x_axis)-transform(xmin,c.x_axis))*pw;
  const sy=(v:number)=>pad.top+ph-(transform(v,c.y_axis)-transform(ymin,c.y_axis))/(transform(ymax,c.y_axis)-transform(ymin,c.y_axis))*ph;
  const out=[`<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="${xml(c.title)}" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="background:white;font-family:system-ui,sans-serif;font-size:${f}px;color:#172b45">`,
    '<rect width="100%" height="100%" fill="white"/>',text(pad.left,32,c.title,'font-size="20" font-weight="600"'),text(pad.left,56,c.subtitle,'fill="#52657c"'),
    `<defs><clipPath id="plot"><rect x="${pad.left}" y="${pad.top}" width="${pw}" height="${ph}"/></clipPath></defs>`];
  if(c.chart_type==='heatmap'){
    const cw=pw/series.length,ch=ph/rows.length;
    rows.forEach((r,i)=>{
      out.push(text(pad.left-8,pad.top+(i+.6)*ch,r[c.x_column],'text-anchor="end"'));
      series.forEach((s,j)=>{const v=r[s.column];const intensity=numeric(v)?Math.min(1,Math.abs(v)/Math.max(Math.abs(ymin),Math.abs(ymax),1e-12)):0;
        const color=numeric(v)?v>=0?`rgb(${240-180*intensity},${247-120*intensity},${255-25*intensity})`:`rgb(255,${240-150*intensity},${220-150*intensity})`:'#eee';
        out.push(`<rect x="${pad.left+j*cw}" y="${pad.top+i*ch}" width="${cw}" height="${ch}" fill="${color}" stroke="white">${tip(String(r[c.x_column])+' × '+s.label+': '+String(v))}</rect>`);
        if(numeric(v)&&ch>18&&cw>40)out.push(text(pad.left+(j+.5)*cw,pad.top+(i+.6)*ch,chartNumber(v,c.y_axis),'text-anchor="middle"'));
      });
    });
    series.forEach((s,j)=>out.push(text(pad.left+(j+.5)*cw,pad.top+ph+22,s.label,'text-anchor="middle"')));
  }else{
    for(let i=0;i<c.y_axis.ticks;i++){
      const t=i/(c.y_axis.ticks-1);const v=c.y_axis.scale==='log'?Math.exp(Math.log(ymin)+t*(Math.log(ymax)-Math.log(ymin))):ymin+t*(ymax-ymin);const y=sy(v);
      if(c.grid)out.push(line(pad.left,y,pad.left+pw,y,'stroke="#d4dce7"'));
      out.push(text(pad.left-10,y+4,chartNumber(v,c.y_axis),'text-anchor="end" fill="#52657c"'));
    }
    const categories=!(dateX||rows.every(r=>numeric(r[c.x_column])))&&c.chart_type!=='histogram'&&c.chart_type!=='box';
    for(let i=0;i<c.x_axis.ticks;i++){
      const t=i/(c.x_axis.ticks-1),v=c.x_axis.scale==='log'?Math.exp(Math.log(xmin)+t*(Math.log(xmax)-Math.log(xmin))):xmin+t*(xmax-xmin);
      const label=c.chart_type==='box'?series[Math.max(0,Math.min(series.length-1,Math.round(v)))]?.label:categories?rows[Math.max(0,Math.min(rows.length-1,Math.round(v)))]?.[c.x_column]:dateX?chartNumber(v,{...c.x_axis,format:'date'}):chartNumber(v,c.x_axis);
      out.push(text(sx(v),pad.top+ph+24,label,'text-anchor="middle" fill="#52657c"'));
    }
    out.push(`<g clip-path="url(#plot)">`);
    if(hist.length){for(const a of hist)for(let i=0;i<a.counts.length;i++){
      const lo=a.lo+i*(a.hi-a.lo)/c.bins,hi=a.lo+(i+1)*(a.hi-a.lo)/c.bins;
      out.push(`<rect x="${sx(lo)}" y="${sy(a.counts[i])}" width="${Math.max(1,sx(hi)-sx(lo)-1)}" height="${Math.max(0,sy(0)-sy(a.counts[i]))}" fill="${a.series.color}" fill-opacity=".55">${tip(a.series.label+`: [${lo}, ${hi}${i===c.bins-1?']':')'} count=${a.counts[i]}`)}</rect>`);
    }}else if(c.chart_type==='box'){
      series.forEach((s,i)=>{const a=rows.map(r=>r[s.column]).filter(numeric).sort((a,b)=>a-b);if(!a.length)return;
        const q=(p:number)=>{const k=(a.length-1)*p,l=Math.floor(k);return a[l]+(a[Math.min(l+1,a.length-1)]-a[l])*(k-l);};
        const x=sx(i),bw=Math.min(70,pw/series.length*.6),low=q(0),high=q(1),q1=q(.25),median=q(.5),q3=q(.75);
        out.push(`<g>${tip(`${s.label}: min=${low}, Q1=${q1}, median=${median}, Q3=${q3}, max=${high}; min/max whiskers`)}`,
          line(x,sy(low),x,sy(high),`stroke="${s.color}"`),`<rect x="${x-bw/2}" y="${sy(q3)}" width="${bw}" height="${Math.max(1,sy(q1)-sy(q3))}" fill="${s.color}" fill-opacity=".2" stroke="${s.color}"/>`,line(x-bw/2,sy(median),x+bw/2,sy(median),`stroke="${s.color}" stroke-width="2"`),'</g>');
      });
    }else for(const [seriesIndex,s] of series.entries()){
      let path='';let connected=false;
      rows.forEach((r,i)=>{const v=r[s.column];if(!numeric(v)){connected=false;return;}const x=sx(xv[i]),y=sy(v),label=`${s.label} · ${String(r[c.x_column])}: ${v}`;
        if(c.chart_type==='line'){path+=(connected?'L':'M')+x+','+y+' ';connected=true;}
        if(c.chart_type==='bar'){const groupWidth=pw/Math.max(2,rows.length)*.7,bw=Math.max(1,groupWidth/series.length);out.push(`<rect x="${x-groupWidth/2+seriesIndex*bw}" y="${Math.min(y,sy(0))}" width="${bw}" height="${Math.abs(y-sy(0))}" fill="${s.color}" fill-opacity=".65">${tip(label)}</rect>`);}
        else{
          if(c.chart_type==='coefficient'&&numeric(r.ci_low)&&numeric(r.ci_high))out.push(line(x,sy(Number(r.ci_low)),x,sy(Number(r.ci_high)),`stroke="${s.color}" stroke-width="${s.width}"`));
          out.push(`<circle cx="${x}" cy="${y}" r="${Math.max(2,s.marker)}" fill="${s.color}" fill-opacity=".85">${tip(label+(c.chart_type==='coefficient'?` CI=[${r.ci_low}, ${r.ci_high}]`:''))}</circle>`);
        }
      });
      if(path)out.push(`<path d="${path}" fill="none" stroke="${s.color}" stroke-width="${s.width}"/>`);
    }
    out.push('</g>',line(pad.left,pad.top+ph,pad.left+pw,pad.top+ph,'stroke="#64748b"'),line(pad.left,pad.top,pad.left,pad.top+ph,'stroke="#64748b"'));
  }
  out.push(text(pad.left+pw/2,h-48,c.x_axis.title+' · '+c.x_axis.unit,'text-anchor="middle"'),text(18,pad.top-18,c.y_axis.title+' · '+c.y_axis.unit));
  if(c.legend!=='none')series.forEach((s,i)=>{const x=c.legend==='right'?w-160:pad.left+i*150,y=c.legend==='top'?76:c.legend==='right'?110+i*26:h-18;
    out.push(line(x,y-4,x+16,y-4,`stroke="${s.color}" stroke-width="3"`),text(x+22,y,s.label));});
  out.push('</svg>');return out.join('');
}
