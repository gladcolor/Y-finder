"""Live connectors to public catalogs and data services.

Every function returns plain dicts. Search results are normalized to:
  {ref, title, source, provider, description, url, access, access_class, region|bbox, time, t0, t1, res_m, unit, type}
so recommend_data can check them with fit.assess like catalog cards.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
import re
import socket
import threading
import time
from urllib.parse import quote, urlencode, urlparse

import requests

from .config import USER_AGENT
from .geo import bbox_size_km, geojson_bbox, valid_bbox

HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
TIMEOUT = 25

_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, ttl: float, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _cache_lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[key] = (now, val)
    return val


def _get(url: str, params: dict | None = None, timeout: int = TIMEOUT):
    r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _clip(s, n=260):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "..."


def _years(text) -> tuple[int | None, int | None]:
    ys = [int(y) for y in re.findall(r"(?<!\d)(1[6-9]\d\d|20\d\d)(?!\d)", str(text or ""))]
    if not ys:
        return None, None
    return min(ys), max(ys)


# --------------------------------------------------------------------------- geocoding
_nom_lock = threading.Lock()
_nom_last = [0.0]


ADMIN_WORDS = re.compile(r"\b(district|county|province|prefecture|municipality|department|governorate|region|division|sub-?county|parish)\b", re.I)


def _is_united_states(d: dict) -> bool:
    return d.get("addresstype") == "country" and str(d.get("name") or d.get("display_name") or "").startswith("United States")


def _boundary(data) -> dict | None:
    return next((d for d in data or [] if (d.get("category") or d.get("class")) == "boundary"), None)


def geocode(place: str) -> dict | None:
    """Nominatim search (1 request per second, cached). Returns bbox, center and a boundary if available.

    Of the first five answers a boundary wins, else the first answer. A query naming a district, county or province
    that finds no boundary is asked again without that word: "Soroti District, Uganda" finds the district office,
    "Soroti, Uganda" finds the district."""
    def ask(q):
        with _nom_lock:  # Nominatim usage policy: at most one request per second
            wait = 1.05 - (time.time() - _nom_last[0])
            if wait > 0:
                time.sleep(wait)
            _nom_last[0] = time.time()
            return _get("https://nominatim.openstreetmap.org/search", {
                "q": q, "format": "jsonv2", "limit": 5, "polygon_geojson": 1, "polygon_threshold": 0.0015,
                "accept-language": "en"})  # English names for the map and the answer ("Pakistan", not "پاکستان")

    def run():
        data = ask(place)
        d = _boundary(data)
        if d is None and ADMIN_WORDS.search(place):
            short = re.sub(r"\s+,", ",", re.sub(r"\s{2,}", " ", ADMIN_WORDS.sub("", place))).strip(" ,")
            if short and short.lower() != place.strip().lower():
                more = ask(short)
                d = _boundary(more) or (None if data else (more or [None])[0])
        d = d or (data or [None])[0]
        if d is None:
            return None
        if _is_united_states(d):
            # the country's box spans the globe (the Aleutians cross 180 degrees): study the contiguous states
            return {"name": "United States (contiguous 48 states; Alaska, Hawaii and the territories lie outside this box)",
                    "lat": 39.8, "lon": -98.6, "bbox": [-124.85, 24.4, -66.88, 49.39], "type": "country", "class": "boundary",
                    "geometry": None}
        s, n, w, e = (float(x) for x in d["boundingbox"])
        geom = d.get("geojson")
        if geom and geom.get("type") not in ("Polygon", "MultiPolygon"):
            geom = None
        return {"name": d.get("display_name"), "lat": float(d["lat"]), "lon": float(d["lon"]),
                "bbox": [w, s, e, n], "type": d.get("type"), "class": d.get("category") or d.get("class"),
                "geometry": geom}
    return _cached("geo:" + place.strip().lower(), 86400, run)


def reverse(lon: float, lat: float) -> str | None:
    """The neighbourhood, district or town at a point (Nominatim reverse, same 1-per-second rule, cached a week)."""
    def run():
        with _nom_lock:
            wait = 1.05 - (time.time() - _nom_last[0])
            if wait > 0:
                time.sleep(wait)
            _nom_last[0] = time.time()
            try:
                d = _get("https://nominatim.openstreetmap.org/reverse",
                         {"lat": f"{lat:.4f}", "lon": f"{lon:.4f}", "format": "jsonv2", "zoom": 14, "accept-language": "en"})
            except (requests.RequestException, ValueError):
                return None
        a = (d or {}).get("address") or {}
        for k in ("neighbourhood", "suburb", "quarter", "city_district", "village", "town", "city", "county", "state"):
            if a.get(k):
                return str(a[k])[:60]
        return None
    return _cached(f"rev:{lon:.3f},{lat:.3f}", 7 * 86400, run)


# --------------------------------------------------------------------------- STAC (Planetary Computer)
PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"


def stac_search(collection: str, bbox, datetime: str | None, max_cloud: float | None, limit: int = 8) -> list[dict]:
    body = {"collections": [collection], "bbox": bbox, "limit": max(1, min(int(limit or 8), 20)),
            "sortby": [{"field": "properties.datetime", "direction": "desc"}]}
    if datetime:
        body["datetime"] = datetime
    if max_cloud is not None:
        body["query"] = {"eo:cloud_cover": {"lt": float(max_cloud)}}
    r = requests.post(PC_STAC + "/search", json=body, headers=HEADERS, timeout=40)
    if r.status_code == 400 and "query" in body:  # collection without eo:cloud_cover
        body.pop("query")
        r = requests.post(PC_STAC + "/search", json=body, headers=HEADERS, timeout=40)
    r.raise_for_status()
    out = []
    for f in r.json().get("features", []):
        p = f.get("properties", {})
        a = f.get("assets", {})
        out.append({
            "id": f["id"], "collection": collection, "datetime": p.get("datetime") or p.get("start_datetime"),
            "cloud_cover": p.get("eo:cloud_cover"), "gsd": p.get("gsd"), "bbox": f.get("bbox"),
            "geometry": f.get("geometry"),
            "tilejson": (a.get("tilejson") or {}).get("href"),
            "preview": (a.get("rendered_preview") or {}).get("href"),
            "url": f"https://planetarycomputer.microsoft.com/api/stac/v1/collections/{collection}/items/{f['id']}",
        })
    return out


def stac_tiles(tilejson_url: str) -> dict | None:
    if not tilejson_url or not tilejson_url.startswith("https://planetarycomputer.microsoft.com/api/data/v1/"):
        return None
    tj = _cached("tj:" + tilejson_url, 3600, lambda: _get(tilejson_url))
    return {"tiles": tj.get("tiles"), "bounds": tj.get("bounds"), "minzoom": tj.get("minzoom", 0),
            "maxzoom": min(int(tj.get("maxzoom", 18)), 20)}


# --------------------------------------------------------------------------- Yale Dataverse
def dataverse(query: str, host: str = "dataverse.harvard.edu", source: str = "Harvard Dataverse", limit: int = 8) -> list[dict]:
    """Datasets in a Dataverse repository (Harvard's is the largest academic one; Yale has its own)."""
    d = _cached(f"dv:{host}:{query}", 3600, lambda: _get(f"https://{host}/api/search", {"q": query, "type": "dataset", "per_page": limit}))
    out = []
    for it in d.get("data", {}).get("items", []):
        url = it.get("url") or ""
        out.append({
            "ref": url, "title": it.get("name"), "source": source,
            "provider": ", ".join(it.get("authors", [])[:2]) or it.get("publisher") or source,
            "description": _clip(it.get("description")), "url": url, "access": "Open to download (check terms)",
            "access_class": "open", "region": None, "time": "Published " + (it.get("published_at") or "")[:10],
            "t0": None, "t1": None, "res_m": None, "unit": None, "type": "Repository dataset",
            "citation": _clip(it.get("citation"), 200), "data_type": "table",
            "doi": it.get("global_id") if str(it.get("global_id", "")).startswith("doi:") else None,
            "dataverse_host": host, "files": it.get("fileCount"),
        })
    return out


def yale_dataverse(query: str, limit: int = 8) -> list[dict]:
    return dataverse(query, "dataverse.yale.edu", "Yale Dataverse", limit)


# Socrata powers many US open data portals (CDC, CMS, states, cities); its catalog API searches them all.
SOCRATA_CATALOG = "https://api.us.socrata.com/api/catalog/v1"
_SOC_ID = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}$")


