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
import statistics
import threading
import time
import zipfile

import requests

from . import browser
from . import catalog as catalog_mod
from . import downloads, geolibre, session, sources, tables, vector
from .geo import bbox_size_km, coverage_fraction, geojson_bbox
from .session import save_layer

MAX_PICKS = 10
MAX_RAW_BYTES = 100 * 1024 * 1024     # "download a small data volume (100 MB)" for sources without a service
_slots = threading.BoundedSemaphore(4)  # previews fetching at once on the whole server
MAX_BROWSER = 6                         # pages per session handed to Y-finder's browser
UA = {"User-Agent": "Y-finder/0.1 (YCGS data guide; +https://app.researcharchitect.ai/y-finder/)"}
TIGER2020 = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Census2020/MapServer"
ESRI_ACS_INCOME = ("https://P3ePLMYs2RVChkJx.svcs.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
                   "ACS_Median_Household_Income_View_Boundaries/FeatureServer")
ESRI_ACS_RACE = ("https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
                 "ACS_Population_by_Race_and_Hispanic_Origin_Boundaries/FeatureServer")
ESRI_ACS_POP = ("https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
                "ACS_Total_Population_Boundaries/FeatureServer")   # table B01001: total, under 18, 65 and over
# which ACS variable a pick stands for, read from its factors first and its reason second
RACE_WORDS = re.compile(r"race|racial|ethnic|latin|hispan|black|african|asian|minorit|segregat|people of color")
CHILD_WORDS = re.compile(r"\b(child|children|childhood|kids?|youth|minors|under[- ]?18|pediatric|paediatric|infants?|toddlers?|"
                         r"school[- ]age)\b")   # not "students": university students are adults
OLDER_WORDS = re.compile(r"\b(older adults?|older people|elderly|seniors?|65\+|over 65|65 and over|aged 65|ag(e|ing|eing) "
                         r"(structure|population)|retire\w*)\b")
INCOME_WORDS = re.compile(r"income|earning|wage|poverty|wealth|afford|socio-?economic|deprivation|disadvantage")
# "people" and "residents" alone say nothing about the measure ("unemployed people", "neighborhood residents")
POP_WORDS = re.compile(r"\b(population|inhabitants|denominator|head ?count|per capita)\b")
ACS_AGE = {  # measure -> (value field, map property, layer title, legend label, what the note says)
    "children": ("B01001_calc_pctLT18E", "under_18_pct", "ACS residents under 18 by {unit}", "Residents under 18 (%)",
                 "Share of residents under 18"),
    "older": ("B01001_calc_pctGE65E", "over_65_pct", "ACS residents 65 and over by {unit}", "Residents 65 and over (%)",
              "Share of residents 65 and over"),
    "population": ("B01001_001E", "population", "ACS total population by {unit}", "Total population (ACS 5-year)", "Total population"),
}


def _acs_measure(factors: str, why: str = "") -> str:
    """race, children, older, income or population: the first that the factors name, else the first the reason names,
    else income (most ACS picks stand for socioeconomic conditions)."""
    for text in (factors or "", why or ""):
        t = text.lower()
        for name, rx in (("race", RACE_WORDS), ("children", CHILD_WORDS), ("older", OLDER_WORDS), ("income", INCOME_WORDS),
                         ("population", POP_WORDS)):
            if rx.search(t):
                return name
    return "income"
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


def _paint(prop: str, values: list[float], ramp: str, label: str = "") -> dict | None:
    st = _stops(values, ramp)
    if len(st) < 2:  # one value (one county, one monitor): a single colour, still labelled for the legend
        return {"fill": RAMPS[ramp][-2], "stroke": "#ffffff", "opacity": 0.75, "width": 0.5, "label": label or prop.replace("_", " ")} if st else None
    return {"mode": "graduated", "property": prop, "stops": st, "fill": st[-1][1], "stroke": "#ffffff", "opacity": 0.75,
            "width": 0.5, "label": label or prop.replace("_", " ")}


def _vector_layer(s, pick, fc_feats: list[dict], title: str, source: str, paint: dict | None = None,
                  label: str = "") -> tuple[dict | None, str | None]:
    keep = {w for w in _pick_words(s, pick, pick.get("meta") or {}) if len(w) >= 5}  # columns that name the topic stay
    fc, note, _total = vector.slim(fc_feats, s.scope.get("bbox"), keep=keep)
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


def _drawn(s, test) -> dict | None:
    return next((l for l in list(s.layers) if test(l)), None)


def _already(layer: dict | None) -> dict:
    """The card's data is a layer already drawn (by the agent or another card): name it as the Layers list does, and
    send it, so the card can show or hide it and the browser can draw it again if it lost it."""
    title = (layer or {}).get("title") or ""
    return dict(ALREADY, layers=[], existing=[dict(layer)] if layer else [],
                note=f"Already on the map as “{title}”" if title else ALREADY["note"])


def short_title(t: str) -> str:
    """'NAIP: National Agriculture Imagery Program' -> 'NAIP'; long names are cut at a word."""
    t = (t or "").strip()
    head = t.split(":")[0].strip()
    if ":" in t and 2 <= len(head) <= 24:
        return head
    if len(t) <= 40:
        return t
    if "," in t[:44]:  # "Annual Housing Permit Data By Town, 1990-2025" -> before the comma
        return t.split(",")[0].strip()
    return t[:40].rsplit(" ", 1)[0] + "..."


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
    if t.get("legend"):
        layer["legend"] = t["legend"]
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
        same = _drawn(s, lambda l: (l.get("service_url") or "").lower() == url.lower())
        if same:
            return _already(same)
        fc = sources.arcgis_query(url, s.scope["bbox"], max_features=2000)
        feats = fc.get("features", [])
        fb = geojson_bbox({"type": "FeatureCollection", "features": feats}) if feats else None
        same = fb and _drawn(s, lambda l: str(l.get("source", "")).startswith("ArcGIS service") and l.get("n") == len(feats)
                             and l.get("bbox") and all(abs(x - y) < 1e-3 for x, y in zip(l["bbox"], fb)))
        if same:
            return _already(same)  # same features already drawn (an older layer without its service URL)
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


RACE_GROUPS = [  # words in the pick's factors -> (ACS percent field, label)
    (r"latin|hispan", "B03002_calc_pctHispLatE", "Latino"),
    (r"\bblack|african", "B03002_calc_pctBlackE", "Black"),
    (r"asian", "B03002_calc_pctAsianE", "Asian"),
    (r"native|indigenous|american indian", "B03002_calc_pctAIANE", "American Indian and Alaska Native"),
]


def _plural(unit: str) -> str:
    return "counties" if unit == "county" else unit + "s"


def _acs(s, pick, meta):
    """ACS by tract (county for big areas) from Esri's keyless Living Atlas services: race and Hispanic origin
    shares when the pick measures race or ethnicity, the share under 18 or 65 and over for age factors, total
    population for a denominator, otherwise median household income."""
    sb = s.scope.get("bbox")
    if not sb:
        return {"status": "none", "note": "Set a study area to preview ACS values"}
    w, h = bbox_size_km(sb)
    lid, unit = (2, "tract") if w * h <= 40000 else (1, "county")
    nodes = {n["id"]: n.get("label", "") + " " + n.get("observable", "") for n in (s.graph or {}).get("nodes", [])}
    ftext = " ".join(nodes.get(f, "") for f in pick.get("factors") or []).lower()
    text = (ftext + " " + (pick.get("why") or "")).lower()
    measure = _acs_measure(ftext, pick.get("why") or "")
    simplify = max(0.00005, min(0.003, (sb[2] - sb[0]) / 4000))
    if measure in ACS_AGE:
        field, prop, title, label, what = ACS_AGE[measure]
        fc = sources.arcgis_query(f"{ESRI_ACS_POP}/{lid}", sb, max_features=3000, simplify_deg=simplify,
                                  out_fields="GEOID,NAME,B01001_001E,B01001_calc_numLT18E,B01001_calc_pctLT18E,"
                                             "B01001_calc_numGE65E,B01001_calc_pctGE65E")
        feats = []
        for f in fc.get("features", []):
            p = f.get("properties") or {}
            pop = p.get("B01001_001E")
            few = measure != "population" and (pop or 0) < 200  # parks and airports: no meaningful share
            v = p.get(field)
            pr = {"GEOID": p.get("GEOID"), "name": p.get("NAME"), "population": pop,
                  "under_18": p.get("B01001_calc_numLT18E"), "under_18_pct": p.get("B01001_calc_pctLT18E"),
                  "over_65": p.get("B01001_calc_numGE65E"), "over_65_pct": p.get("B01001_calc_pctGE65E")}
            pr[prop] = round(v, 1) if isinstance(v, (int, float)) and v >= 0 and not few else None
            feats.append({"type": "Feature", "geometry": f.get("geometry"), "properties": pr})
        vals = [f["properties"][prop] for f in feats if f["properties"][prop] is not None]
        layer, note = _vector_layer(s, pick, feats, title.format(unit=unit), "ACS 5-year, latest (Esri Living Atlas)",
                                    _paint(prop, vals, "blues", label), "ACS preview")
        if not layer:
            return {"status": "none", "note": note or "No areas found"}
        keys = ["GEOID", "name", "population", "under_18", "under_18_pct", "over_65", "over_65_pct"]
        rows = [keys] + [[f["properties"].get(k) for k in keys] for f in feats]
        return {"status": "ready", "kind": "service", "layers": [layer], "csv": _csv(s, pick, rows),
                "note": f"{what} for {len(feats)} {_plural(unit)} (ACS table B01001, latest 5-year)"}
    if measure == "race":
        groups = [(f, lab) for rx, f, lab in RACE_GROUPS if re.search(rx, text)]
        fc = sources.arcgis_query(f"{ESRI_ACS_RACE}/{lid}", sb, max_features=3000, simplify_deg=simplify,
                                  out_fields="GEOID,NAME,B03002_001E,B03002_calc_pctHispLatE,B03002_calc_pctBlackE,"
                                             "B03002_calc_pctAsianE,B03002_calc_pctNHWhiteE,B03002_calc_pctAIANE")
        feats = []
        for f in fc.get("features", []):
            p = f.get("properties") or {}
            pr = {"GEOID": p.get("GEOID"), "name": p.get("NAME"), "population": p.get("B03002_001E"),
                  "latino_pct": p.get("B03002_calc_pctHispLatE"), "black_pct": p.get("B03002_calc_pctBlackE"),
                  "asian_pct": p.get("B03002_calc_pctAsianE"), "white_nh_pct": p.get("B03002_calc_pctNHWhiteE")}
            few = (p.get("B03002_001E") or 0) < 200  # parks, airports and other near-empty tracts: no meaningful share
            if groups:
                vals = [p.get(fld) for fld, _l in groups]
                pr["share_pct"] = round(sum(vals), 1) if all(isinstance(v, (int, float)) for v in vals) and not few else None
            else:
                v = p.get("B03002_calc_pctNHWhiteE")
                pr["share_pct"] = round(100 - v, 1) if isinstance(v, (int, float)) and not few else None
            feats.append({"type": "Feature", "geometry": f.get("geometry"), "properties": pr})
        who = " or ".join(l for _f, l in groups) if groups else "people of color (not non-Hispanic White)"
        label = f"The share of {who} residents (%)" if groups else "The share of people of color (% not non-Hispanic White)"
        vals = [f["properties"]["share_pct"] for f in feats if f["properties"]["share_pct"] is not None]
        layer, note = _vector_layer(s, pick, feats, f"ACS share of {who} residents by {unit}", "ACS 5-year, latest (Esri Living Atlas)",
                                    _paint("share_pct", vals, "purples", label), "ACS preview")
        if not layer:
            return {"status": "none", "note": note or "No areas found"}
        keys = ["GEOID", "name", "population", "share_pct", "latino_pct", "black_pct", "asian_pct", "white_nh_pct"]
        rows = [keys] + [[f["properties"].get(k) for k in keys] for f in feats]
        return {"status": "ready", "kind": "service", "layers": [layer], "csv": _csv(s, pick, rows),
                "note": f"Share of {who} residents for {len(feats)} {_plural(unit)} (ACS table B03002, latest 5-year)"}
    fc = sources.arcgis_query(f"{ESRI_ACS_INCOME}/{lid}", sb, max_features=3000, simplify_deg=simplify)
    feats = []
    for f in fc.get("features", []):
        p = f.get("properties") or {}
        v = p.get("B19049_001E")
        feats.append({"type": "Feature", "geometry": f.get("geometry"),
                      "properties": {"GEOID": p.get("GEOID"), "name": p.get("NAME"), "median_household_income": v if isinstance(v, (int, float)) and v >= 0 else None,
                                     "moe": p.get("B19049_001M")}})
    vals = [f["properties"]["median_household_income"] for f in feats if f["properties"]["median_household_income"] is not None]
    layer, note = _vector_layer(s, pick, feats, f"ACS median household income by {unit}", "ACS 5-year, latest (Esri Living Atlas)",
                                _paint("median_household_income", vals, "greens", "Median household income (US$)"), "ACS preview")
    if not layer:
        return {"status": "none", "note": note or "No areas found"}
    rows = [["GEOID", "name", "median_household_income", "moe"]] + [[f["properties"][k] for k in ("GEOID", "name", "median_household_income", "moe")] for f in feats]
    return {"status": "ready", "kind": "service", "layers": [layer], "csv": _csv(s, pick, rows),
            "note": f"Median household income for {len(feats)} {_plural(unit)} (latest 5-year ACS)"}


