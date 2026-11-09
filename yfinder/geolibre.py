"""Layers collected in GeoLibre (https://geolibre.app, MIT, opengeos/GeoLibre).

Two kinds of catalog items come from GeoLibre and can be drawn on Y-finder's map:

* gallery maps (ids "gl:<slug>"): GeoLibre's open data gallery and starter projects. Each project file
  (assets.geolibre.app/projects/<slug>.geolibre.json) holds GeoJSON snapshots and raster tile layers.
* web services (ids "gls:<name>"): GeoLibre's built-in WMS / WMTS / XYZ / ArcGIS presets.

The catalog build (scripts/build_catalog.py) uses summarize(); the map tool uses fetch_project(), map_layers()
and service_layer(). Project files are only fetched from GeoLibre's asset host, by slug, never from a URL a
model or a browser supplies.
"""
from __future__ import annotations

import json
import math
import re
import threading
from pathlib import Path
from urllib.parse import urlencode

import requests

from .config import RUNTIME_DIR
from . import vector
from .geo import geojson_bbox, valid_bbox

ASSET_HOST = "https://assets.geolibre.app"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,90}$")
CACHE_DIR = RUNTIME_DIR / "geolibre"
MAX_PROJECT_BYTES = 40 * 1024 * 1024
KEEP_PROJECTS = 40          # newest project files kept in the server cache
UA = {"User-Agent": "Y-finder/0.1 (YCGS data guide; +https://app.researcharchitect.ai/y-finder/)"}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def project_url(slug: str) -> str:
    return f"{ASSET_HOST}/projects/{slug}.geolibre.json"


def viewer_url(slug: str) -> str:
    """Open the original, styled project in the GeoLibre web app."""
    return "https://web.geolibre.app/?" + urlencode({"url": project_url(slug)})


# ---------------------------------------------------------------------------- reading projects
def _years(text: str) -> tuple[int | None, int | None]:
    ys = [int(y) for y in re.findall(r"(?<!\d)(1[6-9]\d\d|20[0-4]\d)(?!\d)", text or "")]
    return (min(ys), max(ys)) if ys else (None, None)


def _raster_tiles(layer: dict) -> list[str]:
    src = layer.get("source") if isinstance(layer.get("source"), dict) else {}
    tiles = src.get("tiles") or ([src["url"]] if isinstance(src.get("url"), str) and "{z}" in src["url"] else [])
    return [t for t in tiles if isinstance(t, str) and t.startswith("https://")
            and ("{z}" in t or "{bbox-epsg-3857}" in t)][:4]


_BASEMAP_TILES = re.compile(r"World_Imagery|World_Street_Map|World_Topo_Map|tile\.openstreetmap\.org|basemaps\.cartocdn|"
                            r"tiles\.openfreemap|api\.protomaps", re.I)


def _is_basemap(layer: dict) -> bool:
    """Background maps the user added in GeoLibre; Y-finder has its own streets and satellite basemaps."""
    md = layer.get("metadata") if isinstance(layer.get("metadata"), dict) else {}
    if md.get("basemapId") or md.get("externalNativeLayer") or str(layer.get("id", "")).startswith("basemap-"):
        return True
    return any(_BASEMAP_TILES.search(t) for t in _raster_tiles(layer))


