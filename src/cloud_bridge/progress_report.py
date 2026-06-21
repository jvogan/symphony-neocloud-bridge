from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .manifest import has_port
from .monitor import comparable_status
from .proxy import proxy_url, tcp_endpoint_from_pod, tcp_endpoint_from_runtime_report, tcp_url
from .providers.runpod.rest import RunpodRestClient, RunpodRestError, summarize_pod
from .providers.runpod.runtime import RunpodGraphqlClient, RunpodGraphqlError, build_runtime_metrics_report
from .util import redact_text

MAX_PROGRESS_TAIL_BYTES = 12_000


def build_progress_report(
    manifest: dict[str, Any],
    pod_id: str,
    *,
    previous_report: dict[str, Any] | None = None,
    client: RunpodRestClient | None = None,
    runtime_client: RunpodGraphqlClient | None = None,
    mode: str = "auto",
    public_ip: str = "",
    external_port: int | None = None,
    progress_timeout_seconds: int = 5,
) -> dict[str, Any]:
    api = client or RunpodRestClient()
    pod: dict[str, Any] = {}
    resource: dict[str, Any] = {"ok": False, "summary": {}, "error": ""}
    try:
        pod = api.get_pod(pod_id)
        resource = {"ok": True, "summary": summarize_pod(pod), "error": ""}
    except RunpodRestError as exc:
        resource = {"ok": False, "summary": {}, "error": str(exc), "status_code": exc.status_code}

    runtime: dict[str, Any] = {"ok": False, "error": "not_checked"}
    try:
        previous_runtime = previous_report.get("runtime") if isinstance(previous_report, dict) else None
        runtime = build_runtime_metrics_report(
            pod_id,
            client=runtime_client,
            previous_report=previous_runtime if isinstance(previous_runtime, dict) else None,
        )
    except RunpodGraphqlError as exc:
        runtime = {"ok": False, "error": str(exc), "source": "runpod_graphql_pod_runtime"}

    live_progress = fetch_live_progress(
        manifest,
        pod_id,
        pod=pod,
        runtime=runtime,
        mode=mode,
        public_ip=public_ip,
        external_port=external_port,
        timeout_seconds=progress_timeout_seconds,
    )
    classification = classify_progress(resource, runtime, live_progress, previous_report)
    return {
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pod_id": pod_id,
        "run_id": manifest.get("run_id"),
        "resource": resource,
        "runtime": runtime,
        "live_progress": live_progress,
        "classification": classification,
        "reporting_rule": (
            "Report monitor liveness, provider runtime, and workload progress separately. "
            "Do not claim workload progress unless classification.workload_progressing is true "
            "or terminal artifacts validate."
        ),
    }


