import {beforeEach,describe,expect,it,vi} from 'vitest';
const fake=vi.hoisted(()=>({handlers:new Map<string,Function>(),calls:[] as any[],execute:vi.fn()}));
vi.mock('electron',()=>({app:{isPackaged:false},ipcMain:{handle:(name:string,fn:Function)=>fake.handlers.set(name,fn)}}));
vi.mock('../project-context',()=>({workspaceStore:()=>({get:(id:string)=>({id,path:'C:/fixture'})}),nomismaProjectRoot:()=>'/fixture',registeredContext:(id:string,_path:string,session:string)=>({project_id:id,session_id:session,mode:'finance'})}));
vi.mock('../nomisma-project-agent',()=>({projectAgentEnvironment:(id:string,_path:string,session:string)=>({context:{project_id:id,session_id:session,mode:'finance'},env:{NOMISMA_SESSION_ID:session}})}));
vi.mock('child_process',()=>({execFile:(...args:any[])=>fake.execute(...args)}));
import {researchCall,receiptFromUpdate,researchToolName,markPlanning,clearPlanning,planningPending,planningStatus,captureResearchProposal} from '../research-task-service';
import {registerResearchTasks} from '../../ipc/research-tasks';
const context={project_id:'p',session_id:'s',mode:'finance'} as any;
beforeEach(()=>{
  fake.calls.length=0;clearPlanning('s');
  fake.execute.mockImplementation((command,argv,options,callback)=>({stdin:{on:()=>{},end:(raw:string)=>{
    const request=JSON.parse(raw);fake.calls.push({command,argv,options,...request});
    queueMicrotask(()=>callback(null,JSON.stringify({ok:true,data:request.action==='context'?{managed:false}:{saved:true}})));
  }}}));
});
describe('trusted research adapter',()=>{
  it('maps only the actual Nomisma MCP prefix to the enabled tool name',()=>{
    expect(researchToolName('mcp__nomisma__nomisma_analysis_describe')).toBe('nomisma_analysis_describe');
    expect(researchToolName('nomisma_dataset_inspect')).toBe('nomisma_dataset_inspect');
    expect(researchToolName('mcp__other__nomisma_dataset_inspect')).toBeNull();
  });
  it('uses only the fixed coordinator and serializes same-project updates',async()=>{
    await Promise.all([researchCall(context,'pause'),researchCall(context,'context')]);
    expect(fake.calls.map(c=>c.action)).toEqual(['pause','context']);
    expect(fake.calls[0].command).toBe('D:/AKSHARE/envs/nomisma/python.exe');
    expect(fake.calls[0].argv[1]).toMatch(/research_control.py$/);
    expect(fake.calls[0].options.env.NOMISMA_SESSION_ID).toBe('s');
  });
  it('does not ingest plans from ordinary assistant text',async()=>{
    const text='```nomisma-plan\n{"objective":"fixture"}\n```';
    await captureResearchProposal(context,text);expect(fake.calls).toHaveLength(0);
    markPlanning('s');await captureResearchProposal(context,text);
    expect(fake.calls.map(c=>c.action)).toEqual(['context','create']);expect(planningPending('s')).toBe(false);
  });
  it('retains planning-only state while asking for necessary information',async()=>{
    markPlanning('s');await captureResearchProposal(context,'Which sample should be used?');
    expect(planningPending('s')).toBe(true);expect(fake.calls).toHaveLength(0);
  });
  it('exposes an invalid proposal error without granting execution permission',async()=>{
    markPlanning('s');await expect(captureResearchProposal(context,'```nomisma-plan\ninvalid\n```')).rejects.toThrow();
    expect(planningStatus('s').error).toContain('未通过检查');expect(planningPending('s')).toBe(true);expect(fake.calls).toHaveLength(0);
  });
  it('extracts a real structured MCP receipt but ignores unrelated prose',()=>{
    const receipt={ok:true,data:{execution_id:'a'.repeat(32)},artifacts:[],warnings:[],error:null};
    expect(receiptFromUpdate({rawOutput:{structuredContent:receipt}})).toEqual(receipt);
    expect(receiptFromUpdate({content:[{type:'content',content:{type:'text',text:JSON.stringify(receipt)}}]})).toEqual(receipt);
    expect(receiptFromUpdate({text:JSON.stringify(receipt)})).toBeNull();
    expect(receiptFromUpdate({rawOutput:'I computed 90%'})).toBeNull();
  });
  it('prevents renderer from synthesizing admissions, decisions or tool results',async()=>{
    registerResearchTasks();const request=fake.handlers.get('research-tasks:request')!;
    for(const action of ['admit','decision','result','interrupted','prompt','eval'])await expect(request(null,'p','s',action,{})).rejects.toThrow('not exposed');
    expect(fake.calls).toHaveLength(0);
    await request(null,'p','s','list',{});expect(fake.calls[0].action).toBe('list');
  });
  it('lets the user leave an unfinished planning conversation without running tools',async()=>{
    markPlanning('s');registerResearchTasks();
    const result=await fake.handlers.get('research-tasks:request')!(null,'p','s','cancel-planning',{});
    expect(result.pending).toBe(false);expect(fake.calls).toHaveLength(0);
  });
});
