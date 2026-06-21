from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

from .closeout import write_closeout_files
from .manifest import validate_manifest
from .monitor import inspect_execution
from .startup import render_startup_script
from .util import redact


def run_local(
    manifest: dict[str, Any],
    *,
    repo_dir: str | Path = ".",
    runtime_dir: str | Path = ".runtime/run-local",
) -> dict[str, Any]:
    validation = validate_manifest(manifest)
    if not validation.ok:
        return {
            "ok": False,
            "phase": "validate",
            "validation": validation.as_dict(),
        }

    repo_path = Path(repo_dir).resolve()
    repo_path.mkdir(parents=True, exist_ok=True)
    runtime_path = Path(runtime_dir).resolve()
    runtime_path.mkdir(parents=True, exist_ok=True)

    script_path = runtime_path / "startup.sh"
    script_path.write_text(render_startup_script(manifest))
    script_path.chmod(0o755)

    env = os.environ.copy()
    env["RUNPOD_REPO_DIR"] = str(repo_path)
    env.setdefault("RUNPOD_HEARTBEAT_INTERVAL_SECONDS", "1")
    env.setdefault("RUNPOD_HOLD_AFTER_SUCCESS_SECONDS", "0")
    process = subprocess.run(
        [str(script_path)],
        cwd=repo_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    monitor = inspect_execution(manifest, repo_path)
    closeout = write_closeout_files(manifest, repo_path)
    result = {
        "ok": process.returncode == 0 and closeout["status"] == "succeeded",
        "phase": "run",
        "returncode": process.returncode,
        "script_path": str(script_path),
        "repo_dir": str(repo_path),
        "stdout": process.stdout,
        "stderr": process.stderr,
        "monitor": monitor,
        "closeout": closeout,
    }
    # value-aware redaction: the workload's stdout/stderr can echo secrets/presigned URLs
    (runtime_path / "run-local-result.json").write_text(json.dumps(redact(result), indent=2, sort_keys=True) + "\n")
    return result
