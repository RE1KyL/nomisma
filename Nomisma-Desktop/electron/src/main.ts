import { execSync } from "child_process";
import { app, BrowserWindow, clipboard, globalShortcut, ipcMain, Menu, session, systemPreferences } from "electron";
import path from "path";
import http from "http";
import contextMenu from "electron-context-menu";
import { getBootstrapMinWindowWidth } from "../../src/lib/layout-constants";

// Packaged .app bundles launched from Finder get a minimal PATH (/usr/bin:/bin).
// Inherit the user's shell PATH so child processes (SDK's `node`, git, etc.) resolve.
if (process.platform !== "win32") {
  try {
    const shell = process.env.SHELL || "/bin/zsh";
    const shellPath = execSync(`${shell} -ilc 'echo -n "$PATH"'`, {
      encoding: "utf8",
      timeout: 5000,
    });
    if (shellPath) process.env.PATH = shellPath;
  } catch {
    // Fall through — keep whatever PATH we already have
  }
}
import {installIpcBoundary,secureRendererSession,openExternalReviewed} from "./lib/desktop-security";
import { log } from "./lib/logger";
import { reportError } from "./lib/error-utils";
import { migrateFromOpenAcpUi } from "./lib/migration";
import { applyStartupMigrationPolicy } from "./lib/startup-migration-policy";
import { configureInstalledNomisma, installedNomisma } from "./lib/nomisma-runtime";
import { registerResearch } from "./ipc/research";
import { registerResearchTasks } from './ipc/research-tasks';
import {registerInfrastructure} from './ipc/infrastructure';
import { registerWorkspace } from './ipc/workspace';
import { glassEnabled, liquidGlass } from "./lib/glass";
import { initAutoUpdater, getIsInstallingUpdate } from "./lib/updater";
import { initPostHog, shutdownPostHog, reinitPostHog, captureEvent } from "./lib/posthog";
import { sessions } from "./ipc/claude-sessions";
import { acpSessions, getAcpAnalyticsPropertiesForSession } from "./ipc/acp-sessions";
import { terminals } from "./ipc/terminal";

// IPC module registrations
import * as spacesIpc from "./ipc/spaces";
import * as projectsIpc from "./ipc/projects";
import * as sessionsIpc from "./ipc/sessions";
import * as ccImportIpc from "./ipc/cc-import";
import * as filesIpc from "./ipc/files";
import * as claudeSessionsIpc from "./ipc/claude-sessions";
import * as titleGenIpc from "./ipc/title-gen";
import * as terminalIpc from "./ipc/terminal";
import * as gitIpc from "./ipc/git";
import * as agentRegistryIpc from "./ipc/agent-registry";
import * as acpSessionsIpc from "./ipc/acp-sessions";
import * as codexSessionsIpc from "./ipc/codex-sessions";
import * as mcpIpc from "./ipc/mcp";
import * as settingsIpc from "./ipc/settings";
import {register as registerModelProviders} from "./ipc/model-providers";
import * as jiraIpc from "./ipc/jira";
import { onSettingsChanged } from "./ipc/settings";

if (installedNomisma()) {
  configureInstalledNomisma();
} else if (app.isPackaged && process.env.NOMISMA_DESKTOP_MODE !== "isolated") {
  throw new Error("Start NOMISMA with its validated isolated launcher; direct launch is not configured yet.");
}

// --- Performance: Chromium/V8 flags (must be set before app.whenReady()) ---
app.commandLine.appendSwitch("enable-gpu-rasterization"); // force GPU raster for all content
app.commandLine.appendSwitch("enable-zero-copy"); // avoid CPU→GPU memory copies for tiles
app.commandLine.appendSwitch("ignore-gpu-blocklist"); // use GPU even on blocklisted hardware
app.commandLine.appendSwitch("enable-features", "CanvasOopRasterization"); // off-main-thread canvas

// --- Liquid Glass command-line switches ---
if (glassEnabled && process.env.NOMISMA_DESKTOP_MODE!=="isolated") {
  app.commandLine.appendSwitch("remote-debugging-port", "9222");
  app.commandLine.appendSwitch("remote-allow-origins", "*");
}

let mainWindow: BrowserWindow | null = null;

function getMainWindow(): BrowserWindow | null {
  return mainWindow;
}

function isMainRendererPermissionRequest(webContents: Electron.WebContents | null): boolean {
  return !!webContents && webContents.id === mainWindow?.webContents.id;
}

