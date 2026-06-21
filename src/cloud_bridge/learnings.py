"""Self-learning ledger for the cloud bridge.

Agents that drive provider runs through this bridge keep hitting provider-specific
issues - auth shape, capacity, billing surprises, egress, cleanup. This module is
the durable, append-only memory where they:

1. record() an issue (and its resolution) the moment they hit it,
2. search()/recent() prior learnings *before* escalating or asking the operator,
3. build_research_brief() a payload to hand to a research sub-agent when stuck,
4. promote() scrub-clean resolved learnings into the public provider entries.

The runtime ledger is gitignored by default (``internal/private/learnings/``), so
raw operational context never reaches the public-readiness scan. record() also runs
the SAME secret detectors as the public audit (``sensitive_line_hits``) plus the
internal-token check, and flags any entry whose text carries a secret/internal
token, so promotion into public docs/known_patterns stays safe: promotion
candidates exclude anything with a scrub warning. ``ledger_safety_warning`` warns if
the ledger dir is pointed at a tracked subtree.

The ledger is strictly append-only JSONL. A learning is one line
(``{"event": "learning", ...}``); status changes are appended as marker lines
(``{"event": "promoted", "ref": <id>}``) rather than rewriting history.
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Iterable

from .public_readiness import FORBIDDEN_TEXT, sensitive_line_hits
from .util import now

try:  # POSIX file locking for concurrent multi-agent appends
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None

LEDGER_DIR_ENV = "CLOUD_BRIDGE_LEARNINGS_DIR"
LEDGER_FILENAME = "ledger.jsonl"

# Relative repo paths that are gitignored, so an in-repo ledger there is safe.
_SAFE_IN_REPO_PREFIXES = ("internal/private", "internal/learnings", ".runtime")

# Free-form but suggested so the ledger stays groupable; new categories may emerge.
CATEGORIES = (
    "auth",
    "launch",
    "capacity",
    "billing",
    "egress",
    "cleanup",
    "monitoring",
    "source-ingress",
    "image",
    "quota",
    "other",
)
SEVERITIES = ("info", "warn", "critical")
STATUSES = ("open", "resolved")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ledger_dir() -> Path:
    override = os.environ.get(LEDGER_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return _repo_root() / "internal" / "private" / "learnings"


def ledger_path() -> Path:
    return ledger_dir() / LEDGER_FILENAME


def ledger_safety_warning() -> str:
    """Warn if the ledger dir is inside the repo but not a gitignored path - raw
    entries there could be committed and reach the public-readiness scan."""
    target = ledger_dir().resolve()
    root = _repo_root().resolve()
    try:
        rel = target.relative_to(root)
    except ValueError:
        return ""  # outside the repo - safe
    rel_str = str(rel).replace("\\", "/")
    if any(rel_str == p or rel_str.startswith(p + "/") for p in _SAFE_IN_REPO_PREFIXES):
        return ""
    return (
        f"learnings ledger dir is inside the repo at {rel_str}/ but is not a gitignored path; "
        "raw entries could be committed and scanned - point CLOUD_BRIDGE_LEARNINGS_DIR at a "
        "gitignored location (default: internal/private/learnings/)"
    )


# ---------------------------------------------------------------------------
# scrub: keep secret/internal *values* out of anything that could be promoted
# ---------------------------------------------------------------------------
def scrub_findings(*fields: str | None) -> list[str]:
    """Return generic labels (never the matched values) for any internal tokens or
    secrets across the given text fields. Reuses the public audit's detectors so the
    ledger and the public-readiness scan can never disagree about what is sensitive.
    Scans each field separately AND a single space-joined line, so a secret split
    across fields still trips a contiguity-sensitive detector."""
    raw = [field for field in fields if field]
    if not raw:
        return []
    findings: set[str] = set()
    if any(token in "\n".join(raw) for token in FORBIDDEN_TEXT):
        findings.add("internal_token")
    lines = list(raw)
    lines.append(" ".join(raw))  # catch secrets split across fields
    for index, line in enumerate(lines, start=1):
        for hit in sensitive_line_hits("learning", index, line):
            findings.add(str(hit.get("kind", "sensitive")))
    return sorted(findings)


# ---------------------------------------------------------------------------
# read / write
# ---------------------------------------------------------------------------
def read_entries(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or ledger_path()
    if not target.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # surfaced via corrupt_lines() / stats so it is never fully silent
    return entries


def corrupt_lines(path: Path | None = None) -> int:
    """Count non-blank lines that fail to parse - a torn write or hand-edit."""
    target = path or ledger_path()
    if not target.exists():
        return 0
    bad = 0
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            bad += 1
    return bad


def _append(entry: dict[str, Any], path: Path | None = None) -> None:
    target = path or ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def learnings(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if e.get("event", "learning") == "learning"]


def promoted_ids(entries: Iterable[dict[str, Any]]) -> set[str]:
    return {e["ref"] for e in entries if e.get("event") == "promoted" and e.get("ref")}


def record_learning(
    *,
    provider: str,
    symptom: str,
    category: str = "other",
    severity: str = "warn",
    context: str = "",
    resolution: str = "",
    status: str = "open",
    tags: list[str] | None = None,
    evidence: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    timestamp = now()
    tag_list = sorted(set(tags or []))
    # Operational ledger ids only need to be unique - including under concurrent
    # multi-agent appends, where a time+content hash collides at 1-second granularity.
    # Random entropy means two records can never share an id (which would let one
    # promotion silently evict another from the queue).
    entry_id = secrets.token_hex(6)
    entry: dict[str, Any] = {
        "event": "learning",
        "id": entry_id,
        "ts": timestamp,
        "provider": provider,
        "category": category,
        "severity": severity,
        "status": status,
        "symptom": symptom,
        "context": context,
        "resolution": resolution,
        "tags": tag_list,
        "evidence": evidence,
    }
    warnings = scrub_findings(symptom, context, resolution, evidence, provider, " ".join(tag_list))
    if warnings:
        entry["scrub_warning"] = warnings
    _append(entry, path)
    return entry


def mark_promoted(ref: str, *, path: Path | None = None) -> dict[str, Any]:
    """Append a promotion marker for ``ref``. Refuses ids that are not currently a
    promotable candidate (resolved, scrub-clean, not already promoted), so a typo
    cannot pollute the ledger or silently fail to mark a real learning."""
    entries = read_entries(path)
    eligible = {record["id"] for record in promotion_candidates(entries)}
    if ref not in eligible:
        raise ValueError(
            f"{ref!r} is not promotable: it must be a resolved, scrub-clean, not-yet-promoted learning id"
        )
    marker = {"event": "promoted", "ref": ref, "ts": now()}
    _append(marker, path)
    return marker


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------
def search(
    entries: Iterable[dict[str, Any]],
    *,
    provider: str | None = None,
    query: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    tag: str | None = None,
) -> list[dict[str, Any]]:
    records = learnings(entries)
    done = promoted_ids(entries)
    results: list[dict[str, Any]] = []
    needle = query.lower() if query else None
    for record in records:
        if provider and record.get("provider") != provider:
            continue
        if category and record.get("category") != category:
            continue
        if severity and record.get("severity") != severity:
            continue
        if status and record.get("status") != status:
            continue
        if tag and tag not in (record.get("tags") or []):
            continue
        if needle:
            blob = " ".join(
                str(record.get(field, ""))
                for field in ("symptom", "context", "resolution", "evidence", "provider", "category")
            ).lower()
            if needle not in blob:
                continue
        annotated = dict(record)
        annotated["promoted"] = record.get("id") in done
        results.append(annotated)
    results.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return results


def stats(entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    records = learnings(entries)
    done = promoted_ids(entries)

    def tally(field: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in records:
            key = str(record.get(field, "unknown"))
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    return {
        "total": len(records),
        "open": sum(1 for r in records if r.get("status") == "open"),
        "resolved": sum(1 for r in records if r.get("status") == "resolved"),
        "promoted": len(done),
        "promotable": len(promotion_candidates(entries)),
        "with_scrub_warning": sum(1 for r in records if r.get("scrub_warning")),
        "by_provider": tally("provider"),
        "by_category": tally("category"),
        "by_severity": tally("severity"),
    }


def promotion_candidates(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolved, scrub-clean learnings not yet promoted - safe to fold into a public
    provider entry's known_patterns."""
    done = promoted_ids(entries)
    out: list[dict[str, Any]] = []
    for record in learnings(entries):
        if record.get("status") != "resolved":
            continue
        if record.get("scrub_warning"):
            continue
        if record.get("id") in done:
            continue
        out.append(record)
    out.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return out


