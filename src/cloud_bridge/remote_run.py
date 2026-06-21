from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable

from .progress_report import build_progress_report
from .proxy import pod_from_runtime_report, tcp_endpoint_from_pod, verify_proxy_packet, verify_tcp_packet
from .providers.runpod.rest import (
    RunpodRestClient,
    RunpodRestError,
    cleanup_pod_flow,
    create_pod_flow,
    summarize_pod,
)
from .util import now, redact
from .providers.runpod.s3_verify import verify_network_volume_s3
from .providers.runpod.runtime import RunpodGraphqlError, build_runtime_metrics_report
from .providers import get_adapter
from .providers.base import ProviderLaunchUnsupported


LOCK_KEY_RE = re.compile(r"[^A-Za-z0-9_.-]+")

PacketVerifier = Callable[
    [str, dict[str, Any], str, RunpodRestClient, int, Path, int, int],
    dict[str, Any],
]

DEFAULT_VERIFICATION_PORT = 8000


def run_remote_flow(
    manifest: dict[str, Any],
    *,
    out_dir: str | Path,
    execute: bool,
    max_spend_usd: float | None = None,
    allow_duplicate: bool = False,
    verification_mode: str = "auto",
    port: int = 8000,
    timeout_seconds: int = 180,
    interval_seconds: int = 5,
    cleanup_action: str = "delete",
    cleanup_wait: bool = True,
    cleanup_timeout_seconds: int = 120,
    lock_dir: str | Path | None = None,
    client: RunpodRestClient | None = None,
    packet_verifier: PacketVerifier | None = None,
) -> dict[str, Any]:
    if verification_mode not in ("auto", "tcp", "proxy", "none"):
        raise ValueError("verification_mode must be auto, tcp, proxy, or none")
    if cleanup_action not in ("stop", "delete"):
        raise ValueError("cleanup_action must be stop or delete")
    if execute:
        assert_provider_launch_supported(manifest)

    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    api = client or RunpodRestClient()
    verifier = packet_verifier or default_packet_verifier
    record: dict[str, Any] = {
        "ts": now(),
        "action": "run_remote",
        "execute": execute,
        "manifest_run_id": manifest.get("run_id"),
        "verification_mode": verification_mode,
        "cleanup_action": cleanup_action,
        "create": {},
        "verification": {},
        "cleanup": {},
        "launch_lock": {},
        "status": "started",
    }
    write_record(output, record)

    pod_id = ""
    create_record: dict[str, Any] = {}
    launch_lock: dict[str, Any] = {}
    try:
        if execute and not allow_duplicate:
            launch_lock = acquire_launch_lock(manifest, out_dir=output, lock_dir=lock_dir)
            record["launch_lock"] = redact(launch_lock)
            if not launch_lock.get("acquired"):
                record["status"] = "blocked_launch_lock"
                write_record(output, record)
                return record
            write_record(output, record)

        create_record = create_pod_flow(
            manifest,
            out_dir=output / "create",
            execute=execute,
            max_spend_usd=max_spend_usd,
            allow_duplicate=allow_duplicate,
            client=api,
        )
        record["create"] = compact_create_record(create_record, output / "create" / "runpod_resource_record.json")
        pod_id = str(create_record.get("response", {}).get("id") or "")
        write_record(output, record)

        if create_record.get("status") != "created":
            record["status"] = str(create_record.get("status") or "create_not_created")
            write_record(output, record)
            return record

        if verification_mode == "none":
            record["status"] = "created_unverified"
            write_record(output, record)
            return record

        try:
            verification = verifier(
                verification_mode,
                manifest,
                pod_id,
                api,
                port,
                output / "packet",
                timeout_seconds,
                interval_seconds,
            )
        except Exception as exc:  # Keep cleanup and audit records even if packet retrieval crashes.
            verification = {"ok": False, "error": str(exc), "error_type": exc.__class__.__name__}
            record["verification"] = redact(verification)
            record["status"] = "verification_error"
            write_record(output, record)
            return record
        record["verification"] = redact(verification)
        if artifact_verification_succeeded(verification):
            record["status"] = "artifacts_verified_cleanup_pending"
        else:
            record["status"] = "verification_failed"
        write_record(output, record)
        return record
    finally:
        if execute and pod_id:
            try:
                cleanup = cleanup_pod_flow(
                    pod_id,
                    out_dir=output / "cleanup",
                    action=cleanup_action,
                    execute=True,
                    wait=cleanup_wait,
                    timeout_seconds=cleanup_timeout_seconds,
                    interval_seconds=interval_seconds,
                    client=api,
                )
            except (RunpodRestError, ValueError) as exc:
                cleanup = {"status": "failed", "error": str(exc), "pod_id": pod_id, "action": cleanup_action}
                record["status"] = "cleanup_failed"
            record["cleanup"] = cleanup
            if cleanup_action == "stop" and cleanup.get("status") in ("verified", "already_absent"):
                # a stopped pod still bills for container disk + any attached network volume
                record["residual_billing"] = {
                    "cost_fully_stopped": False,
                    "warning": "pod was stopped, not deleted; a stopped RunPod pod and any attached "
                    "network volume can still bill - delete to stop all charges",
                }
            if record.get("status") == "artifacts_verified_cleanup_pending":
                if cleanup.get("status") in ("verified", "already_absent"):
                    record["status"] = "succeeded"
                else:
                    record["status"] = "cleanup_unverified"
            write_record(output, record)
        elif execute and not allow_duplicate and create_record.get("status") == "created_missing_pod_id":
            # S2: create returned no id but a pod may exist; the duplicate check already passed,
            # so any pod matching this run's prefix is ours - delete it instead of orphaning it.
            record["cleanup"] = cleanup_orphan_pods(
                create_record.get("recovery_candidates", []),
                out_dir=output / "cleanup",
                client=api,
                wait=cleanup_wait,
                timeout_seconds=cleanup_timeout_seconds,
                interval_seconds=interval_seconds,
            )
            record["status"] = record["cleanup"].get("run_status", record.get("status"))
            write_record(output, record)
        if launch_lock.get("acquired"):
            release_launch_lock(launch_lock)
            record["launch_lock"] = redact(launch_lock)
            write_record(output, record)


