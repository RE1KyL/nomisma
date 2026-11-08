import { renderToStaticMarkup } from 'react-dom/server';
import type { ComponentProps, HTMLAttributes, PointerEvent, KeyboardEvent, ReactElement } from 'react';
import { expect, it, vi } from 'vitest';
import { WorkspaceDivider } from './WorkspaceDivider';
import { boundedPanelWidth, storedPanelWidths } from '@/lib/workspace-panels';

function mount(side:'left'|'right'){
  const resize=vi.fn();let element:ReactElement<HTMLAttributes<HTMLDivElement>>;
  const capture=vi.fn();const release=vi.fn();
  const target={setPointerCapture:capture,hasPointerCapture:()=>true,releasePointerCapture:release};
  const options:ComponentProps<typeof WorkspaceDivider>={side,width:side==='left'?150:280,otherWidth:side==='left'?280:150,container:{current:{clientWidth:1200} as HTMLDivElement},onResize:resize};
  function Probe(){element=WorkspaceDivider(options);return element;}
  const html=renderToStaticMarkup(<Probe/>);
  const pointer=(x:number)=>({button:0,pointerId:7,clientX:x,currentTarget:target,preventDefault:vi.fn()} as unknown as PointerEvent<HTMLDivElement>);
  return {props:element!.props,resize,capture,release,pointer,html};
}
it('drags left and right boundaries, releases capture and stops resizing',()=>{
  for(const side of ['left','right'] as const){
    const {props,resize,capture,release,pointer}=mount(side);
    props.onPointerDown!(pointer(300));props.onPointerMove!(pointer(340));
    expect(resize).toHaveBeenLastCalledWith(side==='left'?190:240);
    props.onPointerUp!(pointer(340));props.onPointerMove!(pointer(500));
    expect(resize).toHaveBeenCalledTimes(1);expect(capture).toHaveBeenCalledWith(7);expect(release).toHaveBeenCalledWith(7);
  }
});
it('provides a named keyboard separator and preserves room for the center',()=>{
  const {props,resize,html}=mount('right');
  expect(html).toContain('role="separator"');expect(html).toContain('调整右侧文件栏宽度');
  props.onKeyDown!({key:'ArrowRight',preventDefault:vi.fn()} as unknown as KeyboardEvent<HTMLDivElement>);
  expect(resize).toHaveBeenLastCalledWith(264);
  expect(boundedPanelWidth('right',2000,1000,200)).toBe(386);
  expect(storedPanelWidths('{"left":190,"right":310}')).toEqual({left:190,right:310});
  expect(storedPanelWidths('invalid')).toEqual({left:150,right:280});
});
