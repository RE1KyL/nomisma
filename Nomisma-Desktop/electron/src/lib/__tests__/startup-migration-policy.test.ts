import fs from "fs";
import os from "os";
import path from "path";
import { describe, expect, it, vi } from "vitest";
import { applyStartupMigrationPolicy } from "../startup-migration-policy";

describe("Nomisma startup migration policy", () => {
  const stateRoot = path.resolve("nomisma-policy-test", "Local", "Nomisma");
  const userData = path.join(stateRoot, "desktop-dev");

  it("keeps upstream behavior for an ordinary Harnss launch", () => {
    const migrate = vi.fn();
    expect(applyStartupMigrationPolicy(undefined, path.resolve("Harnss"), stateRoot, migrate)).toBe("upstream");
    expect(migrate).toHaveBeenCalledTimes(1);
  });

  it("rejects isolated mode pointing at original Harnss userData", () => {
    const migrate = vi.fn();
    expect(() => applyStartupMigrationPolicy("isolated", path.resolve("Harnss"), stateRoot, migrate)).toThrow("independent");
    expect(migrate).not.toHaveBeenCalled();
  });

  it("rejects missing explicit state root in isolated mode", () => {
    const migrate = vi.fn();
    expect(() => applyStartupMigrationPolicy("isolated", userData, undefined, migrate)).toThrow("independent");
    expect(migrate).not.toHaveBeenCalled();
  });

  it("rejects direct launch into Nomisma state without explicit policy", () => {
    const migrate = vi.fn();
    expect(() => applyStartupMigrationPolicy(undefined, userData, stateRoot, migrate)).toThrow("launcher");
    expect(migrate).not.toHaveBeenCalled();
  });

  it("rejects an unknown mode rather than importing old data", () => {
    const migrate = vi.fn();
    expect(() => applyStartupMigrationPolicy("unknown", userData, stateRoot, migrate)).toThrow("Unsupported");
    expect(migrate).not.toHaveBeenCalled();
  });

  it("does not import existing old data or create a marker when isolated", () => {
    const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "nomisma-policy-test-"));
    try {
      const local = path.join(tempRoot, "Local");
      const independent = path.join(local, "Nomisma", "desktop-dev");
      const oldData = path.join(tempRoot, "OpenACP UI", "openacpui-data");
      fs.mkdirSync(independent, { recursive: true });
      fs.mkdirSync(oldData, { recursive: true });
      const oldFile = path.join(oldData, "old-settings.json");
      fs.writeFileSync(oldFile, "test-only old data");
      const migrate = vi.fn(() => fs.cpSync(oldData, independent, { recursive: true }));
      expect(applyStartupMigrationPolicy("isolated", independent, path.join(local, "Nomisma"), migrate)).toBe("isolated");
      expect(migrate).not.toHaveBeenCalled();
      expect(fs.readdirSync(independent)).toEqual([]);
      expect(fs.readFileSync(oldFile, "utf8")).toBe("test-only old data");
    } finally {
      if (path.dirname(path.resolve(tempRoot)) !== path.resolve(os.tmpdir()) || !path.basename(tempRoot).startsWith("nomisma-policy-test-")) {
        throw new Error("Refusing to clean an unexpected test directory");
      }
      fs.rmSync(tempRoot, { recursive: true });
    }
  });
});
