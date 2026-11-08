// Fixed official ACP entry point. All arguments are arrays; no shell is used.
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const { contextWorkspace, isRedirected } = require('./project_context.cjs');
const { ensureInstalledProfile, validateManagedProfile } = require('./managed_profile.cjs');
const workspace = contextWorkspace(process.env, path.resolve(__dirname, '../workspace/projects/hs300_demo'));
const stateRoot = process.env.NOMISMA_STATE_ROOT;
if (!stateRoot || !path.isAbsolute(stateRoot) || path.basename(stateRoot).toLowerCase() !== 'nomisma') {
  throw new Error('Nomisma requires an explicit independent NOMISMA_STATE_ROOT');
}
const root = path.join(stateRoot, 'runtime');
const installed = process.env.NOMISMA_BUNDLED_ACP === '1';
const harness = installed ? path.resolve(__dirname, '../../agent') : 'D:/DSK HARNS';
const packages = path.join(harness, installed ? 'node_modules' : 'resources/app.asar/dsh/node_modules');
const env = { ...process.env, ELECTRON_RUN_AS_NODE: '1',
  DSH_HOME: path.join(root, 'dsh-home'),
  DSH_AGENTS_HOME: path.join(root, 'agents-home'),
  DSH_TELEMETRY_DISABLED: '1', PYTHONUTF8: '1',
  NOMISMA_DSH_MODULE_ROOT: packages,
  NOMISMA_WORKSPACE: workspace };
for (const key of Object.keys(env)) {
  if (/(?:API_?KEY|TOKEN|SECRET|PASSWORD|AUTHORIZATION|^HTTP_PROXY$|^HTTPS_PROXY$|^ALL_PROXY$|^NODE_OPTIONS$)/i.test(key)) delete env[key];
}
if (installed) {
  delete env.ELECTRON_RUN_AS_NODE;
  if (!env.NOMISMA_PROVIDER_GATEWAY || !env.NOMISMA_PROVIDER_LEASE) throw new Error('Installed ACP requires its managed gateway');
  fs.mkdirSync(root, {recursive: true});
  if (!fs.lstatSync(root).isDirectory() || isRedirected(root)) throw new Error('Unsafe installed runtime state');
  ensureInstalledProfile(env.DSH_HOME);
}
if (!fs.existsSync(path.join(env.DSH_HOME, 'profiles/acp/cordis.patch.yml'))) {
  throw new Error('Nomisma ACP configuration is missing; see NOMISMA_RUN.md');
}
// A failed policy plugin must fail closed, rather than silently run without Ask.
// The launcher owns this nonce; an Agent cannot choose or reuse a receipt path.
const policyDir = path.join(root, 'policy');
fs.mkdirSync(policyDir, { recursive: true });
if (installed && (!fs.lstatSync(policyDir).isDirectory() || isRedirected(policyDir))) throw new Error('Unsafe installed policy directory');
const policyReady = path.join(policyDir, 'ready_' + crypto.randomUUID() + '.json');
env.NOMISMA_POLICY_READY_FILE = policyReady;
const manifestHash = crypto.createHash('sha256').update(fs.readFileSync(path.join(__dirname, 'research_tool_manifest.json'))).digest('hex');
let runtimePatch=path.join(__dirname,'research_runtime.patch.json');
let ownedProviderPatch=null;
if(env.NOMISMA_PROVIDER_GATEWAY||env.NOMISMA_PROVIDER_LEASE){
  if(!env.NOMISMA_PROVIDER_GATEWAY||!env.NOMISMA_PROVIDER_LEASE)throw new Error('Incomplete managed provider binding');
  if (!installed) validateManagedProfile(env.DSH_HOME);
  for(const key of ['OPENAI_API_KEY','ANTHROPIC_API_KEY','DEEPSEEK_API_KEY'])delete env[key];
  const managed=JSON.parse(fs.readFileSync(path.join(__dirname,'managed_provider.patch.json'),'utf8'));
  managed.push({insert:[{id:'nomisma-managed-model-provider',name:path.join(__dirname,'managed_provider.mjs')}]});
  ownedProviderPatch=path.join(policyDir,'provider_'+crypto.randomUUID()+'.json');
  fs.writeFileSync(ownedProviderPatch,JSON.stringify([...JSON.parse(fs.readFileSync(runtimePatch,'utf8')),...managed]),{flag:'wx'});
  runtimePatch=ownedProviderPatch;
}
const cleanupProvider=()=>{if(ownedProviderPatch){try{fs.unlinkSync(ownedProviderPatch);}catch{}}};
const args = ['--require', path.join(__dirname, 'acp_diagnostics.cjs')];
if (installed) {
  args.push(path.join(__dirname, 'installed_dsh_profile.mjs'), runtimePatch);
} else {
  args.push('--expose-internals', path.join(packages, '@deepseek-ai/dsh-desktop-host/lib/cli.js'),
    '--profile', 'acp', '--patch', runtimePatch);
}
const executable = installed ? process.execPath : path.join(harness, 'DeepSeek Harness.exe');
const child = spawn(executable, args, {
  cwd: installed ? harness : workspace, env, stdio: ['pipe', 'pipe', 'inherit'], windowsHide: true, shell: false,
});
// Do not deliver any ACP requests until the registered policy has attested.
const deadline = Date.now() + 20000;
let policyAttested = false;
const readyTimer = setInterval(() => {
  try {
    if (fs.existsSync(policyReady)) {
      const receipt = JSON.parse(fs.readFileSync(policyReady, 'utf8'));
      if (receipt.manifest_sha256 !== manifestHash || receipt.mode !== (env.NOMISMA_MODE ?? null)) throw new Error('Policy receipt mismatch');
      clearInterval(readyTimer); fs.unlinkSync(policyReady);
      policyAttested = true;
      process.stdin.pipe(child.stdin);
    } else if (Date.now() > deadline) throw new Error('Policy readiness timeout');
  } catch {
    clearInterval(readyTimer); console.error('Nomisma research policy did not initialize; ACP requests were blocked');
    child.kill(); process.exitCode = 1;
  }
}, 25);
child.stdout.pipe(process.stdout);
process.stdin.on('end', () => child.stdin.end());
child.stdin.on('error', () => { /* Exit/EOF is handled by the child lifecycle. */ });
child.on('error', () => { cleanupProvider(); console.error('Nomisma official ACP process could not start'); process.exitCode = 1; });
child.on('exit', code => { cleanupProvider(); clearInterval(readyTimer); process.exit(policyAttested ? (code ?? 1) : 1); });
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