def assert_provider_launch_supported(manifest: dict[str, Any]) -> None:
    """Gate paid execution to providers with automated launch support."""
    provider = "runpod"
    block = manifest.get("provider")
    if isinstance(block, dict) and block.get("name"):
        provider = str(block["name"])
    try:
        adapter = get_adapter(provider)
    except KeyError as exc:
        raise ProviderLaunchUnsupported(provider, reason=str(exc)) from exc
    adapter.assert_launch_supported()


def cleanup_orphan_pods(
    candidates: list[dict[str, Any]],
    *,
    out_dir: Path,
    client: RunpodRestClient,
    wait: bool,
    timeout_seconds: int,
    interval_seconds: int,
) -> dict[str, Any]:
    """Delete pods matching this run's prefix after a create returned no pod id (S2)."""
    ids = [str(c.get("id") or "") for c in candidates if isinstance(c, dict) and c.get("id")]
    if not ids:
        return {"status": "no_orphans_found", "run_status": "created_missing_pod_id_no_orphan", "deleted": []}
    results: list[dict[str, Any]] = []
    all_ok = True
    for index, orphan_id in enumerate(ids):
        try:
            res = cleanup_pod_flow(
                orphan_id,
                out_dir=out_dir / f"orphan_{index}",
                action="delete",
                execute=True,
                wait=wait,
                timeout_seconds=timeout_seconds,
                interval_seconds=interval_seconds,
                client=client,
            )
        except (RunpodRestError, ValueError) as exc:
            res = {"status": "failed", "error": str(exc), "pod_id": orphan_id}
        results.append(res)
        if res.get("status") not in ("verified", "already_absent"):
            all_ok = False
    return {
        "status": "orphans_deleted" if all_ok else "orphan_cleanup_unverified",
        "run_status": "created_missing_pod_id_cleaned" if all_ok else "created_missing_pod_id_cleanup_unverified",
        "deleted": results,
    }


