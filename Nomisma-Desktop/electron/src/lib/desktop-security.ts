import {app,ipcMain,dialog,shell,type BrowserWindow,type IpcMainEvent,type IpcMainInvokeEvent,type Session} from 'electron';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {privacySettings} from './privacy-policy';
import {allowsResearchChannel} from './security-gateway';
import {recordSecurityEvent} from './security-audit';

export function externalLink(value:string):boolean {
 try{const url=new URL(value);return value.length<=2048&&['https:','http:'].includes(url.protocol)&&!url.username&&!url.password&&![...url.searchParams.keys()].some(k=>/api.?key|token|secret|authorization|password/i.test(k));}catch{return false;}
}
export async function openExternalReviewed(window:BrowserWindow|null,url:string):Promise<boolean>{
 if(!window||privacySettings().mode==='local_only'||!externalLink(url))return false;
 const confirmation=await dialog.showMessageBox(window,{type:'question',title:'访问外部网站',message:'将在系统浏览器中访问以下地址。不会附加本地文件。',detail:url,buttons:['取消','打开网站'],defaultId:0,cancelId:0,noLink:true});
 if(confirmation.response!==1||privacySettings().mode==='local_only')return false;await shell.openExternal(url);return true;
}
export function trustedFrame(event:IpcMainEvent|IpcMainInvokeEvent,window:BrowserWindow|null,entry=path.resolve(__dirname,'../../dist/index.html')):boolean {
 if(!window||event.sender!==window.webContents||event.senderFrame!==window.webContents.mainFrame)return false;
 const url=event.senderFrame?.url??'';
 if(!url||url!==window.webContents.getURL())return false;
 if(url.startsWith('file:')){try{return path.resolve(fileURLToPath(new URL(url.split('#')[0])))===entry;}catch{return false;}}
 if(app.isPackaged)return false;
 try{const parsed=new URL(url);return parsed.origin===`http://localhost:${process.env.NOMISMA_DEV_SERVER_PORT??'5173'}`&&parsed.pathname==='/';}catch{return false;}
}
/** One boundary covers every current/future registered handler, including send. */
export function installIpcBoundary(getWindow:()=>BrowserWindow|null,entry=path.resolve(__dirname,'../../dist/index.html')):void {
 const handle=ipcMain.handle.bind(ipcMain),on=ipcMain.on.bind(ipcMain);
 const blocked=/^(files:(?:read-multiple|calculate-deep-size)$|jira:|mcp:|claude:|codex:|terminal:(?!list$)|git:|agents:(?:save|delete|check-binaries)|acp:trace)/;
 const permits=(channel:string)=>process.env.NOMISMA_DESKTOP_MODE==='isolated'?allowsResearchChannel(channel):!blocked.test(channel);
 ipcMain.handle=(channel,listener)=>handle(channel,(event,...args)=>{
  if(!trustedFrame(event,getWindow(),entry)||!permits(channel)){
   recordSecurityEvent('ipc','capability','denied');
   throw new Error('IPC request denied');
  }
  return listener(event,...args);
 });
 ipcMain.on=(channel,listener)=>on(channel,(event,...args)=>{if(trustedFrame(event,getWindow(),entry)&&permits(channel))listener(event,...args);});
}
export function rendererDestination(url:string,origin:string,root=path.resolve(__dirname,'../../dist')):boolean {
 try{const parsed=new URL(url);if(parsed.protocol==='file:'){const relative=path.relative(root,fileURLToPath(parsed));return !!relative&&!relative.startsWith('..')&&!path.isAbsolute(relative);}return ['data:','blob:'].includes(parsed.protocol)||(!app.isPackaged&&(parsed.protocol==='devtools:'||parsed.origin===origin||(parsed.protocol==='ws:'&&parsed.host===new URL(origin).host)));}catch{return false;}
}
export function secureRendererSession(session:Session,origin:string,root=path.resolve(__dirname,'../../dist')):void {
 session.webRequest.onBeforeRequest((details,done)=>done({cancel:!rendererDestination(details.url,origin,root)}));
 const connect=app.isPackaged?"'self'":`'self' ${origin} ${origin.replace('http:','ws:')}`;
 const csp=`default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src ${connect}; media-src 'self' blob:; worker-src 'self' blob:; frame-src data:; object-src 'none'; base-uri 'none'; form-action 'none'`;
 session.webRequest.onHeadersReceived((details,done)=>done({responseHeaders:{...details.responseHeaders,'Content-Security-Policy':[csp]}}));
}