def promotion_bullet(record: dict[str, Any]) -> str:
    """Render a resolved learning as a known_patterns-style bullet."""
    symptom = (record.get("symptom") or "").strip().rstrip(".")
    resolution = (record.get("resolution") or "").strip().rstrip(".")
    if resolution:
        return f"{symptom} -> {resolution}"
    return symptom


def provider_entry_file(provider: str) -> str:
    return f"src/cloud_bridge/providers/{provider}/adapter.py"


# ---------------------------------------------------------------------------
# research brief: the payload an agent hands to a research sub-agent when stuck
# ---------------------------------------------------------------------------
def build_research_brief(
    *,
    provider: str,
    symptom: str,
    entries: Iterable[dict[str, Any]],
    adapter_status: dict[str, Any] | None = None,
    failing_invocation: str = "",
) -> dict[str, Any]:
    entries = list(entries)
    prior = search(entries, provider=provider, query=symptom)
    related = search(entries, provider=provider)
    known_patterns: list[str] = []
    docs: list[str] = []
    learnings_doc = ""
    if adapter_status:
        known_patterns = list(adapter_status.get("known_patterns", []) or [])
        learnings_doc = adapter_status.get("learnings_doc", "") or ""
        # docs live top-level (runpod) or under orchestration_glue (aws); provider entries may omit them
        doc_map = adapter_status.get("docs")
        if not isinstance(doc_map, dict):
            glue = adapter_status.get("orchestration_glue")
            doc_map = glue.get("docs") if isinstance(glue, dict) else None
        if isinstance(doc_map, dict):
            docs = sorted(str(v) for v in doc_map.values())

    queries = [
        f"{provider} {symptom}",
        f"{provider} official docs {symptom}",
        f"{provider} {symptom} error fix 2026",
        f"{provider} pricing cleanup terminate billing",
    ]
    record_cmd = (
        f'cloud-bridge learnings record --provider {provider} --status resolved '
        f'--symptom "{symptom}" --resolution "<the fix>" --evidence "<citation url>"'
    )
    return {
        "provider": provider,
        "symptom": symptom,
        "failing_invocation": failing_invocation,
        # full context+evidence so the sub-agent has the real error, not just a label
        "prior_learnings": [
            {k: r.get(k) for k in ("id", "ts", "severity", "status", "symptom", "resolution", "context", "evidence")}
            for r in prior[:6]
        ],
        "related_learnings_count": len(related),
        "provider_known_patterns": known_patterns,
        "learnings_doc": learnings_doc,
        "doc_links": docs,
        "suggested_search_queries": queries,
        "record_resolution_command": record_cmd,
        "agent_instruction": (
            f"You are a research sub-agent. The parent agent is blocked on {provider!r} with: "
            f"{symptom!r}. First read this provider's known_patterns and learnings_doc above, any "
            "prior_learnings (their context/evidence hold the real error), and the failing_invocation. "
            "Then search the provider's OFFICIAL docs and the recent (2025-2026) web for a concrete fix, "
            "preferring authoritative sources. Return: root cause, the exact fix/command, a citation, and "
            "whether it has cost/cleanup implications. Do not run paid or mutating actions. When the fix is "
            f"known, the parent records it so the lesson compounds: {record_cmd}"
        ),
    }