function createWindow(): void {
  const windowOptions: Electron.BrowserWindowConstructorOptions = {
    show: false,
    title: process.env.NOMISMA_FILE_TEST==='1'?'NOMISMA · LOCAL FILE VALIDATION':"NOMISMA — RESEARCH WORKSPACE",
    width: 1200,
    height: 800,
    // Matches the renderer's stricter island-layout minimum before first IPC sync,
    // including the extra Windows frame buffer.
    minWidth: getBootstrapMinWindowWidth(process.platform),
    minHeight: 600,
    // Packaged builds get the icon from the .app bundle / electron-builder config
    ...(!app.isPackaged && { icon: path.join(__dirname, "../../build/icon.png") }),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: false,
      sandbox: true,
      devTools: !glassEnabled,
      v8CacheOptions: "bypassHeatCheckAndEagerCompile", // cache compiled JS on first run — eliminates cold-start jank
    },
  };

  if (glassEnabled) {
    // macOS Tahoe+ with liquid glass
    windowOptions.titleBarStyle = "hidden";
    windowOptions.transparent = true;
    windowOptions.trafficLightPosition = { x: 19, y: 19 };
  } else if (process.platform === "win32") {
    // Windows: native Electron backgroundMaterial handles DWM mica/acrylic.
    // WebContents is automatically transparent (no transparent: true needed),
    // and the native title bar stays intact.
    windowOptions.autoHideMenuBar = true;
    windowOptions.backgroundMaterial = "mica";
  } else {
    // macOS without glass / Linux
    windowOptions.titleBarStyle = "hiddenInset";
    windowOptions.trafficLightPosition = { x: 19, y: 19 };
    windowOptions.backgroundColor = "#040404";
  }

  mainWindow = new BrowserWindow(windowOptions);
  mainWindow.webContents.on("will-attach-webview",event=>event.preventDefault());
  secureRendererSession(mainWindow.webContents.session,`http://localhost:${process.env.NOMISMA_DEV_SERVER_PORT??"5173"}`);
  if(process.env.NOMISMA_FILE_TEST==='1')mainWindow.on('page-title-updated',event=>event.preventDefault());

  mainWindow.once("ready-to-show", () => {
    mainWindow?.show();
  });

  contextMenu({
    window: mainWindow,
    showSearchWithGoogle: false,
    showLookUpSelection: false,
    showInspectElement: false,
  });

  const isDev = !app.isPackaged && process.env.NOMISMA_COMPILED_UI!=="1";
  if (isDev) {
    const configuredPort = process.env.NOMISMA_DEV_SERVER_PORT ?? "5173";
    if (!/^\d+$/.test(configuredPort) || Number(configuredPort) < 1024 || Number(configuredPort) > 65535) {
      throw new Error("NOMISMA_DEV_SERVER_PORT must be a local port between 1024 and 65535");
    }
    mainWindow.loadURL(`http://localhost:${configuredPort}`);
  } else {
    mainWindow.loadFile(path.join(__dirname, "../../dist/index.html"));
  }

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void openExternalReviewed(mainWindow,url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (url === mainWindow?.webContents.getURL()) return;
    event.preventDefault();
    void openExternalReviewed(mainWindow,url);
  });

  if (glassEnabled) {
    // macOS: apply liquid glass after content loads
    mainWindow.webContents.once("did-finish-load", () => {
      const glassId = liquidGlass!.addView(mainWindow!.getNativeWindowHandle(), {});
      if (glassId === -1) {
        log("GLASS", "addView returned -1 — native addon failed, glass will not be visible");
      } else {
        log("GLASS", `Liquid glass applied, viewId=${glassId}`);
      }
    });
  }
}

installIpcBoundary(getMainWindow);
if(process.env.NOMISMA_STATE_ROOT)process.env.NOMISMA_PRIVACY_SETTINGS=path.join(process.env.NOMISMA_STATE_ROOT,"privacy.v1.json");

// Renderer uses this to decide whether the transparency toggle is available.
ipcMain.handle("app:getGlassSupported", () => {
  return !!(glassEnabled || process.platform === "win32");
});

ipcMain.handle("clipboard:write-text", (_event, text: string) => {
  try {
    clipboard.writeText(text);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: reportError("CLIPBOARD_WRITE", err) };
  }
});

