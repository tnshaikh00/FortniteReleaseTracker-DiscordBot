# fortnite_api.py
from __future__ import annotations

import os
import re
from typing import Dict, Optional

import requests

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


def _get_json(url: str, *, params: Dict | None = None, timeout: int = 20) -> Dict:
    try:
        resp = requests.get(url, headers=_auth_headers(), params=params or {}, timeout=timeout)
        resp.raise_for_status()
        return resp.json() or {}
    except Exception as exc:  # pragma: no cover - network failures
        raise FortniteAPIError(str(exc)) from exc


# -------------------------------------------------------------------
# Versions
# -------------------------------------------------------------------
def fetch_game_version() -> Dict:
    """
    Return {"version": str|None, "published": str|None, "sections": []}.
    Tries /v2 first, then /v1 for broader compatibility.
    """
    data: Dict = {}
    tried = []

    for url in (
        "https://fortniteapi.io/v2/game/versions",
        "https://fortniteapi.io/v1/game/versions",
    ):
        tried.append(url)
        try:
            data = _get_json(url)
            break
        except FortniteAPIError:
            continue

    # Known shapes: {"current": {...}} or {"data": [{...}, ...]}
    current = data.get("current") or data.get("data") or {}
    if isinstance(current, list):
        current = current[0] if current else {}

    version = current.get("version") or current.get("build")
    published = current.get("since") or current.get("timestamp")

    return {"version": version, "published": published, "sections": []}


# -------------------------------------------------------------------
# News
# -------------------------------------------------------------------
_VERSION_RX = re.compile(r"\bv?\s?(\d{1,2}[.\-]\d{1,2})\b", re.IGNORECASE)

def fetch_fortnite_news(lang: str = "en") -> Optional[Dict]:
    """
    Return the latest Fortnite news post normalized to:
      {"version": str|None, "published": str|None,
       "sections":[{"header": str, "items":[str, ...]}], "url": str|None}
    Returns None if no posts are available.
    """
    data = _get_json("https://fortniteapi.io/v2/news", params={"lang": lang})

    posts = (
        data.get("news", {}).get("motds", [])
        or data.get("news", {}).get("br", {}).get("motds", [])
        or []
    )
    if not posts:
        return None

    latest = posts[0]
    title = (latest.get("title") or latest.get("tabTitle") or "").strip()
    body = (latest.get("body") or latest.get("description") or "").strip()
    published = latest.get("time") or latest.get("date")
    url = latest.get("url") or latest.get("videoUrl")

    # Extract a version token if present, e.g., v27.10 / 27.10
    m = _VERSION_RX.search(f"{title} {body}")
    token = (m.group(1).replace("-", ".") if m else None)
    version = (f"v{token}" if token and not token.lower().startswith("v") else token)

    sections = [{
        "header": title or "Highlights",
        "items": [s for s in (line.strip() for line in body.splitlines()) if len(s) >= 3] or ([body] if body else []),
    }]

    return {
        "version": version,
        "published": published,
        "sections": sections,
        "url": url,
    }


# -------------------------------------------------------------------
# Status / Downtime
# -------------------------------------------------------------------
def fetch_fortnite_status() -> Optional[str]:
    """
    Return ISO timestamp string for scheduled downtime 'begin' if present, else None.
    """
    data = _get_json("https://fortniteapi.io/v2/status/fortnite")
    events = data.get("status", {}).get("downtime", []) or []
    if events:
        return events[0].get("begin") or events[0].get("start")
    return None