def default_packet_verifier(
    mode: str,
    manifest: dict[str, Any],
    pod_id: str,
    api: RunpodRestClient,
    port: int,
    out_dir: Path,
    timeout_seconds: int,
    interval_seconds: int,
) -> dict[str, Any]:
    artifact_port = verification_port_for_artifacts(manifest, port)
    egress_mode = str((manifest.get("artifact_egress", {}) if isinstance(manifest.get("artifact_egress"), dict) else {}).get("mode") or "")
    if mode == "auto" and egress_mode == "runpod_network_volume_s3":
        result = verify_network_volume_s3(
            manifest,
            out_dir=out_dir / "network-volume-s3",
            execute=True,
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
        )
        closeout = result.get("closeout", {}) if isinstance(result.get("closeout"), dict) else {}
        return {
            "ok": result.get("ok") is True,
            "mode": "runpod_network_volume_s3",
            "pod_id": pod_id,
            "status": closeout.get("workload_status", {}) if isinstance(closeout.get("workload_status"), dict) else {},
            "closeout": closeout,
            "s3_verification": result,
        }

    progress_dir = out_dir.parent if out_dir.parent != Path("") else out_dir
    progress_callback = lambda: persist_progress_snapshot(
        manifest,
        pod_id,
        api,
        progress_dir,
        mode="auto" if mode == "auto" else mode,
        progress_timeout_seconds=min(3, max(1, interval_seconds)),
    )
    if mode == "tcp":
        return verify_tcp_with_client(
            manifest,
            pod_id,
            api,
            artifact_port,
            out_dir / "tcp",
            timeout_seconds,
            interval_seconds,
            progress_callback=progress_callback,
        )
    if mode == "proxy":
        result = verify_proxy_packet(
            manifest,
            pod_id,
            port=artifact_port,
            out_dir=out_dir / "proxy",
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
            progress_callback=progress_callback,
        )
        result["mode"] = "proxy"
        return result

    tcp_result = verify_tcp_with_client(
        manifest,
        pod_id,
        api,
        artifact_port,
        out_dir / "tcp",
        timeout_seconds,
        interval_seconds,
        progress_callback=progress_callback,
    )
    if tcp_result.get("ok"):
        tcp_result["mode"] = "tcp"
        tcp_result["fallback_attempted"] = False
        return tcp_result

    proxy_result = verify_proxy_packet(
        manifest,
        pod_id,
        port=artifact_port,
        out_dir=out_dir / "proxy",
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        progress_callback=progress_callback,
    )
    proxy_result["mode"] = "proxy"
    proxy_result["fallback_from"] = redact(tcp_result)
    return proxy_result


def verification_port_for_artifacts(manifest: dict[str, Any], requested_port: int) -> int:
    if requested_port != DEFAULT_VERIFICATION_PORT:
        return requested_port
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    inspection = startup.get("inspection", {}) if isinstance(startup.get("inspection"), dict) else {}
    try:
        configured_port = int(inspection.get("http_artifact_server_port") or 0)
    except (TypeError, ValueError):
        configured_port = 0
    return configured_port or requested_port


