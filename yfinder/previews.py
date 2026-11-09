"""Map previews for the datasets Y-finder recommends, built in the background after recommend_data.

For each pick the first rule that applies wins:
  * an online map service is drawn directly: GeoLibre services and gallery maps, Planetary Computer imagery
    (best recent scene over the study area), ArcGIS MapServer/ImageServer tiles or FeatureServer features
    (also ArcGIS Online items from searches), WMS or XYZ services named on curated cards, and ACS tracts from
    Esri's Living Atlas service
  * open data without a service: a small slice for the study area is downloaded (raw files up to 100 MB),
    turned into a layer and kept for this session only: NOAA nClimGrid county averages, CDC PLACES tracts,
    Census boundaries, direct file links
  * otherwise the card says why there is no preview (licensed, needs an account, registration, too large)
Core picks are shown; supporting ones are added hidden, so the map stays readable.
"""
from __future__ import annotations

import concurrent.futures as cf
import csv
import datetime as dt
import io
import os
import re
import threading
import time

import requests

from . import catalog as catalog_mod
from . import downloads, geolibre, session, sources, vector
from .geo import bbox_size_km, coverage_fraction, geojson_bbox
from .session import save_layer

MAX_PICKS = 10
MAX_RAW_BYTES = 100 * 1024 * 1024     # "download a small data volume (100 MB)" for sources without a service
_slots = threading.BoundedSemaphore(4)  # previews fetching at once on the whole server
UA = {"User-Agent": "Y-finder/0.1 (YCGS data guide; +https://app.researcharchitect.ai/y-finder/)"}
TIGER2020 = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Census2020/MapServer"
ESRI_ACS_INCOME = ("https://P3ePLMYs2RVChkJx.svcs.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
                   "ACS_Median_Household_Income_View_Boundaries/FeatureServer")
NCLIMGRID = "https://www.ncei.noaa.gov/data/nclimgrid-daily/access/averages"
PLACES_TRACTS = "https://data.cdc.gov/resource/cwsq-ngmh.json"   # PLACES census tract data, 2025 release
FILE_EXT = (".geojson", ".json", ".csv", ".zip", ".kml", ".kmz")

STATE_FIPS = {"AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10", "DC": "11",
              "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
              "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30",
              "NE": "31", "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
              "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49",
              "VT": "50", "VA": "51", "WA": "53", "WV": "54", "WI": "55", "WY": "56"}
