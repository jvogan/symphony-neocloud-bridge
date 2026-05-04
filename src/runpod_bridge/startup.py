from __future__ import annotations

import re
import shlex
from typing import Any


def render_startup_script(manifest: dict[str, Any]) -> str:
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    monitoring = manifest.get("monitoring", {}) if isinstance(manifest.get("monitoring"), dict) else {}
    repo = manifest.get("repo", {}) if isinstance(manifest.get("repo"), dict) else {}
    commands = startup.get("commands", [])
    if not isinstance(commands, list):
        commands = []
    commands = normalize_workload_commands(commands, repo)
    validation_commands = manifest.get("validation_commands", [])
    if not isinstance(validation_commands, list):
        validation_commands = []

    log_file = startup.get("log_file", "runpod-execution/logs/startup.log")
    status_file = startup.get("status_file", "runpod-execution/status.json")
    heartbeat_file = startup.get("heartbeat_file", "runpod-execution/monitor_events.ndjson")
    interval = int(monitoring.get("poll_interval_seconds") or 30)
    expected_artifacts = manifest.get("expected_artifacts", [])
    artifact_paths = [
        artifact.get("path")
        for artifact in expected_artifacts
        if isinstance(artifact, dict) and isinstance(artifact.get("path"), str) and artifact.get("path")
    ]
    artifact_egress = manifest.get("artifact_egress", {}) if isinstance(manifest.get("artifact_egress"), dict) else {}
    archive_path = str(artifact_egress.get("archive_path") or "")
    egress_mode = str(artifact_egress.get("mode") or "workspace_archive")
    archive_enabled = "1" if egress_mode in ("workspace_archive", "network_volume", "runpod_network_volume_s3", "object_store_upload", "aws_s3_presigned_upload", "scp") and archive_path else "0"
    object_store_uri = str(artifact_egress.get("destination_uri") or "")
    if not object_store_uri.startswith("s3://"):
        object_store_uri = ""
    object_store_required = "1" if artifact_egress.get("requires_object_store_upload") is True else "0"
    presigned_upload_required = "1" if artifact_egress.get("requires_presigned_upload") is True else "0"
    presigned_archive_url_env = env_name_from_ref(
        str(artifact_egress.get("archive_upload_url_ref") or artifact_egress.get("upload_url_ref") or ""),
        "RUNPOD_PRESIGNED_ARCHIVE_PUT_URL",
    )
    presigned_hash_ref = str(artifact_egress.get("hash_upload_url_ref") or "")
    presigned_hash_url_env = env_name_from_ref(presigned_hash_ref, "RUNPOD_PRESIGNED_HASH_PUT_URL")
    presigned_hash_declared = "1" if presigned_hash_ref else "0"
    runpod_volume_id = str((manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}).get("networkVolumeId") or "")
    artifact_hash_paths = [path for path in artifact_paths if path != archive_path]

    workload_lines = "\n".join(str(command) for command in commands)
    validation_lines = "\n".join(str(command) for command in validation_commands)
    artifact_case = "\n".join(
        f"  add_hash {shlex.quote(path)}" for path in artifact_hash_paths
    )
    archive_artifact_case = "\n".join(
        f"  archive_add {shlex.quote(path)}" for path in artifact_hash_paths
    )

    log_default = bash_parameter_default(str(log_file))
    status_default = bash_parameter_default(str(status_file))
    heartbeat_default = bash_parameter_default(str(heartbeat_file))
    repo_default = bash_parameter_default(str(repo.get("workdir") or "."))
    repo_source_default = bash_parameter_default(str(repo.get("source") or ""))
    repo_url_default = bash_parameter_default(str(repo.get("url_or_path") or ""))
    repo_ref_default = bash_parameter_default(str(repo.get("commit_or_snapshot") or ""))
    archive_default = bash_parameter_default(archive_path)
    object_store_default = bash_parameter_default(object_store_uri)
    runpod_volume_default = bash_parameter_default(runpod_volume_id)
    presigned_archive_url_env_default = bash_parameter_default(presigned_archive_url_env)
    presigned_hash_url_env_default = bash_parameter_default(presigned_hash_url_env)
    inspection = startup.get("inspection", {}) if isinstance(startup.get("inspection"), dict) else {}
    hold_seconds = int(inspection.get("hold_after_success_seconds") or 0)
    inspection_port = int(inspection.get("http_artifact_server_port") or 0)
    progress = startup.get("progress", {}) if isinstance(startup.get("progress"), dict) else {}
    progress_port = int(progress.get("http_status_server_port") or 0)
    progress_token_env = env_name_from_ref(str(progress.get("auth_token_ref") or ""), "RUNPOD_PROGRESS_TOKEN")
    progress_include_log_tail = "1" if progress.get("include_log_tail") is True else "0"
    progress_log_tail_bytes = int(progress.get("log_tail_bytes") or 4096)
    progress_token_env_default = bash_parameter_default(progress_token_env)
    progress_server_py = progress_server_python()

    return f"""#!/usr/bin/env bash
set -Eeuo pipefail

RUNPOD_EXECUTION_DIR="${{RUNPOD_EXECUTION_DIR:-runpod-execution}}"
RUNPOD_REPO_DIR="${{RUNPOD_REPO_DIR:-{repo_default}}}"
RUNPOD_REPO_SOURCE="${{RUNPOD_REPO_SOURCE:-{repo_source_default}}}"
RUNPOD_REPO_URL="${{RUNPOD_REPO_URL:-{repo_url_default}}}"
RUNPOD_REPO_REF="${{RUNPOD_REPO_REF:-{repo_ref_default}}}"
RUNPOD_ENABLE_REPO_BOOTSTRAP="${{RUNPOD_ENABLE_REPO_BOOTSTRAP:-0}}"
RUNPOD_LOG_FILE="${{RUNPOD_LOG_FILE:-{log_default}}}"
RUNPOD_STATUS_FILE="${{RUNPOD_STATUS_FILE:-{status_default}}}"
RUNPOD_HEARTBEAT_FILE="${{RUNPOD_HEARTBEAT_FILE:-{heartbeat_default}}}"
RUNPOD_HEARTBEAT_INTERVAL_SECONDS="${{RUNPOD_HEARTBEAT_INTERVAL_SECONDS:-{interval}}}"
RUNPOD_HASH_FILE="${{RUNPOD_HASH_FILE:-runpod-execution/artifact_hashes.jsonl}}"
RUNPOD_WORKLOAD_SCRIPT="${{RUNPOD_WORKLOAD_SCRIPT:-runpod-execution/workload.sh}}"
RUNPOD_VALIDATION_SCRIPT="${{RUNPOD_VALIDATION_SCRIPT:-runpod-execution/validation.sh}}"
RUNPOD_ARCHIVE_PATH="${{RUNPOD_ARCHIVE_PATH:-{archive_default}}}"
RUNPOD_CREATE_ARCHIVE="${{RUNPOD_CREATE_ARCHIVE:-{archive_enabled}}}"
RUNPOD_ARCHIVE_ITEMS_FILE="${{RUNPOD_ARCHIVE_ITEMS_FILE:-runpod-execution/archive_items.txt}}"
RUNPOD_EGRESS_MODE="${{RUNPOD_EGRESS_MODE:-{egress_mode}}}"
RUNPOD_OBJECT_STORE_URI="${{RUNPOD_OBJECT_STORE_URI:-{object_store_default}}}"
RUNPOD_OBJECT_STORE_REQUIRED="${{RUNPOD_OBJECT_STORE_REQUIRED:-{object_store_required}}}"
RUNPOD_PRESIGNED_UPLOAD_REQUIRED="${{RUNPOD_PRESIGNED_UPLOAD_REQUIRED:-{presigned_upload_required}}}"
RUNPOD_PRESIGNED_ARCHIVE_URL_ENV="${{RUNPOD_PRESIGNED_ARCHIVE_URL_ENV:-{presigned_archive_url_env_default}}}"
RUNPOD_PRESIGNED_HASH_URL_ENV="${{RUNPOD_PRESIGNED_HASH_URL_ENV:-{presigned_hash_url_env_default}}}"
RUNPOD_PRESIGNED_HASH_UPLOAD_DECLARED="${{RUNPOD_PRESIGNED_HASH_UPLOAD_DECLARED:-{presigned_hash_declared}}}"
RUNPOD_PRESIGNED_ARCHIVE_PUT_URL="${{RUNPOD_PRESIGNED_ARCHIVE_PUT_URL:-}}"
RUNPOD_PRESIGNED_HASH_PUT_URL="${{RUNPOD_PRESIGNED_HASH_PUT_URL:-}}"
RUNPOD_NETWORK_VOLUME_ID="${{RUNPOD_NETWORK_VOLUME_ID:-{runpod_volume_default}}}"
RUNPOD_EGRESS_STATUS_FILE="${{RUNPOD_EGRESS_STATUS_FILE:-runpod-execution/egress_status.json}}"
RUNPOD_HOLD_AFTER_SUCCESS_SECONDS="${{RUNPOD_HOLD_AFTER_SUCCESS_SECONDS:-{hold_seconds}}}"
RUNPOD_HTTP_ARTIFACT_SERVER_PORT="${{RUNPOD_HTTP_ARTIFACT_SERVER_PORT:-{inspection_port}}}"
RUNPOD_PROGRESS_SERVER_PORT="${{RUNPOD_PROGRESS_SERVER_PORT:-{progress_port}}}"
RUNPOD_PROGRESS_TOKEN_ENV="${{RUNPOD_PROGRESS_TOKEN_ENV:-{progress_token_env_default}}}"
RUNPOD_PROGRESS_INCLUDE_LOG_TAIL="${{RUNPOD_PROGRESS_INCLUDE_LOG_TAIL:-{progress_include_log_tail}}}"
RUNPOD_PROGRESS_LOG_TAIL_BYTES="${{RUNPOD_PROGRESS_LOG_TAIL_BYTES:-{progress_log_tail_bytes}}}"
RUNPOD_BOOTSTRAP_TIMEOUT_SECONDS="${{RUNPOD_BOOTSTRAP_TIMEOUT_SECONDS:-600}}"

bootstrap_timestamp() {{
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}}

bootstrap_prepare_files() {{
  mkdir -p "$(dirname "$RUNPOD_LOG_FILE")" "$(dirname "$RUNPOD_STATUS_FILE")" "$(dirname "$RUNPOD_HEARTBEAT_FILE")" "$(dirname "$RUNPOD_HASH_FILE")"
}}

bootstrap_log() {{
  bootstrap_prepare_files
  printf '[%s] bootstrap: %s\\n' "$(bootstrap_timestamp)" "$1" >> "$RUNPOD_LOG_FILE"
}}

bootstrap_event() {{
  bootstrap_prepare_files
  printf '{{"ts":"%s","phase":"bootstrap","status":"%s"}}\\n' "$(bootstrap_timestamp)" "$1" >> "$RUNPOD_HEARTBEAT_FILE"
}}

bootstrap_fail() {{
  exit_code="$1"
  message="$2"
  bootstrap_log "$message"
  bootstrap_event "failed"
  printf '{{"ts":"%s","status":"failed","exit_code":%s,"phase":"bootstrap","message":"%s","log_file":"%s","hash_file":"%s"}}\\n' "$(bootstrap_timestamp)" "$exit_code" "$message" "$RUNPOD_LOG_FILE" "$RUNPOD_HASH_FILE" > "$RUNPOD_STATUS_FILE"
  exit "$exit_code"
}}

run_bootstrap_cmd() {{
  if command -v timeout >/dev/null 2>&1; then
    timeout "$RUNPOD_BOOTSTRAP_TIMEOUT_SECONDS" "$@"
  else
    "$@"
  fi
}}

bootstrap_repo() {{
  if [ "$RUNPOD_ENABLE_REPO_BOOTSTRAP" != "1" ]; then
    mkdir -p "$RUNPOD_REPO_DIR"
    cd "$RUNPOD_REPO_DIR"
    return
  fi
  if [ "$RUNPOD_REPO_SOURCE" != "git_remote_or_snapshot" ]; then
    bootstrap_fail 90 "RUNPOD_ENABLE_REPO_BOOTSTRAP=1 requires repo.source=git_remote_or_snapshot"
  fi
  if [ -z "$RUNPOD_REPO_URL" ] || [ -z "$RUNPOD_REPO_REF" ]; then
    bootstrap_fail 91 "RUNPOD_REPO_URL and RUNPOD_REPO_REF are required for remote bootstrap"
  fi
  command -v git >/dev/null 2>&1 || bootstrap_fail 92 "git is required for remote repo bootstrap"
  bootstrap_event "begin"
  bootstrap_log "cloning $RUNPOD_REPO_URL at $RUNPOD_REPO_REF"
  rm -rf "$RUNPOD_REPO_DIR"
  mkdir -p "$(dirname "$RUNPOD_REPO_DIR")"
  run_bootstrap_cmd git clone "$RUNPOD_REPO_URL" "$RUNPOD_REPO_DIR" >> "$RUNPOD_LOG_FILE" 2>&1 || bootstrap_fail 93 "git clone failed or timed out"
  cd "$RUNPOD_REPO_DIR"
  run_bootstrap_cmd git checkout "$RUNPOD_REPO_REF" >> "$RUNPOD_LOG_FILE" 2>&1 || bootstrap_fail 94 "git checkout failed or timed out"
  bootstrap_event "succeeded"
}}

bootstrap_repo
mkdir -p "$(dirname "$RUNPOD_LOG_FILE")" "$(dirname "$RUNPOD_STATUS_FILE")" "$(dirname "$RUNPOD_HEARTBEAT_FILE")" "$(dirname "$RUNPOD_HASH_FILE")"
if [ -d .git ]; then
  git rev-parse HEAD > "$RUNPOD_EXECUTION_DIR/repo_commit.txt" 2>/dev/null || true
fi

timestamp() {{
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}}

event() {{
  phase="$1"
  status="$2"
  printf '{{"ts":"%s","phase":"%s","status":"%s"}}\\n' "$(timestamp)" "$phase" "$status" >> "$RUNPOD_HEARTBEAT_FILE"
}}

write_status() {{
  status="$1"
  exit_code="$2"
  printf '{{"ts":"%s","status":"%s","exit_code":%s,"log_file":"%s","hash_file":"%s"}}\\n' "$(timestamp)" "$status" "$exit_code" "$RUNPOD_LOG_FILE" "$RUNPOD_HASH_FILE" > "$RUNPOD_STATUS_FILE"
}}

write_egress_status() {{
  status="$1"
  message="$2"
  destination="$RUNPOD_OBJECT_STORE_URI"
  if [ "$RUNPOD_EGRESS_MODE" = "aws_s3_presigned_upload" ]; then
    destination="aws-s3-presigned-upload"
  fi
  if [ -z "$destination" ] && [ -n "$RUNPOD_NETWORK_VOLUME_ID" ]; then
    destination="runpod-network-volume:$RUNPOD_NETWORK_VOLUME_ID"
  fi
  printf '{{"ts":"%s","mode":"%s","status":"%s","message":"%s","destination":"%s"}}\\n' "$(timestamp)" "$RUNPOD_EGRESS_MODE" "$status" "$message" "$destination" > "$RUNPOD_EGRESS_STATUS_FILE"
}}

resolve_presigned_upload_env() {{
  if [ -z "$RUNPOD_PRESIGNED_ARCHIVE_PUT_URL" ] && [ -n "$RUNPOD_PRESIGNED_ARCHIVE_URL_ENV" ]; then
    RUNPOD_PRESIGNED_ARCHIVE_PUT_URL="${{!RUNPOD_PRESIGNED_ARCHIVE_URL_ENV:-}}"
  fi
  if [ -z "$RUNPOD_PRESIGNED_HASH_PUT_URL" ] && [ -n "$RUNPOD_PRESIGNED_HASH_URL_ENV" ]; then
    RUNPOD_PRESIGNED_HASH_PUT_URL="${{!RUNPOD_PRESIGNED_HASH_URL_ENV:-}}"
  fi
}}

hash_file() {{
  file_path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file_path" | awk '{{print $1}}'
  else
    shasum -a 256 "$file_path" | awk '{{print $1}}'
  fi
}}

add_hash() {{
  file_path="$1"
  if [ -f "$file_path" ]; then
    digest="$(hash_file "$file_path")"
    printf '{{"path":"%s","sha256":"%s","present":true}}\\n' "$file_path" "$digest" >> "$RUNPOD_HASH_FILE"
  else
    printf '{{"path":"%s","sha256":null,"present":false}}\\n' "$file_path" >> "$RUNPOD_HASH_FILE"
  fi
}}

archive_add() {{
  file_path="$1"
  if [ -e "$file_path" ] && [ "$file_path" != "$RUNPOD_ARCHIVE_PATH" ]; then
    printf '%s\\n' "$file_path" >> "$RUNPOD_ARCHIVE_ITEMS_FILE"
  fi
}}

create_archive() {{
  if [ "$RUNPOD_CREATE_ARCHIVE" != "1" ] || [ -z "$RUNPOD_ARCHIVE_PATH" ]; then
    return 0
  fi
  mkdir -p "$(dirname "$RUNPOD_ARCHIVE_PATH")" "$(dirname "$RUNPOD_ARCHIVE_ITEMS_FILE")"
  : > "$RUNPOD_ARCHIVE_ITEMS_FILE"
  archive_add "$RUNPOD_LOG_FILE"
  archive_add "$RUNPOD_STATUS_FILE"
  archive_add "$RUNPOD_HEARTBEAT_FILE"
  archive_add "$RUNPOD_HASH_FILE"
{archive_artifact_case}
  if [ -s "$RUNPOD_ARCHIVE_ITEMS_FILE" ]; then
    tar -czf "$RUNPOD_ARCHIVE_PATH" -T "$RUNPOD_ARCHIVE_ITEMS_FILE"
  fi
}}

durable_egress() {{
  mkdir -p "$(dirname "$RUNPOD_EGRESS_STATUS_FILE")"
  if [ "$RUNPOD_EGRESS_MODE" = "network_volume" ]; then
    write_egress_status "retained" "artifacts written to attached network volume"
    return 0
  fi
  if [ "$RUNPOD_EGRESS_MODE" = "runpod_network_volume_s3" ]; then
    write_egress_status "retained" "artifacts written to attached network volume for post-cleanup S3 pull"
    return 0
  fi
  if [ "$RUNPOD_EGRESS_MODE" = "aws_s3_presigned_upload" ]; then
    resolve_presigned_upload_env
    if [ -z "$RUNPOD_PRESIGNED_ARCHIVE_PUT_URL" ]; then
      write_egress_status "blocked" "RUNPOD_PRESIGNED_ARCHIVE_PUT_URL is required"
      [ "$RUNPOD_PRESIGNED_UPLOAD_REQUIRED" = "1" ] && return 75
      return 0
    fi
    if [ -z "$RUNPOD_ARCHIVE_PATH" ] || [ ! -f "$RUNPOD_ARCHIVE_PATH" ]; then
      write_egress_status "blocked" "archive file is required before presigned upload"
      [ "$RUNPOD_PRESIGNED_UPLOAD_REQUIRED" = "1" ] && return 76
      return 0
    fi
    if ! command -v curl >/dev/null 2>&1; then
      write_egress_status "blocked" "curl is required for aws_s3_presigned_upload"
      [ "$RUNPOD_PRESIGNED_UPLOAD_REQUIRED" = "1" ] && return 77
      return 0
    fi
    curl --fail --silent --show-error --upload-file "$RUNPOD_ARCHIVE_PATH" "$RUNPOD_PRESIGNED_ARCHIVE_PUT_URL" >> "$RUNPOD_LOG_FILE" 2>&1
    if [ "$RUNPOD_PRESIGNED_HASH_UPLOAD_DECLARED" = "1" ]; then
      if [ -z "$RUNPOD_PRESIGNED_HASH_PUT_URL" ]; then
        write_egress_status "blocked" "RUNPOD_PRESIGNED_HASH_PUT_URL is required"
        [ "$RUNPOD_PRESIGNED_UPLOAD_REQUIRED" = "1" ] && return 78
        return 0
      fi
      if [ -f "$RUNPOD_HASH_FILE" ]; then
        curl --fail --silent --show-error --upload-file "$RUNPOD_HASH_FILE" "$RUNPOD_PRESIGNED_HASH_PUT_URL" >> "$RUNPOD_LOG_FILE" 2>&1
      fi
    fi
    write_egress_status "uploaded" "archive uploaded through presigned S3 URL"
    return 0
  fi
  if [ "$RUNPOD_EGRESS_MODE" != "object_store_upload" ]; then
    write_egress_status "not_required" "mode does not require object store upload"
    return 0
  fi
  if [ -z "$RUNPOD_OBJECT_STORE_URI" ]; then
    write_egress_status "blocked" "RUNPOD_OBJECT_STORE_URI is required"
    [ "$RUNPOD_OBJECT_STORE_REQUIRED" = "1" ] && return 73
    return 0
  fi
  if ! command -v aws >/dev/null 2>&1; then
    write_egress_status "blocked" "aws CLI is required for generic object_store_upload"
    [ "$RUNPOD_OBJECT_STORE_REQUIRED" = "1" ] && return 74
    return 0
  fi
  if [ -n "$RUNPOD_ARCHIVE_PATH" ] && [ -f "$RUNPOD_ARCHIVE_PATH" ]; then
    aws s3 cp "$RUNPOD_ARCHIVE_PATH" "${{RUNPOD_OBJECT_STORE_URI%/}}/" >> "$RUNPOD_LOG_FILE" 2>&1
  fi
  if [ -f "$RUNPOD_HASH_FILE" ]; then
    aws s3 cp "$RUNPOD_HASH_FILE" "${{RUNPOD_OBJECT_STORE_URI%/}}/" >> "$RUNPOD_LOG_FILE" 2>&1
  fi
  write_egress_status "uploaded" "archive and hash file uploaded when present"
  return 0
}}

heartbeat_loop() {{
  trap 'exit 0' TERM INT
  while true; do
    event "heartbeat" "running"
    sleep "$RUNPOD_HEARTBEAT_INTERVAL_SECONDS" &
    wait "$!" || exit 0
  done
}}

progress_server() {{
  RUNPOD_PROGRESS_PID=""
  if [ "$RUNPOD_PROGRESS_SERVER_PORT" -le 0 ]; then
    return 0
  fi
  python3 - <<'RUNPOD_BRIDGE_PROGRESS_SERVER' >> "$RUNPOD_LOG_FILE" 2>&1 &
{progress_server_py}
RUNPOD_BRIDGE_PROGRESS_SERVER
  RUNPOD_PROGRESS_PID="$!"
  event "progress" "http-serving"
}}

stop_progress_server() {{
  if [ -n "${{RUNPOD_PROGRESS_PID:-}}" ]; then
    kill "$RUNPOD_PROGRESS_PID" >/dev/null 2>&1 || true
    wait "$RUNPOD_PROGRESS_PID" >/dev/null 2>&1 || true
    RUNPOD_PROGRESS_PID=""
    event "progress" "stopped"
  fi
}}

inspection_hold() {{
  if [ "$RUNPOD_EXIT_CODE" -ne 0 ] || [ "$RUNPOD_HOLD_AFTER_SUCCESS_SECONDS" -le 0 ]; then
    return 0
  fi
  event "inspection" "begin"
  RUNPOD_HTTP_PID=""
  if [ "$RUNPOD_HTTP_ARTIFACT_SERVER_PORT" -gt 0 ]; then
    python3 -m http.server "$RUNPOD_HTTP_ARTIFACT_SERVER_PORT" --bind 0.0.0.0 >> "$RUNPOD_LOG_FILE" 2>&1 &
    RUNPOD_HTTP_PID="$!"
    event "inspection" "http-serving"
  fi
  sleep "$RUNPOD_HOLD_AFTER_SUCCESS_SECONDS"
  if [ -n "$RUNPOD_HTTP_PID" ]; then
    kill "$RUNPOD_HTTP_PID" >/dev/null 2>&1 || true
    wait "$RUNPOD_HTTP_PID" >/dev/null 2>&1 || true
  fi
  event "inspection" "ended"
}}

cat > "$RUNPOD_WORKLOAD_SCRIPT" <<'RUNPOD_BRIDGE_WORKLOAD'
{workload_lines}
RUNPOD_BRIDGE_WORKLOAD
chmod +x "$RUNPOD_WORKLOAD_SCRIPT"

cat > "$RUNPOD_VALIDATION_SCRIPT" <<'RUNPOD_BRIDGE_VALIDATION'
{validation_lines}
RUNPOD_BRIDGE_VALIDATION
chmod +x "$RUNPOD_VALIDATION_SCRIPT"

event "startup" "begin"
write_status "running" 0
heartbeat_loop &
RUNPOD_HEARTBEAT_PID="$!"
RUNPOD_PROGRESS_PID=""
progress_server

set +e
bash "$RUNPOD_WORKLOAD_SCRIPT" 2>&1 | tee -a "$RUNPOD_LOG_FILE"
RUNPOD_EXIT_CODE="${{PIPESTATUS[0]}}"
set -e

if [ "$RUNPOD_EXIT_CODE" -eq 0 ] && [ -s "$RUNPOD_VALIDATION_SCRIPT" ]; then
  event "validation" "begin"
  set +e
  bash "$RUNPOD_VALIDATION_SCRIPT" 2>&1 | tee -a "$RUNPOD_LOG_FILE"
  RUNPOD_VALIDATION_EXIT_CODE="${{PIPESTATUS[0]}}"
  set -e
  if [ "$RUNPOD_VALIDATION_EXIT_CODE" -ne 0 ]; then
    RUNPOD_EXIT_CODE="$RUNPOD_VALIDATION_EXIT_CODE"
    event "validation" "failed"
  else
    event "validation" "succeeded"
  fi
fi

kill "$RUNPOD_HEARTBEAT_PID" >/dev/null 2>&1 || true
wait "$RUNPOD_HEARTBEAT_PID" >/dev/null 2>&1 || true

: > "$RUNPOD_HASH_FILE"
{artifact_case}

if [ "$RUNPOD_EXIT_CODE" -eq 0 ]; then
  event "startup" "succeeded"
  write_status "succeeded" "$RUNPOD_EXIT_CODE"
else
  event "startup" "failed"
  write_status "failed" "$RUNPOD_EXIT_CODE"
fi

create_archive
if [ -n "$RUNPOD_ARCHIVE_PATH" ] && [ -f "$RUNPOD_ARCHIVE_PATH" ]; then
  add_hash "$RUNPOD_ARCHIVE_PATH"
fi
RUNPOD_EGRESS_EXIT_CODE=0
durable_egress || RUNPOD_EGRESS_EXIT_CODE="$?"
if [ "$RUNPOD_EXIT_CODE" -eq 0 ] && [ "$RUNPOD_EGRESS_EXIT_CODE" -ne 0 ]; then
  RUNPOD_EXIT_CODE="$RUNPOD_EGRESS_EXIT_CODE"
  event "egress" "failed"
  write_status "failed" "$RUNPOD_EXIT_CODE"
elif [ "$RUNPOD_EXIT_CODE" -eq 0 ]; then
  event "egress" "completed"
  write_status "succeeded" "$RUNPOD_EXIT_CODE"
fi

stop_progress_server
inspection_hold

exit "$RUNPOD_EXIT_CODE"
"""


