// Narrow real-runtime cancellation test; no GUI automation or mock model/tools.
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { Readable, Writable } from 'node:stream';
import { fileURLToPath } from 'node:url';
import * as acp from '@agentclientprotocol/sdk';
import { cancelAcpPrompt } from '../electron/src/lib/acp-cancel.ts';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.resolve(desktop, '../Nomisma/tests/phase1b1/stop-fix-20261101');
const cfg = JSON.parse(fs.readFileSync(path.join(desktop, 'nomisma-agent.example.json'), 'utf8'));
const state = path.join(output, 'live-state', 'Nomisma');
const profile = path.join(state, 'runtime/dsh-home/profiles/acp');
fs.mkdirSync(profile, { recursive: true });
for (const name of ['package.json', 'cordis.yml', 'cordis.patch.yml']) {
  fs.copyFileSync(path.join(cfg.stateRoot, 'runtime/dsh-home/profiles/acp', name), path.join(profile, name), fs.constants.COPYFILE_EXCL);
}
const audit = path.join(output, 'python-audit');
const env = { ...process.env, ...cfg.agent.env, NOMISMA_STATE_ROOT: state,
  DSH_HOME: path.join(state, 'runtime/dsh-home'), DSH_AGENTS_HOME: path.join(state, 'runtime/agents-home') };
for (const key of Object.keys(env)) if (/^(HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NODE_OPTIONS|ELECTRON_RUN_AS_NODE)$/i.test(key)) delete env[key];
const child = spawn(cfg.agent.binary, cfg.agent.args, { cwd:cfg.workspace, env, stdio:['pipe','pipe','pipe'], shell:false, windowsHide:true });
child.stderr.on('data', () => { /* Runtime diagnostics stay off the protocol/evidence. */ });
const events = [];
const pending = new Map();
let approvalObserved = false;
const connection = new acp.ClientSideConnection(() => ({
  async sessionUpdate(data) { events.push({ event:'session/update', ...data }); },
  async requestPermission(params) {
    approvalObserved = true;
    events.push({ event:'permission_wait', sessionId:params.sessionId, toolCallId:params.toolCall.toolCallId });
    const response = new Promise(resolve => pending.set(params.toolCall.toolCallId, { resolve }));
    // Same cancellation function called by Harnss acp:cancel IPC.
    void cancelAcpPrompt(connection, params.sessionId, pending).then(() => events.push({event:'cancel_sent'}));
    return response;
  },
}), acp.ndJsonStream(Writable.toWeb(child.stdin), Readable.toWeb(child.stdout)));
const timer = setTimeout(() => { child.kill(); process.exitCode = 2; }, 120000);
const checks = {};
try {
  await connection.initialize({ protocolVersion:acp.PROTOCOL_VERSION, clientCapabilities:{} });
  const mcpServers = cfg.mcpServers.map(server => ({ name:server.name, command:server.command, args:server.args,
    env:Object.entries({...server.env,NOMISMA_LOG_DIR:audit}).map(([name,value])=>({name,value})) }));
  const a = await connection.newSession({ cwd:cfg.workspace, mcpServers });
  const b = await connection.newSession({ cwd:cfg.workspace, mcpServers:[] });
  for (const session of [a,b]) {
    if ((session.configOptions??[]).some(option=>option.id==='reasoning_effort')) {
      await connection.setSessionConfigOption({ sessionId:session.sessionId, configId:'reasoning_effort', value:'off' });
    }
  }
  const cancelled = await connection.prompt({ sessionId:a.sessionId, prompt:[{type:'text',text:'请实际调用 nomisma_health_check 工具，等待我的批准，不要猜测结果。'}] });
  checks.permission_wait_observed = approvalObserved;
  checks.prompt_cancelled = cancelled.stopReason === 'cancelled';
  checks.pending_approval_cleared = pending.size === 0;
  const lines = fs.existsSync(audit) ? fs.readdirSync(audit).flatMap(name => fs.readFileSync(path.join(audit,name),'utf8').trim().split('\n').filter(Boolean).map(line=>JSON.parse(line))) : [];
  checks.python_service_really_started = lines.some(line=>line.event==='service_started');
  checks.no_python_tool_execution = !lines.some(line=>line.event==='tool_started'||line.event==='tool_finished');
  const start = events.length;
  const unaffected = await connection.prompt({ sessionId:b.sessionId, prompt:[{type:'text',text:'只回答 NOMISMA_B_STILL_ACTIVE，不调用工具。'}] });
  const answer = events.slice(start).filter(event=>event.update?.sessionUpdate==='agent_message_chunk').map(event=>event.update.content.text??'').join('');
  checks.other_session_still_works = unaffected.stopReason==='end_turn' && answer.includes('NOMISMA_B_STILL_ACTIVE');
  // The cancelled session remains open: its config API must still work.
  const option = (a.configOptions??[]).find(item=>item.id==='reasoning_effort');
  if (option) { await connection.setSessionConfigOption({sessionId:a.sessionId,configId:option.id,value:'off'}); checks.cancelled_session_still_open=true; }
  for (const session of [a,b]) await connection.extMethod('session/close',{sessionId:session.sessionId});
  fs.writeFileSync(path.join(output,'live-cancel-evidence.json'),JSON.stringify({checks,stopReason:cancelled.stopReason,events,python_audit:lines},null,2),{flag:'wx'});
  console.log(JSON.stringify(checks));
  if (!Object.values(checks).every(Boolean)) process.exitCode=1;
} catch(error) {
  fs.writeFileSync(path.join(output,'live-cancel-failure.json'),JSON.stringify({checks,error:String(error),events},null,2),{flag:'wx'});
  console.error(String(error)); process.exitCode=1;
} finally {
  clearTimeout(timer); child.stdin.end();
}