CT_BOX = [-73.75, 40.95, -71.75, 42.07]
CTECO_SERVICES = [  # (words in the question or graph, ArcGIS service, title)
    (r"canopy|tree|forest|shade", "https://cteco.uconn.edu/ctraster/rest/services/landcover/CCAP_LandcoverForestCanopy2021_1m/MapServer",
     "CT ECO tree canopy 2021 (1 m)"),
    (r"standing water|ponding|puddle|pond|wetland|open water|larva|mosquito", "https://cteco.uconn.edu/ctraster/rest/services/landcover/CCAP_LandcoverWater2021_1m/MapServer",
     "CT ECO water 2021 (1 m)"),
    (r"impervious|pavement|paved|heat island", "https://cteco.uconn.edu/ctraster/rest/services/landcover/CCAP_LandcoverImpervious2021_1m/MapServer",
     "CT ECO impervious surface 2021 (1 m)"),
    (r"shoreline|coast|waterfront|harbor|harbour|filled", "https://cteco.uconn.edu/ctmaps/rest/services/Coastal/Shoreline_Change_Long_Term/MapServer",
     "CT ECO long-term shoreline change (historical shorelines)"),
]
CTECO_1934 = "https://cteco.uconn.edu/ctraster/rest/services/images/Mosaic_1934_MAGIC/ImageServer"
CTECO_ORTHO = "https://cteco.uconn.edu/ctraster/rest/services/images/Ortho_2023/ImageServer"


def _cteco(s, pick, meta):
    """CT ECO's own map services, chosen by the question: 1934 aerial photos for historical questions, 1 m tree
    canopy, water or impervious land cover, historical shorelines, else the 2023 orthophotos."""
    sb = s.scope.get("bbox")
    if not sb or sb[2] < CT_BOX[0] or sb[0] > CT_BOX[2] or sb[3] < CT_BOX[1] or sb[1] > CT_BOX[3]:
        return {"status": "none", "note": "CT ECO covers Connecticut only"}
    text = _scope_text(s)
    question = ((s.graph or {}).get("question") or "").lower()
    m = re.match(r"\d{4}", str(s.scope.get("time_start") or ""))
    picks = []
    if m and int(m.group()) < 1950:
        picks.append((CTECO_1934, "CT ECO 1934 aerial photo mosaic"))
    hits = [(len(re.findall(rx, question)) * 2 + len(re.findall(rx, text)), i, u, t) for i, (rx, u, t) in enumerate(CTECO_SERVICES)]
    picks += [(u, t) for n, _i, u, t in sorted(hits, key=lambda h: (-h[0], h[1])) if n]  # the question's own words first
    if not picks:
        picks.append((CTECO_ORTHO, "CT ECO orthophotos 2023"))
    layers = []
    for i, (url, title) in enumerate(picks[:2]):
        r = _arcgis_url(s, pick, url, title)
        for l in (r or {}).get("layers", []):
            l["source"] = "CT ECO (UConn CLEAR, CT DEEP)"
            l["bbox"] = CT_BOX
            if i:
                l["hidden"] = True
            layers.append(l)
    if not layers:
        return {"status": "none", "note": "CT ECO services did not answer"}
    return {"status": "ready", "kind": "service", "layers": layers,
            "note": "Live CT ECO map service: " + "; ".join(t for _u, t in picks[:2])}


# ---------------------------------------------------------------- NOAA HMS smoke polygons
HMS = "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/Shapefile"


def _hms(s, pick, meta):
    """NOAA Hazard Mapping System smoke polygons for each day of the window (up to 31 days) over the study area."""
    import logging
    import shapefile  # pyshp
    logging.getLogger("shapefile").setLevel(logging.ERROR)  # HMS rings are often wound backwards; pyshp fixes them and logs each
    sb = s.scope.get("bbox")
    if not sb:
        return {"status": "none", "note": "Set a study area to preview smoke polygons"}
    try:
        d0 = dt.date.fromisoformat(str(s.scope.get("time_start"))[:10])
        d1 = dt.date.fromisoformat(str(s.scope.get("time_end"))[:10])
    except ValueError:
        return {"status": "none", "note": "HMS smoke is daily: give the study a start and end date"}
    if d0 < dt.date(2005, 8, 1) or d0 > dt.date.today():
        return {"status": "none", "note": "HMS smoke polygons start in August 2005"}
    days = [d0 + dt.timedelta(days=i) for i in range(min((d1 - d0).days + 1, 31))]
    feats, per_day = [], {}

    def day(d):
        url = f"{HMS}/{d.year}/{d.month:02d}/hms_smoke{d:%Y%m%d}.zip"
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code != 200 or len(r.content) > 20 * 1024 * 1024:
                return d, []
            zf = zipfile.ZipFile(io.BytesIO(r.content))
            names = {n.lower()[-4:]: n for n in zf.namelist()}
            rd = shapefile.Reader(shp=io.BytesIO(zf.read(names[".shp"])), dbf=io.BytesIO(zf.read(names[".dbf"])))
            out = []
            for sr in rd.iterShapeRecords():
                b = sr.shape.bbox
                if b[2] < sb[0] or b[0] > sb[2] or b[3] < sb[1] or b[1] > sb[3]:
                    continue
                rec = sr.record.as_dict()
                out.append({"type": "Feature", "geometry": sr.shape.__geo_interface__,
                            "properties": {"date": d.isoformat(), "density": str(rec.get("Density") or "").strip() or "Unknown",
                                           "satellite": rec.get("Satellite"), "start": rec.get("Start"), "end": rec.get("End")}})
            return d, out
        except (requests.RequestException, zipfile.BadZipFile, KeyError, ValueError, shapefile.ShapefileException):
            return d, []
    with cf.ThreadPoolExecutor(4) as ex:
        for d, out in ex.map(day, days):
            feats += out
            per_day[d] = out
    if not feats:
        return {"status": "none", "note": f"No HMS smoke polygons over the study area from {days[0]} to {days[-1]}"}
    order = {"Light": 0, "Medium": 1, "Heavy": 2}
    feats.sort(key=lambda f: order.get(f["properties"]["density"], -1))  # heavy smoke drawn on top
    paint = {"mode": "categorized", "property": "density", "stops": [["Light", "#d9d9d9"], ["Medium", "#969696"], ["Heavy", "#525252"]],
             "fill": "#bdbdbd", "stroke": "#ffffff", "opacity": 0.35, "width": 0.4, "label": "Smoke density (NOAA HMS)"}
    layer, note = _vector_layer(s, pick, feats, f"NOAA HMS smoke polygons, {days[0]} to {days[-1]}", "NOAA HMS daily smoke shapefiles", paint, "HMS preview")
    if not layer:
        return {"status": "none", "note": note or "No smoke polygons in the study area"}
    heavy = sorted(d for d, out in per_day.items() if any(f["properties"]["density"] == "Heavy" for f in out))
    smoky = sum(1 for out in per_day.values() if out)
    rows = [["date", "polygons", "heavy_polygons"]] + [[d.isoformat(), len(o), sum(1 for f in o if f["properties"]["density"] == "Heavy")]
                                                       for d, o in sorted(per_day.items())]
    return {"status": "ready", "kind": "slice", "layers": [layer], "csv": _csv(s, pick, rows),
            "series": {"label": "Smoke polygons over the study area per day", "points": [[d.day, len(o)] for d, o in sorted(per_day.items())]} if len(days) >= 3 else None,
            "note": f"Smoke on {smoky} of {len(days)} days" + (f"; heavy smoke on {len(heavy)} days, first {heavy[0]}" if heavy else "")}


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
    same = _drawn(s, lambda l: l.get("source") == "U.S. Census Bureau TIGERweb" and l.get("title") in (agent_name, f"Census {plural}"))
    if same:
        return _already(same)
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
    nodes = {n["id"]: n.get("label", "") + " " + n.get("observable", "") for n in (s.graph or {}).get("nodes", [])}
    words = " ".join([nodes.get(f, "") for f in pick.get("factors") or []] + [_scope_text(s)]).lower()
    heat = bool(re.search(r"extreme heat|heat day|hot day|heat wave|heatwave|heat exposure|days above|days over", words))
    prop, title, label = (("days_over_32c", f"nClimGrid days at or above 32.2 °C (90 °F), {y}-{m:02d}", f"Days at or above 32.2 °C (90 °F), {y}-{m:02d}")
                          if heat else ("mean_tmax_c", f"nClimGrid mean daily max temperature, {y}-{m:02d}", "Mean daily max temperature (°C)"))
    vals = [f["properties"][prop] for f in feats if f["properties"][prop] is not None]
    layer, note = _vector_layer(s, pick, feats, title, "NOAA nClimGrid-Daily county averages (downloaded for this session)",
                                _paint(prop, vals, "reds", label), "nClimGrid preview")
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
                                _paint("value_pct", vals, "purples", f"{name} (%)"), "PLACES preview")
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


