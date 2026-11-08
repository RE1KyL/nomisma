"""Launch the existing desktop with its own renderer port and userData.

No dependency installation, credential access or system configuration changes.
Use --check for a read-only preflight without opening a window.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
NODE = Path("D:/node.exe")
ELECTRON = ROOT / "node_modules/electron/dist/electron.exe"
VITE = ROOT / "node_modules/vite/bin/vite.js"
PORT = 5178
PYTHON = Path("D:/AKSHARE/envs/nomisma/python.exe")


def require_path(item, *, directory=False, configuration=False):
    """Use stat/open so access errors are never reported as missing files."""
    missing = "CONFIG_MISSING" if configuration else "MISSING_PATH"
    try:
        info = item.stat()
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not valid:
            raise RuntimeError(f"[WRONG_PATH_TYPE] Expected {'directory' if directory else 'file'}: {item}")
        if directory:
            with os.scandir(item) as entries:
                next(entries, None)
        else:
            with item.open("rb") as stream:
                stream.read(1)
    except FileNotFoundError as error:
        raise RuntimeError(f"[{missing}] Required {'directory' if directory else 'file'} missing: {item}") from error
    except PermissionError as error:
        raise RuntimeError(f"[PERMISSION_DENIED] Cannot read: {item}; check this user's access permissions") from error
    except OSError as error:
        raise RuntimeError(f"[PATH_ACCESS_ERROR] Cannot access: {item}; OS error {error.errno}, Windows error {error.winerror if hasattr(error, 'winerror') else None}") from error


def read_config(item, expected_type):
    require_path(item, configuration=True)
    try:
        value = json.loads(item.read_text(encoding="utf-8-sig"))
    except PermissionError as error:
        raise RuntimeError(f"[PERMISSION_DENIED] Cannot read configuration: {item}") from error
    except (json.JSONDecodeError, UnicodeError) as error:
        raise RuntimeError(f"[CONFIG_INVALID] Invalid JSON or encoding: {item}") from error
    if not isinstance(value, expected_type):
        raise RuntimeError(f"[CONFIG_INVALID] Wrong JSON root type: {item}")
    return value


def same_path(value, expected):
    return isinstance(value, str) and Path(value).is_absolute() and Path(value).resolve() == expected.resolve()


def preflight():
    if not os.environ.get("LOCALAPPDATA") or not Path(os.environ["LOCALAPPDATA"]).is_absolute():
        raise RuntimeError("[ENVIRONMENT_MISSING] LOCALAPPDATA must name an absolute directory")
    config = read_config(ROOT / "nomisma-agent.example.json", dict)
    state_root = config.get("stateRoot")
    if (config.get("migrationPolicy") != "isolated" or not isinstance(state_root, str)
            or not Path(state_root).is_absolute() or Path(state_root).name.lower() != "nomisma"):
        raise RuntimeError("[CONFIG_INVALID] Runtime manifest must declare an absolute Nomisma stateRoot and isolated migrationPolicy")
    state = Path(state_root)
    required = [NODE, ELECTRON, VITE, ROOT / "electron/dist/main.js",
                ROOT / "electron/dist/preload.js", PYTHON]
    for item in required:
        require_path(item)
    project = ROOT.parent / "Nomisma"
    workspace = project / "workspace/projects/hs300_demo"
    userdata = state / "desktop-dev"
    dsh_home = state / "runtime/dsh-home"
    agents_home = state / "runtime/agents-home"
    for directory in [workspace, userdata, dsh_home, agents_home]:
        require_path(directory, directory=True)
    # Reject redirected state directories rather than silently reusing another app's data.
    for directory in [userdata, dsh_home, agents_home]:
        if os.path.normcase(str(directory.resolve())) != os.path.normcase(os.path.abspath(directory)):
            raise RuntimeError(f"[ISOLATION_ERROR] Independent state directory is redirected: {directory}")
    if not same_path(config.get("workspace"), workspace):
        raise RuntimeError("[CONFIG_INVALID] Unexpected Nomisma workspace in template; configuration preserved")
    data = userdata / "openacpui-data"
    projects = read_config(data / "projects.json", list)
    if not any(isinstance(p, dict) and p.get("id") == "nomisma-hs300" and same_path(p.get("path"), workspace) for p in projects):
        raise RuntimeError("[CONFIG_INVALID] Nomisma hs300 project registration is missing or points to another workspace")
    agents = read_config(data / "agents.json", list)
    agent = next((a for a in agents if isinstance(a, dict) and a.get("id") == "nomisma-dsh-acp"), None)
    launcher = project / "launcher/start_dsh_acp.cjs"
    require_path(launcher)
    if (agent is None or agent.get("engine") != "acp" or not same_path(agent.get("binary"), NODE)
            or not isinstance(agent.get("args"), list) or len(agent["args"]) != 1 or not same_path(agent["args"][0], launcher)):
        raise RuntimeError("[CONFIG_INVALID] Nomisma Agent is missing or does not use the fixed ACP launcher")
    agent_env = agent.get("env")
    if not isinstance(agent_env, dict) or not all(same_path(agent_env.get(k), p) for k, p in {
        "DSH_HOME": dsh_home, "DSH_AGENTS_HOME": agents_home, "NOMISMA_WORKSPACE": workspace,
        "NOMISMA_STATE_ROOT": state,
    }.items()):
        raise RuntimeError("[ISOLATION_ERROR] Nomisma Agent environment must use its independent homes and workspace")
    servers = read_config(data / "mcp/nomisma-hs300.json", list)
    server_file = project / "nomisma_tools/server.py"
    require_path(server_file)
    require_path(project / "nomisma_tools/data_tools.py")
    server = servers[0] if len(servers) == 1 and isinstance(servers[0], dict) else {}
    if (server.get("name") != "nomisma" or server.get("transport") != "stdio"
            or not same_path(server.get("command"), PYTHON) or not isinstance(server.get("args"), list)
            or len(server["args"]) != 1 or not same_path(server["args"][0], server_file)):
        raise RuntimeError("[CONFIG_INVALID] Project MCP must contain only the fixed Nomisma Python service")
    mcp_env = server.get("env")
    if (not isinstance(mcp_env, dict) or not same_path(mcp_env.get("NOMISMA_WORKSPACE"), workspace)
            or not same_path(mcp_env.get("NOMISMA_LOG_DIR"), state / "logs/python")):
        raise RuntimeError("[ISOLATION_ERROR] MCP workspace or audit directory is incorrect")
    read_config(data / "settings.json", dict)
    profile = dsh_home / "profiles/acp"
    package = read_config(profile / "package.json", dict)
    dsh_config = package.get("dsh")
    profile_config = dsh_config.get("profile") if isinstance(dsh_config, dict) else None
    if not isinstance(profile_config, dict) or profile_config.get("bundles") != ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-acp-app"]:
        raise RuntimeError("[CONFIG_INVALID] Independent DSH profile must use the official base and ACP bundles")
    for name in ["cordis.yml", "cordis.patch.yml"]:
        require_path(profile / name, configuration=True)
        if (profile / name).stat().st_size == 0:
            raise RuntimeError(f"[CONFIG_INVALID] Empty DSH profile configuration: {profile / name}")
    if "NOMISMA_DESKTOP_MODE" not in (ROOT / "electron/dist/main.js").read_text(encoding="utf8"):
        raise RuntimeError("[BUILD_OUTDATED] Rebuild Electron before launching with the isolated migration policy")
    probe_code = """import sys,json,importlib.metadata as m
