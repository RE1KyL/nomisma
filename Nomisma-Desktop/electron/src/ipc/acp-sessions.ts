import { BrowserWindow, ipcMain } from "electron";
import { spawn, ChildProcess } from "child_process";
import { Readable, Writable } from "stream";
import crypto from "crypto";
import path from "path";
import {researchEnvironment,coordinationForModel} from "../lib/privacy-policy";
import { log } from "../lib/logger";
import {modelProviders} from "../lib/model-provider/service";
import {ProviderError} from "../lib/model-provider/protocol";
import { safeSend } from "../lib/safe-send";
import { getAgent } from "../lib/agent-registry";
import type { InstalledAgent } from "../lib/agent-registry";
import { getMcpAuthHeaders } from "../lib/mcp-oauth-flow";
import { extractErrorMessage, reportError } from "../lib/error-utils";
import { captureEvent } from "../lib/posthog";
import { cancelAcpPrompt } from "../lib/acp-cancel";
import { projectAgentEnvironment, projectMcpServers, assertProjectResume } from '../lib/nomisma-project-agent';
import { nomismaProjectRoot,workspaceStore } from '../lib/project-context';
import { installedNomisma, nomismaNode } from '../lib/nomisma-runtime';
import type { ProjectContext } from '../../../shared/types/workspace';
import {researchCall,hasResearch,planningPending,captureResearchProposal,receiptFromUpdate,researchToolName,clearPlanning} from '../lib/research-task-service';
import {continueResearchTurn,type ResearchTurnContext} from '../lib/research-continuation';

// ACP SDK is ESM-only, must be async-imported
import type { ClientSideConnection,RequestPermissionResponse } from "@agentclientprotocol/sdk";
let _acp: typeof import("@agentclientprotocol/sdk") | null = null;
async function getACP() {
  if (!_acp) _acp = await import("@agentclientprotocol/sdk");
  return _acp;
}

import { resolveACPFilePath, applyReadRange } from "@shared/lib/acp-helpers";
import type { ACPTextFileParams } from "@shared/lib/acp-helpers";

type ACPReadTextFileParams = ACPTextFileParams & { content?: string; line?: number | null; limit?: number | null };
type ACPWriteTextFileParams = ACPTextFileParams & { content: string };

async function acpReadTextFile(params: ACPReadTextFileParams): Promise<{ content: string; filePath: string }> {
  const filePath = resolveACPFilePath(params);
  const fs = await import("fs/promises");
  const content = await fs.readFile(filePath, "utf-8");
  return { filePath, content: applyReadRange(content, params.line, params.limit) };
}

async function acpWriteTextFile(params: ACPWriteTextFileParams): Promise<{ filePath: string }> {
  const filePath = resolveACPFilePath(params);
  const fs = await import("fs/promises");
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, params.content, "utf-8");
  return { filePath };
}

// Advertise only capabilities that are fully implemented here.
// Intentionally omit `terminal` and any custom `_meta` flags (e.g. terminal_output)
// so ACP agents use their protocol fallbacks instead of unsupported client RPC paths.
const ACP_CLIENT_CAPABILITIES = {
  fs: { readTextFile: true, writeTextFile: true },
} as const;

interface ACPSessionEntry {
  projectContext?: ProjectContext;
  promptRunning?: boolean;
  lastStopReason?:string;
  process: ChildProcess;
  connection: ClientSideConnection;
  acpSessionId: string;
  internalId: string;
  analyticsProperties: AcpAnalyticsProperties;
  eventCounter: number;
  pendingPermissions: Map<string, { resolve: (response: unknown) => void }>;
  cwd: string;
  supportsLoadSession: boolean;
  supportsResumeSession: boolean;
  isDeepSeekHarness: boolean;
  /** True while session/load is in-flight — suppresses history replay notifications from reaching the renderer */
  isReloading: boolean;
  /** ACP-side session IDs for ephemeral utility prompts (title gen, commit msg) */
  utilitySessionIds?: Set<string>;
  /** Text accumulator buffers for utility sessions, keyed by ACP sessionId */
  utilityTextBuffers?: Map<string, string>;
  /** Last actionable stderr error line observed from the ACP agent process */
  lastStderrError?: string;
  researchText?:string;
  researchStopRequested?:boolean;
}

export const acpSessions = new Map<string, ACPSessionEntry>();

// Buffer latest config options per session — survives the renderer's DRAFT→active transition
// where events arrive before useACP's listener is subscribed
const configBuffer = new Map<string, unknown[]>();

// Buffer latest available commands per session — same lifecycle as configBuffer
const commandsBuffer = new Map<string, unknown[]>();

// Track in-flight acp:start so the renderer can abort during npx download / protocol init.
// Only one start can be in-flight at a time (guarded by materializingRef in the renderer).
let pendingStartProcess: { id: string; process: ChildProcess; aborted?: boolean } | null = null;

type AcpAnalyticsProperties = {
  acp_agent: string;
  acp_agent_source: "registry" | "custom";
  acp_agent_launch_method: "npx" | "binary" | "unknown";
  acp_agent_registry_id?: string;
  acp_agent_registry_version?: string;
};

function buildAcpAnalyticsProperties(agent: InstalledAgent): AcpAnalyticsProperties {
  const registryId = agent.registryId?.trim();
  const launchMethod = agent.binary === "npx" ? "npx" : agent.binary ? "binary" : "unknown";

  if (registryId) {
    return {
      acp_agent: registryId,
      acp_agent_source: "registry",
      acp_agent_launch_method: launchMethod,
      acp_agent_registry_id: registryId,
      ...(agent.registryVersion ? { acp_agent_registry_version: agent.registryVersion } : {}),
    };
  }

  const customHash = crypto.createHash("sha256").update(agent.id).digest("hex").slice(0, 12);
  return {
    acp_agent: `custom:${customHash}`,
    acp_agent_source: "custom",
    acp_agent_launch_method: launchMethod,
  };
}

