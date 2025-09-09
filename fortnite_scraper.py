# scripts/fortnite_scraper.py
import re
from typing import List, Dict, Optional
from datetime import datetime, timezone
from bs4 import BeautifulSoup

VERSION_RX = re.compile(r"\bv?\s?(\d{2}\.\d{1,2}|\d{2}\-\d{2})\b", re.IGNORECASE)

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def extract_version_from_html(html: str) -> Optional[str]:
    m = VERSION_RX.search(html or "")
    if not m: return None
    return "v" + m.group(1).replace("-", ".")

def parse_fortnite_news_article(html: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.find(["h1","title"])
    title = _norm(title_node.get_text(" ", strip=True)) if title_node else ""
    published = None
    time_tag = soup.find("time")
    if time_tag and (time_tag.get("datetime") or time_tag.get_text(strip=True)):
        published = time_tag.get("datetime") or time_tag.get_text(strip=True)

    sections: List[Dict] = []
    for header in soup.select("h2, h3"):
        htext = _norm(header.get_text(" ", strip=True))
        if not htext: continue
        items: List[str] = []
        for sib in header.find_all_next():
            if sib == header: continue
            if sib.name in ["h2","h3"]: break
            if sib.name == "ul":
                for li in sib.select("li"):
                    t = _norm(li.get_text(" ", strip=True))
                    if 3 <= len(t) <= 500: items.append(t)
            if sib.name == "p":
                t = _norm(sib.get_text(" ", strip=True))
                if 10 <= len(t) <= 500: items.append(t)
        if htext and items:
            sections.append({"header": htext, "items": items[:10]})

    version = extract_version_from_html(html)
    return {"source": "Fortnite News", "title": title, "published": published, "version": version, "sections": sections}

def parse_epic_dev_docs_article(html: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.find(["h1","title"])
    title = _norm(title_node.get_text(" ", strip=True)) if title_node else ""
    published = None
    pub_tag = soup.find(string=re.compile(r"Published\s", re.IGNORECASE))
    if pub_tag: published = _norm(pub_tag)

    sections: List[Dict] = []
    for header in soup.select("h2, h3"):
        htext = _norm(header.get_text(" ", strip=True))
        if not htext: continue
        items: List[str] = []
        for sib in header.find_all_next():
            if sib == header: continue
            if sib.name in ["h2","h3"]: break
            if sib.name == "ul":
                for li in sib.select("li"):
                    t = _norm(li.get_text(" ", strip=True))
                    if 3 <= len(t) <= 500: items.append(t)
            if sib.name == "p":
                t = _norm(sib.get_text(" ", strip=True))
                if 10 <= len(t) <= 500: items.append(t)
        if htext and items:
            sections.append({"header": htext, "items": items[:10]})

    version = extract_version_from_html(html)
    return {"source": "Epic Dev Docs", "title": title, "published": published, "version": version, "sections": sections}

def select_top_sections(article: Dict, max_sections: int = 3, max_items_per: int = 3) -> List[Dict]:
    priority = [
        "New", "Weapon", "Gadget", "Map", "Gameplay", "Vehicles",
        "Improvements", "Fixes", "Creative", "UEFN", "Performance", "Bug"
    ]
    scored = []
    for sec in article.get("sections", []):
        header = sec["header"]
        score = sum(1 for k in priority if k.lower() in header.lower())
        scored.append((score, {"header": header, "items": sec["items"][:max_items_per]}))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [x for _, x in scored[:max_sections]] or article.get("sections", [])[:max_sections]

def compose_summary_lines(article: Dict, max_sections: int = 3, max_items_per: int = 3) -> List[str]:
    lines: List[str] = []
    for sec in select_top_sections(article, max_sections, max_items_per):
        lines.append(f"**{sec['header']}**")
        for it in sec["items"]:
            lines.append(f"• {it}")
    return lines
