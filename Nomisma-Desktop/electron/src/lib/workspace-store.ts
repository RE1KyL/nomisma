import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import { isPathRedirected } from './windows-runtime.cjs';
import type { ProjectContext, ResearchMode, WorkspaceProject } from '../../../shared/types/workspace';

export function checkedId(value: unknown): string {
  if(typeof value!=='string'||!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/.test(value))throw new Error('Invalid project/session ID');
  return value;
}
export function checkedMode(value: unknown): ResearchMode {
  if(value!=='economic'&&value!=='finance'&&value!=='big_data_explore')throw new Error('Unknown research mode');
  return value;
}
const samePath=(a:string,b:string)=>process.platform==='win32'?path.resolve(a).toLowerCase()===path.resolve(b).toLowerCase():path.resolve(a)===path.resolve(b);
export function checkedDirectory(value:string):string {
  if(!path.isAbsolute(value))throw new Error('Project directory must be absolute');
  const info=fs.lstatSync(value);
  if(!info.isDirectory()||!samePath(value,fs.realpathSync.native(value)))throw new Error('Project directory cannot be redirected or linked');
  return path.resolve(value);
}
function text(value:unknown,label:string,max:number):string {
  if(typeof value!=='string'||value.includes('\0')||value.length>max)throw new Error(`Invalid ${label}`);
  return value.trim();
}
function normalized(raw:Record<string,unknown>):WorkspaceProject {
  const id=checkedId(raw.id);
  if(raw.project_id!==undefined&&raw.project_id!==id)throw new Error('Project identity mismatch');
  const name=text(raw.name,'project name',100);if(!name)throw new Error('Project name is required');
  if(typeof raw.path!=='string'||!path.isAbsolute(raw.path))throw new Error('Invalid registered project path');
  const created=Number(raw.created_at??raw.createdAt);
  if(!Number.isFinite(created)||created<0)throw new Error('Invalid project creation time');
  const updated=Number(raw.updated_at??created);
  const opened=raw.last_opened_at==null?null:Number(raw.last_opened_at);
  if(!Number.isFinite(updated)||updated<0||(opened!==null&&(!Number.isFinite(opened)||opened<0)))throw new Error('Invalid project activity time');
  const mode=raw.mode===undefined?(id==='nomisma-hs300'?'finance':'economic'):checkedMode(raw.mode);
  const status=raw.status??'active';if(status!=='active'&&status!=='archived')throw new Error('Invalid project status');
  return {...raw,id,project_id:id,name,path:raw.path,mode,status,description:text(raw.description??'','description',2000),
    createdAt:created,created_at:created,updated_at:updated,last_opened_at:opened} as WorkspaceProject;
}

