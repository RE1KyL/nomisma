import type {ProviderConfig,ProviderDraft,ProviderType,CapabilityProfile} from '../../../../shared/types/model-provider';
import {cloudAllowed} from '../privacy-policy';
import {pinnedProviderResponse,type ProviderTransport} from '../provider-destination';
import {recordSecurityEvent} from '../security-audit';

export const defaults:Record<ProviderType,string>={openai:'https://api.openai.com/v1',anthropic:'https://api.anthropic.com/v1',deepseek:'https://api.deepseek.com/v1',custom:''};
export const errorMessages:Record<string,string>={LOCAL_ONLY:'当前为 Local-only；仅允许明确配置的本机模型，云端请求已阻止。',REENTER:'更换服务商或 API Base URL 后，请重新输入 API Key 以授权新接口。',TOOL_CHECK:'工具调用往返校验未通过，当前配置暂不能用于研究 Agent。',AUTH:'API Key 无效或没有访问权限。',MODEL:'Model ID 不存在或无权访问。',ENDPOINT:'API Base URL 或接口路径不正确。',TIMEOUT:'请求超时，请检查网络和服务商状态。',NETWORK:'无法连接模型服务，请检查网络和 Base URL。',QUOTA:'账户配额或余额不足。',RATE_LIMIT:'请求受到限流，请稍后重试。',UNSUPPORTED:'当前模型不支持所请求的能力。',BAD_RESPONSE:'服务商返回了非预期或不完整的数据。',CANCELLED:'请求已取消。',STORAGE:'系统安全凭证存储不可用，未保存明文密钥。',CREDENTIAL:'凭证无法解密或已丢失，请重新输入 API Key。',SETUP:'尚未配置可用模型。请打开 Settings → Model Providers。',BUSY:'当前配置正在执行研究任务，请等待该任务结束后再更新或删除。',INVALID:'配置格式不正确。',STALE:'配置已经更新，请重新加载后再编辑。',VERIFY:'请先成功测试这份配置，再将其用于默认模型。'};
export class ProviderError extends Error {constructor(public code:string){super(errorMessages[code]??errorMessages.BAD_RESPONSE);this.name='ProviderError';}}
errorMessages.CONTEXT_DENIED='云端上下文未经批准，或隐私设置已变化。此请求未发送。';
errorMessages.CONTEXT_LIMIT='额外云端上下文超过可审查的范围。请缩小当前研究任务或开启新会话。';
function transportError(error:unknown,signal?:AbortSignal):ProviderError {if(error instanceof ProviderError)return error;if(signal?.aborted)return new ProviderError('CANCELLED');return new ProviderError(error instanceof Error&&['TimeoutError','AbortError'].includes(error.name)?'TIMEOUT':'NETWORK');}
async function boundedText(r:Response,limit:number,truncate=false):Promise<string>{if(!r.body)throw new ProviderError('BAD_RESPONSE');const reader=r.body.getReader(),decoder=new TextDecoder();let result='',size=0;try{while(true){const part=await reader.read();if(part.done)return result+decoder.decode();size+=part.value.length;if(size>limit){if(truncate)return result+decoder.decode(part.value.subarray(0,Math.max(0,limit-(size-part.value.length))));throw new ProviderError('BAD_RESPONSE');}result+=decoder.decode(part.value,{stream:true});}}catch(error){throw transportError(error);}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}}
export function object(value:unknown):Record<string,unknown>{if(typeof value!=='object'||!value||Array.isArray(value))throw new ProviderError('BAD_RESPONSE');return Object.fromEntries(Object.entries(value));}
export function array(value:unknown):unknown[]{if(!Array.isArray(value))throw new ProviderError('BAD_RESPONSE');return value;}
export function string(value:unknown):string{if(typeof value!=='string')throw new ProviderError('BAD_RESPONSE');return value;}
export function profile(type:ProviderType):CapabilityProfile {
 const common={text:'untested',streaming:'untested',tool_calling:'untested',structured_output:'untested',multimodal:'untested'} as const;
 return {adapter:{...common,text:'passed',streaming:'passed',tool_calling:'passed',structured_output:type==='anthropic'?'not_exposed':'passed',multimodal:'not_exposed'},model:{...common},runtime:{...common,text:'passed',streaming:'passed',tool_calling:'passed',structured_output:'not_exposed',multimodal:'not_exposed'},checked_at:null};
}
export function normalize(draft:ProviderDraft,requireModel=true):Pick<ProviderConfig,'provider_name'|'provider_type'|'api_protocol'|'base_url'|'model_id'|'display_name'> {
 if(!Object.hasOwn(defaults,draft.provider_type)||typeof draft.provider_name!=='string'||!draft.provider_name.trim()||draft.provider_name.length>120||typeof draft.model_id!=='string'||(requireModel&&!draft.model_id.trim())||draft.model_id.length>240)throw new ProviderError('INVALID');
 let url:URL;try{url=new URL(draft.base_url||defaults[draft.provider_type]);}catch{throw new ProviderError('INVALID');}
 if(url.username||url.password||url.search||url.hash||(!['https:'].includes(url.protocol)&&!(url.protocol==='http:'&&['127.0.0.1','localhost','[::1]'].includes(url.hostname))))throw new ProviderError('INVALID');
 const hosts={openai:'api.openai.com',anthropic:'api.anthropic.com',deepseek:'api.deepseek.com'};
 if(draft.provider_type!=='custom'&&(url.hostname!==hosts[draft.provider_type]||url.port))throw new ProviderError('INVALID');
 return {provider_name:draft.provider_name.trim(),provider_type:draft.provider_type,api_protocol:draft.provider_type==='anthropic'?'anthropic-messages':'openai-chat',base_url:url.href.replace(/\/+$/,''),model_id:draft.model_id.trim(),display_name:draft.provider_name.trim()};
}
export function secret(value:string):string {if(!value.trim()||value.length>8192||/[\r\n\0]/.test(value))throw new ProviderError('INVALID');return value.trim();}
export interface DshInput {messages:unknown[];tools?:unknown[];maxTokens?:number;}
type Message = Record<string,unknown>;
export function messages(input:DshInput,anthropic:boolean):Message[] {
 const result:Message[]=[];
 for(const raw of input.messages){const m=object(raw),role=string(m.role),blocks=array(m.content??[]);let text='';const calls:Message[]=[],content:Message[]=[];
  for(const value of blocks){const block=object(value);if(block.type==='text'){const t=string(block.text);text+=t;content.push({type:'text',text:t});}else if(block.type==='tool-call'){const id=string(block.id),name=string(block.name),args=string(block.arguments);let parsed:unknown;try{parsed=JSON.parse(args);}catch{throw new ProviderError('BAD_RESPONSE');}calls.push({id,type:'function',function:{name,arguments:args}});content.push({type:'tool_use',id,name,input:parsed});}else if(block.type==='reasoning'){/* Provider-private reasoning is not replayed across vendors. */}else throw new ProviderError('UNSUPPORTED');}
  if(anthropic){if(role==='system'||role==='developer'){result.push({role:'system',content:text});continue;}if(role==='tool')result.push({role:'user',content:[{type:'tool_result',tool_use_id:string(m.toolCallId),content:text,is_error:m.isError===true}]});else if(content.length)result.push({role:role==='assistant'?'assistant':'user',content});}
  else if(role==='tool')result.push({role:'tool',tool_call_id:string(m.toolCallId),content:text});else if(['system','developer','user','assistant'].includes(role))result.push({role:role==='developer'?'system':role,content:text||null,...calls.length?{tool_calls:calls}:{}});
 }
 return result;
}
export interface ProviderAdapter {
 protocol:ProviderConfig['api_protocol'];endpoint:string;
 headers:(key:string)=>Record<string,string>;
 build:(config:ProviderConfig,input:DshInput,stream:boolean,extra:Message)=>Message;
}
const bearerHeaders=(key:string)=>({'content-type':'application/json',authorization:'Bearer '+key});
function chatBody(config:ProviderConfig,input:DshInput,streaming:boolean,extra:Message,official:boolean):Message {
 const tools=(input.tools??[]).map(raw=>{const tool=object(raw);return {type:'function',function:{name:string(tool.name),description:tool.description??'',parameters:tool.parameters}};});
 const budget=Math.min(input.maxTokens??4096,4096);
 return {model:config.model_id,messages:messages(input,false),stream:streaming,...official?{max_completion_tokens:budget}:{max_tokens:budget},...tools.length?{tools}:{},...extra};
}
export const providerAdapters:Record<ProviderType,ProviderAdapter>={
 openai:{protocol:'openai-chat',endpoint:'/chat/completions',headers:bearerHeaders,build:(c,i,s,e)=>chatBody(c,i,s,e,true)},
 custom:{protocol:'openai-chat',endpoint:'/chat/completions',headers:bearerHeaders,build:(c,i,s,e)=>chatBody(c,i,s,e,false)},
 deepseek:{protocol:'openai-chat',endpoint:'/chat/completions',headers:bearerHeaders,build:(c,i,s,e)=>({...chatBody(c,i,s,e,false),thinking:{type:'disabled'}})},
 anthropic:{protocol:'anthropic-messages',endpoint:'/messages',headers:key=>({'content-type':'application/json','x-api-key':key,'anthropic-version':'2023-06-01'}),build:(config,input,streaming,extra)=>{
  const converted=messages(input,true),tools=(input.tools??[]).map(raw=>{const tool=object(raw);return {name:string(tool.name),description:tool.description??'',input_schema:tool.parameters};});
  return {model:config.model_id,max_tokens:Math.min(input.maxTokens??4096,4096),stream:streaming,system:converted.filter(m=>m.role==='system').map(m=>m.content).join('\n'),messages:converted.filter(m=>m.role!=='system'),...tools.length?{tools}:{},...extra};
 }},
};
function adapter(config:ProviderConfig):ProviderAdapter {const found=providerAdapters[config.provider_type];if(!found||found.protocol!==config.api_protocol)throw new ProviderError('INVALID');return found;}
export function requestBody(config:ProviderConfig,input:DshInput,streaming:boolean,extra:Message={}):Message{return adapter(config).build(config,input,streaming,extra);}
export async function response(config:ProviderConfig,key:string,body:Message,signal?:AbortSignal,method='POST',suffix?:string,transport:ProviderTransport=pinnedProviderResponse):Promise<Response>{
 const purpose=method==='GET'?'directory':config.provider_id==='draft'||!config.credential_reference?'probe':'inference';
 if(!cloudAllowed(config.base_url)){recordSecurityEvent('model',purpose,'denied',config.provider_type);throw new ProviderError('LOCAL_ONLY');}
 let r:Response;try{const url=new URL(config.base_url+(suffix??adapter(config).endpoint));const init:RequestInit={method,headers:adapter(config).headers(key),...method==='POST'?{body:JSON.stringify(body)}:{},signal:signal?AbortSignal.any([signal,AbortSignal.timeout(25000)]):AbortSignal.timeout(25000),redirect:'error'};r=await transport(url,init);}catch(e){recordSecurityEvent('model',purpose,'failed',config.provider_type);throw transportError(e,signal);}
 recordSecurityEvent('model',purpose,r.ok?'completed':'failed',config.provider_type);
 if(!r.ok){let detail='';try{detail=await boundedText(r,8192,true);}catch{/* Body is intentionally never logged. */}const quota=/insufficient_quota|insufficient_balance|credit balance|payment_required/i.test(detail);throw new ProviderError(r.status===401||r.status===403?'AUTH':r.status===429?(quota?'QUOTA':'RATE_LIMIT'):r.status===402?'QUOTA':r.status===404?(/model/i.test(detail)?'MODEL':'ENDPOINT'):r.status===400?(/model.*(not|exist)/i.test(detail)?'MODEL':'UNSUPPORTED'):'BAD_RESPONSE');}
 return r;
}
export async function jsonResponse(r:Response):Promise<Record<string,unknown>> {const text=await boundedText(r,8_000_000);try{return object(JSON.parse(text));}catch{throw new ProviderError('BAD_RESPONSE');}}
export async function* sse(r:Response,signal?:AbortSignal):AsyncGenerator<Record<string,unknown>> {
 if(!r.body||!r.headers.get('content-type')?.includes('text/event-stream'))throw new ProviderError('BAD_RESPONSE');
 const reader=r.body.getReader(),decoder=new TextDecoder();let buffer='',size=0;
 try{while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;buffer+=decoder.decode(value,{stream:true});if(buffer.length>2_000_000||size>8_000_000)throw new ProviderError('BAD_RESPONSE');let i;while((i=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,i).replace(/\r$/,'');buffer=buffer.slice(i+1);if(!line.startsWith('data:'))continue;const data=line.slice(5).trim();if(data==='[DONE]')return;if(!data)continue;let event:Record<string,unknown>;try{event=object(JSON.parse(data));}catch{throw new ProviderError('BAD_RESPONSE');}if(event.error||event.type==='error')throw new ProviderError('BAD_RESPONSE');yield event;}}if(buffer.trim())throw new ProviderError('BAD_RESPONSE');}catch(error){throw transportError(error,signal);}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
}
export async function* stream(config:ProviderConfig,key:string,input:DshInput,signal?:AbortSignal,transport?:ProviderTransport):AsyncGenerator<Message>{
 const r=await response(config,key,requestBody(config,input,true),signal,'POST',undefined,transport);const native=config.api_protocol==='anthropic-messages';
 const blocks=new Map<number,{type:string;text:string;id:string;name:string;arguments:string}>();let finished=false,toolOffset:number|null=null;
 const end=(index:number)=>{const b=blocks.get(index);if(!b)throw new ProviderError('BAD_RESPONSE');if(b.type==='tool-call'){try{object(JSON.parse(b.arguments||'{}'));}catch{throw new ProviderError('BAD_RESPONSE');}return {type:'block-end',index,block:{type:'tool-call',id:b.id,name:b.name,arguments:b.arguments||'{}'}};}return {type:'block-end',index,block:{type:'text',text:b.text}};};
 for await(const event of sse(r,signal)){
  if(native){const index=typeof event.index==='number'?event.index:0;
   if(event.type==='content_block_start'){const b=object(event.content_block),type=b.type==='tool_use'?'tool-call':'text';blocks.set(index,{type,text:typeof b.text==='string'?b.text:'',id:typeof b.id==='string'?b.id:'',name:typeof b.name==='string'?b.name:'',arguments:''});yield {type:'block-start',index,blockType:type};}
   if(event.type==='content_block_delta'){const d=object(event.delta),b=blocks.get(index);if(!b)throw new ProviderError('BAD_RESPONSE');if(d.type==='text_delta'){const text=string(d.text);b.text+=text;yield {type:'text-delta',index,text};}else if(d.type==='input_json_delta'){const args=string(d.partial_json);b.arguments+=args;yield {type:'tool-call-delta',index,id:b.id,name:b.name,argumentsDelta:args};}}
   if(event.type==='content_block_stop')yield end(index);
   if(event.type==='message_delta'){const d=object(event.delta);if(d.stop_reason==='max_tokens')throw new ProviderError('BAD_RESPONSE');}
   if(event.type==='message_stop'){finished=true;yield {type:'finish',reason:{kind:[...blocks.values()].some(b=>b.type==='tool-call')?'tool-calls':'stop'}};}
  }else{const choices=array(event.choices??[]);if(!choices.length)continue;const choice=object(choices[0]),delta=object(choice.delta??{});
   if(typeof delta.content==='string'&&delta.content){if(blocks.get(0)?.type==='tool-call')throw new ProviderError('BAD_RESPONSE');if(!blocks.has(0)){blocks.set(0,{type:'text',text:'',id:'',name:'',arguments:''});yield {type:'block-start',index:0,blockType:'text'};}blocks.get(0)!.text+=delta.content;yield {type:'text-delta',index:0,text:delta.content};}
   for(const raw of array(delta.tool_calls??[])){const call=object(raw);toolOffset??=blocks.get(0)?.type==='text'?1:0;const index=Number(call.index)+toolOffset;if(!Number.isSafeInteger(index)||index<0)throw new ProviderError('BAD_RESPONSE');if(!blocks.has(index)){blocks.set(index,{type:'tool-call',text:'',id:'',name:'',arguments:''});yield {type:'block-start',index,blockType:'tool-call'};}const b=blocks.get(index)!,fn=object(call.function??{});if(typeof call.id==='string')b.id+=call.id;if(typeof fn.name==='string')b.name+=fn.name;const args=typeof fn.arguments==='string'?fn.arguments:'';b.arguments+=args;yield {type:'tool-call-delta',index,id:b.id,name:b.name,argumentsDelta:args};}
   if(choice.finish_reason){if(choice.finish_reason==='length'||choice.finish_reason==='content_filter')throw new ProviderError('BAD_RESPONSE');for(const index of blocks.keys())yield end(index);finished=true;yield {type:'finish',reason:{kind:choice.finish_reason==='tool_calls'?'tool-calls':'stop'}};}
  }
 }
 if(!finished||!blocks.size)throw new ProviderError('BAD_RESPONSE');
}
