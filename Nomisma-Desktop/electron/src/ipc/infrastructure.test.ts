import fs from 'fs';import path from 'path';import os from 'os';
import {beforeAll,afterAll,beforeEach,expect,it,vi} from 'vitest';
const mock=vi.hoisted(()=>({handlers:new Map<string,Function>(),user:'',confirm:vi.fn(),windows:vi.fn()}));
vi.mock('electron',()=>({app:{getPath:()=>mock.user,getAppPath:()=>process.cwd()},ipcMain:{handle:(n:string,f:Function)=>mock.handlers.set(n,f)},dialog:{showMessageBox:mock.confirm},BrowserWindow:class {constructor(){mock.windows();throw Error('PNG uses real Electron UI acceptance, not a fake raster');}}}));
import {registerInfrastructure} from './infrastructure';
let base='',project='',oldEnv:NodeJS.ProcessEnv;
const invoke=(action:string,payload:Record<string,unknown>={})=>mock.handlers.get('infrastructure:request')!(null,'shared-fixture',action,payload);
beforeAll(()=>{
  oldEnv={...process.env};base=fs.mkdtempSync(path.join(os.tmpdir(),'nomisma-shared-'));const state=path.join(base,'Nomisma');mock.user=path.join(state,'desktop-dev');const data=path.join(mock.user,'openacpui-data');fs.mkdirSync(data,{recursive:true});project=path.join(state,'workspace/projects/fixture');fs.mkdirSync(project,{recursive:true});
  fs.writeFileSync(path.join(data,'projects.json'),JSON.stringify([{id:'shared-fixture',name:'Shared integration fixture',path:project,mode:'finance',status:'active',createdAt:1}]));
  fs.writeFileSync(path.join(project,'data.csv'),'date,value\n2025-01-01,1\n2025-01-02,2\n');
  Object.assign(process.env,{NOMISMA_FILE_TEST:'1',NOMISMA_STATE_ROOT:state,NOMISMA_PROJECT_ROOT:path.resolve('../Nomisma')});registerInfrastructure();
});
afterAll(()=>{process.env=oldEnv;if(base&&path.dirname(path.resolve(base))===path.resolve(os.tmpdir())&&path.basename(base).startsWith('nomisma-shared-'))fs.rmSync(base,{recursive:true,force:true});});
beforeEach(()=>mock.confirm.mockReset().mockResolvedValue({response:1}));
it('opens a real page without any write permission and cannot publish arbitrary bytes',async()=>{
  const p=await invoke('sheet.page',{path:'data.csv'});expect(p.read_only).toBe(true);expect(mock.confirm).not.toHaveBeenCalled();
  await expect(invoke('chart.publish_export',{content_base64:'anything'})).rejects.toThrow('not exposed');
  await expect(invoke('chart.create',{authorization:'user_confirmed'})).rejects.toThrow('Untrusted');
});
it('Deny creates an audit but no chart or modified source',async()=>{
  const before=fs.readFileSync(path.join(project,'data.csv'));const file=await invoke('artifact.inspect',{path:'data.csv'});mock.confirm.mockResolvedValueOnce({response:0});
  expect(await invoke('chart.create_from_file',{path:'data.csv',sha256:file.sha256})).toBeNull();
  expect(fs.readFileSync(path.join(project,'data.csv'))).toEqual(before);expect(fs.existsSync(path.join(project,'.nomisma/shared/charts'))).toBe(false);
  expect((await invoke('audit')).some((e:any)=>e.authorization==='denied')).toBe(true);
},30000);
it('saves versions and exports the actual shared SVG renderer through real Python',async()=>{
  const before=fs.readFileSync(path.join(project,'data.csv')),file=await invoke('artifact.inspect',{path:'data.csv'});
  const o=await invoke('chart.create_from_file',{path:'data.csv',sha256:file.sha256});const c={...o.chart.chart_config,title:'<safe> actual points'};
  const v=await invoke('chart.save',{chart_id:o.chart.chart_id,expected_version:1,config:c});expect(v.chart.version).toBe(2);
  const exported=await invoke('chart.export',{chart_id:o.chart.chart_id,version:2,format:'svg'});
  const svg=fs.readFileSync(path.join(project,exported.file_path),'utf8');expect(svg).toContain('&lt;safe&gt; actual points');expect(svg).toContain('value · 2025-01-02: 2');expect(fs.readFileSync(path.join(project,'data.csv'))).toEqual(before);
},30000);