def fetch_live_progress(
    manifest: dict[str, Any],
    pod_id: str,
    *,
    pod: dict[str, Any],
    runtime: dict[str, Any] | None = None,
    mode: str,
    public_ip: str,
    external_port: int | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    progress = startup.get("progress", {}) if isinstance(startup.get("progress"), dict) else {}
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    ports = [port for port in runpod.get("ports", []) if isinstance(port, str)]
    progress_port = int(progress.get("http_status_server_port") or 0)
    headers = progress_auth_headers(progress)
    if not progress_port:
        return {"ok": False, "status": "not_configured", "payload": {}, "url": "", "error": "no progress port configured"}

    attempts: list[dict[str, Any]] = []
    if mode in ("auto", "proxy") and has_port(ports, "http", progress_port):
        url = proxy_url(pod_id, progress_port, "healthz")
        result = fetch_json_url(url, timeout_seconds=timeout_seconds, headers=headers)
        result["mode"] = "proxy"
        attempts.append(result)
        if result.get("ok"):
            attach_log_tail(result, proxy_url(pod_id, progress_port, "tail"), progress, headers, timeout_seconds)
            result["attempts"] = attempts
            return result

    if mode in ("auto", "tcp") and has_port(ports, "tcp", progress_port):
        endpoint = None
        if public_ip and external_port:
            endpoint = (public_ip, external_port)
        elif pod:
            endpoint = tcp_endpoint_from_pod(pod, progress_port)
        if endpoint is None and isinstance(runtime, dict):
            endpoint = tcp_endpoint_from_runtime_report(runtime, progress_port, pod_id)
        if endpoint:
            url = tcp_url(endpoint[0], endpoint[1], "healthz")
            result = fetch_json_url(url, timeout_seconds=timeout_seconds, headers=headers)
            result["mode"] = "tcp"
            attempts.append(result)
            if result.get("ok"):
                attach_log_tail(result, tcp_url(endpoint[0], endpoint[1], "tail"), progress, headers, timeout_seconds)
                result["attempts"] = attempts
                return result
        else:
            attempts.append({"ok": False, "mode": "tcp", "error": "no external TCP mapping for progress port"})

    return {
        "ok": False,
        "status": "unreachable",
        "payload": {},
        "url": attempts[-1].get("url", "") if attempts else "",
        "error": attempts[-1].get("error", "progress endpoint is not exposed") if attempts else "progress endpoint is not exposed",
        "attempts": attempts,
    }


def classify_progress(
    resource: dict[str, Any],
    runtime: dict[str, Any],
    live_progress: dict[str, Any],
    previous_report: dict[str, Any] | None,
) -> dict[str, Any]:
    evidence: list[str] = []
    warnings: list[str] = []
    runtime_analysis = runtime.get("analysis", {}) if isinstance(runtime.get("analysis"), dict) else {}
    runtime_state = str(runtime_analysis.get("state") or "")
    live_payload = live_progress.get("payload", {}) if isinstance(live_progress.get("payload"), dict) else {}
    status = live_payload.get("status", {}) if isinstance(live_payload.get("status"), dict) else {}
    status_value = str(status.get("status") or "")
    if progress_http_404_seen(live_progress):
        warnings.append(
            "progress endpoint returned HTTP 404; this does not prove workload failure. Try declared artifact paths before cleanup."
        )

    if status_value in ("succeeded", "success", "completed"):
        return classification(
            "terminal_reported",
            evidence + ["progress endpoint reports terminal workload status"],
            warnings,
            workload_progressing=False,
            monitor_alive=bool(live_payload.get("monitor_alive")),
            cleanup_recommended=False,
            next_action="fetch_artifact_packet_validate_hashes_then_cleanup",
        )
    if status_value in ("failed", "error"):
        return classification(
            "terminal_failed",
            evidence + ["progress endpoint reports failed workload status"],
            warnings,
            workload_progressing=False,
            monitor_alive=bool(live_payload.get("monitor_alive")),
            cleanup_recommended=True,
            next_action="fetch_failure_packet_then_cleanup",
        )

    advancement = live_advancement(live_payload, previous_report)
    evidence.extend(advancement["evidence"])
    monitor_alive = bool(live_payload.get("monitor_alive")) or bool(live_progress.get("ok"))

    if advancement["workload_advanced"]:
        return classification(
            "workload_progressing",
            evidence,
            warnings,
            workload_progressing=True,
            monitor_alive=monitor_alive,
            cleanup_recommended=False,
            next_action="continue_monitoring_with_previous_progress_report",
        )

    if runtime_state in ("crash_loop_suspected", "invalid_runtime_telemetry") and not live_progress.get("ok"):
        warnings.extend(runtime_analysis.get("warnings", []))
        return classification(
            "pod_unhealthy_or_unobservable",
            evidence + runtime_analysis.get("evidence", []),
            warnings,
            workload_progressing=False,
            monitor_alive=False,
            cleanup_recommended=True,
            next_action="operator_peek_immediately_or_cleanup_before_relaunch",
        )

    if live_progress.get("ok"):
        state = "harness_alive_progress_unproven"
        next_action = "take_another_sample_and_require_log_status_or_counter_advancement"
        if advancement["harness_advanced"]:
            state = "harness_progress_workload_unproven"
        return classification(
            state,
            evidence or ["live progress endpoint responded"],
            warnings,
            workload_progressing=False,
            monitor_alive=monitor_alive,
            cleanup_recommended=False,
            next_action=next_action,
        )

    if resource.get("ok") or runtime.get("ok"):
        next_action = "check_progress_endpoint_configuration_or_use_ssh_log_tail"
        if progress_http_404_seen(live_progress):
            next_action = "try_declared_artifact_paths_before_cleanup"
        return classification(
            "provider_alive_workload_unproven",
            evidence + ["provider resource or runtime probe responded"],
            warnings,
            workload_progressing=False,
            monitor_alive=False,
            cleanup_recommended=False,
            next_action=next_action,
        )

    return classification(
        "unknown_no_reliable_progress_signal",
        evidence,
        warnings + ["control-plane and progress probes did not produce workload evidence; do not call this a provider outage from one monitor"],
        workload_progressing=False,
        monitor_alive=False,
        cleanup_recommended=False,
        next_action="retry_from_trusted_network_then_escalate_if_multiple_independent_probes_fail",
    )


def live_advancement(live_payload: dict[str, Any], previous_report: dict[str, Any] | None) -> dict[str, Any]:
    evidence: list[str] = []
    if not live_payload:
        return {"workload_advanced": False, "harness_advanced": False, "evidence": evidence}
    if not previous_report:
        return {
            "workload_advanced": False,
            "harness_advanced": False,
            "evidence": ["single live progress sample; advancement requires a previous sample"],
        }
    previous_live = previous_report.get("live_progress", {}) if isinstance(previous_report.get("live_progress"), dict) else {}
    previous_payload = previous_live.get("payload", {}) if isinstance(previous_live.get("payload"), dict) else {}
    current_files = live_payload.get("files", {}) if isinstance(live_payload.get("files"), dict) else {}
    previous_files = previous_payload.get("files", {}) if isinstance(previous_payload.get("files"), dict) else {}
    current_log_bytes = int(current_files.get("log_bytes") or 0)
    previous_log_bytes = int(previous_files.get("log_bytes") or 0)
    workload_advanced = False
    harness_advanced = False
    if current_log_bytes > previous_log_bytes:
        workload_advanced = True
        evidence.append(f"log_bytes_increased:{previous_log_bytes}->{current_log_bytes}")
    current_hash_bytes = int(current_files.get("hash_bytes") or 0)
    previous_hash_bytes = int(previous_files.get("hash_bytes") or 0)
    if current_hash_bytes > previous_hash_bytes:
        workload_advanced = True
        evidence.append(f"hash_ledger_grew:{previous_hash_bytes}->{current_hash_bytes}")
    current_status = live_payload.get("status", {}) if isinstance(live_payload.get("status"), dict) else {}
    previous_status = previous_payload.get("status", {}) if isinstance(previous_payload.get("status"), dict) else {}
    if comparable_status(current_status) != comparable_status(previous_status):
        workload_advanced = True
        evidence.append("status_payload_changed")
    current_hb = live_payload.get("last_heartbeat", {}) if isinstance(live_payload.get("last_heartbeat"), dict) else {}
    previous_hb = previous_payload.get("last_heartbeat", {}) if isinstance(previous_payload.get("last_heartbeat"), dict) else {}
    current_phase = str(current_hb.get("phase") or "")
    previous_phase = str(previous_hb.get("phase") or "")
    current_status_value = str(current_hb.get("status") or "")
    previous_status_value = str(previous_hb.get("status") or "")
    if (current_phase, current_status_value) != (previous_phase, previous_status_value):
        if current_phase == "monitor" and current_status_value == "alive":
            evidence.append("monitor_heartbeat_tick_only")
        else:
            harness_advanced = True
            evidence.append(f"harness_phase_changed:{previous_phase or '<none>'}->{current_phase or '<none>'}")
    return {"workload_advanced": workload_advanced, "harness_advanced": harness_advanced, "evidence": evidence}


def classification(
    state: str,
    evidence: list[str],
    warnings: list[str],
    *,
    workload_progressing: bool,
    monitor_alive: bool,
    cleanup_recommended: bool,
    next_action: str,
) -> dict[str, Any]:
    return {
        "state": state,
        "workload_progressing": workload_progressing,
        "monitor_alive": monitor_alive,
        "cleanup_recommended": cleanup_recommended,
        "outage_suspected": False,
        "evidence": evidence,
        "warnings": warnings,
        "next_action": next_action,
    }


def progress_http_404_seen(live_progress: dict[str, Any]) -> bool:
    attempts = live_progress.get("attempts") if isinstance(live_progress.get("attempts"), list) else []
    candidates = attempts + [live_progress]
    return any("HTTP 404" in str(item.get("error") or "") for item in candidates if isinstance(item, dict))


def progress_auth_headers(progress: dict[str, Any]) -> dict[str, str]:
    ref = str(progress.get("auth_token_ref") or "")
    env_name = ""
    if ref.startswith("env:"):
        candidate = ref.split(":", 1)[1].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate):
            env_name = candidate
    if not env_name:
        return {}
    token = os.environ.get(env_name, "")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}", "X-Runpod-Progress-Token": token}


