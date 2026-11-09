"""Build yfinder/data/catalog.json from live public catalogs plus the curated cards.

Sources
  * Microsoft Planetary Computer STAC collections (API, ~140 collections)
  * Google Earth Engine public catalog (STAC, ~1,000 collections, crawled)
  * GeoLibre's collected layers (opengeos/GeoLibre, MIT):
      - its open data gallery and starter projects (~110 maps; project files cached in runtime/geolibre_cache,
        about 290 MB on the first run)
      - its built-in web services (yfinder/data/geolibre_services.json, copied from GeoLibre's TypeScript)
      - the Earth Engine community catalog it lists (samapriya/awesome-gee-community-datasets, CC BY 4.0),
        grouped into one card per product page
  * yfinder/data/curated.json (hand-maintained Yale and topic cards, tools, methods)

Usage:  python scripts/build_catalog.py [--skip-gee] [--skip-geolibre] [--check-urls]
Runtime: about 2 to 4 minutes (the Earth Engine crawl dominates), plus the first GeoLibre download.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from yfinder import geolibre  # noqa: E402
from yfinder.geo import parse_resolution_m, region_bbox  # noqa: E402

DATA = os.path.join(ROOT, "yfinder", "data")
UA = {"User-Agent": "Y-finder catalog builder/0.1 (+https://github.com/gladcolor)"}
PC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1/collections"
GEE_ROOT = "https://storage.googleapis.com/earthengine-stac/catalog/catalog.json"
GL_RAW = "https://raw.githubusercontent.com/opengeos/GeoLibre/main/"
GEEC_URL = "https://raw.githubusercontent.com/samapriya/awesome-gee-community-datasets/master/community_datasets.json"
GL_CACHE = os.path.join(ROOT, "runtime", "geolibre_cache")

# Nominal resolutions for well-known collections whose STAC metadata lack gsd.
KNOWN_RES = {
    "pc:sentinel-2-l2a": 10, "pc:landsat-c2-l2": 30, "pc:landsat-c2-l1": 30, "pc:naip": 0.6,
    "pc:sentinel-1-grd": 10, "pc:sentinel-1-rtc": 10, "pc:hls2-l30": 30, "pc:hls2-s30": 30,
    "pc:modis-13Q1-061": 250, "pc:modis-11A1-061": 1000, "pc:esa-worldcover": 10, "pc:io-lulc-annual-v02": 10,
    "pc:cop-dem-glo-30": 30, "pc:cop-dem-glo-90": 90, "pc:nasadem": 30, "pc:alos-dem": 30,
    "pc:usgs-lcmap-conus-v13": 30, "pc:nlcd": 30, "pc:gridmet": 4000, "pc:daymet-daily-na": 1000,
    "pc:era5-pds": 31000, "pc:goes-cmi": 2000, "pc:chloris-biomass": 4600, "pc:jrc-gsw": 30,
    "pc:ms-buildings": None, "pc:impact-toolbox-glad-alerts": 30,
}


def clip(s: str | None, n: int = 260) -> str:
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[#*_`>|]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "..."


def years(interval) -> tuple[int | None, int | None]:
    try:
        a, b = interval[0]
    except (TypeError, ValueError, IndexError):
        return None, None
    y0 = int(a[:4]) if a else None
    y1 = int(b[:4]) if b else None
    # An end date within the last year means the collection is still updated.
    if y1 and y1 >= dt.date.today().year - 1:
        y1 = None
    return y0, y1


def first_bbox(extent) -> list[float] | None:
    try:
        b = extent["spatial"]["bbox"][0]
    except (KeyError, IndexError, TypeError):
        return None
    if len(b) == 6:
        b = [b[0], b[1], b[3], b[4]]
    if len(b) != 4:
        return None
    w, s, e, n = (float(x) for x in b)
    if w > e:  # crosses the antimeridian; treat as global longitude
        w, e = -180.0, 180.0
    return [max(-180, w), max(-90, s), min(180, e), min(90, n)]


def gsd_from(obj) -> float | None:
    vals: list[float] = []
    s = obj.get("summaries", {}) or {}
    g = s.get("gsd")
    if isinstance(g, list):
        vals += [float(x) for x in g if isinstance(x, (int, float))]
    elif isinstance(g, dict) and "minimum" in g:
        vals.append(float(g["minimum"]))
    for band in s.get("eo:bands", []) or []:
        if isinstance(band, dict) and isinstance(band.get("gsd"), (int, float)):
            vals.append(float(band["gsd"]))
    for a in (obj.get("item_assets") or {}).values():
        if isinstance(a, dict) and isinstance(a.get("gsd"), (int, float)):
            vals.append(float(a["gsd"]))
    vals = [v for v in vals if v > 0]
    return min(vals) if vals else None


def region_label(b) -> str:
    if not b:
        return "Unknown"
    w, s, e, n = b
    if e - w >= 300 and n - s >= 100:
        return "Global"
    if w >= -170 and e <= -50 and s >= 14:
        return "US / North America"
    return "Regional"


def harvest_pc() -> list[dict]:
    cols = requests.get(PC_URL, headers=UA, timeout=60).json()["collections"]
    out = []
    for c in cols:
        cid = "pc:" + c["id"]
        bbox = first_bbox(c.get("extent", {}))
        t0, t1 = years(c.get("extent", {}).get("temporal", {}).get("interval"))
        desc = c.get("msft:short_description") or c.get("description", "")
        res = gsd_from(c)
        res_src = "metadata" if res else None
        if res is None and cid in KNOWN_RES:
            res, res_src = KNOWN_RES[cid], "known"
        if res is None:
            res = parse_resolution_m((c.get("title") or "") + ". " + desc)
            res_src = "parsed" if res else None
        thumb = (c.get("assets", {}).get("thumbnail") or {}).get("href")
        out.append({
            "id": cid, "kind": "dataset", "title": c.get("title", c["id"]),
            "provider": ", ".join(p.get("name", "") for p in c.get("providers", [])[:2]),
            "description": clip(desc), "keywords": " ".join(c.get("keywords", [])),
            "source": "Planetary Computer", "access": "Open (free STAC API)", "access_class": "open",
            "url": "https://planetarycomputer.microsoft.com/dataset/" + c["id"],
            "region": region_label(bbox), "bbox": bbox, "t0": t0, "t1": t1,
            "time": f"{t0 or '?'} to {t1 or 'present'}", "res_m": res, "res_source": res_src,
            "type": "Raster / imagery", "stac": {"api": "pc", "collection": c["id"]}, "thumbnail": thumb,
        })
    return out


def _get_json(url, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=40)
            if r.status_code == 200:
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1 + i)
    return None


def harvest_gee() -> list[dict]:
    root = _get_json(GEE_ROOT)
    subcats = [l["href"] for l in root["links"] if l.get("rel") == "child"]
    coll_urls: list[str] = []
    with cf.ThreadPoolExecutor(16) as ex:
        for cat in ex.map(_get_json, subcats):
            if cat:
                coll_urls += [l["href"] for l in cat.get("links", []) if l.get("rel") == "child"]
    print(f"  Earth Engine: {len(subcats)} providers, {len(coll_urls)} collections", flush=True)
    out = []
    with cf.ThreadPoolExecutor(24) as ex:
        for c in ex.map(_get_json, coll_urls):
            if not c or c.get("deprecated") or c.get("gee:status") == "deprecated":
                continue
            gid = c["id"]
            bbox = first_bbox(c.get("extent", {}))
            t0, t1 = years(c.get("extent", {}).get("temporal", {}).get("interval"))
            res = gsd_from(c)
            res_src = "metadata" if res else None
            if res is None:
                res = parse_resolution_m((c.get("title") or "") + ". " + (c.get("description") or ""))
                res_src = "parsed" if res else None
            preview = next((l["href"] for l in c.get("links", []) if l.get("rel") == "preview"), None)
            gtype = c.get("gee:type", "")
            out.append({
                "id": "gee:" + gid, "kind": "dataset", "title": c.get("title", gid),
                "provider": ", ".join(p.get("name", "") for p in c.get("providers", [])[:2]),
                "description": clip(c.get("description")), "keywords": " ".join(c.get("keywords", [])),
                "source": "Earth Engine", "access": "Open (Earth Engine account; YCGS supports GEE)", "access_class": "open",
                "url": "https://developers.google.com/earth-engine/datasets/catalog/" + gid.replace("/", "_"),
                "region": region_label(bbox), "bbox": bbox, "t0": t0, "t1": t1,
                "time": f"{t0 or '?'} to {t1 or 'present'}", "res_m": res, "res_source": res_src,
                "type": "Vector table" if gtype == "table" else "Raster / imagery", "gee_id": gid, "thumbnail": preview,
            })
    return out


# ---------------------------------------------------------------------------- GeoLibre
def _yrs(text: str) -> tuple[int | None, int | None]:
    ys = [int(y) for y in re.findall(r"(?<![\d-])(19\d\d|20[0-4]\d)(?!\d)", text or "")]
    return (min(ys), max(ys)) if ys else (None, None)


_UNIT = (("census tract", "Census tract"), ("county", "County"), ("counties", "County"), ("state", "State"),
         ("country", "Country"), ("countries", "Country"), ("city", "City"), ("cities", "City"))


def _gallery_item(slug, title, caption, provider, url, topic="", theme=""):
    try:
        proj = geolibre.fetch_project(slug, cache_dir=GL_CACHE, timeout=180)
    except (ValueError, requests.RequestException) as e:
        print(f"  GeoLibre: skipped {slug}: {e}", flush=True)
        return None
    sm = geolibre.summarize(proj)
    text = " ".join([title, caption, sm["description"]])
    t0, t1 = _yrs(text)
    live = re.search(r"last \d+ days|right now|today|live|forecast", text, re.I)
    kinds = {l["type"] for l in sm["layers"] if l.get("map", True)}
    typ = ("Map layers and imagery tiles" if {"geojson", "tiles"} <= kinds else "Imagery tiles" if kinds == {"tiles"}
           else "Map layers (GeoJSON snapshot)" if kinds else "Map (opens in GeoLibre)")
    low = text.lower()
    unit = next((u for k, u in _UNIT if re.search(r"\b" + k + r"\b", low)), None)
    desc = caption.rstrip(".")
    if sm["description"] and sm["description"][:40].lower() not in desc.lower():
        desc += ". " + sm["description"]
    return {
        "id": "gl:" + slug, "kind": "dataset", "title": title, "provider": provider or "GeoLibre",
        "description": clip(desc),
        "keywords": " ".join(filter(None, [theme.replace("-", " "), topic, provider, "geolibre map",
                                           " ".join(l["name"] for l in sm["layers"]),
                                           " ".join(f.replace("_", " ") for f in sm["fields"])])),
        "source": "GeoLibre gallery", "access": f"Open (GeoLibre open data gallery; original data: {provider or 'see the map'})",
        "access_class": "open", "url": url, "bbox": sm["bbox"],
        # US maps with Alaska's Aleutians span the antimeridian, so their box alone reads as "Regional"
        "region": ("US" if re.search(r"\b(US|U\.S\.|United States|American)\b", title + " " + caption)
                   else region_label(sm["bbox"]) if sm["bbox"] else None),
        "t0": t0, "t1": t1,
        "time": ("Snapshot of a live feed" if live and not t0 else f"{t0}" if t0 and t0 == t1
                 else f"{t0} to {t1}" if t0 else "Snapshot"),
        "res_m": None, "res_source": None, "type": typ, "unit": unit,
        "thumbnail": f"{geolibre.ASSET_HOST}/images/{slug}.webp",
        "geolibre": {"slug": slug, "viewer": geolibre.viewer_url(slug), "layers": sm["layers"][:12],
                     "mappable": sm["mappable"], "bbox_from": sm["bbox_from"]},
    }


def harvest_geolibre_gallery() -> list[dict]:
    gal = _get_json(GL_RAW + "scripts/demo-gallery.json") or {}
    starters = (_get_json(GL_RAW + "apps/geolibre-desktop/src/lib/starter-projects.json") or {}).get("examples", [])
    owner = gal.get("owner") or "giswqs"
    jobs = [(d["slug"], d.get("title") or d["slug"], d.get("caption") or "", d.get("source") or "",
             f"https://share.geolibre.app/{owner}/{d['slug']}", d.get("topic") or "", d.get("theme") or "")
            for d in gal.get("demos", []) if geolibre.SLUG_RE.fullmatch(d.get("slug") or "")]
    have = {j[0] for j in jobs}
    for st in starters:
        m = re.fullmatch(re.escape(geolibre.ASSET_HOST) + r"/projects/([a-z0-9-]+)\.geolibre\.json", st.get("projectUrl") or "")
        if m and m.group(1) not in have:
            jobs.append((m.group(1), st.get("title") or m.group(1), st.get("description") or "", "",
                         geolibre.viewer_url(m.group(1)), "", ""))
    with cf.ThreadPoolExecutor(6) as ex:
        out = [x for x in ex.map(lambda j: _gallery_item(*j), jobs) if x]
    print(f"  GeoLibre gallery: {len(out)} of {len(jobs)} maps", flush=True)
    return out


def harvest_geolibre_services() -> list[dict]:
    raw = json.load(open(os.path.join(DATA, "geolibre_services.json"), encoding="utf-8"))
    src = ""
    for f in ("apps/geolibre-desktop/src/components/layout/add-data/constants.ts",
              "apps/geolibre-desktop/src/components/layout/add-data/service-library.ts"):
        try:
            src += requests.get(GL_RAW + f, headers=UA, timeout=40).text
        except requests.RequestException:
            pass
    out = []
    for c in raw["services"]:
        sv = c["service"]
        probe = sv.get("endpoint") or sv.get("tiles") or sv.get("url")
        if src and probe not in src:
            print(f"  GeoLibre services: {c['id']} endpoint no longer in GeoLibre's source; check it", flush=True)
        c = dict(c)
        c.update(kind="dataset", source="GeoLibre services", access="Open (public web service, listed in GeoLibre)",
                 access_class="open", bbox=region_bbox({"contiguous us": "conus"}.get(c["region"].lower(), c["region"])),
                 res_source="curated" if c.get("res_m") else None)
        c.setdefault("t0", None)
        c.setdefault("t1", None)
        out.append(c)
    return out


def _common_title(titles: list[str]) -> str:
    if len(titles) == 1:
        return titles[0]
    heads = {t.split(":")[0].strip() for t in titles}
    if len(heads) == 1:
        return heads.pop()
    p = os.path.commonprefix(titles)
    if not p.endswith(" "):
        p = re.sub(r"\S*$", "", p)
    p = p.strip(" :-_(")
    return p if len(p) >= 12 else titles[0]


def harvest_gee_community() -> list[dict]:
    rows = _get_json(GEEC_URL) or []
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("thematic_group") == "Insiders only datasets" or not r.get("id"):
            continue  # insiders datasets need a paid sponsorship
        groups.setdefault(r.get("docs") or r["id"], []).append(r)
    kind_of = {"table": "Vector table", "feature_collection": "Vector table"}
    out = []
    for docs, rs in groups.items():
        slug = re.sub(r"[^a-z0-9]+", "-", docs.lower().rstrip("/").rsplit("/", 1)[-1]).strip("-")
        slug = slug or re.sub(r"[^a-z0-9]+", "-", rs[0]["id"].lower()).strip("-")
        titles = [r["title"] for r in rs]
        title = _common_title(titles)
        tags = list(dict.fromkeys(t.strip() for r in rs for t in (r.get("tags") or "").split(",") if t.strip()))[:30]
        t0, t1 = _yrs(" ".join(titles))
        blob = (title + " " + " ".join(tags)).lower()
        region = ("Global" if re.search(r"\bglobal\b|\bworld", blob)
                  else "US" if re.search(r"united states|\busa?\b|conus|\bnhd\b|\bnwi\b|\bepa\b", blob) else None)
        kinds = [kind_of.get(r.get("type"), "Raster / imagery") for r in rs]
        typ = max(set(kinds), key=kinds.count)
        lic = rs[0].get("license") or "see the product page"
        parts = (f"{len(rs)} Earth Engine assets (for example {rs[0]['id']})" if len(rs) > 1
                 else f"Earth Engine asset {rs[0]['id']}")
        out.append({
            "id": "geec:" + slug[:60], "kind": "dataset", "title": title, "provider": (rs[0].get("provider") or "").strip(),
            "description": clip(f"{rs[0].get('thematic_group') or 'Community dataset'}. {parts}. License: {lic}."),
            "keywords": " ".join(tags + [rs[0].get("thematic_group") or "", "earth engine community"]),
            "source": "GEE community catalog",
            "access": "Open (Earth Engine account; community-contributed, not reviewed by Google)",
            "access_class": "open", "url": docs if docs.startswith("http") else "https://gee-community-catalog.org/",
            "region": region, "bbox": region_bbox(region), "t0": t0, "t1": t1,
            "time": f"{t0} to {t1}" if t0 and t1 != t0 else f"{t0}" if t0 else "See the product page",
            "res_m": parse_resolution_m(title + ". " + " ".join(tags)), "res_source": None, "type": typ,
            "gee_id": rs[0]["id"], "gee_parts": len(rs), "thumbnail": rs[0].get("thumbnail"), "license": lic,
            "sample_code": rs[0].get("sample_code"),
        })
    seen: dict[str, int] = {}
    for it in out:  # two product pages can share a last path segment
        n = seen.get(it["id"], 0)
        seen[it["id"]] = n + 1
        if n:
            it["id"] += f"-{n + 1}"
    print(f"  GEE community: {len(rows)} assets grouped into {len(out)} product cards", flush=True)
    return out


def harvest_worldbank() -> list[dict]:
    """World Development Indicators (about 1,500 country-year statistics), non-spatial tables joinable by country."""
    data = _get_json("https://api.worldbank.org/v2/sources/2/indicators?format=json&per_page=3000") or []
    rows = data[1] if isinstance(data, list) and len(data) > 1 else []
    out = []
    for r in rows:
        iid = r.get("id")
        if not iid or not r.get("name"):
            continue
        topics = " ".join(t.get("value", "") for t in (r.get("topics") or []) if isinstance(t, dict))
        out.append({
            "id": "wb:" + iid, "kind": "dataset", "title": r["name"], "provider": "World Bank (World Development Indicators)",
            "description": clip(r.get("sourceNote")), "keywords": f"{topics} world bank wdi country statistics indicator",
            "source": "World Bank WDI", "access": "Open (World Bank API, no key)", "access_class": "open",
            "url": f"https://data.worldbank.org/indicator/{iid}", "region": "Global", "bbox": [-180.0, -90.0, 180.0, 90.0],
            "t0": 1960, "t1": None, "time": "1960 to latest year (varies by country)", "res_m": None, "res_source": None,
            "type": "Table (country-year statistics)", "unit": "Country", "data_type": "table", "wb_indicator": iid,
        })
    print(f"  World Bank WDI: {len(out)} indicators", flush=True)
    return out


def load_curated() -> list[dict]:
    cur = json.load(open(os.path.join(DATA, "curated.json"), encoding="utf-8"))
    out = []
    for c in cur["datasets"]:
        c = dict(c)
        c["source"] = "YCGS curated"
        c.setdefault("bbox", region_bbox(c.get("region")))
        c["res_source"] = "curated" if c.get("res_m") else None
        out.append(c)
    for c in cur["tools"] + cur["methods"]:
        c = dict(c)
        c["source"] = "YCGS curated"
        out.append(c)
    return out


def check_urls(items):
    def head(c):
        if not c.get("url"):
            return c["id"], None
        try:
            r = requests.get(c["url"], headers={"User-Agent": "Mozilla/5.0"}, timeout=20, allow_redirects=True, stream=True)
            return c["id"], r.status_code
        except requests.RequestException:
            return c["id"], "ERR"
    with cf.ThreadPoolExecutor(12) as ex:
        return dict(ex.map(head, items))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-gee", action="store_true")
    ap.add_argument("--skip-geolibre", action="store_true")
    ap.add_argument("--check-urls", action="store_true", help="check curated URLs (slow)")
    a = ap.parse_args()
    t = time.time()
    items = load_curated()
    print("curated", len(items), flush=True)
    pc = harvest_pc()
    print("planetary computer", len(pc), flush=True)
    gee = [] if a.skip_gee else harvest_gee()
    print("earth engine", len(gee), flush=True)
    gl = [] if a.skip_geolibre else harvest_geolibre_gallery()
    gls = harvest_geolibre_services()
    geec = [] if a.skip_geolibre else harvest_gee_community()
    wb = harvest_worldbank()
    print("geolibre gallery", len(gl), "services", len(gls), "gee community", len(geec), "world bank", len(wb), flush=True)
    items += pc + gee + gl + gls + geec + wb
    if a.check_urls:
        st = check_urls([i for i in items if i["source"] == "YCGS curated"])
        for i in items:
            if i["id"] in st:
                i["url_status"] = st[i["id"]]
        bad = {k: v for k, v in st.items() if v not in (200, None)}
        print("curated URLs not 200 (often bot blocks, check by hand):", bad)
    meta = {"built": dt.date.today().isoformat(), "counts": {
        "curated": sum(i["source"] == "YCGS curated" for i in items),
        "planetary_computer": len(pc), "earth_engine": len(gee), "geolibre_gallery": len(gl),
        "geolibre_services": len(gls), "gee_community": len(geec), "world_bank": len(wb), "total": len(items)},
        "with_resolution": sum(1 for i in items if i.get("res_m"))}
    json.dump({"meta": meta, "items": items}, open(os.path.join(DATA, "catalog.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(meta, f"{time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