export function getAcpAnalyticsPropertiesForSession(sessionId: string): Record<string, unknown> | null {
  return acpSessions.get(sessionId)?.analyticsProperties ?? null;
}

/** One-line summary for each ACP session update (mirrors summarizeEvent for Claude) */
function summarizeUpdate(update: Record<string, unknown>): string {
  const kind = update.sessionUpdate as string;
  switch (kind) {
    case "agent_message_chunk": {
      const c = update.content as { type?: string; text?: string } | undefined;
      return `agent_message_chunk text_len=${c?.text?.length ?? 0}`;
    }
    case "agent_thought_chunk": {
      const c = update.content as { type?: string; text?: string } | undefined;
      return `agent_thought_chunk text_len=${c?.text?.length ?? 0}`;
    }
    case "user_message_chunk": {
      const c = update.content as { type?: string; text?: string } | undefined;
      return `user_message_chunk text_len=${c?.text?.length ?? 0}`;
    }
    case "tool_call": {
      const tc = update as { toolCallId?: string; title?: string; kind?: string; status?: string };
      return `tool_call id=${tc.toolCallId?.slice(0, 12)} title="${tc.title}" kind=${tc.kind ?? "?"} status=${tc.status}`;
    }
    case "tool_call_update": {
      const tcu = update as { toolCallId?: string; status?: string; rawOutput?: unknown; content?: unknown[] };
      const hasOutput = tcu.rawOutput != null;
      const contentCount = Array.isArray(tcu.content) ? tcu.content.length : 0;
      return `tool_call_update id=${tcu.toolCallId?.slice(0, 12)} status=${tcu.status ?? "?"} hasOutput=${hasOutput} content_items=${contentCount}`;
    }
    case "plan": {
      const p = update as { entries?: unknown[] };
      return `plan entries=${p.entries?.length ?? 0}`;
    }
    case "usage_update": {
      const uu = update as { size?: number; used?: number; cost?: { amount?: number; currency?: string } };
      const parts: string[] = [];
      if (uu.size != null) parts.push(`size=${uu.size}`);
      if (uu.used != null) parts.push(`used=${uu.used}`);
      if (uu.cost) parts.push(`cost=$${uu.cost.amount}`);
      return `usage_update ${parts.join(" ")}`;
    }
    case "session_info_update": {
      const si = update as { title?: string };
      return `session_info_update title="${si.title ?? ""}"`;
    }
    case "current_mode_update": {
      const cm = update as { currentModeId?: string };
      return `current_mode_update mode=${cm.currentModeId}`;
    }
    case "config_option_update": {
      const co = update as { configOptions?: unknown[] };
      return `config_option_update options_count=${co.configOptions?.length ?? 0}`;
    }
    case "available_commands_update": {
      const ac = update as { availableCommands?: unknown[] };
      return `available_commands_update count=${ac.availableCommands?.length ?? 0}`;
    }
    default:
      return `${kind} (unknown)`;
  }
}

type McpServerInput = { name: string; transport: string; command?: string; args?: string[]; env?: Record<string, string>; url?: string; headers?: Record<string, string> };

/** Convert renderer MCP server configs to ACP SDK format (with fresh auth headers). */
async function buildAcpMcpServers(servers: McpServerInput[]): Promise<unknown[]> {
  return (await Promise.all(servers.map(async (s) => {
    if (s.transport === "stdio") {
      if (!s.command) { log("ACP_MCP_WARN", `Server "${s.name}" (stdio) missing command — skipping`); return null; }
      return {
        name: s.name,
        command: s.command,
        args: s.args ?? [],
        env: s.env ? Object.entries(s.env).map(([name, value]) => ({ name, value })) : [],
      };
    }
    if (!s.url) { log("ACP_MCP_WARN", `Server "${s.name}" (${s.transport}) missing URL — skipping`); return null; }
    const authHeaders = await getMcpAuthHeaders(s.name, s.url);
    const mergedHeaders = { ...s.headers, ...authHeaders };
    return {
      type: s.transport as "http" | "sse",
      name: s.name,
      url: s.url,
      headers: Object.entries(mergedHeaders).map(([name, value]) => ({ name, value })),
    };
  }))).filter(Boolean);
}

/** Merge configOptions from session response, event buffer, and unstable models API. */
function resolveConfigOptions(
  sessionResult: { configOptions?: unknown[]; models?: unknown },
  internalId: string,
  logLabel: string,
): unknown[] {
  const fromResponse = (sessionResult.configOptions ?? []) as unknown[];
  const fromEvents = (configBuffer.get(internalId) ?? []) as unknown[];
  let configOptions = fromResponse.length ? fromResponse : fromEvents;

  // Fallback: synthesize config option from unstable models API
  const models = (sessionResult as Record<string, unknown>).models as { currentModelId?: string; availableModels?: Array<{ modelId: string; name: string; description?: string }> } | null;
  if (configOptions.length === 0 && models?.availableModels?.length) {
    log(logLabel, `No configOptions, synthesizing from ${models.availableModels.length} models (unstable API)`);
    configOptions = [{
      id: "model",
      name: "Model",
      category: "model",
      type: "select",
      currentValue: models.currentModelId ?? models.availableModels[0].modelId,
      options: models.availableModels.map(m => ({
        value: m.modelId,
        name: m.name,
        description: m.description ?? null,
      })),
    }];
  }

  if (configOptions.length) configBuffer.set(internalId, configOptions);
  log(logLabel, `${configOptions.length} config options (response=${fromResponse.length}, buffered=${fromEvents.length}, models=${models?.availableModels?.length ?? 0})`);
  return configOptions;
}

interface AcpConnectionResult {
  projectContext?: ProjectContext;
  proc: ChildProcess;
  connection: ClientSideConnection;
  pendingPermissions: Map<string, { resolve: (r: unknown) => void }>;
  internalId: string;
  supportsLoadSession: boolean;
  supportsResumeSession: boolean;
  isDeepSeekHarness: boolean;
}

