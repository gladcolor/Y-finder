"""Catalog search: BM25 over dataset, tool and method cards, with synonyms and scope-aware ranking."""
from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter

from . import fit
from .config import DATA_DIR

STOP = set("""a an and are as at be by for from has have how i in into is it its me my of on or our over
that the their them these this those to was we what when where which who why will with would can could do does
data dataset datasets using use used study studies research want need about between across per vs than more most
find get show map maps some any all each like""".split())

# Query expansion. Keys and values are phrases; expansion terms get a lower weight.
SYNONYMS = {
    "pm2.5": "pm25 particulate air pollution aerosol", "pm25": "particulate air pollution aerosol",
    "air quality": "pollution pm25 ozone no2 aerosol", "pollution": "air quality pm25 emissions",
    "smoke": "wildfire aerosol aod fire", "wildfire": "fire burn smoke burned area",
    "heat": "temperature land surface temperature lst thermal", "temperature": "heat climate weather lst",
    "tree": "canopy forest vegetation", "trees": "canopy forest vegetation", "canopy": "tree forest vegetation lidar",
    "green": "vegetation ndvi park canopy", "greenness": "ndvi vegetation", "vegetation": "ndvi evi greenness land cover",
    "mosquito": "vector larval standing water wetland breeding", "larval": "mosquito standing water breeding habitat",
    "standing water": "surface water ponds wetlands depressions", "water": "surface water hydrology lakes rivers",
    "flood": "flooding inundation floodplain sea level storm surge", "flooding": "flood inundation floodplain",
    "sea level": "coastal inundation slr tide", "coastal": "coast shoreline sea level",
    "income": "socioeconomic poverty acs demographics", "poverty": "income socioeconomic acs",
    "race": "ethnicity demographics census acs", "demographics": "population census acs age race",
    "population": "demographics census people gridded", "housing": "homes rent units zoning parcels permits",
    "zoning": "land use regulation housing districts parcels", "parcel": "parcels property assessor cadastral",
    "mobility": "foot traffic movement visits travel smartphone", "foot traffic": "mobility visits poi",
    "commute": "commuting travel work lodes", "transit": "bus rail transportation gtfs",
    "street view": "street level imagery mapillary panorama", "imagery": "satellite aerial image photo",
    "satellite": "imagery remote sensing sentinel landsat", "aerial": "imagery orthophoto naip",
    "drone": "uav aerial very high resolution", "elevation": "dem terrain lidar topography",
    "lidar": "elevation point cloud dem canopy height", "terrain": "elevation slope dem",
    "land cover": "landcover land use classification", "land use": "land cover zoning",
    "crops": "agriculture cropland farming", "agriculture": "crops cropland farming",
    "health": "disease public health chronic outcomes", "asthma": "respiratory health places",
    "malaria": "mosquito vector disease", "disease": "health epidemiology",
    "species": "biodiversity occurrences wildlife", "biodiversity": "species ecology habitat",
    "roads": "streets network transportation osm", "buildings": "footprints structures",
    "schools": "education facilities", "hospitals": "health care facilities",
    "historical": "historic old maps archive", "maps": "cartographic map sheets",
    "bonds": "municipal finance debt", "climate": "weather temperature precipitation projections",
    "rain": "precipitation rainfall", "rainfall": "precipitation rain", "precipitation": "rainfall rain",
    "nighttime lights": "night lights viirs radiance", "night lights": "nighttime lights viirs",
    "voters": "elections voting turnout", "elections": "voters voting",
    "solar": "rooftop irradiance energy", "energy": "electricity power solar",
}

FIELD_WEIGHTS = (("title", 3), ("keywords", 2), ("description", 1), ("provider", 1), ("source", 1))


_SENSOR = re.compile(r"\b(sentinel|landsat|goes|noaa|terra|aqua|naip|hls|s|l)[\s_-]?(\d{1,2})\b")
_COMPOUND = re.compile(r"\b([a-z]{2,})-(\d{1,2}[a-z]?)\b")


def tokens(text: str) -> list[str]:
    t = (text or "").lower().replace("pm2.5", "pm25").replace("pm 2.5", "pm25")
    # Keep sensor and product names whole ("Sentinel-2" -> sentinel2) so they do not match Sentinel-3.
    t = _SENSOR.sub(lambda m: f" {m.group(1)}{m.group(2)} ", t)
    t = _COMPOUND.sub(lambda m: f" {m.group(1)}{m.group(2)} {m.group(1)} ", t)
    out = []
    for w in re.findall(r"[a-z0-9]+", t):
        if w in STOP or len(w) < 2:
            continue
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