# ---------------------------------------------------------------------------- download pages (no direct link on the card)
DATA_LINK_EXT = (".csv", ".xlsx", ".zip", ".geojson", ".kml", ".kmz")
_HREF = re.compile(r"""href\s*=\s*["']([^"'#]+)["']""", re.I)
_BAD_LINK = re.compile(r"method|technical|appendix|readme|codebook|dictionary|template|report|press|summary|glossary|metadata", re.I)
_GOOD_LINK = re.compile(r"result|score|data|indicator|value|estimate|table", re.I)


_ANCHOR = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S)


def _data_links(page_url: str, html_text: str, year: int | None, words=None) -> list[str]:
    from urllib.parse import urljoin, urlparse
    links, seen = [], set()
    texts = {h.strip(): re.sub(r"<[^>]+>|\s+", " ", t)[:200] for h, t in _ANCHOR.findall(html_text)}
    for href in _HREF.findall(html_text):
        u = urljoin(page_url, href.strip())
        path = urlparse(u).path.lower()
        if not path.endswith(DATA_LINK_EXT) or u in seen or not u.startswith("https://"):
            continue
        seen.add(u)
        name = path.rsplit("/", 1)[-1]
        score = (3 if _GOOD_LINK.search(name) else 0) - (6 if _BAD_LINK.search(name) else 0)
        score += 2 if name.endswith((".xlsx", ".csv", ".geojson")) else 0
        score += 1 if year and str(year) in name else 0
        score += 3 * min(2, browser.topic_hits(name, texts.get(href.strip(), ""), words))  # the card's topic in the name or link text
        links.append((score, -len(name), u))
    links.sort(reverse=True)
    return [u for _s, _l, u in links]


def _boundaries(s, kind: str) -> dict[str, dict]:
    sb = s.scope.get("bbox")
    if kind == "country":
        return tables.countries()
    if not sb:
        return {}
    layer = {"state": 80, "county": 82, "tract": 8}[kind]
    fc = _retry(lambda: sources.arcgis_query(f"{sources.TIGER}/{layer}", sb, max_features=3000,
                                             simplify_deg=max(0.0001, min(0.003, (sb[2] - sb[0]) / 4000))))
    out = {}
    for f in fc.get("features", []):
        g = str((f.get("properties") or {}).get("GEOID") or "")
        if g:
            out[g] = {"type": "Feature", "geometry": f["geometry"], "properties": {"name": (f.get("properties") or {}).get("NAME")}}
    return out


def _table_layers(s, pick, meta, raw: bytes, fname: str, src: str):
    tbls = tables.read_tables(raw, fname)
    labels = tables.dictionary(tbls)
    iso3 = set(tables.countries()) if any(len(r) > 1 for _n, rows in tbls for r in rows[:3]) else set()
    nodes = {n["id"]: n.get("label", "") for n in (s.graph or {}).get("nodes", [])}
    # what the column should measure: the question, the factors this dataset was picked for, and why
    text = " ".join([_scope_text(s), " ".join(nodes.get(f, "") for f in pick.get("factors") or []), pick.get("why") or ""])
    for tname, rows in tbls:
        if len(rows) < 4:
            continue
        h = tables._header_row(rows)
        header, body = rows[h], rows[h + 1:]
        found = tables.detect_key(header, body, iso3)
        if not found:
            continue
        key, kind = found
        cols = tables.value_columns(header, body, key, labels, text)
        if not cols:
            continue
        shapes = _boundaries(s, kind)
        if not shapes:
            continue
        layers, out_rows = [], [[header[key], "name"] + [header[c] for c in cols]]
        joined = {}
        for r in body:
            if len(r) <= key or not r[key]:
                continue
            k = tables.key_value(kind, r[key])
            if k in shapes:
                joined[k] = r
        for n, c in enumerate(cols):
            feats, vals = [], []
            for k, r in joined.items():
                v = tables._num(r[c]) if len(r) > c else None
                f = shapes[k]
                feats.append({"type": "Feature", "geometry": f["geometry"],
                              "properties": {"key": k, "name": f["properties"].get("name"), header[c]: v}})
                if v is not None:
                    vals.append(v)
            lab = tables.label_for(header[c], labels)
            layer, _note = _vector_layer(s, pick, feats, f"{short_title(meta.get('title') or pick.get('title') or '')}: {lab}"[:100],
                                         src, _paint(header[c], vals, ("blues", "greens", "purples")[n % 3], lab), "table preview")
            if layer:
                if n:
                    layer["hidden"] = True  # the best-matching column shows; the others wait in Layers
                layers.append(layer)
        for k, r in joined.items():
            out_rows.append([k, shapes[k]["properties"].get("name")] + [tables._num(r[c]) if len(r) > c else None for c in cols])
        if layers:
            unit = {"country": "countries", "state": "states", "county": "counties", "tract": "tracts"}[kind]
            return {"status": "ready", "kind": "slice", "layers": layers, "csv": _csv(s, pick, out_rows),
                    "note": f"{fname} ({tname}): {len(joined)} {unit}; " + ", ".join(tables.label_for(header[c], labels) for c in cols)}
    return None


def _landing(s, pick, meta):
    """The card links a download page: find its data files, download the best one (up to 100 MB) and map it."""
    url = meta.get("url") or pick.get("url") or ""
    acc = (meta.get("access_class") or "").lower()
    if not url.startswith("https://") or acc in ("yale", "paid", "request") or pick["ref"].startswith(("gee:", "geec:")):
        return None
    if not sources.safe_url(url):
        return None
    try:
        r = requests.get(url, headers=UA, timeout=(6, 20))  # some sites drop connections from data centres
    except requests.RequestException:
        return {"status": "none", "note": "The source site did not answer the Y-finder server; open it with the Source link",
                "retry_browser": True}
    if r.status_code != 200 or "html" not in (r.headers.get("Content-Type") or "") or not sources.safe_url(r.url):
        return None
    links = _data_links(r.url, r.text[:3_000_000], _study_year(s.scope), _pick_words(s, pick, meta))
    res, _why = _try_links(s, pick, meta, links[:3])
    return res


