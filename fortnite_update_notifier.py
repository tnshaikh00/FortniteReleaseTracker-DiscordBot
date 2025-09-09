import os, re, json, time
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from fortnite_scraper import (
    parse_fortnite_news_article,
    parse_epic_dev_docs_article,
    parse_uefn_whats_new,
    select_top_sections,
)
from size_parser import parse_crowd_sizes, format_size_field

from fortnite_api import fetch_game_version, fetch_fortnite_news, fetch_fortnite_status

from fortnite_api import fetch_game_version


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
UEFN_WHATS_NEW_URL = "https://dev.epicgames.com/documentation/en-us/fortnite/whats-new-in-unreal-editor-for-fortnite"
REDDIT_NEW_JSON = "https://www.reddit.com/r/FortNiteBR/new.json?limit=30"

STATE_PATH = os.getenv("STATE_PATH", "state/fortnite_state.json")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
FORCE_SEND = os.getenv("FORCE_SEND", "").lower() == "true"
ENABLE_CROWDSIZE = os.getenv("ENABLE_CROWDSIZE", "").lower() == "true"
DEBUG = os.getenv("DEBUG", "").strip() in ("1", "true", "True", "yes", "on")

def dbg(*a):
    if DEBUG:
        print(*a, flush=True)

# Headers: more “browser-like” to avoid basic bot blocks
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
# HTTP helper (sync)
# -------------------------
def fetch(url: str, *, retries: int = 3, json_mode: bool = False, headers: Dict | None = None, timeout: int = 30):
    h = dict(DEFAULT_HEADERS)
    if headers:
        h.update(headers)
    delay = 0.8
    last_exc = None
    for i in range(retries):
        try:
            dbg(f"[HTTP] GET {url} (try {i+1}/{retries})")
            r = requests.get(url, headers=h, timeout=timeout)
            dbg(f"[HTTP] {r.status_code} for {url}")
            r.raise_for_status()
            return r.json() if json_mode else r.text
        except Exception as e:
            last_exc = e
            dbg(f"[HTTP] Error on {url}: {repr(e)}")
            if i == retries - 1:
                raise
            time.sleep(delay); delay *= 2
    if last_exc:
        raise last_exc

def post_webhook(payload: Dict, *, timeout: int = 20):
    dbg("[DISCORD] POST webhook payload title:",
        payload.get("embeds", [{}])[0].get("title"))
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=timeout)
    dbg("[DISCORD] Status:", r.status_code)
    r.raise_for_status()

# -------------------------
# Probes (sync)
# -------------------------
def get_latest_news_article() -> Tuple[Optional[str], Optional[Dict]]:
    try:
        api_news = fetch_fortnite_news()
        if api_news:
            dbg("[NEWS] using fortniteapi.io news endpoint")
            return "https://fortniteapi.io/news", api_news
        dbg("[NEWS] fortniteapi.io returned no news")
    except Exception as e:
        dbg(f"[NEWS] fortniteapi.io error: {repr(e)}")

    try:
        html = fetch(NEWS_URL)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 403:
            dbg("[NEWS] 403 on news landing page")
            return None, None
        raise

    soup = BeautifulSoup(html, "html.parser")

    # Collect plausible candidates first; prefer real /news/ article paths
    candidates: List[Tuple[int, str]] = []
    for a in soup.find_all("a", href=True):
        title = (a.get_text(" ", strip=True) or "").lower()
        if not title:
            continue
        if any(k in title for k in ("update", "patch", "release notes", "hotfix", "v")):
            href = a["href"]
            if href and not href.startswith("http"):
                href = "https://www.fortnite.com" + href
            score = int("/news/" in href.lower())
            candidates.append((score, href))

    dbg(f"[NEWS] candidates found: {len(candidates)}")

    for _, href in sorted(candidates, key=lambda x: x[0], reverse=True):
        try:
            article_html = fetch(href)
            parsed = parse_fortnite_news_article(article_html)
            if parsed.get("version") or parsed.get("sections"):
                dbg(f"[NEWS] selected: {href} v={parsed.get('version')} sections={len(parsed.get('sections', []))}")
                return href, parsed
        except requests.HTTPError as e:
            dbg(f"[NEWS] skip {href} due to HTTP error: {getattr(e.response,'status_code',None)}")
            continue
        except Exception as e:
            dbg(f"[NEWS] skip {href} due to parse error: {repr(e)}")
            continue
    dbg("[NEWS] no valid article parsed")
    return None, None

