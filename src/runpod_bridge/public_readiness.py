from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contract import contract_self_check
from .linear_issue import validate_issue_file
from .manifest import load_manifest, validate_manifest


BRIDGE_ROOT = Path(__file__).resolve().parents[2]
SCAN_GLOBS = ("*.md", "*.json", "*.toml", "*.yaml", "*.yml")
SCAN_DIRS = ("README.md", "AGENTS.md", "docs", "templates", "skills", "examples", "logs", "pyproject.toml")
FORBIDDEN_TEXT = (
    "/" + "Users/",
    "jacob" + "vogan",
    "Bio" + "Symphony",
    "bio" + "symphony",
    "Gene" + "Cluster",
    "Bio" + "Prospector",
    "Cryo" + "-EM",
    "xyla" + "nase",
    "yeast" + "-isoprenoid",
    "gene" + "cluster-demo",
)
REQUIRED_FILES = (
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "docs/public-release-checklist.md",
    "docs/remote-smoke-runbook.md",
    "templates/runpod-launch-manifest.template.json",
    "skills/runpod-symphony/SKILL.md",
)


def run_public_audit(root: str | Path = BRIDGE_ROOT) -> dict[str, Any]:
    base = Path(root)
    checks: list[dict[str, Any]] = []

    for rel_path in REQUIRED_FILES:
        path = base / rel_path
        add(checks, f"required_file:{rel_path}", "pass" if path.is_file() else "fail", str(path))

    scan_hits = scan_for_forbidden_text(base)
    add(
        checks,
        "scrubbed_public_text",
        "pass" if not scan_hits else "fail",
        "no internal text found" if not scan_hits else f"{len(scan_hits)} internal text matches",
        details=scan_hits,
    )

    json_results = validate_json_files(base)
    add(
        checks,
        "json_templates_parse",
        "pass" if not json_results else "fail",
        "json files parse" if not json_results else f"{len(json_results)} json parse failures",
        details=json_results,
    )

    manifest_results = validate_manifests(base)
    manifest_failures = [item for item in manifest_results if not item["ok"]]
    add(
        checks,
        "manifests_validate",
        "pass" if not manifest_failures else "fail",
        "all manifests validate" if not manifest_failures else f"{len(manifest_failures)} manifest failures",
        details=manifest_results,
    )

    contract_results = validate_contracts(base)
    contract_failures = [item for item in contract_results if not item["ok"]]
    add(
        checks,
        "contract_self_checks",
        "pass" if not contract_failures else "fail",
        "all manifest contracts pass" if not contract_failures else f"{len(contract_failures)} contract failures",
        details=contract_results,
    )

    issue_results = validate_issue_examples(base)
    issue_failures = [item for item in issue_results if not item["ok"]]
    add(
        checks,
        "linear_issue_examples_validate",
        "pass" if not issue_failures else "fail",
        "linear issue examples validate" if not issue_failures else f"{len(issue_failures)} issue example failures",
        details=issue_results,
    )

    if any(check["status"] == "fail" for check in checks):
        overall = "fail"
    elif any(check["status"] == "warn" for check in checks):
        overall = "warn"
    else:
        overall = "pass"
    return {"overall": overall, "checks": checks}


def add(
    checks: list[dict[str, Any]],
    name: str,
    status: str,
    message: str,
    *,
    details: list[dict[str, Any]] | None = None,
) -> None:
    check: dict[str, Any] = {"name": name, "status": status, "message": message}
    if details is not None:
        check["details"] = details
    checks.append(check)


def scan_for_forbidden_text(base: Path) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for path in iter_scan_files(base):
        try:
            lines = path.read_text().splitlines()
        except UnicodeDecodeError:
            continue
        rel = str(path.relative_to(base))
        for lineno, line in enumerate(lines, start=1):
            for token in FORBIDDEN_TEXT:
                if token in line:
                    hits.append({"path": rel, "line": lineno, "token": token})
    return hits


def iter_scan_files(base: Path):
    roots: list[Path] = []
    for entry in SCAN_DIRS:
        path = base / entry
        if path.exists():
            roots.append(path)
    for root in roots:
        if root.is_file():
            yield root
            continue
        for glob in SCAN_GLOBS:
            yield from root.rglob(glob)


def validate_json_files(base: Path) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for path in list((base / "templates").glob("*.json")) + list((base / "examples").rglob("*.json")):
        try:
            json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            failures.append({"path": str(path.relative_to(base)), "error": str(exc)})
    return failures


def validate_manifests(base: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    paths = [base / "templates" / "runpod-launch-manifest.template.json"]
    paths.extend((base / "examples").rglob("launch_manifest.json"))
    for path in paths:
        manifest = load_manifest(path)
        validation = validate_manifest(manifest)
        results.append(
            {
                "path": str(path.relative_to(base)),
                "ok": validation.ok,
                "errors": [issue.__dict__ for issue in validation.errors],
                "warnings": [issue.__dict__ for issue in validation.warnings],
            }
        )
    return results


def validate_contracts(base: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    paths = [base / "templates" / "runpod-launch-manifest.template.json"]
    paths.extend((base / "examples").rglob("launch_manifest.json"))
    for path in paths:
        manifest = load_manifest(path)
        contract = contract_self_check(manifest)
        results.append(
            {
                "path": str(path.relative_to(base)),
                "ok": contract["ok"],
                "errors": contract["errors"],
                "warnings": contract["warnings"],
                "recommendations": contract["recommendations"],
            }
        )
    return results


def validate_issue_examples(base: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in sorted((base / "examples").rglob("linear_issue.md")):
        validation = validate_issue_file(path)
        results.append(
            {
                "path": str(path.relative_to(base)),
                "ok": validation.ok,
                "errors": [issue.__dict__ for issue in validation.errors],
                "warnings": [issue.__dict__ for issue in validation.warnings],
            }
        )
    return results
