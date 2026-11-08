import {researchEnvironment} from "../lib/privacy-policy";
import { dialog, ipcMain } from 'electron';
import fs from 'fs';
import path from 'path';
import { execFile } from 'child_process';
import { researchPath } from '../lib/research-path';
import type { CellChange, Workbook, ResearchFile } from '@shared/types/research';
import { nomismaProjectRoot, workspaceStore } from '../lib/project-context';
import { nomismaPython, nomismaPythonCwd } from '../lib/nomisma-runtime';
function root(projectId: string): string {
  return workspaceStore().get(projectId).path;
}
async function workbook(projectId: string, relative: string, action: string, version?: string, changes?: CellChange[]): Promise<Workbook> {
  const workspace = root(projectId); researchPath(workspace,relative);
  const projectRoot = nomismaProjectRoot();
  return new Promise<Workbook>((resolve,reject)=>{
    const child = execFile(nomismaPython(), ['-B',path.join(projectRoot,'nomisma_tools/workbook_io.py')],
      {cwd:nomismaPythonCwd(),timeout:30000,maxBuffer:8_000_000,windowsHide:true,encoding:'utf8',env:{...researchEnvironment(process.env),PYTHONUTF8:'1'} },
      (error,stdout)=>{
        try {
          if(!stdout)throw new Error(`Workbook service failed (${error?.code ?? 'empty response'}); check Nomisma Python and workbook_io.py`);
          const result: {ok:boolean;data:Workbook;error:string} = JSON.parse(stdout);
          if(!result.ok)throw new Error(result.error);
          if(error)throw new Error(`Workbook service did not complete (${error.code})`);
          resolve(result.data);
        } catch(failure) { reject(failure); }
      });
    child.stdin?.on('error',()=>{ /* execFile reports process/startup failures above. */ });
    child.stdin?.end(JSON.stringify({root:workspace,path:relative,action,version,changes}));
  });
}
export function registerResearch(): void {
  ipcMain.handle('research:context',()=>({fileTest:process.env.NOMISMA_FILE_TEST === '1'}));
  ipcMain.handle('research:list',(_event,id:string)=>{
    const workspace=root(id); researchPath(workspace,'.'); const result:ResearchFile[]=[];
    function walk(relative:string) { if(result.length>10000)return; for(const entry of fs.readdirSync(path.join(workspace,relative),{withFileTypes:true})) {
      if(entry.name.startsWith('.')||entry.isSymbolicLink())continue;
      const rel=relative?`${relative}/${entry.name}`:entry.name;
      try {researchPath(workspace,rel);} catch {continue;}
      result.push({path:rel,directory:entry.isDirectory()}); if(entry.isDirectory())walk(rel);
    }} walk(''); return result;
  });
  ipcMain.handle('research:read',async(_event,id:string,relative:string)=>{
    const file=researchPath(root(id),relative);
    if(fs.statSync(file).size>(/\.(png|jpg|jpeg|webp|svg|pdf)$/i.test(file)?12_000_000:2_000_000))throw new Error('Preview exceeds the supported file budget');
    if(/\.svg$/i.test(file)){
      const svg=fs.readFileSync(file,'utf8');
      if(/<script\b|<foreignObject\b|\bon[a-z]+\s*=|(?:xlink:)?href\s*=\s*["'](?!#)|@import|url\s*\(\s*(?!#)/i.test(svg))throw new Error('Active or external SVG content is not supported');
      return 'data:image/svg+xml;base64,'+Buffer.from(svg).toString('base64');
    }
    if(/\.pdf$/i.test(file))return 'data:application/pdf;base64,'+fs.readFileSync(file).toString('base64');
    const image=/\.(png|jpg|jpeg|webp)$/i.exec(file);
    if(image)return `data:image/${image[1].toLowerCase()==='jpg'?'jpeg':image[1].toLowerCase()};base64,${fs.readFileSync(file).toString('base64')}`;
    if(!/\.(csv|tsv|md|txt|py|json)$/i.test(file))throw new Error('Unsupported preview');
    return fs.readFileSync(file,'utf8');
  });
  ipcMain.handle('research:workbook',(_event,id:string,relative:string)=>workbook(id,relative,'open'));
  ipcMain.handle('research:save',async(_event,id:string,relative:string,version:string,changes:CellChange[])=>{
    if(workspaceStore().get(id).status!=='active')throw new Error('Archived project files are read-only');
    researchPath(root(id),relative);
    if(relative.toLowerCase().startsWith('datasets/raw/'))throw new Error('Raw datasets are read-only');
    const choice=await dialog.showMessageBox({type:'question',title:'NOMISMA',message:`覆盖保存 ${relative}？`,detail:'修改将写入实际工作簿。公式不会自动重新计算。',buttons:['取消','保存'],defaultId:0,cancelId:0});
    if(choice.response!==1)return null; return workbook(id,relative,'save',version,changes);
  });
}
