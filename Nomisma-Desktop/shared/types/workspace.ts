export type ResearchMode = 'economic' | 'finance' | 'big_data_explore';
export type ProjectStatus = 'active' | 'archived';
export interface WorkspaceProject {
  id: string; project_id: string; name: string; path: string;
  mode: ResearchMode; description: string; status: ProjectStatus;
  createdAt: number; created_at: number; updated_at: number; last_opened_at: number | null;
  spaceId?: string; icon?: string; iconType?: 'emoji' | 'lucide';
}
export interface ProjectContext { mode: ResearchMode; project_id: string; session_id: string }
export interface HistoryEntry {
  history_id: string; project_id: string; mode: ResearchMode; session_id: string;
  title: string; activity_type: 'conversation'; status: 'running' | 'completed' | 'cancelled' | 'interrupted' | 'error' | 'draft' | 'recorded';
  created_at: number; updated_at: number; summary: string;
}
export interface WorkspaceAPI {
  create(input:{name:string;mode:ResearchMode;description:string;location:'new'|'existing'}):Promise<WorkspaceProject|null>;
  update(id:string,patch:{name?:string;description?:string;status?:ProjectStatus}):Promise<WorkspaceProject>;
  open(id:string):Promise<WorkspaceProject>;
  history(mode:ResearchMode,projectId?:string):Promise<HistoryEntry[]>;
}
declare global { interface Window { workspace: WorkspaceAPI } }
