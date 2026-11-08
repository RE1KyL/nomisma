// Existing profile entry is retained. Finite names mirror Python MCP contracts.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
export const name = 'nomisma-health-policy';
export const inject = ['tools'];
const manifest=JSON.parse(fs.readFileSync(new URL('./research_tool_manifest.json',import.meta.url),'utf8'));
export function allowedTools(mode) {
  return new Set((manifest[mode]??['nomisma_health_check']).map(name=>'mcp__nomisma__'+name));
}
export function apply(ctx) {
  const allowed=allowedTools(process.env.NOMISMA_MODE);
  // Official scoped restriction: remove inherited global tools (plan/resource
  // discovery included). ACP mounts this project's MCP tools in agent scope;
  // they remain visible and are independently checked by guard + Python.
  ctx.on('agent/created',({agent})=>{agent.ctx.tools.restrict({allow:[]});});
  ctx.tools.guard(exec=>allowed.has(exec.name)?undefined:'Tool not permitted in this Nomisma project mode');
  ctx.on('tools/pre-execute',async(exec,next)=>{
    const prior=await next();
    if(prior.kind!=='allow')return prior;
    if(!allowed.has(exec.name))return {kind:'deny',reason:'Tool not permitted in this Nomisma project mode'};
    return {kind:'ask',reason:'Review the exact dataset version, finite parameters and new output scope; allow once only'};
  });
  const ready=process.env.NOMISMA_POLICY_READY_FILE;
  const directory=path.join(process.env.NOMISMA_STATE_ROOT,'runtime','policy');
  if(!ready||path.dirname(ready)!==directory||!/^ready_[a-f0-9-]{36}\.json$/.test(path.basename(ready)))throw new Error('Missing owned policy readiness path');
  const manifestBytes=fs.readFileSync(new URL('./research_tool_manifest.json',import.meta.url));
  fs.writeFileSync(ready,JSON.stringify({mode:process.env.NOMISMA_MODE??null,
    manifest_sha256:crypto.createHash('sha256').update(manifestBytes).digest('hex')}),{flag:'wx'});
}