/**
 * Spawn an ACP agent process, create the ClientSideConnection, and initialize the protocol.
 * Shared by acp:start and acp:revive-session to avoid duplicating ~120 lines of boilerplate.
 */
async function createAcpConnection(
  agentDef: { binary: string; args?: string[]; env?: Record<string, string>; name: string },
  getMainWindow: () => BrowserWindow | null,
  logLabel: string,
  cwd: string,
  request?:{projectId?:string;sessionId?:string;agentSessionId?:string},
): Promise<AcpConnectionResult> {
  const acp = await getACP();
  const isolated=process.env.NOMISMA_DESKTOP_MODE==='isolated';
  const needsProject=isolated||Boolean(agentDef.env?.NOMISMA_WORKSPACE);
  const internalId=needsProject&&request?.sessionId?request.sessionId:crypto.randomUUID();
  const binding=needsProject?projectAgentEnvironment(request?.projectId,cwd,internalId):undefined;
  if(binding&&request?.sessionId){
    if(acpSessions.has(internalId))throw new Error('Work session is already connected');
    assertProjectResume(binding.context,request.agentSessionId);
  }

  const binary=isolated?nomismaNode():agentDef.binary;
  const args=isolated?[path.join(nomismaProjectRoot(),'launcher/start_dsh_acp.cjs')]:agentDef.args??[];
  const providerEnvironment=binding?await modelProviders().runtime.environment(internalId):{};
  const proc = spawn(binary,args, {
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...researchEnvironment({...process.env,...(isolated?{}:agentDef.env),...binding?.env},isolated),
      ...providerEnvironment, ...(installedNomisma()?{NOMISMA_BUNDLED_ACP:'1'}:{}) },
    cwd: installedNomisma() ? path.dirname(binary) : cwd,
    windowsHide: true,
    shell: !isolated&&process.platform === "win32" && /\.(cmd|bat)$/i.test(binary),
  });

  if(binding)proc.once("close",()=>modelProviders().runtime.release(internalId));
  // Process lifecycle handlers
  proc.on("error", (err) => {
    log(logLabel, `ERROR: spawn failed: ${err.message}`);
    safeSend(getMainWindow, "acp:exit", {
      _sessionId: internalId,
      code: 1,
      error: `Failed to start agent: ${err.message}`,
    });
    acpSessions.delete(internalId);
    configBuffer.delete(internalId);
    commandsBuffer.delete(internalId);
  });

  proc.stderr?.on("data", (chunk: Buffer) => {
    const raw = chunk.toString().trim();
    log("ACP_STDERR", `session=${internalId.slice(0, 8)} ${raw}`);
    const cleaned = raw.replace(/\x1b\[[0-9;]*m/g, "");
    const turnError = cleaned.match(/Unhandled error during turn:\s*(.+)$/)?.[1]?.trim();
    const parsed = turnError || (/\bERROR\b/i.test(cleaned) ? cleaned : undefined);
    if (!parsed) return;
    const entry = acpSessions.get(internalId);
    if (entry) entry.lastStderrError = process.env.NOMISMA_DESKTOP_MODE==='isolated'?'研究运行失败；请检查模型连接或本地任务状态。':parsed;
  });

  proc.on("exit", (code) => {
    // Guard: session may already be deleted by the "error" handler (ENOENT race)
    if (!acpSessions.has(internalId)) return;
    const entry = acpSessions.get(internalId)!;
    log("ACP_EXIT", `session=${internalId.slice(0, 8)} code=${code} total_events=${entry.eventCounter}`);
    for (const [, resolver] of entry.pendingPermissions) {
      resolver.resolve({ outcome: { outcome: "cancelled" } });
    }
    entry.pendingPermissions.clear();
    clearPlanning(internalId);
    if(entry.projectContext&&hasResearch(entry.projectContext))void researchCall(entry.projectContext,'interrupted').catch(error=>log('RESEARCH_RECOVERY_REQUIRED',String(error)));
    safeSend(getMainWindow, "acp:exit", { _sessionId: internalId, code });
    acpSessions.delete(internalId);
    configBuffer.delete(internalId);
    commandsBuffer.delete(internalId);
  });

  // Stream + connection setup
  const input = Writable.toWeb(proc.stdin!) as WritableStream;
  const output = Readable.toWeb(proc.stdout!) as ReadableStream<Uint8Array>;
  const stream = acp.ndJsonStream(input, output);
  const pendingPermissions = new Map<string, { resolve: (r: unknown) => void }>();
  const toolCalls = new Map<string, Record<string, unknown>>();

  const connection = new acp.ClientSideConnection((_agent) => ({
    async sessionUpdate(params: Record<string, unknown>) {
      const update = (params as { update: Record<string, unknown> }).update;
      const acpSessionId = (params as { sessionId: string }).sessionId;
      const entry = acpSessions.get(internalId);

      // Utility session events: accumulate text, skip renderer forwarding
      if (entry?.utilitySessionIds?.has(acpSessionId)) {
        const eventKind = (update as { sessionUpdate: string }).sessionUpdate;
        if (eventKind === "agent_message_chunk") {
          const text = (update as { content?: { text?: string } }).content?.text ?? "";
          if (text && entry.utilityTextBuffers) {
            const current = entry.utilityTextBuffers.get(acpSessionId) ?? "";
            entry.utilityTextBuffers.set(acpSessionId, current + text);
          }
        }
        return;
      }

      if (entry) entry.eventCounter++;
      if(entry?.projectContext && update.sessionUpdate==='agent_message_chunk'){
        const content=update.content as {text?:string}|undefined;
        if(typeof content?.text==='string')entry.researchText=((entry.researchText??'')+content.text).slice(-120000);
      }
      const count = entry?.eventCounter ?? 0;
      const summary = summarizeUpdate(update);
      log("ACP_EVENT", `session=${internalId.slice(0, 8)} #${count} ${entry?.isReloading ? "[suppressed] " : ""}${summary}`);

      // Full dump for tool calls and tool results
      const eventKind = update?.sessionUpdate as string;
      if (eventKind === "tool_call" && typeof update.toolCallId === "string") {
        toolCalls.set(update.toolCallId, update);
      }
      if (eventKind === "tool_call" || eventKind === "tool_call_update") {
        log("ACP_EVENT_FULL", update);
        if(entry?.projectContext && hasResearch(entry.projectContext) && ['completed','failed'].includes(String(update.status)) && typeof update.toolCallId==='string'){
          try{await researchCall(entry.projectContext,'result',{tool_call_id:update.toolCallId,receipt:receiptFromUpdate(update)});}
          catch(error){log('RESEARCH_RESULT_ERROR',String(error));}
        }
      }

      // Buffer config options for late-subscribing renderer listeners
      if (eventKind === "config_option_update") {
        const configOptions = (update as { configOptions: unknown[] }).configOptions;
        configBuffer.set(internalId, configOptions);
      }

      // Buffer available commands for late-subscribing renderer listeners
      if (eventKind === "available_commands_update") {
        const commands = (update as { availableCommands: unknown[] }).availableCommands;
        commandsBuffer.set(internalId, commands);
      }

      // During session/load, suppress history replay from reaching the renderer
      if (entry?.isReloading) return;

      safeSend(getMainWindow, "acp:event", {
        _sessionId: internalId,
        sessionId: acpSessionId,
        update,
      });
    },

    async requestPermission(params: Record<string, unknown>) {
      const acpSessionId = (params as { sessionId: string }).sessionId;
      const entry = acpSessions.get(internalId);

      // Auto-deny permission requests for utility sessions
      if (entry?.utilitySessionIds?.has(acpSessionId)) {
        log("ACP_UTILITY", `Auto-denying permission for utility session ${acpSessionId.slice(0, 12)}`);
        const options = (params as { options: Array<{ optionId: string; kind: string }> }).options;
        const rejectOption = options.find(o => o.kind === "reject_once") ?? options[options.length - 1];
        return { outcome: { outcome: "selected", optionId: rejectOption?.optionId ?? "reject" } };
      }

      const requestId = crypto.randomUUID();
        const requestCall = (params as { toolCall: Record<string, unknown> }).toolCall;
        const previousCall = typeof requestCall.toolCallId === "string" ? toolCalls.get(requestCall.toolCallId) : undefined;
        // DSH permissions can contain only an id; correlate the earlier real call.
        const toolCall = { ...previousCall, ...requestCall };
        const opts = (params as { options: unknown[] }).options;
      if(entry?.projectContext){
        try{
          if(planningPending(internalId))throw new Error('Research planning turn cannot execute tools before plan confirmation');
          if(hasResearch(entry.projectContext)){
            const name=researchToolName(toolCall.title);
            await researchCall(entry.projectContext,'admit',{tool:name??'unknown',arguments:toolCall.rawInput??{},
                tool_call_id:toolCall.toolCallId,permission_id:requestId});
          }
        }catch(error){
          log('RESEARCH_ADMISSION_DENIED',String(error));
          const options=opts as Array<{kind?:string;optionId:string}>;
          return {outcome:{outcome:'selected',optionId:options.find(o=>o.kind==='reject_once')?.optionId??'reject-once'}};
        }
      }
      return new Promise((resolve) => {
        pendingPermissions.set(requestId, { resolve:response=>resolve(response as RequestPermissionResponse) });

        log("ACP_PERMISSION_REQUEST", {
          session: internalId.slice(0, 8),
          requestId,
          tool: toolCall?.title,
          kind: toolCall?.kind,
          toolCallId: (toolCall?.toolCallId as string)?.slice(0, 12),
          optionCount: Array.isArray(opts) ? opts.length : 0,
        });

        safeSend(getMainWindow, "acp:permission_request", {
          _sessionId: internalId,
          requiresExplicitApproval: Boolean(nomismaWorkspace),
          requestId,
          sessionId: acpSessionId,
          toolCall,
          options: opts,
        });
      });
    },

    async readTextFile(params: { path?: string; uri?: string; line?: number | null; limit?: number | null }) {
      if (nomismaWorkspace) throw new Error("Nomisma ACP client filesystem is disabled");
      const { filePath, content } = await acpReadTextFile(params);
      log("ACP_FS", `readTextFile path=${filePath} line=${params.line ?? ""} limit=${params.limit ?? ""}`);
      log("ACP_FS", `readTextFile result len=${content.length}`);
      return { content };
    },
    async writeTextFile(params: { path?: string; uri?: string; content: string }) {
      if (nomismaWorkspace) throw new Error("Nomisma ACP client filesystem is disabled");
      const { filePath } = await acpWriteTextFile(params);
      log("ACP_FS", `writeTextFile path=${filePath} len=${params.content.length}`);
      return {};
    },
  }), stream);

  // Protocol initialization
  log(logLabel, `Initializing protocol...`);
  const initResult = await connection.initialize({
    protocolVersion: acp.PROTOCOL_VERSION,
    clientCapabilities: nomismaWorkspace ? {} : ACP_CLIENT_CAPABILITIES,
  });
  const supportsLoadSession = initResult.agentCapabilities?.loadSession === true;
  const supportsResumeSession = initResult.agentCapabilities?.sessionCapabilities?.resume != null;
  const isDeepSeekHarness = initResult.agentInfo?.name === "deepseek-harness-acp";
  log(logLabel, `Initialized protocol v${initResult.protocolVersion} for ${agentDef.name} (loadSession=${supportsLoadSession})`);

  return { proc, connection, pendingPermissions, internalId, supportsLoadSession, supportsResumeSession, isDeepSeekHarness,projectContext:binding?.context };
}

