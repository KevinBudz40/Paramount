"""
Thin authenticated HTTP client for Microsoft Graph API v1.0.

All calls use the delegated token from rag.graph.auth.get_token().
Includes retry-with-backoff for HTTP 429 (throttling), which Graph
enforces aggressively on SharePoint endpoints.
"""
import time
from typing import Any

import requests

from rag.graph.auth import get_token

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_MAX_RETRIES = 5


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_token()}",
        "Accept": "application/json",
    }


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """Execute an HTTP request, retrying on 429 with Retry-After backoff."""
    for attempt in range(_MAX_RETRIES):
        r = requests.request(method, url, **kwargs)
        if r.status_code == 429:
            # Graph returns Retry-After in seconds; honour it exactly.
            wait = int(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(wait)
            continue
        if r.status_code == 503:
            time.sleep(2 ** attempt)
            continue
        return r
    r.raise_for_status()
    return r


def get(path: str, params: dict | None = None) -> dict:
    url = path if path.startswith("https://") else f"{GRAPH_BASE}{path}"
    r = _request_with_retry("GET", url, headers=_headers(), params=params or {})
    r.raise_for_status()
    return r.json()


def get_bytes(path: str) -> bytes:
    """Download binary content (file downloads, attachment bytes)."""
    url = path if path.startswith("https://") else f"{GRAPH_BASE}{path}"
    r = _request_with_retry("GET", url, headers=_headers())
    r.raise_for_status()
    return r.content


def get_paginated(path: str, params: dict | None = None) -> list[dict]:
    """
    Collect all pages of a Graph OData collection, following @odata.nextLink.
    Returns the combined list of `value` items.
    """
    results: list[dict] = []
    url: str | None = path if path.startswith("https://") else f"{GRAPH_BASE}{path}"
    first = True

    while url:
        r = _request_with_retry(
            "GET", url,
            headers=_headers(),
            params=(params or {}) if first else {},
        )
        r.raise_for_status()
        data: dict = r.json()
        results.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        first = False

    return results
