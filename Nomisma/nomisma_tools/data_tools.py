"""Bounded local health information; no shell, network or user-selected paths."""

import os
from pathlib import Path
import platform
import sys
import uuid
from project_context import project_context

TOOL_VERSION = "0.1.0"
WORKSPACE = Path(__file__).resolve().parents[1] / "workspace/projects/hs300_demo"


def nomisma_health_check() -> dict:
    """Return actual Python process facts, never credentials or environment values."""
    context = project_context()
    workspace = context[1] if context else WORKSPACE.resolve(strict=True)
    declared = Path(os.environ.get("NOMISMA_WORKSPACE", str(workspace))).resolve()
    if declared != workspace:
        raise ValueError("Workspace must match the registered project")
    environment = Path(sys.prefix).name
    installed_python = Path(__file__).resolve().parents[2] / "python/python.exe"
    if Path(sys.executable).resolve() == installed_python:
        environment = "nomisma"
    elif environment != "nomisma":
        raise RuntimeError("The dedicated nomisma Python environment is required")
    return {
        "ok": True,
        "summary": "Nomisma health check passed",
        "data": {
            "service_status": "ready",
            "python_version": platform.python_version(),
            "environment": environment,
            "python_executable": sys.executable,
            "workspace": str(workspace),
            "tool_version": TOOL_VERSION,
            "execution_id": uuid.uuid4().hex,
            **({'project_context':context[0]} if context else {}),
        },
        "artifacts": [],
        "warnings": [],
        "error": None,
    }