def probe_dev_docs() -> Tuple[Optional[str], Optional[Dict]]:
    for url in DEV_DOCS:
        try:
            html = fetch(url)
            parsed = parse_epic_dev_docs_article(html)
            if parsed.get("version"):
                dbg(f"[DEV] {url} v={parsed.get('version')} sections={len(parsed.get('sections', []))}")
                return url, parsed
            else:
                dbg(f"[DEV] {url} no version")
        except Exception as e:
            dbg(f"[DEV] {url} error: {repr(e)}")
    return None, None

def probe_uefn_whats_new() -> Tuple[Optional[str], Optional[Dict]]:
    try:
        html = fetch(UEFN_WHATS_NEW_URL)
        parsed = parse_uefn_whats_new(html)
        if parsed.get("version"):
            dbg(f"[UEFN] v={parsed.get('version')} sections={len(parsed.get('sections', []))}")
            return UEFN_WHATS_NEW_URL, parsed
        dbg("[UEFN] no version")
    except Exception as e:
        dbg(f"[UEFN] error: {repr(e)}")
    return None, None


def probe_fortnite_api() -> Tuple[Optional[str], Optional[Dict]]:
    """Use fortniteapi.io as an additional source for version info."""
    try:
        data = fetch_game_version()
        if data.get("version"):
            dbg(f"[FAPI] v={data.get('version')}")
            return "https://fortniteapi.io/", data
        dbg("[FAPI] no version")
    except Exception as e:
        dbg(f"[FAPI] error: {repr(e)}")
    return None, None

def epic_status_maintenance_time() -> Optional[str]:
    try:
        begin = fetch_fortnite_status()
        if begin:
            dbg(f"[STATUS] downtime via fortniteapi.io: {begin}")
            return begin
        dbg("[STATUS] fortniteapi.io reported no downtime")
    except Exception as e:
        dbg(f"[STATUS] fortniteapi.io error: {repr(e)}")

    try:
        html = fetch(EPIC_STATUS_URL)
        text = " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
        # Allow H:mm or HH:mm and optional space before UTC
        m = re.search(r"(?:Scheduled|In progress).+?(\w{3}\s\d{1,2},\s\d{1,2}:\d{2}\s?UTC)", text)
        if m:
            dbg(f"[STATUS] found maintenance time: {m.group(1)}")
        else:
            dbg("[STATUS] no maintenance time found")
        return m.group(1) if m else None
    except Exception as e:
        dbg(f"[STATUS] error: {repr(e)}")
        return None

def crowdsourced_sizes() -> Optional[str]:
    if not ENABLE_CROWDSIZE:
        return None
    try:
        raw = fetch(
            REDDIT_NEW_JSON,
            json_mode=True,
            headers={"User-Agent": DEFAULT_HEADERS["User-Agent"] + " FortniteUpdateBot/1.0"},
        )
        posts = [
            f"{c['data'].get('title','')} {c['data'].get('selftext','')}"
            for c in raw.get("data", {}).get("children", [])
        ]
        sizes = parse_crowd_sizes(posts)
        out = format_size_field(sizes)
        dbg(f"[SIZES] {out}")
        return out
    except Exception as e:
        dbg(f"[SIZES] error: {repr(e)}")
        return None

# -------------------------
# Time + state + embed
# -------------------------
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

