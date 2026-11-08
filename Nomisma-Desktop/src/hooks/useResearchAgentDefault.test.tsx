import { renderToStaticMarkup } from 'react-dom/server';
import type { EffectCallback } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { InstalledAgent } from '@/types';
const effects=vi.hoisted(()=>new Array<EffectCallback>());
vi.mock('react',async original=>({...await original<typeof import('react')>(),useEffect:(effect:EffectCallback)=>effects.push(effect)}));
import { useResearchAgentDefault } from './useResearchAgentDefault';
const agent:InstalledAgent={id:'nomisma-dsh-acp',engine:'acp',name:'Nomisma DSH ACP'};
beforeEach(()=>{effects.length=0;});
function mount(agents:InstalledAgent[], selected:InstalledAgent|null,live:boolean,select:(agent:InstalledAgent|null)=>void){
  function Probe(){useResearchAgentDefault(agents,selected,live,select);return null;}
  renderToStaticMarkup(<Probe/>);effects.splice(0).forEach(effect=>effect());
}
it('selects the sole Nomisma agent after async registry loading',()=>{
  const select=vi.fn();mount([],null,false,select);expect(select).not.toHaveBeenCalled();
  mount([agent],null,false,select);expect(select).toHaveBeenCalledWith(agent);
  mount([agent],agent,false,select);expect(select).toHaveBeenCalledTimes(1);
});
it('does not replace restored conversations or choose built-in Claude',()=>{
  const select=vi.fn();mount([agent],null,true,select);
  mount([{id:'claude-code',name:'Claude Code',engine:'claude'}],null,false,select);
  expect(select).not.toHaveBeenCalled();
});
