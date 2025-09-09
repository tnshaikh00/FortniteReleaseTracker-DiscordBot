# fortnite_scraper.py
# and return a unified dict: {"version": str|None, "published": str|None, "sections": [ {header, items[]} ]}

from __future__ import annotations
import re
from datetime import datetime
from typing import List, Dict, Optional
from bs4 import BeautifulSoup

# Versions like: v37.20, 37.20, 37-20
_VERSION_RX = re.compile(r"\bv?\s?(\d{2}\.\d{1,2}|\d{2}\-\d{1,2})\b", re.IGNORECASE)

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _extract_version(text_or_html: str) -> Optional[str]:
    m = _VERSION_RX.search(text_or_html or "")
    if not m:
        return None
    token = m.group(1).replace("-", ".")
    return "v" + token if not token.lower().startswith("v") else token

def _iso_or_none(s: Optional[str]) -> Optional[str]:
    """Accept ISO, or parse common date strings like 'Sep 06, 2025' into ISO."""
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}T", s):
        return s
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s.strip(), fmt)
            return dt.strftime("%Y-%m-%dT00:00:00Z")
        except Exception:
            pass
    return None

def _collect_sections_from_anchor(soup: BeautifulSoup, start_tag) -> List[Dict]:
    """
    Collect 'header + items' blocks beginning at start_tag, tolerant across News/DevDocs/UEFN.
    """
    sections: List[Dict] = []
    cur_header: Optional[str] = None
    items: List[str] = []

    def flush():
        nonlocal cur_header, items, sections
        if cur_header and items:
            sections.append({"header": cur_header, "items": items[:10]})
        cur_header, items = None, []

    for sib in start_tag.find_all_next():
        name = getattr(sib, "name", None)
        if name in ("h2", "h3"):
            flush()
            cur_header = _norm(sib.get_text(" ", strip=True))
        elif name == "ul":
            for li in sib.select("li"):
                t = _norm(li.get_text(" ", strip=True))
                if 3 <= len(t) <= 500:
                    items.append(t)
        elif name == "p":
            t = _norm(sib.get_text(" ", strip=True))
            if 10 <= len(t) <= 500:
                items.append(t)
    flush()
    return sections

