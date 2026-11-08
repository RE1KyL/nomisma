import {app} from 'electron';
import fs from 'node:fs';import path from 'node:path';import {execFile} from 'node:child_process';
import {installedNomisma, nomismaProjectRoot, nomismaPython, nomismaPythonCwd} from './nomisma-runtime';
const helper=()=>path.join(nomismaProjectRoot(),'launcher/credential_dpapi.py');
export const dpapiPrefix=Buffer.from('NOMISMA-DPAPI-1\0','utf8');
export function dpapiAvailable():boolean{return app.isReady()&&fs.existsSync(nomismaPython())&&fs.existsSync(helper());}
/** Fixed isolated credential helper; key bytes use pipes, never argv/env/logs. */
export function windowsDpapi(mode:'encrypt'|'decrypt',input:Buffer):Promise<Buffer>{
 return new Promise((resolve,reject)=>{
  const env:NodeJS.ProcessEnv={};for(const key of ['SYSTEMROOT','SystemRoot','WINDIR','TEMP','TMP'])if(process.env[key])env[key]=process.env[key];
  if(installedNomisma())env.PATH=process.env.PATH;
  const child=execFile(nomismaPython(),['-I','-S','-B',helper(),mode],{cwd:nomismaPythonCwd(),windowsHide:true,timeout:10000,maxBuffer:65536,encoding:'buffer',env},(error,stdout)=>{input.fill(0);if(error)reject(new Error('Windows credential operation failed'));else resolve(stdout);});
  child.stdin?.on('error',()=>{});child.stdin?.end(input);
 });
}