SOCRATA_REGION = {  # portal -> what it covers, so a table's extent fit is not "unknown"
    "data.ct.gov": "Connecticut", "data.cdc.gov": "US", "healthdata.gov": "US", "data.cms.gov": "US",
    "data.medicaid.gov": "US", "data.transportation.gov": "US", "data.hrsa.gov": "US", "data.energystar.gov": "US",
}
DATE_TYPES = {"calendar date", "date", "floating timestamp", "fixed timestamp"}


def _date_field(names: list, types: list) -> str | None:
    """The table's first date column (or a text column named date or year), for its years."""
    cols = [(str(n), str(t).lower()) for n, t in zip(names or [], types or []) if isinstance(n, str) and not n.startswith(":")]
    for n, t in cols:
        if t in DATE_TYPES:
            return n
    return next((n for n, t in cols if n.lower() in ("date", "year", "week_end", "week_ending_date")), None)


def socrata_span(soda_csv: str, field: str) -> tuple[int, int] | None:
    """First and last year in a Socrata table's date column (one small query, cached a day)."""
    m = re.match(r"^https://([a-z0-9.-]+)/resource/([a-z0-9]{4}-[a-z0-9]{4})\.csv$", soda_csv or "")
    if not m or not re.fullmatch(r"[A-Za-z_][\w]*", field or "") or os.environ.get("YFINDER_TABLE_SPANS", "1") == "0":
        return None

    def run():
        try:
            d = _get(f"https://{m.group(1)}/resource/{m.group(2)}.json",
                     {"$select": f"min({field}) as a, max({field}) as b"}, timeout=8)
        except (requests.RequestException, ValueError):
            return None
        row = (d or [{}])[0] if isinstance(d, list) else {}
        ys = [int(x[:4]) for x in (str(row.get("a") or ""), str(row.get("b") or "")) if re.match(r"^\d{4}", x)]
        if len(ys) != 2 or not (1800 <= ys[0] <= ys[1] <= dt.date.today().year + 1):
            return None
        return ys[0], ys[1]
    return _cached(f"socspan:{soda_csv}:{field}", 86400, run)


