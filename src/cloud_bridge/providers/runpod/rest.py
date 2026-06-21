from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cloud_bridge.bootstrap_requirements import bootstrap_requirements_report
from cloud_bridge.contract import contract_self_check
from cloud_bridge.launch_env import build_bridge_managed_env, git_source_aliases
from cloud_bridge.manifest import build_plan, validate_manifest
from cloud_bridge.payload import create_request_payload_report
from cloud_bridge.source_check import check_source_reachability, source_proof_report
from cloud_bridge.startup import render_startup_script
from cloud_bridge.util import now, redact
from .catalog import build_gpu_catalog_report_from_manifest, gpu_catalog_launch_blockers, should_check_gpu_catalog
from .runtime import RunpodGraphqlError


DEFAULT_BASE_URL = "https://rest.runpod.io/v1"


class RunpodRestError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class RunpodRestClient:
    def __init__(self, token: str | None = None, base_url: str | None = None, timeout_seconds: int = 60):
        self.token = token or os.environ.get("RUNPOD_API_KEY", "")
        self.base_url = (base_url or os.environ.get("RUNPOD_REST_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def require_token(self) -> None:
        if not self.token:
            raise RunpodRestError("RUNPOD_API_KEY is required for remote RunPod API calls")

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.require_token()
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            clean_query = {key: value for key, value in query.items() if value not in (None, "", [], {})}
            if clean_query:
                url = f"{url}?{urlencode(clean_query, doseq=True)}"
        data = None
        headers = {"Authorization": f"Bearer {self.token}"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
                if not payload:
                    return None
                return json.loads(payload.decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RunpodRestError(f"RunPod API {method} {path} failed with HTTP {exc.code}: {detail}", status_code=exc.code) from exc
        except URLError as exc:
            raise RunpodRestError(f"RunPod API {method} {path} failed: {exc.reason}") from exc

    def list_pods(self, name: str | None = None) -> list[dict[str, Any]]:
        query = {
            "includeMachine": "true",
            "includeNetworkVolume": "true",
        }
        pods = self.request("GET", "/pods", query=query)
        if not isinstance(pods, list):
            return []
        if name:
            return [pod for pod in pods if isinstance(pod, dict) and str(pod.get("name", "")).startswith(name)]
        return [pod for pod in pods if isinstance(pod, dict)]

    def get_pod(self, pod_id: str) -> dict[str, Any]:
        pod = self.request(
            "GET",
            f"/pods/{pod_id}",
            query={
                "includeMachine": "true",
                "includeNetworkVolume": "true",
                "includeSavingsPlans": "true",
                "includeTemplate": "true",
            },
        )
        return pod if isinstance(pod, dict) else {}

    def create_pod(self, body: dict[str, Any]) -> dict[str, Any]:
        pod = self.request("POST", "/pods", body=body)
        return pod if isinstance(pod, dict) else {}

    def stop_pod(self, pod_id: str) -> dict[str, Any]:
        result = self.request("POST", f"/pods/{pod_id}/stop")
        return result if isinstance(result, dict) else {"pod_id": pod_id, "action": "stop"}

    def delete_pod(self, pod_id: str) -> dict[str, Any]:
        result = self.request("DELETE", f"/pods/{pod_id}")
        return result if isinstance(result, dict) else {"pod_id": pod_id, "action": "delete"}

    def billing_pods(self, **query: Any) -> list[dict[str, Any]]:
        billing = self.request("GET", "/billing/pods", query=query)
        return [item for item in billing if isinstance(item, dict)] if isinstance(billing, list) else []

    def billing_endpoints(self, **query: Any) -> list[dict[str, Any]]:
        billing = self.request("GET", "/billing/endpoints", query=query)
        return [item for item in billing if isinstance(item, dict)] if isinstance(billing, list) else []

    def billing_network_volumes(self, **query: Any) -> list[dict[str, Any]]:
        billing = self.request("GET", "/billing/networkvolumes", query=query)
        return [item for item in billing if isinstance(item, dict)] if isinstance(billing, list) else []

    def list_network_volumes(self) -> list[dict[str, Any]]:
        volumes = self.request("GET", "/networkvolumes")
        return [item for item in volumes if isinstance(item, dict)] if isinstance(volumes, list) else []

    def get_network_volume(self, network_volume_id: str) -> dict[str, Any]:
        volume = self.request("GET", f"/networkvolumes/{network_volume_id}")
        return volume if isinstance(volume, dict) else {}

    def create_network_volume(self, body: dict[str, Any]) -> dict[str, Any]:
        volume = self.request("POST", "/networkvolumes", body=body)
        return volume if isinstance(volume, dict) else {}

    def update_network_volume(self, network_volume_id: str, body: dict[str, Any]) -> dict[str, Any]:
        volume = self.request("PATCH", f"/networkvolumes/{network_volume_id}", body=body)
        return volume if isinstance(volume, dict) else {}

    def delete_network_volume(self, network_volume_id: str) -> dict[str, Any]:
        result = self.request("DELETE", f"/networkvolumes/{network_volume_id}")
        return result if isinstance(result, dict) else {"networkVolumeId": network_volume_id, "action": "delete"}

    def list_templates(self) -> list[dict[str, Any]]:
        templates = self.request("GET", "/templates")
        return [item for item in templates if isinstance(item, dict)] if isinstance(templates, list) else []

    def get_template(self, template_id: str) -> dict[str, Any]:
        template = self.request("GET", f"/templates/{template_id}")
        return template if isinstance(template, dict) else {}

    def create_template(self, body: dict[str, Any]) -> dict[str, Any]:
        template = self.request("POST", "/templates", body=body)
        return template if isinstance(template, dict) else {}

    def update_template(self, template_id: str, body: dict[str, Any]) -> dict[str, Any]:
        template = self.request("PATCH", f"/templates/{template_id}", body=body)
        return template if isinstance(template, dict) else {}

    def delete_template(self, template_id: str) -> dict[str, Any]:
        result = self.request("DELETE", f"/templates/{template_id}")
        return result if isinstance(result, dict) else {"templateId": template_id, "action": "delete"}


def build_create_pod_request(manifest: dict[str, Any]) -> dict[str, Any]:
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    repo = manifest.get("repo", {}) if isinstance(manifest.get("repo"), dict) else {}
    budget = manifest.get("budget", {}) if isinstance(manifest.get("budget"), dict) else {}
    worker = manifest.get("worker_coordination", {}) if isinstance(manifest.get("worker_coordination"), dict) else {}

    body: dict[str, Any] = {}
    for key in (
        "cloudType",
        "imageName",
        "templateId",
        "containerDiskInGb",
        "volumeInGb",
        "volumeMountPath",
        "networkVolumeId",
        "containerRegistryAuthId",
        "supportPublicIp",
        "globalNetworking",
        "allowedCudaVersions",
        "countryCodes",
        "dataCenterIds",
        "dataCenterPriority",
        "gpuTypeIds",
        "gpuTypePriority",
        "cpuFlavorIds",
        "cpuFlavorPriority",
        "vcpuCount",
        "minVcpuCount",
        "minVCPUPerGPU",
        "minRAMPerGPU",
        "minDiskBandwidthMBps",
        "minMemoryInGb",
        "minDownloadMbps",
        "minUploadMbps",
        "ports",
        "dockerEntrypoint",
        "interruptible",
        "locked",
        "volumeEncrypted",
    ):
        value = runpod.get(key)
        if key == "dockerEntrypoint" and key in runpod and value == []:
            body[key] = []
        elif value not in (None, "", [], {}):
            body[key] = value

    gpu_count = int(runpod.get("gpuCount") or 0)
    body["computeType"] = "GPU" if gpu_count > 0 else "CPU"
    if gpu_count > 0:
        body["gpuCount"] = gpu_count

    pod_name = str(runpod.get("name") or worker.get("resource_name_prefix") or manifest.get("run_id") or "symphony-runpod")
    body["name"] = pod_name[:191]

    env = runpod.get("env", {})
    body["env"] = resolve_pod_env(env)
    body["env"].update(build_bridge_managed_env(manifest))

    body["dockerStartCmd"] = ["bash", "-lc", render_startup_script(manifest)]
    return body


def resolve_pod_env(raw_env: Any) -> dict[str, str]:
    if not isinstance(raw_env, dict):
        return {}
    resolved: dict[str, str] = {}
    for key, value in raw_env.items():
        env_key = str(key)
        text = str(value)
        if text.startswith("env:"):
            env_name = text.split(":", 1)[1].strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
                resolved[env_key] = ""
                continue
            resolved[env_key] = os.environ.get(env_name, "")
        else:
            resolved[env_key] = text
    return resolved


def build_remote_launch_preview(manifest: dict[str, Any], *, source_report: dict[str, Any] | None = None) -> dict[str, Any]:
    validation = validate_manifest(manifest)
    plan = build_plan(manifest, validation)
    contract = contract_self_check(manifest)
    contract_blockers = [f"{issue['path']}: {issue['message']}" for issue in contract["errors"]]
    plan_with_contract = dict(plan)
    plan_with_contract["blockers"] = [*plan["blockers"], *contract_blockers]
    plan_with_contract["remote_ready"] = bool(plan["remote_ready"] and contract["ok"])
    ok = bool(validation.ok and contract["ok"])
    request_body = build_create_pod_request(manifest) if ok else {}
    payload = create_request_payload_report(request_body) if ok else {}
    bootstrap = bootstrap_requirements_report(manifest) if ok else {}
    source_gate = source_launch_gate(manifest, source_report=source_report)
    retrieval_gate = artifact_retrieval_gate(manifest)
    payload_blockers = [f"{issue['path']}: {issue['message']}" for issue in payload.get("errors", [])]
    payload_warnings = [f"{issue['path']}: {issue['message']}" for issue in payload.get("warnings", [])]
    bootstrap_blockers = [f"{issue['path']}: {issue['message']}" for issue in bootstrap.get("errors", [])]
    bootstrap_warnings = [f"{issue['path']}: {issue['message']}" for issue in bootstrap.get("warnings", [])]
    plan_with_contract["blockers"].extend(payload_blockers)
    plan_with_contract["blockers"].extend(bootstrap_blockers)
    plan_with_contract["blockers"].extend(source_gate["blockers"])
    plan_with_contract["blockers"].extend(retrieval_gate["blockers"])
    plan_with_contract.setdefault("warnings", [])
    plan_with_contract["warnings"] = [
        *plan_with_contract.get("warnings", []),
        *payload_warnings,
        *bootstrap_warnings,
        *source_gate["warnings"],
        *retrieval_gate["warnings"],
    ]
    return {
        "ok": ok,
        "remote_ready": bool(
            plan_with_contract["remote_ready"]
            and not payload_blockers
            and not bootstrap_blockers
            and not source_gate["blockers"]
            and not retrieval_gate["blockers"]
        ),
        "validation": validation.as_dict(),
        "contract": contract,
        "payload": payload,
        "bootstrap_requirements": bootstrap,
        "source_gate": source_gate,
        "artifact_retrieval_gate": retrieval_gate,
        "plan": plan_with_contract,
        "request": redact(request_body),
    }


def create_pod_flow(
    manifest: dict[str, Any],
    *,
    out_dir: str | Path,
    execute: bool,
    max_spend_usd: float | None = None,
    allow_duplicate: bool = False,
    client: RunpodRestClient | None = None,
) -> dict[str, Any]:
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)

    source_report: dict[str, Any] = {}
    if execute and should_execute_source_check(manifest):
        source_report = check_source_reachability(manifest, execute=True)
    preview = build_remote_launch_preview(manifest, source_report=source_report or None)
    request_body = build_create_pod_request(manifest) if preview["ok"] else {}
    record = {
        "ts": now(),
        "action": "create_pod",
        "execute": execute,
        "request": redact(request_body),
        "preview": preview,
        "source_check": source_report,
        "duplicate_check": {},
        "gpu_catalog_check": {},
        "response": {},
    }
    write_json(output / "runpod_resource_record.json", record)

    if not preview["remote_ready"]:
        record["status"] = "blocked"
        record["blockers"] = preview["plan"]["blockers"]
        write_json(output / "runpod_resource_record.json", record)
        return record
    if max_spend_usd is not None:
        budget = manifest.get("budget", {}) if isinstance(manifest.get("budget"), dict) else {}
        estimated = float_or_none(budget.get("max_estimated_cost_usd"))
        if estimated is None or estimated > max_spend_usd:
            record["status"] = "blocked_spend_ceiling"
            record["blockers"] = [f"budget.max_estimated_cost_usd must be <= {max_spend_usd}"]
            write_json(output / "runpod_resource_record.json", record)
            return record
    if not execute:
        record["status"] = "dry_run_request"
        write_json(output / "runpod_resource_record.json", record)
        return record

    api = client or RunpodRestClient()
    prefix = str(get_nested(manifest, ["worker_coordination", "resource_name_prefix"], "") or "")
    try:
        existing_pods = api.list_pods(prefix)
    except RunpodRestError as exc:
        record["status"] = "failed_duplicate_check"
        record["error"] = str(exc)
        record["blockers"] = [f"RunPod duplicate check failed before pod creation: {exc}"]
        write_json(output / "runpod_resource_record.json", record)
        return record

    duplicates = active_duplicate_pods(existing_pods, manifest)
    record["duplicate_check"] = {"prefix": prefix, "active_matches": [summarize_pod(pod) for pod in duplicates]}
    if duplicates and not allow_duplicate:
        record["status"] = "blocked_duplicate"
        write_json(output / "runpod_resource_record.json", record)
        return record

    if should_check_gpu_catalog(manifest):
        try:
            gpu_catalog = build_gpu_catalog_report_from_manifest(manifest)
        except RunpodGraphqlError as exc:
            record["gpu_catalog_check"] = {
                "ok": False,
                "error": str(exc),
                "warning": "RunPod GraphQL gpuTypes probe failed; continuing to REST create because this check is advisory unless it proves a catalog mismatch",
            }
            write_json(output / "runpod_resource_record.json", record)
        else:
            record["gpu_catalog_check"] = redact(gpu_catalog)
            catalog_blockers = gpu_catalog_launch_blockers(gpu_catalog)
            if catalog_blockers:
                record["status"] = "blocked_gpu_catalog"
                record["blockers"] = catalog_blockers
                write_json(output / "runpod_resource_record.json", record)
                return record
            write_json(output / "runpod_resource_record.json", record)

    try:
        pod = api.create_pod(request_body)
    except RunpodRestError as exc:
        record["status"] = "failed_create_request"
        record["error"] = str(exc)
        record["blockers"] = [f"RunPod create request failed; creation state is unknown until pods are listed: {exc}"]
        write_json(output / "runpod_resource_record.json", record)
        return record

    record["response"] = redact(pod)
    pod_id = str(pod.get("id") or "").strip() if isinstance(pod, dict) else ""
    if not pod_id:
        record["status"] = "created_missing_pod_id"
        record["blockers"] = ["RunPod create request returned no pod id; cleanup cannot be targeted until pods are listed"]
        try:
            recovery_candidates = api.list_pods(prefix)
        except RunpodRestError as exc:
            record["recovery_error"] = str(exc)
            recovery_candidates = []
        record["recovery_candidates"] = [summarize_pod(item) for item in active_duplicate_pods(recovery_candidates, manifest)]
        write_json(output / "runpod_resource_record.json", record)
        return record
    record["status"] = "created"
    write_json(output / "runpod_resource_record.json", record)
    return record


def source_launch_gate(manifest: dict[str, Any], *, source_report: dict[str, Any] | None = None) -> dict[str, Any]:
    repo = manifest.get("repo", {}) if isinstance(manifest.get("repo"), dict) else {}
    source = str(repo.get("source") or "")
    if manifest.get("remote_launch_allowed") is not True or source not in git_source_aliases():
        return {"required": False, "ok": True, "blockers": [], "warnings": [], "proof": {}, "check": source_report or {}}
    proof = source_proof_report(manifest)
    if proof.get("ok"):
        return {"required": True, "ok": True, "blockers": [], "warnings": [], "proof": proof, "check": source_report or {}}
    if source_report and source_report.get("ok"):
        return {"required": True, "ok": True, "blockers": [], "warnings": [], "proof": proof, "check": source_report}
    blockers = ["repo.source_proof: executed source reachability proof is required before paid git launch"]
    if source_report and source_report.get("errors"):
        blockers.extend(f"source-check: {item}" for item in source_report.get("errors", []))
    return {"required": True, "ok": False, "blockers": blockers, "warnings": proof.get("warnings", []), "proof": proof, "check": source_report or {}}


def should_execute_source_check(manifest: dict[str, Any]) -> bool:
    repo = manifest.get("repo", {}) if isinstance(manifest.get("repo"), dict) else {}
    if manifest.get("remote_launch_allowed") is not True:
        return False
    if str(repo.get("source") or "") not in git_source_aliases():
        return False
    return not source_proof_report(manifest).get("ok")


def artifact_retrieval_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("remote_launch_allowed") is not True:
        return {"ok": True, "blockers": [], "warnings": []}
    egress = manifest.get("artifact_egress", {}) if isinstance(manifest.get("artifact_egress"), dict) else {}
    mode = str(egress.get("mode") or "workspace_archive")
    if mode in ("aws_s3_presigned_upload", "object_store_upload"):
        return {
            "ok": True,
            "blockers": [],
            "warnings": [
                f"{mode} is an upload path; final success still requires orchestrator-side object existence/download/hash verification and egress_status.status=verified"
            ],
        }
    if mode in ("network_volume", "runpod_network_volume_s3"):
        return {
            "ok": True,
            "blockers": [],
            "warnings": ["network-volume egress preserves artifacts, but closeout still needs an orchestrator pull/hash verification step"],
        }
    if has_live_artifact_channel(manifest):
        return {"ok": True, "blockers": [], "warnings": []}
    return {
        "ok": False,
        "blockers": [
            "artifact_retrieval: remote launch requires durable egress with supported upload or an exposed inspection/progress retrieval channel"
        ],
        "warnings": [],
    }


def has_live_artifact_channel(manifest: dict[str, Any]) -> bool:
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    access = manifest.get("access", {}) if isinstance(manifest.get("access"), dict) else {}
    ports = [str(port) for port in runpod.get("ports", []) if isinstance(port, str)]
    inspection = startup.get("inspection", {}) if isinstance(startup.get("inspection"), dict) else {}
    progress = startup.get("progress", {}) if isinstance(startup.get("progress"), dict) else {}
    inspection_port = int_or_zero(inspection.get("http_artifact_server_port"))
    progress_port = int_or_zero(progress.get("http_status_server_port"))
    hold_seconds = int_or_zero(inspection.get("hold_after_success_seconds"))
    has_inspection = inspection_port > 0 and hold_seconds > 0 and any(port == f"{inspection_port}/http" or port == f"{inspection_port}/tcp" for port in ports)
    has_progress = progress_port > 0 and any(port == f"{progress_port}/http" or port == f"{progress_port}/tcp" for port in ports)
    return bool((has_inspection or has_progress) and (access.get("http_proxy_required") is True or access.get("tcp_ports_required") is True))


def cleanup_pod_flow(
    pod_id: str,
    *,
    out_dir: str | Path,
    action: str,
    execute: bool,
    wait: bool = False,
    timeout_seconds: int = 120,
    interval_seconds: int = 5,
    client: RunpodRestClient | None = None,
) -> dict[str, Any]:
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": now(),
        "action": action,
        "execute": execute,
        "pod_id": pod_id,
        "response": {},
    }
    if not execute:
        record["status"] = "dry_run_request"
        write_json(output / "runpod_cleanup_record.json", record)
        return record
    api = client or RunpodRestClient()
    try:
        if action == "delete":
            record["response"] = redact(api.delete_pod(pod_id))
        elif action == "stop":
            record["response"] = redact(api.stop_pod(pod_id))
        else:
            raise ValueError("cleanup action must be stop or delete")
    except RunpodRestError as exc:
        if action == "delete" and exc.status_code == 404:
            record["response"] = {"pod_id": pod_id, "action": "delete", "already_absent": True}
            record["status"] = "already_absent"
            write_json(output / "runpod_cleanup_record.json", record)
            return record
        raise
    record["status"] = "submitted"
    if wait:
        record["verification"] = verify_cleanup(api, pod_id, action, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)
        if record["verification"].get("ok"):
            record["status"] = "verified"
        else:
            record["status"] = "unverified"
    write_json(output / "runpod_cleanup_record.json", record)
    return record


def verify_cleanup(
    api: RunpodRestClient,
    pod_id: str,
    action: str,
    *,
    timeout_seconds: int = 120,
    interval_seconds: int = 5,
) -> dict[str, Any]:
    if not hasattr(api, "get_pod"):
        return {
            "ok": False,
            "status": "unverifiable_client",
            "pod_id": pod_id,
            "error": "client does not implement get_pod for cleanup verification",
        }
    deadline = time_monotonic() + timeout_seconds
    last_pod: dict[str, Any] = {}
    last_error = ""
    terminal_statuses = set() if action == "delete" else {"EXITED", "STOPPED", "TERMINATED"}
    while time_monotonic() <= deadline:
        try:
            pod = api.get_pod(pod_id)
        except RunpodRestError as exc:
            if action == "delete" and exc.status_code == 404:
                return {"ok": True, "status": "absent", "pod_id": pod_id}
            last_error = str(exc)
        else:
            last_pod = pod
            desired = str(pod.get("desiredStatus") or "")
            if action == "delete" and desired == "TERMINATED":
                last_error = "delete submitted but pod is still returned by get_pod; absence is not verified"
            if desired in terminal_statuses:
                return {"ok": True, "status": desired, "pod": summarize_pod(pod)}
        sleep_seconds(interval_seconds)
    return {
        "ok": False,
        "status": "timeout",
        "pod_id": pod_id,
        "last_pod": summarize_pod(last_pod) if last_pod else {},
        "error": last_error,
    }


def active_duplicate_pods(pods: list[dict[str, Any]], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    worker = manifest.get("worker_coordination", {}) if isinstance(manifest.get("worker_coordination"), dict) else {}
    name = str(runpod.get("name") or "")
    prefix = str(worker.get("resource_name_prefix") or "")
    matches: list[dict[str, Any]] = []
    for pod in pods:
        pod_name = str(pod.get("name") or "")
        status = str(pod.get("desiredStatus") or "")
        same_name = bool(name and pod_name == name)
        same_prefix = bool(prefix and pod_name.startswith(prefix))
        if (same_name or same_prefix) and status != "TERMINATED":
            matches.append(pod)
    return matches


def summarize_pod(pod: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": pod.get("id"),
        "name": pod.get("name"),
        "desiredStatus": pod.get("desiredStatus"),
        "costPerHr": pod.get("costPerHr"),
        "adjustedCostPerHr": pod.get("adjustedCostPerHr"),
        "lastStartedAt": pod.get("lastStartedAt"),
        "lastStatusChange": pod.get("lastStatusChange"),
        "publicIp": pod.get("publicIp"),
        "portMappings": pod.get("portMappings"),
        "networkVolume": pod.get("networkVolume"),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def time_monotonic() -> float:
    return time.monotonic()


def sleep_seconds(seconds: int | float) -> None:
    time.sleep(max(0.1, float(seconds)))


def get_nested(obj: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    current: Any = obj
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_or_zero(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0