def summarize(project: dict) -> dict:
    """What the catalog needs from a project file: extent, layers, fields, years, description."""
    layers, fields, boxes = [], [], []
    for L in project.get("layers") or []:
        if not isinstance(L, dict) or _is_basemap(L):
            continue
        t = L.get("type")
        name = str(L.get("name") or "")[:120]
        if t == "geojson" and isinstance(L.get("geojson"), dict):
            feats = L["geojson"].get("features") or []
            b = geojson_bbox(L["geojson"]) if feats else None
            if b:
                boxes.append(b)
            props = feats[0].get("properties") if feats and isinstance(feats[0].get("properties"), dict) else {}
            fields += [k for k in props if isinstance(k, str)][:20]
            layers.append({"name": name, "type": "geojson", "n": len(feats)})
        elif t in ("xyz", "raster") and _raster_tiles(L):
            src = L.get("source") or {}
            if valid_bbox(src.get("bounds")):
                boxes.append(src["bounds"])
            layers.append({"name": name, "type": "tiles"})
        elif t:
            layers.append({"name": name, "type": str(t)[:20], "map": False})
    bbox, bbox_from = None, "layers"
    if not boxes:  # imagery-only maps store no bounds: use the saved map view (about a 1280 x 800 px window)
        mv = project.get("mapView") or {}
        c, z = mv.get("center"), mv.get("zoom")
        if isinstance(c, list) and len(c) == 2 and isinstance(z, (int, float)):
            if z < 2.2:
                boxes.append([-180.0, -85.0, 180.0, 85.0])
            else:
                w = 360 * 1280 / (256 * 2 ** z) / 2
                h = w * 800 / 1280 * max(0.2, math.cos(math.radians(max(-80, min(80, c[1])))))
                boxes.append([-180.0, -85.0, 180.0, 85.0] if w > 75 else [c[0] - w, c[1] - h, c[0] + w, c[1] + h])
            bbox_from = "map view"
    if boxes:
        bbox = [round(min(b[0] for b in boxes), 4), round(min(b[1] for b in boxes), 4),
                round(max(b[2] for b in boxes), 4), round(max(b[3] for b in boxes), 4)]
        bbox = [max(-180.0, bbox[0]), max(-90.0, bbox[1]), min(180.0, bbox[2]), min(90.0, bbox[3])]
    desc = str((project.get("metadata") or {}).get("description") or "")
    return {"name": project.get("name") or "", "description": desc, "bbox": bbox if bbox and valid_bbox(bbox) else None,
            "bbox_from": bbox_from,
            "layers": layers, "fields": list(dict.fromkeys(fields))[:40],
            "mappable": sum(1 for l in layers if l.get("map", True))}


def fetch_project(slug: str, cache_dir: Path | None = None, timeout: int = 90) -> dict:
    """Download (once) and parse a GeoLibre project file by slug."""
    if not SLUG_RE.fullmatch(slug or ""):
        raise ValueError("Not a GeoLibre project id")
    cache = Path(cache_dir or CACHE_DIR)
    path = cache / f"{slug}.json"
    with _locks_guard:
        lock = _locks.setdefault(slug, threading.Lock())
    with lock:
        if not path.exists():
            r = requests.get(project_url(slug), headers=UA, timeout=timeout, stream=True)
            if r.status_code != 200:
                raise ValueError(f"GeoLibre returned {r.status_code} for this map")
            buf, n = [], 0
            for chunk in r.iter_content(1 << 16):
                n += len(chunk)
                if n > MAX_PROJECT_BYTES:
                    raise ValueError("This GeoLibre map is too large to load here; open it in GeoLibre instead")
                buf.append(chunk)
            cache.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            tmp.write_bytes(b"".join(buf))
            tmp.replace(path)
            files = sorted(cache.glob("*.json"), key=lambda p: p.stat().st_mtime)
            for p in files[:-KEEP_PROJECTS]:
                p.unlink(missing_ok=True)
        else:
            path.touch()
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------- styling
_HEX = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def paint_hint(style: dict | None) -> dict | None:
    """GeoLibre's colors for a vector layer, in a small validated form the browser turns into MapLibre paint.

    Graduated stops are class lower bounds (a MapLibre `step`); categorized stops are exact values (a `match`),
    as in GeoLibre's vectorColorExpression."""
    if not isinstance(style, dict):
        return None
    h: dict = {}
    for k, out in (("fillColor", "fill"), ("strokeColor", "stroke")):
        if isinstance(style.get(k), str) and _HEX.match(style[k]):
            h[out] = style[k]
    for k, out, lo, hi in (("fillOpacity", "opacity", 0, 1), ("strokeWidth", "width", 0, 8), ("circleRadius", "radius", 1, 20)):
        v = style.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            h[out] = max(lo, min(hi, float(v)))
    mode, prop = style.get("vectorStyleMode"), style.get("vectorStyleProperty")
    if mode in ("graduated", "categorized") and isinstance(prop, str) and 0 < len(prop) <= 80:
        stops = []
        for st in (style.get("vectorStyleStops") or [])[:40]:
            if not (isinstance(st, dict) and isinstance(st.get("color"), str) and _HEX.match(st["color"])):
                continue
            v = st.get("value")
            if mode == "graduated":
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
            elif not isinstance(v, (str, int, float)) or isinstance(v, bool):
                continue
            stops.append([v if mode == "graduated" else str(v)[:80], st["color"]])
        if mode == "graduated":
            stops.sort(key=lambda x: x[0])
            stops = [x for i, x in enumerate(stops) if i == 0 or x[0] > stops[i - 1][0]]
        if len(stops) >= (2 if mode == "graduated" else 1):
            h.update(mode=mode, property=prop, stops=stops)
    return h or None


