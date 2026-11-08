"""Build yfinder/data/catalog.json from live public catalogs plus the curated cards.

Sources
  * Microsoft Planetary Computer STAC collections (API, ~140 collections)
  * Google Earth Engine public catalog (STAC, ~1,000 collections, crawled)
  * yfinder/data/curated.json (hand-maintained Yale and topic cards, tools, methods)

Usage:  python scripts/build_catalog.py [--skip-gee] [--check-urls]
Runtime: about 2 to 4 minutes (the Earth Engine crawl dominates).
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
from yfinder.geo import parse_resolution_m, region_bbox  # noqa: E402

DATA = os.path.join(ROOT, "yfinder", "data")
UA = {"User-Agent": "Y-finder catalog builder/0.1 (+https://github.com/gladcolor)"}
PC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1/collections"
GEE_ROOT = "https://storage.googleapis.com/earthengine-stac/catalog/catalog.json"

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
    ap.add_argument("--check-urls", action="store_true", help="check curated URLs (slow)")
    a = ap.parse_args()
    t = time.time()
    items = load_curated()
    print("curated", len(items), flush=True)
    pc = harvest_pc()
    print("planetary computer", len(pc), flush=True)
    gee = [] if a.skip_gee else harvest_gee()
    print("earth engine", len(gee), flush=True)
    items += pc + gee
    if a.check_urls:
        st = check_urls([i for i in items if i["source"] == "YCGS curated"])
        for i in items:
            if i["id"] in st:
                i["url_status"] = st[i["id"]]
        bad = {k: v for k, v in st.items() if v not in (200, None)}
        print("curated URLs not 200 (often bot blocks, check by hand):", bad)
    meta = {"built": dt.date.today().isoformat(), "counts": {
        "curated": sum(i["source"] == "YCGS curated" for i in items),
        "planetary_computer": len(pc), "earth_engine": len(gee), "total": len(items)},
        "with_resolution": sum(1 for i in items if i.get("res_m"))}
    json.dump({"meta": meta, "items": items}, open(os.path.join(DATA, "catalog.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(meta, f"{time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
