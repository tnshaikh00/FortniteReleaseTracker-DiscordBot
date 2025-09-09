import os, json
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from zoneinfo import ZoneInfo

import requests

from fortnite_scraper import select_top_sections
from fortnite_api import fetch_game_version, fetch_fortnite_news, fetch_fortnite_status

STATE_PATH = os.getenv("STATE_PATH", "state/fortnite_state.json")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
FORCE_SEND = os.getenv("FORCE_SEND", "").lower() == "true"


def post_webhook(payload: Dict, *, timeout: int = 20):
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=timeout)
    r.raise_for_status()


def get_latest_news_article() -> Tuple[Optional[str], Optional[Dict]]:
    try:
        news = fetch_fortnite_news()
        if news:
            return news.get("url"), news
    except Exception:
        pass
    return None, None


def probe_fortnite_api() -> Tuple[Optional[str], Optional[Dict]]:
    try:
        data = fetch_game_version()
        if data.get("version"):
            return "https://fortniteapi.io/", data
    except Exception:
        pass
    return None, None


def epic_status_maintenance_time() -> Optional[str]:
    try:
        return fetch_fortnite_status()
    except Exception:
        return None


def to_pacific_display(iso_or_utc: Optional[str]) -> str:
    PT = ZoneInfo("America/Los_Angeles")
    if not iso_or_utc:
        return datetime.now(ZoneInfo("UTC")).astimezone(PT).strftime("%Y-%m-%d %I:%M %p %Z")
    try:
        if "UTC" in iso_or_utc:
            dt = datetime.strptime(iso_or_utc.replace(" UTC", ""), "%b %d, %H:%M").replace(
                year=datetime.now().year, tzinfo=ZoneInfo("UTC")
            )
        else:
            dt = datetime.fromisoformat(iso_or_utc.replace("Z", "+00:00"))
        return dt.astimezone(PT).strftime("%Y-%m-%d %I:%M %p %Z")
    except Exception:
        return datetime.now(ZoneInfo("UTC")).astimezone(PT).strftime("%Y-%m-%d %I:%M %p %Z")


def load_state() -> Dict:
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"last_version": None}


def save_state(state: Dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def build_embed(version: str, time_pt: str, lines: List[str], links: List[str], forced: bool) -> Dict:
    desc = "\n".join(lines[:12])[:1500] or "See full notes below."
    fields = [{"name": "Time", "value": time_pt, "inline": True}]
    if links:
        fields.append({"name": "Links", "value": " · ".join(links), "inline": False})
    title = f"🔔 Fortnite Update {version}" + (" (forced)" if forced else "")
    return {
        "username": "Fortnite Updates",
        "embeds": [
            {
                "title": title,
                "description": desc,
                "color": 0x5865F2,
                "fields": fields,
            }
        ],
    }


def main():
    forced = FORCE_SEND or ("--force" in os.sys.argv)
    if not DISCORD_WEBHOOK:
        raise SystemExit("Set DISCORD_WEBHOOK_URL env var first.")

    state = load_state()
    last_version = state.get("last_version")

    news_url, news = get_latest_news_article()
    _, api = probe_fortnite_api()

    version = (news.get("version") if news else None) or (api.get("version") if api else None)

    maint_utc = epic_status_maintenance_time()
    published_iso = (news.get("published") if news else None) or (api.get("published") if api else None)
    time_pt = to_pacific_display(maint_utc or published_iso)

    article = news or api
    lines = select_top_sections(article, max_sections=3, max_items_per=3) if article else ["*(no details available)*"]

    links = []
    if news_url:
        links.append(f"[Full notes]({news_url})")
    links.append("[fortniteapi.io](https://fortniteapi.io/)")

    if not version and not forced:
        maint_utc = epic_status_maintenance_time()
        if maint_utc:
            reason = "Downtime detected via Fortnite API — patch notes not yet available."
            time_pt = to_pacific_display(maint_utc)
        else:
            reason = "State mismatch detected — couldn't retrieve a version from the API."
            time_pt = to_pacific_display(None)

        lines = [f"*({reason})*"]
        links = ["[fortniteapi.io](https://fortniteapi.io/)"]

        payload = build_embed("(unknown)", time_pt, lines, links, forced)
        wire = {"content": "Fortnite update notifier — unknown version", **payload}
        post_webhook(wire)

        state["last_version"] = "(unknown)"
        save_state(state)
        return

    if not forced and last_version == version:
        return

    payload = build_embed(version, time_pt, lines, links, forced)
    wire = {"content": "Fortnite update notifier", **payload}
    post_webhook(wire)

    if not forced and version:
        state["last_version"] = version
        save_state(state)


if __name__ == "__main__":
    main()
