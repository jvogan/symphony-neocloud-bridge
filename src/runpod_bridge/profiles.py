from __future__ import annotations

from copy import deepcopy
from typing import Any

from .manifest import infer_scale


PROFILES: dict[str, dict[str, Any]] = {
    "cheap-cpu-smoke": {
        "description": "Small CPU-only smoke, no persistent volume, short runtime, sanitized artifacts only.",
        "workload_scale": "small",
        "budget": {"max_runtime_minutes": 10, "max_estimated_cost_usd": 1},
        "runpod": {
            "cloudType": "SECURE",
            "imageName": "python:3.12-slim",
            "gpuCount": 0,
            "containerDiskInGb": 10,
            "volumeInGb": 0,
            "ports": [],
        },
        "artifact_egress": {"mode": "workspace_archive", "requires_network_volume": False},
        "monitoring": {"poll_interval_seconds": 30, "max_silent_minutes": 5},
    },
    "proxy-matrix-smoke": {
        "description": "CPU smoke with temporary HTTP/TCP artifact inspection for sanitized public artifacts.",
        "workload_scale": "small",
        "budget": {"max_runtime_minutes": 10, "max_estimated_cost_usd": 2},
        "runpod": {
            "cloudType": "SECURE",
            "imageName": "python:3.12-slim",
            "gpuCount": 0,
            "containerDiskInGb": 10,
            "volumeInGb": 0,
            "ports": ["8000/http", "8000/tcp"],
        },
        "artifact_egress": {"mode": "workspace_archive", "requires_network_volume": False},
        "monitoring": {"poll_interval_seconds": 30, "max_silent_minutes": 5},
    },
    "small-gpu": {
        "description": "Single-GPU short job with workspace archive proof and no retained pod.",
        "workload_scale": "medium",
        "budget": {"max_runtime_minutes": 60, "max_estimated_cost_usd": 10},
        "runpod": {
            "cloudType": "SECURE",
            "imageName": "runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04",
            "gpuCount": 1,
            "containerDiskInGb": 50,
            "volumeInGb": 50,
            "ports": [],
        },
        "artifact_egress": {"mode": "workspace_archive", "requires_network_volume": False},
        "monitoring": {"poll_interval_seconds": 30, "max_silent_minutes": 10},
    },
    "large-gpu-checkpoint": {
        "description": "Long single-GPU job with explicit checkpointing and durable egress.",
        "workload_scale": "large",
        "budget": {"max_runtime_minutes": 240, "max_estimated_cost_usd": 50},
        "runpod": {
            "cloudType": "SECURE",
            "imageName": "runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04",
            "gpuCount": 1,
            "containerDiskInGb": 80,
            "volumeInGb": 150,
            "ports": ["22/tcp"],
            "supportPublicIp": True,
        },
        "artifact_egress": {"mode": "object_store_upload", "requires_network_volume": True},
        "monitoring": {"poll_interval_seconds": 30, "max_silent_minutes": 20},
    },
    "huge-sharded-volume": {
        "description": "Huge sharded workload with network volume, checkpoint policy, and durable object-store proof.",
        "workload_scale": "huge",
        "budget": {"max_runtime_minutes": 720, "max_estimated_cost_usd": 150},
        "runpod": {
            "cloudType": "SECURE",
            "imageName": "runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04",
            "gpuCount": 1,
            "containerDiskInGb": 100,
            "volumeInGb": 250,
            "ports": ["22/tcp"],
            "supportPublicIp": True,
        },
        "artifact_egress": {"mode": "object_store_upload", "requires_network_volume": True},
        "monitoring": {"poll_interval_seconds": 30, "max_silent_minutes": 20},
    },
}


def list_profiles() -> list[dict[str, Any]]:
    return [{"name": name, **deepcopy(profile)} for name, profile in sorted(PROFILES.items())]


def get_profile(name: str) -> dict[str, Any]:
    if name not in PROFILES:
        raise KeyError(f"unknown profile: {name}")
    return {"name": name, **deepcopy(PROFILES[name])}


def recommend_profile(manifest: dict[str, Any]) -> dict[str, Any]:
    scale = infer_scale(manifest)
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    gpu_count = int(runpod.get("gpuCount") or 0)
    if scale == "huge":
        name = "huge-sharded-volume"
    elif scale == "large":
        name = "large-gpu-checkpoint" if gpu_count else "proxy-matrix-smoke"
    elif gpu_count:
        name = "small-gpu"
    else:
        name = "cheap-cpu-smoke"
    profile = get_profile(name)
    profile["reason"] = {"inferred_scale": scale, "gpu_count": gpu_count}
    return profile
