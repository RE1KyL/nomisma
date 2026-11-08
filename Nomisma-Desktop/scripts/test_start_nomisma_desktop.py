"""Isolated startup-gate regression tests; never launch Electron or touch user state."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("nomisma_start", Path(__file__).with_name("start-nomisma-desktop.py"))
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class StartupPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nomisma-startup-test-")
        self.base = Path(self.temp.name).resolve()
        self.assertEqual(self.base.parent, Path(tempfile.gettempdir()).resolve())
        self.root = self.base / "Nomisma-Desktop"
        self.project = self.base / "Nomisma"
        self.state = self.base / "AppData/Local/Nomisma"
        self.workspace = self.project / "workspace/projects/hs300_demo"
        self.data = self.state / "desktop-dev/openacpui-data"
        self.profile = self.state / "runtime/dsh-home/profiles/acp"
        self.python = self.base / "envs/nomisma/python.exe"
        self.node = self.base / "node.exe"
        self.server = self.project / "nomisma_tools/server.py"
        self.agent_launcher = self.project / "launcher/start_dsh_acp.cjs"
        for folder in [self.workspace, self.data, self.profile, self.state / "runtime/agents-home"]:
            folder.mkdir(parents=True)
        for file in [self.node, self.python, self.root / "node_modules/electron/dist/electron.exe",
                     self.root / "node_modules/vite/bin/vite.js", self.root / "electron/dist/preload.js",
                     self.server, self.server.with_name("data_tools.py"), self.agent_launcher]:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("test fixture", encoding="utf8")
        (self.root / "electron/dist/main.js").write_text("NOMISMA_DESKTOP_MODE", encoding="utf8")
        self.write(self.root / "nomisma-agent.example.json", {"workspace": str(self.workspace),
                   "stateRoot": str(self.state), "migrationPolicy": "isolated"})
        self.write(self.data / "projects.json", [{"id": "nomisma-hs300", "path": str(self.workspace)}])
        self.agent = {"id": "nomisma-dsh-acp", "engine": "acp", "binary": str(self.node),
                      "args": [str(self.agent_launcher)], "env": {
                          "DSH_HOME": str(self.state / "runtime/dsh-home"),
                          "DSH_AGENTS_HOME": str(self.state / "runtime/agents-home"),
                          "NOMISMA_STATE_ROOT": str(self.state),
                          "NOMISMA_WORKSPACE": str(self.workspace)}}
        self.write(self.data / "agents.json", [self.agent])
        self.server_config = {"name": "nomisma", "transport": "stdio", "command": str(self.python),
                              "args": [str(self.server)], "env": {"NOMISMA_WORKSPACE": str(self.workspace),
                              "NOMISMA_LOG_DIR": str(self.state / "logs/python")}}
        self.write(self.data / "mcp/nomisma-hs300.json", [self.server_config])
        self.write(self.data / "settings.json", {})
        self.write(self.profile / "package.json", {"dsh": {"profile": {"bundles": [
            "@deepseek-ai/dsh-base", "@deepseek-ai/dsh-acp-app"]}}})
        for name in ["cordis.yml", "cordis.patch.yml"]:
            (self.profile / name).write_text("[]\n", encoding="utf8")
        self.constants = patch.multiple(launcher, ROOT=self.root, NODE=self.node,
                                       PYTHON=self.python, ELECTRON=self.root / "node_modules/electron/dist/electron.exe",
                                       VITE=self.root / "node_modules/vite/bin/vite.js")
        self.constants.start()
        self.environment = patch.dict(os.environ, {"LOCALAPPDATA": str(self.state.parent)})
        self.environment.start()
        self.probe = patch.object(launcher.subprocess, "run", return_value=subprocess.CompletedProcess([], 0,
            stdout=json.dumps({"python": [3, 11], "environment": str(self.python.parent), "packages": {}, "missing": []})))
        self.probe.start()

    def tearDown(self):
        self.probe.stop()
        self.environment.stop()
        self.constants.stop()
        # Delete only this test's verified absolute temporary directory.
        self.assertEqual(self.base.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(self.base.name.startswith("nomisma-startup-test-"))
        self.temp.cleanup()

    def write(self, file, value):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(value), encoding="utf8")

    def test_marker_absent_complete_configuration_passes_without_process_launch(self):
        marker = self.state / "desktop-dev/.harnss-migrated"
        self.assertFalse(marker.exists())
        with patch.object(launcher.subprocess, "Popen") as launch, patch.object(launcher.sys, "argv", ["launcher", "--check"]):
            with contextlib.redirect_stdout(io.StringIO()):
                launcher.main()
            launch.assert_not_called()
        self.assertFalse(marker.exists())

    def test_missing_mcp_blocks_before_any_process(self):
        (self.data / "mcp/nomisma-hs300.json").unlink()
        with patch.object(launcher.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(RuntimeError, "CONFIG_MISSING.*nomisma-hs300.json"):
                launcher.preflight()
            launch.assert_not_called()

    def test_explicit_state_root_survives_different_process_localappdata(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.base / "OtherLocal")}):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(launcher.preflight(), self.state)

    def test_launch_plan_sets_isolated_mode_without_marker(self):
        process = MagicMock()
        process.poll.return_value = None
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.status = 200
        with patch.object(launcher.subprocess, "Popen", return_value=process) as launch, \
                patch.object(launcher.socket, "socket"), \
                patch.object(launcher.urllib.request, "build_opener", return_value=opener), \
                patch.object(launcher.sys, "argv", ["launcher"]), contextlib.redirect_stdout(io.StringIO()):
            launcher.main()
        self.assertEqual(launch.call_count, 2)
        app_call = launch.call_args_list[1]
        self.assertIn(f"--user-data-dir={self.state / 'desktop-dev'}", app_call.args[0])
        self.assertEqual(app_call.kwargs["env"]["NOMISMA_DESKTOP_MODE"], "isolated")
        self.assertEqual(app_call.kwargs["env"]["NOMISMA_STATE_ROOT"], str(self.state))
        self.assertFalse((self.state / "desktop-dev/.harnss-migrated").exists())

    def test_missing_state_manifest_is_reported_as_configuration_missing(self):
        (self.root / "nomisma-agent.example.json").unlink()
        with self.assertRaisesRegex(RuntimeError, "CONFIG_MISSING.*nomisma-agent.example.json"):
            launcher.preflight()

    def test_missing_agent_registration_blocks(self):
        self.write(self.data / "agents.json", [])
        with self.assertRaisesRegex(RuntimeError, "CONFIG_INVALID.*Agent"):
            launcher.preflight()

    def test_foreign_agent_home_blocks(self):
        self.agent["env"]["DSH_HOME"] = str(self.base / "original-qq-home")
        self.write(self.data / "agents.json", [self.agent])
        with self.assertRaisesRegex(RuntimeError, "ISOLATION_ERROR"):
            launcher.preflight()

    def test_additional_mcp_blocks(self):
        self.write(self.data / "mcp/nomisma-hs300.json", [self.server_config, {"name": "qq"}])
        with self.assertRaisesRegex(RuntimeError, "CONFIG_INVALID.*MCP"):
            launcher.preflight()

    def test_invalid_json_is_not_reported_as_missing(self):
        (self.data / "projects.json").write_text("{", encoding="utf8")
        with self.assertRaisesRegex(RuntimeError, "CONFIG_INVALID.*projects.json"):
            launcher.preflight()

    def test_permission_denied_is_not_reported_as_missing(self):
        original_stat = Path.stat
        denied = self.data / "settings.json"

        def restricted_stat(file, *args, **kwargs):
            if file == denied:
                raise PermissionError("Test-only access denial")
            return original_stat(file, *args, **kwargs)

        with patch.object(Path, "stat", restricted_stat):
            with self.assertRaisesRegex(RuntimeError, "PERMISSION_DENIED.*settings.json"):
                launcher.preflight()

    def test_missing_python_is_reported_accurately(self):
        self.python.unlink()
        with self.assertRaisesRegex(RuntimeError, "MISSING_PATH.*python.exe"):
            launcher.preflight()

    def test_old_build_blocks(self):
        (self.root / "electron/dist/main.js").write_text("old build", encoding="utf8")
        with self.assertRaisesRegex(RuntimeError, "BUILD_OUTDATED"):
            launcher.preflight()

    def test_environment_variable_missing_blocks(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": ""}):
            with self.assertRaisesRegex(RuntimeError, "ENVIRONMENT_MISSING"):
                launcher.preflight()

    def test_malformed_profile_structure_reports_configuration_error(self):
        self.write(self.profile / "package.json", {"dsh": None})
        with self.assertRaisesRegex(RuntimeError, "CONFIG_INVALID.*profile"):
            launcher.preflight()


if __name__ == "__main__":
    unittest.main(verbosity=2)
