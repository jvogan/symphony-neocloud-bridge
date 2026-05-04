from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable

from .proxy import tcp_endpoint_from_pod, verify_proxy_packet, verify_tcp_packet
from .runpod_rest import (
    RunpodRestClient,
    RunpodRestError,
    cleanup_pod_flow,
    create_pod_flow,
    now,
    redact,
    summarize_pod,
)


LOCK_KEY_RE = re.compile(r"[^A-Za-z0-9_.-]+")

PacketVerifier = Callable[
    [str, dict[str, Any], str, RunpodRestClient, int, Path, int, int],
    dict[str, Any],
]


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
    lock_dir: str | Path | None = None,
    client: RunpodRestClient | None = None,
    packet_verifier: PacketVerifier | None = None,
) -> dict[str, Any]:
    if verification_mode not in ("auto", "tcp", "proxy", "none"):
        raise ValueError("verification_mode must be auto, tcp, proxy, or none")
    if cleanup_action not in ("stop", "delete"):
        raise ValueError("cleanup_action must be stop or delete")

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
        if verification.get("ok"):
            record["status"] = "succeeded"
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
                    client=api,
                )
            except (RunpodRestError, ValueError) as exc:
                cleanup = {"status": "failed", "error": str(exc), "pod_id": pod_id, "action": cleanup_action}
                record["status"] = "cleanup_failed"
            record["cleanup"] = cleanup
            write_record(output, record)
        if launch_lock.get("acquired"):
            release_launch_lock(launch_lock)
            record["launch_lock"] = redact(launch_lock)
            write_record(output, record)


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
    if mode == "tcp":
        return verify_tcp_with_client(manifest, pod_id, api, port, out_dir / "tcp", timeout_seconds, interval_seconds)
    if mode == "proxy":
        result = verify_proxy_packet(
            manifest,
            pod_id,
            port=port,
            out_dir=out_dir / "proxy",
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
        )
        result["mode"] = "proxy"
        return result

    tcp_result = verify_tcp_with_client(manifest, pod_id, api, port, out_dir / "tcp", timeout_seconds, interval_seconds)
    if tcp_result.get("ok"):
        tcp_result["mode"] = "tcp"
        tcp_result["fallback_attempted"] = False
        return tcp_result

    proxy_result = verify_proxy_packet(
        manifest,
        pod_id,
        port=port,
        out_dir=out_dir / "proxy",
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )
    proxy_result["mode"] = "proxy"
    proxy_result["fallback_from"] = redact(tcp_result)
    return proxy_result


def verify_tcp_with_client(
    manifest: dict[str, Any],
    pod_id: str,
    api: RunpodRestClient,
    port: int,
    out_dir: Path,
    timeout_seconds: int,
    interval_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    pod: dict[str, Any] = {}
    last_error = ""
    while time.monotonic() <= deadline:
        try:
            pod = api.get_pod(pod_id)
        except RunpodRestError as exc:
            last_error = str(exc)
        else:
            if tcp_endpoint_from_pod(pod, port):
                remaining = max(1, int(deadline - time.monotonic()))
                result = verify_tcp_packet(
                    manifest,
                    pod,
                    internal_port=port,
                    out_dir=out_dir,
                    timeout_seconds=remaining,
                    interval_seconds=interval_seconds,
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


def write_record(output: Path, record: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "remote_run_record.json").write_text(json.dumps(redact(record), indent=2, sort_keys=True) + "\n")


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
