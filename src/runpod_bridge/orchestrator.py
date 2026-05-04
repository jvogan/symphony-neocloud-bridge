from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .handoff import load_provider_handoff, run_handoff_flow, validate_provider_handoff
from .linear_issue import validate_issue_file
from .manifest import build_plan, load_manifest, validate_manifest
from .packet import prepare_packet


def scan_handoffs(root: str | Path) -> dict[str, Any]:
    base = Path(root).resolve()
    handoffs: list[dict[str, Any]] = []
    for path in sorted(base.rglob("provider_handoff.json")):
        try:
            handoff = load_provider_handoff(path)
            validation = validate_provider_handoff(handoff, handoff_path=path)
        except Exception as exc:
            validation = {"ok": False, "errors": [{"path": str(path), "message": str(exc)}], "warnings": []}
        handoffs.append({"path": str(path), "validation": validation})
    return {
        "root": str(base),
        "handoffs": handoffs,
        "ready": [item for item in handoffs if item["validation"].get("ok")],
        "blocked": [item for item in handoffs if not item["validation"].get("ok")],
    }


def run_orchestrator_once(
    root: str | Path,
    *,
    out_root: str | Path,
    execute: bool,
    max_spend_usd: float | None = None,
    lock_dir: str | Path | None = None,
) -> dict[str, Any]:
    scan = scan_handoffs(root)
    output = Path(out_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    for item in scan["ready"]:
        handoff_path = Path(item["path"])
        run_id = str(item["validation"].get("run_id") or handoff_path.parent.name)
        run_record = run_handoff_flow(
            handoff_path,
            out_dir=output / run_id,
            execute=execute,
            max_spend_usd=max_spend_usd,
            lock_dir=lock_dir,
        )
        runs.append(run_record)
    result = {"scan": scan, "runs": runs, "status": "completed"}
    (output / "orchestrator_once.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def issue_intake(issue_markdown: str | Path, manifest_path: str | Path, out_dir: str | Path) -> dict[str, Any]:
    issue_validation = validate_issue_file(issue_markdown)
    manifest = load_manifest(manifest_path)
    manifest_validation = validate_manifest(manifest)
    plan = build_plan(manifest, manifest_validation)
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    packet = prepare_packet(manifest, output / "packet")
    result = {
        "issue_markdown": str(Path(issue_markdown).resolve()),
        "manifest_path": str(Path(manifest_path).resolve()),
        "issue_validation": issue_validation.as_dict(),
        "manifest_validation": manifest_validation.as_dict(),
        "plan": plan,
        "packet": packet,
        "handoff_path": packet["files"].get("provider_handoff"),
        "ready_for_remote": bool(issue_validation.ok and plan["remote_ready"]),
    }
    (output / "issue_intake.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result
