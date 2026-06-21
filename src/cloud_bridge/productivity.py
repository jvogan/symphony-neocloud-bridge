from __future__ import annotations

from typing import Any

from .manifest import get_nested, has_port
from .proxy import proxy_url, tcp_url


def build_productivity_plan(
    manifest: dict[str, Any],
    *,
    pod_id: str = "POD_ID",
    public_ip: str = "POD_PUBLIC_IP",
    external_port: int | None = None,
) -> dict[str, Any]:
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    progress = startup.get("progress", {}) if isinstance(startup.get("progress"), dict) else {}
    inspection = startup.get("inspection", {}) if isinstance(startup.get("inspection"), dict) else {}
    access = manifest.get("access", {}) if isinstance(manifest.get("access"), dict) else {}
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    ports = [port for port in runpod.get("ports", []) if isinstance(port, str)]
    startup_log = str(startup.get("log_file") or "runpod-execution/logs/startup.log")
    status_file = str(startup.get("status_file") or "runpod-execution/status.json")
    heartbeat_file = str(startup.get("heartbeat_file") or "runpod-execution/monitor_events.ndjson")
    max_silent_minutes = float(get_nested(manifest, ["monitoring", "max_silent_minutes"], 10) or 10)

    live_progress_port = int(progress.get("http_status_server_port") or 0)
    inspection_port = int(inspection.get("http_artifact_server_port") or 0)
    commands: list[str] = []
    warnings: list[str] = []
    blockers: list[str] = []
    signals: list[dict[str, Any]] = []

    if live_progress_port:
        signal: dict[str, Any] = {
            "name": "live_progress_http",
            "status": "configured",
            "port": live_progress_port,
            "proves": "bridge harness liveness plus status, heartbeat, and log byte fields that can be compared across samples",
            "does_not_prove": "workload progress from one sample, artifact success, or scientific/domain success",
            "requires_repeated_samples": True,
        }
        if has_port(ports, "http", live_progress_port):
            url = proxy_url(pod_id, live_progress_port, "healthz")
            signal["proxy_url"] = url
            commands.append(f"curl --fail --silent --show-error {url}")
        if has_port(ports, "tcp", live_progress_port):
            mapped = external_port or live_progress_port
            url = tcp_url(public_ip, mapped, "healthz")
            signal["tcp_url"] = url
            commands.append(f"curl --fail --silent --show-error {url}")
        if not has_port(ports, "http", live_progress_port) and not has_port(ports, "tcp", live_progress_port):
            blockers.append(f"startup.progress.http_status_server_port={live_progress_port} is not exposed in runpod.ports")
            signal["status"] = "blocked"
        if not progress.get("auth_token_ref"):
            warnings.append("live progress HTTP has no auth_token_ref; use it only for sanitized smoke metadata")
        if progress.get("include_log_tail") is True:
            warnings.append("live progress log tail can expose workload data; enable only for sanitized runs or with runtime auth")
        signals.append(signal)
    else:
        warnings.append("no live progress HTTP server is configured; HTTP/TCP artifact probes will stay silent until completion")

    if access.get("ssh_required") is True:
        commands.append(f"cloud-bridge pod-ssh-info {pod_id}")
        commands.append(f"ssh <from pod-ssh-info> 'tail -n 80 -f {startup_log}'")
        signals.append(
            {
                "name": "ssh_log_tail",
                "status": "configured",
                "proves": "actual stdout/stderr movement and exact installer/download/tool progress when SSH is reachable",
                "does_not_prove": "artifact success until closeout validates artifacts",
            }
        )
    else:
        warnings.append("SSH peek is not configured; agents cannot tail startup.log during a private long-running workload")

    if inspection_port:
        signals.append(
            {
                "name": "artifact_inspection_http",
                "status": "completion_only",
                "port": inspection_port,
                "proves": "the startup script reached inspection_hold after workload and validation completed",
                "does_not_prove": "live workload productivity before completion",
                "connection_refused_means": "expected while still in workload phase, or port mapping/server failure if the run should already be complete",
            }
        )

    signals.append(
        {
            "name": "runpod_control_plane",
            "status": "available_when_api_reachable",
            "proves": "resource allocation, pod status, hardware fields, public IP, port mappings, and cost rate",
            "does_not_prove": "container command progress or artifact success",
        }
    )
    commands.append(f"cloud-bridge runtime-metrics {pod_id} --expected-elapsed-seconds ELAPSED_SECONDS --json")
    signals.append(
        {
            "name": "runpod_graphql_runtime",
            "status": "read_only_probe",
            "proves": "container uptime and point-in-time CPU/GPU utilization; catches crash loops when uptime resets or stays tiny after long elapsed time",
            "does_not_prove": "useful workload progress, artifact success, or domain/scientific success from one utilization sample",
        }
    )
    signals.append(
        {
            "name": "billing",
            "status": "closeout_only",
            "proves": "cost after billing records are emitted",
            "does_not_prove": "live progress; billing can be aggregated and delayed",
        }
    )

    live_channel_names = {"live_progress_http", "ssh_log_tail"}
    live_productivity_channels = [
        signal
        for signal in signals
        if signal.get("name") in live_channel_names and signal.get("status") == "configured"
    ]
    productive_definition = [
        "fresh bridge heartbeat or live /healthz proves monitor liveness only",
        "workload progress requires advancement across samples: log byte growth, status/progress counter change, artifact/hash ledger growth, or SSH tail showing current command output",
        "GraphQL runtime uptime is monotonic across samples; a reset or tiny uptime after long elapsed time means likely crash loop, not productivity",
        "provider RUNNING is only resource allocation, not productivity",
        "artifact HTTP/TCP readiness is a completion signal, not a live progress signal",
    ]
    return {
        "ok": not blockers,
        "run_id": manifest.get("run_id"),
        "max_silent_minutes": max_silent_minutes,
        "status_file": status_file,
        "heartbeat_file": heartbeat_file,
        "log_file": startup_log,
        "has_live_productivity_channel": bool(live_productivity_channels),
        "live_productivity_channels": live_productivity_channels,
        "productive_definition": productive_definition,
        "signals": signals,
        "commands": commands,
        "warnings": warnings,
        "blockers": blockers,
    }
