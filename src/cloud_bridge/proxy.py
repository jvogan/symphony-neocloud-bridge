from __future__ import annotations

from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import tarfile
import time
from typing import Any, Callable
from http.client import RemoteDisconnected
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .closeout import packet_relative_path, write_closeout_files
from .util import redact_text


def proxy_url(pod_id: str, port: int, remote_path: str) -> str:
    clean_path = remote_path.lstrip("/")
    encoded_path = quote(clean_path)
    return f"https://{pod_id}-{port}.proxy.runpod.net/{encoded_path}"


def tcp_url(host: str, external_port: int, remote_path: str) -> str:
    clean_path = remote_path.lstrip("/")
    encoded_path = quote(clean_path)
    return f"http://{host}:{external_port}/{encoded_path}"


def fetch_proxy_file(pod_id: str, port: int, remote_path: str, output_path: str | Path, timeout_seconds: int = 30) -> dict[str, Any]:
    url = proxy_url(pod_id, port, remote_path)
    return fetch_url_file(url, output_path, timeout_seconds=timeout_seconds)


def fetch_tcp_file(host: str, external_port: int, remote_path: str, output_path: str | Path, timeout_seconds: int = 30) -> dict[str, Any]:
    url = tcp_url(host, external_port, remote_path)
    return fetch_url_file(url, output_path, timeout_seconds=timeout_seconds)


def fetch_url_file(url: str, output_path: str | Path, timeout_seconds: int = 30) -> dict[str, Any]:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() <= deadline:
        remaining = max(1, min(10, int(deadline - time.monotonic()) + 1))
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 runpod-bridge-proxy-verifier",
                    "Accept": "*/*",
                },
            )
            with urlopen(request, timeout=remaining) as response:
                data = response.read()
            output.write_bytes(data)
            return {"ok": True, "url": url, "output_path": str(output), "bytes": len(data)}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            last_error = f"HTTP {exc.code}: {detail}"
            if exc.code in (401, 403):
                return {"ok": False, "url": url, "output_path": str(output), "error": last_error}
        except URLError as exc:
            last_error = str(exc.reason)
        except (TimeoutError, ConnectionError, RemoteDisconnected, OSError) as exc:
            last_error = f"{exc.__class__.__name__}: {exc}"
        sleep_seconds = min(2, max(0, deadline - time.monotonic()))
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return {"ok": False, "url": url, "output_path": str(output), "error": last_error or "timeout"}


