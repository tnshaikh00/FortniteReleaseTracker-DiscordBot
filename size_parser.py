# size_parser.py
import re
from typing import Dict, Optional

SIZE_RX = re.compile(r"(?P<val>\d+(?:\.\d+)?)\s?(?P<unit>GB|MB)", re.IGNORECASE)
PLAT_NAMES = (
    ("pc", "PC"),
    ("ps5", "PS5"),
    ("playstation", "PS5"),
    ("xbox", "Xbox"),
    ("switch", "Switch"),
    ("nintendo", "Switch"),
)

def _detect_platform(text: str) -> Optional[str]:
    low = text.lower()
    for key, out in PLAT_NAMES:
        if key in low:
            return out
    return None

def _to_gb(val: float, unit: str) -> float:
    return val if unit.upper() == "GB" else val / 1024.0

def parse_crowd_sizes(posts: list[str]) -> Dict[str, float]:
    """Scan text posts for platform + size mentions."""
    sizes: Dict[str, float] = {}
    for txt in posts:
        plat = _detect_platform(txt)
        m = SIZE_RX.search(txt)
        if not (plat and m):
            continue
        gb = _to_gb(float(m.group("val")), m.group("unit"))
        sizes[plat] = max(gb, sizes.get(plat, 0.0))
    return sizes

def format_size_field(platform_sizes: Dict[str, float]) -> Optional[str]:
    if not platform_sizes:
        return None
    order = ["PC", "PS5", "Xbox", "Switch"]
    parts = [f"{p} {platform_sizes[p]:.1f} GB" for p in order if p in platform_sizes]
    return " • ".join(parts) if parts else None
