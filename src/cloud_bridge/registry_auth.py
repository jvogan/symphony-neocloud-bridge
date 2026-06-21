from __future__ import annotations

import re
import shlex
from typing import Any

from .bootstrap_requirements import image_registry_auth_declared, likely_private_registry_image


ECR_IMAGE_RE = re.compile(r"^(?P<registry>\d{12}\.dkr\.ecr(?:-fips)?\.(?P<region>[a-z0-9-]+)\.amazonaws\.com(?:\.cn)?)/")


def build_registry_auth_plan(manifest: dict[str, Any]) -> dict[str, Any]:
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    image_name = str(runpod.get("imageName") or "")
    registry_auth_id = str(runpod.get("containerRegistryAuthId") or "")
    image_pull_verified = runpod.get("image_pull_verified") is True
    likely_private = likely_private_registry_image(image_name)
    registry_auth_declared = image_registry_auth_declared(runpod)
    ecr = ecr_image_info(image_name)
    host = registry_host(image_name)
    commands: list[str] = ["runpodctl registry list"]
    warnings: list[str] = []
    blockers: list[str] = []

    if registry_auth_id:
        commands.append(f"runpodctl registry get {shlex.quote(registry_auth_id)}")

    if ecr:
        aws = manifest.get("aws", {}) if isinstance(manifest.get("aws"), dict) else {}
        ecr_config = aws.get("ecr", {}) if isinstance(aws.get("ecr"), dict) else {}
        auth_name = str(ecr_config.get("runpod_registry_auth_name") or f"runpod-ecr-{safe_name(manifest.get('run_id') or 'run')}")
        commands.extend(
            [
                f"RUNPOD_ECR_PASSWORD=\"$(aws ecr get-login-password --region {shlex.quote(ecr['region'])})\"",
                f"runpodctl registry create --name {shlex.quote(auth_name)} --username AWS --password \"$RUNPOD_ECR_PASSWORD\"",
            ]
        )
        warnings.append("ECR tokens expire; refresh RunPod registry auth immediately before launch and delete stale auth records.")
    elif likely_private and not registry_auth_declared:
        commands.append(
            'runpodctl registry create --name "$RUNPOD_REGISTRY_AUTH_NAME" --username "$RUNPOD_REGISTRY_USERNAME" --password "$RUNPOD_REGISTRY_PASSWORD"'
        )

    if likely_private and not registry_auth_declared and not image_pull_verified:
        blockers.append(
            "likely private registry image requires runpod.containerRegistryAuthId or runpod.image_pull_verified from an exact RunPod image-pull canary"
        )

    canary = {
        "purpose": "prove RunPod can pull the exact image with provider-side registry auth before launching the real workload",
        "same_fields": ["runpod.imageName", "runpod.containerRegistryAuthId", "runpod.cloudType", "runpod.dataCenterIds"],
        "recommended_runtime": {"gpuCount": 0, "max_runtime_minutes": 5, "max_estimated_cost_usd": 0.10},
        "success_requirement": "canary pod writes a required artifact and is then deleted with cleanup verified",
    }
    return {
        "ok": not blockers,
        "status": registry_status(likely_private, registry_auth_declared, image_pull_verified),
        "image": image_name,
        "registry_host": host,
        "likely_private_registry_image": likely_private,
        "registry_auth_declared": registry_auth_declared,
        "image_pull_verified": image_pull_verified,
        "ecr": ecr,
        "commands": commands,
        "canary": canary,
        "blockers": blockers,
        "warnings": warnings,
        "docs": [
            "https://docs.runpod.io/runpodctl/reference/runpodctl-registry",
            "https://docs.runpod.io/pods/configuration/pod-settings",
        ],
    }


def ecr_image_info(image_name: str) -> dict[str, str]:
    match = ECR_IMAGE_RE.match(image_name)
    if not match:
        return {}
    return {"registry": match.group("registry"), "region": match.group("region")}


def registry_host(image_name: str) -> str:
    first = image_name.split("/", 1)[0]
    if "." in first or ":" in first:
        return first
    return "docker.io"


def registry_status(likely_private: bool, registry_auth_declared: bool, image_pull_verified: bool) -> str:
    if not likely_private:
        return "public_or_official_image"
    if registry_auth_declared:
        return "provider_registry_auth_declared"
    if image_pull_verified:
        return "image_pull_canary_verified"
    return "provider_registry_auth_required"


def safe_name(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-._") or "run"