// Dynamic minimum window width — renderer calculates based on which panels are open.
// Also expands the window if it's currently smaller than the new minimum (e.g. Tasks
// panel appeared while at min size), so content never overflows off-screen.
ipcMain.on("app:set-min-width", (_event, minWidth: number) => {
  if (mainWindow && Number.isFinite(minWidth) && minWidth >= 600) {
    const clamped = Math.min(Math.round(minWidth), 4000);
    const [, minH] = mainWindow.getMinimumSize();
    mainWindow.setMinimumSize(clamped, minH);
    // Grow the window if it's currently smaller than the new minimum
    const [currentW, currentH] = mainWindow.getSize();
    if (currentW < clamped) {
      mainWindow.setSize(clamped, currentH);
    }
  }
});

// --- Register all IPC modules ---
spacesIpc.register();
registerResearch();
registerResearchTasks();
registerInfrastructure();
registerWorkspace(getMainWindow);
projectsIpc.register(getMainWindow);
sessionsIpc.register();
ccImportIpc.register();
filesIpc.register(getMainWindow);
claudeSessionsIpc.register(getMainWindow);
titleGenIpc.register();
if (process.env.NOMISMA_DESKTOP_MODE !== "isolated") terminalIpc.register(getMainWindow);
else ipcMain.handle("terminal:list", () => ({ terminals: [] }));
if (process.env.NOMISMA_DESKTOP_MODE !== "isolated") gitIpc.register();
agentRegistryIpc.register();
acpSessionsIpc.register(getMainWindow);
codexSessionsIpc.register(getMainWindow);
mcpIpc.register();
settingsIpc.register();
registerModelProviders(getMainWindow);
jiraIpc.register();

// Listen for analytics settings changes and reinitialize PostHog
let lastAnalyticsEnabled: boolean | undefined;
onSettingsChanged((settings) => {
  if (lastAnalyticsEnabled !== undefined && settings.analyticsEnabled !== lastAnalyticsEnabled) {
    lastAnalyticsEnabled = settings.analyticsEnabled;
    reinitPostHog().catch((err) => {
      reportError("POSTHOG", err, { context: "reinitialize" });
    });
  } else {
    lastAnalyticsEnabled = settings.analyticsEnabled;
  }
});

// --- Renderer→main analytics bridge ---
ipcMain.on("analytics:capture", (_event, eventName: string, properties?: Record<string, unknown>) => {
  const nextProperties: Record<string, unknown> = properties ? { ...properties } : {};
  const sessionId = typeof nextProperties.session_id === "string" ? nextProperties.session_id : null;
  delete nextProperties.session_id;

  if (nextProperties.engine === "acp" && sessionId) {
    Object.assign(nextProperties, getAcpAnalyticsPropertiesForSession(sessionId) ?? {});
  }

  captureEvent(eventName, nextProperties).catch(() => { /* non-fatal */ });
});

// --- DevTools in separate window via remote debugging ---
let devToolsWindow: BrowserWindow | null = null;

function openDevToolsWindow(): void {
  if (!glassEnabled) {
    mainWindow?.webContents.openDevTools({ mode: "detach" });
    return;
  }

  if (devToolsWindow && !devToolsWindow.isDestroyed()) {
    devToolsWindow.focus();
    return;
  }

  http.get("http://127.0.0.1:9222/json", (res) => {
    let body = "";
    res.on("data", (chunk: Buffer) => { body += chunk; });
    res.on("end", () => {
      try {
        const targets = JSON.parse(body) as Array<{ type: string; webSocketDebuggerUrl?: string }>;
        const page = targets.find((t) => t.type === "page");
        if (!page) {
          log("DEVTOOLS", "No debuggable page target found");
          return;
        }

        const wsUrl = page.webSocketDebuggerUrl;
        if (!wsUrl) {
          log("DEVTOOLS", "No webSocketDebuggerUrl in target");
          return;
        }

        const wsParam = encodeURIComponent(wsUrl.replace("ws://", ""));
        const fullUrl = `devtools://devtools/bundled/inspector.html?ws=${wsParam}`;

        devToolsWindow = new BrowserWindow({
          width: 1000,
          height: 700,
          title: "NOMISMA DevTools",
          webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
          },
        });

        devToolsWindow.loadURL(fullUrl);
        devToolsWindow.on("closed", () => {
          devToolsWindow = null;
        });

        log("DEVTOOLS", `Opened DevTools window: ${fullUrl}`);
      } catch (err) {
        reportError("DEVTOOLS_ERR", err, { context: "parse-targets" });
      }
    });
  }).on("error", (err) => {
    reportError("DEVTOOLS_ERR", err, { context: "remote-debugging" });
  });
}

