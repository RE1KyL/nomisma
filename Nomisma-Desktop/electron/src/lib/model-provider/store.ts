import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {ProviderConfig,ProviderDraft,ProviderState,ProbeResult} from '../../../../shared/types/model-provider';
import {normalize,profile,secret,ProviderError,object,string} from './protocol';
import {isPathRedirected} from '../windows-runtime.cjs';

export interface Cipher {available():Promise<boolean>;encrypt(value:string):Promise<Buffer>;decrypt(value:Buffer):Promise<string>;}
interface Document {version:1;providers:ProviderConfig[];default_provider_id:string|null;setup_dismissed:boolean;}
const identifier=(id:string)=>/^(pv|cred)_[a-f0-9-]{36}$/.test(id);
export class ProviderStore {
 private serial:Promise<unknown>=Promise.resolve();
 constructor(readonly directory:string,private cipher:Cipher,private busy:(id:string)=>boolean=()=>false){fs.mkdirSync(directory,{recursive:true});if(isPathRedirected(directory))throw new ProviderError('STORAGE');}
 private file(name:string):string {const file=path.join(this.directory,name);if(fs.existsSync(file)&&isPathRedirected(file))throw new ProviderError('STORAGE');return file;}
 private read():Document {const file=this.file('providers.v1.json');if(!fs.existsSync(file))return {version:1,providers:[],default_provider_id:null,setup_dismissed:false};let data:unknown;try{data=JSON.parse(fs.readFileSync(file,'utf8'));}catch{throw new ProviderError('STORAGE');}const value=object(data);if(value.version!==1||!Array.isArray(value.providers))throw new ProviderError('STORAGE');
  const providers=value.providers.map((raw):ProviderConfig=>{const p=object(raw),base=normalize({provider_name:string(p.provider_name),provider_type:p.provider_type==='openai'||p.provider_type==='anthropic'||p.provider_type==='deepseek'||p.provider_type==='custom'?p.provider_type:(()=>{throw new ProviderError('STORAGE');})(),base_url:string(p.base_url),model_id:string(p.model_id)});const id=string(p.provider_id),ref=string(p.credential_reference);if(!identifier(id)||!identifier(ref))throw new ProviderError('STORAGE');const cap=profile(base.provider_type);const rawCap=object(p.capability_profile);for(const scope of ['adapter','model','runtime'] as const){const source=object(rawCap[scope]);for(const field of ['text','streaming','tool_calling','structured_output','multimodal'] as const){const v=source[field];if(v!=='passed'&&v!=='unsupported'&&v!=='untested'&&v!=='error'&&v!=='not_exposed')throw new ProviderError('STORAGE');cap[scope][field]=v;}}cap.checked_at=rawCap.checked_at===null?null:string(rawCap.checked_at);const status=p.connection_status;if(status!=='ready'&&status!=='limited'&&status!=='failed'&&status!=='untested')throw new ProviderError('STORAGE');return {...base,provider_id:id,credential_reference:ref,connection_status:status,capability_profile:cap,created_at:string(p.created_at),updated_at:string(p.updated_at)};});
  const def=value.default_provider_id===null?null:string(value.default_provider_id);if(def&&!providers.some(p=>p.provider_id===def))throw new ProviderError('STORAGE');return {version:1,providers,default_provider_id:def,setup_dismissed:value.setup_dismissed===true};
 }
 private write(doc:Document):void {const file=this.file('providers.v1.json'),tmp=this.file('metadata_'+crypto.randomUUID()+'.tmp');try{fs.writeFileSync(tmp,JSON.stringify(doc,null,2),{flag:'wx',mode:0o600});fs.renameSync(tmp,file);}catch{throw new ProviderError('STORAGE');}finally{if(fs.existsSync(tmp))fs.unlinkSync(tmp);}}
 private lock<T>(operation:()=>Promise<T>):Promise<T>{const next=this.serial.then(operation,operation);this.serial=next.catch(()=>{});return next;}
 /** Import only the explicit version-zero envelope, in place. An interrupted
  * import can leave unused ciphertext, but the original remains until commit. */
 migrateLegacy():Promise<number>{
  return this.lock(async()=>{
   const source=this.file('providers.v1.json');
   if(!fs.existsSync(source))return 0;
   const stat=fs.lstatSync(source);
   if(!stat.isFile()||stat.size>1024*1024)throw new ProviderError('STORAGE');
   const original=fs.readFileSync(source,'utf8');
   const value=object(JSON.parse(original));
   if(value.version!==0)return 0;
   if(!Array.isArray(value.providers)||value.providers.length>64||!await this.cipher.available())throw new ProviderError('STORAGE');
   const now=new Date().toISOString();
   const encryptedFiles:string[]=[];
   const providers:ProviderConfig[]=[];
   try{
    for(const raw of value.providers){
     const record=object(raw);
     const type=record.provider_type;
     if(type!=='openai'&&type!=='anthropic'&&type!=='deepseek'&&type!=='custom')throw new ProviderError('INVALID');
     const base=normalize({provider_type:type,provider_name:string(record.provider_name),base_url:string(record.base_url),model_id:string(record.model_id)});
     const key=secret(string(record.api_key));
     const reference='cred_'+crypto.randomUUID();
     const target=this.file(reference+'.secret');
     const ciphertext=await this.cipher.encrypt(key);
     fs.writeFileSync(target,ciphertext,{flag:'wx',mode:0o600});
     encryptedFiles.push(target);
     const config:ProviderConfig={...base,provider_id:'pv_'+crypto.randomUUID(),credential_reference:reference,
      connection_status:'untested',capability_profile:profile(type),created_at:now,updated_at:now};
     if(await this.key(config)!==key)throw new ProviderError('STORAGE');
     providers.push(config);
    }
    if(fs.readFileSync(source,'utf8')!==original)throw new ProviderError('STALE');
    this.write({version:1,providers,default_provider_id:null,setup_dismissed:value.setup_dismissed===true});
   }catch(error){
    for(const file of encryptedFiles){try{fs.unlinkSync(file);}catch{/* Orphan ciphertext cannot expose the key. */}}
    throw error;
   }
   return providers.length;
  });
 }
 async list():Promise<ProviderState>{const d=this.read();return {providers:d.providers,default_provider_id:d.default_provider_id,setup_dismissed:d.setup_dismissed,secure_storage_available:await this.cipher.available()};}
 get(id:string):ProviderConfig{if(!identifier(id))throw new ProviderError('INVALID');const p=this.read().providers.find(p=>p.provider_id===id);if(!p)throw new ProviderError('INVALID');return p;}
 current():ProviderConfig {const d=this.read();if(!d.default_provider_id)throw new ProviderError('SETUP');return this.get(d.default_provider_id);}
 async key(config:ProviderConfig):Promise<string>{if(!await this.cipher.available())throw new ProviderError('STORAGE');try{return secret(await this.cipher.decrypt(fs.readFileSync(this.file(config.credential_reference+'.secret'))));}catch{throw new ProviderError('CREDENTIAL');}}
 async draftKey(draft:ProviderDraft):Promise<string>{return draft.api_key?secret(draft.api_key):draft.provider_id?this.key(this.get(draft.provider_id)):(()=>{throw new ProviderError('CREDENTIAL');})();}
 async candidate(draft:ProviderDraft,requireModel=true):Promise<{config:ProviderConfig;key:string;fingerprint:string}>{const base=normalize(draft,requireModel);if(draft.provider_id&&!draft.api_key){const prior=this.get(draft.provider_id);if(prior.base_url!==base.base_url||prior.provider_type!==base.provider_type)throw new ProviderError('REENTER');}const key=await this.draftKey(draft),now=new Date().toISOString();return {config:{...base,provider_id:draft.provider_id??'draft',credential_reference:'',connection_status:'untested',capability_profile:profile(base.provider_type),created_at:now,updated_at:now},key,fingerprint:crypto.createHash('sha256').update(JSON.stringify(base)+'\0'+key).digest('hex')};}
 save(draft:ProviderDraft,verification:ProbeResult|null,verifiedFingerprint:string|null):Promise<ProviderConfig>{return this.lock(async()=>{const doc=this.read(),prior=draft.provider_id?this.get(draft.provider_id):null;if(prior&&this.busy(prior.provider_id))throw new ProviderError('BUSY');if(prior&&draft.expected_updated_at!==prior.updated_at)throw new ProviderError('STALE');const candidate=await this.candidate(draft);if(verification&&candidate.fingerprint!==verifiedFingerprint)throw new ProviderError('VERIFY');if(verification&&!verification.ok)throw new ProviderError('VERIFY');if(prior?.provider_id===doc.default_provider_id&&!verification?.compatible)throw new ProviderError('VERIFY');
  if(!await this.cipher.available())throw new ProviderError('STORAGE');const ref='cred_'+crypto.randomUUID(),target=this.file(ref+'.secret');let encrypted:Buffer;try{encrypted=await this.cipher.encrypt(candidate.key);}catch{throw new ProviderError('STORAGE');}fs.writeFileSync(target,encrypted,{flag:'wx',mode:0o600});
  const config:ProviderConfig={...candidate.config,provider_id:prior?.provider_id??'pv_'+crypto.randomUUID(),credential_reference:ref,created_at:prior?.created_at??candidate.config.created_at,updated_at:new Date(Math.max(Date.now(),prior?Date.parse(prior.updated_at)+1:0)).toISOString(),capability_profile:verification?.capability_profile??candidate.config.capability_profile,connection_status:verification?(verification.compatible?'ready':'limited'):'untested'};
  try{doc.providers=doc.providers.filter(p=>p.provider_id!==config.provider_id).concat(config);this.write(doc);}catch(e){fs.unlinkSync(target);throw e;}if(prior){try{fs.unlinkSync(this.file(prior.credential_reference+'.secret'));}catch{/* Orphan ciphertext is harmless; never roll back a committed replacement. */}}return config;
 });}
 setDefault(id:string):Promise<void>{return this.lock(async()=>{const d=this.read(),p=this.get(id);if(p.connection_status!=='ready')throw new ProviderError('VERIFY');await this.key(p);d.default_provider_id=id;this.write(d);});}
 remove(id:string,confirmed:boolean):Promise<void>{return this.lock(async()=>{if(!confirmed)throw new ProviderError('INVALID');if(this.busy(id))throw new ProviderError('BUSY');const d=this.read(),p=this.get(id);d.providers=d.providers.filter(x=>x.provider_id!==id);if(d.default_provider_id===id)d.default_provider_id=null;this.write(d);try{fs.unlinkSync(this.file(p.credential_reference+'.secret'));}catch{/* Metadata is committed; no plaintext exists. */}});}
 dismiss():Promise<void>{return this.lock(async()=>{const d=this.read();d.setup_dismissed=true;this.write(d);});}
}
