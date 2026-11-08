import {describe,it,expect,beforeAll,afterAll,afterEach,vi} from 'vitest';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import crypto from 'node:crypto';import http from 'node:http';
import dns from 'node:dns/promises';
import https from 'node:https';
import type {LookupAddress,LookupAllOptions} from 'node:dns';
import {ProviderStore,type Cipher} from '../model-provider/store';
import {ProviderProbe} from '../model-provider/probe';import {ProviderRuntime} from '../model-provider/runtime';
import type {ContextReviewer} from '../model-provider/cloud-context';
import {stream,response,jsonResponse,sse,normalize,profile,ProviderError,object,array,requestBody} from '../model-provider/protocol';
import type {ProviderDraft,ProviderConfig} from '../../../../shared/types/model-provider';
import {pinnedProviderResponse,type ProviderTransport} from '../provider-destination';

const key=crypto.randomBytes(32);
const cipher:Cipher={async available(){return true;},async encrypt(value){const iv=crypto.randomBytes(12),c=crypto.createCipheriv('aes-256-gcm',key,iv);const data=Buffer.concat([c.update(value,'utf8'),c.final()]);return Buffer.concat([iv,c.getAuthTag(),data]);},async decrypt(value){const d=crypto.createDecipheriv('aes-256-gcm',key,value.subarray(0,12));d.setAuthTag(value.subarray(12,28));return Buffer.concat([d.update(value.subarray(28)),d.final()]).toString('utf8');}};
let server:http.Server,base='';const calls:{model:unknown;authorization:string|undefined;native:boolean;body:Record<string,unknown>}[]=[];
const temps:string[]=[];const runtimes:ProviderRuntime[]=[];
function storeWithRuntime(review?:ContextReviewer,transport?:ProviderTransport){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'nomisma-model-test-'));temps.push(dir);let runtime:ProviderRuntime;const store=new ProviderStore(dir,cipher,id=>runtime?.busy(id)??false);runtime=new ProviderRuntime(store,review,transport);runtimes.push(runtime);return {store,runtime,probe:new ProviderProbe(store,transport)};}
const draft=(model='fixture-a',apiKey='test-key-a'):ProviderDraft=>({provider_type:'custom',provider_name:model,base_url:base,model_id:model,api_key:apiKey});
it('migrates explicit version-zero metadata atomically and retains the source on failure',async()=>{
 const {store}=storeWithRuntime();
 const legacy=path.join(store.directory,'providers.v1.json');
 const record={...draft('legacy-model','legacy-virtual-canary'),base_url:'https://example.invalid/v1'};
 const invalid=JSON.stringify({version:0,providers:[record,{...record,base_url:'file:///secret'}]});
 fs.writeFileSync(legacy,invalid);
 await expect(store.migrateLegacy()).rejects.toMatchObject({code:'INVALID'});
 expect(fs.readFileSync(legacy,'utf8')).toBe(invalid);
 expect(fs.readdirSync(store.directory)).toEqual(['providers.v1.json']);
 fs.writeFileSync(legacy,JSON.stringify({version:0,providers:[record]}));
 await expect(store.migrateLegacy()).resolves.toBe(1);
 const state=await store.list();
 expect(state.default_provider_id).toBeNull();
 expect(state.providers[0].connection_status).toBe('untested');
 expect(await store.key(state.providers[0])).toBe('legacy-virtual-canary');
 expect(fs.readFileSync(legacy,'utf8')).not.toContain('legacy-virtual-canary');
 await expect(store.migrateLegacy()).resolves.toBe(0);
});
async function ready(env:ReturnType<typeof storeWithRuntime>,d:ProviderDraft){const proof=await env.probe.test(d,true);expect(proof.compatible).toBe(true);const verified={...d,verification_id:proof.verification_id};const value=env.probe.proof(verified)!;return env.store.save(verified,value.result,value.fingerprint);}
beforeAll(async()=>{server=http.createServer(async(req,res)=>{let raw='';for await(const p of req)raw+=p.toString();const body=raw?object(JSON.parse(raw)):{};const auth=req.headers.authorization??req.headers['x-api-key'];
 if(req.url==='/redirect'){res.writeHead(307,{location:'/collect-redirect'});res.end();return;}
 if(auth==='Bearer bad-key'){res.writeHead(401,{'content-type':'application/json'});res.end(JSON.stringify({error:{message:'Echo bad-key'}}));return;}
 if(body.model==='quota'){res.writeHead(429,{'content-type':'application/json'});res.end('{"error":{"code":"insufficient_quota"}}');return;}
 if(req.url==='/v1/models'){res.setHeader('content-type','application/json');res.end('{"data":[{"id":"fixture-a"},{"id":"fixture-b"}]}');return;}
 const native=req.url==='/v1/messages';calls.push({model:body.model,authorization:typeof auth==='string'?auth:undefined,native,body});const msgs=array(body.messages??[]);const toolResult=msgs.find(m=>object(m).role==='tool');const wantsTool=Array.isArray(body.tools)&&!toolResult&&body.model!=='no-tools';const userText=JSON.stringify(msgs);const nonce=userText.match(/[a-f0-9]{8}-[a-f0-9-]{27}/)?.[0]??'nonce';const text=toolResult?String(object(toolResult).content):body.response_format?'|{"ok":true}|'.slice(1,-1):'OK';
 if(!body.stream){res.setHeader('content-type','application/json');res.end(JSON.stringify(native?{content:[{type:'text',text}]}:{choices:[{message:{content:text}}]}));return;}
 res.writeHead(200,{'content-type':'text/event-stream'});const emit=(event:unknown)=>res.write('data: '+JSON.stringify(event)+'\n\n');
 if(native&&body.model==='native-tool'){emit({type:'content_block_start',index:0,content_block:{type:'tool_use',id:'native_call',name:'echo',input:{}}});emit({type:'content_block_delta',index:0,delta:{type:'input_json_delta',partial_json:'{"nonce":"native"}'}});emit({type:'content_block_stop',index:0});emit({type:'message_delta',delta:{stop_reason:'tool_use'}});emit({type:'message_stop'});}
 else if(native){emit({type:'content_block_start',index:0,content_block:{type:'text',text:''}});emit({type:'content_block_delta',index:0,delta:{type:'text_delta',text:'Native OK'}});emit({type:'content_block_stop',index:0});emit({type:'message_delta',delta:{stop_reason:'end_turn'}});emit({type:'message_stop'});}
 else if(wantsTool){emit({choices:[{delta:{tool_calls:[{index:0,id:'call_probe',function:{name:'nomisma_capability_echo',arguments:'{"nonce":'}}]}}]});emit({choices:[{delta:{tool_calls:[{index:0,function:{arguments:JSON.stringify(nonce)+'}'}}]},finish_reason:null}]});emit({choices:[{delta:{},finish_reason:'tool_calls'}]});}
 else{emit({choices:[{delta:{content:text}}]});if(body.model!=='truncated')emit({choices:[{delta:{},finish_reason:'stop'}]});}
 res.end('data: [DONE]\n\n');});await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));const address=server.address();if(!address||typeof address==='string')throw Error('No fixture port');base=`http://127.0.0.1:${address.port}/v1`;});
