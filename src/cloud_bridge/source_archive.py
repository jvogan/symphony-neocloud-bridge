from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tarfile
from typing import Any


DEFAULT_EXCLUDES = {
    ".git",
    ".runtime",
    ".pytest_cache",
    "__pycache__",
    ".DS_Store",
    ".aws",
    ".azure",
    ".config/gcloud",
    ".docker",
    ".env",
    ".env.local",
    ".envrc",
    ".gnupg",
    ".huggingface",
    ".ssh",
    "node_modules",
    # standalone secret files that may live outside the dirs above
    ".kube",
    "kubeconfig",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}
SECRET_SUFFIXES = {
    ".key",
    ".pem",
    ".p12",
    ".pfx",
}
TEXT_SUFFIXES = {".cfg", ".json", ".md", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml"}
PERSONAL_PATH_LABEL = "/" + "Users/" + "..."
PERSONAL_PATH_RE = re.compile(r"/" + r"Users/[A-Za-z0-9._-]+/")


def prepare_source_archive(
    source_dir: str | Path,
    out_dir: str | Path,
    *,
    archive_name: str = "source_snapshot.tar.gz",
) -> dict[str, Any]:
    source = Path(source_dir).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"source snapshot directory does not exist: {source}")
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / archive_name
    members = write_archive(source, archive_path)
    personal_path_matches = scan_personal_paths(source)
    digest = sha256_file(archive_path)
    manifest = {
        "source_dir": str(source),
        "archive_path": str(archive_path),
        "archive_name": archive_name,
        "archive_sha256": digest,
        "archive_size_bytes": archive_path.stat().st_size,
        "member_count": len(members),
        "excluded_names": sorted(DEFAULT_EXCLUDES),
        "personal_path_matches": personal_path_matches,
        "warnings": (
            [
                f"source snapshot contains hardcoded {PERSONAL_PATH_LABEL} paths; "
                "prefer prepared snapshots over public git and scrub before publishing"
            ]
            if personal_path_matches
            else []
        ),
    }
    (output / "source_snapshot.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def write_archive(source: Path, archive_path: Path) -> list[str]:
    members: list[str] = []
    archive_resolved = archive_path.resolve()
    with tarfile.open(archive_path, "w:gz") as tar:
        for path in sorted(source.rglob("*")):
            if path.resolve() == archive_resolved:
                continue
            rel = path.relative_to(source)
            if should_exclude(rel):
                continue
            arcname = str(rel)
            tar.add(path, arcname=arcname, recursive=False)
            members.append(arcname)
    return members


def should_exclude(path: Path) -> bool:
    path_text = path.as_posix()
    if any(part in DEFAULT_EXCLUDES for part in path.parts):
        return True
    for excluded in DEFAULT_EXCLUDES:
        excluded_parts = Path(excluded).parts
        if len(excluded_parts) > 1 and tuple(path.parts[: len(excluded_parts)]) == excluded_parts:
            return True
    if any(part.startswith(".env.") for part in path.parts):
        return True
    if path_text in DEFAULT_EXCLUDES:
        return True
    return path.suffix.lower() in SECRET_SUFFIXES


def scan_personal_paths(source: Path) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        if should_exclude(rel) or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if PERSONAL_PATH_RE.search(line):
                matches.append({"path": str(rel), "line": line_no})
                break
    return matches[:100]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
