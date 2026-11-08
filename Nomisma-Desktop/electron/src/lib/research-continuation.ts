/** Bounded continuation through the existing ACP connection, not another runtime. */
export interface ResearchTurnContext {
  managed?:boolean;status?:string;tasks?:unknown[];ready_tasks?:unknown[];
}
export async function continueResearchTurn<T extends {stopReason:string}>(options:{
  result:T;before:ResearchTurnContext|undefined;refresh:()=>Promise<ResearchTurnContext>;
  next:()=>Promise<T>;stopped:()=>boolean;
}):Promise<T>{
  let result=options.result,before=options.before;
  // Each next() also consumes the persistent ten-prompt budget in the coordinator.
  for(let i=0;i<9&&!options.stopped()&&result.stopReason==='end_turn'&&before?.managed;i++){
    const after=await options.refresh();
    if(options.stopped()||after.status!=='active'||!after.ready_tasks?.length||JSON.stringify(after.tasks)===JSON.stringify(before.tasks))break;
    before=after;result=await options.next();
  }
  return result;
}
