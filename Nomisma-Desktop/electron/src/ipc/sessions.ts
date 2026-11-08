import { ipcMain } from "electron";
import path from "path";
import fs from "fs";
import { getDataDir, getProjectSessionsDir, getSessionFilePath } from "../lib/data-dir";
import { reportError } from "../lib/error-utils";
import { sessionContext, workspaceStore } from '../lib/project-context';
import { checkedId } from '../lib/workspace-store';
import { isPathRedirected } from '../lib/windows-runtime.cjs';
import crypto from 'crypto';
import { acpSessions } from './acp-sessions';
import {
  getLastUserMessageTimestamp,
  extractSessionMeta,
  type SessionMeta,
} from "@shared/lib/session-persistence";

interface SearchResult {
  messageResults: Array<{
    sessionId: string;
    projectId: string;
    sessionTitle: string;
    messageId: string;
    snippet: string;
    timestamp: number;
  }>;
  sessionResults: Array<{
    sessionId: string;
    projectId: string;
    title: string;
    createdAt: number;
  }>;
}

function getMetaFilePath(projectId: string, sessionId: string): string {
  return getSessionFilePath(projectId, sessionId).replace(/\.json$/, ".meta.json");
}

function bindSession(data:Record<string,unknown>):Record<string,unknown> {
  if(process.env.NOMISMA_DESKTOP_MODE!=='isolated')return data;
  const context=sessionContext(data as {projectId:string;id:string});
  if(data.mode!==undefined&&data.mode!==context.mode)throw new Error('Session mode cannot change');
  const declared=data.project_context as Record<string,unknown>|undefined;
  if(declared&&Object.keys(context).some(key=>declared[key]!==context[key as keyof typeof context]))throw new Error('Session context mismatch');
  return {...data,mode:context.mode,project_id:context.project_id,session_id:context.session_id,project_context:context};
}
function ownedPath(projectId:string,id?:string):void {
  if(process.env.NOMISMA_DESKTOP_MODE!=='isolated')return;
  workspaceStore().get(projectId);checkedId(projectId);if(id)sessionContext({projectId,id});
  const directory=path.join(getDataDir(),'sessions',projectId);
  for(const target of [path.dirname(directory),directory,...(id?[path.join(directory,`${id}.json`),path.join(directory,`${id}.meta.json`)]:[])]){
    if(fs.existsSync(target)&&isPathRedirected(target))throw new Error('Session links are not allowed');
  }
}
async function atomicSession(file:string,value:string):Promise<void>{
  const temp=path.join(path.dirname(file),`.session-${crypto.randomUUID()}.tmp`);
  try{await fs.promises.writeFile(temp,value,{encoding:'utf8',flag:'wx'});await fs.promises.rename(temp,file);}
  finally{if(path.dirname(temp)===path.dirname(file)&&fs.existsSync(temp))await fs.promises.unlink(temp);}
}

