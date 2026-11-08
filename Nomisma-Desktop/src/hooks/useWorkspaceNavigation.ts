import { useCallback, useEffect, useRef, useState } from 'react';
import { parseWorkspaceRoute, routeFallback, workspaceHash, type WorkspaceRoute } from '@/lib/workspace-routes';

export function useWorkspaceNavigation(canLeave:()=>boolean){
  const [route,setRoute]=useState(()=>parseWorkspaceRoute(window.location.hash));
  const index=useRef(Number(window.history.state?.nomismaIndex??0));
  const maximum=useRef(Number(sessionStorage.getItem('nomisma-history-max')??index.current));
  const current=useRef(route);const guard=useRef(canLeave);guard.current=canLeave;
  const reverting=useRef(false);const [,refresh]=useState(0);
  useEffect(()=>{
    window.history.replaceState({...window.history.state,nomismaIndex:index.current},'',window.location.hash||'#/overview');
    const pop=()=>{
      if(reverting.current){reverting.current=false;return;}
      const next=Number(window.history.state?.nomismaIndex??0);
      if(!guard.current()){
        const delta=index.current-next;
        if(delta){reverting.current=true;window.history.go(delta);}
        else window.history.replaceState({nomismaIndex:index.current},'',workspaceHash(current.current));
        return;
      }
      index.current=next;current.current=parseWorkspaceRoute(window.location.hash);setRoute(current.current);refresh(value=>value+1);
    };
    window.addEventListener('popstate',pop);
    return()=>window.removeEventListener('popstate',pop);
  },[]);
  const navigate=useCallback((next:WorkspaceRoute,replace=false,bypassGuard=false)=>{
    const hash=workspaceHash(next);if(hash===window.location.hash)return true;
    if(!bypassGuard&&!guard.current())return false;
    if(!replace){index.current++;maximum.current=index.current;sessionStorage.setItem('nomisma-history-max',String(maximum.current));}
    window.history[replace?'replaceState':'pushState']({nomismaIndex:index.current},'',hash);
    current.current=next;setRoute(next);refresh(value=>value+1);return true;
  },[]);
  const back=()=>{if(index.current>0)window.history.back();else navigate(routeFallback(current.current),true);};
  const forward=()=>window.history.forward();
  useEffect(()=>{
    const keys=(event:KeyboardEvent)=>{if(event.altKey&&(event.key==='ArrowLeft'||event.key==='ArrowRight')){event.preventDefault();if(event.key==='ArrowLeft')back();else forward();}};
    window.addEventListener('keydown',keys);return()=>window.removeEventListener('keydown',keys);
  });
  return {route,navigate,back,forward,canForward:index.current<maximum.current};
}
