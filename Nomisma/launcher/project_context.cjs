const fs = require('node:fs');
const path = require('node:path');
const same=(a,b)=>process.platform==='win32'?path.resolve(a).toLowerCase()===path.resolve(b).toLowerCase():path.resolve(a)===path.resolve(b);

function isRedirected(file, entry = fs.lstatSync(file)) {
  return entry.isSymbolicLink() || !same(file, fs.realpathSync.native(file));
}

function contextWorkspace(env, legacy) {
  const keys=['NOMISMA_PROJECT_ID','NOMISMA_MODE','NOMISMA_SESSION_ID'];
  if (!keys.some(key=>env[key])) return legacy;
  const valid=value=>typeof value==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/.test(value);
  if(!valid(env.NOMISMA_PROJECT_ID)||!valid(env.NOMISMA_SESSION_ID)||!['economic','finance','big_data_explore'].includes(env.NOMISMA_MODE))throw new Error('Invalid Nomisma project context');
  const state=env.NOMISMA_STATE_ROOT;
  if(!state||!path.isAbsolute(state)||path.basename(state).toLowerCase()!=='nomisma')throw new Error('Independent state root is required');
  const registry=path.join(state,'desktop-dev/openacpui-data/projects.json');
  if(isRedirected(registry))throw new Error('Project registry cannot be redirected');
  const projects=JSON.parse(fs.readFileSync(registry,'utf8').replace(/^\uFEFF/,''));
  const entries=projects.filter(project=>project.id===env.NOMISMA_PROJECT_ID);
  if(entries.length!==1)throw new Error('Project is not registered');
  const project=entries[0];const mode=project.mode??(project.id==='nomisma-hs300'?'finance':'economic');
  if(mode!==env.NOMISMA_MODE||(project.status??'active')!=='active'||!env.NOMISMA_WORKSPACE||!same(project.path,env.NOMISMA_WORKSPACE))throw new Error('Project context does not match its registration');
  const entry=fs.lstatSync(project.path);
  if(!entry.isDirectory()||isRedirected(project.path,entry))throw new Error('Project root is unavailable or redirected');
  return path.resolve(project.path);
}
module.exports={contextWorkspace,isRedirected};
