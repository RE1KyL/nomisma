import { useRef, useState, type RefObject } from 'react';
import { boundedPanelWidth, PANEL_LIMITS, type PanelSide } from '@/lib/workspace-panels';

export function WorkspaceDivider({side,width,otherWidth,container,onResize}:{
  side:PanelSide; width:number; otherWidth:number; container:RefObject<HTMLDivElement|null>; onResize:(value:number)=>void;
}) {
  const drag = useRef<{id:number;x:number;width:number}|null>(null);
  const [dragging,setDragging] = useState(false);
  const apply = (next:number) => onResize(boundedPanelWidth(side,next,container.current?.clientWidth ?? 1200,otherWidth));
  return <div className={`n-divider${dragging?' dragging':''}`} role="separator" tabIndex={0}
    aria-label={side==='left'?'调整左侧导航宽度':'调整右侧文件栏宽度'} aria-orientation="vertical"
    aria-valuenow={width} aria-valuemin={PANEL_LIMITS[side].min}
    aria-valuemax={boundedPanelWidth(side,PANEL_LIMITS[side].max,container.current?.clientWidth ?? 1200,otherWidth)}
    title="拖动调整宽度；也可用左右方向键"
    onPointerDown={event=>{
      if(event.button!==0)return;
      event.preventDefault();
      drag.current={id:event.pointerId,x:event.clientX,width};
      event.currentTarget.setPointerCapture(event.pointerId);setDragging(true);
    }}
    onPointerMove={event=>{
      const start=drag.current;if(!start||start.id!==event.pointerId)return;
      apply(start.width+(event.clientX-start.x)*(side==='left'?1:-1));
    }}
    onPointerUp={event=>{
      if(drag.current?.id!==event.pointerId)return;
      drag.current=null;setDragging(false);
      if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);
    }}
    onPointerCancel={()=>{drag.current=null;setDragging(false);}}
    onDoubleClick={()=>apply(PANEL_LIMITS[side].initial)}
    onKeyDown={event=>{
      if(event.key==='ArrowLeft'||event.key==='ArrowRight'){
        event.preventDefault();apply(width+(event.key==='ArrowRight'?16:-16)*(side==='left'?1:-1));
      } else if(event.key==='Home'||event.key==='End'){
        event.preventDefault();apply(event.key==='Home'?PANEL_LIMITS[side].min:PANEL_LIMITS[side].max);
      }
    }}/>
}
