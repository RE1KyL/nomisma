import type { ClientSideConnection } from "@agentclientprotocol/sdk";

/** Existing ACP prompt cancellation, ordered before its pending approval replies. */
export async function cancelAcpPrompt(
  connection: Pick<ClientSideConnection, "cancel">,
  agentSessionId: string,
  pendingPermissions: Map<string, { resolve: (response: unknown) => void }>,
): Promise<void> {
  await connection.cancel({ sessionId: agentSessionId });
  for (const { resolve } of pendingPermissions.values()) {
    resolve({ outcome: { outcome: "cancelled" } });
  }
  pendingPermissions.clear();
}
