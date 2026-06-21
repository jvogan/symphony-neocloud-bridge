from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


LINEAR_API_URL = "https://api.linear.app/graphql"
ISSUE_RE = re.compile(r"/issue/([A-Z][A-Z0-9]+-\d+)(?:/|$)", re.I)


class LinearApiError(RuntimeError):
    pass


class LinearClient:
    def __init__(self, token: str | None = None, api_url: str = LINEAR_API_URL, timeout_seconds: int = 30):
        self.token = token or os.environ.get("LINEAR_API_KEY", "")
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds

    def require_token(self) -> None:
        if not self.token:
            raise LinearApiError("LINEAR_API_KEY is required for Linear API calls")

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.require_token()
        payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        request = Request(
            self.api_url,
            data=payload,
            headers={
                "Authorization": self.token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LinearApiError(f"Linear API failed with HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise LinearApiError(f"Linear API failed: {exc.reason}") from exc
        if data.get("errors"):
            raise LinearApiError(f"Linear API returned errors: {data['errors']}")
        return data.get("data", {}) if isinstance(data, dict) else {}

    def get_issue(self, issue: str) -> dict[str, Any]:
        issue_id = issue_identifier(issue)
        query = """
        query BridgeIssue($id: String!) {
          issue(id: $id) {
            id
            identifier
            title
            description
            url
            state { name }
            project { name }
            team { key name }
          }
        }
        """
        data = self.graphql(query, {"id": issue_id})
        issue_data = data.get("issue") if isinstance(data, dict) else None
        if not isinstance(issue_data, dict):
            raise LinearApiError(f"Linear issue not found: {issue_id}")
        return issue_data

    def create_comment(self, issue: str, body: str) -> dict[str, Any]:
        issue_id = issue_identifier(issue)
        mutation = """
        mutation BridgeComment($input: CommentCreateInput!) {
          commentCreate(input: $input) {
            success
            comment { id url body }
          }
        }
        """
        data = self.graphql(mutation, {"input": {"issueId": issue_id, "body": body}})
        result = data.get("commentCreate") if isinstance(data, dict) else None
        return result if isinstance(result, dict) else {}


def issue_identifier(value: str) -> str:
    match = ISSUE_RE.search(value)
    if match:
        return match.group(1).upper()
    return value.strip()


def issue_to_markdown(issue: dict[str, Any]) -> str:
    title = issue.get("title") or issue.get("identifier") or "Linear issue"
    description = issue.get("description") or ""
    state = issue.get("state", {}).get("name", "") if isinstance(issue.get("state"), dict) else ""
    return f"""# {title}

Linear: {issue.get("identifier", "")}
URL: {issue.get("url", "")}
State: {state}

{description}
"""


def write_issue_markdown(issue: dict[str, Any], out_path: str | Path) -> dict[str, Any]:
    output = Path(out_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(issue_to_markdown(issue))
    return {"path": str(output), "identifier": issue.get("identifier"), "url": issue.get("url")}
