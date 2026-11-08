import { expect,it } from 'vitest';
import { parseWorkspaceRoute,workspaceHash,routeFallback,type WorkspaceRoute } from './workspace-routes';
it('lands on overview without generating a session and round trips project-owned routes',()=>{
  expect(parseWorkspaceRoute('')).toEqual({page:'overview'});
  const routes:WorkspaceRoute[]=[{page:'overview'},{page:'settings'},{page:'projects',mode:'economic'},{page:'history',mode:'finance'},
    {page:'project',mode:'economic',projectId:'project_123'},{page:'project',mode:'finance',projectId:'project_456',sessionId:'stable-session'},
    {page:'project',mode:'economic',projectId:'project_123',sessionId:'__draft__'},{page:'project',mode:'finance',projectId:'project_456',section:'datasets'}];
  for(const route of routes)expect(parseWorkspaceRoute(workspaceHash(route))).toEqual(route);
});
it('rejects traversal, invalid mode and encoding and uses a project-list fallback',()=>{
  for(const hash of ['#/unknown/projects','#/economic/projects/..','#/finance/projects/%2E%2E/abc','#/finance/projects/project_1/sessions/%2E%2E','#/%ZZ'])expect(parseWorkspaceRoute(hash)).toEqual({page:'invalid'});
  expect(routeFallback({page:'project',mode:'finance',projectId:'project_a'})).toEqual({page:'projects',mode:'finance'});
});

it('round trips the independent Explore projects, history and project-owned conversation routes',()=>{
  const routes:WorkspaceRoute[]=[{page:'projects',mode:'big_data_explore'},{page:'history',mode:'big_data_explore'},{page:'project',mode:'big_data_explore',projectId:'project_explore',sessionId:'session_explore'}];
  for(const route of routes)expect(parseWorkspaceRoute(workspaceHash(route))).toEqual(route);
});
