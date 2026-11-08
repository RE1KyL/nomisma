import type { ResearchMode } from '@shared/types/workspace';
export type WorkspaceRoute = {page:'overview'}|{page:'settings'}|{page:'projects';mode:ResearchMode}|{page:'history';mode:ResearchMode}|
  {page:'project';mode:ResearchMode;projectId:string;sessionId?:string;section?:'assistant'|'datasets'}|{page:'invalid'};
const id=/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/;
export function parseWorkspaceRoute(hash:string):WorkspaceRoute{
  try{
    const parts=decodeURIComponent(hash.replace(/^#\/?/,'')).split('/').filter(Boolean);
    if(parts.length===0||parts.join('/')==='overview')return {page:'overview'};
    if(parts.join('/')==='settings')return {page:'settings'};
    const mode=parts[0];if(mode!=='economic'&&mode!=='finance'&&mode!=='big_data_explore')return {page:'invalid'};
    if(parts.length===2&&(parts[1]==='projects'||parts[1]==='history'))return {page:parts[1],mode};
    if(parts[1]!=='projects'||!id.test(parts[2]??''))return {page:'invalid'};
    const base:Extract<WorkspaceRoute,{page:'project'}>={page:'project',mode,projectId:parts[2]};
    if(parts.length===3)return base;
    if(parts.length===4&&parts[3]==='new')return {...base,sessionId:'__draft__'};
    if(parts.length===4&&(parts[3]==='assistant'||parts[3]==='datasets'))return {...base,section:parts[3]};
    if(parts.length===5&&parts[3]==='sessions'&&id.test(parts[4]))return {...base,sessionId:parts[4]};
  }catch{/* Invalid encoding is rendered as a recoverable route error. */}
  return {page:'invalid'};
}
export function workspaceHash(route:WorkspaceRoute):string{
  if(route.page==='overview'||route.page==='invalid')return '#/overview';
  if(route.page==='settings')return '#/settings';
  if(route.page==='projects'||route.page==='history')return `#/${route.mode}/${route.page}`;
  const base=`#/${route.mode}/projects/${encodeURIComponent(route.projectId)}`;
  return route.sessionId?`${base}/${route.sessionId==='__draft__'?'new':`sessions/${encodeURIComponent(route.sessionId)}`}`:route.section?`${base}/${route.section}`:base;
}
export function routeFallback(route:WorkspaceRoute):WorkspaceRoute{
  return route.page==='project'||route.page==='history'?{page:'projects',mode:route.mode}:{page:'overview'};
}