def attach_log_tail(
    progress_result: dict[str, Any],
    tail_url: str,
    progress: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: int,
) -> None:
    if progress.get("include_log_tail") is not True:
        return
    if not headers:
        progress_result["log_tail"] = {
            "ok": False,
            "status": "auth_token_unavailable",
            "url": tail_url,
            "bytes": 0,
            "text": "",
            "error": "startup.progress.include_log_tail requires a usable auth_token_ref env value for client-side tail fetch",
        }
        return
    configured_bytes = int(progress.get("log_tail_bytes") or MAX_PROGRESS_TAIL_BYTES)
    max_bytes = max(0, min(configured_bytes, MAX_PROGRESS_TAIL_BYTES))
    progress_result["log_tail"] = fetch_text_url(
        tail_url,
        timeout_seconds=timeout_seconds,
        headers=headers,
        max_bytes=max_bytes,
        redact_tokens=progress_auth_tokens(headers),
    )


def progress_auth_tokens(headers: dict[str, str]) -> list[str]:
    tokens: list[str] = []
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        tokens.append(auth.split(" ", 1)[1])
    header_token = headers.get("X-Runpod-Progress-Token", "")
    if header_token:
        tokens.append(header_token)
    return [token for index, token in enumerate(tokens) if token and token not in tokens[:index]]


