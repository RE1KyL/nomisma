import crypto from 'node:crypto';
import type {ProviderDraft,ProbeResult,Capability} from '../../../../shared/types/model-provider';
import {ProviderStore} from './store';
import {response as modelResponse,requestBody,jsonResponse,stream as modelStream,object,array,string,ProviderError,errorMessages} from './protocol';
import type {ProviderTransport} from '../provider-destination';

export class ProviderProbe {
 private proofs=new Map<string,{fingerprint:string;result:ProbeResult;expires:number}>();
 constructor(private store:ProviderStore,private transport?:ProviderTransport){}
 proof(draft:ProviderDraft):{fingerprint:string;result:ProbeResult}|null {if(!draft.verification_id)return null;const proof=this.proofs.get(draft.verification_id);if(!proof||proof.expires<Date.now())throw new ProviderError('VERIFY');return proof;}
 async test(draft:ProviderDraft,optional:boolean):Promise<ProbeResult>{const {config,key,fingerprint}=await this.store.candidate(draft);const cap=config.capability_profile;let errorCode:string|undefined;const nonce=crypto.randomUUID();
  const response=(c:typeof config,k:string,body:Parameters<typeof modelResponse>[2])=>
   modelResponse(c,k,body,undefined,'POST',undefined,this.transport);
  const stream=(c:typeof config,k:string,input:Parameters<typeof modelStream>[2])=>modelStream(c,k,input,undefined,this.transport);
  const check=async(field:Capability,run:()=>Promise<void>)=>{try{await run();cap.model[field]='passed';}catch(e){const code=e instanceof ProviderError?e.code:'BAD_RESPONSE';cap.model[field]=code==='UNSUPPORTED'?'unsupported':'error';errorCode??=code;}};
  await check('text',async()=>{const r=await jsonResponse(await response(config,key,requestBody(config,{maxTokens:512,messages:[{role:'user',content:[{type:'text',text:'Reply with the word OK.'}]}]},false)));const text=config.api_protocol==='anthropic-messages'?array(r.content).map(b=>object(b).text??'').join(''):object(object(array(r.choices)[0]).message).content;if(typeof text!=='string'||!text.trim())throw new ProviderError('BAD_RESPONSE');});
  if(cap.model.text==='passed'){
   await check('streaming',async()=>{let text='';for await(const e of stream(config,key,{maxTokens:512,messages:[{role:'user',content:[{type:'text',text:'Reply with the word OK.'}]}]}))if(e.type==='text-delta')text+=string(e.text);if(!text.trim())throw new ProviderError('BAD_RESPONSE');});
   await check('tool_calling',async()=>{const name='nomisma_capability_echo',input={maxTokens:1024,messages:[{role:'user',content:[{type:'text',text:`Call ${name} once with nonce ${nonce}. Do not answer in text.`}]}],tools:[{name,description:'Return the supplied nonce. Capability probe only; never executes a research tool.',parameters:{type:'object',properties:{nonce:{type:'string'}},required:['nonce'],additionalProperties:false}}]};let call:Record<string,unknown>|null=null;for await(const e of stream(config,key,input)){if(e.type==='block-end'){const b=object(e.block);if(b.type==='tool-call')call=b;}}if(!call||call.name!==name||object(JSON.parse(string(call.arguments))).nonce!==nonce)throw new ProviderError('TOOL_CHECK');
    const continuation={maxTokens:512,messages:[...input.messages,{role:'assistant',content:[call]},{role:'tool',toolCallId:call.id,content:[{type:'text',text:nonce}]},{role:'user',content:[{type:'text',text:'Repeat exactly the nonce returned by the tool.'}]}]};let text='';for await(const e of stream(config,key,continuation))if(e.type==='text-delta')text+=string(e.text);if(!text.includes(nonce))throw new ProviderError('TOOL_CHECK');
   });
   if(optional&&config.api_protocol!=='anthropic-messages')await check('structured_output',async()=>{const body=requestBody(config,{maxTokens:256,messages:[{role:'user',content:[{type:'text',text:'Return JSON: {"ok":true}.'}]}]},false,{response_format:{type:'json_object'}});const r=await jsonResponse(await response(config,key,body));const text=string(object(object(array(r.choices)[0]).message).content);if(object(JSON.parse(text)).ok!==true)throw new ProviderError('BAD_RESPONSE');});
  }
  cap.checked_at=new Date().toISOString();const compatible=['text','streaming','tool_calling'].every(k=>cap.model[k as 'text'|'streaming'|'tool_calling']==='passed');const result:ProbeResult={verification_id:crypto.randomUUID(),ok:cap.model.text==='passed',compatible,capability_profile:cap,...errorCode?{error_code:errorCode}:{},message:(compatible?'文本、流式与工具调用往返验证通过。可用于研究 Agent。':cap.model.text==='passed'?'连接成功，但流式或工具调用未通过，不能标为完全兼容。':'连接测试失败；已有配置未被修改。')+(!compatible&&errorCode?' '+errorMessages[errorCode]:'')};this.proofs.set(result.verification_id,{fingerprint,result,expires:Date.now()+600000});for(const [id,p] of this.proofs)if(p.expires<Date.now())this.proofs.delete(id);return result;
 }
 async models(draft:ProviderDraft):Promise<string[]>{const {config,key}=await this.store.candidate(draft,false);const r=await jsonResponse(await modelResponse(config,key,{},undefined,'GET','/models',this.transport));return array(r.data).map(m=>string(object(m).id)).slice(0,2000);}
}
