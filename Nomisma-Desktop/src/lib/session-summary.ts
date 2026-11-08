import type { SessionMeta } from '@shared/lib/session-persistence';
import type { ChatSession } from '@/types';

/** Refresh the index without discarding identity hydrated from a full transcript. */
export function mergeSessionSummaries(
  current: ChatSession[], summaries: SessionMeta[], projectIds: ReadonlySet<string>,
  liveIds: ReadonlySet<string> = new Set(),
): ChatSession[] {
  const previous = new Map(current.map(session => [session.id, session]));
  const seen = new Set<string>();
  const merged = summaries.map(summary => {
    if (!projectIds.has(summary.projectId) || seen.has(summary.id)) {
      throw new Error('Invalid or duplicate work-session ownership in index');
    }
    seen.add(summary.id);
    const prior = previous.get(summary.id);
    if (prior && prior.projectId !== summary.projectId) {
      throw new Error('Work-session index belongs to another project');
    }
    // Legacy sidecars omit agentId/provider identity. Undefined index fields
    // must never erase the identities recovered by applyLoadedSession.
    const defined = Object.fromEntries(Object.entries(summary).filter(([,value]) => value !== undefined && value !== null));
    return {
      ...prior, ...defined,
      id: summary.id, projectId: summary.projectId, title: summary.title,
      createdAt: summary.createdAt, lastMessageAt: summary.lastMessageAt || summary.createdAt,
      totalCost: summary.totalCost ?? prior?.totalCost ?? 0,
      isActive: prior?.isActive ?? false,
      isProcessing: prior?.isProcessing,
      hasPendingPermission: prior?.hasPendingPermission,
    } as ChatSession;
  });
  // A live/materializing session may not have reached its first disk save yet.
  return [...merged, ...current.filter(session => !seen.has(session.id) && projectIds.has(session.projectId)
    && (session.isActive || session.isProcessing || liveIds.has(session.id)))];
}
