import {ipcMain,dialog,type BrowserWindow} from 'electron';
import {writeFile} from 'node:fs/promises';
import {privacySettings,savePrivacy,saveSensitiveFields} from '../lib/privacy-policy';
import type {ProviderDraft} from '../../../shared/types/model-provider';
import {modelProviders} from '../lib/model-provider/service';
import {ProviderError} from '../lib/model-provider/protocol';
import {reportError} from '../lib/error-utils';
import {log} from '../lib/logger';
import {saveEconomicCredential} from '../lib/economic-credentials';
import {recordSecurityEvent,securityDiagnostics} from '../lib/security-audit';

export function register(getMainWindow:()=>BrowserWindow|null):void {
 const handle=(name:string,run:(...args:unknown[])=>Promise<unknown>)=>ipcMain.handle(name,async(event,...args:unknown[])=>{try{if(event.sender!==getMainWindow()?.webContents||event.senderFrame!==event.sender.mainFrame)throw new ProviderError('INVALID');return await run(...args);}catch(error){const safe=error instanceof ProviderError?error:new ProviderError('STORAGE');reportError('MODEL_PROVIDER',new Error(safe.message));throw new Error(safe.message);}});
 // IPC input is validated by normalize/candidate; never log payloads, keys or provider response bodies.
 const draft=(value:unknown):ProviderDraft=>{if(typeof value!=='object'||!value||!('provider_type' in value)||!('provider_name' in value)||!('model_id' in value)||!('base_url' in value))throw new ProviderError('INVALID');
  if(typeof value.provider_name!=='string'||typeof value.model_id!=='string'||typeof value.base_url!=='string'||!['openai','anthropic','deepseek','custom'].includes(String(value.provider_type)))throw new ProviderError('INVALID');
  const type=value.provider_type;if(type!=='openai'&&type!=='anthropic'&&type!=='deepseek'&&type!=='custom')throw new ProviderError('INVALID');
  return {provider_name:value.provider_name,provider_type:type,model_id:value.model_id,base_url:value.base_url,...'api_key' in value&&typeof value.api_key==='string'?{api_key:value.api_key}:{},...'provider_id' in value&&typeof value.provider_id==='string'?{provider_id:value.provider_id}:{},...'verification_id' in value&&typeof value.verification_id==='string'?{verification_id:value.verification_id}:{},...'expected_updated_at' in value&&typeof value.expected_updated_at==='string'?{expected_updated_at:value.expected_updated_at}:{}};
 };
 handle('model-providers:list',async()=>{
  const {store}=modelProviders();
  try{if(await store.migrateLegacy()){recordSecurityEvent('credential','migration','completed');log('SECURITY:CREDENTIAL_MIGRATED',null);}}
  catch(error){recordSecurityEvent('credential','migration','failed');log('SECURITY:CREDENTIAL_MIGRATION_FAILED',null);throw error;}
  return store.list();
 });
 handle('model-providers:test',async(value,optional)=>modelProviders().probe.test(draft(value),optional===true));
 handle('model-providers:models',async value=>modelProviders().probe.models(draft(value)));
 handle('model-providers:save',async value=>{const d=draft(value),{store,probe}=modelProviders(),proof=probe.proof(d);return store.save(d,proof?.result??null,proof?.fingerprint??null);});
 handle('model-providers:default',async id=>{if(typeof id!=='string')throw new ProviderError('INVALID');await modelProviders().store.setDefault(id);});
 handle('model-providers:delete',async(id,confirmed)=>{if(typeof id!=='string')throw new ProviderError('INVALID');await modelProviders().store.remove(id,confirmed===true);});
 handle('model-providers:reveal',async id=>{
  const window=getMainWindow();
  if(typeof id!=='string'||!window||!window.isFocused())throw new ProviderError('INVALID');
  const {store}=modelProviders();
  const selected=store.get(id);
  const confirm=await dialog.showMessageBox(window,{type:'warning',title:'显示 API Key',message:'密钥将在系统对话框内显示。',buttons:['取消','显示密钥'],defaultId:0,cancelId:0,noLink:true});
  if(confirm.response!==1||window.isDestroyed()||getMainWindow()!==window)throw new ProviderError('CANCELLED');
  if(store.get(id).credential_reference!==selected.credential_reference)throw new ProviderError('STALE');
  const key=await store.key(selected);
  if(window.isDestroyed()||getMainWindow()!==window)throw new ProviderError('CANCELLED');
  await dialog.showMessageBox(window,{type:'info',title:'API Key',message:'已保存的 API Key',detail:key,buttons:['关闭'],noLink:true});
 });
 handle('model-providers:dismiss',async()=>modelProviders().store.dismiss());
 handle('data-sources:save-credential',saveEconomicCredential);
 handle('privacy:get',async()=>privacySettings());
 handle('privacy:set',async mode=>savePrivacy(mode));
 handle('privacy:set-fields',async fields=>saveSensitiveFields(fields));
 handle('privacy:export-diagnostics',async()=>{
  const window=getMainWindow();
  if(!window||!window.isFocused())throw new ProviderError('INVALID');
  const result=await dialog.showSaveDialog(window,{title:'导出本次运行的安全事件',defaultPath:'nomisma-security-events.json',filters:[{name:'JSON',extensions:['json']}]});
  if(result.canceled||!result.filePath||window.isDestroyed()||getMainWindow()!==window)return false;
  await writeFile(result.filePath,JSON.stringify(securityDiagnostics(),null,2)+'\n',{encoding:'utf8',flag:'wx',mode:0o600});
  return true;
 });
}
