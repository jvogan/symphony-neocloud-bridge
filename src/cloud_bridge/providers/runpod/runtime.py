from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_GRAPHQL_URL = "https://api.runpod.io/graphql"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
POD_RUNTIME_QUERY = """
query PodRuntime($podId: String!) {
  pod(input: {podId: $podId}) {
    id
    name
    desiredStatus
    runtime {
      uptimeInSeconds
      ports {
        ip
        isIpPublic
        privatePort
        publicPort
        type
      }
      gpus {
        id
        gpuUtilPercent
        memoryUtilPercent
      }
      container {
        cpuPercent
        memoryPercent
      }
    }
  }
}
""".strip()


class RunpodGraphqlError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class RunpodGraphqlClient:
    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 30,
        user_agent: str | None = None,
    ):
        self.token = token or os.environ.get("RUNPOD_API_KEY", "")
        self.base_url = (base_url or os.environ.get("RUNPOD_GRAPHQL_URL") or DEFAULT_GRAPHQL_URL).rstrip("?")
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent or os.environ.get("RUNPOD_GRAPHQL_USER_AGENT") or DEFAULT_USER_AGENT

    def require_token(self) -> None:
        if not self.token:
            raise RunpodGraphqlError("RUNPOD_API_KEY is required for RunPod GraphQL runtime metrics")

    def request(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        self.require_token()
        separator = "&" if "?" in self.base_url else "?"
        url = f"{self.base_url}{separator}{urlencode({'api_key': self.token})}"
        body = {"query": query, "variables": variables or {}}
        request = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": self.user_agent,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            hint = cloudflare_hint(exc.code, detail)
            message = f"RunPod GraphQL request failed with HTTP {exc.code}: {detail}"
            if hint:
                message = f"{message}\n{hint}"
            raise RunpodGraphqlError(message, status_code=exc.code) from exc
        except URLError as exc:
            raise RunpodGraphqlError(f"RunPod GraphQL request failed: {exc.reason}") from exc

        if not payload:
            return {}
        try:
            result = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RunpodGraphqlError("RunPod GraphQL response was not JSON") from exc
        if isinstance(result, dict) and result.get("errors"):
            raise RunpodGraphqlError(f"RunPod GraphQL returned errors: {json.dumps(result['errors'], sort_keys=True)[:2000]}")
        return result if isinstance(result, dict) else {}

    def pod_runtime(self, pod_id: str) -> dict[str, Any]:
        payload = self.request(POD_RUNTIME_QUERY, {"podId": pod_id})
        data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
        pod = data.get("pod")
        return pod if isinstance(pod, dict) else {}


def cloudflare_hint(status_code: int, detail: str) -> str:
    lowered = detail.lower()
    if status_code == 403 and ("1010" in lowered or "cloudflare" in lowered):
        return (
            "RunPod GraphQL may have been blocked by Cloudflare bot detection. "
            "The bridge sends a browser-like User-Agent by default; set RUNPOD_GRAPHQL_USER_AGENT if your environment rewrites it."
        )
    return ""


def build_runtime_metrics_report(
    pod_id: str,
    *,
    client: RunpodGraphqlClient | None = None,
    expected_elapsed_seconds: float | None = None,
    previous_report: dict[str, Any] | None = None,
    crash_loop_uptime_threshold_seconds: float = 120,
) -> dict[str, Any]:
    api = client or RunpodGraphqlClient()
    pod = api.pod_runtime(pod_id)
    metrics = normalize_pod_runtime(pod)
    previous_metrics = previous_report.get("metrics") if isinstance(previous_report, dict) else None
    analysis = analyze_runtime_metrics(
        metrics,
        expected_elapsed_seconds=expected_elapsed_seconds,
        previous_metrics=previous_metrics if isinstance(previous_metrics, dict) else None,
        crash_loop_uptime_threshold_seconds=crash_loop_uptime_threshold_seconds,
    )
    return {
        "ok": bool(metrics.get("runtime_present")),
        "source": "runpod_graphql_pod_runtime",
        "source_url": DEFAULT_GRAPHQL_URL,
        "docs": "https://docs.runpod.io/sdks/graphql/manage-pods",
        "pod_id": pod_id,
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "metrics": metrics,
        "analysis": analysis,
    }


def normalize_pod_runtime(pod: dict[str, Any]) -> dict[str, Any]:
    runtime = pod.get("runtime", {}) if isinstance(pod.get("runtime"), dict) else {}
    container = runtime.get("container", {}) if isinstance(runtime.get("container"), dict) else {}
    gpus = runtime.get("gpus", []) if isinstance(runtime.get("gpus"), list) else []
    ports = runtime.get("ports", []) if isinstance(runtime.get("ports"), list) else []
    return {
        "id": pod.get("id"),
        "name": pod.get("name"),
        "desiredStatus": pod.get("desiredStatus"),
        "runtime_present": bool(runtime),
        "uptimeInSeconds": number_or_none(runtime.get("uptimeInSeconds")),
        "container": {
            "cpuPercent": number_or_none(container.get("cpuPercent")),
            "memoryPercent": number_or_none(container.get("memoryPercent")),
        },
        "gpus": [
            {
                "id": gpu.get("id"),
                "gpuUtilPercent": number_or_none(gpu.get("gpuUtilPercent")),
                "memoryUtilPercent": number_or_none(gpu.get("memoryUtilPercent")),
            }
            for gpu in gpus
            if isinstance(gpu, dict)
        ],
        "ports": [port for port in ports if isinstance(port, dict)],
    }


def analyze_runtime_metrics(
    metrics: dict[str, Any],
    *,
    expected_elapsed_seconds: float | None = None,
    previous_metrics: dict[str, Any] | None = None,
    crash_loop_uptime_threshold_seconds: float = 120,
) -> dict[str, Any]:
    warnings: list[str] = []
    evidence: list[str] = []
    recommendations: list[str] = []
    uptime = number_or_none(metrics.get("uptimeInSeconds"))
    desired_status = str(metrics.get("desiredStatus") or "")
    previous_uptime = number_or_none(previous_metrics.get("uptimeInSeconds")) if previous_metrics else None
    cpu_percent = number_or_none((metrics.get("container") or {}).get("cpuPercent")) or 0
    memory_percent = number_or_none((metrics.get("container") or {}).get("memoryPercent")) or 0
    gpu_utils = [number_or_none(gpu.get("gpuUtilPercent")) or 0 for gpu in metrics.get("gpus", []) if isinstance(gpu, dict)]
    gpu_memory = [number_or_none(gpu.get("memoryUtilPercent")) or 0 for gpu in metrics.get("gpus", []) if isinstance(gpu, dict)]

    if not metrics.get("runtime_present") or uptime is None:
        return {
            "state": "no_runtime_metrics",
            "runtime_alive": False,
            "crash_loop_suspected": False,
            "activity_sample": "unknown",
            "productive": None,
            "warnings": ["RunPod GraphQL returned no runtime metrics for this pod"],
            "evidence": [],
            "recommendations": ["fall back to get-pod, live /healthz, SSH tail, or cleanup if the pod cannot produce artifacts"],
        }

    if uptime < 0:
        return {
            "state": "invalid_runtime_telemetry",
            "runtime_alive": False,
            "crash_loop_suspected": False,
            "uptime_seconds": uptime,
            "previous_uptime_seconds": previous_uptime,
            "expected_elapsed_seconds": expected_elapsed_seconds,
            "activity_sample": "unknown",
            "container_cpu_percent": cpu_percent,
            "container_memory_percent": memory_percent,
            "gpu_util_percent": gpu_utils,
            "gpu_memory_percent": gpu_memory,
            "productive": None,
            "warnings": [
                f"RunPod GraphQL returned negative uptimeInSeconds ({uptime:g}); provider runtime telemetry is invalid or the pod agent is unhealthy"
            ],
            "evidence": [f"uptimeInSeconds is negative: {uptime:g}"],
            "recommendations": [
                "do not infer productivity from provider runtime metrics for this pod",
                "require a workload heartbeat, SSH/log tail, or fetched artifact packet immediately; otherwise clean up and retry with a tiny provider smoke",
            ],
        }

    crash_loop = False
    if previous_uptime is not None and uptime + 5 < previous_uptime:
        crash_loop = True
        evidence.append(f"uptimeInSeconds dropped from {previous_uptime:g} to {uptime:g}")
    if (
        expected_elapsed_seconds is not None
        and expected_elapsed_seconds > crash_loop_uptime_threshold_seconds
        and uptime <= crash_loop_uptime_threshold_seconds
    ):
        crash_loop = True
        evidence.append(
            f"expected elapsed time is {expected_elapsed_seconds:g}s but current container uptime is only {uptime:g}s"
        )

    active_sample = bool(cpu_percent > 0 or memory_percent > 0 or any(value > 0 for value in gpu_utils) or any(value > 0 for value in gpu_memory))
    if active_sample:
        evidence.append("point-in-time CPU/GPU/memory utilization is non-zero")
    else:
        warnings.append("zero CPU/GPU utilization is only a point-in-time idle sample, not proof that the workload is stuck")

    if crash_loop:
        state = "crash_loop_suspected"
        runtime_alive = False
        recommendations.append("stop or delete the pod unless an operator can inspect logs immediately")
        recommendations.append("relaunch only after adding a live progress endpoint, SSH/log peek, or startup wrapper that persists failure logs")
    elif uptime <= crash_loop_uptime_threshold_seconds:
        state = "recent_start_or_restart"
        runtime_alive = True
        recommendations.append("take a second runtime-metrics sample after a few minutes; a reset or persistently tiny uptime indicates a crash loop")
    else:
        state = "runtime_alive"
        runtime_alive = True
        recommendations.append("use workload heartbeat, /healthz, SSH tail, or artifact packet checks before claiming productivity or success")

    if desired_status and desired_status.upper() != "RUNNING":
        warnings.append(f"desiredStatus is {desired_status}; runtime metrics may be stale or transitional")

    return {
        "state": state,
        "runtime_alive": runtime_alive,
        "crash_loop_suspected": crash_loop,
        "uptime_seconds": uptime,
        "previous_uptime_seconds": previous_uptime,
        "expected_elapsed_seconds": expected_elapsed_seconds,
        "activity_sample": "nonzero" if active_sample else "idle_or_zero",
        "container_cpu_percent": cpu_percent,
        "container_memory_percent": memory_percent,
        "gpu_util_percent": gpu_utils,
        "gpu_memory_percent": gpu_memory,
        "productive": None,
        "warnings": warnings,
        "evidence": evidence,
        "recommendations": recommendations,
    }


def number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_previous_report(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        report = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RunpodGraphqlError(f"could not read previous runtime-metrics report {path}: {exc}") from exc
    return report if isinstance(report, dict) else None
