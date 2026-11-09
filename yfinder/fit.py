"""Deterministic scope-fit labels: does a dataset fit the study's extent, time and resolution?

This is the "scope -> data" check of tri-projection. The language model proposes
datasets; this module, not the model, decides the labels shown to users, using
catalog metadata. Labels: good, partial, poor, unknown.
"""
from __future__ import annotations

import datetime as dt
import re

from .geo import coverage_fraction, region_bbox, valid_bbox

ORDER = {"poor": 0, "partial": 1, "good": 2}

# Finest-to-coarsest reporting units. Checked longest phrase first.
_UNIT_PATTERNS = [
    (2, r"block[ -]?groups?|\bbg\b"),
    (1, r"\bblocks?\b"),
    (3, r"tracts?|zcta|zip"),
    (4, r"towns?|places?|city|cities|municipal|county subdivisions?|school districts?|neighborhoods?"),
    (5, r"county|counties"),
    (6, r"\bstates?\b"),
    (7, r"countr(y|ies)|nation"),
    (0, r"address|parcel|property|properties|building|point|sensor|monitor|photo|panorama|feature|household|individual|person|poi|station|plot|site"),
]
UNIT_NAMES = {0: "address or point", 1: "block", 2: "block group", 3: "tract", 4: "town or place",
              5: "county", 6: "state", 7: "country"}


def unit_rank(text: str | None) -> int | None:
    """Finest reporting unit named in the text, as a rank (0 = point/parcel, 7 = country)."""
    if not text:
        return None
    t = text.lower()
    found = []
    for rank, pat in _UNIT_PATTERNS:
        if re.search(pat, t):
            found.append(rank)
            t = re.sub(pat, " ", t)
    return min(found) if found else None


def _year_frac(s, end: bool = False) -> float | None:
    """Fractional year. Start values map to the first instant; end values to the end of the stated
    period (a year-only end covers the whole year, a month covers the month, a date covers that day)."""
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return float(s) + (1.0 if end else 0.0)
    m = re.match(r"^\s*(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", str(s))
    if not m:
        return None
    y = int(m.group(1))
    if not m.group(2):
        return y + (1.0 if end else 0.0)
    mo = int(m.group(2))
    if not m.group(3):
        return y + (mo if end else mo - 1) / 12
    day = int(m.group(3))
    return y + (mo - 1) / 12 + (day if end else day - 1) / 365


def _fmt_res(m: float) -> str:
    if m >= 1000:
        km = m / 1000
        return f"{km:.0f} km" if km >= 10 else f"{km:.1f} km".replace(".0 km", " km")
    if m >= 1:
        return f"{m:.0f} m"
    return f"{m * 100:.0f} cm"


def dataset_bbox(meta: dict) -> list[float] | None:
    b = meta.get("bbox")
    if b and valid_bbox(b):
        return [float(x) for x in b]
    return region_bbox(meta.get("region") or meta.get("coverage"))


def check_extent(meta: dict, scope: dict) -> dict:
    sb = scope.get("bbox")
    if not sb or not valid_bbox(sb):
        return {"label": "unknown", "reason": "No study area set yet"}
    db = dataset_bbox(meta)
    if not db:
        return {"label": "unknown", "reason": "Coverage not listed"}
    f = coverage_fraction(db, sb)
    where = meta.get("region") or "its area"
    if f >= 0.97:
        return {"label": "good", "reason": "Covers your study area"}
    if f > 0.02:
        return {"label": "partial", "reason": f"Covers about {f * 100:.0f}% of your study area"}
    return {"label": "poor", "reason": f"Does not cover your study area (data: {where})"}


