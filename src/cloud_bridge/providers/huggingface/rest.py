"""Hugging Face Jobs REST client - a stdlib-only urllib wrapper.

HF Jobs is a one-shot container-job product (https://huggingface.co/api/jobs): submit a
Docker image plus a command, poll until the job reaches a terminal stage, fetch logs, and
cancel to stop early. Unlike a rented pod there is no persistent server to attach to and no
delete endpoint - a job auto-terminates (and stops billing) on command exit, error, timeout,
or cancel. The huggingface_hub Python SDK is a thin wrapper over these same endpoints; this
client reimplements only the calls the bridge needs so the runtime stays standard-library only.

Auth mirrors the RunPod client: the token comes from the environment (HF_TOKEN), which a
machine-side Keychain wrapper is expected to populate. Nothing is stored in the repo.
See docs/providers/huggingface.md.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://huggingface.co"
# A job is still doing work unless it has reached one of these. HF also emits a real
# "UPDATING" stage that the documented enum omits, so terminality is an allow-list, not a
# deny-list: anything outside this set means "keep polling".
TERMINAL_STAGES: tuple[str, ...] = ("COMPLETED", "CANCELED", "ERROR", "DELETED")
SUCCESS_STAGE = "COMPLETED"
# resolve/ URL prefixes by repo type. Datasets and models are documented and stable; buckets
# are a newer storage primitive whose read-back path is confirmed at first smoke (see docs).
_REPO_URL_PREFIX = {"dataset": "datasets/", "model": "", "bucket": "buckets/"}


class HfJobsError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def is_terminal_stage(stage: str) -> bool:
    return str(stage or "").upper() in TERMINAL_STAGES


def job_stage(job: dict[str, Any]) -> str:
    status = job.get("status") if isinstance(job, dict) else None
    if isinstance(status, dict):
        return str(status.get("stage") or "")
    return ""


def resolve_url(base_url: str, repo_type: str, repo_id: str, path: str, *, revision: str = "main") -> str:
    """Build a Hub resolve/ download URL for an artifact a finished job pushed to a repo."""
    prefix = _REPO_URL_PREFIX.get(repo_type, "datasets/")
    safe_path = quote(path.lstrip("/"))
    return f"{base_url.rstrip('/')}/{prefix}{repo_id}/resolve/{quote(revision, safe='')}/{safe_path}"


class HfJobsClient:
    def __init__(
        self,
        token: str | None = None,
        namespace: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 60,
    ):
        self.token = token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN", "")
        self.namespace = namespace or os.environ.get("HF_JOBS_NAMESPACE", "")
        self.base_url = (base_url or os.environ.get("HF_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def require_token(self) -> None:
        if not self.token:
            raise HfJobsError("HF_TOKEN is required for Hugging Face Jobs API calls")

    def require_namespace(self) -> str:
        if not self.namespace:
            raise HfJobsError(
                "a Hugging Face namespace (username or org) is required; set huggingface.namespace "
                "in the manifest or the HF_JOBS_NAMESPACE environment variable"
            )
        return self.namespace

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
            raise HfJobsError(
                f"HF Jobs API {method} {path} failed with HTTP {exc.code}: {detail}", status_code=exc.code
            ) from exc
        except URLError as exc:
            raise HfJobsError(f"HF Jobs API {method} {path} failed: {exc.reason}") from exc

    # --- job lifecycle ---------------------------------------------------

    def submit_job(self, spec: dict[str, Any]) -> dict[str, Any]:
        namespace = self.require_namespace()
        result = self.request("POST", f"/api/jobs/{namespace}", body=spec)
        return result if isinstance(result, dict) else {}

    def get_job(self, job_id: str) -> dict[str, Any]:
        namespace = self.require_namespace()
        result = self.request("GET", f"/api/jobs/{namespace}/{job_id}")
        return result if isinstance(result, dict) else {}

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        namespace = self.require_namespace()
        result = self.request("POST", f"/api/jobs/{namespace}/{job_id}/cancel")
        return result if isinstance(result, dict) else {}

    def list_jobs(self) -> list[dict[str, Any]]:
        namespace = self.require_namespace()
        result = self.request("GET", f"/api/jobs/{namespace}")
        return [job for job in result if isinstance(job, dict)] if isinstance(result, list) else []

    def hardware(self) -> list[dict[str, Any]]:
        """Authoritative per-flavor pricing from the live catalog (GET /api/jobs/hardware)."""
        result = self.request("GET", "/api/jobs/hardware")
        return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []

    # --- evidence + egress -----------------------------------------------

    def fetch_logs(self, job_id: str, *, tail: int | None = None) -> list[str]:
        """Best-effort log capture. The /logs route is an SSE stream of `data: {json}` lines;
        a finished job's stream may 500 or close early, so failures yield an empty list rather
        than aborting closeout."""
        self.require_token()
        namespace = self.require_namespace()
        url = f"{self.base_url}/api/jobs/{namespace}/{job_id}/logs"
        if tail:
            url = f"{url}?{urlencode({'tail': tail})}"
        request = Request(url, headers={"Authorization": f"Bearer {self.token}"}, method="GET")
        lines: list[str] = []
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                for raw in response:
                    text = raw.decode("utf-8", errors="replace").strip()
                    if not text.startswith("data:"):
                        continue
                    payload = text[len("data:"):].strip()
                    if not payload.startswith("{"):
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict) and "data" in event:
                        lines.append(str(event["data"]))
        except (HTTPError, URLError):
            return lines
        return lines

    def download(self, url: str) -> bytes:
        """Download artifact bytes from a Hub resolve/ URL. Sends the token so private repos work."""
        self.require_token()
        request = Request(url, headers={"Authorization": f"Bearer {self.token}"}, method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise HfJobsError(f"HF artifact download failed with HTTP {exc.code}: {detail}", status_code=exc.code) from exc
        except URLError as exc:
            raise HfJobsError(f"HF artifact download failed: {exc.reason}") from exc
