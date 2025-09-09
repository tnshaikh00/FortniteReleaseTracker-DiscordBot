# size_parser.py
import re
from typing import Dict, Optional, List, Tuple, Any

# --- Patterns & mappings ---

# Platform keywords (longer phrases first to avoid partial matches).
PLATFORM_KEYS = [
    ("nintendo switch", "Switch"),
    ("playstation 5", "PS5"),
    ("xbox series x", "Xbox"),
    ("xbox series s", "Xbox"),
    ("windows", "PC"),
    ("playstation", "PS5"),
    ("nintendo", "Switch"),
    ("switch", "Switch"),
    ("xbox", "Xbox"),
    ("ps5", "PS5"),
    ("pc", "PC"),
]

# Build an alternation for platform matching.
PLAT_ALT = "|".join(sorted((re.escape(k) for k, _ in PLATFORM_KEYS), key=len, reverse=True))
PLAT_MAP = {k.lower(): out for k, out in PLATFORM_KEYS}

# Sizes like "5.1 GB", "6200MB", etc.
SIZE_RX = re.compile(r"(?P<val>\d+(?:\.\d+)?)\s?(?P<unit>GB|MB)", re.IGNORECASE)

# Platform before size within ~20 non-digit chars (e.g., "PS5 6.2GB", "PC about 5.1 GB").
PAIR_RX = re.compile(
    rf"(?P<plat>{PLAT_ALT})[^\d]{{0,20}}(?P<val>\d+(?:\.\d+)?)[ ]?(?P<unit>GB|MB)",
    re.IGNORECASE,
)

# Size before platform within ~10 non-word chars (e.g., "5.1GB on PC", "6200 MB for PS5").
REV_PAIR_RX = re.compile(
    rf"(?P<val>\d+(?:\.\d+)?)[ ]?(?P<unit>GB|MB)[^\w]{{0,10}}(?:on|for|at|to|for the)?[^\w]{{0,10}}(?P<plat>{PLAT_ALT})",
    re.IGNORECASE,
)

# --- Helpers ---

def _norm_platform(name: str) -> Optional[str]:
    return PLAT_MAP.get(name.lower())

def _to_gb(val: float, unit: str) -> float:
    return val if unit.upper() == "GB" else val / 1024.0

def _extract_platform_sizes_from_text(text: str) -> List[Tuple[str, float]]:
    """
    Extract (Platform, GB) pairs from a single post, using multiple patterns.
    Returns possibly multiple pairs if the post mentions multiple platforms.
    """
    res: List[Tuple[str, float]] = []
    low = text.lower()

    # platform → size
    for m in PAIR_RX.finditer(low):
        plat = _norm_platform(m.group("plat"))
        if not plat:
            continue
        gb = _to_gb(float(m.group("val")), m.group("unit"))
        res.append((plat, gb))

    # size → platform
    for m in REV_PAIR_RX.finditer(low):
        plat = _norm_platform(m.group("plat"))
        if not plat:
            continue
        gb = _to_gb(float(m.group("val")), m.group("unit"))
        res.append((plat, gb))

    # Fallback (least reliable): single platform mention + single size anywhere
    if not res:
        fallback_plat = None
        for k, out in PLATFORM_KEYS:
            if k in low:
                fallback_plat = out
                break
        if fallback_plat:
            m = SIZE_RX.search(low)
            if m:
                gb = _to_gb(float(m.group("val")), m.group("unit"))
                res.append((fallback_plat, gb))

    return res

def _cluster_values(values: List[float], rel_tol: float = 0.12, abs_tol: float = 0.25) -> Tuple[float, int]:
    """
    Greedy clustering to pick the strongest agreement among reported sizes.
    - rel_tol: relative tolerance (12% default)
    - abs_tol: absolute tolerance (0.25 GB default)
    Returns (median_of_best_cluster, votes_in_cluster). If no values, returns (0.0, 0).
    """
    if not values:
        return (0.0, 0)
    vals = sorted(values)
    best_cluster: List[float] = []
    n = len(vals)
    i = 0
    while i < n:
        cluster = [vals[i]]
        j = i + 1
        while j < n:
            median = cluster[len(cluster) // 2]
            dt = abs(vals[j] - median)
            if dt <= max(abs_tol, median * rel_tol):
                cluster.append(vals[j])
                j += 1
            else:
                break
        if len(cluster) > len(best_cluster):
            best_cluster = cluster[:]
        i = j

    k = len(best_cluster)
    if k == 0:
        return (0.0, 0)
    mid = k // 2
    median = best_cluster[mid] if k % 2 == 1 else (best_cluster[mid - 1] + best_cluster[mid - 1 + 1]) / 2.0
    return (median, k)

# --- Public API (same function names you already import) ---

def parse_crowd_sizes(posts: List[str], min_votes: int = 2) -> Dict[str, Dict[str, Any]]:
    """
    Scan a list of text posts for platform + size mentions.
    Returns:
        {
          "PC":   {"gb": 5.1, "votes": 3},
          "PS5":  {"gb": 6.2, "votes": 4},
          "Xbox": {"gb": 4.8, "votes": 2},
          "Switch":{"gb": 2.9, "votes": 3},
        }
    Only platforms with >= min_votes corroboration are included.
    """
    buckets: Dict[str, List[float]] = {"PC": [], "PS5": [], "Xbox": [], "Switch": []}

    for txt in posts:
        pairs = _extract_platform_sizes_from_text(txt)
        # Deduplicate repeated mentions within the same post
        seen = set()
        for plat, gb in pairs:
            key = (plat, round(gb, 2))
            if key in seen:
                continue
            seen.add(key)
            if plat in buckets:
                buckets[plat].append(gb)

    results: Dict[str, Dict[str, Any]] = {}
    for plat, vals in buckets.items():
        median, votes = _cluster_values(vals)
        if votes >= min_votes:
            results[plat] = {"gb": round(median, 2), "votes": votes}
    return results

def format_size_field(platform_sizes: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """
    Format a Discord-friendly field, in fixed order:
      'PC 5.1 GB (3) • PS5 6.2 GB (4) • Xbox 4.8 GB (2) • Switch 2.9 GB (3)'
    Returns None if no platforms passed the min_votes filter.
    """
    if not platform_sizes:
        return None
    order = ["PC", "PS5", "Xbox", "Switch"]
    parts = []
    for p in order:
        info = platform_sizes.get(p)
        if not info:
            continue
        parts.append(f"{p} {info['gb']:.1f} GB ({info['votes']})")
    return " • ".join(parts) if parts else None

