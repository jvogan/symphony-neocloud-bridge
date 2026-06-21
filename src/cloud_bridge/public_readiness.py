from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .contract import contract_self_check
from .linear_issue import validate_issue_file
from .manifest import load_manifest, validate_manifest


BRIDGE_ROOT = Path(__file__).resolve().parents[2]
SCAN_GLOBS = ("*.md", "*.json", "*.toml", "*.yaml", "*.yml", "*.py", "*.sh", "*.txt")
SCAN_DIRS = (
    "README.md",
    "AGENTS.md",
    "docs",
    "templates",
    "skills",
    "examples",
    "src",
    "tests",
    "logs",
    "pyproject.toml",
)
# Generic identifiers that must never ship in public files. Kept split so this
# module never matches itself during the scan (the home-directory prefix is
# generic, not private).
_GENERIC_FORBIDDEN_TEXT = (
    "/" + "Users/",
)


def _joined_pattern(*parts: str) -> str:
    return r"\b" + r"\s+".join(parts) + r"\b"


_PROVENANCE_TEXT_PATTERNS = (
    ("private_source", re.compile(_joined_pattern("sibling", "private", r"repos?"), re.IGNORECASE)),
    ("prior_run_source", re.compile(_joined_pattern("prior", "internal", r"runs?"), re.IGNORECASE)),
    ("internal_system", re.compile(_joined_pattern("internal", "cross-cloud"), re.IGNORECASE)),
    ("prior_run_source", re.compile(_joined_pattern("mi" + "ned", "from", "real", "prior", r"runs?"), re.IGNORECASE)),
    ("run_source", re.compile(_joined_pattern("real", r"runs?", "exist", "across"), re.IGNORECASE)),
    ("run_count", re.compile(r"~\d+\s+real\s+\w+\s+runs?\b", re.IGNORECASE)),
    ("named_run_source", re.compile("Elastic" + "BLAST", re.IGNORECASE)),
    ("observed_economics", re.compile("Observed" + r"\s+" + "economics", re.IGNORECASE)),
    ("real_cost_discipline", re.compile("Real" + r"\s+" + "cost" + r"\s+" + "discipline", re.IGNORECASE)),
    ("sample_run_costs", re.compile("Sample" + r"\s+" + "per-run" + r"\s+" + "points", re.IGNORECASE)),
    ("instance_campaign", re.compile(r"\b\d+-instance\b.*\bcampaign\b", re.IGNORECASE)),
    ("wall_time_smoke", re.compile(r"\bsmoke:\s*\d+(?:\.\d+)?s\s+wall\b", re.IGNORECASE)),
    ("burned_cost", re.compile(r"\bburned\s+\$[0-9]", re.IGNORECASE)),
)


def load_forbidden_text() -> tuple[str, ...]:
    """Build the forbidden-text denylist for the public scan.

    Author-private vocabulary (project codenames, usernames, stack names) is not
    stored in this published file. It is read at runtime from a gitignored terms
    file -- ``internal/forbidden_terms.txt`` by default, or the path named by
    ``BRIDGE_FORBIDDEN_TERMS_FILE`` -- one term per non-empty, non-comment line.
    Public clones ship no such file, so only the generic defaults apply there.
    """
    terms: list[str] = list(_GENERIC_FORBIDDEN_TEXT)
    candidates: list[Path] = []
    env_override = os.environ.get("BRIDGE_FORBIDDEN_TERMS_FILE")
    if env_override:
        candidates.append(Path(env_override))
    candidates.append(BRIDGE_ROOT / "internal" / "forbidden_terms.txt")
    for candidate in candidates:
        try:
            raw = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and stripped not in terms:
                terms.append(stripped)
        break
    return tuple(terms)