def verify_tcp_with_client(
    manifest: dict[str, Any],
    pod_id: str,
    api: RunpodRestClient,
    port: int,
    out_dir: Path,
    timeout_seconds: int,
    interval_seconds: int,
    progress_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    pod: dict[str, Any] = {}
    last_error = ""
    while time.monotonic() <= deadline:
        if progress_callback is not None:
            try:
                progress_callback()
            except Exception:
                pass
        try:
            pod = api.get_pod(pod_id)
        except RunpodRestError as exc:
            last_error = str(exc)
        else:
            verify_pod = pod if tcp_endpoint_from_pod(pod, port) else pod_from_runtime_report(fetch_runtime_for_tcp_mapping(pod_id), pod_id)
            if tcp_endpoint_from_pod(verify_pod, port):
                remaining = max(1, int(deadline - time.monotonic()))
                result = verify_tcp_packet(
                    manifest,
                    verify_pod,
                    internal_port=port,
                    out_dir=out_dir,
                    timeout_seconds=remaining,
                    interval_seconds=interval_seconds,
                    progress_callback=progress_callback,
                )
                result["mode"] = "tcp"
                return result
        time.sleep(interval_seconds)

    result = {
        "ok": False,
        "mode": "tcp",
        "pod_id": pod_id,
        "port": port,
        "base_url": "",
        "status": {},
        "status_fetch": {},
        "fetches": [],
        "closeout": {},
        "last_pod": summarize_pod(pod) if pod else {},
        "error": last_error or f"could not resolve external TCP mapping for internal port {port}",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tcp_verify.json").write_text(json.dumps(redact(result), indent=2, sort_keys=True) + "\n")
    return result


def fetch_runtime_for_tcp_mapping(pod_id: str) -> dict[str, Any]:
    try:
        return build_runtime_metrics_report(pod_id)
    except RunpodGraphqlError:
        return {}


def persist_progress_snapshot(
    manifest: dict[str, Any],
    pod_id: str,
    api: RunpodRestClient,
    out_dir: str | Path,
    *,
    mode: str = "auto",
    progress_timeout_seconds: int = 3,
) -> dict[str, Any]:
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    previous = read_json(output / "remote_progress_latest.json")
    try:
        report = build_progress_report(
            manifest,
            pod_id,
            previous_report=previous,
            client=api,
            mode=mode,
            progress_timeout_seconds=progress_timeout_seconds,
        )
    except Exception as exc:
        report = {
            "checked_at": now(),
            "pod_id": pod_id,
            "run_id": manifest.get("run_id"),
            "classification": {
                "state": "progress_report_error",
                "workload_progressing": False,
                "monitor_alive": False,
                "cleanup_recommended": False,
                "outage_suspected": False,
                "evidence": [],
                "warnings": [f"{exc.__class__.__name__}: {exc}"],
                "next_action": "retry_progress_report_from_trusted_network_or_use_ssh_log_tail",
            },
        }
    redacted = redact(report)
    (output / "remote_progress_latest.json").write_text(json.dumps(redacted, indent=2, sort_keys=True) + "\n")
    with (output / "remote_progress.jsonl").open("a") as handle:
        handle.write(json.dumps(redacted, sort_keys=True) + "\n")
    return redacted


def artifact_verification_succeeded(verification: dict[str, Any]) -> bool:
    if verification.get("ok") is not True:
        return False
    status = verification.get("status", {}) if isinstance(verification.get("status"), dict) else {}
    if str(status.get("status") or "") not in ("succeeded", "success", "completed"):
        return False
    closeout = verification.get("closeout", {}) if isinstance(verification.get("closeout"), dict) else {}
    if closeout.get("status") != "succeeded":
        return False
    archive_materialization = verification.get("archive_materialization")
    if isinstance(archive_materialization, dict) and archive_materialization.get("ok") is not True:
        return False
    presigned_egress = verification.get("presigned_egress_verification")
    if isinstance(presigned_egress, dict) and presigned_egress.get("ok") is not True:
        return False
    artifacts = closeout.get("artifacts", [])
    if not isinstance(artifacts, list) or not artifacts:
        return False
    if closeout.get("missing_required_artifacts"):
        return False
    required_artifacts = [artifact for artifact in artifacts if isinstance(artifact, dict) and artifact.get("required") is True]
    if not required_artifacts:
        return False
    for artifact in required_artifacts:
        if artifact.get("present") is not True or not artifact.get("sha256"):
            return False
    return True


def write_record(output: Path, record: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "remote_run_record.json").write_text(json.dumps(redact(record), indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def acquire_launch_lock(
    manifest: dict[str, Any],
    *,
    out_dir: str | Path,
    lock_dir: str | Path | None = None,
) -> dict[str, Any]:
    directory = resolve_lock_dir(lock_dir)
    directory.mkdir(parents=True, exist_ok=True)
    key = launch_lock_key(manifest)
    path = directory / f"{key}.lock.json"
    owner_id = f"{os.getpid()}-{time.time_ns()}"
    payload = {
        "ts": now(),
        "owner_id": owner_id,
        "pid": os.getpid(),
        "key": key,
        "run_id": manifest.get("run_id"),
        "resource_name_prefix": nested_str(manifest, "worker_coordination", "resource_name_prefix"),
        "runpod_name": nested_str(manifest, "runpod", "name"),
        "out_dir": str(Path(out_dir).resolve()),
    }
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return {
            "status": "held",
            "acquired": False,
            "key": key,
            "path": str(path),
            "existing": read_existing_lock(path),
        }
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(redact(payload), indent=2, sort_keys=True) + "\n")
    return {
        "status": "acquired",
        "acquired": True,
        "key": key,
        "path": str(path),
        "owner_id": owner_id,
    }


def release_launch_lock(lock: dict[str, Any]) -> None:
    path_value = str(lock.get("path") or "")
    if not path_value:
        return
    path = Path(path_value)
    try:
        existing = json.loads(path.read_text())
    except FileNotFoundError:
        lock["released"] = True
        lock["release_note"] = "already_absent"
        return
    except json.JSONDecodeError:
        lock["released"] = False
        lock["release_error"] = "lock file is not valid JSON"
        return
    if existing.get("owner_id") != lock.get("owner_id"):
        lock["released"] = False
        lock["release_error"] = "lock owner mismatch"
        return
    path.unlink()
    lock["released"] = True
    lock["status"] = "released"


def resolve_lock_dir(lock_dir: str | Path | None) -> Path:
    if lock_dir is not None:
        return Path(lock_dir).expanduser().resolve()
    configured = os.environ.get("RUNPOD_BRIDGE_LOCK_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".cache" / "runpod-bridge" / "locks"


def launch_lock_key(manifest: dict[str, Any]) -> str:
    raw = (
        nested_str(manifest, "worker_coordination", "resource_name_prefix")
        or nested_str(manifest, "runpod", "name")
        or str(manifest.get("run_id") or "unnamed-run")
    )
    clean = LOCK_KEY_RE.sub("-", raw).strip("-._") or "unnamed-run"
    return clean[:120]


def nested_str(obj: dict[str, Any], section: str, key: str) -> str:
    value = obj.get(section, {}) if isinstance(obj.get(section), dict) else {}
    if not isinstance(value, dict):
        return ""
    return str(value.get(key) or "")


def read_existing_lock(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {"status": "missing_after_conflict"}
    except json.JSONDecodeError:
        return {"status": "unreadable", "path": str(path)}
    return redact(data) if isinstance(data, dict) else {"status": "invalid_shape", "path": str(path)}


def compact_create_record(record: dict[str, Any], record_path: Path) -> dict[str, Any]:
    response = record.get("response", {}) if isinstance(record.get("response"), dict) else {}
    compact: dict[str, Any] = {
        "status": record.get("status"),
        "record_path": str(record_path.resolve()),
        "pod_id": response.get("id"),
        "pod": summarize_pod(response) if response else {},
        "duplicate_check": record.get("duplicate_check", {}),
    }
    for key in ("blockers", "error"):
        if record.get(key):
            compact[key] = record[key]
    return compact
