/** Renderer capabilities in the Nomisma research profile. New handlers opt in. */
const researchChannels = new Set([
  'app:getGlassSupported', 'app:set-min-width', 'clipboard:write-text',
  'workspace:create', 'workspace:update', 'workspace:open', 'workspace:history',
  'projects:list', 'projects:create', 'projects:create-dev', 'projects:delete',
  'projects:rename', 'projects:reorder', 'projects:update-icon', 'projects:update-space',
  'spaces:list', 'spaces:save',
  'sessions:list', 'sessions:load', 'sessions:save', 'sessions:delete', 'sessions:search',
  'research:context', 'research:list', 'research:read', 'research:workbook', 'research:save',
  'research-tasks:planning', 'research-tasks:request', 'infrastructure:request',
  'files:list', 'files:list-all', 'files:watch', 'files:unwatch', 'file:read',
  'shell:open-external', 'settings:get', 'settings:set', 'terminal:list',
  'agents:list', 'agents:update-cached-config',
  'acp:start', 'acp:revive-session', 'acp:prompt', 'acp:stop', 'acp:cancel',
  'acp:abort-pending-start', 'acp:reload-session', 'acp:set-config',
  'acp:get-config-options', 'acp:get-available-commands', 'acp:permission_response', 'acp:log',
  'model-providers:list', 'model-providers:test', 'model-providers:models',
  'model-providers:save', 'model-providers:default', 'model-providers:delete',
  'model-providers:dismiss', 'model-providers:reveal', 'privacy:get', 'privacy:set',
  'privacy:set-fields', 'privacy:export-diagnostics',
  'data-sources:save-credential',
  'speech:get-platform', 'speech:request-mic-permission', 'speech:start-native-dictation',
]);

export function allowsResearchChannel(channel: string): boolean {
  return researchChannels.has(channel);
}
