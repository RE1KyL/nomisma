import {BrowserWindow} from 'electron';
import crypto from 'crypto';
/** Rasterize only our fixed renderer's escaped SVG in an isolated hidden window. */
export async function renderChartPng(svg:string,width:number,height:number):Promise<string>{
  if(!Number.isInteger(width)||!Number.isInteger(height)||width<480||width>2400||height<320||height>1600||Buffer.byteLength(svg)>12_000_000)throw new Error('Invalid chart raster budget');
  const hidden=new BrowserWindow({show:false,width,height,webPreferences:{sandbox:true,nodeIntegration:false,contextIsolation:true,partition:'chart-export-'+crypto.randomUUID()}});
  hidden.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  hidden.webContents.session.webRequest.onBeforeRequest((details,callback)=>callback({cancel:!details.url.startsWith('data:')}));
  try{
    await hidden.loadURL('data:text/html,<html><body></body></html>');
    const data='data:image/svg+xml;base64,'+Buffer.from(svg).toString('base64');
    return await hidden.webContents.executeJavaScript(`new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>{const c=document.createElement('canvas');c.width=${width};c.height=${height};c.getContext('2d').drawImage(img,0,0);resolve(c.toDataURL('image/png').split(',')[1]);};img.onerror=()=>reject(new Error('Chart image could not be rasterized'));img.src=${JSON.stringify(data)};})`) as string;
  }finally{if(!hidden.isDestroyed())hidden.destroy();}
}
