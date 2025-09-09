# fortnite_update_notifier.py
import os, re, json, asyncio
from datetime import datetime
from typing import Optional, Tuple, List, Dict
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup
from aiohttp import ClientResponseError, ClientTimeout

NEWS_URL = "https://www.fortnite.com/news?lang=en-US"
EPIC_STATUS_URL = "https://status.epicgames.com/"
DEV_DOCS = [
    "https://dev.epicgames.com/documentation/en-us/fortnite/37-10-fortnite-ecosystem-updates-and-release-notes",
    "https://dev.epicgames.com/documentation/en-us/fortnite/37-00-fortnite-ecosystem-updates-and-release-notes",
    "https://dev.epicgames.com/documentation/en-us/fortnite/36-20-fortnite-ecosystem-updates-and-release-notes",
]

STATE_PATH = os.getenv("STATE_PATH", "state/fortnite_state.json")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
FORCE_SEND_ENV = os.getenv("FORCE_SEND", "").lower() == "true"  # optional env trigger

VERSION_RX = re.compile(r"\bv?\s?(\d{2}\.\d{1,2}|\d{2}\-\d{2})\b", re.IGNORECASE)
DEFAULT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/127.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
}

async def fetch(session: aiohttp.ClientSession, url: str, *, retries: int = 3) -> str:
    delay = 0.8
    for i in range(retries):
        try:
            async with session.get(url, headers=DEFAULT_HEADERS, timeout=ClientTimeout(total=30)) as r:
                if r.status == 403:
                    raise ClientResponseError(r.request_info, r.history, status=403, message="Forbidden", headers=r.headers)
                r.raise_for_status()
                return await r.text()
        except Exception:
            if i == retries - 1:
                raise
            await asyncio.sleep(delay); delay *= 2

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def extract_version_from_html(html: str) -> Optional[str]:
    m = VERSION_RX.search(html or "")
    return ("v" + m.group(1).replace("-", ".")) if m else None

def parse_news_article(html: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")
    time_tag = soup.find("time")
    published = time_tag.get("datetime") if time_tag and time_tag.get("datetime") else (time_tag.get_text(strip=True) if time_tag else None)
    sections: List[Dict] = []
    for h in soup.select("h2, h3"):
        header = _norm(h.get_text(" ", strip=True))
        if not header: continue
        items: List[str] = []
        for sib in h.find_all_next():
            if sib == h: continue
            if sib.name in ("h2","h3"): break
            if sib.name == "ul":
                for li in sib.select("li"):
                    t = _norm(li.get_text(" ", strip=True))
                    if 3 <= len(t) <= 500: items.append(t)
            if sib.name == "p":
                t = _norm(sib.get_text(" ", strip=True))
                if 10 <= len(t) <= 500: items.append(t)
        if items: sections.append({"header": header, "items": items[:10]})
    return {"version": extract_version_from_html(html), "published": published, "sections": sections}

def parse_dev_docs_article(html: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")
    pub = soup.find(string=re.compile(r"Published\s", re.IGNORECASE))
    published = _norm(pub) if pub else None
    sections: List[Dict] = []
    for h in soup.select("h2, h3"):
        header = _norm(h.get_text(" ", strip=True))
        if not header: continue
        items: List[str] = []
        for sib in h.find_all_next():
            if sib == h: continue
            if sib.name in ("h2","h3"): break
            if sib.name == "ul":
                for li in sib.select("li"):
                    t = _norm(li.get_text(" ", strip=True))
                    if 3 <= len(t) <= 500: items.append(t)
            if sib.name == "p":
                t = _norm(sib.get_text(" ", strip=True))
                if 10 <= len(t) <= 500: items.append(t)
        if items: sections.append({"header": header, "items": items[:10]})
    return {"version": extract_version_from_html(html), "published": published, "sections": sections}

def select_top_sections(article: Dict, max_sections: int = 3, max_items_per: int = 3) -> List[str]:
    priority = ("New","Weapon","Gadget","Map","Gameplay","Vehicles","Improvements","Fixes","Creative","UEFN","Performance","Bug")
    scored = []
    for sec in article.get("sections", []):
        scored.append((sum(k.lower() in sec["header"].lower() for k in priority), sec))
    scored.sort(key=lambda x: x[0], reverse=True)
    lines: List[str] = []
    for _, sec in (scored[:max_sections] if scored else article.get("sections", [])[:max_sections]):
        lines.append(f"**{sec['header']}**")
        for it in sec["items"][:max_items_per]:
            lines.append(f"• {it}")
    return lines

async def get_latest_news_article(session: aiohttp.ClientSession) -> Tuple[Optional[str], Optional[Dict]]:
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
            return href, parse_news_article(article_html)
    return None, None

async def probe_dev_docs(session: aiohttp.ClientSession) -> Tuple[Optional[str], Optional[Dict]]:
    for url in DEV_DOCS:
        try:
            html = await fetch(session, url)
            parsed = parse_dev_docs_article(html)
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

def build_embed(version: str, time_pt: str, lines: List[str], links: List[str], forced: bool) -> Dict:
    desc = "\n".join(lines[:12]) or "See full notes below."
    title = f"🔔 Fortnite Update {version}" + (" (forced)" if forced else "")
    return {
        "username": "Fortnite Updates",
        "embeds": [{
            "title": title,
            "description": desc,
            "color": 0x5865F2,
            "fields": [
                {"name": "Time", "value": time_pt, "inline": True},
                {"name": "Links", "value": " · ".join(links) if links else "—", "inline": False},
            ],
        }]
    }

async def main():
    # Optional CLI flag: --force
    FORCE_SEND = os.getenv("FORCE_SEND", "").lower() == "true" or ("--force" in os.sys.argv)

    if not DISCORD_WEBHOOK:
        raise SystemExit("Set DISCORD_WEBHOOK_URL env var first.")

    state = load_state()

    async with aiohttp.ClientSession() as session:
        news_url, news = await get_latest_news_article(session)
        dev_url, devs = await probe_dev_docs(session)

        # Try to detect version
        version = (news or {}).get("version") or (devs or {}).get("version")

        # If forced and no version found, still send a test/ping
        if FORCE_SEND and not version:
            version = "(no version detected)"
            lines = ["**Connectivity Test**", "• This is a forced test message.", "• If you see this, the webhook is working."]
            time_pt = to_pacific_display(None)
            links = ["[Epic Status](https://status.epicgames.com/)"]
        else:
            # Normal behavior: bail if nothing to announce
            if not version:
                return
            if not FORCE_SEND and state.get("last_version") == version:
                return
            maint_utc = await epic_status_maintenance_time(session)
            published_iso = (news or {}).get("published")
            time_pt = to_pacific_display(maint_utc or published_iso)
            lines = select_top_sections(news or devs, max_sections=3, max_items_per=3)
            links = []
            if news_url: links.append(f"[Full notes (News)]({news_url})")
            if dev_url:  links.append(f"[Dev release notes]({dev_url})")
            links.append("[Epic Status](https://status.epicgames.com/)")

        # Build payload (send both content and embed for maximum deliverability)
        embed = build_embed(version, time_pt, lines, links, forced=FORCE_SEND)
        payload = {"content": "Fortnite update notifier", **embed}

        async with session.post(DISCORD_WEBHOOK, json=payload, timeout=ClientTimeout(total=20)) as r:
            r.raise_for_status()

        if not FORCE_SEND:
            state["last_version"] = version
            save_state(state)

if __name__ == "__main__":
    asyncio.run(main())


