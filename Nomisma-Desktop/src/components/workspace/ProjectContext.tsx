import { createContext, useContext } from 'react';
import type { ProjectContext, WorkspaceProject } from '@shared/types/workspace';
export interface ActiveProjectContext {project:WorkspaceProject|null;session:ProjectContext|null}
export const ResearchProjectContext=createContext<ActiveProjectContext>({project:null,session:null});
export const useResearchProject=()=>useContext(ResearchProjectContext);