def socrata(query: str, limit: int = 8, domains: str | None = None) -> list[dict]:
    params = {"q": query, "only": "dataset", "limit": limit}
    if domains:
        params["domains"] = domains
    d = _cached(f"soc:{domains}:{query}", 3600, lambda: _get(SOCRATA_CATALOG, params))
    out = []
    for r in d.get("results") or []:
        res, md = r.get("resource") or {}, r.get("metadata") or {}
        dom, rid = md.get("domain") or "", res.get("id") or ""
        if not dom or not _SOC_ID.match(rid) or not re.fullmatch(r"[a-z0-9.-]+", dom):
            continue
        link = r.get("permalink") or r.get("link") or f"https://{dom}/d/{rid}"
        upd = (res.get("data_updated_at") or res.get("updatedAt") or "")[:10]
        out.append({
            "ref": link, "title": res.get("name"), "source": f"Open data portal ({dom})",
            "provider": res.get("attribution") or dom, "description": _clip(res.get("description")), "url": link,
            "access": "Open (Socrata API)", "access_class": "open", "region": SOCRATA_REGION.get(dom),
            "time": f"Updated {upd}" if upd else "",
            "date_field": _date_field(res.get("columns_field_name"), res.get("columns_datatype")),
            "t0": None, "t1": None, "res_m": None, "unit": None, "type": "Table", "data_type": "table",
            "columns": [c for c in (res.get("columns_name") or [])[:30] if isinstance(c, str)],
            "soda": f"https://{dom}/resource/{rid}.csv", "rows_updated": upd,
        })
    return out


