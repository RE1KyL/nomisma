export interface ResearchTask {
  task_id:string;task_name:string;objective:string;tool:string;arguments:Record<string,unknown>;
  dependencies:string[];expected_outputs:string[];validation_criteria:string[];
  status:string;reason:string|null;verified:Record<string,unknown>;
}
export interface ResearchState {
  research_id:string;project_id:string;session_id:string;mode:'economic'|'finance'|'big_data_explore';version:number;
  plan_version:number;objective:string;locked_constraints:Record<string,unknown>;status:string;
  tasks:Record<string,ResearchTask>;calls:number;prompts:number;active_seconds:number;
  delivery?:{manifest_path:string;sha256:string;files:Array<{path:string;sha256:string}>};
}
export interface ResearchTaskAPI {
  request(projectId:string,sessionId:string,action:string,payload:Record<string,unknown>):Promise<unknown>;
  planning(projectId:string,sessionId:string,objective:string):Promise<string>;
}
declare global {interface Window {researchTasks:ResearchTaskAPI}}
