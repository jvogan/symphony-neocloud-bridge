from __future__ import annotations

from pathlib import Path
import json
import time
from typing import Any
from http.client import RemoteDisconnected
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .closeout import write_closeout_files


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
) -> dict[str, Any]:
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    status_path = str(startup.get("status_file") or "runpod-execution/status.json")

    deadline = time.monotonic() + timeout_seconds
    status: dict[str, Any] = {}
    status_fetch: dict[str, Any] = {}
    while time.monotonic() <= deadline:
        status_fetch = fetch_proxy_file(pod_id, port, status_path, output / status_path, timeout_seconds=2)
        if status_fetch["ok"]:
            status = read_json(output / status_path)
            if status.get("status") in ("succeeded", "success", "completed", "failed"):
                break
        time.sleep(interval_seconds)

    paths = required_proxy_paths(manifest)
    fetches = []
    for remote_path in paths:
        fetches.append(fetch_proxy_file(pod_id, port, remote_path, output / remote_path, timeout_seconds=10))

    closeout = write_closeout_files(manifest, output)
    result = {
        "ok": closeout["status"] == "succeeded" and status.get("status") in ("succeeded", "success", "completed"),
        "pod_id": pod_id,
        "port": port,
        "base_url": f"https://{pod_id}-{port}.proxy.runpod.net",
        "status": status,
        "status_fetch": status_fetch,
        "fetches": fetches,
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
        status_fetch = fetch_tcp_file(host, external_port, status_path, output / status_path, timeout_seconds=2)
        if status_fetch["ok"]:
            status = read_json(output / status_path)
            if status.get("status") in ("succeeded", "success", "completed", "failed"):
                break
        time.sleep(interval_seconds)

    paths = required_proxy_paths(manifest)
    fetches = []
    for remote_path in paths:
        fetches.append(fetch_tcp_file(host, external_port, remote_path, output / remote_path, timeout_seconds=10))

    closeout = write_closeout_files(manifest, output)
    result = {
        "ok": closeout["status"] == "succeeded" and status.get("status") in ("succeeded", "success", "completed"),
        "pod_id": pod.get("id"),
        "port": internal_port,
        "external_port": external_port,
        "base_url": f"http://{host}:{external_port}",
        "status": status,
        "status_fetch": status_fetch,
        "fetches": fetches,
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
    ]
    for artifact in manifest.get("expected_artifacts", []):
        if isinstance(artifact, dict) and artifact.get("path"):
            paths.append(str(artifact["path"]))
    if artifact_egress.get("mode") == "workspace_archive" and artifact_egress.get("archive_path"):
        paths.append(str(artifact_egress["archive_path"]))
    return dedupe(paths)


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