# --------------------------------------------------------------------------- Yale GeoBlacklight
def _attr(a: dict, k: str):
    v = a.get(k)
    if isinstance(v, dict):
        v = v.get("attributes", {}).get("value")
    return v


_GBL_GENERIC = {"map", "maps", "historical", "historic", "old", "data", "gis", "layer", "layers", "find", "of", "the", "and",
                "from", "to", "for", "in", "on", "shoreline", "changes", "change", "trace", "how", "with"}


def yale_geoblacklight(query: str, bbox=None, limit: int = 8, years: tuple | None = None) -> list[dict]:
    """Search Yale GeoBlacklight. Its Solr search ANDs every word, so broaden step by step:
    the full query, then without generic words, then the first two remaining words (usually the place)."""
    words = [w for w in re.findall(r"[\w'-]+", query)]
    core = [w for w in words if w.lower() not in _GBL_GENERIC]
    attempts = [query, " ".join(core), " ".join(core[:2])]
    data, seen_ids = [], set()
    for q in dict.fromkeys(a for a in attempts if a.strip()):
        params = {"q": q, "per_page": limit}
        if bbox and valid_bbox(bbox):
            params["bbox"] = " ".join(f"{x:.4f}" for x in bbox)
        if years and years[0]:
            params["range[solr_year_i][begin]"] = int(years[0])
            params["range[solr_year_i][end]"] = int(years[1] or years[0])
        d = _cached("gbl:" + json.dumps(params, sort_keys=True), 3600,
                    lambda p=params: _get("https://geoblacklight.library.yale.edu/catalog.json", p))
        for it in d.get("data", []):
            if it["id"] not in seen_ids:
                seen_ids.add(it["id"])
                data.append(it)
        if len(data) >= limit:
            break
    out = []
    for it in data[:limit]:
        a = it.get("attributes", {})
        year = _attr(a, "solr_year_i")
        rights = _attr(a, "dc_rights_s") or ""
        url = f"https://geoblacklight.library.yale.edu/catalog/{it['id']}"
        y = int(year) if str(year or "").isdigit() else None
        out.append({
            "ref": url, "title": _attr(a, "title") or it["id"], "source": "Yale GeoBlacklight",
            "provider": _attr(a, "dc_publisher_s") or _attr(a, "dc_creator_sm") or "Yale Library",
            "description": _clip(_attr(a, "dc_description_s") or _attr(a, "dct_spatial_sm") or ""),
            "url": url, "access": f"{rights or 'See record'} (Yale Library)",
            "access_class": "open" if rights.lower() == "public" else "yale",
            "region": None, "time": str(year or ""), "t0": y, "t1": y, "res_m": None,
            "unit": "Map sheet", "type": _attr(a, "layer_geom_type_s") or "Map",
        })
    return out


# --------------------------------------------------------------------------- ArcGIS Online
AGOL_TYPES = {
    "any": '(type:"Feature Service" OR type:"Map Service" OR type:"Image Service")',
    "feature": 'type:"Feature Service"', "imagery": 'type:"Image Service"', "map": 'type:"Map Service"',
}