RAMPS = {"reds": ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
         "greens": ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
         "purples": ["#f2f0f7", "#cbc9e2", "#9e9ac8", "#756bb1", "#54278f"],
         "blues": ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"]}
PLACES_MEASURES = [  # words in the question or graph -> PLACES measure id
    (("asthma",), "CASTHMA"), (("copd", "lung"), "COPD"), (("diabetes",), "DIABETES"), (("obesity", "obese"), "OBESITY"),
    (("heart", "coronary", "cardio"), "CHD"), (("stroke",), "STROKE"), (("depress",), "DEPRESSION"),
    (("mental",), "MHLTH"), (("inactiv", "exercise", "walk", "physical activity"), "LPA"), (("smok",), "CSMOKING"),
    (("blood pressure", "hypertension"), "BPHIGH"), (("cancer",), "CANCER"), (("kidney",), "KIDNEY"),
    (("sleep",), "SLEEP"), (("insurance", "uninsured"), "ACCESS2"), (("disabilit",), "DISABILITY")]


def enabled() -> bool:
    return os.environ.get("YFINDER_PREVIEWS", "1") != "0"


# ---------------------------------------------------------------------------- helpers
def _stops(values: list[float], ramp: str) -> list[list]:
    """Quantile class lower bounds (GeoLibre's graduated style): [[value, color], ...]."""
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return []
    colors = RAMPS[ramp]
    k = len(colors)
    out, last = [], None
    for i in range(k):
        v = round(vals[min(len(vals) - 1, int(i * len(vals) / k))], 2)
        if last is None or v > last:
            out.append([v, colors[i]])
            last = v
    return out


def _paint(prop: str, values: list[float], ramp: str) -> dict | None:
    st = _stops(values, ramp)
    if len(st) < 2:
        return None
    return {"mode": "graduated", "property": prop, "stops": st, "fill": st[-1][1], "stroke": "#ffffff", "opacity": 0.75, "width": 0.5}


def _vector_layer(s, pick, fc_feats: list[dict], title: str, source: str, paint: dict | None = None,
                  label: str = "") -> tuple[dict | None, str | None]:
    fc, note, _total = vector.slim(fc_feats, s.scope.get("bbox"))
    if fc is None:
        return None, note
    layer = save_layer(fc, title[:100], source, style="preview")
    if paint:
        layer["paint"] = paint
    downloads.track(s, [layer], label or title)
    return layer, note


def _retry(fn, tries: int = 2):
    """Public boundary services time out now and then; one more try usually works."""
    for i in range(tries):
        try:
            return fn()
        except (requests.RequestException, ValueError):
            if i == tries - 1:
                raise
            time.sleep(1.5)


ALREADY = {"status": "ready", "kind": "service", "layers": [], "note": "Already on the map"}


def _on_map(s, test) -> bool:
    """Whether the agent (or an earlier preview) already drew this layer, so previews do not add a copy."""
    return any(test(l) for l in list(s.layers))


def short_title(t: str) -> str:
    """'NAIP: National Agriculture Imagery Program' -> 'NAIP'; long names are cut at a word."""
    t = (t or "").strip()
    head = t.split(":")[0].strip()
    if ":" in t and 2 <= len(head) <= 24:
        return head
    return t if len(t) <= 40 else t[:40].rsplit(" ", 1)[0] + "..."


def _study_year(scope: dict) -> int | None:
    m = re.match(r"\d{4}", str((scope or {}).get("time_end") or ""))
    return int(m.group()) if m else None


def _scope_text(s) -> str:
    g = s.graph or {}
    return " ".join([g.get("question") or ""] + [n.get("label", "") for n in g.get("nodes", [])]).lower()


# ---------------------------------------------------------------------------- online services
def _geolibre(s, pick, meta):
    from . import agent
    if not (meta.get("geolibre") or {}).get("mappable"):
        return None
    _res, events, label = agent.h_map_catalog_layer(s, {"ref": pick["ref"]})
    layers = [e["layer"] for e in events if e["type"] == "layer"]
    downloads.track(s, [l for l in layers if l.get("kind") == "geojson"], label)
    return {"status": "ready", "kind": "slice", "layers": layers, "note": f"{label} (a GeoLibre gallery snapshot)"}


def _service(s, pick, meta):
    """A service named on the card (curated or GeoLibre): tiles, or ArcGIS features cut to the study area."""
    sv = meta.get("service") or {}
    if not sv:
        return None
    if sv.get("kind") == "arcgis":
        return _arcgis_url(s, pick, sv["url"], meta.get("title") or pick.get("title"))
    t = geolibre.service_layer(sv)
    layer = {"id": "pv-" + re.sub(r"[^a-z0-9]", "", pick["ref"].lower())[-40:], "kind": "raster",
             "title": (meta.get("title") or pick.get("title") or "Service")[:100], "source": meta.get("provider") or "",
             "tiles": t["tiles"], "bbox": meta.get("bbox"), "minzoom": t["minzoom"], "maxzoom": t["maxzoom"]}
    return {"status": "ready", "kind": "service", "layers": [layer], "note": "Live map service"}


_REST = re.compile(r"^(https://[^?#]+/rest/services/[^?#]+?/(FeatureServer|MapServer|ImageServer))(?:/(\d+))?/?(?:[?#].*)?$", re.I)
_ITEM = re.compile(r"^https://(?:www\.)?arcgis\.com/home/item\.html\?id=([0-9a-f]{32})", re.I)


def _arcgis_url(s, pick, url, title):
    m = _REST.match(url or "")
    if not m or not sources.safe_url(m.group(1)):
        return None
    root, kind, idx = m.group(1), m.group(2).lower(), m.group(3)
    if kind == "featureserver" or (kind == "mapserver" and idx is not None):
        if not s.scope.get("bbox"):
            return {"status": "none", "note": "Set a study area to preview this feature layer"}
        url = f"{root}/{idx or 0}"
        if _on_map(s, lambda l: (l.get("service_url") or "").lower() == url.lower()):
            return dict(ALREADY, layers=[])  # a fresh copy: results are updated later
        fc = sources.arcgis_query(url, s.scope["bbox"], max_features=2000)
        feats = fc.get("features", [])
        fb = geojson_bbox({"type": "FeatureCollection", "features": feats}) if feats else None
        if fb and _on_map(s, lambda l: str(l.get("source", "")).startswith("ArcGIS service") and l.get("n") == len(feats)
                          and l.get("bbox") and all(abs(x - y) < 1e-3 for x, y in zip(l["bbox"], fb))):
            return dict(ALREADY, layers=[])  # same features already drawn (an older layer without its service URL)
        layer, note = _vector_layer(s, pick, feats, title or "ArcGIS layer", "ArcGIS service (features in the study area)")
        if not layer:
            return {"status": "none", "note": note or "No features in the study area"}
        layer["service_url"] = url
        return {"status": "ready", "kind": "service", "layers": [layer], "note": note or f"{layer['n']} features in the study area"}
    t = geolibre.service_layer({"kind": "arcgis-map" if kind == "mapserver" else "arcgis-image", "url": root})
    layer = {"id": "pv-" + re.sub(r"[^a-z0-9]", "", root.lower())[-40:], "kind": "raster", "title": (title or "ArcGIS map")[:100],
             "source": "ArcGIS service", "tiles": t["tiles"], "bbox": None, "minzoom": 0, "maxzoom": 19}
    return {"status": "ready", "kind": "service", "layers": [layer], "note": "Live map service"}


def _arcgis(s, pick, meta):
    urls = [pick.get("ref"), meta.get("service_url"), meta.get("url"), pick.get("url")]
    for u in urls:
        u = str(u or "")
        mi = _ITEM.match(u)
        if mi:  # an ArcGIS Online item page from a search: find its service
            try:
                it = requests.get(f"https://www.arcgis.com/sharing/rest/content/items/{mi.group(1)}?f=json", headers=UA, timeout=20).json()
            except (requests.RequestException, ValueError):
                continue
            u = it.get("url") or ""
            if it.get("type") == "Feature Service" and re.search(r"/FeatureServer/?$", u):
                u = u.rstrip("/") + "/0"
        if _REST.match(u):
            return _arcgis_url(s, pick, u, meta.get("title") or pick.get("title"))
    return None


def _stac(s, pick, meta):
    from . import agent
    coll = (meta.get("stac") or {}).get("collection")
    if not coll:
        return None
    sb = s.scope.get("bbox")
    if not sb:
        return {"status": "none", "note": "Set a study area to preview imagery"}
    window = agent._scope_datetime(s.scope)
    items = []
    # clear scenes in the window first; collections without cloud cover (NAIP, lidar) return nothing with
    # that filter, so drop it; finally the latest scenes whatever their date
    for when, cloud in ((window, 30), (window, None), (None, None)):
        items = [it for it in sources.stac_search(coll, sb, when, cloud, 12) if it.get("tilejson")]
        if items:
            break
    if not items:
        return {"status": "none", "note": "No ready-made map rendering for this collection; use the source"}
    for it in items:
        it["covers"] = coverage_fraction(it["bbox"], sb) if it.get("bbox") else 0
    it = sorted(items, key=lambda x: (-round(x["covers"], 1), x.get("cloud_cover") or 0))[0]
    date = (it.get("datetime") or "")[:10]
    # small tiles (NAIP quarter quads): add the other scenes from the same day that cover the study area
    same = [it] + sorted([x for x in items if x is not it and (x.get("datetime") or "")[:10] == date and x["covers"] > 0],
                         key=lambda x: -x["covers"])[:4]
    parts = []
    for sc in same:
        t = sources.stac_tiles(sc["tilejson"])
        if t and t.get("tiles"):
            parts.append({"tiles": t["tiles"], "bbox": t.get("bounds") or sc.get("bbox"), "minzoom": t["minzoom"], "maxzoom": t["maxzoom"]})
    if not parts:
        return {"status": "none", "note": "The imagery tiler did not answer"}
    union = [min(x["bbox"][0] for x in same), min(x["bbox"][1] for x in same), max(x["bbox"][2] for x in same), max(x["bbox"][3] for x in same)]
    pct = round(100 * min(1.0, coverage_fraction(union, sb)))
    n = len(parts)
    # one map entry per date: same-day scenes are parts of it, and the date stays visible in the Layers panel
    layer = {"id": "pv-stac-" + re.sub(r"[^A-Za-z0-9]", "", it["id"])[-40:], "kind": "raster",
             "title": f"{short_title(meta.get('title') or coll)} · {date}" + (f" · {n} scenes" if n > 1 else ""),
             "date": date, "source": "Microsoft Planetary Computer", "tiles": parts[0]["tiles"], "bbox": parts[0]["bbox"],
             "minzoom": parts[0]["minzoom"], "maxzoom": parts[0]["maxzoom"], "parts": parts[1:]}
    return {"status": "ready", "kind": "service", "layers": [layer],
            "note": f"Imagery of {date}" + (f", {n} scenes" if n > 1 else "") + f", about {pct}% of the study area"}


def _acs(s, pick, meta):
    """ACS median household income by tract (county for big areas) from Esri's keyless Living Atlas service."""
    sb = s.scope.get("bbox")
    if not sb:
        return {"status": "none", "note": "Set a study area to preview ACS values"}
    w, h = bbox_size_km(sb)
    lid, unit = (2, "tract") if w * h <= 40000 else (1, "county")
    fc = sources.arcgis_query(f"{ESRI_ACS_INCOME}/{lid}", sb, max_features=3000, simplify_deg=max(0.00005, min(0.003, (sb[2] - sb[0]) / 4000)))
    feats = []
    for f in fc.get("features", []):
        p = f.get("properties") or {}
        v = p.get("B19049_001E")
        feats.append({"type": "Feature", "geometry": f.get("geometry"),
                      "properties": {"GEOID": p.get("GEOID"), "name": p.get("NAME"), "median_household_income": v if isinstance(v, (int, float)) and v >= 0 else None,
                                     "moe": p.get("B19049_001M")}})
    vals = [f["properties"]["median_household_income"] for f in feats if f["properties"]["median_household_income"] is not None]
    layer, note = _vector_layer(s, pick, feats, f"ACS median household income by {unit}", "ACS 5-year, latest (Esri Living Atlas)",
                                _paint("median_household_income", vals, "greens"), "ACS preview")
    if not layer:
        return {"status": "none", "note": note or "No areas found"}
    rows = [["GEOID", "name", "median_household_income", "moe"]] + [[f["properties"][k] for k in ("GEOID", "name", "median_household_income", "moe")] for f in feats]
    return {"status": "ready", "kind": "service", "layers": [layer], "csv": _csv(s, pick, rows),
            "note": f"Median household income for {len(feats)} {unit}s (latest 5-year ACS)"}


def _tiger(s, pick, meta):
    sb = s.scope.get("bbox")
    if not sb:
        return {"status": "none", "note": "Set a study area to preview boundaries"}
    unit = (s.scope.get("unit_of_analysis") or "").lower()
    w, h = bbox_size_km(sb)
    level = ("block_group" if "block" in unit else "tract" if "tract" in unit else "town" if "town" in unit
             else "county" if "county" in unit or w * h > 40000 else "tract")
    plural = {"county": "counties", "block_group": "block groups"}.get(level, level + "s")
    agent_name = {"tract": "Census tracts", "block_group": "Census block groups", "town": "Towns (county subdivisions)",
                  "county": "Counties"}.get(level)  # names map_census_boundaries uses
    if _on_map(s, lambda l: l.get("source") == "U.S. Census Bureau TIGERweb" and l.get("title") in (agent_name, f"Census {plural}")):
        return dict(ALREADY, layers=[])  # a fresh copy: results are updated later
    fc = _retry(lambda: sources.census_boundaries(level, sb))
    layer, note = _vector_layer(s, pick, fc.get("features", []), f"Census {plural}", "U.S. Census Bureau TIGERweb")
    if not layer:
        return {"status": "none", "note": note or "No boundaries found"}
    return {"status": "ready", "kind": "service", "layers": [layer], "note": f"{layer['n']} {plural}"}


# ---------------------------------------------------------------------------- small downloads (no service)
def _csv(s, pick, rows: list[list]) -> str | None:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    name = re.sub(r"[^a-z0-9]+", "-", pick["ref"].lower()).strip("-")[:50] + ".csv"
    try:
        return downloads.save_sample(s, name, buf.getvalue())
    except downloads.DownloadError:
        return None


def _months(scope: dict) -> list[tuple[int, int]]:
    """Months to try for nClimGrid. A window of a year or more previews July (the summer peak) of its last year;
    a shorter window previews its last month; a window reaching today starts from the latest month."""
    today = dt.date.today()
    y = _study_year(scope)
    a = re.match(r"(\d{4})(?:-(\d{2}))?", str(scope.get("time_start") or ""))
    b = re.match(r"(\d{4})(?:-(\d{2}))?", str(scope.get("time_end") or ""))
    start = (today.year, today.month)
    if y and b:
        months = (int(b.group(1)) * 12 + int(b.group(2) or 12)) - ((int(a.group(1)) * 12 + int(a.group(2) or 1)) if a else 0)
        want = (y, 7) if (not a or months >= 11) else (y, int(b.group(2) or 7))
        if want < (today.year, today.month):
            start = want
    out, (yy, mm) = [], start
    for _ in range(8):
        out.append((yy, mm))
        yy, mm = (yy, mm - 1) if mm > 1 else (yy - 1, 12)
    return out


def _nclimgrid_month(var: str, y: int, m: int) -> list[str] | None:
    url = f"{NCLIMGRID}/{y}/{var}-{y}{m:02d}-cty-scaled.csv"
    if not sources.safe_url(url):
        return None
    r = requests.get(url, headers=UA, timeout=40)
    if r.status_code != 200 or len(r.content) > MAX_RAW_BYTES:
        return None
    return r.text.splitlines()


def _nclimgrid(s, pick, meta):
    """NOAA nClimGrid-Daily county averages: one month of daily max temperature and rain for the counties in the area."""
    sb = s.scope.get("bbox")
    if not sb:
        return {"status": "none", "note": "Set a study area to preview nClimGrid"}
    lines = None
    for y, m in _months(s.scope):
        lines = _nclimgrid_month("tmax", y, m)
        if lines:
            break
    if not lines:
        return {"status": "none", "note": "NOAA's county files did not answer"}
    prcp = {r.split(",")[1]: r for r in (_nclimgrid_month("prcp", y, m) or [])}
    # neighbouring counties too, so a small study area still shows contrast on the map
    pad = max(0.35, 0.5 * max(sb[2] - sb[0], sb[3] - sb[1]))
    sb = [max(-180, sb[0] - pad), max(-90, sb[1] - pad), min(180, sb[2] + pad), min(90, sb[3] + pad)]
    counties = _retry(lambda: sources.arcgis_query(f"{TIGER2020}/82", sb, max_features=3000,
                                                   simplify_deg=max(0.0001, min(0.003, (sb[2] - sb[0]) / 4000))))  # 2020 counties match NOAA's
    by_geoid = {(f.get("properties") or {}).get("GEOID"): f for f in counties.get("features", [])}
    feats, rows = [], [["GEOID", "county", "date", "tmax_c", "prcp_mm"]]
    for line in lines:
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 8 or parts[0] != "cty":
            continue
        abbr = parts[2].split(":")[0].strip()
        geoid = STATE_FIPS.get(abbr, "??") + parts[1][2:]
        f = by_geoid.get(geoid)
        if not f:
            continue
        days = [float(v) for v in parts[6:] if v not in ("", "-999.99")]
        rain = [float(v) for v in prcp.get(parts[1], "").split(",")[6:] if v.strip() not in ("", "-999.99")] if prcp else []
        hot = sum(1 for d in days if d >= 32.2)
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": {
            "GEOID": geoid, "county": parts[2], "month": f"{y}-{m:02d}",
            "mean_tmax_c": round(sum(days) / len(days), 1) if days else None, "max_tmax_c": max(days) if days else None,
            "days_over_32c": hot, "total_prcp_mm": round(sum(rain), 1) if rain else None}})
        for i, d in enumerate(days):
            rows.append([geoid, parts[2], f"{y}-{m:02d}-{i + 1:02d}", d, rain[i] if i < len(rain) else ""])
    if not feats:
        return {"status": "none", "note": "No nClimGrid counties in the study area"}
    vals = [f["properties"]["mean_tmax_c"] for f in feats if f["properties"]["mean_tmax_c"] is not None]
    layer, note = _vector_layer(s, pick, feats, f"nClimGrid mean daily max temperature, {y}-{m:02d}",
                                "NOAA nClimGrid-Daily county averages (downloaded for this session)", _paint("mean_tmax_c", vals, "reds"),
                                "nClimGrid preview")
    if not layer:
        return {"status": "none", "note": note}
    return {"status": "ready", "kind": "slice", "layers": [layer], "csv": _csv(s, pick, rows),
            "note": f"{y}-{m:02d} daily max temperature and rain for {len(feats)} counties (downloaded for this session)"}


