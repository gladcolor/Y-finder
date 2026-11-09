"""Small geometry helpers shared by the catalog builder, the fit checker and the tools.

Everything here works on plain lon/lat bounding boxes [west, south, east, north]
so the app does not need GDAL or shapely.
"""
from __future__ import annotations

import math
import re

# Named regions used by curated cards. Boxes are generous on purpose: they decide
# whether a dataset can cover a study area, so a tight box would cause false "no".
REGION_BBOX = {
    "global": [-180.0, -90.0, 180.0, 90.0],
    "us": [-180.0, 17.5, -64.5, 72.0],  # all 50 states, Puerto Rico and the Virgin Islands (a 50-state study box fits inside)
    "us / north america": [-170.0, 14.0, -50.0, 72.0],
    "north america": [-170.0, 14.0, -50.0, 72.0],
    "conus": [-125.0, 24.4, -66.9, 49.4],
    "us coasts": [-180.0, 17.5, -64.5, 72.0],
    "americas": [-170.0, -56.0, -30.0, 72.0],
    "connecticut": [-73.73, 40.95, -71.78, 42.06],
    "new england": [-73.73, 40.95, -66.9, 47.46],
}


def region_bbox(region: str | None) -> list[float] | None:
    if not region:
        return None
    return REGION_BBOX.get(region.strip().lower())


def valid_bbox(b) -> bool:
    try:
        w, s, e, n = (float(x) for x in b)
    except (TypeError, ValueError):
        return False
    return -180 <= w < e <= 180 and -90 <= s < n <= 90


def clamp_bbox(b) -> list[float]:
    w, s, e, n = (float(x) for x in b)
    return [max(-180.0, w), max(-90.0, s), min(180.0, e), min(90.0, n)]


def bbox_area(b) -> float:
    """Approximate area in square degrees scaled by cos(latitude); fine for ratios."""
    w, s, e, n = b
    if e <= w or n <= s:
        return 0.0
    lat = math.radians((s + n) / 2)
    return (e - w) * (n - s) * max(0.05, math.cos(lat))


def intersect(a, b) -> list[float] | None:
    w, s = max(a[0], b[0]), max(a[1], b[1])
    e, n = min(a[2], b[2]), min(a[3], b[3])
    if e <= w or n <= s:
        return None
    return [w, s, e, n]


def coverage_fraction(dataset_bbox, study_bbox) -> float:
    """Share of the study box that falls inside the dataset box (0..1)."""
    inter = intersect(dataset_bbox, study_bbox)
    if not inter:
        return 0.0
    area = bbox_area(study_bbox)
    return 1.0 if area == 0 else min(1.0, bbox_area(inter) / area)


def bbox_size_km(b) -> tuple[float, float]:
    w, s, e, n = b
    lat = math.radians((s + n) / 2)
    return (e - w) * 111.32 * math.cos(lat), (n - s) * 110.57


def geojson_bbox(geom_or_fc) -> list[float] | None:
    xs: list[float] = []
    ys: list[float] = []

    def walk(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            xs.append(float(c[0]))
            ys.append(float(c[1]))
        elif isinstance(c, (list, tuple)):
            for x in c:
                walk(x)

    def geom(g):
        if not g:
            return
        if g.get("type") == "GeometryCollection":
            for x in g.get("geometries", []):
                geom(x)
        else:
            walk(g.get("coordinates"))

    if geom_or_fc.get("type") == "FeatureCollection":
        for f in geom_or_fc.get("features", []):
            geom(f.get("geometry"))
    elif geom_or_fc.get("type") == "Feature":
        geom(geom_or_fc.get("geometry"))
    else:
        geom(geom_or_fc)
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


_RES_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:-|\s)?\s*(km|kilometers?|kilometres?|m|meters?|metres?|arc[- ]?seconds?|arcsec|arc[- ]?minutes?|degrees?|deg|°)(?![a-z])",
    re.I,
)


def parse_resolution_m(text: str | None) -> float | None:
    """Pull the finest plausible spatial resolution (meters) out of free text.

    Used only for catalog entries whose metadata lack a gsd. Returns None when no
    number with a distance unit appears. Values are converted at the equator.
    """
    if not text:
        return None
    best = None
    for m in _RES_RE.finditer(text[:900]):
        v = float(m.group(1))
        u = m.group(2).lower()
        if u.startswith("k"):
            meters = v * 1000
        elif u.startswith("arc") and "min" in u:
            meters = v * 1852
        elif u.startswith("arc"):
            meters = v * 30.87
        elif u.startswith("deg") or u == "°":
            if v > 5:  # "30 degrees" is an angle, not a grid
                continue
            meters = v * 111_320
        else:
            meters = v
        if not (0.01 <= meters <= 200_000):
            continue
        best = meters if best is None else min(best, meters)
    return best