packages = {}
missing = []
for name in ['pandas','numpy','openpyxl','akshare','mcp']:
    try:
        packages[name] = m.version(name)
    except m.PackageNotFoundError:
        missing.append(name)
print(json.dumps({'python':sys.version_info[:2],'environment':sys.prefix,'packages':packages,'missing':missing}))
"""
    try:
        probe = subprocess.run([str(PYTHON), "-B", "-c", probe_code], capture_output=True, text=True,
                               timeout=15, check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        environment = json.loads(probe.stdout)
    except PermissionError as error:
        raise RuntimeError(f"[PERMISSION_DENIED] Cannot execute Nomisma Python: {PYTHON}") from error
    except (subprocess.SubprocessError, ValueError) as error:
        raise RuntimeError("[PYTHON_ENVIRONMENT_ERROR] Nomisma Python or required package metadata check failed") from error
    if not isinstance(environment, dict):
        raise RuntimeError("[PYTHON_ENVIRONMENT_ERROR] Invalid response from the fixed Python environment probe")
    if environment.get("missing"):
        raise RuntimeError("[DEPENDENCY_MISSING] Nomisma Python packages missing: " + ", ".join(environment["missing"]))
    if environment.get("python") != [3, 11] or not same_path(environment.get("environment"), PYTHON.parent):
        raise RuntimeError("[PYTHON_ENVIRONMENT_ERROR] Expected the dedicated nomisma Python 3.11 environment")
    print("Preflight passed. Independent userData:", userdata, flush=True)
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    state = preflight()
    if args.check:
        return
    env = os.environ.copy()
    for key in list(env):
        if key.upper() in {"NODE_OPTIONS", "ELECTRON_RUN_AS_NODE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"} or any(word in key.upper() for word in ("API_KEY","APIKEY","TOKEN","SECRET","PASSWORD")):
            del env[key]
    env["NOMISMA_COMPILED_UI"] = "1"
    env["NOMISMA_DEV_SERVER_PORT"] = str(PORT)
    env["NOMISMA_PROJECT_ROOT"] = str(ROOT.parent / "Nomisma")
    env["NOMISMA_DESKTOP_MODE"] = "isolated"
    env["NOMISMA_FILE_TEST"] = "0"
    env["NOMISMA_STATE_ROOT"] = str(state)
    log_dir = state / "logs/desktop-launch"
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    children = []
    try:
        with (log_dir / f"{run_id}-vite.log").open("x", encoding="utf8") as vite_log, \
                (log_dir / f"{run_id}-electron.log").open("x", encoding="utf8") as electron_log:
            # Formal launches serve only compiled local files, not a development server.
            require_path(ROOT / "dist/index.html")
            app = subprocess.Popen([str(ELECTRON), str(ROOT), f"--user-data-dir={state / 'desktop-dev'}"],
                                   cwd=ROOT, env=env, stdout=electron_log, stderr=subprocess.STDOUT, creationflags=flags)
            children.append(app)
            print("Nomisma desktop started. Keep this launcher open until you close the desktop.", flush=True)
            print("Launch logs:", log_dir, flush=True)
            app.wait()
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    print("This launch has stopped; its saved sessions are retained.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Launch stopped: {error}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("Launcher interrupted.")
        raise SystemExit(130)
