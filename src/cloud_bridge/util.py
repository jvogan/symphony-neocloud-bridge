from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any


SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|token|password|secret|credential|private[_-]?key|authorization|source[_-]?archive[_-]?url|presigned)",
    re.I,
)
REDACT_CIRCULAR_MARKER = "<redacted:circular>"
REDACT_MAX_DEPTH_MARKER = "<redacted:max-depth>"
REDACT_MAX_DEPTH = 80

# Value-shape detectors applied to every string leaf by redact() and to free text
# by redact_text(). One source of truth shared with progress_report. A secret is
# caught by its VALUE here, independent of the key name it sits under.
SENSITIVE_TEXT_PATTERNS = [
    (re.compile(r"(?i)(Authorization:\s*Bearer\s+)[^\s]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9_./+=:-]{12,}"), r"\1<redacted>"),
    (
        re.compile(
            r"(?i)([?&](?:X-Amz-Signature|X-Amz-Credential|X-Amz-Security-Token|Signature|token|api_key|access_token)=)[^&\s\"')]+"
        ),
        r"\1<redacted>",
    ),
    (re.compile(r"\bhf_[A-Za-z0-9]{12,}\b"), "<redacted>"),
    (re.compile(r"\brps_[A-Za-z0-9]{12,}\b"), "<redacted>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<redacted>"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "<redacted>"),
    (re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+"), "<redacted>"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "<redacted>"),
    # password embedded in a URL userinfo: scheme://user:<secret>@host
    (re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:)[^\s:/@]+(@)"), r"\1<redacted>\2"),
    (
        re.compile(
            r"(?i)\b((?:AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|RUNPOD_API_KEY|HF_TOKEN|[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)\s*[:=]\s*)([^\s'\";]+)"
        ),
        r"\1<redacted>",
    ),
]


def redact_text(text: str, tokens: list[str] | None = None) -> str:
    """Redact secret-shaped substrings from free text (and any explicit tokens)."""
    if not isinstance(text, str):
        return text
    redacted = text
    for token in tokens or []:
        if token:
            redacted = redacted.replace(token, "<redacted>")
    for pattern, replacement in SENSITIVE_TEXT_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact(value: Any) -> Any:
    return _redact(value, active=set(), depth=0)


def _redact(value: Any, *, active: set[int], depth: int) -> Any:
    if depth > REDACT_MAX_DEPTH:
        return REDACT_MAX_DEPTH_MARKER
    if isinstance(value, dict):
        value_id = id(value)
        if value_id in active:
            return REDACT_CIRCULAR_MARKER
        active.add(value_id)
        redacted: dict[str, Any] = {}
        try:
            for key, item in value.items():
                redacted_key = str(key)
                if SENSITIVE_KEY_RE.search(redacted_key):
                    redacted[redacted_key] = "<redacted>"
                else:
                    redacted[redacted_key] = _redact(item, active=active, depth=depth + 1)
            return redacted
        finally:
            active.discard(value_id)
    if isinstance(value, (list, tuple)):
        value_id = id(value)
        if value_id in active:
            return REDACT_CIRCULAR_MARKER
        active.add(value_id)
        try:
            return [_redact(item, active=active, depth=depth + 1) for item in value]
        finally:
            active.discard(value_id)
    if isinstance(value, set):
        value_id = id(value)
        if value_id in active:
            return REDACT_CIRCULAR_MARKER
        active.add(value_id)
        try:
            return [_redact(item, active=active, depth=depth + 1) for item in sorted(value, key=repr)]
        finally:
            active.discard(value_id)
    if isinstance(value, str):
        # value-level scan: catch a secret even under a benign key name
        return redact_text(value)
    return value


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