def _zip_has_shp(path) -> bool:
    """A zip with a shapefile inside (the member list sits at the end of the file, so look there, not at the first bytes)."""
    try:
        with zipfile.ZipFile(path) as z:
            return any(n.lower().endswith(".shp") for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def _read_raw(s, pick, meta, raw_path, fname: str, host: str):
    """A downloaded file as map layers (GeoJSON, KML, zipped shapefile) or a table joined to places, else None."""
    raw = raw_path.read_bytes()
    low = fname.lower()
    if low.endswith((".geojson", ".kml", ".kmz")) or _zip_has_shp(raw_path) or raw[:200].lstrip().startswith(b"{"):
        fmt = downloads.sniff(fname, "", raw[:4096])
        parts = downloads.read_layers(raw_path, fmt, fname)
        layers = []
        for stem, feats in parts[:3]:
            layer, _n = _vector_layer(s, pick, feats, f"{short_title(meta.get('title') or pick.get('title') or '')}: {stem}"[:100],
                                      f"Downloaded from {host} (kept for this session only)")
            if layer:
                layers.append(layer)
        if layers:
            return {"status": "ready", "kind": "slice", "layers": layers, "note": f"{fname} downloaded for this session"}
        return None
    # tables: points from coordinates, a map joined on place codes or town names, else a table preview
    inner = _zipped_csv(raw)
    if inner:  # a zip holding one main CSV (USGS mines): read the CSV, so its coordinates become points
        name, raw, part = inner
        res = _table_result(s, pick, meta, raw, name, f"{name} in {fname} from {host} (downloaded for this session)")
        if res and part:
            res["note"] = (res.get("note") or name) + f"; read from the first {CSV_PART // 1_000_000} MB of a {part // 1_000_000} MB file"
        return res
    return _table_result(s, pick, meta, raw, fname, f"{fname} from {host} (downloaded for this session)")


CSV_PART = 60_000_000  # a CSV bigger than this inside a zip is read from its start (the rows read are capped anyway)


def _zipped_csv(raw: bytes):
    """(name, bytes, full size if only the start was read) of the main CSV in a zip, else None."""
    if raw[:2] != b"PK":
        return None
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        members = [i for i in z.infolist() if i.filename.lower().endswith((".csv", ".tsv", ".txt")) and not i.filename.startswith("__MACOSX")]
    except (zipfile.BadZipFile, OSError):
        return None
    if not members:
        return None
    big = max(members, key=lambda i: i.file_size)
    if big.file_size < 0.5 * sum(i.file_size for i in members):
        return None  # several tables of the same weight (read as a workbook instead)
    name = big.filename.rsplit("/", 1)[-1]
    if big.file_size <= CSV_PART:
        return name, z.read(big), 0
    with z.open(big) as f:
        head = f.read(CSV_PART)
    return name, head[: head.rfind(b"\n") + 1], big.file_size


def _try_links(s, pick, meta, links: list[str], cookies: dict | None = None, progress=None):
    """Download the links in order (up to 100 MB each) until one becomes a layer or a table: (result, last problem)."""
    why = None
    for link in links:
        fname = re.sub(r"[^\w.\-]+", "-", link.split("?")[0].rsplit("/", 1)[-1])[-80:] or "download"
        host = link.split("/")[2]
        if progress:
            progress(f"Downloading {fname} from {host}")
        d = downloads.session_dir(s.sid)
        d.mkdir(parents=True, exist_ok=True)
        raw_path = d / ("pv-" + _tag(pick) + re.sub(r"[^a-z0-9.]+", "-", fname.lower())[-60:])
        try:
            got = downloads.fetch(link, raw_path, MAX_RAW_BYTES, cookies=cookies) if cookies else downloads.fetch(link, raw_path, MAX_RAW_BYTES)
            name = got.get("name") or fname if isinstance(got, dict) else fname
            if progress:
                progress(f"Reading {name}")
            res = _read_raw(s, pick, meta, raw_path, name if "." in name else fname, host)
            if res:
                return res, None
            why = f"found {name} on {host}, but it has nothing Y-finder can draw or tabulate"
        except downloads.DownloadError as e:
            msg = str(e)
            why = f"found {fname} on {host}, but {msg[:1].lower() + msg[1:]}"
        except (tables.TableError, requests.RequestException, ValueError, KeyError, OSError, zipfile.BadZipFile) as e:
            why = f"found {fname} on {host}, but could not read it ({type(e).__name__})"
        finally:
            raw_path.unlink(missing_ok=True)  # only the map layer and the CSV slice are kept
    return None, why


# ---------------------------------------------------------------------------- EPA AirData (pre-generated daily files)
AIRDATA = "https://aqs.epa.gov/aqsweb/airdata"
# words in the question or graph -> (AQS parameter code, name, standard, daily statistic EPA compares with that standard,
# sample duration of that statistic). PM uses the 24-hour mean; the gases use the daily maximum 8-hour or 1-hour value.
POLLUTANTS = [(("pm2.5", "pm 2.5", "pm25", "fine particul", "smoke"), "88101", "PM2.5", 35.0, "mean", ""),
              (("ozone", " o3"), "44201", "Ozone", 0.070, "max", "8-HR"),
              (("no2", "nitrogen dioxide", "nox"), "42602", "NO2", 100.0, "max", "1 HOUR"),
              (("pm10", "pm 10", "coarse particul"), "81102", "PM10", 150.0, "mean", ""),
              (("so2", "sulfur dioxide"), "42401", "SO2", 75.0, "max", "1 HOUR"),
              ((" co ", "carbon monoxide"), "42101", "CO", 9.0, "max", "8-HR")]
_airdata_list: dict = {"t": 0, "files": {}}


def _airdata_files() -> dict[str, int]:
    """AirData's file list (name -> size in KB), refreshed daily."""
    if time.time() - _airdata_list["t"] > 86400:
        r = requests.get(f"{AIRDATA}/file_list.csv", headers=UA, timeout=30)
        r.raise_for_status()
        files = {}
        for row in csv.reader(io.StringIO(r.text)):
            if len(row) >= 3 and row[0].endswith(".zip"):
                try:
                    files[row[0]] = int(row[2])
                except ValueError:
                    pass
        _airdata_list.update(t=time.time(), files=files)
    return _airdata_list["files"]


def _pollutants(text: str) -> list[tuple]:
    t = " " + text.lower() + " "
    hits = [p for p in POLLUTANTS if any(w in t for w in p[0])]
    return (hits or [POLLUTANTS[0]])[:2]


HOUR_WORDS = re.compile(r"hourly|\bhours?\b|rush|diurnal|time of day|morning|evening|commut|weekday|weekend")


def _aqs_hourly(s, code: str, pname: str, year: int, sites: dict, d0: str, d1: str) -> tuple[dict | None, str]:
    """Mean by hour of day on weekdays and weekends at the study's monitors (EPA AirData hourly file)."""
    fname = f"hourly_{code}_{year}.zip"
    if not 0 < _airdata_files().get(fname, 0) * 1024 <= MAX_RAW_BYTES:
        return None, ""
    want = set(sites)
    prefixes = tuple({'"%s","%s"' % tuple(sid.split("-")[:2]) for sid in want})
    d = downloads.session_dir(s.sid)
    d.mkdir(parents=True, exist_ok=True)
    raw = d / f"pv-{fname}"
    acc: dict[tuple[bool, int], list[float]] = {}
    units, weekday = "", {}
    try:
        downloads.fetch(f"{AIRDATA}/{fname}", raw, MAX_RAW_BYTES)
        with zipfile.ZipFile(raw) as zf:
            member = next(i for i in zf.infolist() if i.filename.endswith(".csv"))
            with zf.open(member) as fh:
                for line in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
                    if not line.startswith(prefixes):
                        continue  # a cheap test first: the state and county of the study's monitors
                    r = next(csv.reader([line]))
                    if len(r) < 15 or f"{r[0]}-{r[1]}-{r[2]}" not in want or not d0 <= r[9] <= d1:
                        continue
                    try:
                        hr, v = int(r[10][:2]), float(r[13])
                    except ValueError:
                        continue
                    if r[9] not in weekday:
                        weekday[r[9]] = dt.date.fromisoformat(r[9]).weekday() < 5
                    a = acc.setdefault((weekday[r[9]], hr), [0.0, 0])
                    a[0] += v
                    a[1] += 1
                    units = units or r[14]
    except (downloads.DownloadError, zipfile.BadZipFile, StopIteration, OSError):
        return None, ""
    finally:
        raw.unlink(missing_ok=True)
    wk = [[h, round(acc[(True, h)][0] / acc[(True, h)][1], 2)] for h in range(24) if acc.get((True, h), [0, 0])[1]]
    we = [[h, round(acc[(False, h)][0] / acc[(False, h)][1], 2)] for h in range(24) if acc.get((False, h), [0, 0])[1]]
    if len(wk) < 20:
        return None, ""
    peak = max(wk, key=lambda p: p[1])
    wem = dict(we).get(peak[0])
    unit = units.split("(")[0].strip().lower().replace("parts per billion", "ppb")
    note = (f"weekday {pname} peaks at {peak[0]:02d}:00 ({peak[1]:g} {unit})" +
            (f", {round(100 * (peak[1] / wem - 1))}% above weekends at that hour" if wem else ""))
    return ({"label": f"{pname} by hour of day, weekdays ({unit})", "x": "hour", "points": wk,
             "compare": {"label": "weekends", "points": we}}, note)


def _aqs(s, pick, meta):
    """EPA AirData daily files: monitors in and around the study area for the study's last published year."""
    sb = s.scope.get("bbox")
    if not sb:
        return {"status": "none", "note": "Set a study area to preview monitors"}
    files = _airdata_files()
    pad = max(0.35, 0.3 * max(sb[2] - sb[0], sb[3] - sb[1]))
    box = [sb[0] - pad, sb[1] - pad, sb[2] + pad, sb[3] + pad]
    want = _study_year(s.scope) or dt.date.today().year
    a = re.match(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", str(s.scope.get("time_start") or ""))
    b = re.match(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", str(s.scope.get("time_end") or ""))
    layers, notes, rows_out, series = [], [], [["site", "name", "pollutant", "date", "daily_value", "units"]], None
    for n, (_words, code, pname, limit, stat, duration) in enumerate(_pollutants(_scope_text(s))):
        years = sorted(int(f[len(f"daily_{code}_"):-4]) for f in files if f.startswith(f"daily_{code}_") and f[-8:-4].isdigit())
        full = [y for y in years if y < dt.date.today().year] or years
        if not full:
            continue
        year = max([y for y in full if y <= want] or [min(full)])
        fname = f"daily_{code}_{year}.zip"
        if files.get(fname, 0) * 1024 > MAX_RAW_BYTES:
            notes.append(f"{pname} {year} file too large")
            continue
        # the study window inside that year (whole year when the window does not touch it)
        d0 = f"{a.group(1)}-{a.group(2) or '01'}-{a.group(3) or '01'}" if a and int(a.group(1)) == year else f"{year}-01-01"
        d1 = f"{b.group(1)}-{b.group(2) or '12'}-{b.group(3) or '31'}" if b and int(b.group(1)) == year else f"{year}-12-31"
        d = downloads.session_dir(s.sid)
        d.mkdir(parents=True, exist_ok=True)
        raw = d / f"pv-{fname}"
        try:
            downloads.fetch(f"{AIRDATA}/{fname}", raw, MAX_RAW_BYTES)
            sites: dict[str, dict] = {}
            with zipfile.ZipFile(raw) as zf:
                member = next(i for i in zf.infolist() if i.filename.endswith(".csv"))
                with zf.open(member) as fh:
                    rd = csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"))
                    head = next(rd)
                    ix = {h: i for i, h in enumerate(head)}
                    for r in rd:
                        try:
                            lat, lon = float(r[ix["Latitude"]]), float(r[ix["Longitude"]])
                        except (ValueError, IndexError):
                            continue
                        if not (box[0] <= lon <= box[2] and box[1] <= lat <= box[3]):
                            continue
                        day = r[ix["Date Local"]]
                        if not (d0 <= day <= d1):
                            continue
                        if duration and duration not in r[ix["Sample Duration"]].upper():
                            continue  # e.g. ozone: the 8-hour running averages, not the 1-hour rows
                        try:
                            v = float(r[ix["Arithmetic Mean" if stat == "mean" else "1st Max Value"]])
                        except ValueError:
                            continue
                        sid = f"{r[ix['State Code']]}-{r[ix['County Code']]}-{r[ix['Site Num']]}"
                        st = sites.setdefault(sid, {"lat": lat, "lon": lon, "name": r[ix["Local Site Name"]] or r[ix["City Name"]],
                                                    "units": r[ix["Units of Measure"]], "days": {}})
                        st["days"].setdefault(day, []).append(v)  # several instruments (POCs) on one day: averaged
        finally:
            raw.unlink(missing_ok=True)
        if not sites:
            notes.append(f"no {pname} monitors near the study area in {year}")
            continue
        feats, vals = [], []
        for sid, st in sites.items():
            daily = {k: (sum(v) / len(v) if stat == "mean" else max(v)) for k, v in st["days"].items()}
            mean = sum(daily.values()) / len(daily)
            inside = sb[0] <= st["lon"] <= sb[2] and sb[1] <= st["lat"] <= sb[3]
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [st["lon"], st["lat"]]},
                          "properties": {"site": sid, "name": st["name"], f"mean_{pname}": round(mean, 3 if mean < 1 else 1),
                                         "days_measured": len(daily), "days_above_standard": sum(1 for x in daily.values() if x > limit),
                                         "units": st["units"], "in_study_area": inside}})
            vals.append(mean)
            for k in sorted(daily):
                rows_out.append([sid, st["name"], pname, k, round(daily[k], 4), st["units"]])
        units = next(iter(sites.values()))["units"].split("(")[0].strip()
        what = "24-hour mean" if stat == "mean" else f"daily max {duration.lower().replace('-hr', '-hour').replace(' hour', '-hour')}"
        lab = f"{pname}, average {what}, {d0[:7]} to {d1[:7]} ({units})"
        layer, _note = _vector_layer(s, pick, feats, f"EPA AirData: {pname} monitors, {year}", "EPA AirData daily file (downloaded for this session)",
                                     _paint(f"mean_{pname}", vals, ("reds", "purples")[n % 2], lab), "AirData preview")
        if layer:
            if n:
                layer["hidden"] = True
            layers.append(layer)
            notes.append(f"{pname} {year}: {len(sites)} monitors")
            if not n and HOUR_WORDS.search(_scope_text(s)):  # the question is about hours: the daily cycle
                inside = {k: v for k, v in sites.items() if sb[0] <= v["lon"] <= sb[2] and sb[1] <= v["lat"] <= sb[3]} or sites
                hourly, hnote = _aqs_hourly(s, code, pname, year, inside, d0, d1)
                if hourly:
                    series = hourly
                    notes.append(hnote)
    if not layers:
        return {"status": "none", "note": "; ".join(notes) or "No AirData file for this window"}
    return {"status": "ready", "kind": "slice", "layers": layers, "csv": _csv(s, pick, rows_out), "series": series,
            "note": "; ".join(notes) + (" (EPA daily and hourly values, downloaded for this session)" if series else " (EPA daily values, downloaded for this session)")}


# ---------------------------------------------------------------------------- non-spatial tables
def _towns(s) -> dict[str, dict]:
    """Town (county subdivision) shapes in and around the study area, keyed by lower-case name."""
    sb = s.scope.get("bbox")
    if not sb:
        return {}
    fc = _retry(lambda: sources.arcgis_query(f"{sources.TIGER}/22", sb, max_features=3000,
                                             simplify_deg=max(0.0001, min(0.003, (sb[2] - sb[0]) / 4000))))
    out = {}
    for f in fc.get("features", []):
        p = f.get("properties") or {}
        name = str(p.get("BASENAME") or p.get("NAME") or "").strip()
        if name:
            out[name.lower()] = {"type": "Feature", "geometry": f["geometry"], "properties": {"name": name}}
    return out


