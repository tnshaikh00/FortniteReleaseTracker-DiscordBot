# fortnite_update_notifier.py
import os, re, json, asyncio
from datetime import datetime
from typing import Optional, List, Dict
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import ClientResponseError, ClientTimeout
from bs4 import BeautifulSoup

from fortnite_scraper import (
    parse_fortnite_news_article,
    parse_epic_dev_docs_article,
    select_top_sections,
)
from size_parser import parse_crowd_sizes, format_size_field

# -------------------------
# Config
# -------------------------
NEWS_URL = "https://www.fortnite.com/news?lang=en-US"
EPIC_STATUS_URL = "https://status.epicgames.com/"
DEV_DOCS = [
    "https://dev.epicgames.com/documentation/en-us/fortnite/37-10-fortnite-ecosystem-updates-and-release-notes",
    "https://dev.epicgames.com/documentation/en-us/fortnite/37-00-fortnite-ecosystem-updates-and-release-notes",
    "https://dev.epicgames.com/documentation/en-us/fortnite/36-20-fortnite-ecosystem-updates-and-release-notes",
]
REDDIT_NEW_JSON = "https://www.reddit.com/r/FortNiteBR/new.json?limit=30"

STATE_PATH = os.getenv("STATE_PATH", "state/fortnite_state.json")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
FORCE_SEND = os.getenv("FORCE_SEND", "").lower() == "true"
ENABLE_CROWDSIZE = os.getenv("ENABLE_CROWDSIZE", "").lower() == "true"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
}

# -------------------------
# Helpers
# -------------------------
async def fetch(session: aiohttp.ClientSession, url: str, *, retries: int = 3, json_mode: bool = False, headers: Dict = None):
    h = dict(DEFAULT_HEADERS)
    if headers: h.update(headers)
    delay = 0.8
    for i in range(retries):
        try:
            async with session.get(url, headers=h, timeout=ClientTimeout(total=30)) as r:
                if r.status == 403:
                    raise ClientResponseError(r.request_info, r.history, status=403, message="Forbidden", headers=r.headers)
                r.raise_for_status()
                return await (r.json() if json_mode else r.text())
        except Exception:
            if i == retries - 1:
                raise
            await asyncio.sleep(delay); delay *= 2

async def get_latest_news_article(session: aiohttp.ClientSession):
    try:
        html = await fetch(session, NEWS_URL)
    except ClientResponseError as e:
        if e.status == 403:
            return None, None
        raise
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a"):
        title = (a.get_text(" ", strip=True) or "").lower()
        if not title: continue
        if any(k in title for k in ("update","patch","release notes","hotfix","v")):
            href = a.get("href", "")
            if href and not href.startswith("http"):
                href = "https://www.fortnite.com" + href
            try:
                article_html = await fetch(session, href)
            except ClientResponseError:
                return None, None
            return href, parse_fortnite_news_article(article_html)
    return None, None

async def probe_dev_docs(session: aiohttp.ClientSession):
    for url in DEV_DOCS:
        try:
            html = await fetch(session, url)
            parsed = parse_epic_dev_docs_article(html)
            if parsed.get("version"):
                return url, parsed
        except Exception:
            pass
    return None, None

async def epic_status_maintenance_time(session: aiohttp.ClientSession) -> Optional[str]:
    try:
        html = await fetch(session, EPIC_STATUS_URL)
        text = " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
        m = re.search(r"(?:Scheduled|In progress).+?(\w{3}\s\d{1,2},\s\d{2}:\d{2}\sUTC)", text)
        return m.group(1) if m else None
    except Exception:
        return None

async def crowdsourced_sizes(session: aiohttp.ClientSession) -> Optional[str]:
    try:
        raw = await fetch(
            session,
            REDDIT_NEW_JSON,
            json_mode=True,
            headers={"User-Agent": DEFAULT_HEADERS["User-Agent"] + " FortniteUpdateBot/1.0"},
        )
        posts = [
            f"{c['data'].get('title','')} {c['data'].get('selftext','')}"
            for c in raw.get("data", {}).get("children", [])
        ]
        sizes = parse_crowd_sizes(posts)
        return format_size_field(sizes)
    except Exception:
        return None

def to_pacific_display(iso_or_utc: Optional[str]) -> str:
    PT = ZoneInfo("America/Los_Angeles")
    if not iso_or_utc:
        return datetime.now(ZoneInfo("UTC")).astimezone(PT).strftime("%Y-%m-%d %I:%M %p %Z")
    try:
        if "UTC" in iso_or_utc:
            dt = datetime.strptime(iso_or_utc.replace(" UTC",""), "%b %d, %H:%M").replace(year=datetime.now().year, tzinfo=ZoneInfo("UTC"))
        else:
            dt = datetime.fromisoformat(iso_or_utc.replace("Z","+00:00"))
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

def build_embed(version: str, time_pt: str, lines: List[str], links: List[str], size_field: Optional[str], forced: bool) -> Dict:
    fields = [{"name": "Time", "value": time_pt, "inline": True}]
    if size_field:
        fields.append({
            "name": "Approx. Size",
            "value": size_field + " *(crowdsourced)*",
            "inline": True,
        })
    if links:
        fields.append({"name": "Links", "value": " · ".join(links), "inline": False})
    title = f"🔔 Fortnite Update {version}" + (" (forced)" if forced else "")
    return {
        "username": "Fortnite Updates",
        "embeds": [{
            "title": title,
            "description": "\n".join(lines[:12]) or "See full notes below.",
            "color": 0x5865F2,
            "fields": fields
        }]
    }

# -------------------------
# Main
# -------------------------
async def main():
    forced = FORCE_SEND or ("--force" in os.sys.argv)
    if not DISCORD_WEBHOOK:
        raise SystemExit("Set DISCORD_WEBHOOK_URL env var first.")

    state = load_state()
    async with aiohttp.ClientSession() as session:
        news_url, news = await get_latest_news_article(session)
        dev_url, devs = await probe_dev_docs(session)

        version = (news or {}).get("version") or (devs or {}).get("version")
        if not version and not forced:
            return
        if not forced and state.get("last_version") == version:
            return

        maint_utc = await epic_status_maintenance_time(session)
        published_iso = (news or {}).get("published")
        time_pt = to_pacific_display(maint_utc or published_iso)

        lines = select_top_sections(news or devs, max_sections=3, max_items_per=3)

        size_field = None
        if ENABLE_CROWDSIZE:
            size_field = await crowdsourced_sizes(session)

        links = []
        if news_url: links.append(f"[Full notes (News)]({news_url})")
        if dev_url:  links.append(f"[Dev release notes]({dev_url})")
        links.append("[Epic Status](https://status.epicgames.com/)")

        payload = build_embed(version or "(no version)", time_pt, lines, links, size_field, forced)
        async with session.post(DISCORD_WEBHOOK, json=payload, timeout=ClientTimeout(total=20)) as r:
            r.raise_for_status()

        if not forced and version:
            state["last_version"] = version
            save_state(state)

if __name__ == "__main__":
    asyncio.run(main())