export function register(getMainWindow: () => BrowserWindow | null): void {

  // Forward renderer-side ACP logs to main process log file
  ipcMain.on("acp:log", (_event, label: string, data: unknown) => {
    log(`ACP_UI:${label}`, data);
  });

  ipcMain.handle("acp:start", async (_event, options: { agentId: string; cwd: string; projectId?:string; mcpServers?: McpServerInput[] }) => {
    log("ACP_SPAWN", `acp:start called with agentId=${options.agentId} cwd=${options.cwd}`);

    const agentDef = getAgent(options.agentId);
    if(process.env.NOMISMA_DESKTOP_MODE==='isolated'&&options.agentId!=='nomisma-dsh-acp')return {error:'Only the registered Nomisma ACP agent is enabled'};
    if (!agentDef || agentDef.engine !== "acp") {
      const err = `Agent "${options.agentId}" not found or not an ACP agent`;
      log("ACP_SPAWN", `ERROR: ${err}`);
      return { error: err };
    }
    if (!agentDef.binary) {
      const err = `Agent "${options.agentId}" has no binary configured`;
      log("ACP_SPAWN", `ERROR: ${err}`);
      return { error: err };
    }

    let connResult: AcpConnectionResult | null = null;
    const analyticsProperties = buildAcpAnalyticsProperties(agentDef);
    try {
      connResult = await createAcpConnection(agentDef as { binary: string; args?: string[]; env?: Record<string, string>; name: string }, getMainWindow, "ACP_SPAWN", options.cwd,options);
      const { proc, connection, pendingPermissions, internalId, supportsLoadSession, supportsResumeSession, isDeepSeekHarness } = connResult;

      // Track immediately so the renderer can abort during the long protocol init / npx download
      pendingStartProcess = { id: internalId, process: proc };

      const servers=connResult.projectContext?projectMcpServers(connResult.projectContext):(options.mcpServers??[]);
      const acpMcpServers = await buildAcpMcpServers(servers);

      log("ACP_SPAWN", `Creating new session with ${acpMcpServers.length} MCP server(s)...`);
      const sessionResult = await connection.newSession({
        cwd: options.cwd,
        mcpServers: acpMcpServers,
      });
      log("ACP_SPAWN", `Created session ${sessionResult.sessionId} for ${agentDef.name}`);

      const entry: ACPSessionEntry = {
        projectContext:connResult.projectContext,
        process: proc,
        connection,
        acpSessionId: sessionResult.sessionId,
        internalId,
        analyticsProperties,
        eventCounter: 0,
        pendingPermissions,
        cwd: options.cwd,
        supportsLoadSession,
        supportsResumeSession,
        isDeepSeekHarness,
        isReloading: false,
      };
      acpSessions.set(internalId, entry);

      const configOptions = resolveConfigOptions(sessionResult, internalId, "ACP_SPAWN");

      // Derive MCP statuses — ACP doesn't report them, so infer from config
      const mcpStatuses = servers.map(s => ({
        name: s.name,
        status: "connected" as const,
      }));

      // Startup succeeded — clear the pending tracker before returning
      pendingStartProcess = null;

      void captureEvent("session_created", { engine: "acp", ...analyticsProperties });

      return {
        sessionId: internalId,
        agentSessionId: sessionResult.sessionId,
        agentName: agentDef.name,
        configOptions,
        mcpStatuses,
      };
    } catch (err) {
      // Check if the user intentionally aborted the start (stop button during download)
      const wasAborted = pendingStartProcess?.aborted === true;
      pendingStartProcess = null;

      // Kill the spawned process to avoid orphans
      try { connResult?.proc?.kill(); } catch { /* already dead */ }

      if (wasAborted) {
        log("ACP_SPAWN", `Aborted by user`);
        return { cancelled: true };
      }

      const msg = reportError("ACP_SPAWN", err, { engine: "acp", ...analyticsProperties });
      return { error: msg };
    }
  });

  // Revive a dead ACP session after app restart.
  // Spawns a fresh agent process and calls session/load (if supported) to restore context,
  // or falls back to newSession (fresh context, UI messages already restored from disk).
  ipcMain.handle("acp:revive-session", async (_event, options: {
    agentId: string;
    cwd: string;
    projectId?:string;
    sessionId?:string;
    agentSessionId?: string; // ACP-side session ID from previous run
    mcpServers?: McpServerInput[];
  }) => {
    log("ACP_REVIVE", `agentId=${options.agentId} agentSessionId=${options.agentSessionId?.slice(0, 12) ?? "none"} cwd=${options.cwd}`);

    const agentDef = getAgent(options.agentId);
    if (!agentDef || agentDef.engine !== "acp" || !agentDef.binary) {
      return { error: `Agent "${options.agentId}" not found or not an ACP agent` };
    }

    let connResult: AcpConnectionResult | null = null;
    const analyticsProperties = buildAcpAnalyticsProperties(agentDef);
    try {
      if(process.env.NOMISMA_DESKTOP_MODE==='isolated'&&options.agentId!=='nomisma-dsh-acp')throw new Error('Only Nomisma work sessions can be resumed');
      if((process.env.NOMISMA_DESKTOP_MODE==='isolated'||agentDef.env?.NOMISMA_WORKSPACE)&&!options.sessionId)throw new Error('A stored Nomisma work-session ID is required for resume');
      connResult = await createAcpConnection(agentDef as { binary: string; args?: string[]; env?: Record<string, string>; name: string }, getMainWindow, "ACP_REVIVE", options.cwd,options);
      const { proc, connection, pendingPermissions, internalId, supportsLoadSession, supportsResumeSession, isDeepSeekHarness } = connResult;

      const servers=connResult.projectContext?projectMcpServers(connResult.projectContext):(options.mcpServers??[]);
      const acpMcpServers = await buildAcpMcpServers(servers);

      let acpSessionId: string;
      let usedLoad = false;
      let configOptions: unknown[] = [];

      if (supportsLoadSession && options.agentSessionId) {
        // Restore full context — suppress history replay from reaching the renderer
        const entry: ACPSessionEntry = { process: proc, connection, acpSessionId: options.agentSessionId, internalId, analyticsProperties, eventCounter: 0, pendingPermissions, cwd: options.cwd, supportsLoadSession, supportsResumeSession, isDeepSeekHarness, isReloading: true };
        acpSessions.set(internalId, entry);
        const loadResult = await connection.loadSession({ sessionId: options.agentSessionId, cwd: options.cwd, mcpServers: acpMcpServers });
        entry.isReloading = false;
        acpSessionId = options.agentSessionId;
        usedLoad = true;
        configOptions = (loadResult.configOptions ?? configBuffer.get(internalId) ?? []) as unknown[];
        if (configOptions.length) configBuffer.set(internalId, configOptions);
        log("ACP_REVIVE", `loadSession OK, session=${acpSessionId.slice(0, 12)} configOptions=${configOptions.length}`);
      } else if (supportsResumeSession && options.agentSessionId) {
        // Official ACP resume retains agent context without replaying UI history.
        const resumed = await connection.unstable_resumeSession({
          sessionId: options.agentSessionId, cwd: options.cwd, mcpServers: acpMcpServers,
        });
        acpSessionId = options.agentSessionId;
        const entry: ACPSessionEntry = { process: proc, connection, acpSessionId, internalId, analyticsProperties, eventCounter: 0, pendingPermissions, cwd: options.cwd, supportsLoadSession, supportsResumeSession, isDeepSeekHarness, isReloading: false };
        acpSessions.set(internalId, entry);
        configOptions = resolveConfigOptions(resumed, internalId, "ACP_REVIVE");
        log("ACP_REVIVE", `resumeSession OK, session=${acpSessionId.slice(0, 12)}`);
      } else if (isDeepSeekHarness && options.agentSessionId) {
        throw new Error("DSH session cannot be resumed; refusing silent context loss");
      } else {
        // Fall back to fresh session — UI messages already restored from disk
        const sessionResult = await connection.newSession({ cwd: options.cwd, mcpServers: acpMcpServers });
        acpSessionId = sessionResult.sessionId;
        const entry: ACPSessionEntry = { process: proc, connection, acpSessionId, internalId, analyticsProperties, eventCounter: 0, pendingPermissions, cwd: options.cwd, supportsLoadSession, supportsResumeSession, isDeepSeekHarness, isReloading: false };
        acpSessions.set(internalId, entry);
        configOptions = resolveConfigOptions(sessionResult, internalId, "ACP_REVIVE");
        log("ACP_REVIVE", `newSession fallback, session=${acpSessionId.slice(0, 12)}`);
      }

      const revived=acpSessions.get(internalId);if(revived)revived.projectContext=connResult.projectContext;
      const mcpStatuses = servers.map(s => ({ name: s.name, status: "connected" as const }));
      void captureEvent("session_revived", { engine: "acp", success: true, ...analyticsProperties });
      return { sessionId: internalId, agentSessionId: acpSessionId, usedLoad, configOptions, mcpStatuses };
    } catch (err) {
      // Kill process and clean up any partial session entry
      try { connResult?.proc?.kill(); } catch { /* already dead */ }
      if (connResult?.internalId) {
        acpSessions.delete(connResult.internalId);
        configBuffer.delete(connResult.internalId);
      }
      const msg = reportError("ACP_REVIVE", err, { engine: "acp", ...analyticsProperties });
      return { error: msg };
    }
  });

  ipcMain.handle("acp:prompt", async (_event, { sessionId, text, images }: { sessionId: string; text: string; images?: Array<{ data: string; mediaType: string }> }) => {
    const session = acpSessions.get(sessionId);
    if (!session) {
      log("ACP_SEND", `ERROR: session ${sessionId?.slice(0, 8)} not found`);
      return { error: "Session not found" };
    }

    if(session.projectContext&&images?.length)return {error:'原始文件与图片上传当前未开放；请使用本地数据导入工具。'};
    if(session.promptRunning)return {error:new ProviderError("BUSY").message};
    log("ACP_SEND", `session=${sessionId.slice(0, 8)} text_length=${text.length} images=${images?.length ?? 0}`);

    const prompt: Array<{ type: string; text?: string; data?: string; mimeType?: string }> = [];
    if (images) {
      for (const img of images) {
        prompt.push({ type: "image", data: img.data, mimeType: img.mediaType });
      }
    }
    prompt.push({ type: "text", text });

    session.promptRunning=true;
    let providerTurnStarted=false;
    try {
      if(session.projectContext){await modelProviders().runtime.begin(sessionId,text);providerTurnStarted=true;}
      if(session.projectContext){
        const context=workspaceStore().context(session.projectContext.project_id,sessionId,session.cwd);
        if(context.mode!==session.projectContext.mode)throw new Error('Project context changed');
        prompt.unshift({type:'text',text:`Nomisma Project Context (application supplied):\n${JSON.stringify(coordinationForModel(context))}`});
      }
      session.promptRunning=true;
      session.researchStopRequested=false;
      session.researchText='';
      session.lastStopReason=undefined;
      session.lastStderrError = undefined;
      const wasPlanning=planningPending(sessionId);
      let researchBefore:ResearchTurnContext|undefined;
      if(session.projectContext && hasResearch(session.projectContext)){
        const context=await researchCall(session.projectContext,'prompt') as {managed?:boolean};
        researchBefore=context;
        if(context.managed)prompt.push({type:'text',text:(planningPending(sessionId)?'Planning/revision only: do not execute tools. Propose a revised nomisma-plan block or ask missing questions.\n':'Application-supplied research coordination context (tool approvals remain mandatory):\n')+JSON.stringify(coordinationForModel(context))});
      }
      let result = await session.connection.prompt({
        sessionId: session.acpSessionId,
        prompt,
      });
      if(session.projectContext&&!wasPlanning){
        const trustedContext=session.projectContext;
        result=await continueResearchTurn({result,before:researchBefore,stopped:()=>!!session.researchStopRequested,
          refresh:async()=>await researchCall(trustedContext,'context') as ResearchTurnContext,
          next:async()=>{
            const context=await researchCall(trustedContext,'prompt');
            if(session.researchStopRequested)return result;
            return session.connection.prompt({sessionId:session.acpSessionId,prompt:[{type:'text',text:'Application research continuation after verified task progress. Execute only the exact ready task arguments; each tool still requires Ask First. Do not change locked definitions.\n'+JSON.stringify(coordinationForModel(context))}]});
          }});
      }
      session.lastStopReason=result.stopReason;
      if(session.projectContext){
        try{
          await captureResearchProposal(session.projectContext,session.researchText??'');
          if(result.stopReason==='cancelled' && hasResearch(session.projectContext))await researchCall(session.projectContext,'interrupted');
        }catch(error){log('RESEARCH_TURN_RECONCILIATION',String(error));}
      }

      log("ACP_TURN_COMPLETE", `session=${sessionId.slice(0, 8)} stopReason=${result.stopReason} usage=${JSON.stringify(result.usage ?? null)}`);

      safeSend(getMainWindow,"acp:turn_complete", {
        _sessionId: sessionId,
        stopReason: result.stopReason,
        usage: result.usage,
      });

      return { ok: true };
    } catch (err) {
      if(err instanceof ProviderError&&["SETUP","VERIFY","CREDENTIAL","STORAGE"].includes(err.code))safeSend(getMainWindow,"model-providers:setup-required",{});
      if(providerTurnStarted&&session.projectContext && hasResearch(session.projectContext)){
        try{await researchCall(session.projectContext,'interrupted');}catch(error){log('RESEARCH_RECOVERY_REQUIRED',String(error));}
      }
      const msg = extractErrorMessage(err);
      const surfacedError = msg === "Internal error" && session.lastStderrError ? session.lastStderrError : msg;
      session.lastStopReason='error';
      reportError("ACP_PROMPT_ERR", err, { engine: "acp", sessionId, surfacedError });
      return { error: surfacedError };
    } finally { session.promptRunning=false;if(session.projectContext)modelProviders().runtime.end(sessionId); }
  });

  // Abort an in-flight acp:start (e.g. user clicked stop during npx download).
  // Marks pendingStartProcess as aborted and kills the process — the acp:start
  // catch block will detect `.aborted` and return { cancelled: true }.
  ipcMain.handle("acp:abort-pending-start", async () => {
    if (!pendingStartProcess) {
      log("ACP_ABORT_START", "No pending start to abort");
      return { ok: false };
    }
    log("ACP_ABORT_START", `Aborting start id=${pendingStartProcess.id.slice(0, 8)} pid=${pendingStartProcess.process.pid}`);
    pendingStartProcess.aborted = true;
    try { pendingStartProcess.process.kill(); } catch { /* already dead */ }
    return { ok: true };
  });

  ipcMain.handle("acp:stop", async (_event, sessionId: string) => {
    const session = acpSessions.get(sessionId);
    if (!session) {
      // Fallback: check if this is a pending start that hasn't completed yet
      if (pendingStartProcess?.id === sessionId) {
        log("ACP_STOP", `session=${sessionId?.slice(0, 8)} is pending start — aborting`);
        pendingStartProcess.aborted = true;
        try { pendingStartProcess.process.kill(); } catch { /* already dead */ }
        return { ok: true };
      }
      log("ACP_STOP", `session=${sessionId?.slice(0, 8)} already removed`);
      return { ok: true };
    }
    log("ACP_STOP", `session=${sessionId.slice(0, 8)} killing pid=${session.process.pid} total_events=${session.eventCounter}`);
    session.researchStopRequested=true;
    if(session.projectContext)modelProviders().runtime.end(sessionId);
    // Drain pending permissions before killing
    for (const [, resolver] of session.pendingPermissions) {
      resolver.resolve({ outcome: { outcome: "cancelled" } });
    }
    session.pendingPermissions.clear();
    if (session.isDeepSeekHarness) {
      // SDK 0.15 does not type session/close; DSH documents this public method.
      // extMethod sends the public wire method directly, without an internal API.
      let deadline: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          session.connection.extMethod("session/close", { sessionId: session.acpSessionId }),
          new Promise<never>((_, reject) => { deadline = setTimeout(() => reject(new Error("ACP close timeout")), 10000); }),
        ]);
        log("ACP_STOP", "DSH session/close completed");
      } catch (error) {
        reportError("ACP_CLOSE_ERR", error, { sessionId });
      } finally { if (deadline) clearTimeout(deadline); }
      session.process.stdin?.end();
    } else {
      session.process.kill();
    }
    clearPlanning(sessionId);
    if(session.projectContext&&hasResearch(session.projectContext)){
      try{await researchCall(session.projectContext,'interrupted');}catch(error){log('RESEARCH_RECOVERY_REQUIRED',String(error));}
    }
    acpSessions.delete(sessionId);
    configBuffer.delete(sessionId);
    commandsBuffer.delete(sessionId);
    return { ok: true };
  });

  // Reload an existing ACP session with a new MCP server list using session/load.
  // This preserves full conversation context on the agent side — no process restart needed.
  // Returns { ok: true, supportsLoad: true } if successful, { supportsLoad: false } if not supported.
  ipcMain.handle("acp:reload-session", async (_event, { sessionId, mcpServers, cwd }: {
    sessionId: string;
    mcpServers?: McpServerInput[];
    cwd?: string;
  }) => {
    const session = acpSessions.get(sessionId);
    if (!session) {
      log("ACP_RELOAD", `ERROR: session ${sessionId?.slice(0, 8)} not found`);
      return { error: "Session not found" };
    }
    if (!session.supportsLoadSession) {
      log("ACP_RELOAD", `session=${sessionId.slice(0, 8)} agent does not support session/load, falling back to restart`);
      return { supportsLoad: false };
    }

    const nextCwd = cwd ?? session.cwd;
    if(session.projectContext){
      workspaceStore().context(session.projectContext.project_id,sessionId,nextCwd);
      mcpServers=projectMcpServers(session.projectContext);
    }
    log("ACP_RELOAD", `session=${sessionId.slice(0, 8)} calling loadSession with ${mcpServers?.length ?? 0} MCP server(s) cwd=${nextCwd}`);

    const acpMcpServers = await buildAcpMcpServers(mcpServers ?? []);

    try {
      // Suppress history replay notifications so the renderer doesn't get duplicates
      session.isReloading = true;
      try {
        await session.connection.loadSession({
          sessionId: session.acpSessionId,
          cwd: nextCwd,
          mcpServers: acpMcpServers,
        });
      } finally {
        // Always reset — even if loadSession throws or process crashes
        if (acpSessions.has(sessionId)) {
          acpSessions.get(sessionId)!.isReloading = false;
        }
      }
      session.cwd = nextCwd;
      log("ACP_RELOAD", `session=${sessionId.slice(0, 8)} loadSession OK`);
      return { ok: true, supportsLoad: true };
    } catch (err) {
      const msg = reportError("ACP_RELOAD_ERR", err, { engine: "acp", sessionId });
      return { error: msg, supportsLoad: true };
    }
  });

  ipcMain.handle("acp:cancel", async (_event, sessionId: string) => {
    const session = acpSessions.get(sessionId);
    if (!session) {
      log("ACP_CANCEL", `ERROR: session ${sessionId?.slice(0, 8)} not found`);
      return { error: "Session not found" };
    }

    const pendingCount = session.pendingPermissions.size;
    session.researchStopRequested=true;
    if(session.projectContext)modelProviders().runtime.end(sessionId);
    log("ACP_CANCEL", `session=${sessionId.slice(0, 8)} cancelling (${pendingCount} pending permissions)`);

    try {
      clearPlanning(sessionId);
      if(session.projectContext && hasResearch(session.projectContext)){
        const state=await researchCall(session.projectContext,'context') as {managed?:boolean;research_id?:string};
        if(state.managed&&state.research_id)await researchCall(session.projectContext,'pause',{research_id:state.research_id});
      }
      await cancelAcpPrompt(session.connection, session.acpSessionId, session.pendingPermissions);
      log("ACP_CANCEL", `session=${sessionId.slice(0, 8)} acknowledged`);
      return { ok: true };
    } catch (err) {
      const msg = reportError("ACP_CANCEL_ERR", err, { engine: "acp", sessionId });
      return { error: msg };
    }
  });

  ipcMain.handle("acp:set-config", async (_event, { sessionId, configId, value }: { sessionId: string; configId: string; value: string }) => {
    const session = acpSessions.get(sessionId);
    if (!session) {
      log("ACP_CONFIG", `ERROR: session ${sessionId?.slice(0, 8)} not found`);
      return { error: "Session not found" };
    }
    log("ACP_CONFIG", `session=${sessionId.slice(0, 8)} setting ${configId}=${value}`);
    try {
      const conn = session.connection;

      // Try the stable config option API first
      try {
        const result = await conn.setSessionConfigOption({
          sessionId: session.acpSessionId,
          configId,
          value,
        });
        log("ACP_CONFIG", `session=${sessionId.slice(0, 8)} ${configId}=${value} OK (via setSessionConfigOption)`);
        if (result.configOptions) configBuffer.set(sessionId, result.configOptions);
        return { configOptions: result.configOptions };
      } catch (configErr) {
        // If it fails and this is the model config, try the unstable setSessionModel API
        if (configId === "model") {
          log("ACP_CONFIG", `session=${sessionId.slice(0, 8)} setSessionConfigOption failed, trying unstable_setSessionModel...`);
          await conn.unstable_setSessionModel({
            sessionId: session.acpSessionId,
            modelId: value,
          });
          log("ACP_CONFIG", `session=${sessionId.slice(0, 8)} model=${value} OK (via unstable_setSessionModel)`);

          // Update the synthesized config option in the buffer
          const buffered = configBuffer.get(sessionId) as Array<{ id: string; currentValue: string }> | undefined;
          if (buffered) {
            const modelOpt = buffered.find(o => o.id === "model");
            if (modelOpt) modelOpt.currentValue = value;
            return { configOptions: buffered };
          }
          return {};
        }
        throw configErr;
      }
    } catch (err) {
      const errMsg = reportError("ACP_CONFIG_ERR", err, { engine: "acp", sessionId, configId });
      return { error: errMsg };
    }
  });

  // Retrieve buffered config options — used by renderer when useACP first mounts
  // and may have missed config_option_update events during DRAFT→active transition
  ipcMain.handle("acp:get-config-options", async (_event, sessionId: string) => {
    return { configOptions: configBuffer.get(sessionId) ?? [] };
  });

  // Retrieve buffered available commands — same pattern as config options
  ipcMain.handle("acp:get-available-commands", async (_event, sessionId: string) => {
    return { commands: commandsBuffer.get(sessionId) ?? [] };
  });

  ipcMain.handle("acp:permission_response", async (_event, { sessionId, requestId, optionId }: { sessionId: string; requestId: string; optionId: string }) => {
    const session = acpSessions.get(sessionId);
    if (!session) {
      log("ACP_PERMISSION_RESPONSE", `ERROR: session ${sessionId?.slice(0, 8)} not found`);
      return { error: "Session not found" };
    }

    const resolver = session.pendingPermissions.get(requestId);
    if (!resolver) {
      log("ACP_PERMISSION_RESPONSE", `ERROR: session=${sessionId.slice(0, 8)} no pending permission for requestId=${requestId}`);
      return { error: "No pending permission" };
    }

    log("ACP_PERMISSION_RESPONSE", `session=${sessionId.slice(0, 8)} requestId=${requestId} optionId=${optionId}`);
    if(session.projectContext && hasResearch(session.projectContext)){
      try{await researchCall(session.projectContext,'decision',{permission_id:requestId,allow:optionId==='allow-once'});}
      catch(error){resolver.resolve({outcome:{outcome:'cancelled'}});session.pendingPermissions.delete(requestId);return {error:String(error)};}
    }
    resolver.resolve({ outcome: { outcome: "selected", optionId } });
    session.pendingPermissions.delete(requestId);
    return { ok: true };
  });
}
