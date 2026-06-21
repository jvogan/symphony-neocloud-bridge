from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from .manifest import build_plan, validate_manifest
from .providers.runpod.rest import build_remote_launch_preview
from .source_archive import prepare_source_archive
from .handoff import write_provider_handoff
from .startup import render_startup_script


def prepare_packet(
    manifest: dict[str, Any],
    out_dir: str | Path,
    *,
    source_dir: str | Path | None = None,
    source_archive_pod_path: str = "",
) -> dict[str, Any]:
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    packet_manifest = copy.deepcopy(manifest)

    source_archive: dict[str, Any] = {}
    repo = packet_manifest.get("repo", {}) if isinstance(packet_manifest.get("repo"), dict) else {}
    if repo.get("source") in ("local_snapshot", "prepared_snapshot"):
        snapshot_source = source_dir or repo.get("url_or_path") or "."
        source_archive = prepare_source_archive(snapshot_source, output)
        repo["source"] = "prepared_snapshot"
        repo["url_or_path"] = "prepared_snapshot"
        repo["commit_or_snapshot"] = f"sha256:{source_archive['archive_sha256']}"
        repo.setdefault("snapshot", {})
        repo["snapshot"].update(
            {
                "archive_path": source_archive["archive_path"],
                "archive_sha256": source_archive["archive_sha256"],
                "archive_size_bytes": source_archive["archive_size_bytes"],
            }
        )
        if source_archive_pod_path:
            repo["snapshot"]["archive_pod_path"] = source_archive_pod_path
    validation = validate_manifest(packet_manifest)
    plan = build_plan(packet_manifest, validation)
    launch_preview = build_remote_launch_preview(packet_manifest)
    manifest_text = json.dumps(packet_manifest, indent=2, sort_keys=True) + "\n"

    manifest_path = output / "launch_manifest.json"
    preflight_path = output / "local_preflight.json"
    startup_path = output / "startup.sh"
    handoff_path = output / "provider_handoff.json"

    manifest_path.write_text(manifest_text)
    preflight = {
        "ok": validation.ok,
        "manifest_sha256": hashlib.sha256(manifest_text.encode("utf-8")).hexdigest(),
        "validation": validation.as_dict(),
        "plan": plan,
        "files": {
            "launch_manifest": str(manifest_path),
            "local_preflight": str(preflight_path),
            "startup": str(startup_path) if validation.ok else "",
            "provider_handoff": str(handoff_path),
            "source_archive": source_archive.get("archive_path", ""),
            "source_archive_manifest": str(output / "source_snapshot.json") if source_archive else "",
        },
        "launch_preview": launch_preview,
        "launch_ready": launch_preview["remote_ready"],
        "source_archive": source_archive,
    }
    preflight_path.write_text(json.dumps(preflight, indent=2, sort_keys=True) + "\n")

    if validation.ok:
        startup_path.write_text(render_startup_script(packet_manifest))
        startup_path.chmod(0o755)

    write_provider_handoff(
        packet_manifest,
        manifest_path=manifest_path,
        out_path=handoff_path,
        reason="prepared_launch_packet",
        local_preflight_path=preflight_path,
        startup_path=startup_path if validation.ok else None,
        source_archive_path=source_archive.get("archive_path", "") if source_archive else None,
        source_archive_manifest_path=output / "source_snapshot.json" if source_archive else None,
    )

    return preflight