afterEach(()=>{vi.unstubAllGlobals();for(const r of runtimes.splice(0))r.close();});
afterAll(()=>{server.closeAllConnections();server.close();for(const dir of temps){const resolved=path.resolve(dir);if(path.dirname(resolved)!==path.resolve(os.tmpdir())||!path.basename(resolved).startsWith('nomisma-model-test-'))throw Error('Unsafe test cleanup');fs.rmSync(resolved,{recursive:true,force:true});}});

describe('custom transport confinement',()=>{
 it('rejects a mixed public and private DNS answer before connecting',async()=>{
  const lookup=vi.spyOn(dns as {lookup(hostname:string,options:LookupAllOptions):Promise<LookupAddress[]>},'lookup').mockResolvedValue([
   {address:'8.8.8.8',family:4},{address:'169.254.169.254',family:4},
  ]);
  await expect(pinnedProviderResponse(new URL('https://provider.invalid/v1'),{method:'POST'}))
   .rejects.toThrow('Private provider destination');
  expect(lookup).toHaveBeenCalledExactlyOnceWith('provider.invalid',{all:true});
 });
 it.each(['openai','anthropic','deepseek'] as const)('rejects a private DNS answer for the registered %s host before opening a socket',async(provider_type)=>{
  const env=storeWithRuntime();
  const {config}=await env.store.candidate({...draft(),provider_type,base_url:''});
  const lookup=vi.spyOn(dns as {lookup(hostname:string,options:LookupAllOptions):Promise<LookupAddress[]>},'lookup')
   .mockResolvedValue([{address:'169.254.169.254',family:4}]);
  const socket=vi.spyOn(https,'request');
  await expect(response(config,'fixture-dns-secret',{})).rejects.toMatchObject({code:'NETWORK'});
  expect(lookup).toHaveBeenCalledExactlyOnceWith(new URL(config.base_url).hostname,{all:true});
  expect(socket).not.toHaveBeenCalled();
 });
 it('does not replay the virtual credential or body across a real HTTP redirect',async()=>{
  const before=calls.length;
  await expect(pinnedProviderResponse(new URL('/redirect',base),{
   method:'POST',headers:{authorization:'Bearer fixture-redirect-key'},body:'{"fixture":true}',
  })).rejects.toThrow();
  expect(calls).toHaveLength(before);
 });
});