class Catalog:
    def __init__(self, items: list[dict], meta: dict | None = None):
        self.items = items
        self.meta = meta or {}
        self.by_id = {i["id"]: i for i in items}
        self.docs: list[Counter] = []
        df: Counter = Counter()
        for it in items:
            c: Counter = Counter()
            for field, w in FIELD_WEIGHTS:
                for tok in tokens(str(it.get(field, ""))):
                    c[tok] += w
            self.docs.append(c)
            df.update(c.keys())
        self.N = len(items)
        self.avgdl = sum(sum(d.values()) for d in self.docs) / max(1, self.N)
        self.idf = {t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def _expand(self, query: str) -> dict[str, float]:
        q = query.lower()
        weights: dict[str, float] = {}
        for t in tokens(q):
            weights[t] = max(weights.get(t, 0), 1.0)
        for phrase, extra in SYNONYMS.items():
            if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", q):
                for t in tokens(extra):
                    weights[t] = max(weights.get(t, 0), 0.45)
        return weights

    def search(self, query: str, kind: str = "any", access: str = "any", limit: int = 10,
               scope: dict | None = None, source: str = "any") -> list[dict]:
        q = self._expand(query)
        if not q:
            return []
        k1, b = 1.2, 0.75
        scored = []
        for i, d in enumerate(self.docs):
            it = self.items[i]
            if kind != "any" and it.get("kind", "dataset") != kind:
                continue
            if access == "open" and it.get("access_class") != "open":
                continue
            if access == "yale" and it.get("access_class") != "yale":
                continue
            if source != "any" and source.lower() not in (it.get("source") or "").lower():
                continue
            dl = sum(d.values())
            s = 0.0
            for t, w in q.items():
                f = d.get(t)
                if f:
                    s += w * self.idf.get(t, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / self.avgdl))
            if s <= 0:
                continue
            if it.get("source") == "YCGS curated":
                s *= 1.35
            scored.append((s, i))
        scored.sort(reverse=True)
        out = []
        for s, i in scored[: max(limit * 4, 40)]:
            it = self.items[i]
            f = fit.assess(it, scope) if scope and it.get("kind", "dataset") == "dataset" else None
            if f:
                s *= {"good": 1.15, "partial": 1.0, "poor": 0.55}.get(f["overall"], 0.95)
            out.append((s, it, f))
        out.sort(key=lambda x: -x[0])
        return [dict(card(it, f), score=round(s, 2)) for s, it, f in out[:limit]]

    def get(self, cid: str) -> dict | None:
        return self.by_id.get(cid)


def fmt_res(m) -> str:
    if not m:
        return ""
    return fit._fmt_res(float(m))


def card(it: dict, f: dict | None = None) -> dict:
    """Compact, model- and UI-friendly view of a catalog item."""
    c = {
        "id": it["id"], "kind": it.get("kind", "dataset"), "title": it.get("title"), "source": it.get("source"),
        "provider": it.get("provider"), "description": (it.get("description") or "")[:240],
        "access": it.get("access"), "access_class": it.get("access_class"), "url": it.get("url"),
    }
    if c["kind"] == "dataset":
        c.update({"region": it.get("region"), "time": it.get("time"), "resolution": fmt_res(it.get("res_m")),
                  "unit": it.get("unit"), "type": it.get("type")})
        if it.get("thumbnail"):
            c["thumbnail"] = it["thumbnail"]
        if it.get("stac"):
            c["stac_collection"] = it["stac"]["collection"]
    if it.get("yale_note"):
        c["yale_note"] = it["yale_note"]
    if it.get("kind") == "method":
        c.update({"inputs": it.get("inputs"), "pitfalls": it.get("pitfalls"), "tools": it.get("tools")})
    if f:
        c["fit"] = f
    return c


_lock = threading.Lock()
_catalog: Catalog | None = None


def load() -> Catalog:
    global _catalog
    with _lock:
        if _catalog is None:
            path = DATA_DIR / "catalog.json"
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                _catalog = Catalog(raw["items"], raw.get("meta"))
            else:  # fall back to the curated cards so the app still runs before a build
                cur = json.loads((DATA_DIR / "curated.json").read_text(encoding="utf-8"))
                items = []
                for k in ("datasets", "tools", "methods"):
                    for c in cur[k]:
                        items.append(dict(c, source="YCGS curated"))
                _catalog = Catalog(items, {"built": "curated only"})
        return _catalog
