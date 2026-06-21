from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable


Runner = Callable[..., subprocess.CompletedProcess[str]]
GIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def check_source_reachability(
    manifest: dict[str, Any],
    *,
    execute: bool = False,
    timeout_seconds: int = 90,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    repo = manifest.get("repo", {}) if isinstance(manifest.get("repo"), dict) else {}
    run_id = manifest.get("run_id")
    remote_launch_allowed = manifest.get("remote_launch_allowed") is True
    source = str(repo.get("source") or "")
    url = str(repo.get("url_or_path") or "")
    ref = str(repo.get("commit_or_snapshot") or "")
    if source not in ("git_remote", "git_remote_or_snapshot"):
        return {
            "ok": True,
            "run_id": run_id,
            "status": "skipped",
            "reason": "repo.source is not git_remote",
            "commands": [],
            "errors": [],
            "warnings": [],
        }

    errors: list[str] = []
    warnings: list[str] = []
    if not url or "replace-" in url or url.endswith(".invalid/repo.git"):
        errors.append("repo.url_or_path must be a real git remote before source reachability can be checked")
    if not ref or "replace-" in ref or ref in ("main", "master", "HEAD", "example-commit-sha", "dryrun-snapshot"):
        errors.append("repo.commit_or_snapshot must be an immutable reachable ref or commit SHA before paid launch")

    commands = source_check_commands(url or "REPO_URL", ref or "COMMIT_OR_REF")
    if errors and not remote_launch_allowed:
        return {
            "ok": True,
            "run_id": run_id,
            "status": "not_required_for_dry_run",
            "source": source,
            "url": url,
            "ref": ref,
            "commands": commands,
            "errors": [],
            "warnings": ["source reachability is required before paid launch; dry-run placeholders were not executed"],
        }
    if errors:
        return {
            "ok": False,
            "run_id": run_id,
            "status": "blocked",
            "source": source,
            "url": url,
            "ref": ref,
            "commands": commands,
            "errors": errors,
            "warnings": warnings,
        }
    if not execute:
        return {
            "ok": True,
            "run_id": run_id,
            "status": "not_executed",
            "source": source,
            "url": url,
            "ref": ref,
            "commands": commands,
            "errors": [],
            "warnings": ["source reachability was not executed; run with --execute before paid launch"],
        }
    if shutil.which("git") is None:
        return {
            "ok": False,
            "run_id": run_id,
            "status": "failed",
            "source": source,
            "url": url,
            "ref": ref,
            "commands": commands,
            "errors": ["git is not installed or not on PATH"],
            "warnings": warnings,
        }

    try:
        if GIT_SHA_RE.fullmatch(ref):
            result = fetch_sha(url, ref, timeout_seconds, runner, commands)
        else:
            result = ls_remote_ref(url, ref, timeout_seconds, runner, commands)
        result["run_id"] = run_id
        return result
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {
            "ok": False,
            "status": "failed",
            "source": source,
            "url": url,
            "ref": ref,
            "commands": commands,
            "errors": [str(exc)],
            "warnings": warnings,
        }


def source_check_commands(url: str, ref: str) -> list[str]:
    if GIT_SHA_RE.fullmatch(ref):
        return [
            "tmp=$(mktemp -d)",
            f"git -C \"$tmp\" init",
            f"git -C \"$tmp\" remote add origin {shell_quote(url)}",
            f"git -C \"$tmp\" fetch --depth=1 origin {shell_quote(ref)}",
            f"git -C \"$tmp\" cat-file -e {shell_quote(ref)}^{{commit}}",
            "rm -rf \"$tmp\"",
        ]
    return [f"git ls-remote --exit-code {shell_quote(url)} {shell_quote(ref)}"]


def ls_remote_ref(url: str, ref: str, timeout_seconds: int, runner: Runner, commands: list[str]) -> dict[str, Any]:
    result = runner(
        ["git", "ls-remote", "--exit-code", url, ref],
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    ok = result.returncode == 0
    return {
        "ok": ok,
        "status": "reachable" if ok else "failed",
        "url": url,
        "ref": ref,
        "commands": commands,
        "stdout": result.stdout[:2000],
        "stderr": result.stderr[:2000],
        "errors": [] if ok else [result.stderr.strip() or result.stdout.strip() or f"git ls-remote exited {result.returncode}"],
        "warnings": [],
    }


def fetch_sha(url: str, ref: str, timeout_seconds: int, runner: Runner, commands: list[str]) -> dict[str, Any]:
    logs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="runpod-bridge-source-") as tmp:
        tmp_path = Path(tmp)
        steps = [
            ["git", "-C", str(tmp_path), "init"],
            ["git", "-C", str(tmp_path), "remote", "add", "origin", url],
            ["git", "-C", str(tmp_path), "fetch", "--depth=1", "origin", ref],
            ["git", "-C", str(tmp_path), "cat-file", "-e", f"{ref}^{{commit}}"],
        ]
        for step in steps:
            result = runner(step, text=True, capture_output=True, timeout=timeout_seconds, check=False)
            logs.append(
                {
                    "command": redact_git_command(step),
                    "returncode": result.returncode,
                    "stdout": result.stdout[:2000],
                    "stderr": result.stderr[:2000],
                }
            )
            if result.returncode != 0:
                return {
                    "ok": False,
                    "status": "failed",
                    "url": url,
                    "ref": ref,
                    "commands": commands,
                    "steps": logs,
                    "errors": [result.stderr.strip() or result.stdout.strip() or f"git exited {result.returncode}"],
                    "warnings": ["some git hosts do not allow direct fetch by SHA unless the object is advertised or reachable"],
                }
    return {
        "ok": True,
        "status": "reachable",
        "url": url,
        "ref": ref,
        "commands": commands,
        "steps": logs,
        "errors": [],
        "warnings": [],
    }


def redact_git_command(command: list[str]) -> list[str]:
    return ["<repo-url>" if item.startswith(("https://", "http://", "git@")) else item for item in command]


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def source_proof_report(manifest: dict[str, Any]) -> dict[str, Any]:
    repo = manifest.get("repo", {}) if isinstance(manifest.get("repo"), dict) else {}
    proof = repo.get("source_proof", {}) if isinstance(repo.get("source_proof"), dict) else {}
    source = str(repo.get("source") or "")
    url = str(repo.get("url_or_path") or "")
    ref = str(repo.get("commit_or_snapshot") or "")
    warnings: list[str] = []
    if source not in ("git_remote", "git_remote_or_snapshot"):
        return {"ok": True, "required": False, "warnings": [], "proof": proof}
    if not proof:
        return {"ok": False, "required": True, "warnings": ["repo.source_proof is missing"], "proof": {}}
    if proof.get("status") not in ("reachable", "verified"):
        warnings.append("repo.source_proof.status must be reachable or verified")
    if str(proof.get("url") or "") != url:
        warnings.append("repo.source_proof.url does not match repo.url_or_path")
    if str(proof.get("ref") or proof.get("commit_or_snapshot") or "") != ref:
        warnings.append("repo.source_proof.ref does not match repo.commit_or_snapshot")
    return {"ok": not warnings, "required": True, "warnings": warnings, "proof": proof}
