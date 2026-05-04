from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from typing import Any, Callable

from .startup import render_startup_script


class RunpodCtlError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


def runpodctl_available() -> bool:
    return shutil.which("runpodctl") is not None


def run_runpodctl(
    args: list[str],
    *,
    timeout_seconds: int = 120,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    binary = shutil.which("runpodctl")
    if not binary:
        raise RunpodCtlError("runpodctl is not installed or not on PATH")
    command = [binary, *args]
    result = runner(command, text=True, capture_output=True, timeout=timeout_seconds, check=False)
    record = {
        "backend": "runpodctl",
        "command": ["runpodctl", *args],
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    if result.returncode != 0:
        raise RunpodCtlError(result.stderr.strip() or result.stdout.strip() or f"runpodctl exited {result.returncode}")
    return record


def ssh_info(pod_id: str, *, verbose: bool = False, runner: Runner = subprocess.run) -> dict[str, Any]:
    args = ["ssh", "info", pod_id]
    if verbose:
        args.append("--verbose")
    return run_runpodctl(args, runner=runner)


def billing_pods_command(query: dict[str, Any]) -> list[str]:
    args = ["billing", "pods"]
    add_flag(args, "--pod-id", query.get("podId") or query.get("pod_id"))
    add_flag(args, "--start-time", query.get("startTime") or query.get("start_time"))
    add_flag(args, "--end-time", query.get("endTime") or query.get("end_time"))
    add_flag(args, "--bucket-size", query.get("bucketSize") or query.get("bucket_size"))
    add_flag(args, "--grouping", query.get("grouping"))
    add_flag(args, "--gpu-id", query.get("gpuId") or query.get("gpu_id"))
    return args


def billing_serverless_command(query: dict[str, Any]) -> list[str]:
    args = ["billing", "serverless"]
    add_flag(args, "--endpoint-id", query.get("endpointId") or query.get("endpoint_id"))
    add_flag(args, "--start-time", query.get("startTime") or query.get("start_time"))
    add_flag(args, "--end-time", query.get("endTime") or query.get("end_time"))
    add_flag(args, "--bucket-size", query.get("bucketSize") or query.get("bucket_size"))
    add_flag(args, "--grouping", query.get("grouping"))
    add_flag(args, "--gpu-id", query.get("gpuId") or query.get("gpu_id") or first_value(query.get("gpuTypeId")))
    return args


def billing_network_volume_command(query: dict[str, Any]) -> list[str]:
    args = ["billing", "network-volume"]
    add_flag(args, "--start-time", query.get("startTime") or query.get("start_time"))
    add_flag(args, "--end-time", query.get("endTime") or query.get("end_time"))
    add_flag(args, "--bucket-size", query.get("bucketSize") or query.get("bucket_size"))
    return args


def billing_pods(query: dict[str, Any], *, runner: Runner = subprocess.run) -> dict[str, Any]:
    return run_runpodctl(billing_pods_command(query), runner=runner)


def billing_serverless(query: dict[str, Any], *, runner: Runner = subprocess.run) -> dict[str, Any]:
    return run_runpodctl(billing_serverless_command(query), runner=runner)


def billing_network_volume(query: dict[str, Any], *, runner: Runner = subprocess.run) -> dict[str, Any]:
    return run_runpodctl(billing_network_volume_command(query), runner=runner)


def build_pod_create_command(manifest: dict[str, Any]) -> list[str]:
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    budget = manifest.get("budget", {}) if isinstance(manifest.get("budget"), dict) else {}
    body_env = runpod.get("env", {}) if isinstance(runpod.get("env"), dict) else {}

    args = ["runpodctl", "pod", "create"]
    if runpod.get("templateId"):
        add_flag(args, "--template-id", runpod.get("templateId"))
    elif runpod.get("imageName"):
        add_flag(args, "--image", runpod.get("imageName"))
    add_flag(args, "--name", runpod.get("name"))
    add_flag(args, "--compute-type", "GPU" if int(runpod.get("gpuCount") or 0) > 0 else "CPU")
    if int(runpod.get("gpuCount") or 0) > 0:
        add_flag(args, "--gpu-count", runpod.get("gpuCount"))
        add_flag(args, "--gpu-id", first_value(runpod.get("gpuTypeIds")))
    add_flag(args, "--container-disk-in-gb", runpod.get("containerDiskInGb"))
    add_flag(args, "--volume-in-gb", runpod.get("volumeInGb"))
    add_flag(args, "--volume-mount-path", runpod.get("volumeMountPath"))
    add_flag(args, "--ports", ",".join(str(port) for port in runpod.get("ports", []) if isinstance(port, str)))
    if body_env:
        add_flag(args, "--env", json.dumps({str(key): str(value) for key, value in body_env.items()}, sort_keys=True))
    add_flag(args, "--cloud-type", runpod.get("cloudType"))
    add_flag(args, "--data-center-ids", ",".join(str(item) for item in runpod.get("dataCenterIds", []) if item))
    if runpod.get("globalNetworking") is True:
        args.append("--global-networking")
    if runpod.get("supportPublicIp") is True:
        args.append("--public-ip")
    add_flag(args, "--network-volume-id", runpod.get("networkVolumeId"))
    add_flag(args, "--min-cuda-version", first_value(runpod.get("allowedCudaVersions")))
    add_flag(args, "--docker-args", shell_join(["bash", "-lc", render_startup_script(manifest)]))
    add_flag(args, "--registry-auth-id", runpod.get("containerRegistryAuthId"))
    add_flag(args, "--stop-after", minutes_duration(budget.get("stop_after_minutes")))
    add_flag(args, "--terminate-after", minutes_duration(budget.get("terminate_after_minutes")))
    return [str(item) for item in args if item not in ("", None)]


def shell_join(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)


def add_flag(args: list[str], flag: str, value: Any) -> None:
    if value in (None, "", [], {}):
        return
    args.extend([flag, str(value)])


def first_value(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else ""
    return value


def minutes_duration(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        return str(value)
    return f"{minutes}m"