def fetch_text_url(
    url: str,
    *,
    timeout_seconds: int,
    headers: dict[str, str] | None = None,
    max_bytes: int,
    redact_tokens: list[str] | None = None,
) -> dict[str, Any]:
    request_headers = {
        "User-Agent": "Mozilla/5.0 runpod-bridge-progress-report",
        "Accept": "text/plain",
    }
    request_headers.update(headers or {})
    try:
        request = Request(url, headers=request_headers)
        with urlopen(request, timeout=timeout_seconds) as response:
            data = response.read(max_bytes + 1)
    except HTTPError as exc:
        return {"ok": False, "status": "http_error", "url": url, "bytes": 0, "text": "", "error": f"HTTP {exc.code}"}
    except (TimeoutError, URLError, OSError) as exc:
        return {"ok": False, "status": "error", "url": url, "bytes": 0, "text": "", "error": f"{exc.__class__.__name__}: {exc}"}
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    return {
        "ok": True,
        "status": "ok",
        "url": url,
        "bytes": len(data[:max_bytes]),
        "truncated": truncated,
        "text": redact_sensitive_text(text, redact_tokens or []),
    }


def redact_sensitive_text(text: str, tokens: list[str] | None = None) -> str:
    return redact_text(text, tokens)


def fetch_json_url(url: str, *, timeout_seconds: int, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request_headers = {
        "User-Agent": "Mozilla/5.0 runpod-bridge-progress-report",
        "Accept": "application/json",
    }
    request_headers.update(headers or {})
    try:
        request = Request(
            url,
            headers=request_headers,
        )
        with urlopen(request, timeout=timeout_seconds) as response:
            data = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return {"ok": False, "status": "http_error", "url": url, "payload": {}, "error": f"HTTP {exc.code}: {detail}"}
    except (TimeoutError, URLError, OSError) as exc:
        return {"ok": False, "status": "error", "url": url, "payload": {}, "error": f"{exc.__class__.__name__}: {exc}"}
    try:
        payload = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "status": "invalid_json", "url": url, "payload": {}, "error": str(exc)}
    return {"ok": True, "status": "ok", "url": url, "payload": payload if isinstance(payload, dict) else {}}
