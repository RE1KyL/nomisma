"""Initialize only new Nomisma state files; never read or copy credential values."""
import json
import os
from pathlib import Path
import time

DESKTOP = Path(__file__).resolve().parents[1]
PROJECT = DESKTOP.parent / "Nomisma"
WORKSPACE = PROJECT / "workspace/projects/hs300_demo"
# Persist the actual filesystem path, including any packaged-app redirection.
STATE = (Path(os.environ["LOCALAPPDATA"]) / "Nomisma").resolve()
PROFILE = STATE / "runtime/dsh-home/profiles/acp"
USERDATA = STATE / "desktop-dev"


def create(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Preserving existing file: {path}")
    with path.open("x", encoding="utf8") as stream:
        stream.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))


def main():
    if USERDATA.exists() or PROFILE.exists():
        raise SystemExit("Existing Nomisma configuration found; no files changed")
    assert WORKSPACE.is_dir()
    launcher = PROJECT / "launcher"
    patch = [
        {"id": "acp", "config": {"provider": "deepseek-account", "model": "deepseek-flash"}},
        {"id": "credentials", "disabled": True},
    ]
    disabled = ["tool-working-directory", "tool-bash", "tool-pwsh", "tool-jobs", "tool-fs",
                "tool-fs-search", "tool-skill", "tool-subagent-control", "tool-subagent-list-agents",
                "tool-subagent", "tool-subagent-fork", "tool-workflow", "tool-todo", "tool-goal", "tool-web",
                "workflow-ptc", "ptc-runtime", "subagent-spawn-in-process", "subagent-fork-in-process",
                "session-telemetry-otel", "skill-filesystem", "agent-instructions"]
    patch.extend({"id": item, "disabled": True} for item in disabled)
    patch.append({"insert": [
        {"id": "nomisma-readonly-credentials", "name": str(launcher / "readonly_credentials.mjs"),
         "config": {"path": str(Path.home() / ".dsh/.credentials.yaml"), "watch": False}},
        {"id": "nomisma-health-policy", "name": str(launcher / "health_policy.mjs")},
    ]})
    create(PROFILE / "package.json", {"name": "nomisma-acp-profile", "private": True, "dependencies": {},
         "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-acp-app"]}}})
    create(PROFILE / "cordis.yml", "[]\n")
    create(PROFILE / "cordis.patch.yml", patch)
    (STATE / "runtime/agents-home").mkdir(parents=True, exist_ok=True)
    create(USERDATA / "openacpui-data/settings.json", {"analyticsEnabled": False, "allowPrereleaseUpdates": False})
    agent = {"id": "nomisma-dsh-acp", "name": "Nomisma DSH ACP", "engine": "acp", "binary": "D:\\node.exe",
             "args": [str(launcher / "start_dsh_acp.cjs")], "icon": "activity",
             "env": {"DSH_HOME": str(STATE / "runtime/dsh-home"),
                     "DSH_AGENTS_HOME": str(STATE / "runtime/agents-home"),
                     "NOMISMA_STATE_ROOT": str(STATE),
                     "DSH_TELEMETRY_DISABLED": "1", "NOMISMA_WORKSPACE": str(WORKSPACE), "PYTHONUTF8": "1"}}
    create(USERDATA / "openacpui-data/agents.json", [agent])
    create(USERDATA / "openacpui-data/projects.json", [{"id": "nomisma-hs300", "name": "hs300_demo",
           "path": str(WORKSPACE), "createdAt": int(time.time() * 1000)}])
    mcp = [{"name": "nomisma", "transport": "stdio", "command": "D:\\AKSHARE\\envs\\nomisma\\python.exe",
            "args": [str(PROJECT / "nomisma_tools/server.py")],
            "env": {"PYTHONUTF8": "1", "NOMISMA_WORKSPACE": str(WORKSPACE),
                    "NOMISMA_LOG_DIR": str(STATE / "logs/python")}}]
    create(USERDATA / "openacpui-data/mcp/nomisma-hs300.json", mcp)
    create(DESKTOP / "nomisma-agent.example.json", {"stateRoot": str(STATE), "migrationPolicy": "isolated",
           "agent": agent, "workspace": str(WORKSPACE), "mcpServers": mcp})
    print("Created independent Nomisma state:", STATE)


if __name__ == "__main__":
    main()
