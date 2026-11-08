import {app,BrowserWindow,dialog,safeStorage} from 'electron';
import path from 'node:path';
import {ProviderStore,type Cipher} from './store';
import {ProviderProbe} from './probe';
import {ProviderRuntime} from './runtime';
import {ProviderError} from './protocol';
import {dpapiAvailable,dpapiPrefix,windowsDpapi} from '../windows-dpapi';
import type {ContextReviewer} from './cloud-context';

export const cipher:Cipher={
 async available(){return process.platform==='win32'?dpapiAvailable():app.isReady()&&safeStorage.isEncryptionAvailable()&&(process.platform!=='linux'||safeStorage.getSelectedStorageBackend()!=='basic_text');},
 async encrypt(value){if(!await this.available())throw new ProviderError('STORAGE');if(process.platform==='win32')return Buffer.concat([dpapiPrefix,await windowsDpapi('encrypt',Buffer.from(value,'utf8'))]);return safeStorage.encryptString(value);},
 async decrypt(value){if(value.subarray(0,dpapiPrefix.length).equals(dpapiPrefix)){const decoded=await windowsDpapi('decrypt',Buffer.from(value.subarray(dpapiPrefix.length)));try{return decoded.toString('utf8');}finally{decoded.fill(0);}}if(!app.isReady()||!safeStorage.isEncryptionAvailable())throw new ProviderError('STORAGE');return safeStorage.decryptString(value);},
};
let service:{store:ProviderStore;probe:ProviderProbe;runtime:ProviderRuntime}|null=null;
export const reviewCloudContext:ContextReviewer=async(config,items,signal)=>{
 const window=BrowserWindow.getFocusedWindow();
 if(!window||window.isDestroyed()||signal.aborted)return false;
 // Show the actual new context in bounded pages. Nothing is sent unless every page is approved.
 const preview=items.map(({source,text})=>`【${source}】\n${text.replace(/[\u0000-\u0008\u000b-\u001f\u202a-\u202e\u2066-\u2069]/g,value=>JSON.stringify(value).slice(1,-1))}`).join('\n\n');
 const pages=preview.match(/[\s\S]{1,1800}/gu)??[];
 for(const [index,detail] of pages.entries()){
  if(signal.aborted||window.isDestroyed())return false;
  const result=await dialog.showMessageBox(window,{
   type:'warning',title:'审查云端上下文',
   message:`${config.provider_name} · ${new URL(config.base_url).origin}\n模型 ${config.model_id} · 第 ${index+1}/${pages.length} 部分`,
   detail:`研究推理将使用以下额外内容，其中可能含敏感字段。原始文件与原始数据行保持本地。\n\n${detail}`,
   buttons:['取消本次请求',index===pages.length-1?'批准并发送':'批准此部分'],defaultId:0,cancelId:0,noLink:true,signal,
  });
  if(result.response!==1)return false;
 }
 return pages.length>0&&!signal.aborted;
};
export function modelProviders(){if(!service){const root=process.env.NOMISMA_STATE_ROOT??app.getPath('userData');let runtime:ProviderRuntime|undefined;const store=new ProviderStore(path.join(root,'model-providers'),cipher,id=>runtime?.busy(id)??false);runtime=new ProviderRuntime(store,reviewCloudContext);service={store,probe:new ProviderProbe(store),runtime};app.once('before-quit',()=>runtime?.close());}return service;}
