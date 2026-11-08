import { renderToStaticMarkup } from 'react-dom/server';
import type { EffectCallback, ReactNode } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { WorkspaceProject } from '@shared/types/workspace';
import type { WorkspaceRoute } from '@/lib/workspace-routes';
const state=vi.hoisted(()=>({route:{page:'overview'} as WorkspaceRoute,projects:[] as WorkspaceProject[],effects:[] as EffectCallback[],newChat:vi.fn(),switchSession:vi.fn(),orchestrator:vi.fn()}));
vi.mock('react',async original=>({...await original<typeof import('react')>(),useEffect:(effect:EffectCallback)=>state.effects.push(effect)}));
vi.mock('@/hooks/useAppOrchestrator',()=>({useAppOrchestrator:(options:unknown)=>{state.orchestrator(options);return {projectManager:{projects:state.projects,loading:false,error:'',refresh:vi.fn().mockResolvedValue([])},manager:{sessions:[],activeSessionId:null,isDraft:false,activeSession:null,switchSession:state.switchSession},agents:[],handleNewChat:state.newChat};}}));
vi.mock('@/hooks/useWorkspaceNavigation',()=>({useWorkspaceNavigation:()=>({route:state.route,navigate:vi.fn(),back:vi.fn(),forward:vi.fn(),canForward:false})}));
vi.mock('../AppLayout',()=>({AppLayout:()=> <div data-test="existing-agent-layout"/>}));
vi.mock('../ResearchWorkspace',()=>({ResearchWorkspace:({children,idleContent}:{children:ReactNode;idleContent:ReactNode})=><div>{idleContent}{children}</div>}));
vi.mock('../WorkspaceDivider',()=>({WorkspaceDivider:()=>null}));
vi.mock('./ProjectForm',()=>({ProjectForm:()=>null}));
import { WorkspaceApp } from './WorkspaceApp';
const project=(mode:'economic'|'finance'|'big_data_explore'):WorkspaceProject=>({id:`project_${mode}`,project_id:`project_${mode}`,name:`${mode} study`,path:`C:/tests/${mode}`,mode,description:'Actual registry fixture',status:'active',createdAt:1,created_at:1,updated_at:1,last_opened_at:null});
beforeEach(()=>{state.route={page:'overview'};state.projects=[];state.effects=[];vi.clearAllMocks();vi.stubGlobal('localStorage',{getItem:()=>null,setItem:vi.fn()});vi.stubGlobal('window',{addEventListener:vi.fn(),removeEventListener:vi.fn()});});
it('renders Overview and both modes on initial mount without creating or selecting any conversation',()=>{
  const html=renderToStaticMarkup(<WorkspaceApp/>);state.effects.forEach(effect=>effect());
  expect(html).toContain('Project Overview');expect(html).toContain('Recent Projects');expect(html).toContain('Economic Projects');expect(html).toContain('Finance Projects');expect(html).toContain('Settings');
  expect(html).toContain('还没有 Economic 项目');expect(html).toContain('还没有 Finance 项目');
  expect(state.newChat).not.toHaveBeenCalled();expect(state.switchSession).not.toHaveBeenCalled();expect(state.orchestrator).toHaveBeenCalledWith({projectNavigation:true});
});
it('filters the actual project cards by route mode while keeping both navigation groups visible',()=>{
  state.projects=[project('economic'),project('finance')];state.route={page:'projects',mode:'finance'};
  const html=renderToStaticMarkup(<WorkspaceApp/>);expect(html).toContain('finance study');expect(html).not.toContain('economic study');expect(html).toContain('Research · Modeling · Exploration');expect(html).toContain('Scripting · Quant Research · Backtesting');
});
it('shows a project summary without auto-creating a working session',()=>{
  state.projects=[project('economic')];state.route={page:'project',mode:'economic',projectId:'project_economic'};
  const html=renderToStaticMarkup(<WorkspaceApp/>);expect(html).toContain('economic study');expect(html).toContain('新建工作会话');expect(state.newChat).not.toHaveBeenCalled();
});

it('renders Explore projects within the existing workspace and keeps the other modes isolated',()=>{
  state.projects=[project('economic'),project('finance'),project('big_data_explore')];state.route={page:'projects',mode:'big_data_explore'};
  const html=renderToStaticMarkup(<WorkspaceApp/>);expect(html).toContain('Big Data Explore');expect(html).toContain('big_data_explore study');expect(html).not.toContain('economic study');expect(html).not.toContain('finance study');expect(state.newChat).not.toHaveBeenCalled();
});
