import { describe, expect, it, vi } from "vitest";
import { cancelAcpPrompt } from "../acp-cancel";

describe("ACP prompt cancellation", () => {
  it("sends cancel before resolving approval and touches only the current session", async () => {
    const order: string[] = [];
    const resolveA = vi.fn(() => { order.push("approval-cancelled"); });
    const resolveB = vi.fn();
    const a = new Map([["req-a", { resolve: resolveA }]]);
    const b = new Map([["req-b", { resolve: resolveB }]]);
    const cancel = vi.fn(async () => { order.push("prompt-cancel"); });
    await cancelAcpPrompt({ cancel }, "agent-a", a);
    expect(cancel).toHaveBeenCalledWith({ sessionId: "agent-a" });
    expect(order).toEqual(["prompt-cancel", "approval-cancelled"]);
    expect(resolveA).toHaveBeenCalledWith({ outcome: { outcome: "cancelled" } });
    expect(a.size).toBe(0); expect(b.size).toBe(1); expect(resolveB).not.toHaveBeenCalled();
  });

  it("retains an actionable approval if sending cancel fails", async () => {
    const resolve = vi.fn(); const pending = new Map([["req", { resolve }]]);
    await expect(cancelAcpPrompt({ cancel: async () => { throw new Error("send failed"); } }, "a", pending)).rejects.toThrow("send failed");
    expect(resolve).not.toHaveBeenCalled(); expect(pending.size).toBe(1);
  });
});