describe('cloud context authorization at the HTTP broker',()=>{
 it('blocks denied extras, filters raw rows, and requires review again on a new turn',async()=>{
  const reviews:Parameters<ContextReviewer>[1][]=[];
  let approved=false;
  const realFetch=globalThis.fetch;
  const env=storeWithRuntime(async(_config,items)=>{reviews.push(items);return approved;},
   (url,init)=>realFetch(url.hostname==='api.openai.com'?base+url.pathname.slice(3):url,init));
  const config=await ready(env,{...draft(),provider_type:'openai',base_url:'https://api.openai.com/v1'});
  await env.store.setDefault(config.provider_id);
  const bind=await env.runtime.environment('review');
  const prompt='My submitted prompt. Contact sample@example.invalid';
  const ask=(extra:boolean)=>realFetch(bind.NOMISMA_PROVIDER_GATEWAY+'/stream',{
   method:'POST',headers:{authorization:'Bearer '+bind.NOMISMA_PROVIDER_LEASE},
   body:JSON.stringify({messages:[
    {role:'user',content:[{type:'text',text:prompt}]},
    ...extra?[{role:'tool',toolCallId:'fixture',content:[{type:'text',text:'{"rows":[{"id":"RAW_SENTINEL"}],"count":42}'}]}]:[],
   ]}),
  }).then(response=>response.text());
  await env.runtime.begin('review',prompt);
  const before=calls.length;
  expect(await ask(false)).toContain('OK');
  expect(JSON.stringify(calls.at(-1)?.body)).toContain('[REDACTED]');
  expect(JSON.stringify(calls.at(-1)?.body)).not.toContain('sample@example.invalid');
  expect(reviews).toHaveLength(0);
  expect(await ask(true)).toContain('CONTEXT_DENIED');
  expect(calls).toHaveLength(before+1);
  expect(JSON.stringify(reviews[0])).toContain('42');
  expect(JSON.stringify(reviews[0])).not.toContain('RAW_SENTINEL');
  approved=true;
  expect(await ask(true)).not.toContain('CONTEXT_DENIED');
  expect(JSON.stringify(calls.at(-1)?.body)).not.toContain('RAW_SENTINEL');
  await ask(true);
  expect(reviews).toHaveLength(2);
  env.runtime.end('review');
  approved=false;
  await env.runtime.begin('review',prompt);
  expect(await ask(true)).toContain('CONTEXT_DENIED');
  expect(reviews).toHaveLength(3);
 });
});

