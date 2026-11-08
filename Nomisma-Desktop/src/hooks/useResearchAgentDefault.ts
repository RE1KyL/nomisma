import { useEffect } from 'react';
import type { InstalledAgent } from '@/types';

export function researchAgent(agents: InstalledAgent[]): InstalledAgent | undefined {
  return agents.find(agent => agent.id === 'nomisma-dsh-acp' && agent.engine === 'acp');
}

export function useResearchAgentDefault(agents: InstalledAgent[], selected: InstalledAgent | null,
  hasLiveSession: boolean, select: (agent: InstalledAgent | null) => void): void {
  useEffect(() => {
    if (hasLiveSession) return; // Never silently replace a restored conversation's engine.
    const preferred = researchAgent(agents);
    if (preferred && selected?.id !== preferred.id) select(preferred);
  }, [agents, selected?.id, hasLiveSession, select]);
}