FORBIDDEN_TEXT = load_forbidden_text()
SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    (?P<key>\b[A-Z0-9_]*(?:API[_-]?KEY|SECRET(?:_ACCESS_KEY)?|TOKEN|PASSWORD|PASSWD|PRIVATE[_-]?KEY|AUTH[_-]?TOKEN|REGISTRY[_-]?PASSWORD)[A-Z0-9_]*\b)
    \s*[:=]\s*
    (?P<value>\$\{[A-Z_][A-Z0-9_]*\}|\{\{\s*RUNPOD_SECRET_[A-Za-z0-9_.-]+\s*\}\}|<[^>\s]+>|["']?[^"',\s;\]]+["']?)
    """
)
HIGH_CONFIDENCE_SECRET_RE = re.compile(
    r"(?i)\b(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|sk-[A-Za-z0-9_-]{20,}|rp_[A-Za-z0-9_-]{20,})\b"
)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
BEARER_RE = re.compile(r"(?i)\bBearer\s+(?P<value>[A-Za-z0-9_./+=:-]{12,}|\$\{[^}]+\}|\{\{[^}]+\}\}|<[^>]+>|redacted)")
PRESIGNED_URL_RE = re.compile(r"\bX-Amz-(?:Signature|Credential|Security-Token)=(?P<value>[^&\s\"')]+)")
CONNECTION_CODE_RE = re.compile(
    r"""(?ix)
    \b(?:connection|transfer|pairing|receive|send)[_-]?(?:code|token)\b
    \s*[:=]\s*
    (?P<value>["']?[A-Za-z0-9_-]{12,}["']?)
    """
)
SOURCE_SUFFIXES = {".py"}
SAFE_REFERENCE_PREFIXES = (
    "env:",
    "secret:",
    "secure-store:",
    "vault:",
    "aws-sm:",
    "aws-secretsmanager:",
    "aws-sts:",
    "gcp-sm:",
    "azure-kv:",
)
ALLOWED_ANGLE_PLACEHOLDERS = {
    "<placeholder>",
    "<runpod-s3-access-key>",
    "<runpod-s3-secret-key>",
    "<from .Credentials.AccessKeyId>",
    "<from .Credentials.SecretAccessKey>",
    "<from .Credentials.SessionToken>",
}
REQUIRED_FILES = (
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "docs/public-release-checklist.md",
    "docs/remote-smoke-runbook.md",
    "templates/runpod-launch-manifest.template.json",
    "skills/cloud-symphony/SKILL.md",
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
        "public_text_policy",
        "pass" if not scan_hits else "fail",
        "no disallowed text found" if not scan_hits else f"{len(scan_hits)} disallowed text matches",
        details=scan_hits,
    )

    sensitive_hits = scan_for_sensitive_text(base)
    add(
        checks,
        "no_literal_secrets",
        "pass" if not sensitive_hits else "fail",
        "no literal secrets found" if not sensitive_hits else f"{len(sensitive_hits)} secret-like matches",
        details=sensitive_hits,
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
        "linear issue templates and examples validate"
        if not issue_failures
        else f"{len(issue_failures)} issue template/example failures",
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
            for label, pattern in _PROVENANCE_TEXT_PATTERNS:
                if pattern.search(line):
                    hits.append({"path": rel, "line": lineno, "token": f"source_provenance:{label}"})
    return hits


def scan_for_sensitive_text(base: Path) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for path in iter_scan_files(base):
        try:
            lines = path.read_text().splitlines()
        except UnicodeDecodeError:
            continue
        rel = str(path.relative_to(base))
        for lineno, line in enumerate(lines, start=1):
            hits.extend(sensitive_line_hits(rel, lineno, line))
    return hits


def sensitive_line_hits(rel: str, lineno: int, line: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []

    for match in SECRET_ASSIGNMENT_RE.finditer(line):
        raw_value = match.group("value").strip()
        value = normalize_secret_value(match.group("value"))
        key = normalize_secret_value(match.group("key"))
        if (
            value
            and not is_allowed_placeholder(value)
            and not is_safe_reference_value(value)
            and not is_safe_reference_fragment(line[match.start() :])
            and not should_ignore_secret_assignment(rel, key, raw_value, value, line, match.start())
        ):
            hits.append(
                {
                    "path": rel,
                    "line": lineno,
                    "kind": "secret_assignment",
                    "token": key,
                }
            )

    if PRIVATE_KEY_RE.search(line):
        hits.append({"path": rel, "line": lineno, "kind": "private_key", "token": "PRIVATE KEY"})

    for match in HIGH_CONFIDENCE_SECRET_RE.finditer(line):
        hits.append({"path": rel, "line": lineno, "kind": "high_confidence_secret", "token": match.group(1)[:12] + "..."})

    for match in BEARER_RE.finditer(line):
        value = normalize_secret_value(match.group("value"))
        if value and not is_allowed_placeholder(value):
            hits.append({"path": rel, "line": lineno, "kind": "bearer_token", "token": "Bearer"})

    for match in PRESIGNED_URL_RE.finditer(line):
        value = normalize_secret_value(match.group("value"))
        if value and not is_allowed_placeholder(value):
            hits.append({"path": rel, "line": lineno, "kind": "presigned_url", "token": "X-Amz"})

    for match in CONNECTION_CODE_RE.finditer(line):
        value = normalize_secret_value(match.group("value"))
        if value and not is_allowed_placeholder(value):
            hits.append({"path": rel, "line": lineno, "kind": "connection_code", "token": "connection_code"})

    return hits


def normalize_secret_value(value: str) -> str:
    return value.strip().strip("\"'").strip()


def is_allowed_placeholder(value: str) -> bool:
    stripped = value.strip()
    lowered = stripped.lower()
    if not stripped:
        return True
    if not any(char.isalnum() for char in stripped):
        return True
    if lowered == "redacted" or lowered.startswith("redacted-") or lowered.endswith("-redacted"):
        return True
    if re.fullmatch(r"\$\{[A-Z_][A-Z0-9_]*\}", stripped):
        return True
    if re.fullmatch(r"\{\{\s*RUNPOD_SECRET_[A-Za-z0-9_.-]+\s*\}\}", stripped):
        return True
    if stripped in ALLOWED_ANGLE_PLACEHOLDERS:
        return True
    if stripped in {"...", "***", "xxxx", "XXXXX"}:
        return True
    return lowered.startswith(("example", "dummy", "sample")) or lowered.endswith(("-example", "-dummy", "-sample"))


def is_safe_reference_value(value: str) -> bool:
    stripped = value.strip()
    lowered = stripped.lower()
    if lowered.startswith(SAFE_REFERENCE_PREFIXES):
        return True
    return stripped.startswith("$(")


def is_safe_reference_fragment(fragment: str) -> bool:
    return fragment.lower().startswith(SAFE_REFERENCE_PREFIXES)


def should_ignore_secret_assignment(rel: str, key: str, raw_value: str, value: str, line: str, start: int) -> bool:
    lowered_key = key.lower().strip("`\"'")
    if "secretsmanager" in lowered_key:
        return True
    if lowered_key.endswith(("_re", "_suffixes")):
        return True
    if Path(rel).suffix not in SOURCE_SUFFIXES:
        return False
    if raw_value.startswith(("\"${", "'${")) or value.startswith("${"):
        return True
    if raw_value.startswith(("'", '"')):
        return False
    if start > 0 and line[start - 1] in {"'", '"'}:
        return False
    return not HIGH_CONFIDENCE_SECRET_RE.search(raw_value)


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
    for path in json_template_paths(base):
        try:
            json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            failures.append({"path": str(path.relative_to(base)), "error": str(exc)})
    return failures


def json_template_paths(base: Path) -> list[Path]:
    paths: list[Path] = []
    paths.extend((base / "templates").glob("*.json"))
    paths.extend((base / "examples").rglob("*.json"))
    paths.extend((base / "skills").rglob("assets/templates/*.json"))
    return sorted_unique_existing(paths)


def validate_manifests(base: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    paths = [base / "templates" / "runpod-launch-manifest.template.json"]
    paths.extend((base / "skills").rglob("assets/templates/runpod-launch-manifest.template.json"))
    paths.extend((base / "examples").rglob("launch_manifest.json"))
    for path in sorted_unique_existing(paths):
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
    paths.extend((base / "skills").rglob("assets/templates/runpod-launch-manifest.template.json"))
    paths.extend((base / "examples").rglob("launch_manifest.json"))
    for path in sorted_unique_existing(paths):
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
    paths = [base / "templates" / "linear-runpod-issue.md"]
    paths.extend((base / "skills").rglob("assets/templates/linear-runpod-issue.md"))
    paths.extend((base / "examples").rglob("linear_issue.md"))
    for path in sorted_unique_existing(paths):
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


def sorted_unique_existing(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        unique.append(path)
    return sorted(unique)
