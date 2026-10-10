"""Saved queries, over the REST API the CLI does not expose. Standard library only.

A saved query is the plugin's definition primitive: named, versioned, and hashed by
the server. Executing one needs a database, sent as a header.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import hotdata
import project

BASE = "https://api.hotdata.dev/v1"


def _call(method: str, path: str, body: dict | None = None, database: str = "") -> dict:
    env = hotdata.credentials()
    key = env.get("HOTDATA_API_KEY", "")
    if not key:
        raise hotdata.HotdataError("no Hotdata API key: run `hotdata auth login`")
    headers = {"Authorization": f"Bearer {key}", "X-Workspace-Id": project.workspace_id(),
               "Content-Type": "application/json"}
    if database:
        headers["X-Database-Id"] = database
    base = env.get("HOTDATA_API_URL", "").rstrip("/") or BASE
    if not base.endswith("/v1"):
        base += "/v1"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            text = response.read().decode()
    except urllib.error.HTTPError as error:
        text = error.read().decode(errors="replace")
        message = text[:200] or f"HTTP {error.code}"
        try:
            found = json.loads(text).get("error")
            # the server sends either {"error": {"message": ...}} or {"error": "code"}
            message = found.get("message", message) if isinstance(found, dict) else str(found or message)
        except (ValueError, AttributeError):
            pass
        raise hotdata.HotdataError(message) from None
    except urllib.error.URLError as error:
        raise hotdata.HotdataError(f"cannot reach Hotdata: {error.reason}") from None
    return json.loads(text) if text.strip() else {}


def list_queries(limit: int = 200) -> list[dict]:
    found, offset = [], 0
    while True:
        page = _call("GET", f"/queries?limit={limit}&offset={offset}")
        found.extend(page.get("queries") or [])
        if not page.get("has_more"):
            return found
        offset += limit


def find(name: str) -> dict | None:
    return next((q for q in list_queries() if q.get("name") == name), None)


def get(query_id: str) -> dict:
    return _call("GET", f"/queries/{query_id}")


def create(name: str, sql: str, description: str = "") -> dict:
    return _call("POST", "/queries", {"name": name, "sql": sql, "description": description})


def update(query_id: str, sql: str, description: str | None = None) -> dict:
    body = {"sql": sql}
    if description is not None:
        body["description"] = description
    return _call("PUT", f"/queries/{query_id}", body)


def versions(query_id: str) -> list[dict]:
    return _call("GET", f"/queries/{query_id}/versions").get("versions") or []


def execute(query_id: str, database: str, version: int | None = None) -> dict:
    body = {"version": version} if version is not None else {}
    return _call("POST", f"/queries/{query_id}/execute", body, database=database)


def delete(query_id: str) -> None:
    _call("DELETE", f"/queries/{query_id}")
