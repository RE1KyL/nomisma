import { renderToStaticMarkup } from "react-dom/server";
import type { EffectCallback } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ACPPermissionEvent, ACPTurnCompleteEvent } from "@/types/acp";
import type { PermissionRequest, UIMessage } from "@/types";
import { useACP } from "./useACP";

const mocks = vi.hoisted(() => ({
  effects: new Array<EffectCallback>(),
  setPermission: vi.fn<(value: PermissionRequest | null) => void>(),
  setProcessing: vi.fn<(value: boolean) => void>(),
  setMessages: vi.fn<(value: UIMessage[] | ((previous: UIMessage[]) => UIMessage[])) => void>(),
  session: { current: "session-a" },
  cancel: vi.fn(async (_id: string): Promise<{ error?: string }> => ({})),
  deny: vi.fn(async () => ({})), stop: vi.fn(),
  permissions: new Set<(event: ACPPermissionEvent) => void>(),
  completions: new Set<(event: ACPTurnCompleteEvent) => void>(),
}));

vi.mock("react", async (original) => ({
  ...await original<typeof import("react")>(),
  useEffect: (effect: EffectCallback) => { mocks.effects.push(effect); },
}));
vi.mock("@/lib/analytics", () => ({ captureException: vi.fn() }));
vi.mock("./useEngineBase", () => ({ useEngineBase: () => ({
  messages: [], setMessages: mocks.setMessages, isProcessing: true, setIsProcessing: mocks.setProcessing,
  isConnected: true, setIsConnected: vi.fn(), sessionInfo: null, setSessionInfo: vi.fn(),
  totalCost: 0, setTotalCost: vi.fn(), pendingPermission: {
    requestId: "req-a", toolName: "nomisma_health_check", toolInput: {}, toolUseId: "tool-a",
  }, setPendingPermission: mocks.setPermission, contextUsage: null, setContextUsage: vi.fn(),
  sessionIdRef: mocks.session, scheduleFlush: vi.fn(), cancelPendingFlush: vi.fn(),
}) }));

const approval: ACPPermissionEvent = {
  _sessionId: "session-a", sessionId: "agent-a", requestId: "req-a",
  toolCall: { toolCallId: "tool-a", title: "nomisma_health_check", kind: "other", rawInput: {} },
  options: [{ optionId: "allow-once", name: "Allow", kind: "allow_once" },
    { optionId: "reject-once", name: "Deny", kind: "reject_once" }],
};

function mount(withApproval = true) {
  const captured = new Array<ReturnType<typeof useACP>>();
  function Probe() {
    captured.push(useACP({ sessionId: "session-a", initialRawAcpPermission: withApproval ? approval : null }));
    return null;
  }
  renderToStaticMarkup(<Probe />);
  for (const effect of mocks.effects) effect();
  mocks.setPermission.mockClear(); mocks.setProcessing.mockClear();
  return captured[0];
}

beforeEach(() => {
  vi.clearAllMocks(); mocks.effects.length = 0; mocks.permissions.clear(); mocks.completions.clear();
  mocks.session.current = "session-a";
  mocks.cancel.mockResolvedValue({});
  vi.stubGlobal("window", { claude: { acp: {
    log: vi.fn(), cancel: mocks.cancel, stop: mocks.stop, respondPermission: mocks.deny,
    getConfigOptions: vi.fn(async () => ({})), getAvailableCommands: vi.fn(async () => ({})),
    onEvent: vi.fn(() => () => {}), onExit: vi.fn(() => () => {}),
    onPermissionRequest: (fn: (event: ACPPermissionEvent) => void) => { mocks.permissions.add(fn); return () => mocks.permissions.delete(fn); },
    onTurnComplete: (fn: (event: ACPTurnCompleteEvent) => void) => { mocks.completions.add(fn); return () => mocks.completions.delete(fn); },
  } } });
});

describe("ACP cancellation state transitions", () => {
  it("keeps running during approval, sends cancel not Deny/stop, and settles on cancelled", async () => {
    const api = mount();
    for (const fn of mocks.permissions) fn(approval);
    expect(mocks.setProcessing).not.toHaveBeenCalledWith(false);
    await api.interrupt();
    expect(mocks.cancel).toHaveBeenCalledWith("session-a");
    expect(mocks.deny).not.toHaveBeenCalled(); expect(mocks.stop).not.toHaveBeenCalled();
    expect(mocks.setProcessing).not.toHaveBeenCalledWith(false);
    expect(mocks.setPermission).toHaveBeenLastCalledWith(null);
    for (const fn of mocks.completions) fn({ _sessionId: "session-a", stopReason: "cancelled" });
    expect(mocks.setProcessing).toHaveBeenLastCalledWith(false);
    const pendingTool: UIMessage = { id: "pending-tool", role: "tool_call", content: "", toolName: "nomisma_health_check", timestamp: 0 };
    expect(mocks.setMessages.mock.calls.some(([update]) => typeof update === "function"
      && update([pendingTool])[0].toolResult?.status === "cancelled")).toBe(true);
    await api.respondPermission("deny");
    expect(mocks.deny).not.toHaveBeenCalled();
  });

  it("uses the same prompt cancel for an ordinary running task without approval", async () => {
    const api = mount(false);
    await api.interrupt();
    expect(mocks.cancel).toHaveBeenCalledWith("session-a");
    expect(mocks.stop).not.toHaveBeenCalled(); expect(mocks.deny).not.toHaveBeenCalled();
    for (const fn of mocks.completions) fn({ _sessionId: "session-a", stopReason: "cancelled" });
    expect(mocks.setProcessing).toHaveBeenLastCalledWith(false);
  });

  it("keeps Deny as a permission response without cancelling the prompt", async () => {
    const api = mount();
    await api.respondPermission("deny");
    expect(mocks.deny).toHaveBeenCalledWith("session-a", "req-a", "reject-once");
    expect(mocks.cancel).not.toHaveBeenCalled();
  });

  it("does not clear another session's UI when cancel acknowledgement arrives late", async () => {
    const api = mount();
    mocks.cancel.mockImplementationOnce(async () => { mocks.session.current = "session-b"; return {}; });
    await api.interrupt();
    expect(mocks.cancel).toHaveBeenCalledWith("session-a");
    expect(mocks.setPermission).not.toHaveBeenCalled();
    for (const fn of mocks.completions) fn({ _sessionId: "session-a", stopReason: "cancelled" });
    expect(mocks.setProcessing).not.toHaveBeenCalledWith(false);
  });

  it("leaves approval actionable when cancel fails", async () => {
    const api = mount(); mocks.cancel.mockResolvedValueOnce({ error: "test cancellation failure" });
    await api.interrupt();
    expect(mocks.setPermission).not.toHaveBeenCalledWith(null);
    expect(mocks.setProcessing).not.toHaveBeenCalledWith(false);
    await api.respondPermission("deny");
    expect(mocks.deny).toHaveBeenCalled();
  });

  it("does not insert a late cancellation exception into another session", async () => {
    const api = mount();
    mocks.cancel.mockImplementationOnce(async () => { mocks.session.current = "session-b"; throw new Error("late failure"); });
    await api.interrupt();
    expect(mocks.setMessages).not.toHaveBeenCalled();
    expect(mocks.setPermission).not.toHaveBeenCalled();
  });
});
