from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10; optional config inspection can degrade to a warning.
    tomllib = None  # type: ignore[assignment]


BRIDGE_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = BRIDGE_ROOT / "skills" / "runpod-symphony" / "SKILL.md"
NORMAL_SKILL = Path.home() / ".codex" / "skills" / "runpod-symphony"
REPO_LOCAL_SKILL = BRIDGE_ROOT / ".codex" / "skills" / "runpod-symphony"
SYMPHONY_HOME_ENV = "RUNPOD_BRIDGE_SYMPHONY_HOME"
SYMPHONY_BIN_ENV = "RUNPOD_BRIDGE_SYMPHONY_BIN"


def run_doctor() -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, message: str) -> None:
        checks.append({"name": name, "status": status, "message": message})

    add("bridge_root", "pass" if BRIDGE_ROOT.is_dir() else "fail", str(BRIDGE_ROOT))
    add("skill_source", "pass" if SKILL_PATH.is_file() else "fail", str(SKILL_PATH))
    add_link_check(checks, "normal_codex_skill", NORMAL_SKILL, SKILL_PATH.parent, missing_status="warn")
    add_link_check(checks, "repo_local_skill", REPO_LOCAL_SKILL, SKILL_PATH.parent)

    symphony_home = optional_env_path(SYMPHONY_HOME_ENV)
    if symphony_home is None:
        add("symphony_worker_skill", "warn", f"optional; set {SYMPHONY_HOME_ENV} to check worker skill discovery")
    else:
        add_link_check(
            checks,
            "symphony_worker_skill",
            symphony_home / "skills" / "runpod-symphony",
            SKILL_PATH.parent,
            missing_status="warn",
        )

    symphony_bin = optional_env_path(SYMPHONY_BIN_ENV)
    if symphony_bin is None:
        add("symphony_cli_wrapper", "warn", f"optional; set {SYMPHONY_BIN_ENV} to check a shared worker CLI wrapper")
    else:
        add_link_check(
            checks,
            "symphony_cli_wrapper",
            symphony_bin,
            BRIDGE_ROOT / "bin" / "runpod-bridge",
            missing_status="warn",
        )

    config_status, config_message = check_symphony_config(symphony_home)
    add("symphony_skill_config", config_status, config_message)

    local_wrapper = BRIDGE_ROOT / "bin" / "runpod-bridge"
    add("local_cli_wrapper", "pass" if os.access(local_wrapper, os.X_OK) else "fail", str(local_wrapper))
    add("python3", "pass" if shutil.which("python3") else "fail", shutil.which("python3") or "python3 not found")
    add("runpodctl", "pass" if shutil.which("runpodctl") else "warn", shutil.which("runpodctl") or "optional pod/serverless CLI fallback not installed; install with brew install runpod/runpodctl/runpodctl or see https://docs.runpod.io/runpodctl/overview")
    add("flash", "pass" if shutil.which("flash") else "warn", shutil.which("flash") or "optional RunPod Flash CLI not installed")
    add("RUNPOD_API_KEY", "pass" if os.environ.get("RUNPOD_API_KEY") else "warn", "present" if os.environ.get("RUNPOD_API_KEY") else "missing; required only for remote launch")
    add("LINEAR_API_KEY", "pass" if os.environ.get("LINEAR_API_KEY") else "warn", "present" if os.environ.get("LINEAR_API_KEY") else "missing; Symphony Linear workflows may inject this separately")

    if any(check["status"] == "fail" for check in checks):
        overall = "fail"
    elif any(check["status"] == "warn" for check in checks):
        overall = "warn"
    else:
        overall = "pass"
    return {"overall": overall, "checks": checks}


def optional_env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def add_link_check(
    checks: list[dict[str, str]],
    name: str,
    path: Path,
    target: Path,
    *,
    missing_status: str = "fail",
) -> None:
    if not path.exists():
        checks.append({"name": name, "status": missing_status, "message": f"missing: {path}"})
        return
    resolved = path.resolve()
    target_resolved = target.resolve()
    if resolved == target_resolved:
        checks.append({"name": name, "status": "pass", "message": f"{path} -> {target_resolved}"})
    else:
        checks.append({"name": name, "status": "warn", "message": f"{path} resolves to {resolved}, expected {target_resolved}"})


def check_symphony_config(symphony_home: Path | None = None) -> tuple[str, str]:
    if symphony_home is None:
        return "warn", f"optional; set {SYMPHONY_HOME_ENV} to inspect worker skills.config"
    symphony_config = symphony_home / "config.toml"
    if not symphony_config.is_file():
        return "warn", f"missing optional Symphony config: {symphony_config}"
    if tomllib is None:
        return "warn", "cannot inspect optional Symphony config because tomllib requires Python 3.11+"
    try:
        with symphony_config.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        return "fail", f"invalid TOML: {exc}"
    paths = [item.get("path") for item in data.get("skills", {}).get("config", []) if isinstance(item, dict)]
    if str(SKILL_PATH) in paths:
        return "pass", f"configured: {SKILL_PATH}"
    return "warn", f"missing optional skills.config entry for {SKILL_PATH}"