def _table_result(s, pick, meta, raw: bytes, fname: str, src: str):
    """A downloaded table: points if it has coordinates, a joined map if it has place codes or town names,
    otherwise a table preview (and a trend line when it has years). Always a CSV of what was read."""
    low = fname.lower()
    if low.endswith((".csv", ".tsv", ".tab", ".txt")):
        try:
            feats = downloads._csv(raw)
            layer, note = _vector_layer(s, pick, feats, short_title(meta.get("title") or pick.get("title") or fname), src)
            if layer:
                return {"status": "ready", "kind": "slice", "layers": [layer], "note": note or f"{layer['n']} points"}
        except downloads.DownloadError:
            pass  # no coordinates: try place codes next
    joined = _table_layers(s, pick, meta, raw, fname if not low.endswith(".tab") else fname[:-4] + ".csv", src)
    if joined:
        return joined
    tbls = tables.read_tables(raw, fname if not low.endswith((".tab", ".tsv")) else fname.rsplit(".", 1)[0] + ".csv")
    tname, rows = max(tbls, key=lambda t: len(t[1]), default=("", []))
    if len(rows) < 2:
        return None
    h = tables._header_row(rows)
    header, body = rows[h], rows[h + 1:]
    if len(body) < 3 or len(header) < 2:
        return None  # an empty or one-line file (a tracking pixel, a stub) is not a preview
    towns = _towns(s) if any(tables.TOWN_COLUMN.match(c.strip()) for c in header) else {}
    if towns:
        tc = tables.detect_town(header, body, set(towns))
        if tc is not None:
            nodes = {n["id"]: n.get("label", "") for n in (s.graph or {}).get("nodes", [])}
            text = " ".join([_scope_text(s), " ".join(nodes.get(f, "") for f in pick.get("factors") or []), pick.get("why") or ""])
            years = tables.year_columns(header)
            end = _study_year(s.scope) or 9999
            cols = ([max((j for y, j in years if y <= end), default=years[-1][1])] if years
                    else tables.value_columns(header, body, tc, {}, text, k=1))
            in_towns = [r for r in body if len(r) > tc and r[tc].strip().lower() in towns]
            per_town = len(in_towns) / max(1, len({r[tc].strip().lower() for r in in_towns}))
            # one row per record (a solar system, a death, a sale): count rows per town unless a column matches the question
            records = not years and per_town >= 3
            count = records and (not cols or not (tables._tokens(text) & tables._tokens(header[cols[0]])))
            if cols or count:
                c = None if count else cols[0]
                prop = "rows per town" if count else header[c]
                agg: dict[str, list[float]] = {}
                for r in in_towns:
                    v = 1.0 if count else (tables._num(r[c]) if len(r) > c else None)
                    if v is not None:
                        agg.setdefault(r[tc].strip().lower(), []).append(v)
                how = (lambda v: float(len(v))) if count else ((lambda v: float(statistics.median(v))) if records else (lambda v: sum(v) / len(v)))
                feats = [{"type": "Feature", "geometry": towns[t]["geometry"],
                          "properties": {"town": towns[t]["properties"]["name"], prop: round(how(v), 4), "rows": len(v)}}
                         for t, v in agg.items()]
                label = ("Rows per town (one row per record in this table)" if count
                         else f"{header[c].strip('_ ')} ({'median ' if records else ''}per town)")
                layer, _n = _vector_layer(s, pick, feats, f"{short_title(meta.get('title') or '')}: {'rows per town' if count else header[c].strip('_ ')}"[:100], src,
                                          _paint(prop, [f["properties"][prop] for f in feats], "blues", label), "table preview")
                if layer:
                    return {"status": "ready", "kind": "slice", "layers": [layer], "csv": _csv(s, pick, [header] + body[:50000]),
                            "table": tables.preview(header, body), "series": tables.wide_series(header, in_towns, "Towns in the area"),
                            "note": f"{fname}: {len(feats)} towns, " + ("rows per town (one row per record)" if count
                                                                         else f"{header[c].strip('_ ')}{' (median per town)' if records else ''}")}
    nodes = {n["id"]: n.get("label", "") for n in (s.graph or {}).get("nodes", [])}
    text = " ".join([_scope_text(s), " ".join(nodes.get(f, "") for f in pick.get("factors") or []), pick.get("why") or ""])
    vc = tables.value_columns(header, body, -1, tables.dictionary(tbls), text, k=1)
    trend = tables.wide_series(header, body) or (tables.series(header, body, vc[0]) if vc else None)
    return {"status": "ready", "kind": "table", "layers": [], "csv": _csv(s, pick, [header] + body[:50000]),
            "table": tables.preview(header, body), "series": trend,
            "note": f"{fname}: {len(body):,} rows, {len(header)} columns (a table without places to map)"}


def _tag(pick) -> str:
    """A short per-card prefix for temporary files: two cards can point at the same file."""
    import hashlib
    return hashlib.sha1(str(pick.get("ref")).encode()).hexdigest()[:6] + "-"


def _download_table(s, pick, meta, url: str, fname: str, src: str):
    d = downloads.session_dir(s.sid)
    d.mkdir(parents=True, exist_ok=True)
    raw_path = d / ("pv-" + _tag(pick) + re.sub(r"[^a-z0-9.]+", "-", fname.lower())[-60:])
    try:
        downloads.fetch(url, raw_path, MAX_RAW_BYTES)
        return _table_result(s, pick, meta, raw_path.read_bytes(), fname, src)
    finally:
        raw_path.unlink(missing_ok=True)


def soda_from_url(url: str) -> str:
    """The SODA CSV address of a Socrata dataset page (data.ct.gov/d/rybz-nyjw, or /Category/Name/rybz-nyjw)."""
    m = re.match(r"^https://([a-z0-9-]+(?:\.[a-z0-9-]+)+)/(?:d/|resource/|[^/?#]+/[^/?#]+/)([a-z0-9]{4}-[a-z0-9]{4})(?:[/?#.]|$)", url or "")
    return f"https://{m.group(1)}/resource/{m.group(2)}.csv" if m else ""


