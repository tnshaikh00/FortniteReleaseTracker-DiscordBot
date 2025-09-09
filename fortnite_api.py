"""Utility for querying fortniteapi.io for version information.

This module talks to the public fortniteapi.io REST API and returns
basic information about the current Fortnite version.  The API requires
an API key which should be supplied via the ``FORTNITE_API_KEY``
environment variable.  Only a very small portion of the API is used
here; the client simply fetches the list of game versions and returns
metadata for the currently active one.

The function exposed (``fetch_game_version``) returns a dictionary in
the same shape used throughout the project: ``{"version": str | None,
"published": str | None, "sections": list}`` so that it can easily be
consumed by the notifier.
"""
from __future__ import annotations

import os
import re
from typing import Dict, Optional

import requests

BASE_URL = "https://fortniteapi.io/v1"
API_KEY_ENV = "FORTNITE_API_KEY"

class FortniteAPIError(RuntimeError):
    """Raised when the Fortnite API client encounters an error."""


def _auth_headers() -> Dict[str, str]:
    key = os.getenv(API_KEY_ENV)
    if not key:
        raise FortniteAPIError(
            f"Set the {API_KEY_ENV} environment variable with your fortniteapi.io key"
        )
    return {"Authorization": key}


def fetch_game_version() -> Dict:
    """Return information about the currently active game version.

    The data returned is a minimal dictionary containing ``version`` and
    ``published`` keys.  The function is intentionally tolerant to small
    variations in the JSON payload returned by the API.  If the payload
    cannot be interpreted, an empty dictionary is returned.
    """

    url = f"{BASE_URL}/game/versions"
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=20)
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as exc:  # pragma: no cover - network failure path
        raise FortniteAPIError(str(exc)) from exc

    current = data.get("current") or data.get("data") or {}
    if isinstance(current, list):
        current = current[0] if current else {}

    version = current.get("version") or current.get("build")
    published = current.get("since") or current.get("timestamp")

    return {"version": version, "published": published, "sections": []}


def fetch_fortnite_news() -> Optional[Dict]:
    """Return the latest Fortnite news post via fortniteapi.io.

    The response is normalized to the project's standard structure.  If the
    API returns no posts, ``None`` is returned instead.
    """

    url = "https://fortniteapi.io/v2/news"
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # pragma: no cover - network failure path
        raise FortniteAPIError(str(exc)) from exc

    posts = data.get("news", {}).get("motds", [])
    if not posts:
        return None

    latest = posts[0]
    title = latest.get("title", "")
    body = latest.get("body", "")
    version_match = re.search(r"v\d+\.\d+", f"{title} {body}")

    return {
        "version": version_match.group(0) if version_match else None,
        "published": latest.get("time") or None,
        "sections": [{"header": title, "items": [body]}],
    }


def fetch_fortnite_status() -> Optional[str]:
    """Return the scheduled downtime start time if present."""

    url = "https://fortniteapi.io/v2/status/fortnite"
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # pragma: no cover - network failure path
        raise FortniteAPIError(str(exc)) from exc

    events = data.get("status", {}).get("downtime", [])
    if events:
        return events[0].get("begin")
    return None
