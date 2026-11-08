// Real ACP protocol acceptance against the official installed DSH profile.
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { Readable, Writable } from 'node:stream';
import { fileURLToPath } from 'node:url';
import crypto from 'node:crypto';
import * as acp from '@agentclientprotocol/sdk';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const config = JSON.parse(fs.readFileSync(path.join(root, 'nomisma-agent.example.json'), 'utf8'));
const evidence = path.join(root, '../Nomisma/tests/phase1b1/acp-protocol-evidence.json');
const records = [];
const checks = {};
const live = [];
let cancelOnApproval = false;
const record = (event, data = {}) => records.push({ time: new Date().toISOString(), event, ...data });
const mcpServers = config.mcpServers.map(s => ({ name:s.name, command:s.command, args:s.args,
  env:Object.entries(s.env).map(([name,value])=>({name,value})) }));

async function connect() {
  const env = { ...process.env, ...config.agent.env };
  for(const k of Object.keys(env)) if(/^(http_proxy|https_proxy|all_proxy|node_options|electron_run_as_node)$/i.test(k)) delete env[k];
  const proc = spawn(config.agent.binary, config.agent.args, {env,cwd:config.workspace,stdio:['pipe','pipe','pipe'],windowsHide:true,shell:false});
  live.push(proc);
  let stderr = '';
  proc.stderr.on('data', b=>{ stderr += b.toString(); });
  const conn = new acp.ClientSideConnection(()=>({
    async sessionUpdate(params) { record('session/update',params); },
    async requestPermission(params) {
      record('session/request_permission',params);
      if(cancelOnApproval) {
        cancelOnApproval = false;
        await conn.cancel({sessionId:params.sessionId});
        record('session/cancel',{sessionId:params.sessionId,while:'permission_pending'});
        return {outcome:{outcome:'cancelled'}};
      }
      const call = records.findLast(r=>r.event==='session/update' && r.sessionId===params.sessionId && r.update.sessionUpdate==='tool_call' && r.update.toolCallId===params.toolCall.toolCallId);
      const safe = call?.update.title.includes('nomisma_health_check') === true;
      const option = params.options.find(o=>o.kind===(safe?'allow_once':'reject_once'));
      record('permission_response',{sessionId:params.sessionId,optionId:option?.optionId});
      return option ? {outcome:{outcome:'selected',optionId:option.optionId}} : {outcome:{outcome:'cancelled'}};
    },
  }),acp.ndJsonStream(Writable.toWeb(proc.stdin),Readable.toWeb(proc.stdout)));
  const init = await conn.initialize({protocolVersion:acp.PROTOCOL_VERSION,clientCapabilities:{},clientInfo:{name:'Nomisma acceptance',version:'0.1'}});
  record('initialize',init);
  return {proc,conn,getStderr:()=>stderr};
}

async function close(c,id) {
  const result = await c.conn.extMethod('session/close',{sessionId:id});
  record('session/close',{sessionId:id,result});
}

let deadline;
try {
  deadline=setTimeout(()=>{record('timeout');for(const p of live)p.kill();fs.writeFileSync(evidence,JSON.stringify({checks,records,error:'acceptance deadline'},null,2));process.exit(2);},180000);
  const first=await connect();
  const a=await first.conn.newSession({cwd:config.workspace,mcpServers}); record('session/new',a);
  const b=await first.conn.newSession({cwd:config.workspace,mcpServers}); record('session/new',b);
  checks.two_independent_sessions=a.sessionId!==b.sessionId;
  for(const opt of a.configOptions??[]) if(opt.id==='reasoning_effort') {
    record('session/set_config_option',await first.conn.setSessionConfigOption({sessionId:a.sessionId,configId:opt.id,value:'off'}));
  }
  const nonce='NMS-'+crypto.randomBytes(6).toString('hex');
  const result=await first.conn.prompt({sessionId:a.sessionId,prompt:[{type:'text',text:`请调用 Nomisma 的健康检查工具，确认当前运行环境，并报告 Python 版本和工具 execution_id。记住本会话标记 ${nonce}，在回答中带上它。`} ]});
  record('session/prompt_result',result);
  const output=records.filter(r=>r.event==='session/update'&&r.sessionId===a.sessionId&&r.update.sessionUpdate==='agent_message_chunk').map(r=>r.update.content.text??'').join('');
  checks.real_tool_call=records.some(r=>r.event==='session/update'&&r.update.sessionUpdate==='tool_call'&&r.update.title.includes('nomisma_health_check'));
  checks.final_python_version=output.includes('3.11.18');
  checks.approval=records.some(r=>r.event==='permission_response'&&r.optionId==='allow-once');
  cancelOnApproval=true;
  const cancelResult=await first.conn.prompt({sessionId:b.sessionId,prompt:[{type:'text',text:'请实际调用 nomisma_health_check 工具，不要自行猜测环境。'}]});
  record('cancel_prompt_result',cancelResult);
  checks.cancel_during_task=cancelResult.stopReason==='cancelled';
  await close(first,b.sessionId);
  await close(first,a.sessionId);
  first.proc.stdin.end();
  await new Promise(resolve=>first.proc.once('exit',resolve));
  const second=await connect();
  const listing=await second.conn.unstable_listSessions({cwd:config.workspace}); record('session/list',listing);
  checks.list_persisted=listing.sessions.some(s=>s.sessionId===a.sessionId);
  const resumed=await second.conn.unstable_resumeSession({sessionId:a.sessionId,cwd:config.workspace,mcpServers});record('session/resume',resumed);
  const start=records.length;
  const recall=await second.conn.prompt({sessionId:a.sessionId,prompt:[{type:'text',text:'请只回答此前本会话记住的 NMS- 开头标记，不调用工具。'}]});record('resume_prompt_result',recall);
  const recalled=records.slice(start).filter(r=>r.event==='session/update'&&r.update.sessionUpdate==='agent_message_chunk').map(r=>r.update.content.text??'').join('');
  checks.resume_context=recalled.includes(nonce);
  await close(second,a.sessionId);
  try { await second.conn.newSession({cwd:config.workspace,mcpServers:[{name:'broken',command:'C:/Nomisma-missing-python.exe',args:[],env:[]}]});checks.mcp_failure_rejected=false; }
  catch(error){checks.mcp_failure_rejected=true;record('expected_mcp_start_failure',{type:error.constructor.name,message:error.message});}
  second.proc.stdin.end();
  await new Promise(resolve=>second.proc.once('exit',resolve));
  fs.writeFileSync(evidence,JSON.stringify({checks,records,final_answer:output,resumed_answer:recalled},null,2));
  console.log(JSON.stringify(checks));
} catch(error) {
  record('failure',{type:error.constructor.name,message:error.message});
  fs.writeFileSync(evidence,JSON.stringify({checks,records},null,2));
  console.error(error.constructor.name+': '+error.message);
  process.exitCode=1;
} finally {
  clearTimeout(deadline);
  for(const p of live)if(p.exitCode===null)p.stdin.end();
}
