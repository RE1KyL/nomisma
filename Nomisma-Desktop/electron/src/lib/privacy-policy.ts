import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { isPathRedirected } from './windows-runtime.cjs';
import type {PrivacySettings} from '../../../shared/types/privacy';

function checkedFields(value:unknown):string[] {
 if(!Array.isArray(value)||value.length>24||value.some(item=>typeof item!=='string'||!/^[\p{L}\p{N}_.-]{1,40}$/u.test(item)))throw new Error('Invalid sensitive fields');
 return [...new Set(value.map((item:string)=>item.toLowerCase()))];
}

/** No file means strict defaults; unreadable persisted policy fails closed. */
export function privacySettings():PrivacySettings {
 const file=process.env.NOMISMA_PRIVACY_SETTINGS;
 if(!file)return {version:1,mode:'strict'};
 try{const stat=fs.lstatSync(file);if(!stat.isFile()||isPathRedirected(file,stat)||stat.size>4096)throw Error();const d:unknown=JSON.parse(fs.readFileSync(file,'utf8'));
  if(typeof d==='object'&&d&&'version' in d&&d.version===1&&'mode' in d&&(d.mode==='standard'||d.mode==='strict'||d.mode==='local_only'))return {version:1,mode:d.mode,...'sensitive_fields' in d?{sensitive_fields:checkedFields(d.sensitive_fields)}:{}};
 }catch(error){
  if(error instanceof Error&&'code' in error&&error.code==='ENOENT')return {version:1,mode:'strict'};
 }
 return {version:1,mode:'local_only'};
}
export function savePrivacy(mode:unknown):PrivacySettings {
 if(mode!=='standard'&&mode!=='strict'&&mode!=='local_only')throw new Error('Invalid privacy mode');
 return writePrivacy({...privacySettings(),mode});
}
export function saveSensitiveFields(fields:unknown):PrivacySettings {
 return writePrivacy({...privacySettings(),sensitive_fields:checkedFields(fields)});
}
function writePrivacy(policy:PrivacySettings):PrivacySettings {
 const file=process.env.NOMISMA_PRIVACY_SETTINGS;if(!file)throw new Error('Privacy settings unavailable');
 const directory=path.dirname(file);fs.mkdirSync(directory,{recursive:true});if(isPathRedirected(directory)||(fs.existsSync(file)&&isPathRedirected(file)))throw new Error('Unsafe privacy settings path');
 const temp=path.join(directory,'privacy_'+crypto.randomUUID()+'.tmp');
 try{fs.writeFileSync(temp,JSON.stringify(policy),{flag:'wx',mode:0o600});fs.renameSync(temp,file);}finally{if(fs.existsSync(temp))fs.unlinkSync(temp);}
 return policy;
}
export function isLoopback(url:URL):boolean{return ['127.0.0.1','[::1]','localhost'].includes(url.hostname);}
export function cloudAllowed(base:string):boolean {return privacySettings().mode!=='local_only'||isLoopback(new URL(base));}
const researchVariables=new Set([
 'PATH','SYSTEMROOT','WINDIR','SYSTEMDRIVE','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA',
 'PROCESSOR_ARCHITECTURE','PROCESSOR_ARCHITEW6432',
 'HOME','HOMEDRIVE','HOMEPATH','LANG','LC_ALL','PYTHONUTF8','OPENBLAS_NUM_THREADS','OMP_NUM_THREADS',
 'DSH_HOME','DSH_AGENTS_HOME','DSH_TELEMETRY_DISABLED','NOMISMA_DESKTOP_MODE','NOMISMA_STATE_ROOT',
 'NOMISMA_PROJECT_ROOT','NOMISMA_PRIVACY_SETTINGS','NOMISMA_WORKSPACE','NOMISMA_MODE',
 'NOMISMA_PROJECT_ID','NOMISMA_SESSION_ID','NOMISMA_LOG_DIR','NOMISMA_FILE_TEST',
]);
/** Managed workers inherit only the OS and project context they require. */
export function researchEnvironment(env:NodeJS.ProcessEnv,managed=true):NodeJS.ProcessEnv {
 return Object.fromEntries(Object.entries(env).filter(([key])=>managed?researchVariables.has(key.toUpperCase()):!/(API_?KEY|TOKEN|SECRET|PASSWORD|AUTHORIZATION|PROXY|^NODE_OPTIONS$)/i.test(key)));
}

const privateKeys=/^(sample|preview|rows?|records?|raw|content|document|response_document|history|processing_history|source_path|path|output_file|original|tables?|residuals|values|series|observations|credentials?|api_key|token|authorization)$/i;
const textKeys=/^(method|analysis_domain|status|error_code|code|dataset_id|analysis_id|run_id|execution_id|request_id|sha256|input_sha256|output_sha256|dtype|type|name|column|column_name|variable|term|frequency|unit|mode)$/;
/** Deny raw records and free text; share bounded calculated scalars/schema only. */
export function summaryForModel(value:unknown,depth=0):unknown {
 if(depth>7)return undefined;
 if(value===null||typeof value==='boolean'||typeof value==='number')return value;
 if(Array.isArray(value))return value.slice(0,24).map(v=>typeof v==='object'&&v!==null?summaryForModel(v,depth+1):undefined).filter(v=>v!==undefined);
 if(typeof value!=='object'||!value)return undefined;
 const result:Record<string,unknown>={};let count=0;
 for(const [key,item] of Object.entries(value)){
  if(++count>100)break;if(privateKeys.test(key)||!/^\w[\w.-]{0,63}$/.test(key)||key==='__proto__')continue;
  if(typeof item==='string'){if(textKeys.test(key)&&/^[\p{L}\p{N}_.:+-]{1,120}$/u.test(item))result[key]=item;continue;}
  const safe=summaryForModel(item,depth+1);if(safe!==undefined)result[key]=safe;
 }
 return result;
}
export function toolSummary(text:string,budget=16000):string {
 let data:unknown;try{data=JSON.parse(text);}catch{return JSON.stringify({local_result:true,details_withheld:true});}
 const summary=summaryForModel(data);const encoded=JSON.stringify(typeof summary==='object'&&summary!==null?{...summary,details_withheld:true,privacy_notice:'Raw records and free text withheld; check local results for full diagnostics and limitations.'}:summary);
 return encoded.length<=budget?encoded:JSON.stringify({local_result:true,details_withheld:true,reason:'summary_budget'});
}
/** Research coordination can replay receipts outside the tool-message channel. */
export function coordinationForModel(value:unknown,depth=0):unknown {
 if(depth>16)return undefined;
 if(Array.isArray(value))return value.slice(0,30).map(v=>coordinationForModel(v,depth+1));
 if(typeof value!=='object'||value===null)return value;
 return Object.fromEntries(Object.entries(value).filter(([key])=>!['history','sample','preview','response_document'].includes(key)).map(([key,item])=>[key,key==='receipt'?summaryForModel(item):coordinationForModel(item,depth+1)]));
}
export function withoutLocalPaths(text:string):string {
 return text.replace(/[A-Za-z]:[\\/][^\n"<>|]*/g,'[LOCAL_PATH]').replace(/(?:\/Users\/|\/home\/|\/tmp\/)[^\s"<>]*/g,'[LOCAL_PATH]');
}