def verify_proxy_packet(
    manifest: dict[str, Any],
    pod_id: str,
    *,
    port: int,
    out_dir: str | Path,
    timeout_seconds: int = 180,
    interval_seconds: int = 5,
    progress_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    status_path = str(startup.get("status_file") or "runpod-execution/status.json")

    deadline = time.monotonic() + timeout_seconds
    status: dict[str, Any] = {}
    status_fetch: dict[str, Any] = {}
    while time.monotonic() <= deadline:
        call_progress_callback(progress_callback)
        local_status_path = output / packet_relative_path(status_path)
        status_fetch = fetch_proxy_file(pod_id, port, status_path, local_status_path, timeout_seconds=2)
        if status_fetch["ok"]:
            status = read_json(local_status_path)
            if status.get("status") in ("succeeded", "success", "completed", "failed"):
                break
        time.sleep(interval_seconds)

    paths = required_proxy_paths(manifest)
    fetches = []
    for remote_path in paths:
        fetches.append(fetch_proxy_file(pod_id, port, remote_path, output / packet_relative_path(remote_path), timeout_seconds=10))

    archive_materialization = materialize_workspace_archive(manifest, output)
    if archive_materialization.get("ok"):
        status = read_json(output / packet_relative_path(status_path)) or status

    presigned_egress = verify_presigned_upload_egress(manifest, output)
    closeout = write_closeout_files(manifest, output)
    archive_ok = archive_materialization.get("ok") is True
    result = {
        "ok": (
            archive_ok
            and presigned_egress.get("ok") is True
            and closeout["status"] == "succeeded"
            and status.get("status") in ("succeeded", "success", "completed")
        ),
        "pod_id": pod_id,
        "port": port,
        "base_url": f"https://{pod_id}-{port}.proxy.runpod.net",
        "status": status,
        "status_fetch": status_fetch,
        "fetches": fetches,
        "archive_materialization": archive_materialization,
        "presigned_egress_verification": presigned_egress,
        "closeout": closeout,
    }
    (output / "proxy_verify.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def verify_tcp_packet(
    manifest: dict[str, Any],
    pod: dict[str, Any],
    *,
    internal_port: int,
    out_dir: str | Path,
    timeout_seconds: int = 180,
    interval_seconds: int = 5,
    progress_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    endpoint = tcp_endpoint_from_pod(pod, internal_port)
    if endpoint is None:
        result = {
            "ok": False,
            "pod_id": pod.get("id"),
            "port": internal_port,
            "base_url": "",
            "status": {},
            "status_fetch": {},
            "fetches": [],
            "closeout": write_closeout_files(manifest, output),
            "error": f"could not resolve external TCP mapping for internal port {internal_port}",
        }
        (output / "tcp_verify.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        return result

    host, external_port = endpoint
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    status_path = str(startup.get("status_file") or "runpod-execution/status.json")

    deadline = time.monotonic() + timeout_seconds
    status: dict[str, Any] = {}
    status_fetch: dict[str, Any] = {}
    while time.monotonic() <= deadline:
        call_progress_callback(progress_callback)
        local_status_path = output / packet_relative_path(status_path)
        status_fetch = fetch_tcp_file(host, external_port, status_path, local_status_path, timeout_seconds=2)
        if status_fetch["ok"]:
            status = read_json(local_status_path)
            if status.get("status") in ("succeeded", "success", "completed", "failed"):
                break
        time.sleep(interval_seconds)

    paths = required_proxy_paths(manifest)
    fetches = []
    for remote_path in paths:
        fetches.append(fetch_tcp_file(host, external_port, remote_path, output / packet_relative_path(remote_path), timeout_seconds=10))

    archive_materialization = materialize_workspace_archive(manifest, output)
    if archive_materialization.get("ok"):
        status = read_json(output / packet_relative_path(status_path)) or status

    presigned_egress = verify_presigned_upload_egress(manifest, output)
    closeout = write_closeout_files(manifest, output)
    archive_ok = archive_materialization.get("ok") is True
    result = {
        "ok": (
            archive_ok
            and presigned_egress.get("ok") is True
            and closeout["status"] == "succeeded"
            and status.get("status") in ("succeeded", "success", "completed")
        ),
        "pod_id": pod.get("id"),
        "port": internal_port,
        "external_port": external_port,
        "base_url": f"http://{host}:{external_port}",
        "status": status,
        "status_fetch": status_fetch,
        "fetches": fetches,
        "archive_materialization": archive_materialization,
        "presigned_egress_verification": presigned_egress,
        "closeout": closeout,
    }
    (output / "tcp_verify.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def tcp_endpoint_from_pod(pod: dict[str, Any], internal_port: int) -> tuple[str, int] | None:
    public_ip = first_string(pod, "publicIp", "public_ip", "ip")
    mapping = tcp_endpoint_from_mapping(pod.get("portMappings"), internal_port, public_ip)
    if mapping:
        return mapping
    return tcp_endpoint_from_mapping(pod, internal_port, public_ip)


def pod_from_runtime_report(runtime_report: dict[str, Any], pod_id: str = "") -> dict[str, Any]:
    metrics = runtime_report.get("metrics", {}) if isinstance(runtime_report.get("metrics"), dict) else {}
    ports = metrics.get("ports", []) if isinstance(metrics.get("ports"), list) else []
    return {
        "id": pod_id or str(metrics.get("id") or ""),
        "name": metrics.get("name"),
        "portMappings": ports,
    }


def tcp_endpoint_from_runtime_report(runtime_report: dict[str, Any], internal_port: int, pod_id: str = "") -> tuple[str, int] | None:
    return tcp_endpoint_from_pod(pod_from_runtime_report(runtime_report, pod_id), internal_port)


def call_progress_callback(progress_callback: Callable[[], None] | None) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback()
    except Exception:
        return


def tcp_endpoint_from_mapping(value: Any, internal_port: int, default_host: str) -> tuple[str, int] | None:
    if isinstance(value, dict):
        direct = direct_mapping(value, internal_port, default_host)
        if direct:
            return direct
        candidate = mapping_candidate(value, internal_port, default_host)
        if candidate:
            return candidate
        for item in value.values():
            nested = tcp_endpoint_from_mapping(item, internal_port, default_host)
            if nested:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = tcp_endpoint_from_mapping(item, internal_port, default_host)
            if nested:
                return nested
    return None


def direct_mapping(value: dict[str, Any], internal_port: int, default_host: str) -> tuple[str, int] | None:
    for key, item in value.items():
        key_text = str(key)
        if key_text not in (str(internal_port), f"{internal_port}/tcp"):
            continue
        if isinstance(item, int):
            return (default_host, item) if default_host else None
        if isinstance(item, str):
            parsed = parse_host_port(item, default_host)
            if parsed:
                return parsed
        if isinstance(item, dict):
            candidate = mapping_candidate(item, internal_port, default_host)
            if candidate:
                return candidate
    return None


def mapping_candidate(value: dict[str, Any], internal_port: int, default_host: str) -> tuple[str, int] | None:
    protocol = first_string(value, "protocol", "type", "scheme").lower()
    if protocol and protocol not in ("tcp", "http"):
        return None
    internal = first_int(value, "privatePort", "containerPort", "internalPort", "podPort", "targetPort")
    if internal != internal_port:
        return None
    external = first_int(value, "publicPort", "externalPort", "hostPort", "mappedPort", "port")
    if external is None:
        return None
    host = first_string(value, "ip", "host", "hostname", "publicIp", "public_ip") or default_host
    if not host:
        return None
    return host, external


def first_string(value: dict[str, Any], *keys: str) -> str:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def first_int(value: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        item = value.get(key)
        try:
            if item not in (None, ""):
                return int(item)
        except (TypeError, ValueError):
            continue
    return None


def parse_host_port(value: str, default_host: str) -> tuple[str, int] | None:
    if ":" in value:
        host, port = value.rsplit(":", 1)
    else:
        host, port = default_host, value
    try:
        external = int(port)
    except ValueError:
        return None
    if not host:
        return None
    return host, external


def required_proxy_paths(manifest: dict[str, Any]) -> list[str]:
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    artifact_egress = manifest.get("artifact_egress", {}) if isinstance(manifest.get("artifact_egress"), dict) else {}
    paths = [
        str(startup.get("status_file") or "runpod-execution/status.json"),
        str(startup.get("heartbeat_file") or "runpod-execution/monitor_events.ndjson"),
        str(startup.get("log_file") or "runpod-execution/logs/startup.log"),
        "runpod-execution/artifact_hashes.jsonl",
        "runpod-execution/egress_status.json",
    ]
    for artifact in manifest.get("expected_artifacts", []):
        if isinstance(artifact, dict) and artifact.get("path"):
            paths.append(str(artifact["path"]))
    if artifact_egress.get("mode") == "workspace_archive" and artifact_egress.get("archive_path"):
        paths.append(str(artifact_egress["archive_path"]))
    return dedupe(paths)


def materialize_workspace_archive(manifest: dict[str, Any], output: Path) -> dict[str, Any]:
    artifact_egress = manifest.get("artifact_egress", {}) if isinstance(manifest.get("artifact_egress"), dict) else {}
    mode = str(artifact_egress.get("mode") or "workspace_archive")
    archive_path = str(artifact_egress.get("archive_path") or "")
    if mode != "workspace_archive" or not archive_path:
        return {"ok": True, "attempted": False, "reason": "workspace archive not configured"}

    local_archive = output / packet_relative_path(archive_path)
    if not local_archive.is_file():
        return {
            "ok": False,
            "attempted": True,
            "archive_path": str(local_archive),
            "error": "workspace archive was not fetched",
        }

    try:
        extracted = safe_extract_archive(local_archive, output)
    except (tarfile.TarError, OSError) as exc:
        return {
            "ok": False,
            "attempted": True,
            "archive_path": str(local_archive),
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        }

    return {
        "ok": True,
        "attempted": True,
        "archive_path": str(local_archive),
        "extracted_files": extracted["files"],
        "skipped_members": extracted["skipped"],
    }


def safe_extract_archive(archive_path: Path, destination: Path) -> dict[str, list[str]]:
    dest = destination.resolve()
    archive_resolved = archive_path.resolve()
    extracted: list[str] = []
    skipped: list[str] = []
    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive.getmembers():
            member_name = str(member.name or "")
            if not member_name:
                skipped.append(member_name)
                continue
            candidate = Path(member_name)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise tarfile.TarError(f"unsafe archive member path: {member_name}")
            target = (dest / candidate).resolve()
            if dest != target and dest not in target.parents:
                raise tarfile.TarError(f"unsafe archive member path: {member_name}")
            if target == archive_resolved:
                skipped.append(member_name)
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.isfile():
                if target.exists():
                    skipped.append(member_name)
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise tarfile.TarError(f"could not read archive member: {member_name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as handle:
                    handle.write(source.read())
                extracted.append(member_name)
                continue
            if member.islnk() or member.issym():
                skipped.append(member_name)
                continue
            raise tarfile.TarError(f"unsafe archive member type: {member_name}")
    return {"files": extracted, "skipped": skipped}


def verify_presigned_upload_egress(manifest: dict[str, Any], output: Path) -> dict[str, Any]:
    artifact_egress = manifest.get("artifact_egress", {}) if isinstance(manifest.get("artifact_egress"), dict) else {}
    if str(artifact_egress.get("mode") or "") != "aws_s3_presigned_upload":
        return {"ok": True, "attempted": False, "reason": "presigned upload egress not configured"}

    archive_path = str(artifact_egress.get("archive_path") or "")
    archive_uri = resolve_verify_uri(
        artifact_egress,
        "archive_verify_uri",
        "archive_verify_uri_ref",
        "RUNPOD_PRESIGNED_ARCHIVE_VERIFY_URI",
    )
    hash_uri = resolve_verify_uri(
        artifact_egress,
        "hash_verify_uri",
        "hash_verify_uri_ref",
        "RUNPOD_PRESIGNED_HASH_VERIFY_URI",
    )
    if not archive_path:
        return {"ok": False, "attempted": True, "error": "artifact_egress.archive_path is required for presigned egress verification"}
    if not archive_uri:
        return {
            "ok": False,
            "attempted": True,
            "error": "archive_verify_uri or archive_verify_uri_ref is required for presigned egress verification",
        }

    egress_status_path = output / "runpod-execution" / "egress_status.json"
    pod_egress_status = read_json(egress_status_path)
    if str(pod_egress_status.get("status") or "") not in ("uploaded", "verified"):
        return {
            "ok": False,
            "attempted": True,
            "error": "pod egress_status must be uploaded before trusted verification",
            "pod_egress_status": pod_egress_status,
        }

    verify_dir = output / "trusted-egress"
    verify_dir.mkdir(parents=True, exist_ok=True)
    downloaded_archive = verify_dir / "runpod-execution.tar.gz"
    archive_download = download_verify_uri(archive_uri, downloaded_archive)
    if archive_download.get("ok") is not True:
        return {"ok": False, "attempted": True, "archive_download": archive_download, "pod_egress_status": pod_egress_status}

    archive_sha = sha256_file(downloaded_archive)
    local_archive = output / packet_relative_path(archive_path)
    local_archive_sha = sha256_file(local_archive) if local_archive.is_file() else ""
    if local_archive_sha and local_archive_sha != archive_sha:
        return {
            "ok": False,
            "attempted": True,
            "error": "downloaded S3 archive sha256 does not match fetched archive",
            "archive_sha256": archive_sha,
            "local_archive_sha256": local_archive_sha,
            "pod_egress_status": pod_egress_status,
        }

    hash_download: dict[str, Any] = {"ok": True, "attempted": False}
    hash_sha = ""
    local_hash_sha = ""
    if hash_uri:
        downloaded_hash = verify_dir / "artifact_hashes.jsonl"
        hash_download = download_verify_uri(hash_uri, downloaded_hash)
        if hash_download.get("ok") is not True:
            return {
                "ok": False,
                "attempted": True,
                "archive_download": archive_download,
                "hash_download": hash_download,
                "pod_egress_status": pod_egress_status,
            }
        hash_sha = sha256_file(downloaded_hash)
        local_hash = output / "runpod-execution" / "artifact_hashes.jsonl"
        local_hash_sha = sha256_file(local_hash) if local_hash.is_file() else ""
        if local_hash_sha and local_hash_sha != hash_sha:
            return {
                "ok": False,
                "attempted": True,
                "error": "downloaded S3 hash ledger sha256 does not match fetched hash ledger",
                "archive_download": archive_download,
                "hash_download": hash_download,
                "hash_sha256": hash_sha,
                "local_hash_sha256": local_hash_sha,
                "pod_egress_status": pod_egress_status,
            }

    verified_status = {
        "ts": timestamp(),
        "mode": "aws_s3_presigned_upload",
        "status": "verified",
        "message": "trusted orchestrator downloaded presigned-uploaded artifacts and verified hashes",
        "verified_by": "cloud_bridge.proxy.verify_presigned_upload_egress",
        "archive_verify_uri": redact_text(archive_uri),
        "archive_sha256": archive_sha,
        "local_archive_sha256": local_archive_sha,
        "hash_verify_uri": redact_text(hash_uri),
        "hash_sha256": hash_sha,
        "local_hash_sha256": local_hash_sha,
        "pod_egress_status": pod_egress_status,
    }
    egress_status_path.parent.mkdir(parents=True, exist_ok=True)
    egress_status_path.write_text(json.dumps(verified_status, indent=2, sort_keys=True) + "\n")
    return {
        "ok": True,
        "attempted": True,
        "archive_download": archive_download,
        "hash_download": hash_download,
        "archive_sha256": archive_sha,
        "hash_sha256": hash_sha,
        "egress_status_path": str(egress_status_path),
    }


def resolve_verify_uri(artifact_egress: dict[str, Any], literal_key: str, ref_key: str, default_env: str) -> str:
    literal = str(artifact_egress.get(literal_key) or "").strip()
    if literal:
        return literal
    ref = str(artifact_egress.get(ref_key) or "").strip()
    env_name = default_env
    if ref.startswith("env:"):
        candidate = ref.split(":", 1)[1].strip()
        if candidate:
            env_name = candidate
    import os

    return os.environ.get(env_name, "").strip()


def download_verify_uri(uri: str, output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if uri.startswith("file://"):
        source = Path(uri[7:])
        if not source.is_file():
            return {"ok": False, "uri": uri, "output_path": str(output_path), "error": "source file not found"}
        shutil.copyfile(source, output_path)
        return {"ok": True, "uri": uri, "output_path": str(output_path), "bytes": output_path.stat().st_size}
    if uri.startswith("s3://"):
        try:
            completed = subprocess.run(
                ["aws", "s3", "cp", uri, str(output_path), "--only-show-errors"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "uri": uri, "output_path": str(output_path), "error": f"{exc.__class__.__name__}: {exc}"}
        if completed.returncode != 0:
            return {
                "ok": False,
                "uri": uri,
                "output_path": str(output_path),
                "returncode": completed.returncode,
                "stderr": completed.stderr[-1000:],
            }
        return {"ok": True, "uri": uri, "output_path": str(output_path), "bytes": output_path.stat().st_size}
    return {"ok": False, "uri": uri, "output_path": str(output_path), "error": "unsupported verify URI scheme"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