def build_embed(version: str, time_pt: str, lines: List[str], links: List[str], size_field: Optional[str], forced: bool) -> Dict:
    desc = "\n".join(lines[:12])[:1500] or "See full notes below."
    fields = [{"name": "Time", "value": time_pt, "inline": True}]
    if size_field:
        fields.append({"name": "Approx. Size", "value": size_field + " *(crowdsourced)*", "inline": True})
    if links:
        fields.append({"name": "Links", "value": " · ".join(links), "inline": False})
    title = f"🔔 Fortnite Update {version}" + (" (forced)" if forced else "")
    return {
        "username": "Fortnite Updates",
        "embeds": [{
            "title": title,
            "description": desc,
            "color": 0x5865F2,
            "fields": fields
        }]
    }

# -------------------------
# Main (sync)
# -------------------------
def main():
    forced = FORCE_SEND or ("--force" in os.sys.argv)
    if not DISCORD_WEBHOOK:
        raise SystemExit("Set DISCORD_WEBHOOK_URL env var first.")

    state = load_state()
    last_version = state.get("last_version")

    news_url, news = get_latest_news_article()
    dev_url, devs = probe_dev_docs()
    uefn_url, uefn = probe_uefn_whats_new()
    api_url, api = probe_fortnite_api()

    version = (
        (news.get("version") if news else None)
        or (devs.get("version") if devs else None)
        or (uefn.get("version") if uefn else None)
        or (api.get("version") if api else None)
    )

    maint_utc = epic_status_maintenance_time()
    published_iso = (
        (news.get("published") if news else None)
        or (devs.get("published") if devs else None)
        or (uefn.get("published") if uefn else None)
        or (api.get("published") if api else None)
    )
    time_pt = to_pacific_display(maint_utc or published_iso)

    article = news or devs or uefn or api
    lines = select_top_sections(article, max_sections=3, max_items_per=3) if article else ["*(no details available)*"]

    size_field = crowdsourced_sizes()

    links = []
    if news_url: links.append(f"[Full notes (News)]({news_url})")
    if dev_url:  links.append(f"[Dev release notes]({dev_url})")
    if uefn_url: links.append(f"[UEFN What's New]({uefn_url})")
    if api_url: links.append(f"[fortniteapi.io]({api_url})")
    links.append("[Epic Status](https://status.epicgames.com/)")

    dbg("[SUMMARY] version:", version)
    dbg("[SUMMARY] published_iso:", published_iso)
    dbg("[SUMMARY] lines:", lines)
    dbg("[SUMMARY] links:", links)

    # Unknown path (no version, not forced)
    if not version and not forced:
        maint_utc = epic_status_maintenance_time()
        if maint_utc:
            reason = "Downtime detected via Epic Status — patch notes not yet available."
            time_pt = to_pacific_display(maint_utc)
        else:
            reason = "State mismatch detected — couldn’t scrape a version from Epic sources."
            time_pt = to_pacific_display(None)

        size_field = crowdsourced_sizes()

        lines = [f"*({reason})*"]
        links = ["[Epic Status](https://status.epicgames.com/)"]

        payload = build_embed("(unknown)", time_pt, lines, links, size_field, forced)
        wire = {"content": "Fortnite update notifier — unknown version", **payload}
        dbg("[DISCORD] Payload preview:", json.dumps(wire, indent=2))
        post_webhook(wire)

        state["last_version"] = "(unknown)"
        save_state(state)
        return

    # Dedupe by version
    if not forced and last_version == version:
        dbg("[SKIP] version already sent:", version)
        return

    payload = build_embed(version, time_pt, lines, links, size_field, forced)
    wire = {"content": "Fortnite update notifier", **payload}
    dbg("[DISCORD] Payload preview:", json.dumps(wire, indent=2))
    post_webhook(wire)

    if not forced and version:
        state["last_version"] = version
        save_state(state)

if __name__ == "__main__":
    main()