def _socrata(s, pick, meta):
    """A table on a Socrata open data portal through the SODA API. With a date column and a study window: an even
    sample over the years of the window (the first 50,000 rows can all come from one year) and the portal's count
    of every row per year; otherwise its first 50,000 rows."""
    soda = meta.get("soda") or soda_from_url(meta.get("url") or pick.get("url") or "")
    if not re.fullmatch(r"https://[a-z0-9.-]+/resource/[a-z0-9]{4}-[a-z0-9]{4}\.csv", soda):
        return None
    dom, rid = soda.split("/")[2], soda.rsplit("/", 1)[-1][:-4]
    src = f"{dom} open data (downloaded for this session)"
    y0, y1 = (re.match(r"\d{4}", str(s.scope.get(k) or "")) for k in ("time_start", "time_end"))
    dcol, counts = None, {}
    if y0 and y1 and sources.safe_url(f"https://{dom}/api/views/{rid}.json"):
        try:
            cols = requests.get(f"https://{dom}/api/views/{rid}.json", headers=UA, timeout=25).json().get("columns") or []
            dates = [c["fieldName"] for c in cols if c.get("dataTypeName") in ("calendar_date", "floating_timestamp") and not c["fieldName"].startswith(":")]
            dcol = next((c for c in dates if "date" in c.lower()), dates[0] if dates else None)
            if dcol:
                j = requests.get(soda[:-4] + ".json", params={"$select": f"date_extract_y({dcol}) AS y, count(*) AS n", "$group": "y", "$order": "y"},
                                 headers=UA, timeout=40).json()
                counts = {int(float(r["y"])): int(float(r["n"])) for r in j if r.get("y") not in (None, "")}
        except (requests.RequestException, ValueError, KeyError, TypeError):
            dcol, counts = None, {}
    yrs = [y for y in sorted(counts) if int(y0.group()) <= y <= int(y1.group()) and counts[y] > 0] if counts else []
    if dcol and len(yrs) >= 2 and sum(counts[y] for y in yrs) > 50000:
        per = max(1000, 50000 // len(yrs))
        d = downloads.session_dir(s.sid)
        d.mkdir(parents=True, exist_ok=True)
        header, body = None, []
        for y in yrs:
            raw_path = d / f"pv-{_tag(pick)}{rid}-{y}.csv"
            q = requests.models.PreparedRequest()
            q.prepare_url(soda, {"$where": f"{dcol} >= '{y}-01-01T00:00:00' AND {dcol} < '{y + 1}-01-01T00:00:00'", "$limit": per})
            try:
                downloads.fetch(q.url, raw_path, MAX_RAW_BYTES)
                rows = list(csv.reader(io.StringIO(raw_path.read_text(encoding="utf-8", errors="replace"))))
            finally:
                raw_path.unlink(missing_ok=True)
            if rows:
                header = header or rows[0]
                body += rows[1:]
        if header and body:
            buf = io.StringIO()
            csv.writer(buf).writerows([header] + body)
            res = _table_result(s, pick, meta, buf.getvalue().encode("utf-8"), f"{rid}.csv", src)
            if res:
                res["series"] = {"label": f"All records per year in {short_title(meta.get('title') or pick.get('title') or rid)} ({dom})",
                                 "points": [[y, counts[y]] for y in yrs]}
                res["note"] = (res.get("note") or "") + (f"; an even sample of up to {per:,} rows per year, {yrs[0]}-{yrs[-1]}; "
                                                         f"the trend counts all {sum(counts[y] for y in yrs):,} rows on the portal")
                res["soda"] = {"url": soda, "date": dcol, "from": yrs[0], "to": yrs[-1]}
                return res
    res = _download_table(s, pick, meta, soda + "?$limit=50000", soda.rsplit("/", 1)[-1], src)
    if res and dcol and yrs:
        res["series"] = {"label": f"All records per year in {short_title(meta.get('title') or pick.get('title') or rid)} ({dom})",
                         "points": [[y, counts[y]] for y in yrs]}
        res["soda"] = {"url": soda, "date": dcol, "from": yrs[0], "to": yrs[-1]}
    return res


DV_HOSTS = ("dataverse.harvard.edu", "dataverse.yale.edu")
DV_PREFIX = {"10.7910/": "dataverse.harvard.edu", "10.60600/": "dataverse.yale.edu"}
_DV_COLLECTION = re.compile(r"^https://(dataverse\.(?:harvard|yale)\.edu)/dataverse/([\w-]+)/?(?:[?#].*)?$")
_STOP = set("""the and for with from that this which what where when how does did into over under between than then their there
about across after before during within without whose would could should being been were they them these those over more most
much many each some only also data study area areas year years change changed show""".split())


def _dv_host(doi: str, default: str) -> str:
    """A DOI names the repository that holds the files; Yale Dataverse also lists harvested Harvard datasets."""
    for prefix, host in DV_PREFIX.items():
        if doi[4:].startswith(prefix):
            return host
    return default


def _question_terms(s, extra: str = "", k: int = 6) -> str:
    words = re.findall(r"[a-z][a-z\-]{3,}", (_scope_text(s) + " " + extra).lower())
    out = []
    for w in words:
        if w not in _STOP and w not in out:
            out.append(w)
    return " OR ".join(out[:k])


def _join_dataset_table(s, pick, meta, r, host, tables_):
    """Join a table from the same dataset to its map file by a shared ID column; the value is summed over the study
    years per place (a yearly table becomes one value per place plus a trend)."""
    lay = next((l for l in r.get("layers", []) if l.get("kind") == "geojson"), None)
    path = session.layer_path(lay["id"]) if lay else None
    if not path:
        return None
    import json as _json
    feats = _json.loads(path.read_text(encoding="utf-8")).get("features", [])
    props = [f.get("properties") or {} for f in feats]
    norm = lambda v: re.sub(r"\.0+$", "", str(v).strip())
    for fid, name in tables_[:2]:
        d = downloads.session_dir(s.sid)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / ("pv-" + _tag(pick) + "join-" + re.sub(r"[^a-z0-9.]+", "-", name.lower())[-50:])
        try:
            downloads.fetch(f"https://{host}/api/access/datafile/{fid}?format=original", tmp, MAX_RAW_BYTES)
            tbls = tables.read_tables(tmp.read_bytes(), name if name.lower().endswith((".csv", ".xlsx")) else name.rsplit(".", 1)[0] + ".csv")
        except (downloads.DownloadError, tables.TableError, ValueError, OSError, zipfile.BadZipFile, csv.Error):
            continue
        finally:
            tmp.unlink(missing_ok=True)
        tname, rows = max(tbls, key=lambda t: len(t[1]), default=("", []))
        if len(rows) < 4:
            continue
        hi = tables._header_row(rows)
        header, body = rows[hi], rows[hi + 1:]
        # the shared key: a table column whose values match one map attribute (IDs or names, never years)
        years_ = lambda vs: bool(vs) and sum(bool(re.fullmatch(r"1\d{3}|20\d\d", v)) for v in vs) >= 0.8 * len(vs)
        best = None
        for j, col in enumerate(header):
            vals = {norm(r[j]) for r in body if len(r) > j and r[j].strip()}
            if not 2 <= len(vals) <= 5000 or years_(vals) or (len(body) > 50 and len(vals) > 0.9 * len(body)):
                continue  # years and per-row serial numbers are not place keys
            for k in (props[0].keys() if props else []):
                mv = {norm(p.get(k)) for p in props if p.get(k) not in (None, "")}
                if not mv or years_(mv) or len(mv) > 0.9 * len(props) and len(props) > 50:
                    continue  # a per-record serial number is not a place key
                hit = len(vals & mv)
                score = hit + (2 if re.search(r"id|code|key|name|nombre|caja", col + k, re.I) else 0)
                if hit >= max(2, 0.6 * len(mv)) and (best is None or score > best[0]):
                    best = (score, j, k)
        if not best:
            continue
        _hit, kj, kprop = best
        yc = next((j for j, h in enumerate(header) if re.fullmatch(r"(year|anio|año|start_?|ano|yr)", h.strip().lower())), None)
        nodes = {n["id"]: n.get("label", "") for n in (s.graph or {}).get("nodes", [])}
        text = " ".join([_scope_text(s), " ".join(nodes.get(f, "") for f in pick.get("factors") or [])])
        vcols = tables.value_columns(header, body, kj, tables.dictionary(tbls), text, k=8)
        yearlike = lambda c: all(1400 <= (tables._num(r[c]) or 0) <= 2100 for r in body[:300] if len(r) > c and r[c].strip())
        vcols = [c for c in vcols if c != yc and not yearlike(c) and not re.search(r"diferencia|difference|change|deviaci|desv|std|tendencia|trend|promedio|average|mean", header[c], re.I)]
        if not vcols:
            continue
        vc = vcols[0]
        lo, hi_ = (re.match(r"\d{4}", str(s.scope.get(k) or "")) for k in ("time_start", "time_end"))
        lo, hi_ = (int(lo.group()) if lo else -9999), (int(hi_.group()) if hi_ else 9999)
        sums, years, by_year = {}, {}, {}
        for row in body:
            if len(row) <= max(kj, vc):
                continue
            v = tables._num(row[vc])
            y = None
            if yc is not None and len(row) > yc:
                m = re.match(r"(1[5-9]\d\d|20\d\d)", row[yc].strip())
                y = int(m.group(1)) if m else None
            if v is None or (y is not None and not lo <= y <= hi_):
                continue
            key = norm(row[kj])
            sums[key] = sums.get(key, 0.0) + v
            if y is not None:
                years.setdefault(key, set()).add(y)
                by_year[(key, y)] = by_year.get((key, y), 0.0) + v
        if not sums:
            continue
        out, seen = [], set()
        vname = header[vc].strip()
        for f, p in zip(feats, props):
            key = norm(p.get(kprop))
            if key in sums and key not in seen:
                seen.add(key)
                nm = next((p.get(x) for x in p if re.search(r"label|name|nombre|caja", x, re.I) and isinstance(p.get(x), str) and p.get(x).strip()), key)
                ys = sorted(years.get(key, []))
                out.append({"type": "Feature", "geometry": f["geometry"],
                            "properties": {"name": nm, kprop: p.get(kprop), f"{vname}_total": round(sums[key], 2),
                                           "years_with_data": len(ys), "first_year": ys[0] if ys else None, "last_year": ys[-1] if ys else None}})
        if len(out) < 2:
            continue
        mapped = {norm(f["properties"].get(kprop)) for f in out}
        totals: dict[int, float] = {}
        for (key, y), v in by_year.items():
            if key in mapped:  # the trend covers the places on the map, not the whole table
                totals[y] = totals.get(y, 0.0) + v
        by_year = totals
        span = f"{max(lo, min(by_year))}-{min(hi_, max(by_year))}" if by_year else ""
        vals = [f["properties"][f"{vname}_total"] for f in out]
        new, _n = _vector_layer(s, pick, out, f"{short_title(meta.get('title') or pick.get('title') or name)}: {vname} {span}".strip()[:100],
                                f"{host} files {name} and the map file, joined by {header[kj].strip()}",
                                _paint(f"{vname}_total", vals, "reds", f"{vname}, total {span}".strip()), "joined preview")
        if not new:
            continue
        res = {"status": "ready", "kind": "slice", "layers": [new],
               "csv": _csv(s, pick, [header] + body[:50000]), "table": tables.preview(header, body),
               "note": f"{name}: {vname} summed per place{(' over ' + span) if span else ''} for {len(out)} places, joined to the map file by {header[kj].strip()}"}
        if len(by_year) >= 3:
            res["series"] = {"label": f"{vname} per year, the {len(out)} places on the map", "points": [[y, round(v, 2)] for y, v in sorted(by_year.items())][-120:]}
        return res
    return None


def _dataverse(s, pick, meta):
    """A Dataverse dataset (by DOI) or collection (by its page): the best public file under 100 MB. Map files
    (zipped shapefile, GeoJSON, KML) come before tables; a collection is searched with the words of the question."""
    doi = meta.get("doi") or ""
    if doi.startswith("doi:"):
        host = _dv_host(doi, meta.get("dataverse_host") or "dataverse.harvard.edu")
        return _dataverse_dataset(s, pick, meta, doi, host) if host in DV_HOSTS else None
    m = _DV_COLLECTION.match(meta.get("url") or pick.get("url") or "")
    if not m:
        return None
    host, alias = m.group(1), m.group(2)
    q = _question_terms(s, " ".join(str(x) for x in (pick.get("why"), meta.get("keywords")) if x)) or "*"
    j = requests.get(f"https://{host}/api/search", params={"q": q, "subtree": alias, "type": "dataset", "per_page": 5},
                     headers=UA, timeout=30).json()
    items = ((j.get("data") or {}).get("items")) or []
    last = None
    for it in items[:3]:
        gid = it.get("global_id") or ""
        if not gid.startswith("doi:"):
            continue
        r = _dataverse_dataset(s, pick, meta, gid, _dv_host(gid, host))
        if r and r.get("status") == "ready":
            r["note"] = f"{(it.get('name') or '').removeprefix('Data: ')[:70]}: {r.get('note', '')}"
            return r
        last = r
    return last or {"status": "none", "note": "No dataset in this collection matched the question"}


def _dataverse_dataset(s, pick, meta, doi: str, host: str):
    j = requests.get(f"https://{host}/api/datasets/:persistentId/", params={"persistentId": doi}, headers=UA, timeout=30).json()
    if j.get("status") != "OK" and (j.get("status") or j.get("message")):
        other = next(h for h in DV_HOSTS if h != host)  # a harvested copy: the files live in the other repository
        j2 = requests.get(f"https://{other}/api/datasets/:persistentId/", params={"persistentId": doi}, headers=UA, timeout=30).json()
        if j2.get("data"):
            j, host = j2, other
    files = (((j.get("data") or {}).get("latestVersion") or {}).get("files")) or []
    cands = []
    for f in files:
        df = f.get("dataFile") or {}
        name = df.get("originalFileName") or df.get("filename") or ""
        size = df.get("originalFileSize") or df.get("filesize") or 0
        low, ctype = name.lower(), (df.get("contentType") or "").lower()
        spatial = low.endswith((".geojson", ".kml", ".kmz")) or "shapefile" in ctype
        table = low.endswith((".csv", ".tab", ".tsv", ".xlsx"))
        if f.get("restricted") or size > MAX_RAW_BYTES or not (spatial or table or low.endswith(".zip")):
            continue
        cands.append((2 if spatial else 1 if table else 0, -size, df.get("id"), name))
    if not cands:
        return {"status": "none", "note": "No public map or table file under 100 MB in this dataset"}
    cands.sort(reverse=True)
    tables_ = [(fid, name) for kind, _sz, fid, name in cands if kind == 1]
    for kind, _sz, fid, name in cands[:2]:
        url = f"https://{host}/api/access/datafile/{fid}?format=original"
        if kind == 2:
            d = downloads.session_dir(s.sid)
            d.mkdir(parents=True, exist_ok=True)
            raw_path = d / ("pv-" + _tag(pick) + re.sub(r"[^a-z0-9.]+", "-", name.lower())[-60:])
            try:
                downloads.fetch(url, raw_path, MAX_RAW_BYTES)
                r = _read_raw(s, pick, meta, raw_path, name, host)
            except (downloads.DownloadError, tables.TableError, ValueError, KeyError, OSError, zipfile.BadZipFile):
                r = None
            finally:
                raw_path.unlink(missing_ok=True)
            if r and r.get("status") == "ready" and tables_:  # the values often sit in a table beside the map file
                joined = _join_dataset_table(s, pick, meta, r, host, tables_)
                if joined:
                    return joined
        else:
            r = _download_table(s, pick, meta, url, name, f"{host} file {name} (downloaded for this session)")
        if r and r.get("status") == "ready":
            return r
    return {"status": "none", "note": "The dataset's files could not be drawn or tabulated"}


def _in_ring(x, y, ring) -> bool:
    inside = False
    for i in range(len(ring) - 1):
        (x1, y1), (x2, y2) = ring[i][:2], ring[i + 1][:2]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
            inside = not inside
    return inside


def _contains(geom: dict, x: float, y: float) -> bool:
    polys = [geom["coordinates"]] if geom.get("type") == "Polygon" else geom.get("coordinates", []) if geom.get("type") == "MultiPolygon" else []
    return any(p and _in_ring(x, y, p[0]) and not any(_in_ring(x, y, h) for h in p[1:]) for p in polys)


def _countries_at(sb, shapes: dict, keep) -> list[str]:
    """Countries containing the study area's centre and a grid of points inside it, most points first (at most two)."""
    pts = [(sb[0] + (sb[2] - sb[0]) * fx, sb[1] + (sb[3] - sb[1]) * fy) for fx in (0.5, 0.25, 0.75) for fy in (0.5, 0.25, 0.75)]
    count: dict[str, int] = {}
    for iso, f in shapes.items():
        if iso not in keep:
            continue
        fb = geojson_bbox(f["geometry"])
        if not fb or fb[2] < sb[0] or fb[0] > sb[2] or fb[3] < sb[1] or fb[1] > sb[3]:
            continue
        n = sum(_contains(f["geometry"], x, y) for x, y in pts)
        if n:
            count[iso] = n
    return [iso for iso, _n in sorted(count.items(), key=lambda kv: -kv[1])[:2]]


def _worldbank(s, pick, meta):
    """A World Development Indicator: latest value per country in the study window, and the trend for the study's countries."""
    iid = meta.get("wb_indicator") or ""
    if not re.fullmatch(r"[A-Za-z0-9._]{3,40}", iid):
        return None
    a = re.match(r"\d{4}", str(s.scope.get("time_start") or ""))
    b = re.match(r"\d{4}", str(s.scope.get("time_end") or ""))
    y1 = min(int(b.group()) if b else dt.date.today().year, dt.date.today().year)
    y0 = int(a.group()) if a else y1 - 15
    y0 = min(y0, y1 - 4)  # at least five years for a trend
    r = requests.get(f"https://api.worldbank.org/v2/country/all/indicator/{iid}",
                     params={"format": "json", "date": f"{y0}:{y1}", "per_page": 20000}, headers=UA, timeout=40)
    r.raise_for_status()
    data = r.json()
    rows = data[1] if isinstance(data, list) and len(data) > 1 and data[1] else []
    shapes = tables.countries()
    latest: dict[str, tuple[int, float]] = {}
    by_country: dict[str, list] = {}
    for x in rows:
        iso, v = x.get("countryiso3code"), x.get("value")
        if iso not in shapes or v is None or not str(x.get("date", "")).isdigit():
            continue
        yr = int(x["date"])
        by_country.setdefault(iso, []).append([yr, v])
        if iso not in latest or yr > latest[iso][0]:
            latest[iso] = (yr, v)
    if not latest:
        return {"status": "none", "note": f"No World Bank values for {y0} to {y1}"}
    name = meta.get("title") or iid
    feats = [{"type": "Feature", "geometry": shapes[k]["geometry"],
              "properties": {"iso": k, "name": shapes[k]["properties"]["name"], "value": v, "year": yr}} for k, (yr, v) in latest.items()]
    layer, _n = _vector_layer(s, pick, feats, f"World Bank: {short_title(name)}"[:100], "World Bank WDI (API)",
                              _paint("value", [f["properties"]["value"] for f in feats], "greens", f"{name}, latest year in {y0} to {y1}"),
                              "World Bank preview")
    sb = s.scope.get("bbox")
    near = _countries_at(sb, shapes, by_country) if sb else []  # the study's countries get a trend line in the card
    series = [{"label": shapes[iso]["properties"]["name"], "points": sorted(by_country[iso])} for iso in near]
    rows_out = [["iso", "country", "year", "value"]] + [[k, shapes[k]["properties"]["name"], yr, v]
                                                       for k, pts in by_country.items() for yr, v in sorted(pts)]
    res = {"status": "ready", "kind": "slice", "layers": [layer] if layer else [], "csv": _csv(s, pick, rows_out),
           "note": f"{len(latest)} countries, {y0} to {y1}"}
    if series:
        res["series"] = series[0] if len(series) == 1 else {"label": series[0]["label"], "points": series[0]["points"],
                                                            "others": series[1:]}
    return res


def _gadm(s, pick, meta):
    """GADM boundaries for the country the study area is in: provinces (districts for a small study area)."""
    sb = s.scope.get("bbox")
    if not sb:
        return {"status": "none", "note": "Set a study area to preview boundaries"}
    shapes = tables.countries()
    isos = _countries_at(sb, shapes, shapes)
    if not isos:
        return {"status": "none", "note": "Could not tell which country the study area is in"}
    iso, (w, h) = isos[0], bbox_size_km(sb)
    for level in ((2, 1) if w * h < 60000 else (1,)):
        url = f"https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_{iso}_{level}.json"
        d = downloads.session_dir(s.sid)
        d.mkdir(parents=True, exist_ok=True)
        raw = d / f"pv-gadm-{iso}-{level}.json"
        try:
            downloads.fetch(url, raw, MAX_RAW_BYTES)
            feats = downloads._geojson(raw.read_bytes())
        except downloads.DownloadError:
            continue
        finally:
            raw.unlink(missing_ok=True)
        unit = {1: "provinces or states", 2: "districts or counties"}[level]
        name = shapes[iso]["properties"]["name"]
        layer, note = _vector_layer(s, pick, feats, f"GADM {unit}: {name}", "GADM 4.1 (free for academic and non-commercial use)",
                                    None, "GADM preview")
        if layer:
            layer["style"] = "boundary"
            return {"status": "ready", "kind": "slice", "layers": [layer],
                    "note": note or f"{layer['n']} {unit} of {name} (level {level})"}
    return {"status": "none", "note": "GADM did not answer for this country"}


# ---------------------------------------------------------------------------- Earth Engine data with an open copy
# Earth Engine needs an account; many of its collections are also on Microsoft Planetary Computer, which needs none.
GEE_MIRRORS = [(re.compile(r"^COPERNICUS/S2(_SR)?(_HARMONIZED)?$"), "sentinel-2-l2a"),
               (re.compile(r"^LANDSAT/L[COTE]0[4-9]/C02/T1_L2$"), "landsat-c2-l2"),
               (re.compile(r"^NASA/HLS/HLSS30/"), "hls2-s30"), (re.compile(r"^NASA/HLS/HLSL30/"), "hls2-l30"),
               (re.compile(r"^COPERNICUS/S1_GRD"), "sentinel-1-rtc"),
               (re.compile(r"^ESA/WorldCover/"), "esa-worldcover"),
               (re.compile(r"^USGS/3DEP/(10m|1m)"), "3dep-seamless"),
               (re.compile(r"^COPERNICUS/DEM/GLO30"), "cop-dem-glo-30"), (re.compile(r"^NASA/NASADEM_HGT/"), "nasadem"),
               (re.compile(r"^JAXA/ALOS/AW3D30/"), "alos-dem"),
               (re.compile(r"^JRC/GSW1_\d/GlobalSurfaceWater"), "jrc-gsw"),
               (re.compile(r"^USDA/NAIP/DOQQ"), "naip"), (re.compile(r"^USDA/NASS/CDL"), "usda-cdl"),
               (re.compile(r"^USFS/GTAC/MTBS/"), "mtbs")]
_MODIS = re.compile(r"^MODIS/061/M[OYC]D(\w+)$")


def gee_mirror(ref: str) -> str | None:
    """The Planetary Computer collection holding the same data as an Earth Engine collection, if there is one."""
    if not ref.startswith("gee:"):
        return None
    gid = ref[4:]
    for rx, coll in GEE_MIRRORS:
        if rx.search(gid):
            return coll
    m = _MODIS.match(gid)
    if m and catalog_mod.load().get(f"pc:modis-{m.group(1)}-061"):
        return f"modis-{m.group(1)}-061"
    return None


MAP_WMS = "https://data.malariaatlas.org/geoserver/Malaria/wms"
MAP_LAYERS = [(r"vivax|\bpv\b", "Malaria:202406_Global_Pv_Parasite_Rate", "Plasmodium vivax parasite rate"),
              (r"incidence|cases", "Malaria:202406_Global_Pf_Incidence_Rate", "Plasmodium falciparum incidence rate"),
              (r".", "Malaria:202406_Global_Pf_Parasite_Rate", "Plasmodium falciparum parasite rate (PfPR, ages 2-10)")]


def _malaria_atlas(s, pick, meta):
    """A Malaria Atlas Project page: MAP's own WMS map of the modelled parasite rate (2024 release)."""
    url = meta.get("url") or pick.get("url") or pick.get("ref") or ""
    if not re.match(r"^https://(data\.)?malariaatlas\.org/", url):
        return None
    text = _scope_text(s) + " " + (pick.get("why") or "").lower()
    name, title = next((n, t) for rx, n, t in MAP_LAYERS if re.search(rx, text))
    tiles = (f"{MAP_WMS}?service=WMS&request=GetMap&version=1.1.1&layers={name}&styles=&format=image/png&transparent=true"
             "&srs=EPSG:3857&width=256&height=256&bbox={bbox-epsg-3857}")
    legend = f"{MAP_WMS}?service=WMS&request=GetLegendGraphic&format=image/png&layer={name}"
    layer = {"id": "pv-map-" + re.sub(r"[^a-z0-9]", "", name.lower())[-24:], "kind": "raster", "title": f"Malaria Atlas Project: {title}",
             "source": "Malaria Atlas Project (WMS)", "tiles": [tiles], "bbox": None, "minzoom": 0, "maxzoom": 12, "legend": legend}
    return {"status": "ready", "kind": "service", "layers": [layer], "note": f"Live MAP map service: {title}, 2024 release (latest modelled year)"}


def _gee(s, pick, meta):
    coll = gee_mirror(pick["ref"])
    pc = catalog_mod.load().get("pc:" + coll) if coll else None
    if not pc:
        return None
    r = _stac(s, pick, pc)
    if r and r.get("status") == "ready":
        r["note"] = "From Planetary Computer, which needs no account (Earth Engine does): " + (r.get("note") or "")
        return r
    return None


OSM_TAGS = [  # words in the pick's factors -> OpenStreetMap tags, and the largest area worth asking for (km2)
    (r"village|settlement|hamlet|communit|household|town", ["place=village", "place=hamlet", "place=town"], 60000),
    (r"school", ["amenity=school"], 10000),
    (r"clinic|hospital|health (facilit|care|centre|center)", ["amenity=hospital", "amenity=clinic", "healthcare"], 10000),
    (r"drain|sewer|canal|ditch|culvert|channel", ["waterway=drain", "waterway=ditch", "waterway=canal", "waterway=stream"], 2500),
    (r"water|pond|wetland|lake", ["natural=water", "natural=wetland"], 2500),
    (r"park|green ?space", ["leisure=park"], 2500),
    (r"road|street|highway", ["highway=primary", "highway=secondary", "highway=tertiary"], 2500),
    (r"building|roof|house", ["building"], 100),
]


def _osm(s, pick, meta):
    """OpenStreetMap features that the pick's factors name (villages, schools, clinics, water, roads), from Overpass."""
    sb = s.scope.get("bbox")
    if not sb:
        return {"status": "none", "note": "Set a study area to preview OpenStreetMap features"}
    nodes = {n["id"]: n.get("label", "") + " " + n.get("observable", "") for n in (s.graph or {}).get("nodes", [])}
    text = " ".join([nodes.get(f, "") for f in pick.get("factors") or []] + [pick.get("why") or ""]).lower()
    best = max((len(re.findall(rx, text)), -i, tags, lim) for i, (rx, tags, lim) in enumerate(OSM_TAGS))
    hit = (best[2], best[3]) if best[0] else None  # the rule with the most matching words (drains before villages)
    if not hit:
        return {"status": "none", "note": "OpenStreetMap has many feature types; ask Y-finder for the ones you need (villages, schools, roads...)"}
    tags, lim = hit
    try:
        fc = sources.osm_features(tags, sb, max_elements=4000, max_area_km2=lim, timeout_s=100)  # a background preview can wait
    except ValueError as e:
        return {"status": "none", "note": str(e)}
    feats = fc.get("features", [])
    layer, note = _vector_layer(s, pick, feats, f"OpenStreetMap: {', '.join(t.split('=')[-1] for t in tags)}", "OpenStreetMap contributors (Overpass)")
    if not layer:
        return {"status": "none", "note": note or "No such features in the study area"}
    return {"status": "ready", "kind": "service", "layers": [layer], "note": note or f"{layer['n']} OpenStreetMap features ({', '.join(tags)})"}


NWIS = "https://waterservices.usgs.gov/nwis"


def _rdb(text: str) -> list[dict]:
    rows = [line.split("\t") for line in text.splitlines() if line and not line.startswith("#")]
    if len(rows) < 3:
        return []
    head = rows[0]
    return [dict(zip(head, r)) for r in rows[2:]]  # the second line gives column widths


def _usgs_water(s, pick, meta):
    """USGS streamgages in the study area with their annual mean discharge for the study's last full year."""
    sb = s.scope.get("bbox")
    if not sb:
        return {"status": "none", "note": "Set a study area to preview streamgages"}
    end = _study_year(s.scope) or dt.date.today().year - 1
    year = min(end, dt.date.today().year - 1)
    sites: dict[str, dict] = {}
    x = sb[0]
    while x < sb[2] and len(sites) < 600:  # the site service takes boxes up to 25 square degrees
        y = sb[1]
        while y < sb[3] and len(sites) < 600:
            box = [round(x, 4), round(y, 4), round(min(x + 5, sb[2]), 4), round(min(y + 5, sb[3]), 4)]
            try:
                r = requests.get(f"{NWIS}/site/", params={"format": "rdb", "bBox": ",".join(map(str, box)), "parameterCd": "00060",
                                                         "siteType": "ST", "hasDataTypeCd": "dv", "siteStatus": "active"}, headers=UA, timeout=60)
            except requests.RequestException:
                r = None  # one slow tile should not lose the others
            if r is not None and r.status_code == 200:
                for d in _rdb(r.text):
                    try:
                        sites[d["site_no"]] = {"name": d.get("station_nm", ""), "lat": float(d["dec_lat_va"]), "lon": float(d["dec_long_va"])}
                    except (KeyError, ValueError):
                        continue
            y += 5
        x += 5
    if not sites:
        return {"status": "none", "note": "No USGS streamgages with daily discharge in the study area"}
    ids = list(sites)[:300]

    def batch(args):  # the statistics service takes at most 10 sites per request
        chunk, yr = args
        try:
            r = requests.get(f"{NWIS}/stat/", params={"format": "rdb", "sites": ",".join(chunk), "statReportType": "annual",
                                                     "statTypeCd": "mean", "parameterCd": "00060", "startDt": str(yr), "endDt": str(yr)},
                             headers=UA, timeout=60)
        except requests.RequestException:
            return {}
        out = {}
        for d in (_rdb(r.text) if r.status_code == 200 else []):
            try:
                out[d["site_no"]] = float(d["mean_va"])
            except (KeyError, ValueError):
                continue
        return out
    chunks = [ids[i:i + 10] for i in range(0, len(ids), 10)]
    means: dict[str, float] = {}
    for yr in (year, year - 1, year - 2):  # annual statistics are published some months after a year ends
        if not any(batch((c, yr)) for c in chunks[:2]):
            continue
        with cf.ThreadPoolExecutor(4) as ex:
            for got in ex.map(batch, [(c, yr) for c in chunks]):
                means.update(got)
        year = yr
        break
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [v["lon"], v["lat"]]},
              "properties": {"site": k, "name": v["name"], f"mean_flow_cfs_{year}": means.get(k)}} for k, v in sites.items()]
    vals = [m for m in means.values() if m is not None]
    paint = _paint(f"mean_flow_cfs_{year}", vals, "blues", f"Mean flow {year} (cubic feet per second)") if len(vals) >= 2 else None
    layer, note = _vector_layer(s, pick, feats, f"USGS streamgages: mean flow {year}", "USGS Water Data (waterservices.usgs.gov)", paint)
    if not layer:
        return {"status": "none", "note": note or "No streamgages in the study area"}
    rows = [["site", "name", "lat", "lon", f"mean_flow_cfs_{year}"]] + [[k, v["name"], v["lat"], v["lon"], means.get(k)] for k, v in sites.items()]
    return {"status": "ready", "kind": "service", "layers": [layer], "csv": _csv(s, pick, rows),
            "note": f"{len(sites)} streamgages; annual mean flow {year} for {len(means)} of them (USGS)"}


