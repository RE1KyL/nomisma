/** Show actual request arguments, never a model-written approval summary. */
export function nomismaPermissionDetail(tool: string, input: Record<string, unknown>): string | null {
  if (!tool.includes("nomisma_") && !("dataset" in input) && !("parameters" in input)) return null;
  try {
    const raw = JSON.stringify(input, null, 2);
    // Published Python schemas cap arguments at 16 KiB, pretty printing can
    // expand them. Do not silently truncate the part being approved.
    return raw.length <= 100_000 ? raw : "参数过大，无法完整展示；请拒绝本次调用并缩小请求。";
  } catch {
    return "参数无法完整展示；请拒绝本次调用。";
  }
}
