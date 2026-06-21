from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


SCAN_SUFFIXES = {".md", ".py", ".sh", ".json", ".yaml", ".yml", ".toml"}
SKIP_DIRS = {
    ".git",
    ".runtime",
    ".venv",
    "__pycache__",
    "node_modules",
}
DEFAULT_SKIP_DIRS = SKIP_DIRS | {"logs"}
MAX_TEXT_BYTES = 1_500_000


@dataclass(frozen=True)
class LineRule:
    rule: str
    severity: str
    pattern: re.Pattern[str]
    message: str
    suggestion: str


LINE_RULES = (
    LineRule(
        "runpod_key_from_local_app_config",
        "error",
        re.compile(r"(\.claude\.json|mcpServers\.runpod\.env\.RUNPOD_API_KEY)", re.IGNORECASE),
        "RunPod API key is being read from local app configuration.",
        "Use the centralized bridge environment and doctor gate; never teach workers to scrape local app config for provider keys.",
    ),
    LineRule(
        "direct_runpod_rest_mutation",
        "error",
        re.compile(r"(curl|requests\.|urllib\.request|fetch\().*(POST|DELETE|PUT|PATCH|/v1/pods).*https://(api|rest)\.runpod\.io|https://(api|rest)\.runpod\.io.*(POST|DELETE|PUT|PATCH|/v1/pods)", re.IGNORECASE),
        "File appears to call the RunPod REST API directly for pod lifecycle work.",
        "Route paid mutations through cloud-bridge run-remote/create-pod/cleanup-pod so launch gates, artifact proof, and cleanup records stay coupled.",
    ),
    LineRule(
        "cleanup_submitted_as_status",
        "warning",
        re.compile(r"cleanup_status.*delete_submitted|delete_submitted.*cleanup_status", re.IGNORECASE),
        "Cleanup submission is recorded as a status; submission is not cleanup verification.",
        "Record verified delete/absence or already_absent before final success.",
    ),
    LineRule(
        "direct_runpodctl_create",
        "warning",
        re.compile(r"\brunpodctl\s+pod\s+create\b", re.IGNORECASE),
        "Direct runpodctl pod create appears in repo text.",
        "Prefer cloud-bridge render-runpodctl-create for non-mutating review, or run-remote for audited mutation.",
    ),
)


def audit_runpod_ops_tree(root: str | Path, *, include_logs: bool = False) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    findings: list[dict[str, Any]] = []
    files_scanned = 0
    skip_dirs = SKIP_DIRS if include_logs else DEFAULT_SKIP_DIRS
    for path in iter_scan_files(base, skip_dirs=skip_dirs):
        files_scanned += 1
        rel = relative_display_path(path, base)
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text_lines(rel, text))
        findings.extend(scan_file_patterns(rel, text))
    errors = [item for item in findings if item["severity"] == "error"]
    warnings = [item for item in findings if item["severity"] == "warning"]
    return {
        "root": str(base),
        "ok": not errors,
        "summary": {
            "files_scanned": files_scanned,
            "findings": len(findings),
            "errors": len(errors),
            "warnings": len(warnings),
        },
        "finding_summary": summarize_findings(findings),
        "findings": findings,
    }


def iter_scan_files(base: Path, *, skip_dirs: set[str]):
    if base.is_file():
        if should_scan_file(base):
            yield base
        return
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.relative_to(base).parts):
            continue
        if should_scan_file(path):
            yield path


def should_scan_file(path: Path) -> bool:
    try:
        return path.suffix in SCAN_SUFFIXES and path.stat().st_size <= MAX_TEXT_BYTES
    except OSError:
        return False


def scan_text_lines(rel: str, text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        compact = line.strip()
        if not compact:
            continue
        for rule in LINE_RULES:
            if rule.pattern.search(compact):
                findings.append(
                    {
                        "path": rel,
                        "line": lineno,
                        "severity": rule.severity,
                        "rule": rule.rule,
                        "message": rule.message,
                        "suggestion": rule.suggestion,
                        "excerpt": compact[:240],
                    }
                )
    return findings


def scan_file_patterns(rel: str, text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    lowered = text.lower()
    has_create = "runpod-bridge create-pod" in lowered or "cloud-bridge create-pod" in lowered
    has_cleanup = "runpod-bridge cleanup-pod" in lowered or "cloud-bridge cleanup-pod" in lowered
    has_run_remote = "runpod-bridge run-remote" in lowered or "cloud-bridge run-remote" in lowered
    if has_create and has_cleanup and not has_run_remote:
        findings.append(
            {
                "path": rel,
                "line": None,
                "severity": "warning",
                "rule": "split_create_verify_cleanup_recipe",
                "message": "File documents a split create/verify/cleanup bridge flow without run-remote.",
                "suggestion": "Prefer run-remote for one audited create, verify, closeout, and cleanup record; use split commands only for explicit recovery/debug paths.",
                "excerpt": "",
            }
        )
    if "desiredstatus" in lowered and "running" in lowered and ("success" in lowered or "succeeded" in lowered or "done" in lowered):
        findings.append(
            {
                "path": rel,
                "line": None,
                "severity": "warning",
                "rule": "provider_running_near_success_language",
                "message": "File mentions RunPod RUNNING state near success/done language.",
                "suggestion": "Keep provider RUNNING as intent only; success needs workload status, fetched artifacts, hashes, and cleanup verification.",
                "excerpt": "",
            }
        )
    return findings


def summarize_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for finding in findings:
        key = (str(finding["severity"]), str(finding["rule"]))
        bucket = buckets.setdefault(
            key,
            {
                "severity": finding["severity"],
                "rule": finding["rule"],
                "count": 0,
                "examples": [],
                "message": finding["message"],
                "suggestion": finding["suggestion"],
            },
        )
        bucket["count"] += 1
        if len(bucket["examples"]) < 5:
            bucket["examples"].append({"path": finding["path"], "line": finding.get("line")})
    return sorted(buckets.values(), key=lambda item: (str(item["severity"]) != "error", -int(item["count"]), str(item["rule"])))


def relative_display_path(path: Path, base: Path) -> str:
    if base.is_file():
        return path.name
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)