BY_REF = {"cur:acs": _acs, "cur:tiger": _tiger, "cur:nclimgrid": _nclimgrid, "cur:places": _places, "cur:aqs": _aqs, "cur:gadm": _gadm,
          "cur:cteco": _cteco, "cur:hms": _hms, "cur:osm": _osm, "cur:usgs_water": _usgs_water}


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
    for fn in (_geolibre, _service, BY_REF.get(pick["ref"]), _worldbank, _socrata, _dataverse, _arcgis, _stac, _gee,
               _malaria_atlas, _direct_file, _landing):
        if fn is None:
            continue
        r = fn(s, pick, meta)
        if r is not None:
            break
    else:
        r = {"status": "none", "note": _reason(pick, meta), "fallthrough": True}
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
        escalate = r.pop("fallthrough", False) | r.pop("retry_browser", False)
        handoff = escalate and r.get("status") == "none" and _browser_ok(s, pick)
        with s.plock:
            if handoff:
                s.browser_tries += 1
                n = browser.ahead()
                r = {"status": "pending", "browser": True, "sig": sig, "at": time.time(), "before": r.get("note"),
                     "step": f"Waiting for Y-finder's browser ({n} ahead)" if n else "Starting Y-finder's browser"}
            s.previews[pick["ref"]] = r
        if handoff:  # outside the lock: the browser reports back on its own thread
            _submit_browser(s, pick, sig)


