from __future__ import annotations

import os
from typing import Any


BRIDGE_MANAGED_ENV_KEYS = {
    "SYMPHONY_RUN_ID",
    "RUNPOD_BRIDGE_MANAGED",
    "RUNPOD_ENABLE_REPO_BOOTSTRAP",
    "RUNPOD_REPO_DIR",
    "RUNPOD_REPO_SOURCE",
    "RUNPOD_REPO_URL",
    "RUNPOD_REPO_REF",
    "RUNPOD_MAX_RUNTIME_MINUTES",
    "RUNPOD_TERMINATE_AFTER_MINUTES",
    "RUNPOD_SOURCE_ARCHIVE_URL_ENV",
    "RUNPOD_SOURCE_ARCHIVE_URL",
    "RUNPOD_SOURCE_ARCHIVE_SHA256",
    "RUNPOD_SOURCE_ARCHIVE_PATH",
}


def build_bridge_managed_env(manifest: dict[str, Any]) -> dict[str, str]:
    repo = manifest.get("repo", {}) if isinstance(manifest.get("repo"), dict) else {}
    budget = manifest.get("budget", {}) if isinstance(manifest.get("budget"), dict) else {}
    snapshot = repo.get("snapshot", {}) if isinstance(repo.get("snapshot"), dict) else {}
    source = str(repo.get("source", ""))
    archive_url_ref = str(snapshot.get("archive_url_ref") or "")
    archive_url_env = archive_url_ref.split(":", 1)[1].strip() if archive_url_ref.startswith("env:") else ""
    archive_url = str(snapshot.get("archive_url") or "")
    if not archive_url and archive_url_env:
        archive_url = os.environ.get(archive_url_env, "")
    archive_path = str(snapshot.get("archive_pod_path") or "")
    archive_sha = str(snapshot.get("archive_sha256") or "")
    if not archive_sha and str(repo.get("commit_or_snapshot") or "").startswith("sha256:"):
        archive_sha = str(repo.get("commit_or_snapshot", "")).split(":", 1)[1]

    return {
        "SYMPHONY_RUN_ID": str(manifest.get("run_id", "")),
        "RUNPOD_BRIDGE_MANAGED": "1",
        "RUNPOD_ENABLE_REPO_BOOTSTRAP": "1" if source_requires_bootstrap(source) else "0",
        "RUNPOD_REPO_DIR": str(repo.get("workdir", "/workspace/repo")),
        "RUNPOD_REPO_SOURCE": source,
        "RUNPOD_REPO_URL": str(repo.get("url_or_path", "")),
        "RUNPOD_REPO_REF": str(repo.get("commit_or_snapshot", "")),
        "RUNPOD_MAX_RUNTIME_MINUTES": str(budget.get("max_runtime_minutes", "")),
        "RUNPOD_TERMINATE_AFTER_MINUTES": str(budget.get("terminate_after_minutes", "")),
        "RUNPOD_SOURCE_ARCHIVE_URL_ENV": archive_url_env,
        "RUNPOD_SOURCE_ARCHIVE_URL": archive_url,
        "RUNPOD_SOURCE_ARCHIVE_SHA256": archive_sha,
        "RUNPOD_SOURCE_ARCHIVE_PATH": archive_path,
    }


def source_requires_bootstrap(source: str) -> bool:
    return source in {
        "git_remote",
        "git_remote_or_snapshot",
        "prepared_snapshot",
        "object_store_archive",
    }


def git_source_aliases() -> set[str]:
    return {"git_remote", "git_remote_or_snapshot"}


def snapshot_source_aliases() -> set[str]:
    return {"prepared_snapshot", "object_store_archive"}


def baked_image_source_aliases() -> set[str]:
    return {"baked_image", "container_image"}
