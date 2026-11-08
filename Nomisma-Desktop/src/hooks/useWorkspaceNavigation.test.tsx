import { renderToStaticMarkup } from 'react-dom/server';
import type { EffectCallback } from 'react';
import { beforeEach,expect,it,vi } from 'vitest';
const effects=vi.hoisted(()=>new Array<EffectCallback>());
vi.mock('react',async original=>({...await original<typeof import('react')>(),useEffect:(effect:EffectCallback)=>effects.push(effect)}));
import { useWorkspaceNavigation } from './useWorkspaceNavigation';
function browser(initial=''){
  const listeners=new Set<()=>void>();const location={hash:initial};const entries=[{hash:initial,state:{nomismaIndex:0}}];let position=0;
  const history={state:entries[0].state,
    replaceState:(state:{nomismaIndex:number},_title:string,hash:string)=>{entries[position]={state,hash};history.state=state;location.hash=hash;},
    pushState:(state:{nomismaIndex:number},_title:string,hash:string)=>{entries.splice(position+1);entries.push({state,hash});position++;history.state=state;location.hash=hash;},
    go:(delta:number)=>{const next=position+delta;if(next<0||next>=entries.length)return;position=next;history.state=entries[position].state;location.hash=entries[position].hash;[...listeners].forEach(listener=>listener());},
    back:()=>history.go(-1),forward:()=>history.go(1),};
  vi.stubGlobal('window',{location,history,addEventListener:(name:string,callback:()=>void)=>{if(name==='popstate')listeners.add(callback);},removeEventListener:()=>{}});
  const storage=new Map<string,string>();vi.stubGlobal('sessionStorage',{getItem:(key:string)=>storage.get(key)??null,setItem:(key:string,value:string)=>storage.set(key,value)});
  return {location,history,entries};
}
function mount(guard=()=>true){let navigation!:ReturnType<typeof useWorkspaceNavigation>;function Probe(){navigation=useWorkspaceNavigation(guard);return null;}renderToStaticMarkup(<Probe/>);effects.splice(0).forEach(effect=>effect());return navigation;}
beforeEach(()=>{effects.length=0;});
it('uses actual push/pop history for back/forward and restores hash context on refresh',()=>{
  const {location}=browser();const nav=mount();expect(nav.route).toEqual({page:'overview'});
  nav.navigate({page:'projects',mode:'economic'});nav.navigate({page:'project',mode:'economic',projectId:'project_a',sessionId:'session_a'});
  nav.back();expect(location.hash).toBe('#/economic/projects');nav.forward();expect(location.hash).toBe('#/economic/projects/project_a/sessions/session_a');
  const refreshed=mount();expect(refreshed.route).toEqual({page:'project',mode:'economic',projectId:'project_a',sessionId:'session_a'});
});
it('blocks both direct navigation and browser back when unsaved work refuses leaving',()=>{
  const {location,history}=browser();let allow=true;const nav=mount(()=>allow);
  nav.navigate({page:'project',mode:'finance',projectId:'project_b'});allow=false;
  expect(nav.navigate({page:'overview'})).toBe(false);history.back();expect(location.hash).toBe('#/finance/projects/project_b');
  allow=true;history.back();expect(location.hash).toBe('#/overview');
});
it('replaces a no-history fallback without creating a back-loop to the same project',()=>{
  const {location,entries}=browser('#/finance/projects/project_b');const nav=mount();nav.back();expect(location.hash).toBe('#/finance/projects');expect(entries).toHaveLength(1);
});