# ---------------------------------------------------------------------------- Y-finder's browser (pages that need a person)
def _browser_ok(s, pick) -> bool:
    """Open, public pages only: no Earth Engine, licensed, registration or request-only sources, and a few per session."""
    if not browser.available() or s.browser_tries >= MAX_BROWSER:
        return False
    meta = pick.get("meta") or catalog_mod.load().get(pick["ref"]) or {}
    url = meta.get("url") or pick.get("url") or ""
    acc = (meta.get("access_class") or pick.get("access_class") or "").lower()
    if pick["ref"].startswith(("gee:", "geec:", "pc:")) or acc in ("yale", "paid", "request"):
        return False
    if "registration" in (meta.get("access") or "").lower() or "account" in (meta.get("access") or "").lower():
        return False
    return url.startswith("https://") and sources.safe_url(url)


def _step(s, ref: str, sig: str, text: str):
    with s.plock:
        cur = s.previews.get(ref)
        if cur and cur.get("sig") == sig and cur.get("status") == "pending":
            cur["step"] = text[:120]


def _pick_words(s, pick, meta):
    """The card's topic in words: its title, the graph factors it measures and why it was picked."""
    nodes = {n["id"]: f"{n.get('label', '')} {n.get('observable', '')}" for n in (s.graph or {}).get("nodes", [])}
    return browser.topic_words(" ".join([meta.get("title") or "", pick.get("title") or "", pick.get("why") or "",
                                         " ".join(nodes.get(f, "") for f in pick.get("factors") or [])]))


def _submit_browser(s, pick, sig):
    meta = pick.get("meta") or catalog_mod.load().get(pick["ref"]) or {}
    url = meta.get("url") or pick.get("url")
    ref = pick["ref"]
    browser.submit(url, lambda t: _step(s, ref, sig, t), lambda found: _browser_done(s, pick, meta, sig, found),
                   year=_study_year(s.scope), alive=lambda: session.peek(s.sid) is s and (s.previews.get(ref) or {}).get("sig") == sig,
                   words=_pick_words(s, pick, meta))


def _from_browser(s, pick, meta, found: dict, progress) -> dict:
    host = found.get("host") or "the site"
    w = found.get("wall")
    if w == "signin":
        return {"status": "none", "note": f"{host} asks for a sign-in; Y-finder does not sign in for you. Use the Source link"}
    if w == "robot":
        return {"status": "none", "note": f"{host} shows a robot check or blocks automated visits; open it with the Source link"}
    if w:
        return {"status": "none", "note": f"Y-finder's browser could not open {host}; use the Source link"}
    for svc in found.get("services") or []:
        progress(f"Drawing the map service behind {host}")
        try:
            r = _arcgis_url(s, pick, svc, meta.get("title") or pick.get("title") or "")
        except Exception:
            r = None
        if r and r.get("status") == "ready":
            r["note"] = (r.get("note") or "Live map service") + f" (found by Y-finder's browser on {host})"
            return r
    for f in found.get("files") or []:
        progress(f"Reading {f['name']}")
        d = downloads.session_dir(s.sid)
        d.mkdir(parents=True, exist_ok=True)
        dest = d / ("pv-" + _tag(pick) + re.sub(r"[^a-z0-9.]+", "-", f["name"].lower())[-60:])
        try:
            os.replace(f["path"], dest)
            res = _read_raw(s, pick, meta, dest, f["name"], host)
            if res:
                res["note"] += " by Y-finder's browser"
                return res
        except (tables.TableError, ValueError, KeyError, OSError, zipfile.BadZipFile, downloads.DownloadError):
            pass
        finally:
            dest.unlink(missing_ok=True)
    links = found.get("links") or []
    if links:
        res, why = _try_links(s, pick, meta, links[:4], cookies=found.get("cookies"), progress=progress)
        if res:
            res["note"] = res.get("note", "") + f" (found by Y-finder's browser on {host})"
            return res
        return {"status": "none", "note": f"Y-finder's browser {why or f'found no file it can read on {host}'}. Use the Source link"}
    if found.get("rasters"):
        return {"status": "none", "note": f"{host} offers raster files (GeoTIFF or NetCDF); Y-finder draws vector and table files for now"}
    return {"status": "none", "note": f"Y-finder's browser opened {host} and found no data file to download; use the Source link"}


def _browser_done(s, pick, meta, sig, found: dict):
    ref = pick["ref"]
    try:
        if session.peek(s.sid) is not s:
            return
        with _slots:
            try:
                r = _from_browser(s, pick, meta, found, lambda t: _step(s, ref, sig, t))
            except Exception as e:  # a broken file must not leave the card waiting
                r = {"status": "none", "note": f"Y-finder's browser could not finish ({type(e).__name__}); use the Source link"}
        r.update(at=time.time(), sig=sig, browser=True)
        for l in r.get("layers", []):
            if pick.get("role") != "core":
                l["hidden"] = True
        with s.plock:
            cur = s.previews.get(ref)
            if not cur or cur.get("sig") != sig:
                return  # the scope changed while the browser worked
            for l in r.get("layers", []):
                if l not in s.layers:
                    s.layers.append(l)
            s.previews[ref] = r
    finally:
        for f in found.get("files") or []:
            import shutil
            shutil.rmtree(f.get("dir") or "", ignore_errors=True)


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
        items = {ref: {k: v for k, v in r.items() if k in ("status", "kind", "note", "layers", "existing", "csv", "table", "series", "step", "browser")}
                 for ref, r in s.previews.items()}
        # the browser works after the first pass, so the page keeps asking while any card still waits
        return {"running": s.preview_running or any(i.get("status") == "pending" for i in items.values()), "items": items}
