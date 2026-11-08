import path from "path";

/** Nomisma selects independent state explicitly; upstream migration is unchanged. */
export function applyStartupMigrationPolicy(
  mode: string | undefined,
  userData: string,
  stateRoot: string | undefined,
  migrateUpstream: () => void,
): "isolated" | "upstream" {
  const expected = stateRoot && path.isAbsolute(stateRoot) && path.basename(stateRoot).toLowerCase() === "nomisma"
    ? path.resolve(stateRoot, "desktop-dev") : undefined;
  const normalize = (value: string): string => process.platform === "win32" ? path.resolve(value).toLowerCase() : path.resolve(value);
  const isNomismaPath = expected !== undefined && normalize(userData) === normalize(expected);
  if (mode === "isolated") {
    if (!isNomismaPath) throw new Error("Nomisma isolated mode requires its independent desktop-dev userData path");
    // Do not inspect/write migration flags or read/import any old application's data.
    return "isolated";
  }
  if (mode !== undefined) throw new Error("Unsupported NOMISMA_DESKTOP_MODE");
  if (path.basename(userData).toLowerCase() === "desktop-dev" && path.basename(path.dirname(userData)).toLowerCase() === "nomisma") {
    throw new Error("Use the Nomisma launcher to select its explicit isolated migration policy");
  }
  migrateUpstream();
  return "upstream";
}