describe('provider protocol and capability checks',()=>{
 it.each([[401,'{}','AUTH'],[403,'{}','AUTH'],[404,'{"error":{"code":"model_not_found"}}','MODEL'],[404,'{}','ENDPOINT'],[429,'{}','RATE_LIMIT'],[402,'{}','QUOTA'],[400,'{"error":{"code":"unsupported_parameter"}}','UNSUPPORTED']])('classifies HTTP %s without forwarding response bodies',async(status,body,code)=>{const env=storeWithRuntime(),candidate=await env.store.candidate(draft());vi.stubGlobal('fetch',async()=>new Response(body,{status}));await expect(response(candidate.config,candidate.key,{})).rejects.toMatchObject({code});});
 it.each([['TimeoutError','TIMEOUT'],['TypeError','NETWORK']])('classifies %s transport failures',async(name,code)=>{const env=storeWithRuntime(),candidate=await env.store.candidate(draft());vi.stubGlobal('fetch',async()=>{const e=new Error('Do not disclose remote details');e.name=name;throw e;});await expect(response(candidate.config,candidate.key,{})).rejects.toMatchObject({code});});
 it('classifies an SSE-body timeout and rejects malformed JSON',async()=>{const body=new ReadableStream({start(controller){const error=new Error('opaque transport');error.name='TimeoutError';controller.error(error);}});await expect((async()=>{for await(const _e of sse(new Response(body,{headers:{'content-type':'text/event-stream'}}))){/* drain */}})()).rejects.toMatchObject({code:'TIMEOUT'});await expect(jsonResponse(new Response('<html>invalid response</html>'))).rejects.toMatchObject({code:'BAD_RESPONSE'});});
 it('keeps OpenAI and DeepSeek request controls distinct',async()=>{const env=storeWithRuntime(),{config}=await env.store.candidate(draft());const input={messages:[{role:'user',content:[{type:'text',text:'hello'}]}],maxTokens:128};const openai=requestBody({...config,provider_type:'openai'},input,true),deepseek=requestBody({...config,provider_type:'deepseek'},input,true);expect(openai.max_completion_tokens).toBe(128);expect(openai.thinking).toBeUndefined();expect(deepseek.max_tokens).toBe(128);expect(deepseek.thinking).toEqual({type:'disabled'});});
 it('performs text, streaming, tool roundtrip and JSON probes instead of guessing names',async()=>{const env=storeWithRuntime(),result=await env.probe.test(draft(),true);expect(result.compatible).toBe(true);expect(result.capability_profile.model.tool_calling).toBe('passed');expect(result.capability_profile.model.structured_output).toBe('passed');expect(result.capability_profile.runtime.multimodal).toBe('not_exposed');expect(result.capability_profile.model.multimodal).toBe('untested');expect(await env.probe.models({...draft(),model_id:''})).toEqual(['fixture-a','fixture-b']);});
 it('reports a text-only model as limited, never fully compatible',async()=>{const env=storeWithRuntime(),result=await env.probe.test(draft('no-tools'),false);expect(result.ok).toBe(true);expect(result.compatible).toBe(false);expect(result.capability_profile.model.tool_calling).toBe('error');});
 it('does not leak echoed keys in auth failures and does not mutate valid config',async()=>{const env=storeWithRuntime(),config=await ready(env,draft());const before=fs.readFileSync(path.join(env.store.directory,'providers.v1.json'),'utf8');const result=await env.probe.test({...draft('fixture-a','bad-key'),provider_id:config.provider_id},false);expect(result.ok).toBe(false);expect(result.error_code).toBe('AUTH');expect(JSON.stringify(result)).not.toContain('bad-key');expect(fs.readFileSync(path.join(env.store.directory,'providers.v1.json'),'utf8')).toBe(before);});
 it('distinguishes insufficient quota from rate limiting',async()=>{const env=storeWithRuntime();expect((await env.probe.test(draft('quota'),false)).error_code).toBe('QUOTA');});
 it('uses Anthropic native messages/header/events rather than OpenAI payloads',async()=>{const conf:ProviderConfig={...normalize(draft()),provider_type:'anthropic',api_protocol:'anthropic-messages',base_url:base,provider_id:'native-test',credential_reference:'',connection_status:'untested',capability_profile:profile('anthropic'),created_at:'',updated_at:''};const events=[];for await(const e of stream(conf,'native-secret',{messages:[{role:'system',content:[{type:'text',text:'System'}]},{role:'user',content:[{type:'text',text:'Hello'}]}]}))events.push(e);expect(events.some(e=>e.text==='Native OK')).toBe(true);const last=calls.at(-1)!;expect(last.native).toBe(true);expect(last.authorization).toBe('native-secret');expect(last.body.system).toBe('System');expect(last.body.max_tokens).toBe(4096);expect(array(last.body.messages).every(m=>object(m).role!=='system')).toBe(true);});
 it('assembles Anthropic tool inputs and projects native tool results correctly',async()=>{const env=storeWithRuntime(),candidate=await env.store.candidate(draft());const conf:ProviderConfig={...candidate.config,provider_type:'anthropic',api_protocol:'anthropic-messages',model_id:'native-tool'};const events=[];for await(const e of stream(conf,'native-test-key',{messages:[{role:'user',content:[{type:'text',text:'Use echo'}]}],tools:[{name:'echo',parameters:{type:'object',properties:{}}}]}))events.push(e);expect(events.find(e=>e.type==='block-end')?.block).toEqual({type:'tool-call',id:'native_call',name:'echo',arguments:'{"nonce":"native"}'});expect(events.at(-1)?.reason).toEqual({kind:'tool-calls'});const body=requestBody(conf,{messages:[{role:'assistant',content:[{type:'tool-call',id:'native_call',name:'echo',arguments:'{}'}]},{role:'tool',toolCallId:'native_call',content:[{type:'text',text:'actual tool result'}]}]},false);expect(array(body.messages).map(m=>object(m).role)).toEqual(['assistant','user']);expect(object(array(object(array(body.messages)[1]).content)[0]).tool_use_id).toBe('native_call');});
 it('rejects truncated streams instead of reporting completion',async()=>{const env=storeWithRuntime(),{config,key}=await env.store.candidate(draft('truncated'));await expect((async()=>{for await(const _event of stream(config,key,{messages:[]})){/* drain */}})()).rejects.toMatchObject({code:'BAD_RESPONSE'});});
 it('rejects image input while runtime multimodal is not exposed',()=>{const config:ProviderConfig={...normalize(draft()),provider_id:'test',credential_reference:'',connection_status:'untested',capability_profile:profile('custom'),created_at:'',updated_at:''};expect(()=>requestBody(config,{messages:[{role:'user',content:[{type:'image',data:'image'}]}]},false)).toThrow(ProviderError);});
 it('rejects non-HTTPS external endpoints, URL credentials and mismatched official hosts',()=>{for(const url of ['http://example.com/v1','https://user:secret@example.com/v1','https://example.com/v1?api_key=secret'])expect(()=>normalize({...draft(),base_url:url})).toThrow();expect(()=>normalize({...draft(),provider_type:'openai'})).toThrow();});
});

