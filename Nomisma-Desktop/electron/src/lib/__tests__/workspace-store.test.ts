import { afterEach, expect, it } from 'vitest';
import fs from 'fs';import os from 'os';import path from 'path';
import { WorkspaceStore } from '../workspace-store';
import { spawnSync } from 'child_process';
const roots:string[]=[];
function fixture(){const base=fs.mkdtempSync(path.join(os.tmpdir(),'nomisma-workspace-'));roots.push(base);return new WorkspaceStore(path.join(base,'Nomisma/desktop-dev/openacpui-data'),path.join(base,'projects'));}
afterEach(()=>{for(const root of roots.splice(0)){if(path.dirname(root)!==path.resolve(os.tmpdir()))throw new Error('Unsafe cleanup');fs.rmSync(root,{recursive:true,force:true});}});
it('backs up and migrates legacy metadata once without changing IDs, paths or extra fields',()=>{
  const store=fixture();const root=path.join(store.managedRoot,'legacy');fs.mkdirSync(root,{recursive:true});
  const bytes=JSON.stringify([{id:'nomisma-hs300',name:'HS300',path:root,createdAt:123,icon:'chart',custom:{preserved:true}}]);fs.writeFileSync(store.file,bytes);
  const [project]=store.read();expect(project.mode).toBe('finance');expect(project.id).toBe('nomisma-hs300');expect(project.path).toBe(root);
  expect((project as unknown as {custom:unknown}).custom).toEqual({preserved:true});
  const backups=()=>fs.readdirSync(store.dataDir).filter(n=>n.startsWith('projects.before-workspace'));
  expect(backups()).toHaveLength(1);expect(fs.readFileSync(path.join(store.dataDir,backups()[0]),'utf8')).toBe(bytes);
  store.read();expect(backups()).toHaveLength(1);
});
it('persists both modes, open/rename/archive/restore and immutable membership across restart',()=>{
  const store=fixture();const economic=store.create({name:'经济研究',mode:'economic',description:'产业观察'});
  const finance=store.create({name:'金融研究',mode:'finance',description:'策略研究'});
  expect(economic.id).not.toBe(finance.id);expect(fs.existsSync(path.join(economic.path,'project.md'))).toBe(true);
  store.open(economic.id);store.update(finance.id,{name:'金融研究二',status:'archived'});
  expect(()=>store.context(finance.id,'session-1',finance.path)).toThrow('Archived');
  const restarted=new WorkspaceStore(store.dataDir,store.managedRoot);
  expect(restarted.get(economic.id).last_opened_at).toBeTypeOf('number');expect(restarted.get(finance.id).name).toBe('金融研究二');
  restarted.update(finance.id,{status:'active'});expect(restarted.get(finance.id).mode).toBe('finance');
  expect(()=>store.update(economic.id,{mode:'finance'})).toThrow('immutable');
  expect(()=>store.create({name:'Duplicate',mode:'finance',description:''},economic.path)).toThrow('already belongs');
});
it('rejects unregistered IDs, traversal, cross-project cwd and session reuse',()=>{
  const store=fixture();const a=store.create({name:'A',mode:'economic',description:''});const b=store.create({name:'B',mode:'finance',description:''});
  expect(()=>store.get('../outside')).toThrow();expect(()=>store.context('unknown','session-1',a.path)).toThrow();
  expect(()=>store.context(a.id,'session-1',b.path)).toThrow('do not match');
  const folder=path.join(store.dataDir,'sessions',a.id);fs.mkdirSync(folder,{recursive:true});fs.writeFileSync(path.join(folder,'session-1.json'),'{}');
  expect(()=>store.assertSessionOwner(b.id,'session-1')).toThrow('another project');
  expect(store.context(a.id,'session-2',a.path)).toEqual({mode:'economic',project_id:a.id,session_id:'session-2'});
  const redirected=fixture();const linkedProject=redirected.create({name:'Linked check',mode:'economic',description:''});
  fs.symlinkSync(redirected.managedRoot,path.join(redirected.dataDir,'sessions'),process.platform==='win32'?'junction':'dir');
  expect(()=>redirected.assertSessionOwner(linkedProject.id,'session-link')).toThrow('links');
});
it('preserves malformed registry instead of replacing it with an empty list',()=>{
  const store=fixture();fs.writeFileSync(store.file,'{broken');expect(()=>store.read()).toThrow();expect(fs.readFileSync(store.file,'utf8')).toBe('{broken');
});
it('rolls an unchanged real migration back byte-for-byte and preserves project files',()=>{
  const store=fixture();const project=path.join(store.managedRoot,'legacy');fs.mkdirSync(project,{recursive:true});
  const original=JSON.stringify([{id:'legacy-project',name:'Legacy',createdAt:1,path:project}]);fs.writeFileSync(store.file,original);store.read();
  const manifest=fs.readdirSync(store.dataDir).find(n=>n.startsWith('projects.migration-v1-'))!;
  const args=['-X','utf8','-B',path.resolve('scripts/rollback-workspace-migration.py'),'--data-dir',store.dataDir,'--manifest',manifest];
  expect(spawnSync('D:/AKSHARE/envs/nomisma/python.exe',args,{encoding:'utf8'}).status).toBe(0);
  expect(JSON.parse(fs.readFileSync(store.file,'utf8'))[0].mode).toBe('economic');
  const applied=spawnSync('D:/AKSHARE/envs/nomisma/python.exe',[...args,'--apply'],{encoding:'utf8'});expect(applied.status,applied.stderr).toBe(0);
  expect(fs.readFileSync(store.file,'utf8')).toBe(original);expect(fs.existsSync(project)).toBe(true);
},15000);
it('refuses rollback after later project work changed the registry',()=>{
  const store=fixture();const project=path.join(store.managedRoot,'legacy');fs.mkdirSync(project,{recursive:true});
  fs.writeFileSync(store.file,JSON.stringify([{id:'legacy-project',name:'Legacy',createdAt:1,path:project}]));store.read();
  const manifest=fs.readdirSync(store.dataDir).find(n=>n.startsWith('projects.migration-v1-'))!;store.update('legacy-project',{name:'New work'});
  const result=spawnSync('D:/AKSHARE/envs/nomisma/python.exe',['-X','utf8','-B',path.resolve('scripts/rollback-workspace-migration.py'),'--data-dir',store.dataDir,'--manifest',manifest,'--apply'],{encoding:'utf8'});
  expect(result.status).not.toBe(0);expect(store.get('legacy-project').name).toBe('New work');
},15000);