def _places(s, pick, meta):
    """CDC PLACES tract estimates for one measure that matches the question, inside the study area."""
    sb = s.scope.get("bbox")
    if not sb:
        return {"status": "none", "note": "Set a study area to preview PLACES"}
    w, h = bbox_size_km(sb)
    if w * h > 40000:
        return {"status": "none", "note": "Study area too large for a tract preview; use the county file"}
    text = _scope_text(s)
    measure = next((mid for words, mid in PLACES_MEASURES if any(wd in text for wd in words)), "GHLTH")
    q = {"$where": f"within_box(geolocation, {sb[3]}, {sb[0]}, {sb[1]}, {sb[2]}) AND measureid='{measure}'", "$limit": 5000}
    r = requests.get(PLACES_TRACTS, params=q, headers=UA, timeout=40)
    r.raise_for_status()
    recs = {x.get("locationid"): x for x in r.json()}
    if not recs:
        return {"status": "none", "note": "No PLACES tracts in the study area"}
    tracts = _retry(lambda: sources.census_boundaries("tract", sb))  # current tracts match the 2025 release (Connecticut planning regions)
    feats, rows = [], [["tract", "measure", "value", "low", "high", "population"]]
    name = next(iter(recs.values())).get("measure") or measure
    for f in tracts.get("features", []):
        g = (f.get("properties") or {}).get("GEOID")
        x = recs.get(g)
        if not x:
            continue
        val = float(x["data_value"]) if x.get("data_value") not in (None, "") else None
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": {
            "tract": g, "measure": name, "value_pct": val, "low": x.get("low_confidence_limit"), "high": x.get("high_confidence_limit")}})
        rows.append([g, name, val, x.get("low_confidence_limit"), x.get("high_confidence_limit"), x.get("totalpopulation")])
    vals = [f["properties"]["value_pct"] for f in feats if f["properties"]["value_pct"] is not None]
    layer, note = _vector_layer(s, pick, feats, f"CDC PLACES: {name}"[:100], "CDC PLACES 2025 release (downloaded for this session)",
                                _paint("value_pct", vals, "purples"), "PLACES preview")
    if not layer:
        return {"status": "none", "note": note or "No tracts matched"}
    return {"status": "ready", "kind": "slice", "layers": [layer], "csv": _csv(s, pick, rows),
            "note": f"{name} for {len(feats)} tracts (model-based estimates)"}


