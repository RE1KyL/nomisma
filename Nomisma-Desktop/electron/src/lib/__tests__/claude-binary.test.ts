import { beforeEach, describe, expect, it, vi } from "vitest";
import path from "node:path";

const windows = process.platform === "win32";
const customPath = windows ? "C:\\opt\\bin\\claude.exe" : "/opt/bin/claude";
const envPath = windows ? "C:\\env\\claude.exe" : "/env/claude";
const pathBinary = windows ? "C:\\bin\\claude.exe" : "/usr/local/bin/claude";
const localShim = path.join("/Users/tester", ".local", "bin", "claude");

const {
  mockAccessSync,
  mockExecFileSync,
  mockGetAppSetting,
  mockGetCliPath,
  mockLog,
  mockSpawn,
} = vi.hoisted(() => ({
  mockAccessSync: vi.fn(),
  mockExecFileSync: vi.fn(),
  mockGetAppSetting: vi.fn<(key: string) => string>((key: string) => {
    if (key === "claudeBinarySource") return "auto";
    if (key === "claudeCustomBinaryPath") return "";
    return "Harnss";
  }),
  mockGetCliPath: vi.fn(() => "/app.asar.unpacked/node_modules/@anthropic-ai/claude-agent-sdk/cli.js"),
  mockLog: vi.fn(),
  mockSpawn: vi.fn(),
}));

vi.mock("fs", () => ({
  default: {
    accessSync: mockAccessSync,
    constants: { X_OK: 1 },
  },
}));

vi.mock("os", () => ({
  default: {
    homedir: () => "/Users/tester",
  },
}));

vi.mock("child_process", () => ({
  execFileSync: mockExecFileSync,
  spawn: mockSpawn,
}));

vi.mock("../app-settings", () => ({
  getAppSetting: mockGetAppSetting,
}));

vi.mock("../sdk", () => ({
  getCliPath: mockGetCliPath,
}));

vi.mock("../logger", () => ({
  log: mockLog,
}));

function allowExecutable(...filePaths: string[]): void {
  mockAccessSync.mockImplementation((candidate: string) => {
    if (filePaths.includes(candidate)) return;
    throw new Error("missing");
  });
}

async function loadModule() {
  vi.resetModules();
  return import("../claude-binary");
}

describe("claude binary resolution", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    mockAccessSync.mockReset();
    mockExecFileSync.mockReset();
    mockGetAppSetting.mockReset();
    mockGetAppSetting.mockImplementation((key: string): string => {
      if (key === "claudeBinarySource") return "auto";
      if (key === "claudeCustomBinaryPath") return "";
      return "Harnss";
    });
    mockGetCliPath.mockReset();
    mockGetCliPath.mockReturnValue("/app.asar.unpacked/node_modules/@anthropic-ai/claude-agent-sdk/cli.js");
    mockLog.mockReset();
    mockSpawn.mockReset();
  });

  it("uses a valid custom executable path", async () => {
    mockGetAppSetting.mockImplementation((key: string): string => {
      if (key === "claudeBinarySource") return "custom";
      if (key === "claudeCustomBinaryPath") return customPath;
      return "Harnss";
    });
    allowExecutable(customPath);

    const mod = await loadModule();

    await expect(mod.getClaudeBinaryPath()).resolves.toBe(customPath);
  });

  it("prefers the env override in auto mode", async () => {
    vi.stubEnv("CLAUDE_CODE_CLI_PATH", envPath);
    allowExecutable(envPath);

    const mod = await loadModule();

    await expect(mod.getClaudeBinaryPath({ installIfMissing: false })).resolves.toBe(envPath);
  });

  it("uses the native shim on POSIX and ignores that convention on Windows", async () => {
    allowExecutable(localShim, pathBinary);
    mockExecFileSync.mockReturnValue(pathBinary + "\n");

    const mod = await loadModule();

    await expect(mod.getClaudeBinaryPath({ installIfMissing: false })).resolves.toBe(windows ? pathBinary : localShim);
    if (windows) expect(mockAccessSync).not.toHaveBeenCalledWith(localShim, expect.anything());
    else expect(mockExecFileSync).not.toHaveBeenCalled();
  });

  it("falls back to PATH lookup when the shim is missing", async () => {
    mockExecFileSync.mockImplementation((command: string) => {
      if (command === (windows ? "where" : "which")) return pathBinary + "\n";
      throw new Error("unexpected");
    });
    allowExecutable(pathBinary);

    const mod = await loadModule();

    await expect(mod.getClaudeBinaryPath({ installIfMissing: false })).resolves.toBe(pathBinary);
  });

  it("uses the sdk cli fallback in auto mode when native resolution fails", async () => {
    mockExecFileSync.mockImplementation(() => {
      throw new Error("missing");
    });
    allowExecutable("/app.asar.unpacked/node_modules/@anthropic-ai/claude-agent-sdk/cli.js");

    const mod = await loadModule();

    await expect(mod.getClaudeBinaryPath({ installIfMissing: false, allowSdkFallback: true })).resolves.toBe(
      "/app.asar.unpacked/node_modules/@anthropic-ai/claude-agent-sdk/cli.js",
    );
    expect(mockLog).toHaveBeenCalledWith(
      "CLAUDE_BINARY_SELECTED",
      "strategy=sdk-fallback path=/app.asar.unpacked/node_modules/@anthropic-ai/claude-agent-sdk/cli.js",
    );
  });

  it("reports status without triggering install", async () => {
    allowExecutable(windows ? pathBinary : localShim);
    if (windows) mockExecFileSync.mockReturnValue(pathBinary + "\n");
    const mod = await loadModule();

    expect(mod.getClaudeBinaryStatus()).toEqual({
      installed: true,
      installing: false,
    });
  });

  it("returns a version when the sdk fallback path is a script", async () => {
    mockExecFileSync.mockImplementation((command: string, args: string[]) => {
      if (command === process.execPath) {
        expect(args).toEqual(["/app.asar.unpacked/node_modules/@anthropic-ai/claude-agent-sdk/cli.js", "--version"]);
        return "2.1.70\n";
      }
      throw new Error("unexpected");
    });
    allowExecutable("/app.asar.unpacked/node_modules/@anthropic-ai/claude-agent-sdk/cli.js");

    const mod = await loadModule();

    await expect(mod.getClaudeVersion()).resolves.toBe("2.1.70");
  });

  it("exposes binary metadata for auto-detected paths", async () => {
    allowExecutable(windows ? pathBinary : localShim);
    if (windows) mockExecFileSync.mockReturnValue(pathBinary + "\n");

    const mod = await loadModule();

    expect(mod.getClaudeBinaryMetadata({ installIfMissing: false, allowSdkFallback: true })).toEqual({
      path: windows ? pathBinary : localShim,
      strategy: windows ? "path" : "known",
      source: "auto",
    });
  });

  it("reads a provided binary path version directly", async () => {
    mockExecFileSync.mockImplementation((command: string, args: string[]) => {
      if (command === "/Users/tester/.local/bin/claude") {
        expect(args).toEqual(["--version"]);
        return "1.2.3\n";
      }
      throw new Error("unexpected");
    });

    const mod = await loadModule();

    await expect(mod.getClaudeVersion("/Users/tester/.local/bin/claude")).resolves.toBe("1.2.3");
  });
});
