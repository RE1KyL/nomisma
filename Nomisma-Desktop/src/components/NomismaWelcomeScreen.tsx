import { memo } from 'react';
import { NomismaEmptyState } from './NomismaEmptyState';
export const WelcomeScreen = memo(function WelcomeScreen({hasProjects,onCreateProject}:{hasProjects:boolean;onCreateProject:()=>void}) {
return <section style={{margin:'auto',textAlign:'center',padding:40}}><NomismaEmptyState />{!hasProjects&&<button onClick={onCreateProject}>选择研究项目</button>}</section>;
});