def _direct_file(s, pick, meta):
    url = next((u for u in (meta.get("download_url"), pick.get("ref"), meta.get("url"))
                if isinstance(u, str) and u.lower().split("?")[0].endswith(FILE_EXT)), None)
    if not url:
        return None
    r = downloads.add(s, url, (meta.get("title") or pick.get("title") or "")[:80] or None, max_bytes=MAX_RAW_BYTES)
    if not r["layers"]:
        return {"status": "none", "note": "Nothing in the file could be drawn"}
    return {"status": "ready", "kind": "slice", "layers": r["layers"],
            "note": f"{r['file']} ({r['bytes'] / 1e6:.1f} MB) downloaded for this session"}


BY_REF = {"cur:acs": _acs, "cur:tiger": _tiger, "cur:nclimgrid": _nclimgrid, "cur:places": _places}


def _reason(pick, meta) -> str:
    ref = pick["ref"]
    acc = (meta.get("access_class") or "").lower()
    if ref.startswith(("gee:", "geec:")):
        return "Earth Engine data: viewing it needs an Earth Engine account"
    if acc in ("yale", "paid"):
        return "Licensed: open it through the Yale access route on the card"
    if "registration" in (meta.get("access") or "").lower() or acc == "request":
        return "The provider asks for registration or a request first"
    return "No online map service or small open download found; use the source link"


