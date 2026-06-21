from __future__ import annotations

import json
from typing import Any


# RunPod does not document a REST create-payload byte ceiling. A live smoke
# observed a ~65KB dockerStartCmd request fail before launch; keep a conservative
# warning band and a hard bridge-side stop before that boundary.
WARN_POST_BODY_BYTES = 48 * 1024
MAX_POST_BODY_BYTES = 60 * 1024
EMPIRICAL_REJECTION_BYTES = 64 * 1024


def create_request_payload_report(request_body: dict[str, Any]) -> dict[str, Any]:
    docker_start_cmd = request_body.get("dockerStartCmd")
    startup_script = ""
    if isinstance(docker_start_cmd, list) and len(docker_start_cmd) >= 3:
        startup_script = str(docker_start_cmd[2])

    body_bytes = len(json.dumps(request_body, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    startup_bytes = len(startup_script.encode("utf-8"))
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    recommendations: list[str] = []

    if body_bytes > MAX_POST_BODY_BYTES:
        errors.append(
            {
                "severity": "error",
                "path": "runpod.create_request",
                "message": (
                    f"rendered RunPod POST /pods payload is {body_bytes} bytes, above the bridge hard limit "
                    f"of {MAX_POST_BODY_BYTES} bytes; use gzip/base64 payloads, object-store/bootstrap files, "
                    "or a git/snapshot source instead of a large inline dockerStartCmd"
                ),
            }
        )
    elif body_bytes > WARN_POST_BODY_BYTES:
        warnings.append(
            {
                "severity": "warning",
                "path": "runpod.create_request",
                "message": (
                    f"rendered RunPod POST /pods payload is {body_bytes} bytes, near the empirical startup "
                    "payload risk band; prefer compressed payloads or external handoff files"
                ),
            }
        )

    if startup_bytes > WARN_POST_BODY_BYTES:
        recommendations.append(
            "compress large inline startup payloads with gzip/base64 or move workload material into a repo, snapshot, network volume, or object store"
        )

    return {
        "ok": not errors,
        "post_body_bytes": body_bytes,
        "docker_start_cmd_script_bytes": startup_bytes,
        "warn_post_body_bytes": WARN_POST_BODY_BYTES,
        "max_post_body_bytes": MAX_POST_BODY_BYTES,
        "empirical_rejection_bytes": EMPIRICAL_REJECTION_BYTES,
        "errors": errors,
        "warnings": warnings,
        "recommendations": recommendations,
    }