export function register(): void {
  ipcMain.handle("sessions:save", async (_event, data: { projectId: string; id: string; createdAt?: number; messages?: Array<{ role?: string; timestamp?: number }> }) => {
    try {
      ownedPath(data.projectId,data.id);
      const filePath = getSessionFilePath(data.projectId, data.id);
      const providedLastMessageAt = (data as Record<string, unknown>).lastMessageAt;
      const normalizedProvidedLastMessageAt =
        typeof providedLastMessageAt === "number" ? providedLastMessageAt : undefined;
      // Always prefer the latest user message timestamp when messages are present.
      const lastMessageAt =
        getLastUserMessageTimestamp(data.messages) ??
        normalizedProvidedLastMessageAt ??
        data.createdAt ??
        0;
      const runtime=acpSessions.get(data.id);
      if(runtime?.projectContext&&runtime.projectContext.project_id!==data.projectId)throw new Error('Live session belongs to another project');
      const prior=fs.existsSync(filePath)?JSON.parse(fs.readFileSync(filePath,'utf8')):{};
      if(prior.id!==undefined&&(prior.projectId!==data.projectId||prior.id!==data.id))throw new Error('Stored session ownership mismatch');
      const execution_status=runtime?.promptRunning?'running':runtime?.lastStopReason==='cancelled'?'cancelled':runtime?.lastStopReason==='error'?'error':runtime?.lastStopReason?'completed':prior.execution_status??'recorded';
      // Background exit payloads may omit provider identity; retain it so a
      // restart can validate and resume the same provider session.
      const enriched = bindSession({ ...prior, ...data, lastMessageAt,execution_status });

      // Write main session file (no pretty-printing for smaller file size)
      const writeMain = atomicSession(filePath, JSON.stringify(enriched));

      // Write metadata sidecar (fire-and-forget alongside main write)
      const meta = extractSessionMeta(enriched as unknown as Record<string, unknown>, lastMessageAt);
      const metaPath = getMetaFilePath(data.projectId, data.id);
      const writeMeta = atomicSession(metaPath, JSON.stringify(meta)).catch((err) => {
        reportError("SESSIONS:META_WRITE_ERR", err, { sessionId: data.id });
      });

      await Promise.all([writeMain, writeMeta]);
      return { ok: true };
    } catch (err) {
      const message = reportError("SESSIONS:SAVE_ERR", err, { sessionId: data.id });
      return { error: message };
    }
  });

  ipcMain.handle("sessions:load", async (_event, projectId: string, sessionId: string) => {
    ownedPath(projectId,sessionId);
    try {
      const filePath = getSessionFilePath(projectId, sessionId);
      try {
        await fs.promises.access(filePath);
      } catch {
        return null;
      }
      const data=JSON.parse(await fs.promises.readFile(filePath, "utf-8"));
      if(data.projectId!==projectId||data.id!==sessionId)throw new Error('Session ownership mismatch');
      return bindSession(data);
    } catch (err) {
      reportError("SESSIONS:LOAD_ERR", err, { projectId, sessionId });
      return null;
    }
  });

  ipcMain.handle("sessions:list", async (_event, projectId: string) => {
    ownedPath(projectId);
    try {
      const dir = getProjectSessionsDir(projectId);
      const allFiles = await fs.promises.readdir(dir);

      // Prefer .meta.json sidecar files for fast listing
      const metaFiles = allFiles.filter((f) => f.endsWith(".meta.json"));
      const metaBasenames = new Set(metaFiles.map((f) => f.replace(/\.meta\.json$/, "")));

      // Find .json files that lack a .meta.json sidecar (migration path)
      const fullParseFiles = allFiles.filter(
        (f) => f.endsWith(".json") && !f.endsWith(".meta.json") && !metaBasenames.has(f.replace(/\.json$/, ""))
      );

      const items = await Promise.all([
        // Fast path: read small sidecar files
        ...metaFiles.map(async (file): Promise<SessionMeta | null> => {
          try {
            ownedPath(projectId,file.replace(/\.meta\.json$/,''));
            const raw = await fs.promises.readFile(path.join(dir, file), "utf-8");
            const data = JSON.parse(raw) as SessionMeta;
            if(data.projectId!==projectId||data.id!==file.replace(/\.meta\.json$/,''))throw new Error('Session ownership mismatch');
            return bindSession(data as unknown as Record<string,unknown>) as unknown as SessionMeta;
          } catch {
            return null;
          }
        }),
        // Fallback: full-file parse for sessions without sidecar
        ...fullParseFiles.map(async (file): Promise<SessionMeta | null> => {
          try {
            ownedPath(projectId,file.slice(0,-5));
            const raw = await fs.promises.readFile(path.join(dir, file), "utf-8");
            const data = JSON.parse(raw) as Record<string, unknown>;
            if(data.projectId!==projectId||data.id!==file.slice(0,-5))throw new Error('Session ownership mismatch');
            const lastMessageAt: number =
              getLastUserMessageTimestamp(data.messages as Array<{ role?: string; timestamp?: number }>) ??
              (typeof data.lastMessageAt === "number" ? data.lastMessageAt : undefined) ??
              (data.createdAt as number) ??
              0;

            return extractSessionMeta(bindSession(data), lastMessageAt);
          } catch {
            return null;
          }
        }),
      ]);

      const list: SessionMeta[] = items.filter((item): item is SessionMeta => item !== null);
      // Sort by most recent user activity, not creation time.
      list.sort((a, b) => b.lastMessageAt - a.lastMessageAt);
      return list;
    } catch (err) {
      reportError("SESSIONS:LIST_ERR", err, { projectId });
      return [];
    }
  });

  ipcMain.handle("sessions:delete", async (_event, projectId: string, sessionId: string) => {
    ownedPath(projectId,sessionId);
    if(process.env.NOMISMA_DESKTOP_MODE==='isolated')return {error:'研究历史不直接删除；请归档所属项目'};
    try {
      const filePath = getSessionFilePath(projectId, sessionId);
      const metaPath = getMetaFilePath(projectId, sessionId);

      // Delete both main file and sidecar, ignoring ENOENT
      await Promise.all([
        fs.promises.unlink(filePath).catch((err: NodeJS.ErrnoException) => {
          if (err.code !== "ENOENT") throw err;
        }),
        fs.promises.unlink(metaPath).catch((err: NodeJS.ErrnoException) => {
          if (err.code !== "ENOENT") throw err;
        }),
      ]);
      return { ok: true };
    } catch (err) {
      const message = reportError("SESSIONS:DELETE_ERR", err, { projectId, sessionId });
      return { error: message };
    }
  });

  ipcMain.handle("sessions:search", async (_event, { projectIds, query }: { projectIds: string[]; query: string }): Promise<SearchResult> => {
    try {
      const lowerQuery = query.toLowerCase();
      const messageResults: SearchResult["messageResults"] = [];
      const sessionResults: SearchResult["sessionResults"] = [];

      for (const projectId of projectIds) {
        ownedPath(projectId);
        const dir = path.join(getDataDir(), "sessions", projectId);
        try {
          await fs.promises.access(dir);
        } catch {
          continue;
        }

        const allFiles = await fs.promises.readdir(dir);
        const files = allFiles.filter((f) => f.endsWith(".json") && !f.endsWith(".meta.json"));
        for (const file of files) {
          const filePath = path.join(dir, file);
          try {
            ownedPath(projectId,file.slice(0,-5));
            const stat = await fs.promises.stat(filePath);
            if (stat.size > 5 * 1024 * 1024) continue;

            const raw = await fs.promises.readFile(filePath, "utf-8");
            const data = JSON.parse(raw);
            if(data.projectId!==projectId||data.id!==file.slice(0,-5))throw new Error('Session ownership mismatch');
            bindSession(data);
            const sessionTitle = data.title || "Untitled";
            const sessionId = data.id;

            if (sessionTitle.toLowerCase().includes(lowerQuery)) {
              sessionResults.push({
                sessionId,
                projectId,
                title: sessionTitle,
                createdAt: data.createdAt || 0,
              });
            }

            if (messageResults.length >= 10) continue;
            const messages = data.messages || [];
            for (const msg of messages) {
              if (messageResults.length >= 10) break;
              if (msg.role !== "user" && msg.role !== "assistant") continue;
              if (!msg.content || typeof msg.content !== "string") continue;

              const idx = msg.content.toLowerCase().indexOf(lowerQuery);
              if (idx === -1) continue;

              const start = Math.max(0, idx - 30);
              const end = Math.min(msg.content.length, idx + query.length + 50);
              let snippet = msg.content.slice(start, end);
              if (start > 0) snippet = "..." + snippet;
              if (end < msg.content.length) snippet = snippet + "...";

              messageResults.push({
                sessionId,
                projectId,
                sessionTitle,
                messageId: msg.id,
                snippet,
                timestamp: msg.timestamp || data.createdAt || 0,
              });
            }
          } catch {
            // Skip corrupted files
          }
        }
      }

      return { messageResults, sessionResults };
    } catch (err) {
      reportError("SESSIONS:SEARCH_ERR", err, { query });
      return { messageResults: [], sessionResults: [] };
    }
  });
}
