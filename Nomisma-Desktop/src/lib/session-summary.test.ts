import { expect, it } from 'vitest';
import { extractSessionMeta, type SessionMeta } from '@shared/lib/session-persistence';
import type { ChatSession } from '@/types';
import { mergeSessionSummaries } from './session-summary';

const projects = new Set(['project_economic', 'project_finance']);
const stored = {
  id:'session_a',projectId:'project_economic',title:'Existing research',createdAt:10,
  engine:'acp' as const,agentId:'nomisma-dsh-acp',agentSessionId:'provider-original',totalCost:0,
  mode:'economic' as const,project_id:'project_economic',session_id:'session_a',
  project_context:{mode:'economic' as const,project_id:'project_economic',session_id:'session_a'},
};
const meta = ():SessionMeta => extractSessionMeta(stored,20);

it('restarts from persisted metadata with the actual ACP agent, provider ID and project context',()=>{
  const [session] = mergeSessionSummaries([], [meta()], projects);
  expect(session).toMatchObject(stored);
  expect(session.isActive).toBe(false);
});

it('keeps a fully restored legacy ACP identity when opening the project triggers a second index refresh',()=>{
  const legacy:SessionMeta = {id:stored.id,projectId:stored.projectId,title:stored.title,createdAt:10,lastMessageAt:20,engine:'acp'};
  const [cold] = mergeSessionSummaries([], [legacy], projects);
  // applyLoadedSession obtains these fields from the existing full JSON.
  const hydrated:ChatSession = {...cold,...stored,isActive:true};
  const [refreshed] = mergeSessionSummaries([hydrated],[legacy],projects);
  expect(refreshed.agentId).toBe('nomisma-dsh-acp');
  expect(refreshed.agentSessionId).toBe('provider-original');
  expect(refreshed.project_context).toEqual(stored.project_context);
  expect(refreshed.isActive).toBe(true);
});

it('preserves processing and permission UI state across repeated metadata refreshes',()=>{
  let sessions:ChatSession[]=[{...stored,isActive:true,isProcessing:true,hasPendingPermission:true}];
  for(let i=0;i<3;i++)sessions=mergeSessionSummaries(sessions,[meta()],projects);
  expect(sessions[0]).toMatchObject({agentId:'nomisma-dsh-acp',isActive:true,isProcessing:true,hasPendingPermission:true});
});

it('keeps not-yet-saved live sessions but cannot retain a session under an unregistered project',()=>{
  const session:ChatSession={...stored,isActive:false};
  expect(mergeSessionSummaries([session],[],projects,new Set([session.id]))).toEqual([session]);
  expect(mergeSessionSummaries([session],[],new Set(),new Set([session.id]))).toEqual([]);
});

it('rejects duplicate or cross-project session identity rather than mixing transcripts',()=>{
  expect(()=>mergeSessionSummaries([], [meta(),meta()],projects)).toThrow('ownership');
  expect(()=>mergeSessionSummaries([{...stored,isActive:true}], [{...meta(),projectId:'project_finance'}],projects)).toThrow('another project');
});
