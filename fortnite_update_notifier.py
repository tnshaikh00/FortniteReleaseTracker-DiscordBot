# scripts/fortnite_update_notifier.py
import os, re, json, asyncio, logging
from datetime import datetime
from typing import Optional, Tuple, List, Dict
import aiohttp
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo  # Python 3.9+
from scripts.fortnite_scraper import (
    parse_fortnite_news_article,
    parse_epic_dev_docs_article,
    compose_summary_lines,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

NEWS_URL = "https://www.fortnite.com/news?lang=en-US"
# Probe a handful of likely dev-docs URLs each run; newest first
DEV_DOCS_CANDIDATES = [
    "https://dev.epicgames.com/documentation/en-us/fortnite/37-10-fortnite-ecosystem-updates-and-release-notes",
    "https://dev.epicgames.com/documentation/en-us/fortnite/37-00-fortnite-ecosystem-updates-and-release-notes",
    "https://dev.epicgames.com/documentation/en-us/fortnite/36-20-fortnite-ecosystem-updates-and-release-notes",
]
EPIC_STATUS_URL = "https://status.epicgames.com/"

STATE_PATH = os.getenv("STATE_PATH", "state/fortnite_state.json")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL")
ENABLE_CROWDSIZE = os.getenv("ENABLE_CROWDSIZE", "false").lower() == "true"
REDDIT_NEW_JSON = "https://www.reddit.com/r/FortNiteBR/new.json?limit=20"

SIZE_RX = re.compile(r"(\d+(?:\.\d+)?)\s?(GB|MB)", re.IGNORECASE)

def load_state() -> Dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_version": None}

def save_state(state: Dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

async def fetch(session: aiohttp.ClientSession, url: str, headers: Optional[Dict]=None) -> str:
    h = headers or {}
    if "reddit.com" in url:
        h["User-Agent"] = "Mozilla/5.0 (compatible; FortniteUpdateBot/1.0)"
    async with session.get(url, headers=h, timeout=aiohttp.ClientTimeout(total=30)) as r:
        r.raise_for_status()
        return await r.text()

async def get_latest_news_article(session: aiohttp.ClientSession) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[Dict]]:
    html = await fetch(session, NEWS_URL)
    soup = BeautifulSoup(html, "html.parser")
    # Find first card that looks like an update
    for a in soup.find_all("a"):
        title = (a.get_text(" ", strip=True) or "").strip()
        if not title: continue
        if any(k in title.lower() for k in ["update", "patch", "release notes", "hotfix", "v"]):
            href = a.get("href", "")
            if href and not href.startswith("http"):
                href = "https://www.fortnite.com" + href
            # Pull the full article for structure
            article_html = await fetch(session, href)
            parsed = parse_fortnite_news_article(article_html)
            version = parsed.get("version")
            return title, href, version, parsed
    return None, None, None, None

async def probe_dev_docs(session: aiohttp.ClientSession) -> Tuple[Optional[str], Optional[str], Optional[Dict]]:
    for url in DEV_DOCS_CANDIDATES:
        try:
            html = await fetch(session, url)
            parsed = parse_epic_dev_docs_article(html)
            version = parsed.get("version")
            if version:
                return url, version, parsed
        except Exception as e:
            logging.info(f"Dev-doc probe failed {url}: {e}")
    return None, None, None

async def epic_status_maintenance_time(session: aiohttp.ClientSession) -> Optional[str]:
    """Return a human string like 'Sep 08, 02:00 UTC' if visible on status page."""
    try:
        html = await fetch(session, EPIC_STATUS_URL)
        text = " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
        m = re.search(r"(?:Scheduled|In progress).+?(\w{3}\s\d{1,2},\s\d{2}:\d{2}\sUTC)", text)
        if m: return m.group(1)
    except Exception as e:
        logging.info(f"Epic status parse failed: {e}")
    return None

async def reddit_size_range(session: aiohttp.ClientSession) -> Optional[str]:
    if not ENABLE_CROWDSIZE: return None
    try:
        raw = await fetch(session, REDDIT_NEW_JSON)
        data = json.loads(raw)
        vals: List[float] = []
        for p in data.get("data", {}).get("children", []):
            d = p.get("data", {})
            txt = f"{d.get('title','')} {d.get('selftext','')}"
            if any(k in txt.lower() for k in ["update", "patch", "size"]):
                for m in SIZE_RX.finditer(txt):
                    v, unit = float(m.group(1)), m.group(2).upper()
                    if unit == "MB": v = v / 1024.0
                    vals.append(v)
        if not vals: return None
        lo, hi = min(vals), max(vals)
        return f"~{hi:.1f} GB" if abs(hi - lo) < 0.2 else f"{lo:.1f}–{hi:.1f} GB"
    except Exception as e:
        logging.info(f"Reddit size scan failed: {e}")
        return None

def to_pacific_display(iso_or_utc_str: Optional[str]) -> str:
    """Convert an ISO-like or 'Aug 26, 08:00 UTC' to America/Los_Angeles display."""
    PT = ZoneInfo("America/Los_Angeles")
    now_pt = datetime.now(ZoneInfo("UTC")).astimezone(PT)
    if not iso_or_utc_str:
        return now_pt.strftime("%Y-%m-%d %I:%M %p %Z")

    try:
        if "UTC" in iso_or_utc_str:
            # e.g. "Aug 26, 08:00 UTC"
            dt = datetime.strptime(iso_or_utc_str.replace(" UTC",""), "%b %d, %H:%M")
            # year guess: if month/day have passed this year, use this year else this year as well
            dt = dt.replace(year=datetime.now().year, tzinfo=ZoneInfo("UTC"))
        else:
            # ISO or time tag
            dt = datetime.fromisoformat(iso_or_utc_str.replace("Z","+00:00"))
        return dt.astimezone(PT).strftime("%Y-%m-%d %I:%M %p %Z")
    except Exception:
        return now_pt.strftime("%Y-%m-%d %I:%M %p %Z")

def build_discord_embed(version: str, time_pt: str, lines: List[str], links: List[str], size_str: Optional[str]) -> Dict:
    description = "\n".join(lines[:12]) if lines else "See full notes below."
    fields = [
        {"name": "Time", "value": time_pt, "inline": True},
    ]
    if size_str:
        fields.append({"name": "Approx. Size", "value": size_str + " *(crowdsourced)*", "inline": True})
    if links:
        fields.append({"name": "Links", "value": " · ".join(links), "inline": False})

    return {
        "username": "Fortnite Updates",
        "embeds": [{
            "title": f"🔔 Fortnite Update {version}",
            "description": description,
            "color": 0x5865F2,
            "fields": fields
        }]
    }

async def main():
    state = load_state()
    async with aiohttp.ClientSession() as session:
        title, news_url, news_version, news_parsed = await get_latest_news_article(session)
        dev_url, dev_version, dev_parsed = await probe_dev_docs(session)
        version = news_version or dev_version
        if not version:
            logging.info("No version detected from News or Dev Docs.")
            return

        if state.get("last_version") == version:
            logging.info(f"No new version (still {version}).")
            return

        # time (prefer Epic status if present; fallback to article published time)
        maint_utc = await epic_status_maintenance_time(session)
        # news_published may be ISO; convert to PT as fallback
        news_published = news_parsed.get("published") if news_parsed else None
        time_pt = to_pacific_display(maint_utc or news_published)

        # highlights
        lines = compose_summary_lines(news_parsed or dev_parsed, max_sections=3, max_items_per=3)

        # optional size range
        size_range = await reddit_size_range(session)

        # links
        links = []
        if news_url: links.append(f"[Full notes (News)]({news_url})")
        if dev_url:  links.append(f"[Dev release notes]({dev_url})")
        links.append("[Epic Status](https://status.epicgames.com/)")

        payload = build_discord_embed(version, time_pt, lines, links, size_range)

        if not DISCORD_WEBHOOK:
            logging.error("DISCORD_WEBHOOK_URL not set.")
            return

        async with session.post(DISCORD_WEBHOOK, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status >= 300:
                text = await r.text()
                raise RuntimeError(f"Webhook failed: {r.status} {text}")

        # persist state
        state["last_version"] = version
        save_state(state)
        logging.info(f"Announced {version} and updated state.")

if __name__ == "__main__":
    asyncio.run(main())