describe('secure metadata, rotation and turn switching',()=>{
 it('never reuses a saved credential for a different endpoint without re-entry',async()=>{const env=storeWithRuntime(),config=await ready(env,draft());await expect(env.store.candidate({provider_id:config.provider_id,provider_name:config.provider_name,provider_type:'custom',model_id:config.model_id,base_url:'https://different-provider.example/v1'})).rejects.toMatchObject({code:'REENTER'});expect(await env.store.key(config)).toBe('test-key-a');});
 it('persists only encrypted credential blobs and restores multiple profiles',async()=>{const env=storeWithRuntime(),a=await ready(env,draft()),b=await ready(env,draft('fixture-b','test-key-b'));await env.store.setDefault(a.provider_id);for(const name of fs.readdirSync(env.store.directory)){const bytes=fs.readFileSync(path.join(env.store.directory,name));expect(bytes.includes(Buffer.from('test-key-a'))).toBe(false);expect(bytes.includes(Buffer.from('test-key-b'))).toBe(false);}const reloaded=new ProviderStore(env.store.directory,cipher);expect((await reloaded.list()).providers).toHaveLength(2);expect(await reloaded.key(reloaded.current())).toBe('test-key-a');await reloaded.setDefault(b.provider_id);expect(await reloaded.key(reloaded.current())).toBe('test-key-b');});
 it('binds verification to exact endpoint, model and secret',async()=>{const env=storeWithRuntime(),d=draft(),probe=await env.probe.test(d,false),proof=env.probe.proof({...d,verification_id:probe.verification_id})!;await expect(env.store.save({...d,model_id:'other'},proof.result,proof.fingerprint)).rejects.toMatchObject({code:'VERIFY'});expect((await env.store.list()).providers).toHaveLength(0);});
 it('fails closed if encryption is unavailable or credential decryption fails',async()=>{const env=storeWithRuntime();const bad=new ProviderStore(env.store.directory,{...cipher,async available(){return false;}});await expect(bad.save(draft(),null,null)).rejects.toMatchObject({code:'STORAGE'});const conf=await ready(env,draft());fs.writeFileSync(path.join(env.store.directory,conf.credential_reference+'.secret'),'corrupt');await expect(env.store.key(conf)).rejects.toMatchObject({code:'CREDENTIAL'});});
 it('requires confirmation and preserves other configs when deleting',async()=>{const env=storeWithRuntime(),a=await ready(env,draft()),b=await ready(env,draft('fixture-b','key-b'));await expect(env.store.remove(a.provider_id,false)).rejects.toMatchObject({code:'INVALID'});await env.store.remove(a.provider_id,true);expect(await env.store.key(env.store.get(b.provider_id))).toBe('key-b');expect((await env.store.list()).providers).toHaveLength(1);});
 it('pins a provider through a running turn; new default takes effect on next turn in the same lease',async()=>{const env=storeWithRuntime(),a=await ready(env,draft()),b=await ready(env,draft('fixture-b','test-key-b'));await env.store.setDefault(a.provider_id);const binding=await env.runtime.environment('session-a');await env.runtime.begin('session-a');await expect(env.runtime.begin('session-a')).rejects.toMatchObject({code:'BUSY'});await env.store.setDefault(b.provider_id);await expect(env.store.remove(a.provider_id,true)).rejects.toMatchObject({code:'BUSY'});
  const ask=async()=>{const r=await fetch(binding.NOMISMA_PROVIDER_GATEWAY+'/stream',{method:'POST',headers:{authorization:'Bearer '+binding.NOMISMA_PROVIDER_LEASE},body:JSON.stringify({messages:[{role:'user',content:[{type:'text',text:'Hello'}]}]})});const text=await r.text();expect(text).toContain('block-end');};
  await ask();expect(calls.at(-1)?.model).toBe('fixture-a');expect(calls.at(-1)?.authorization).toBe('Bearer test-key-a');env.runtime.end('session-a');await env.runtime.begin('session-a');await ask();expect(calls.at(-1)?.model).toBe('fixture-b');expect(calls.at(-1)?.authorization).toBe('Bearer test-key-b');env.runtime.end('session-a');await env.store.remove(a.provider_id,true);expect(env.store.current().provider_id).toBe(b.provider_id);});
 it('blocks missing default and unauthenticated local broker access',async()=>{const env=storeWithRuntime(),binding=await env.runtime.environment('empty');await expect(env.runtime.begin('empty')).rejects.toMatchObject({code:'SETUP'});const r=await fetch(binding.NOMISMA_PROVIDER_GATEWAY+'/stream',{method:'POST',body:'{}'});expect(r.status).toBe(403);});
});