def build(s, pick) -> dict:
    meta = pick.get("meta") or catalog_mod.load().get(pick["ref"]) or {}
    for fn in (_geolibre, _service, BY_REF.get(pick["ref"]), _arcgis, _stac, _direct_file):
        if fn is None:
            continue
        r = fn(s, pick, meta)
        if r is not None:
            break
    else:
        r = {"status": "none", "note": _reason(pick, meta)}
    for l in r.get("layers", []):
        if pick.get("role") != "core":
            l["hidden"] = True  # supporting data waits in the Layers panel
        if l not in s.layers:
            s.layers.append(l)
    return r


# ---------------------------------------------------------------------------- running in the background
def _sig(scope: dict) -> str:
    return repr([scope.get(k) for k in ("bbox", "time_start", "time_end", "unit_of_analysis")])


def _one(s, pick, sig):
    with _slots:
        if session.peek(s.sid) is not s:
            return  # the session ended
        try:
            r = build(s, pick)
        except downloads.DownloadError as e:
            r = {"status": "none", "note": str(e)}
        except Exception as e:  # one slow or broken source must not stop the others
            r = {"status": "error", "note": f"Preview failed ({type(e).__name__})"}
        r.update(at=time.time(), sig=sig)
        with s.plock:
            s.previews[pick["ref"]] = r


def start(s, refs: list[str] | None = None, sync: bool = False) -> bool:
    """Build previews for the session's picks that do not have one for the current scope yet."""
    if not enabled():
        return False
    sig = _sig(s.scope)
    with s.plock:
        todo = [p for p in s.picks if (not refs or p["ref"] in refs)
                and (s.previews.get(p["ref"]) or {}).get("sig") != sig][:MAX_PICKS]
        if not todo:
            return False
        for p in todo:
            s.previews[p["ref"]] = {"status": "pending", "sig": sig}
        s.preview_running = True

    def run():
        try:
            with cf.ThreadPoolExecutor(3) as ex:
                list(ex.map(lambda p: _one(s, p, sig), todo))
        finally:
            with s.plock:
                s.preview_running = False
    if sync:
        run()
    else:
        threading.Thread(target=run, name=f"previews-{s.sid[:8]}", daemon=True).start()
    return True


def public(s) -> dict:
    with s.plock:
        items = {ref: {k: v for k, v in r.items() if k in ("status", "kind", "note", "layers", "csv")}
                 for ref, r in s.previews.items()}
        return {"running": s.preview_running, "items": items}