def bash_parameter_default(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")


def env_name_from_ref(ref: str, default: str) -> str:
    if ref.startswith("env:"):
        value = ref.split(":", 1)[1].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            return value
    return default


def progress_server_python() -> str:
    return r'''from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PORT = int(os.environ.get("RUNPOD_PROGRESS_SERVER_PORT", "0") or 0)
STATUS_PATH = Path(os.environ.get("RUNPOD_STATUS_FILE", "runpod-execution/status.json"))
HEARTBEAT_PATH = Path(os.environ.get("RUNPOD_HEARTBEAT_FILE", "runpod-execution/monitor_events.ndjson"))
LOG_PATH = Path(os.environ.get("RUNPOD_LOG_FILE", "runpod-execution/logs/startup.log"))
TOKEN_ENV = os.environ.get("RUNPOD_PROGRESS_TOKEN_ENV", "RUNPOD_PROGRESS_TOKEN")
TOKEN = os.environ.get(TOKEN_ENV, "")
INCLUDE_LOG_TAIL = os.environ.get("RUNPOD_PROGRESS_INCLUDE_LOG_TAIL", "0") == "1"
LOG_TAIL_BYTES = int(os.environ.get("RUNPOD_PROGRESS_LOG_TAIL_BYTES", "4096") or 4096)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_last_json_line(path: Path) -> dict:
    try:
        lines = [line for line in path.read_text().splitlines() if line.strip()]
    except FileNotFoundError:
        return {}
    if not lines:
        return {}
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"raw": lines[-1]}
    return data if isinstance(data, dict) else {"raw": data}


def tail_text(path: Path, max_bytes: int) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes), os.SEEK_SET)
            return handle.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def health_payload() -> dict:
    status = read_json(STATUS_PATH)
    heartbeat = read_last_json_line(HEARTBEAT_PATH)
    log_bytes = LOG_PATH.stat().st_size if LOG_PATH.is_file() else 0
    payload = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "last_heartbeat": heartbeat,
        "files": {
            "status_present": STATUS_PATH.is_file(),
            "heartbeat_present": HEARTBEAT_PATH.is_file(),
            "log_present": LOG_PATH.is_file(),
            "log_bytes": log_bytes,
        },
        "productive_hint": bool(status.get("status") == "running" and heartbeat),
    }
    return payload


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def authorized(self) -> bool:
        if not TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        token = self.headers.get("X-Runpod-Progress-Token", "")
        return auth == f"Bearer {TOKEN}" or token == TOKEN

    def write_json(self, status_code: int, payload: dict):
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_text(self, status_code: int, text: str):
        data = text.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.authorized():
            self.write_json(401, {"ok": False, "error": "unauthorized"})
            return
        if self.path in ("/", "/healthz", "/progress.json"):
            self.write_json(200, health_payload())
            return
        if self.path == "/tail":
            if not INCLUDE_LOG_TAIL:
                self.write_json(404, {"ok": False, "error": "log tail disabled"})
                return
            self.write_text(200, tail_text(LOG_PATH, LOG_TAIL_BYTES))
            return
        self.write_json(404, {"ok": False, "error": "not found"})


if PORT > 0:
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
'''


def normalize_workload_commands(commands: list[Any], repo: dict[str, Any]) -> list[str]:
    workdir = repo.get("workdir")
    normalized: list[str] = []
    for command in commands:
        text = str(command)
        if isinstance(workdir, str) and is_exact_cd(text, workdir):
            default = bash_parameter_default(workdir)
            normalized.append(f'cd "${{RUNPOD_REPO_DIR:-{default}}}"')
        else:
            normalized.append(text)
    return normalized


def is_exact_cd(command: str, workdir: str) -> bool:
    try:
        return shlex.split(command.strip()) == ["cd", workdir]
    except ValueError:
        return False