def arcgis_online(query: str, bbox=None, types: str = "any", limit: int = 8) -> list[dict]:
    q = f"({query}) {AGOL_TYPES.get(types, AGOL_TYPES['any'])} -type:\"Code Attachment\""
    params = {"q": q, "num": limit, "f": "json", "sortField": "numviews", "sortOrder": "desc"}
    if bbox and valid_bbox(bbox):
        params["bbox"] = ",".join(f"{x:.4f}" for x in bbox)
    d = _cached("agol:" + json.dumps(params, sort_keys=True), 3600,
                lambda: _get("https://www.arcgis.com/sharing/rest/search", params))
    out = []
    for it in d.get("results", []):
        ext = it.get("extent") or []
        b = [ext[0][0], ext[0][1], ext[1][0], ext[1][1]] if len(ext) == 2 else None
        t0, t1 = _years(it.get("snippet"))
        out.append({
            "ref": "https://www.arcgis.com/home/item.html?id=" + it["id"], "title": it.get("title"),
            "source": "ArcGIS Online", "provider": it.get("owner"),
            "description": _clip(it.get("snippet") or it.get("description")),
            "url": "https://www.arcgis.com/home/item.html?id=" + it["id"], "service_url": it.get("url"),
            "access": "Public ArcGIS item", "access_class": "open",
            "bbox": b if b and valid_bbox(b) else None, "time": "", "t0": None, "t1": None,
            "res_m": None, "unit": None, "type": it.get("type"), "views": it.get("numViews"),
        })
    return out


# --------------------------------------------------------------------------- Data.gov
def datagov(query: str, limit: int = 8) -> list[dict]:
    d = _cached("dg:" + query, 3600, lambda: _get("https://catalog.data.gov/search", {"q": query, "format": "json"}))
    out = []
    for r in (d.get("results") or [])[:limit]:
        dc = r.get("dcat") or {}
        bbox = None
        sp = dc.get("spatial")
        try:
            spj = json.loads(sp) if isinstance(sp, str) and sp.startswith("[") else None
            if spj and isinstance(spj, list) and len(spj) == 2 and isinstance(spj[1], list):
                c = spj[1][0]
                bbox = [c["WestBoundingCoordinate"], c["SouthBoundingCoordinate"], c["EastBoundingCoordinate"], c["NorthBoundingCoordinate"]]
            elif isinstance(sp, str) and re.fullmatch(r"\s*-?\d+(\.\d+)?(\s*,\s*-?\d+(\.\d+)?){3}\s*", sp):
                bbox = [float(x) for x in sp.split(",")]
        except (ValueError, KeyError, TypeError, IndexError):
            bbox = None
        t0, t1 = _years(dc.get("temporal"))
        slug = r.get("slug") or ""
        pub = dc.get("publisher")
        pub = pub.get("name") if isinstance(pub, dict) else r.get("publisher")
        url = f"https://catalog.data.gov/dataset/{slug}" if slug else (dc.get("landingPage") or "")
        out.append({
            "ref": url, "title": r.get("title") or dc.get("title"), "source": "Data.gov", "provider": pub,
            "description": _clip(r.get("description") or dc.get("description")), "url": url,
            "access": "Public" if r.get("access_level") == "public" else (r.get("access_level") or "See record"),
            "access_class": "open" if r.get("access_level") == "public" else "request",
            "bbox": bbox if bbox and valid_bbox(bbox) else None, "time": dc.get("temporal") or "",
            "t0": t0, "t1": t1 if (t1 and t1 != t0) or dc.get("temporal") else None,
            "res_m": None, "unit": None, "type": "Dataset",
        })
    return out


# --------------------------------------------------------------------------- safety for model-chosen URLs
def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def safe_url(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    return u.scheme == "https" and bool(u.hostname) and _public_host(u.hostname)


# --------------------------------------------------------------------------- ArcGIS REST feature queries
_LAYER_RE = re.compile(r"^(https://[^?#]+/rest/services/[^?#]+/(?:FeatureServer|MapServer))(?:/(\d+))?/?(?:\?.*)?$", re.I)


def arcgis_layer_url(url: str) -> str | None:
    m = _LAYER_RE.match((url or "").strip())
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2) or 0}"