# --------------------------------------------------------------------
# Fortnite News article pages (https://www.fortnite.com/news/... )
# --------------------------------------------------------------------
def parse_fortnite_news_article(html: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")

    # Published: often present as <time datetime="...">
    time_tag = soup.find("time")
    published_iso = _iso_or_none(time_tag.get("datetime") or time_tag.get_text(strip=True)) if time_tag else None

    # Version: scan entire page to be resilient
    version = _extract_version(html)

    # Sections: prefer main <article>, else first heading as anchor
    article = soup.find("article") or soup.find(["h1", "h2", "h3"]) or soup
    sections = _collect_sections_from_anchor(soup, article)
    if not sections:
        # fallback: grab some bullets if structure is odd
        bullets = [_norm(li.get_text(" ", strip=True)) for li in soup.select("li")][:10]
        if bullets:
            sections = [{"header": "Highlights", "items": bullets}]

    return {"version": version, "published": published_iso, "sections": sections}

# --------------------------------------------------------------------
# Epic Dev Docs release notes (ecosystem updates pages)
# --------------------------------------------------------------------
def parse_epic_dev_docs_article(html: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")

    # Version: aggressively scan
    version = _extract_version(html)

    # Published: <time> tags or "Published Sep 06, 2025" text
    published_iso = None
    for t in soup.select("time"):
        iso = _iso_or_none(t.get("datetime") or t.get_text(strip=True))
        if iso:
            published_iso = iso
            break
    if not published_iso:
        m = re.search(r"Published\s+([A-Za-z]{3,}\s\d{1,2},\s\d{4})", soup.get_text(" ", strip=True), re.IGNORECASE)
        if m:
            published_iso = _iso_or_none(m.group(1))

    # Sections: walk from the first top-level header down
    start = soup.find(["h1", "h2"]) or soup
    sections = _collect_sections_from_anchor(soup, start)
    if not sections:
        bullets = [_norm(li.get_text(" ", strip=True)) for li in soup.select("li")][:10]
        if bullets:
            sections = [{"header": "Highlights", "items": bullets}]

    return {"version": version, "published": published_iso, "sections": sections}

# --------------------------------------------------------------------
# UEFN "What's New" page (stable, versioned releases)
# --------------------------------------------------------------------
_UEFN_RELEASE_RX = re.compile(
    r"""release\s+(\d{1,2})[.\-](\d{1,2})      # 37.20 or 37-20
        (?:\s*[\(\-–—]\s*                     # optional "(" or dash/emdash
        ([^)]+?20\d{2})                       # date-like text containing a 20xx year
        \)?)?                                  # optional closing ")"
    """,
    re.IGNORECASE | re.VERBOSE,
)

def parse_uefn_whats_new(html: str) -> Dict:
    """
    Return the MOST RECENT release from the UEFN 'What's New' page in the same dict shape.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find candidate "Release XX.YY (Month DD, YYYY)" headers
    candidates = []
    for tag in soup.select("h2, h3, h4"):
        t = _norm(tag.get_text(" ", strip=True))
        m = _UEFN_RELEASE_RX.search(t)
        if not m:
            continue
        major, minor, date_str = m.groups()
        version = f"v{int(major):02d}.{int(minor):02d}"
        published_iso = _iso_or_none(date_str)
        candidates.append((tag, version, published_iso))

    if not candidates:
        # as a last resort, try a page-level version; no sections
        return {"version": _extract_version(html), "published": None, "sections": []}

    # Most recent is typically the first matched header
    start_tag, version, published_iso = candidates[0]

    sections: List[Dict] = []
    cur_header: Optional[str] = None
    items: List[str] = []

    def flush():
        nonlocal cur_header, items, sections
        if cur_header and items:
            sections.append({"header": cur_header, "items": items[:10]})
        cur_header, items = None, []

    for sib in start_tag.find_all_next():
        name = getattr(sib, "name", None)
        if name in ("h2", "h3"):
            t = _norm(sib.get_text(" ", strip=True))
            # Stop if next release header starts
            if _UEFN_RELEASE_RX.search(t) and sib is not start_tag:
                break
            flush()
            cur_header = t
        elif name == "ul":
            for li in sib.select("li"):
                txt = _norm(li.get_text(" ", strip=True))
                if 3 <= len(txt) <= 500:
                    items.append(txt)
        elif name == "p":
            txt = _norm(sib.get_text(" ", strip=True))
            if 10 <= len(txt) <= 500:
                items.append(txt)

    flush()

    if not sections:
        # fallback: grab nearby bullets
        bullets = []
        for ul in start_tag.find_all_next("ul"):
            for li in ul.select("li"):
                txt = _norm(li.get_text(" ", strip=True))
                if 3 <= len(txt) <= 500:
                    bullets.append(txt)
                if len(bullets) >= 10:
                    break
            if bullets:
                break
        if bullets:
            sections = [{"header": "Highlights", "items": bullets[:10]}]

    return {"version": version, "published": published_iso, "sections": sections}

# --------------------------------------------------------------------
# Summarizer: same signature you already use in the notifier
# --------------------------------------------------------------------
def select_top_sections(article: Dict, max_sections: int = 3, max_items_per: int = 3) -> List[str]:
    sections = article.get("sections", []) if article else []
    if not sections:
        return []

    priority = ("New","Weapon","Gadget","Map","Gameplay","Vehicles",
                "Improvements","Fixes","Creative","UEFN","Performance","Bug")

    # Light scoring to prioritize likely-interesting headers
    scored = []
    for sec in sections:
        hdr = (sec.get("header") or "").lower()
        score = sum(k.lower() in hdr for k in priority)
        scored.append((score, sec))
    scored.sort(key=lambda x: x[0], reverse=True)

    lines: List[str] = []
    for _, sec in scored[:max_sections]:
        header = sec.get("header") or "Highlights"
        lines.append(f"**{_norm(header)}**")
        for it in (sec.get("items") or [])[:max_items_per]:
            lines.append(f"• {_norm(it)}")
    return lines
