from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .manifest import build_plan, validate_manifest
from .handoff import write_provider_handoff
from .startup import render_startup_script


def prepare_packet(manifest: dict[str, Any], out_dir: str | Path) -> dict[str, Any]:
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)

    validation = validate_manifest(manifest)
    plan = build_plan(manifest, validation)
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

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
        },
    }
    preflight_path.write_text(json.dumps(preflight, indent=2, sort_keys=True) + "\n")

    if validation.ok:
        startup_path.write_text(render_startup_script(manifest))
        startup_path.chmod(0o755)

    write_provider_handoff(
        manifest,
        manifest_path=manifest_path,
        out_path=handoff_path,
        reason="prepared_launch_packet",
        local_preflight_path=preflight_path,
        startup_path=startup_path if validation.ok else None,
    )

    return preflight
