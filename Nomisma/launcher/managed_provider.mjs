// API credentials never enter Harness: this adapter receives an ephemeral local lease only.
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const packages = process.env.NOMISMA_DSH_MODULE_ROOT;
if (!packages || !path.isAbsolute(packages)) throw new Error('Nomisma requires its managed Harness package root');
const adapterModule = path.join(packages, '@deepseek-ai/dsh-llm/lib/index.js');
const {LlmAdapter} = await import(pathToFileURL(adapterModule).href);
export const name='nomisma-managed-model-provider';
export const inject=['llm'];
export function apply(ctx){
 const gateway=process.env.NOMISMA_PROVIDER_GATEWAY,token=process.env.NOMISMA_PROVIDER_LEASE;
 if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(gateway??'')||!/^[a-f0-9]{64}$/.test(token??''))throw new Error('Nomisma model broker is unavailable');
 delete process.env.NOMISMA_PROVIDER_LEASE;
 class ManagedAdapter extends LlmAdapter {
  async listModels(provider){return [await this.resolveModel(provider,'configured')];}
  async resolveModel(provider,id){return {provider,id,name:'Nomisma configured model (selected per research turn)',contextWindow:32768,maxTokens:4096,inputModalities:['text']};}
  async *stream(options){
   const response=await fetch(gateway+'/stream',{method:'POST',headers:{authorization:'Bearer '+token,'content-type':'application/json'},body:JSON.stringify({messages:options.messages,tools:options.tools??[],maxTokens:options.maxTokens}),signal:options.signal});
   if(!response.ok||!response.body)throw new Error('Nomisma model request rejected');
   const reader=response.body.getReader(),decoder=new TextDecoder();let pending='';
   try{while(true){const part=await reader.read();if(part.done)break;pending+=decoder.decode(part.value,{stream:true});if(pending.length>2000000)throw new Error('Nomisma model response exceeded budget');let i;while((i=pending.indexOf('\n'))>=0){const line=pending.slice(0,i);pending=pending.slice(i+1);if(!line.trim())continue;const event=JSON.parse(line);if(event.error){const error=new Error('Nomisma model request failed: '+String(event.error).replace(/[^A-Z_]/g,''));error.code=event.error;throw error;}yield event;}}}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  }
 }
 ctx.llm.registerAdapter(['nomisma-managed'],new ManagedAdapter());
}
