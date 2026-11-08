import {researchEnvironment} from "../lib/privacy-policy";
import {dialog,ipcMain} from 'electron';
import {execFile} from 'child_process';
import path from 'path';
import crypto from 'crypto';
import {workspaceStore,nomismaProjectRoot} from '../lib/project-context';
import {nomismaPython,nomismaPythonCwd} from '../lib/nomisma-runtime';
import {researchCall} from '../lib/research-task-service';
import {chartSvg} from '../../../shared/lib/research-chart';
import type {OpenChart} from '../../../shared/types/infrastructure';
import {renderChartPng} from '../lib/chart-export';
const EXPLORE=new Set(['explore.probe','explore.import','explore.query','explore.quality','explore.export']);
const WRITES=new Set(['artifact.register','experiment.capture','chart.create','chart.create_from_file','chart.save','chart.export','experiment.replay.start',...EXPLORE]);
const READS=new Set(['catalog','datasets','tasks','experiments','experiment.open','experiment.check','experiment.compare','experiment.replay','artifacts','artifact.inspect','chart.open','chart.from_file','sheet.page','trace','audit']);
export function sharedCall(projectId:string,sessionId:string,action:string,payload:Record<string,unknown>,authorization='read_only'):Promise<unknown>{
  const store=workspaceStore(),project=store.get(projectId),context=store.context(projectId,sessionId,project.path,false);
  const state=process.env.NOMISMA_STATE_ROOT;
  if(!state||path.basename(state).toLowerCase()!=='nomisma'||path.resolve(store.dataDir).toLowerCase()!==path.resolve(state,'desktop-dev/openacpui-data').toLowerCase())return Promise.reject(new Error('Shared service requires the registered independent state'));
  const raw=JSON.stringify({action,payload,authorization});if(Buffer.byteLength(raw)>18_000_000)return Promise.reject(new Error('Shared request exceeds budget'));
  return new Promise((resolve,reject)=>{
    const child=execFile(nomismaPython(),['-B',path.join(nomismaProjectRoot(),'nomisma_tools/shared_service.py')],
      {cwd:nomismaPythonCwd(),windowsHide:true,timeout:EXPLORE.has(action)?105000:45000,maxBuffer:16_000_000,encoding:'utf8',env:{...researchEnvironment(process.env),PYTHONUTF8:'1',NOMISMA_STATE_ROOT:state,
        NOMISMA_PROJECT_ID:context.project_id,NOMISMA_MODE:context.mode,NOMISMA_SESSION_ID:context.session_id,NOMISMA_WORKSPACE:project.path}},
      (error,stdout)=>{try{if(error)throw new Error('Shared operation did not finish; reopen saved state before retrying');const r=JSON.parse(stdout);if(!r.ok)throw new Error(`${r.error.code}: ${r.error.message}`);resolve(r.data);}catch(e){reject(e);}});
    child.stdin?.on('error',()=>{});child.stdin?.end(raw);
  });
}
export function registerInfrastructure():void{
  ipcMain.handle('infrastructure:request',async(_event,projectId:string,action:string,payload:Record<string,unknown>={},requestedSession?:string)=>{
    if(!READS.has(action)&&!WRITES.has(action))throw new Error('Shared operation is not exposed to the renderer');
    const store=workspaceStore(),project=store.get(projectId);const sessionId=requestedSession??'viewer_'+crypto.createHash('sha256').update(projectId).digest('hex').slice(0,16);
    if(EXPLORE.has(action)&&project.mode!=='big_data_explore')throw new Error('Explore 工作台操作只适用于 Big Data Explore 项目');
    store.assertSessionOwner(projectId,sessionId);store.context(projectId,sessionId,project.path,false);
    if(!payload||typeof payload!=='object'||Array.isArray(payload)||Object.keys(payload).some(k=>['authorization','content_base64','root','environment','code','command'].includes(k)))throw new Error('Untrusted shared parameters');
    if(WRITES.has(action)){
      if(project.status!=='active')throw new Error('Archived projects are read-only');
      const choice=await dialog.showMessageBox({type:'question',title:'NOMISMA',message:EXPLORE.has(action)?'确认执行本地数据操作？':action==='experiment.replay.start'?'从历史实验创建新研究方案？':'确认保存新的研究记录或图表成果？',
        detail:`项目：${project.name}\n操作：${action}\n实际参数：${JSON.stringify(payload,null,2)}\n原始数据与计算结果不会被覆盖。复现计算仍需方案确认和逐次工具审批。`,buttons:['取消','确认'],defaultId:0,cancelId:0});
      if(choice.response!==1){await sharedCall(projectId,sessionId,'audit.decision',{operation:action,parameters:payload,decision:'denied'});return null;}
    }
    if(action==='experiment.replay.start'){
      if(!requestedSession)throw new Error('Open the original project conversation before proposing a reproduction');
      const replay=await sharedCall(projectId,sessionId,'experiment.replay',payload) as {plan:unknown};
      const context=store.context(projectId,sessionId,project.path);
      return researchCall(context,'create',{plan:replay.plan,parent_experiment_id:payload.experiment_id});
    }
    if(action==='chart.export'){
      if(Object.keys(payload).some(k=>!['chart_id','version','format'].includes(k))||!['png','svg'].includes(String(payload.format)))throw new Error('Invalid chart export parameters');
      const opened=await sharedCall(projectId,sessionId,'chart.open',{chart_id:payload.chart_id,version:payload.version}) as OpenChart;
      const svg=chartSvg(opened.data,opened.chart.chart_config),cfg=opened.chart.chart_config;
      const content_base64=payload.format==='svg'?Buffer.from(svg).toString('base64'):await renderChartPng(svg,cfg.width,cfg.height);
      return sharedCall(projectId,sessionId,'chart.publish_export',{chart_id:opened.chart.chart_id,version:opened.chart.version,format:payload.format,content_base64},'user_confirmed');
    }
    return sharedCall(projectId,sessionId,action,payload,WRITES.has(action)?'user_confirmed':'read_only');
  });
}
