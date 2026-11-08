import http from 'node:http';
import crypto from 'node:crypto';
import type {ProviderConfig} from '../../../../shared/types/model-provider';
import {ProviderStore} from './store';
import {object,array,stream,ProviderError} from './protocol';
import {cloudAllowed,isLoopback,privacySettings} from '../privacy-policy';
import {modelContext,contextDigest,type ContextItem,type ContextReviewer} from './cloud-context';
import type {ProviderTransport} from '../provider-destination';

interface Turn {
 config:ProviderConfig;
 key:string|null;
 submittedText:string|undefined;
 approved:Set<string>;
 signal:AbortController;
 activeRequest:boolean;
}
interface Lease {sessionId:string;turn:Turn|null;}
/** The ACP session/context stays intact; provider selection is pinned for a whole user turn. */
export class ProviderRuntime {
 private leases=new Map<string,Lease>();private server:http.Server|null=null;private starting:Promise<number>|null=null;
 constructor(private store:ProviderStore,private review:ContextReviewer=async()=>false,private transport?:ProviderTransport){}
 busy(id:string):boolean{return [...this.leases.values()].some(l=>l.turn?.config.provider_id===id);}
 async begin(sessionId:string,submittedText?:string):Promise<void>{
  const lease=[...this.leases.values()].find(l=>l.sessionId===sessionId);
  if(!lease)throw new ProviderError('SETUP');
  if(lease.turn)throw new ProviderError('BUSY');
  const config=this.store.current();
  if(config.connection_status!=='ready')throw new ProviderError('VERIFY');
  if(!cloudAllowed(config.base_url))throw new ProviderError('LOCAL_ONLY');
  const turn:Turn={config,key:null,submittedText,approved:new Set(),signal:new AbortController(),activeRequest:false};
  lease.turn=turn;
  try{
   turn.key=await this.store.key(config);
   if(turn.signal.signal.aborted)throw new ProviderError('CANCELLED');
  }catch(error){if(lease.turn===turn)lease.turn=null;throw error;}
 }
 end(sessionId:string):void{
  for(const lease of this.leases.values()){
   if(lease.sessionId!==sessionId||!lease.turn)continue;
   lease.turn.signal.abort();
   lease.turn.key=null;
   lease.turn=null;
  }
 }
 release(sessionId:string):void{this.end(sessionId);for(const [token,lease] of this.leases)if(lease.sessionId===sessionId)this.leases.delete(token);}
 async environment(sessionId:string):Promise<Record<string,string>> {const port=await this.listen();this.release(sessionId);const token=crypto.randomBytes(32).toString('hex');this.leases.set(token,{sessionId,turn:null});return {NOMISMA_PROVIDER_GATEWAY:`http://127.0.0.1:${port}`,NOMISMA_PROVIDER_LEASE:token};}
 private listen():Promise<number>{if(this.starting)return this.starting;this.starting=new Promise((resolve,reject)=>{const server=http.createServer(async(req,res)=>{
   const fail=(code:string)=>{if(res.destroyed)return;if(!res.headersSent){res.writeHead(400,{'content-type':'application/x-ndjson'});}res.end(JSON.stringify({error:code})+'\n');};
   const token=(req.headers.authorization??'').replace(/^Bearer /,'');const lease=this.leases.get(token);
   if(req.method!=='POST'||req.url!=='/stream'||!lease||req.headers.origin){res.writeHead(403);res.end();return;}
   const turn=lease.turn;
   const key=turn?.key;
   if(!turn||!key){fail('SETUP');return;}
   if(turn.activeRequest){fail('BUSY');return;}
   turn.activeRequest=true;
   const abort=new AbortController();res.on('close',()=>abort.abort());
   const signal=AbortSignal.any([abort.signal,turn.signal.signal]);
   try{
    const {config}=turn;
    req.setEncoding('utf8');
    let raw='';
    for await(const part of req){raw+=part.toString();if(raw.length>8_000_000)throw new ProviderError('INVALID');}
    const body=object(JSON.parse(raw));
    const policy=privacySettings();
    const local=isLoopback(new URL(config.base_url));
    const {input,extra}=modelContext({messages:array(body.messages),tools:array(body.tools??[]),maxTokens:typeof body.maxTokens==='number'?body.maxTokens:4096},turn.submittedText,policy.mode!=='standard',local?undefined:policy.sensitive_fields??[]);
    if(!cloudAllowed(config.base_url))throw new ProviderError('LOCAL_ONLY');
    if(key.length>=8&&JSON.stringify(input).includes(JSON.stringify(key).slice(1,-1)))throw new ProviderError('CONTEXT_DENIED');
    if(!local){
     const pending=new Map<string,ContextItem>();
     for(const item of extra){
      const id=contextDigest(item);
      if(!turn.approved.has(id))pending.set(id,item);
     }
     if(pending.size){
      const items=[...pending.values()];
      if(items.reduce((size,item)=>size+item.text.length,0)>30000)throw new ProviderError('CONTEXT_LIMIT');
      if(!await this.review(config,items,signal))throw new ProviderError('CONTEXT_DENIED');
      for(const id of pending.keys())turn.approved.add(id);
     }
    }
    if(signal.aborted)throw new ProviderError('CANCELLED');
    if(JSON.stringify(privacySettings())!==JSON.stringify(policy))throw new ProviderError('CONTEXT_DENIED');
    res.writeHead(200,{'content-type':'application/x-ndjson','cache-control':'no-store'});for await(const event of stream(config,key,input,signal,this.transport)){if(res.destroyed||signal.aborted)throw new ProviderError('CANCELLED');if(!res.write(JSON.stringify(event)+'\n'))await new Promise<void>(resolve=>{const done=()=>{res.off('drain',done);res.off('close',done);resolve();};res.once('drain',done);res.once('close',done);if(res.destroyed)done();});}
    res.end();
   }catch(e){fail(e instanceof ProviderError?e.code:'BAD_RESPONSE');}finally{turn.activeRequest=false;}
  });server.maxConnections=16;server.requestTimeout=90000;server.headersTimeout=10000;server.on('error',()=>reject(new ProviderError('NETWORK')));server.listen(0,'127.0.0.1',()=>{const addr=server.address();if(!addr||typeof addr==='string'){reject(new ProviderError('NETWORK'));return;}this.server=server;server.unref();resolve(addr.port);});});return this.starting;}
 close():void{for(const lease of this.leases.values())this.end(lease.sessionId);this.leases.clear();this.server?.closeAllConnections();this.server?.close();this.server=null;this.starting=null;}
}