/** One registry, retaining all upstream fields. No new database or session copy. */
export class WorkspaceStore {
  readonly file:string;
  constructor(readonly dataDir:string,readonly managedRoot:string){
    fs.mkdirSync(dataDir,{recursive:true});
    if (isPathRedirected(dataDir)) throw new Error('Project registry directory cannot be a link');
    this.file=path.join(dataDir,'projects.json');
  }
  private atomic(value:WorkspaceProject[]):void {
    const temp=path.join(this.dataDir,`.nomisma-write-${crypto.randomUUID()}.tmp`);
    try{fs.writeFileSync(temp,JSON.stringify(value,null,2),{encoding:'utf8',flag:'wx'});fs.renameSync(temp,this.file);}
    finally{if(path.dirname(temp)===this.dataDir&&fs.existsSync(temp))fs.unlinkSync(temp);}
  }
  read():WorkspaceProject[]{
    if(!fs.existsSync(this.file))return [];
    if(isPathRedirected(this.file))throw new Error('Project registry cannot be a link');
    const bytes=fs.readFileSync(this.file);const raw:unknown=JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/,''));
    if(!Array.isArray(raw)||raw.length>10000)throw new Error('Invalid project registry; original file preserved');
    const projects=raw.map(item=>normalized(item));const ids=new Set(projects.map(p=>p.id));
    if(ids.size!==projects.length)throw new Error('Duplicate project identity');
    if(JSON.stringify(raw)!==JSON.stringify(projects)){
      const nonce=crypto.randomUUID();const backup=`projects.before-workspace-v1-${nonce}.json`;
      fs.writeFileSync(path.join(this.dataDir,backup),bytes,{flag:'wx'});
      fs.writeFileSync(path.join(this.dataDir,`projects.migration-v1-${nonce}.json`),JSON.stringify({version:1,backup,
        after_sha256:crypto.createHash('sha256').update(JSON.stringify(projects,null,2)).digest('hex'),
        mapping:projects.map(p=>({project_id:p.id,mode:p.mode})),created_at:Date.now()},null,2),{flag:'wx'});
      this.atomic(projects);
    }
    return projects;
  }
  get(id:string,active=false):WorkspaceProject {
    checkedId(id);const project=this.read().find(p=>p.id===id);
    if(!project)throw new Error('Unknown project: not registered');
    if(active&&project.status!=='active')throw new Error('Archived projects cannot start new work');
    return project;
  }
  context(id:string,sessionId:string,cwd?:string,active=true):ProjectContext {
    const project=this.get(id,active);checkedId(sessionId);const root=checkedDirectory(project.path);
    if(cwd!==undefined&&!samePath(root,cwd))throw new Error('Project ID and workspace do not match');
    return {mode:project.mode,project_id:project.id,session_id:sessionId};
  }
  create(input:{name:string;mode:ResearchMode;description:string},existingPath?:string):WorkspaceProject {
    const name=text(input.name,'project name',100);if(!name||/[\r\n]/.test(name))throw new Error('A single-line project name is required');
    const mode=checkedMode(input.mode);const description=text(input.description,'description',2000);const projects=this.read();
    const id=`project_${crypto.randomUUID()}`;
    let directory:string;
    if(existingPath){directory=checkedDirectory(existingPath);}
    else{
      fs.mkdirSync(this.managedRoot,{recursive:true});checkedDirectory(this.managedRoot);
      directory=path.join(this.managedRoot,id);fs.mkdirSync(directory);
      for(const folder of ['datasets/raw','datasets/processed','scripts','charts','reports'])fs.mkdirSync(path.join(directory,folder),{recursive:true});
      fs.writeFileSync(path.join(directory,'project.md'),`# ${name}\n\n${description}\n\nProject: ${id}\nMode: ${mode}\n`,{flag:'wx'});
    }
    if(projects.some(p=>samePath(p.path,directory)))throw new Error('This directory already belongs to a registered project; its mode cannot be changed');
    const now=Date.now();const project:WorkspaceProject={id,project_id:id,name,path:directory,mode,description,status:'active',createdAt:now,created_at:now,updated_at:now,last_opened_at:null};
    this.atomic([...projects,project]);return project;
  }
  update(id:string,patch:Record<string,unknown>):WorkspaceProject {
    const project=this.get(id);
    if(Object.keys(patch).some(key=>!['name','description','status'].includes(key)))throw new Error('Project mode and identity are immutable');
    const next={...project,updated_at:Date.now()};
    if(patch.name!==undefined){next.name=text(patch.name,'project name',100);if(!next.name||/[\r\n]/.test(next.name))throw new Error('Project name is required');}
    if(patch.description!==undefined)next.description=text(patch.description,'description',2000);
    if(patch.status!==undefined){if(patch.status!=='active'&&patch.status!=='archived')throw new Error('Invalid project status');next.status=patch.status;}
    this.atomic(this.read().map(p=>p.id===id?next:p));return next;
  }
  replacePresentation(items:WorkspaceProject[]):void {
    const current=this.read();
    if(items.length!==current.length||new Set(items.map(p=>p.id)).size!==current.length)throw new Error('Use project lifecycle operations');
    for(const item of items){const prior=current.find(p=>p.id===item.id);if(!prior||prior.mode!==item.mode||!samePath(prior.path,item.path)||prior.status!==item.status)throw new Error('Project ownership cannot change');}
    this.atomic(items.map(item=>normalized(item as unknown as Record<string,unknown>)));
  }
  open(id:string):WorkspaceProject {
    const project=this.get(id);checkedDirectory(project.path);
    const next={...project,last_opened_at:Date.now()};this.atomic(this.read().map(p=>p.id===id?next:p));return next;
  }
  assertSessionOwner(projectId:string,sessionId:string):void {
    this.get(projectId);checkedId(sessionId);
    const base=path.join(this.dataDir,'sessions');if(!fs.existsSync(base))return;
    if(isPathRedirected(base))throw new Error('Session directory links are not allowed');
    for(const folder of fs.readdirSync(base,{withFileTypes:true})){
      const linked=folder.isSymbolicLink() || (folder.isDirectory() && isPathRedirected(path.join(base,folder.name)));
      if(folder.name===projectId&&linked)throw new Error('Session directory links are not allowed');
      if(!folder.isDirectory()||linked)continue;
      if(folder.name!==projectId&&fs.existsSync(path.join(base,folder.name,`${sessionId}.json`)))throw new Error('Session belongs to another project');
    }
  }
}