def check_time(meta: dict, scope: dict) -> dict:
    s0, s1 = _year_frac(scope.get("time_start")), _year_frac(scope.get("time_end"), end=True)
    if s0 is None and s1 is None:
        return {"label": "unknown", "reason": "No time window set yet"}
    today = dt.date.today()
    now = today.year + today.timetuple().tm_yday / 365
    if s0 is None:
        s0 = _year_frac(scope.get("time_end"))
    if s1 is None:
        s1 = _year_frac(scope.get("time_start"), end=True)
    d0, d1 = _year_frac(meta.get("t0")), _year_frac(meta.get("t1"), end=True)
    if d0 is None and d1 is None:
        return {"label": "unknown", "reason": f"Dates not listed ({meta.get('time') or 'check the source'})"}
    ongoing = d1 is None
    d0 = d0 if d0 is not None else -1e9
    # An ongoing record (no end date) keeps adding data, so it can serve a window in the near future.
    d1 = d1 if not ongoing else max(now, s1 if s1 <= now + 3 else now)
    span = max(s1 - s0, 1e-6)
    ov = max(0.0, min(s1, d1) - max(s0, d0))
    y_end = int(s1 - 1e-6)
    win = f"{int(s0)}" if y_end == int(s0) else f"{int(s0)} to {y_end}"
    if ov / span >= 0.95:
        if ongoing and s1 > now:
            return {"label": "good", "reason": f"Ongoing record; {win} data arrive as they are collected"}
        return {"label": "good", "reason": f"Covers {win}"}
    if ov > 0:
        lo, hi = max(s0, d0), int(min(s1, d1) - 1e-6)
        part = f"{int(lo)}" if hi == int(lo) else f"{int(lo)} to {hi}"
        return {"label": "partial", "reason": f"Covers {part} of your {win} window"}
    if d1 <= s0:
        return {"label": "poor", "reason": f"Ends in {int(d1 - 1e-6)}, before your {win} window"}
    return {"label": "poor", "reason": f"Starts in {int(d0)}, after your {win} window"}


def check_resolution(meta: dict, scope: dict) -> dict:
    need = scope.get("spatial_resolution_m")
    have = meta.get("res_m")
    s_unit = unit_rank(scope.get("unit_of_analysis"))
    d_unit = unit_rank(meta.get("unit"))
    if need and have:
        need, have = float(need), float(have)
        r = have / need
        if r <= 1.0:
            return {"label": "good", "reason": f"{_fmt_res(have)} detail meets your {_fmt_res(need)} need"}
        if r <= 4.0:
            return {"label": "partial", "reason": f"{_fmt_res(have)} cells are coarser than the {_fmt_res(need)} you need"}
        return {"label": "poor", "reason": f"{_fmt_res(have)} cells are too coarse for {_fmt_res(need)} features"}
    if s_unit is not None and d_unit is not None:
        diff = d_unit - s_unit
        if diff <= 0:
            finer = "" if diff == 0 else f", finer than your {UNIT_NAMES[s_unit]} unit"
            return {"label": "good", "reason": f"Reported by {UNIT_NAMES[d_unit]}{finer}"}
        if diff == 1:
            return {"label": "partial", "reason": f"Reported by {UNIT_NAMES[d_unit]}, one level coarser than {UNIT_NAMES[s_unit]}"}
        return {"label": "poor", "reason": f"Only by {UNIT_NAMES[d_unit]}; too coarse for {UNIT_NAMES[s_unit]} analysis"}
    if need and d_unit is not None and d_unit == 0:
        return {"label": "unknown", "reason": "Point or site records: their spacing, not a cell size, sets the detail"}
    if have and not need:
        return {"label": "unknown", "reason": f"{_fmt_res(have)} data; set a needed resolution to compare"}
    if meta.get("data_type") == "table":
        return {"label": "unknown", "reason": "A table without spatial detail; join it by its place column if it has one"}
    return {"label": "unknown", "reason": "Resolution not listed"}


def check_access(meta: dict) -> dict:
    c = (meta.get("access_class") or "").lower()
    text = meta.get("access") or ""
    if c == "open":
        return {"label": "open", "reason": text or "Open"}
    if c == "yale":
        return {"label": "yale", "reason": text or "Yale access"}
    if c == "paid":
        return {"label": "paid", "reason": text or "Paid license"}
    if c == "request":
        return {"label": "request", "reason": text or "Registration or request needed"}
    return {"label": "unknown", "reason": text or "Check access terms"}


def assess(meta: dict, scope: dict | None) -> dict:
    scope = scope or {}
    parts = {
        "extent": check_extent(meta, scope),
        "time": check_time(meta, scope),
        "resolution": check_resolution(meta, scope),
    }
    known = [p["label"] for p in parts.values() if p["label"] in ORDER]
    overall = min(known, key=ORDER.get) if known else "unknown"
    parts["access"] = check_access(meta)
    parts["overall"] = overall
    return parts


def short(fit: dict) -> str:
    """One-line summary for model context, e.g. 'partial (extent good; time partial: ...)'."""
    bits = []
    for k in ("extent", "time", "resolution"):
        p = fit[k]
        if p["label"] != "good":
            bits.append(f"{k} {p['label']}: {p['reason']}")
    return fit["overall"] + (" (" + "; ".join(bits) + ")" if bits else "")
