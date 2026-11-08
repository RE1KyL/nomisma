import { describe, expect, it } from "vitest";
import { pickAutoResponseOption } from "./acp-adapter";
import { nomismaPermissionDetail } from "./nomisma-permission-detail";

const options = [{ optionId: "once", kind: "allow_once" }, { optionId: "always", kind: "allow_always" }];
describe("Nomisma per-request approval", () => {
  for (const behavior of ["ask", "auto_accept", "allow_all"] as const) {
    it(`keeps foreground and background requests pending in ${behavior}`, () => {
      expect(pickAutoResponseOption(options, behavior, true)).toBeNull();
    });
  }
  it("keeps upstream non-Nomisma behavior", () => {
    expect(pickAutoResponseOption(options, "allow_all")).toBe("always");
    expect(pickAutoResponseOption(options, "auto_accept")).toBe("once");
  });
  it("displays the actual SHA and complete finite cleaning parameters", () => {
    const input = { dataset: { dataset_id: "ds_test", sha256: "a".repeat(64) }, parameters: { steps: [{ operation: "drop_missing", subset: ["close"] }] } };
    expect(JSON.parse(nomismaPermissionDetail("nomisma_dataset_clean", input)!)).toEqual(input);
  });
  it("does not alter other tool details", () => {
    expect(nomismaPermissionDetail("Read", { file_path: "test.md" })).toBeNull();
  });
});
