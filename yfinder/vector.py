"""Make a vector layer light enough for the browser map: cut to the study area, keep one year of a time series,
sample evenly, round coordinates. Used for GeoLibre gallery layers and for downloaded files."""
from __future__ import annotations

import json
import re

from .geo import geojson_bbox, valid_bbox

SMALL_LAYER = 3000          # layers up to this many features and SMALL_BYTES are drawn whole; larger ones are cut
SMALL_BYTES = 2_500_000     # to the study area
MAX_FEATURES = 20000
TARGET_BYTES = 4_000_000    # after cutting, coordinates are rounded more coarsely until a layer fits


def _round(c, nd):
    if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
        return [round(float(x), nd) for x in c[:3]]
    return [_round(x, nd) for x in c] if isinstance(c, (list, tuple)) else c


def _intersects(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


MAX_PROPS = 16  # heavy layers keep this many attributes per feature (popups stay useful, the file stays light)
NAME_LIKE = re.compile(r"(^|_)(name|title|label|town|city|county|state|site|station|facility|hospital|place|neighbou?rhood)(_|$)|name$", re.I)


def _feature(f: dict, nd: int, max_props: int | None = None, keep=None) -> dict:
    g = f["geometry"]
    geom = g if g.get("type") == "GeometryCollection" else {"type": g.get("type"), "coordinates": _round(g.get("coordinates"), nd)}
    props = {k: v for k, v in (f.get("properties") or {}).items() if isinstance(v, (str, int, float, bool)) or v is None}
    if max_props and len(props) > max_props:  # names first (hospital_name, city), then columns that name the topic (fentanyl)
        keys = sorted(props, key=lambda k: 0 if NAME_LIKE.search(k) else 1 if keep and any(w in k.lower() for w in keep) else 2)
        props = {k: props[k] for k in keys[:max_props] if props[k] not in (None, "")}
    return {"type": "Feature", "properties": props, "geometry": geom}


def one_year(feats: list[dict], want: int | None):
    """Time-series layers repeat each place once per year (GeoLibre animates them). Keep one year for the preview."""
    if len(feats) < 200:
        return feats, None
    for key in ("year", "Year", "YEAR"):
        ys = [f["properties"].get(key) for f in feats if isinstance(f.get("properties"), dict)]
        vals = sorted({y for y in ys if isinstance(y, int) and not isinstance(y, bool)})
        if not (len(vals) >= 3 and len(ys) >= 0.9 * len(feats)):
            continue
        # a time series repeats the same places (countries, cities); events (earthquakes) each have their own place
        places = {json.dumps(_round(f["geometry"].get("coordinates"), 2))[:400] for f in feats}
        per_year = [ys.count(v) for v in vals]
        if len(places) <= 0.5 * len(feats) and max(per_year) <= 3 * len(feats) / len(vals):
            pick = want if want in vals else vals[-1]
            return [f for f in feats if (f.get("properties") or {}).get(key) == pick], f"{pick} only ({vals[0]} to {vals[-1]} in GeoLibre)"
    return feats, None


def slim(feats: list[dict], study_bbox=None, raw_bytes: int | None = None, keep=None) -> tuple[dict | None, str | None, int]:
    """Returns (FeatureCollection or None when nothing is left, note, total features before cutting).

    Small layers are kept whole. Large ones are cut to the features touching the study area; what is still too
    large becomes an even sample. Coordinates are rounded (about 1 m, coarser for heavy layers)."""
    feats = [f for f in feats if isinstance(f, dict) and isinstance(f.get("geometry"), dict)]
    total, note = len(feats), None
    if raw_bytes is None:
        raw_bytes = len(json.dumps(feats, separators=(",", ":"))) if total <= SMALL_LAYER else SMALL_BYTES + 1
    if (total > SMALL_LAYER or raw_bytes > SMALL_BYTES) and study_bbox and valid_bbox(study_bbox):
        feats = [f for f in feats if (fb := geojson_bbox(f["geometry"])) and _intersects(fb, study_bbox)]
        note = f"{len(feats)} of {total} features touch the study area"
    kept = len(feats)
    if not feats:
        return None, (f"none of its {total} features touch the study area" if total else "no features"), total
    if len(feats) > MAX_FEATURES:  # an even sample keeps the whole pattern visible
        feats = feats[:: -(-len(feats) // MAX_FEATURES)]
    # Precision drops only as far as it stays honest: about 10 m for points (rounding further would snap them onto a
    # visible grid and move them hundreds of meters), about 100 m for lines and areas. Past that, an even sample.
    points = all(f["geometry"].get("type") in ("Point", "MultiPoint") for f in feats[:200])
    heavy = kept > SMALL_LAYER
    for nd in ((5, 4) if points else (5, 4, 3)):
        fc = {"type": "FeatureCollection", "features": [_feature(f, nd, MAX_PROPS if heavy else None, keep) for f in feats]}
        size = len(json.dumps(fc, separators=(",", ":")))
        if size <= TARGET_BYTES:
            break
    else:  # still too heavy: an even sample that fits
        fc["features"] = fc["features"][:: -(-size // int(TARGET_BYTES * 0.95))]
    if len(fc["features"]) < kept:
        note = "; ".join(filter(None, [note, f"a sample of {len(fc['features'])} of {kept} features"]))
    return fc, note, total
