from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from .contract import contract_self_check
from .linear_issue import validate_issue_file
from .manifest import load_manifest, validate_manifest


BRIDGE_ROOT = Path(__file__).resolve().parents[2]
SCAN_SUFFIXES = {".md", ".json", ".toml", ".yaml", ".yml", ".py", ".sh"}
SCAN_DIRS = (
    "README.md",
    "AGENTS.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    ".gitignore",
    "pyproject.toml",
    "bin/runpod-bridge",
    "docs",
    "templates",
    "skills",
    "examples",
    "logs",
    "src",
    "tests",
)
SKIP_DIR_NAMES = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "build", "dist"}
DISALLOWED_RELEASE_PREFIXES = (
    ".runtime/",
    "runpod-execution/",
    "internal/",
    "private/",
    ".claude/",
    "build/",
    "dist/",
)
DISALLOWED_RELEASE_NAMES = {
    ".DS_Store",
    ".env",
    "env.sh",
    "artifact_hashes.json",
    "closeout.json",
    "local_preflight.json",
    "runpod_resource_record.json",
    "symphony_outcome.md",
}
DISALLOWED_RELEASE_SUFFIXES = (".egg-info",)
DISALLOWED_RELEASE_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
}
FORBIDDEN_TEXT = (
    "/" + "Users/",
    "jacob" + "vogan",
    "auto" + "nomy",
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
    "assets/social-preview/runpod-bridge-social-preview-01.png",
    "templates/runpod-launch-manifest.template.json",
    "skills/runpod-symphony/SKILL.md",
    "skills/runpod-symphony/references/failure-playbook.md",
    "skills/runpod-symphony/references/worker-readiness.md",
)
README_REQUIRED_HEADINGS = (
    "## What It Does",
    "## When To Use It",
    "## Quick Start",
    "## Safety Model",
    "## Public Release",
)
TEMPLATE_PAIRS = (
    (
        "templates/runpod-launch-manifest.template.json",
        "skills/runpod-symphony/assets/templates/runpod-launch-manifest.template.json",
    ),
    (
        "templates/linear-runpod-issue.md",
        "skills/runpod-symphony/assets/templates/linear-runpod-issue.md",
    ),
    (
        "templates/symphony-outcome.md",
        "skills/runpod-symphony/assets/templates/symphony-outcome.md",
    ),
)


def run_public_audit(root: str | Path = BRIDGE_ROOT) -> dict[str, Any]:
    base = Path(root)
    checks: list[dict[str, Any]] = []

    for rel_path in REQUIRED_FILES:
        path = base / rel_path
        add(checks, f"required_file:{rel_path}", "pass" if path.is_file() else "fail", str(path))

    readme_results = validate_readme_sections(base)
    add(
        checks,
        "readme_release_sections",
        "pass" if not readme_results else "fail",
        "README explains purpose, use, quick start, safety, and release checks"
        if not readme_results
        else f"{len(readme_results)} README section gaps",
        details=readme_results,
    )

    path_hits = find_disallowed_release_paths(base)
    add(
        checks,
        "no_generated_or_private_paths",
        "pass" if not path_hits else "fail",
        "no generated or private release paths found" if not path_hits else f"{len(path_hits)} disallowed release paths",
        details=path_hits,
    )

    link_result = validate_repo_skill_link(base)
    add(
        checks,
        "repo_local_skill_link",
        "pass" if link_result["ok"] else "fail",
        link_result["message"],
        details=link_result.get("details"),
    )

    sync_results = validate_template_sync(base)
    add(
        checks,
        "skill_templates_synced",
        "pass" if not sync_results else "fail",
        "top-level templates match skill asset templates" if not sync_results else f"{len(sync_results)} template mismatches",
        details=sync_results,
    )

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
        for path in root.rglob("*"):
            if should_skip_path(base, path):
                continue
            if path.is_file() and is_scan_candidate(path):
                yield path


def should_skip_path(base: Path, path: Path) -> bool:
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        return True
    return any(part in SKIP_DIR_NAMES for part in parts)


def is_scan_candidate(path: Path) -> bool:
    return path.name == ".gitignore" or path.suffix.lower() in SCAN_SUFFIXES


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


def find_disallowed_release_paths(base: Path) -> list[dict[str, Any]]:
    tracked = list_git_tracked_files(base)
    paths = tracked if tracked else list_release_tree_files(base)
    hits: list[dict[str, Any]] = []
    source = "git" if tracked else "filesystem"
    for rel in paths:
        normalized = rel.replace("\\", "/")
        parts = PurePosixPath(normalized).parts
        if any(part in DISALLOWED_RELEASE_PARTS for part in parts):
            hits.append({"path": normalized, "source": source, "reason": "generated cache path"})
            continue
        if any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in DISALLOWED_RELEASE_PREFIXES):
            hits.append({"path": normalized, "source": source, "reason": "private or generated path prefix"})
            continue
        name = parts[-1] if parts else normalized
        if any(part.endswith(DISALLOWED_RELEASE_SUFFIXES) for part in parts):
            hits.append({"path": normalized, "source": source, "reason": "generated package metadata"})
            continue
        if name in DISALLOWED_RELEASE_NAMES or name.startswith(".env."):
            hits.append({"path": normalized, "source": source, "reason": "private or generated filename"})
    return hits


def list_git_tracked_files(base: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(base), "ls-files"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def list_release_tree_files(base: Path) -> list[str]:
    paths: list[str] = []
    for path in base.rglob("*"):
        if should_skip_path(base, path):
            continue
        if path.is_file() or path.is_symlink():
            paths.append(path.relative_to(base).as_posix())
    return paths


def validate_repo_skill_link(base: Path) -> dict[str, Any]:
    link = base / ".codex" / "skills" / "runpod-symphony"
    target = base / "skills" / "runpod-symphony"
    if not link.exists():
        return {
            "ok": False,
            "message": "missing repo-local skill link at .codex/skills/runpod-symphony",
            "details": [{"path": ".codex/skills/runpod-symphony", "error": "missing"}],
        }
    if link.resolve() != target.resolve():
        return {
            "ok": False,
            "message": ".codex skill link resolves to the wrong target",
            "details": [
                {
                    "path": ".codex/skills/runpod-symphony",
                    "resolved": str(link.resolve()),
                    "expected": str(target.resolve()),
                }
            ],
        }
    return {"ok": True, "message": ".codex skill link resolves to skills/runpod-symphony"}


def validate_template_sync(base: Path) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for source_rel, skill_rel in TEMPLATE_PAIRS:
        source = base / source_rel
        skill = base / skill_rel
        if not source.is_file() or not skill.is_file():
            failures.append({"source": source_rel, "skill": skill_rel, "error": "missing template"})
            continue
        if source.read_bytes() != skill.read_bytes():
            failures.append({"source": source_rel, "skill": skill_rel, "error": "content differs"})
    return failures


def validate_readme_sections(base: Path) -> list[dict[str, Any]]:
    readme = base / "README.md"
    if not readme.is_file():
        return [{"path": "README.md", "error": "missing"}]
    text = readme.read_text()
    return [
        {"path": "README.md", "heading": heading, "error": "missing heading"}
        for heading in README_REQUIRED_HEADINGS
        if heading not in text
    ]