# ---------------------------------------------------------------------------- layers for the map
def map_layers(project: dict, study_bbox=None, study_year: int | None = None) -> list[dict]:
    """Drawable layers of a project: {"kind": "geojson", fc, title, paint, note} or {"kind": "raster", tiles, ...}.

    Small vector layers are drawn whole. Large ones are cut to the features touching the study area; what is
    still too large becomes an even sample. Time series keep one year. Coordinates are rounded (about 1 m,
    coarser for heavy layers)."""
    out = []
    has_study = bool(study_bbox and valid_bbox(study_bbox))
    for L in project.get("layers") or []:
        if not isinstance(L, dict) or _is_basemap(L):
            continue
        name = str(L.get("name") or "Layer")[:100]
        hidden = L.get("visible", True) is False
        if L.get("type") == "geojson" and isinstance(L.get("geojson"), dict):
            feats = [f for f in (L["geojson"].get("features") or []) if isinstance(f, dict) and isinstance(f.get("geometry"), dict)]
            feats, year_note = vector.one_year(feats, study_year)
            fc, note, total = vector.slim(feats, study_bbox if has_study else None,
                                          len(json.dumps(L["geojson"], separators=(",", ":"))) if not year_note else None)
            note = "; ".join(filter(None, [year_note, note]))
            if fc is None:
                out.append({"kind": "empty", "title": name, "note": note})
                continue
            out.append({"kind": "geojson", "title": name, "fc": fc, "paint": paint_hint(L.get("style")),
                        "hidden": hidden, "note": note or None})
        elif L.get("type") in ("xyz", "raster") and (tiles := _raster_tiles(L)):
            src = L.get("source") or {}
            out.append({"kind": "raster", "title": name, "tiles": tiles,
                        "bbox": src.get("bounds") if valid_bbox(src.get("bounds")) else None,
                        "minzoom": int(src.get("minzoom") or 0), "maxzoom": int(src.get("maxzoom") or 18),
                        "attribution": str(src.get("attribution") or "")[:200], "hidden": hidden})
        elif L.get("type"):
            out.append({"kind": "skipped", "title": name, "note": f"{L.get('type')} layers open only in GeoLibre"})
    return out


def service_layer(svc: dict) -> dict:
    """Tile template for a GeoLibre web-service preset (WMS, WMTS or XYZ)."""
    kind = svc.get("kind")
    if kind in ("xyz", "wmts"):
        tiles = [svc["tiles"]]
    elif kind == "wms":
        v = svc.get("version") or "1.1.1"
        q = {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": v, "LAYERS": svc["layers"], "STYLES": "",
             "FORMAT": svc.get("format") or "image/png", "TRANSPARENT": "true", "WIDTH": 256, "HEIGHT": 256,
             ("CRS" if v.startswith("1.3") else "SRS"): "EPSG:3857"}
        tiles = [svc["endpoint"] + "?" + urlencode(q) + "&BBOX={bbox-epsg-3857}"]
    else:
        raise ValueError(f"{kind} services are not drawn as tiles")
    if not all(t.startswith("https://") for t in tiles):
        raise ValueError("Only https services are drawn")
    return {"tiles": tiles, "minzoom": int(svc.get("minzoom") or 0), "maxzoom": int(svc.get("maxzoom") or 18)}