// --- App lifecycle ---
// --- Speech dictation IPC ---
ipcMain.handle("speech:start-native-dictation", () => {
  if (process.platform === "darwin") {
    // Sends the macOS Cocoa selector to start native dictation in the focused text field
    Menu.sendActionToFirstResponder("startDictation:");
    return { ok: true };
  }
  return { ok: false, reason: "not-supported" };
});

ipcMain.handle("speech:get-platform", () => process.platform);

ipcMain.handle("speech:request-mic-permission", async () => {
  if (process.platform === "darwin") {
    const status = systemPreferences.getMediaAccessStatus("microphone");
    if (status === "granted") return { granted: true };
    const granted = await systemPreferences.askForMediaAccess("microphone");
    return { granted };
  }
  // Windows/Linux don't require Electron-level mic permission — getUserMedia handles it
  return { granted: true };
});

app.whenReady().then(() => {
  const migrationPolicy = applyStartupMigrationPolicy(
    process.env.NOMISMA_DESKTOP_MODE, app.getPath("userData"),
    process.env.NOMISMA_STATE_ROOT, migrateFromOpenAcpUi,
  );
  log("STARTUP_POLICY", migrationPolicy);

  createWindow();
  if (process.env.NOMISMA_DESKTOP_MODE !== "isolated") initAutoUpdater(getMainWindow);

  // Initialize PostHog analytics (if enabled in settings) — fire-and-forget to avoid blocking startup
  initPostHog().catch((err) => {
    reportError("POSTHOG", err, { context: "startup-init" });
  });

  // Allow microphone access for Whisper voice dictation (getUserMedia in renderer)
  session.defaultSession.setPermissionRequestHandler(
    (webContents, permission, callback) => {
      // Only grant privileged permissions to the app's main renderer, not webviews.
      if (isMainRendererPermissionRequest(webContents) && (permission === "media" || permission === "notifications")) {
        callback(true);
        return;
      }
      callback(false);
    },
  );
  session.defaultSession.setPermissionCheckHandler(
    (webContents, permission) => {
      if (isMainRendererPermissionRequest(webContents) && (permission === "media" || permission === "notifications")) {
        return true;
      }
      return false;
    },
  );

  // Set dock icon in dev mode — packaged builds get it from the .app bundle
  if (!app.isPackaged && process.platform === "darwin" && app.dock) {
    app.dock.setIcon(path.join(__dirname, "../../build/icon.png"));
  }

  const shortcuts = ["CommandOrControl+Alt+I", "F12", "CommandOrControl+Shift+J"];
  for (const shortcut of shortcuts) {
    const ok = globalShortcut.register(shortcut, () => {
      log("DEVTOOLS", `Shortcut ${shortcut} triggered`);
      openDevToolsWindow();
    });
    log("DEVTOOLS", `Register ${shortcut}: ${ok ? "OK" : "FAILED"}`);
  }
});

app.on("will-quit", (event) => {
  globalShortcut.unregisterAll();

  // When an update is being installed, let the updater control the quit lifecycle.
  // In that case, fire-and-forget PostHog shutdown and do not delay quit.
  if (getIsInstallingUpdate()) {
    void shutdownPostHog();
    return;
  }

  // For normal quits, delay process exit until PostHog has flushed pending events.
  event.preventDefault();

  shutdownPostHog()
    .catch((err) => {
      // Log and continue exit even if analytics shutdown fails
      reportError("POSTHOG", err, { context: "shutdown" });
    })
    .finally(() => {
      app.exit(0);
    });
});

app.on("window-all-closed", () => {
  for (const [sessionId, session] of sessions) {
    log("CLEANUP", `Closing session ${sessionId.slice(0, 8)}`);
    // Mark as stopping so event loops suppress expected teardown errors
    session.stopping = true;
    session.channel.close();
    session.queryHandle?.close();
  }
  sessions.clear();

  for (const [sessionId, entry] of acpSessions) {
    log("CLEANUP", `Stopping ACP session ${sessionId.slice(0, 8)}`);
    entry.process?.kill();
  }
  acpSessions.clear();

  log("CLEANUP", "Stopping all Codex sessions");
  codexSessionsIpc.stopAll();

  for (const [terminalId, term] of terminals) {
    log("CLEANUP", `Killing terminal ${terminalId.slice(0, 8)}`);
    term.pty.kill();
  }
  terminals.clear();

  // When quitAndInstall() is running, Squirrel.Mac needs to control the quit lifecycle.
  // Calling app.quit() here would kill the process before the update is applied on macOS.
  if (!getIsInstallingUpdate()) {
    app.quit();
  }
});
