# Upstream origin

- Repository: https://github.com/OpenSource03/harnss
- Fixed release: v0.21.5 (stable; 2026-03-19)
- Full commit: d9a5c62879308c2e517b6e02f6f5db7b3122b3de
- Local branch: nomisma/phase1b
- Remote: upstream (original URL retained)
- License: MIT; original LICENSE and copyright retained unchanged.
- Inspection date: 2026-10-31 (Asia/Shanghai)

The upstream default branch warns of substantial restructuring. The inspected
v0.22.0 beta releases were not selected. Keep pnpm-lock.yaml unchanged.

Nomisma reuses the upstream project/session UI, file panels, MCP configuration,
ACP client, tool rendering and permissions. No branding or layout changes belong
to this phase. `src/components/workspace` and `src/lib/engine` are not directories
in this tag; inspect the actual components/hooks and shared types instead.
CLAUDE.md references SDK docs that are absent from this checkout.

Compatibility: installed DSH 0.2.1-rc.2 provides official ACP v1 but no
session/load or transcript replay. Resume/close require capability-aware handling.
Upstream Windows spawn uses a shell; Nomisma's fixed executable must use direct
argv spawning. This is an early integration with a release-candidate DSH runtime,
not a claim of production stability or a security isolation boundary.

See ../Nomisma/docs/phase1b1_report.md for actual acceptance results and limitations.

## Phase 1B-2 — 2026-11-01

The earlier no-branding/no-lockfile-change statements describe Phase 1B-1 only.
This phase adds the Research Workspace shell, controlled file/workbook IPC,
Python/openpyxl workbook service and user-approved modern orbital N brand assets.
The lockfile adds only the pinned MIT React Data Grid package; original dependency
records are preserved. A narrow WeakMap compatibility fallback is required by the
fixed Electron 40 runtime. No DSH or Python environment upgrade was made.
ACP session/permission/cancel logic and upstream migration.ts remain byte-identical
to the pre-phase snapshot. Existing Nomisma changes remain in this checkout.
Terminal/Git registrations and upstream auto-update are disabled in isolated mode;
upstream source modules remain. The build hook resolves ASAR through builder's
own dependencies under pnpm and retains license/attribution documents.

Original Harnss author, LICENSE and copyright remain unchanged; README.upstream.md
preserves the pre-phase README. This app is a Harnss adaptation.
See ../Nomisma/docs/phase1b2_report.md for checks, risks and manual acceptance status.

该内容为codex书写

## Phase 1C / Phase 2A prerequisite — 2026-11-01

Adds project-centered Economic/Finance navigation and pages while reusing the
upstream session/chat controller, ACP client, permissions and file services.
Extends projects.json and session context in place with backed-up migration.
ACP entry points now bind trusted project context and preserve stable work-session
IDs on resume; the previously accepted cancel helper and useACP stream batching
fix remain byte-identical. Upstream migration.ts, original brand references,
LICENSE, dependencies and lockfile are not changed in this phase.
Full evidence: ../Nomisma/docs/PrePhase_report.md. Manual desktop checks pending.

该内容为codex书写