def arcgis_query(url: str, bbox, where: str = "1=1", max_features: int = 2000, simplify_deg: float | None = None,
                 out_fields: str = "*") -> dict:
    layer = arcgis_layer_url(url)
    if not layer:
        raise ValueError("Not an ArcGIS FeatureServer or MapServer layer URL")
    if not safe_url(layer):
        raise ValueError("URL host is not allowed")
    if not re.fullmatch(r"[\w\s=<>'%.,()\-]*", where or "1=1"):
        raise ValueError("Unsupported where clause")
    if not re.fullmatch(r"\*|[\w,]+", out_fields or "*"):
        raise ValueError("Unsupported field list")
    params = {"where": where or "1=1", "outFields": out_fields or "*", "outSR": 4326, "f": "geojson",
              "resultRecordCount": max(1, min(int(max_features), 4000))}
    if bbox and valid_bbox(bbox):
        params.update({"geometry": ",".join(str(x) for x in bbox), "geometryType": "esriGeometryEnvelope",
                       "inSR": 4326, "spatialRel": "esriSpatialRelIntersects"})
    if simplify_deg:  # server-side generalization keeps big boundary layers light for the browser
        params.update({"maxAllowableOffset": simplify_deg, "geometryPrecision": 5})
    def page(offset: int) -> dict:
        q = dict(params, resultOffset=offset) if offset else params
        r = requests.get(layer + "/query", params=q, headers=HEADERS, timeout=45, allow_redirects=False)
        hops = 0
        while r.is_redirect and hops < 3:  # follow redirects only to hosts that pass the same public-HTTPS check
            nxt = requests.compat.urljoin(r.url, r.headers.get("Location", ""))
            if not safe_url(nxt):
                raise ValueError("Service redirected to a host that is not allowed")
            r = requests.get(nxt, headers=HEADERS, timeout=45, allow_redirects=False)
            hops += 1
        r.raise_for_status()
        out = r.json()
        if "error" in out:
            raise ValueError(str(out["error"].get("message", "ArcGIS error")))
        return out

    # Servers cap each answer (often 1,000 or 2,000 features); keep asking with an offset until max_features.
    fc = page(0)
    feats = list(fc.get("features", []))
    for _ in range(80):  # some Living Atlas services answer 50 features at a time
        more = fc.get("exceededTransferLimit") or (fc.get("properties") or {}).get("exceededTransferLimit")
        if not more or len(feats) >= max_features or not fc.get("features"):
            break
        first = fc["features"][0]
        fc = page(len(feats))
        if not fc.get("features") or fc["features"][0] == first:  # the server ignores offsets
            break
        feats += fc["features"]
    feats = feats[:max_features]
    fc = {"features": feats}
    feats = fc.get("features", [])
    for f in feats:  # keep popups readable and payloads small
        props = f.get("properties") or {}
        f["properties"] = {k: v for k, v in list(props.items())[:24] if not str(k).lower().startswith("shape")}
    return {"type": "FeatureCollection", "features": feats}


TIGER = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer"
TIGER_LAYERS = {"tract": 8, "block_group": 10, "town": 22, "place": 28, "county": 82, "state": 80}


def census_boundaries(level: str, bbox) -> dict:
    lid = TIGER_LAYERS.get(level)
    if lid is None:
        raise ValueError("level must be one of " + ", ".join(TIGER_LAYERS))
    w, s, e, n = bbox
    offset = max(0.00005, min(0.003, (e - w) / 4000))  # about 1/4000 of the view width
    return arcgis_query(f"{TIGER}/{lid}", bbox, max_features=3000, simplify_deg=offset)


