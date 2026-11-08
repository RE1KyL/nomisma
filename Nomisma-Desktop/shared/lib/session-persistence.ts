/**
 * Pure session persistence helpers shared between Electron and CLI.
 */

export interface SessionMeta {
  mode?: import('../types/workspace').ResearchMode;
  project_id?: string;
  session_id?: string;
  project_context?: import('../types/workspace').ProjectContext;
  agentId?: string;
  agentSessionId?: string;
  id: string;
  projectId: string;
  title: string;
  createdAt: number;
  /** Timestamp of the most recent user message — used for sidebar sort order */
  lastMessageAt: number;
  model?: string;
  planMode?: boolean;
  totalCost?: number;
  engine?: "claude" | "acp" | "codex";
  codexThreadId?: string;
}

/**
 * Walk messages backward to find the timestamp of the last user message.
 */
export function getLastUserMessageTimestamp(
  messages?: Array<{ role?: string; timestamp?: number }>,
): number | undefined {
  if (!Array.isArray(messages) || messages.length === 0) return undefined;
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === "user" && typeof msg.timestamp === "number") return msg.timestamp;
  }
  return undefined;
}

/**
 * Extract a SessionMeta from a raw session data object.
 */
export function extractSessionMeta(data: Record<string, unknown>, lastMessageAt: number): SessionMeta {
  return {
    ...(data.project_context?{project_context:data.project_context as SessionMeta['project_context'],mode:data.mode as SessionMeta['mode'],project_id:data.project_id as string,session_id:data.session_id as string}:{}),
    ...(data.agentId?{agentId:data.agentId as string}:{}),
    ...(data.agentSessionId?{agentSessionId:data.agentSessionId as string}:{}),
    id: data.id as string,
    projectId: data.projectId as string,
    title: (data.title as string) || "Untitled",
    createdAt: (data.createdAt as number) || 0,
    lastMessageAt,
    model: data.model as string | undefined,
    planMode: data.planMode as boolean | undefined,
    totalCost: (data.totalCost as number) || 0,
    engine: data.engine as SessionMeta["engine"],
    codexThreadId: data.codexThreadId as string | undefined,
  };
}
