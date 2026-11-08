import { afterEach, expect, it, vi } from 'vitest';
const mocked=vi.hoisted(()=>({
  handlers:new Map<string,(...args:unknown[])=>unknown>(),
  agents:[{id:'claude-code',engine:'claude'},{id:'codex',engine:'codex'},{id:'nomisma-dsh-acp',engine:'acp'},{id:'other-agent',engine:'acp'}],
}));
vi.mock('electron',()=>({ipcMain:{handle:(name:string,handler:(...args:unknown[])=>unknown)=>mocked.handlers.set(name,handler)}}));
vi.mock('../lib/agent-registry',()=>({
  listAgents:()=>mocked.agents,loadUserAgents:vi.fn(),saveAgent:vi.fn(),deleteAgent:vi.fn(),updateCachedConfig:vi.fn(),checkBinaries:vi.fn(),
}));
import { register } from './agent-registry';
afterEach(()=>vi.unstubAllEnvs());

it('isolated Nomisma exposes only its existing registered ACP agent',()=>{
  vi.stubEnv('NOMISMA_DESKTOP_MODE','isolated');register();
  expect(mocked.handlers.get('agents:list')!()).toEqual([{id:'nomisma-dsh-acp',engine:'acp'}]);
});
it('a file-only profile cannot fall back to built-in Claude or Codex',()=>{
  vi.stubEnv('NOMISMA_DESKTOP_MODE','isolated');register();
  const previous=mocked.agents;
  try{mocked.agents=previous.filter(a=>a.id!=='nomisma-dsh-acp');expect(mocked.handlers.get('agents:list')!()).toEqual([]);}
  finally{mocked.agents=previous;}
});
it('upstream mode retains its original registry',()=>{
  vi.stubEnv('NOMISMA_DESKTOP_MODE',undefined);register();
  expect(mocked.handlers.get('agents:list')!()).toEqual(mocked.agents);
});