# --------------------------------------------------------------------------- OpenStreetMap (Overpass)
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter"]
_TAG_RE = re.compile(r"^[A-Za-z0-9_:]+(=[A-Za-z0-9_ :.\-]+)?$")
AREA_KEYS = {"building", "landuse", "leisure", "natural", "amenity", "water", "wetland", "park", "place",
             "boundary", "area", "man_made", "parking", "shop", "tourism", "historic", "aeroway"}


def osm_features(tag_filters: list[str], bbox, max_elements: int = 4000, max_area_km2: float = 2500, timeout_s: int = 65) -> dict:
    if not bbox or not valid_bbox(bbox):
        raise ValueError("Set a study area first")
    wkm, hkm = bbox_size_km(bbox)
    if wkm * hkm > max_area_km2:
        side = max_area_km2 ** 0.5
        raise ValueError(f"Study area is {wkm:.0f} x {hkm:.0f} km; this OpenStreetMap preview is limited to about {side:.0f} x {side:.0f} km")
    filters = [t.strip() for t in tag_filters if t and t.strip()][:4]
    if not filters or not all(_TAG_RE.match(t) for t in filters):
        raise ValueError("Tag filters must look like key or key=value, e.g. natural=water")
    w, s, e, n = bbox
    parts = []
    for t in filters:
        k, _, v = t.partition("=")
        sel = f'["{k}"="{v}"]' if v else f'["{k}"]'
        kind = "node" if k == "place" else "nwr"  # settlements are points; asking for ways too is slow
        parts.append(f"{kind}{sel}({s},{w},{n},{e});")
    q = f"[out:json][timeout:{max(25, timeout_s - 10)}];({''.join(parts)});out geom {int(max_elements)};"
    data, last_err = None, None
    for endpoint in OVERPASS:  # the main server is often busy; fall back to a mirror
        try:
            # Overpass stalls when asked for "Accept: application/json"; the query itself sets the format
            r = requests.post(endpoint, data={"data": q}, headers={"User-Agent": USER_AGENT}, timeout=timeout_s)
            if r.status_code == 200:
                data = r.json()
                break
            last_err = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            last_err = type(e).__name__
    if data is None:
        raise ValueError(f"OpenStreetMap servers are busy ({last_err}); try again in a minute")
    feats = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        props = {"osm_id": f"{el['type']}/{el['id']}", **{k: v for k, v in list(tags.items())[:16]}}
        g = None
        if el["type"] == "node":
            g = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
        elif el["type"] == "way" and el.get("geometry"):
            coords = [[p["lon"], p["lat"]] for p in el["geometry"]]
            closed = len(coords) > 3 and coords[0] == coords[-1]
            if closed and (AREA_KEYS & set(tags)) and "highway" not in tags and "barrier" not in tags:
                g = {"type": "Polygon", "coordinates": [coords]}
            else:
                g = {"type": "LineString", "coordinates": coords}
        elif el["type"] == "relation":
            rings = []
            for m in el.get("members", []):
                if m.get("role") == "outer" and m.get("geometry"):
                    c = [[p["lon"], p["lat"]] for p in m["geometry"]]
                    if len(c) > 3 and c[0] == c[-1]:
                        rings.append([c])
            if rings:
                g = {"type": "MultiPolygon", "coordinates": rings}
        if g:
            feats.append({"type": "Feature", "geometry": g, "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def fc_summary(fc: dict) -> dict:
    feats = fc.get("features", [])
    kinds: dict[str, int] = {}
    for f in feats:
        t = (f.get("geometry") or {}).get("type", "None")
        kinds[t] = kinds.get(t, 0) + 1
    return {"n": len(feats), "geometry_types": kinds, "bbox": geojson_bbox(fc) if feats else None}


__all__ = ["geocode", "stac_search", "stac_tiles", "yale_dataverse", "yale_geoblacklight", "arcgis_online",
           "datagov", "arcgis_query", "census_boundaries", "osm_features", "safe_url", "fc_summary", "quote", "urlencode"]
