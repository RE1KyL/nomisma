import {useEffect,useRef,useState,type ReactNode} from 'react';
import {ProviderEditor} from './settings/ModelProviders';
import {reportError} from '@/lib/analytics';

export function ModelProviderSetup({children}:{children:ReactNode}){
 const [open,setOpen]=useState(false),[needed,setNeeded]=useState(false),[error,setError]=useState('');const dialog=useRef<HTMLElement>(null),prior=useRef<HTMLElement|null>(null);
 const refresh=()=>window.modelProviders?.list().then(s=>{setNeeded(!s.default_provider_id);if(!s.providers.length&&!s.setup_dismissed)setOpen(true);}).catch(()=>{setError('无法读取模型配置。项目和本地功能仍可使用，请重新启动后重试。');reportError('MODEL_SETUP',new Error('Model configuration unavailable'));});
 useEffect(()=>{void refresh();const required=()=>{setOpen(true);setNeeded(true);};const changed=()=>void refresh();window.addEventListener('nomisma:providers-changed',changed);const remove=window.modelProviders?.onSetupRequired(required);return()=>{window.removeEventListener('nomisma:providers-changed',changed);remove?.();};},[]);
 useEffect(()=>{if(open){prior.current=document.activeElement instanceof HTMLElement?document.activeElement:null;dialog.current?.querySelector<HTMLButtonElement>('button')?.focus();}else prior.current?.focus();},[open]);
 const dismiss=()=>{void window.modelProviders.dismissSetup().then(()=>setOpen(false)).catch(()=>{setError('未能记住跳过状态；您仍可浏览工作台。');setOpen(false);});};
 return <><div inert={open}>{children}</div>{!open&&needed&&<aside className="mp-setup-notice"><span>尚未配置研究模型</span><button onClick={()=>setOpen(true)}>配置模型服务</button></aside>}{error&&<div className="mp-setup-notice" role="alert">{error}</div>}{open&&<div className="mp-modal"><section ref={dialog} className="mp-setup-dialog" role="dialog" aria-modal="true" aria-label="Nomisma 首次模型配置" onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();dismiss();}if(e.key==='Tab'){const list=Array.from(e.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled)'));if(e.shiftKey&&document.activeElement===list[0]){e.preventDefault();list[list.length-1]?.focus();}else if(!e.shiftKey&&document.activeElement===list[list.length-1]){e.preventDefault();list[0]?.focus();}}}}><ProviderEditor existing={null} wizard onDone={()=>{setOpen(false);void refresh();}} onCancel={dismiss}/></section></div>}</>;
}
