"""Data files downloaded to the server for one conversation, drawn on the map, deleted when its session ends.

Each session gets runtime/downloads/<sid>/ holding the downloaded files and a manifest of the map layers made
from them (the layers live in the shared layer store, so the map and GeoLibre load them by URL). purge() removes
both. A session ends when it expires (6 hours idle), when the user deletes the conversation, or when the server
restarts; after a restart the janitor removes the orphaned folder once a grace period has passed.

Readable formats (no GDAL on the server): GeoJSON, CSV with latitude/longitude (or WKT point) columns, KML/KMZ,
and zipped shapefiles (pyshp, reprojected with pyproj). Links must be public https; every redirect is checked.
"""
from __future__ import annotations

import csv
import io
import json
import re
import secrets
import shutil
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

from . import session, sources, vector
from .config import RUNTIME_DIR
from .session import layer_path, save_layer

DOWNLOAD_DIR = RUNTIME_DIR / "downloads"
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_SESSION_FILES = 6
MAX_SESSION_BYTES = 150 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 ** 3        # all sessions together; downloads are refused above this
MAX_UNZIPPED = 300 * 1024 * 1024       # zip bomb guard
MAX_ROWS = 300_000
MAX_LAYERS_PER_FILE = 5
FETCH_SECONDS = 180
FORMATS = "GeoJSON, CSV with latitude and longitude columns, KML or KMZ, or a zipped shapefile"
UA = {"User-Agent": "Y-finder/0.1 (YCGS data guide; +https://app.researcharchitect.ai/y-finder/)"}
_SID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_dir_lock = threading.Lock()


class DownloadError(ValueError):
    pass


# ---------------------------------------------------------------------------- storage and cleanup
def session_dir(sid: str) -> Path:
    if not _SID.fullmatch(sid or ""):
        raise DownloadError("Bad session id")
    return DOWNLOAD_DIR / sid


def _manifest_path(d: Path) -> Path:
    return d / "manifest.json"


def read_manifest(d: Path) -> dict:
    try:
        return json.loads(_manifest_path(d).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"files": []}


def _write_manifest(d: Path, m: dict) -> None:
    tmp = d / "manifest.part"
    tmp.write_text(json.dumps(m), encoding="utf-8")
    tmp.replace(_manifest_path(d))


def dir_bytes(d: Path) -> int:
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file()) if d.exists() else 0


def usage() -> int:
    return dir_bytes(DOWNLOAD_DIR)


def purge_dir(d: Path) -> int:
    """Delete a session's downloaded files and the map layers made from them. Returns bytes freed."""
    freed = 0
    for f in read_manifest(d).get("files", []):
        for lid in f.get("layers", []):
            p = layer_path(lid)
            if p:
                freed += p.stat().st_size
                p.unlink(missing_ok=True)
    freed += dir_bytes(d)
    shutil.rmtree(d, ignore_errors=True)
    return freed


def purge(s) -> int:
    sid = s if isinstance(s, str) else s.sid
    try:
        d = session_dir(sid)
    except DownloadError:
        return 0
    with _dir_lock:
        return purge_dir(d) if d.exists() else 0


session.END_HOOKS.append(purge)


# ---------------------------------------------------------------------------- fetching
def _filename(r: requests.Response, url: str) -> str:
    cd = r.headers.get("Content-Disposition") or ""
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    name = unquote(m.group(1)) if m else unquote(urlparse(url).path.rsplit("/", 1)[-1])
    name = re.sub(r"[^\w.\- ]+", "_", name).strip(" .")[:100]
    return name or "download"


def _http_get(url: str):
    # Redirects are followed by fetch(), one checked hop at a time, never by requests.
    return requests.get(url, headers=UA, stream=True, timeout=(15, 60), allow_redirects=False)


def fetch(url: str, dest: Path, max_bytes: int | None = None) -> dict:
    """Stream a public https file to dest, checking every redirect hop and the size as it arrives."""
    started = time.time()
    cap = max_bytes or MAX_FILE_BYTES
    for _hop in range(6):
        if not sources.safe_url(url):
            raise DownloadError("Only public https links can be downloaded")
        try:
            r = _http_get(url)
        except requests.RequestException as e:
            raise DownloadError(f"Could not reach the server ({type(e).__name__})")
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
            url = urljoin(url, r.headers["Location"])
            r.close()
            continue
        if r.status_code != 200:
            r.close()
            raise DownloadError(f"The server answered {r.status_code}")
        size = int(r.headers.get("Content-Length") or 0)
        if size > cap:
            r.close()
            raise DownloadError(f"The file is {size / 1e6:.0f} MB; the limit is {cap // 2**20} MB")
        n = 0
        try:
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    n += len(chunk)
                    if n > cap:
                        raise DownloadError(f"The file is larger than {cap // 2**20} MB")
                    if time.time() - started > FETCH_SECONDS:
                        raise DownloadError("The download took too long")
                    fh.write(chunk)
        except (DownloadError, requests.RequestException, OSError) as e:
            dest.unlink(missing_ok=True)
            raise e if isinstance(e, DownloadError) else DownloadError("The download was interrupted")
        finally:
            r.close()
        return {"name": _filename(r, url), "bytes": n, "content_type": (r.headers.get("Content-Type") or "").split(";")[0].lower(),
                "final_url": url}
    raise DownloadError("Too many redirects")


def sniff(name: str, ctype: str, head: bytes) -> str:
    low = name.lower()
    h = head.lstrip()[:200].lower()
    if head[:4] == b"PK\x03\x04":
        return "kmz" if low.endswith(".kmz") else "zip"
    if h.startswith((b"<!doctype html", b"<html")) or "html" in ctype:
        raise DownloadError("This link is a web page, not a data file. Use the direct download link.")
    if low.endswith((".geojson", ".json")) or "json" in ctype or h[:1] in (b"{", b"["):
        return "geojson"
    if low.endswith(".kml") or b"<kml" in head[:2000].lower():
        return "kml"
    if low.endswith((".csv", ".tsv", ".txt")) or ctype in ("text/csv", "text/plain", "text/tab-separated-values"):
        return "csv"
    if low.endswith((".tif", ".tiff", ".nc", ".hdf", ".h5", ".jp2", ".img")):
        raise DownloadError("Raster files cannot be drawn here yet. Use an imagery preview or open the file in GeoLibre or QGIS.")
    if low.endswith((".gpkg", ".gdb", ".fgb", ".parquet", ".pmtiles")):
        raise DownloadError(f"This format cannot be read here yet. Readable formats: {FORMATS}.")
    raise DownloadError(f"Unrecognized file type. Readable formats: {FORMATS}.")


# ---------------------------------------------------------------------------- reading formats
def _lonlat_ok(feats: list[dict]) -> bool:
    for f in feats[:2000]:
        c = (f.get("geometry") or {}).get("coordinates")
        while isinstance(c, list) and c and isinstance(c[0], list):
            c = c[0]
        if isinstance(c, list) and len(c) >= 2 and isinstance(c[0], (int, float)):
            if abs(c[0]) > 180 or abs(c[1]) > 90:
                return False
    return True


def _reproject(feats: list[dict], crs_text: str | None) -> list[dict]:
    """To longitude/latitude (EPSG:4326). Without a coordinate system, coordinates must already look like degrees."""
    if not crs_text:
        if not _lonlat_ok(feats):
            raise DownloadError("The coordinates are not longitude/latitude and the file has no coordinate system")
        return feats
    from pyproj import CRS, Transformer  # imported here: only shapefiles and old GeoJSON need it
    try:
        crs = CRS.from_user_input(crs_text)
    except Exception:
        raise DownloadError("The file's coordinate system could not be read")
    if crs.equals(CRS.from_epsg(4326), ignore_axis_order=True) or (crs.is_geographic and crs.datum and
                                                                     re.search(r"WGS.?84|North American Datum 1983", crs.datum.name or "")):
        return feats  # degrees already (NAD83 differs from WGS84 by about a meter)
    tr = Transformer.from_crs(crs, 4326, always_xy=True)

    def conv(c):
        if isinstance(c, list) and c and isinstance(c[0], (int, float)):
            x, y = tr.transform(c[0], c[1])
            return [x, y]
        if isinstance(c, list) and c and isinstance(c[0], list) and c[0] and isinstance(c[0][0], (int, float)):
            xs, ys = tr.transform([p[0] for p in c], [p[1] for p in c])
            return [[a, b] for a, b in zip(xs, ys)]
        return [conv(x) for x in c] if isinstance(c, list) else c
    for f in feats:
        g = f.get("geometry")
        if g and g.get("type") != "GeometryCollection":
            g["coordinates"] = conv(g.get("coordinates"))
    if not _lonlat_ok(feats):
        raise DownloadError("The coordinates could not be converted to longitude/latitude")
    return feats


def _geojson(raw: bytes) -> list[dict]:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise DownloadError("The file is not valid JSON")
    if isinstance(data, list):
        feats = data
    elif data.get("type") == "FeatureCollection":
        feats = data.get("features") or []
    elif data.get("type") == "Feature":
        feats = [data]
    elif data.get("type") in ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon", "GeometryCollection"):
        feats = [{"type": "Feature", "properties": {}, "geometry": data}]
    elif "features" in data and isinstance(data.get("features"), list) and data["features"] and "attributes" in data["features"][0]:
        raise DownloadError("This is Esri JSON. Ask for the layer's FeatureServer URL and it can be mapped directly.")
    else:
        raise DownloadError("The JSON is not GeoJSON")
    feats = [f for f in feats[:MAX_ROWS] if isinstance(f, dict) and isinstance(f.get("geometry"), dict)]
    crs = ((data.get("crs") or {}).get("properties") or {}).get("name") if isinstance(data, dict) else None
    if crs and re.search(r"CRS84|EPSG::?4326$", crs):
        crs = None
    return _reproject(feats, crs)


_LAT = {"lat", "latitude", "y", "lat_dd", "latitude_dd", "ylat", "point_y", "decimallatitude", "lat_y", "latitud"}
_LON = {"lon", "lng", "long", "longitude", "x", "lon_dd", "longitude_dd", "xlong", "point_x", "decimallongitude", "lon_x",
        "long_x", "longitud"}
_WKT_POINT = re.compile(r"POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", re.I)
_PAREN = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")


def _csv(raw: bytes) -> list[dict]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    try:
        dialect = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = csv.DictReader(io.StringIO(text), dialect=dialect)
    cols = rows.fieldnames or []
    norm = {c: re.sub(r"[^a-z_]", "", (c or "").strip().lower()) for c in cols}
    lat = next((c for c in cols if norm[c] in _LAT), None)
    lon = next((c for c in cols if norm[c] in _LON), None)
    feats, skipped = [], 0
    first = None
    for i, row in enumerate(rows):
        if i >= MAX_ROWS:
            break
        if first is None:
            first = row
        x = y = None
        try:
            if lat and lon:
                x, y = float(row[lon]), float(row[lat])
            else:
                for c in cols:  # a WKT point or a Socrata-style "(lat, lon)" location column
                    v = row.get(c) or ""
                    m = _WKT_POINT.search(v)
                    if m:
                        x, y = float(m.group(1)), float(m.group(2))
                        break
                    m = _PAREN.search(v)
                    if m:
                        y, x = float(m.group(1)), float(m.group(2))
                        break
        except (TypeError, ValueError):
            pass
        if x is None or not (-180 <= x <= 180 and -90 <= y <= 90) or (x == 0 and y == 0):
            skipped += 1
            continue
        props = {str(k)[:60]: (v[:300] if isinstance(v, str) else v) for k, v in list(row.items())[:40] if k}
        feats.append({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [x, y]}})
    if not feats:
        raise DownloadError("No latitude/longitude columns found" + (f" (columns: {', '.join(cols[:12])})" if cols else ""))
    return feats


def _kml_coords(text: str) -> list[list[float]]:
    out = []
    for tok in (text or "").split():
        p = tok.split(",")
        try:
            out.append([float(p[0]), float(p[1])])
        except (ValueError, IndexError):
            continue
    return out


def _kml_geom(el) -> dict | None:
    tag = el.tag.rsplit("}", 1)[-1]
    if tag == "Point":
        c = _kml_coords(el.findtext("{*}coordinates"))
        return {"type": "Point", "coordinates": c[0]} if c else None
    if tag == "LineString":
        c = _kml_coords(el.findtext("{*}coordinates"))
        return {"type": "LineString", "coordinates": c} if len(c) >= 2 else None
    if tag == "Polygon":
        rings = [_kml_coords(r.findtext("{*}coordinates")) for r in
                 el.findall("{*}outerBoundaryIs/{*}LinearRing") + el.findall("{*}innerBoundaryIs/{*}LinearRing")]
        rings = [r for r in rings if len(r) >= 4]
        return {"type": "Polygon", "coordinates": rings} if rings else None
    if tag == "MultiGeometry":
        parts = [g for g in (_kml_geom(c) for c in el) if g]
        kinds = {g["type"] for g in parts}
        if len(kinds) == 1 and parts:
            k = kinds.pop()
            return {"type": "Multi" + k, "coordinates": [g["coordinates"] for g in parts]}
        return {"type": "GeometryCollection", "geometries": parts} if parts else None
    return None


def _kml(raw: bytes) -> list[dict]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise DownloadError("The KML file could not be read")
    feats = []
    for pm in root.iterfind(".//{*}Placemark"):
        geom = next((g for g in (_kml_geom(c) for c in pm) if g), None)
        if not geom:
            continue
        props = {"name": (pm.findtext("{*}name") or "").strip()[:200]}
        desc = re.sub(r"<[^>]+>", " ", pm.findtext("{*}description") or "").strip()
        if desc:
            props["description"] = re.sub(r"\s+", " ", desc)[:300]
        for d in pm.iterfind(".//{*}Data"):
            if d.get("name"):
                props[d.get("name")[:60]] = (d.findtext("{*}value") or "")[:300]
        for d in pm.iterfind(".//{*}SimpleData"):
            if d.get("name"):
                props[d.get("name")[:60]] = (d.text or "")[:300]
        feats.append({"type": "Feature", "properties": props, "geometry": geom})
        if len(feats) >= MAX_ROWS:
            break
    if not feats:
        raise DownloadError("The KML file has no placemarks with geometry")
    return feats


def _zip_layers(path: Path, name: str) -> list[tuple[str, list[dict]]]:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise DownloadError("The zip file is damaged")
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir() and "__MACOSX" not in i.filename]
        if len(infos) > 5000 or sum(i.file_size for i in infos) > MAX_UNZIPPED:
            raise DownloadError("The zip file is too large to unpack here")
        by_low = {i.filename.lower(): i for i in infos}
        shps = [i for i in infos if i.filename.lower().endswith(".shp")][:MAX_LAYERS_PER_FILE]
        out = []
        if shps:
            import shapefile  # pyshp
            for shp in shps:
                base = shp.filename[:-4]

                def part(ext, base=base):
                    i = by_low.get((base + ext).lower())
                    return zf.read(i) if i else None
                dbf, shx, prj, cpg = part(".dbf"), part(".shx"), part(".prj"), part(".cpg")
                enc = (cpg or b"").decode("ascii", "ignore").strip() or "utf-8"
                try:
                    rd = shapefile.Reader(shp=io.BytesIO(zf.read(shp)), dbf=io.BytesIO(dbf) if dbf else None,
                                          shx=io.BytesIO(shx) if shx else None, encoding=enc, encodingErrors="replace")
                    feats = []
                    for k, sr in enumerate(rd.iterShapeRecords()):
                        if k >= MAX_ROWS:
                            break
                        if sr.shape.shapeType == shapefile.NULL:
                            continue
                        g = sr.shape.__geo_interface__
                        props = {str(kk)[:60]: (str(v)[:300] if not isinstance(v, (int, float, bool)) and v is not None else v)
                                 for kk, v in list(sr.record.as_dict().items())[:40]} if dbf else {}
                        feats.append({"type": "Feature", "properties": props, "geometry": json.loads(json.dumps(g))})
                except (shapefile.ShapefileException, LookupError, ValueError, OSError) as e:
                    raise DownloadError(f"The shapefile {base.rsplit('/', 1)[-1]} could not be read ({e})")
                feats = _reproject(feats, prj.decode("latin-1") if prj else None)
                out.append((base.rsplit("/", 1)[-1], feats))
            return out
        for i in infos:  # a zip of GeoJSON, KML or CSV files
            low = i.filename.lower()
            reader = _geojson if low.endswith((".geojson", ".json")) else _kml if low.endswith(".kml") else _csv if low.endswith(".csv") else None
            if reader:
                out.append((i.filename.rsplit("/", 1)[-1].rsplit(".", 1)[0], reader(zf.read(i))))
            if len(out) >= MAX_LAYERS_PER_FILE:
                break
        if not out:
            raise DownloadError(f"No readable layer in the zip file. Readable formats: {FORMATS}.")
        return out


def read_layers(path: Path, fmt: str, name: str) -> list[tuple[str, list[dict]]]:
    stem = name.rsplit(".", 1)[0] or "Downloaded layer"
    if fmt == "zip":
        return _zip_layers(path, name)
    raw = path.read_bytes()
    if fmt == "kmz":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            kml = next((i for i in zf.infolist() if i.filename.lower().endswith(".kml")), None)
            if not kml or kml.file_size > MAX_UNZIPPED:
                raise DownloadError("The KMZ file has no readable KML inside")
            raw = zf.read(kml)
        fmt = "kml"
    return [(stem, {"geojson": _geojson, "csv": _csv, "kml": _kml}[fmt](raw))]


# ---------------------------------------------------------------------------- one download for one session
def track(s, layers: list[dict], label: str) -> None:
    """Record layers made for this session (map previews) so they are deleted when the session ends."""
    if not layers:
        return
    d = session_dir(s.sid)
    with _dir_lock:
        d.mkdir(parents=True, exist_ok=True)
        m = read_manifest(d)
        m["sid"] = s.sid
        m.setdefault("files", []).append({"name": label[:120], "layers": [l["id"] for l in layers], "at": time.time()})
        _write_manifest(d, m)
    for l in layers:
        l["session_only"] = True


_SAMPLE = re.compile(r"^[a-z0-9_-]{1,60}\.csv$")


def save_sample(s, name: str, text: str) -> str:
    """A small CSV slice kept in the session folder (deleted with it). Returns the file name."""
    if not _SAMPLE.fullmatch(name):
        raise DownloadError("Bad sample name")
    d = session_dir(s.sid)
    with _dir_lock:
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")
    return name


def sample_path(sid: str, name: str) -> Path | None:
    if not _SAMPLE.fullmatch(name or ""):
        return None
    try:
        p = session_dir(sid) / name
    except DownloadError:
        return None
    return p if p.is_file() else None


def add(s, url: str, title: str | None = None, max_bytes: int | None = None) -> dict:
    """Download url for session s, turn it into map layers and record it. Caller holds the session lock."""
    url = (url or "").strip()
    if not url.lower().startswith("https://"):
        raise DownloadError("Give a direct https link to the file")
    if len(s.downloads) >= MAX_SESSION_FILES:
        raise DownloadError(f"This conversation already has {MAX_SESSION_FILES} downloaded files; start a new conversation for more")
    d = session_dir(s.sid)
    if dir_bytes(d) > MAX_SESSION_BYTES:
        raise DownloadError("This conversation's download space is full")
    if usage() > MAX_TOTAL_BYTES:
        from . import janitor
        janitor.sweep()
        if usage() > MAX_TOTAL_BYTES:
            raise DownloadError("The server's download space is full right now; try again later")
    with _dir_lock:
        d.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(6)
    raw_path = d / f"{token}.bin"
    info = fetch(url, raw_path, max_bytes)
    try:
        with open(raw_path, "rb") as fh:
            head = fh.read(4096)
        fmt = sniff(info["name"], info["content_type"], head)
        parts = read_layers(raw_path, fmt, info["name"])
    except DownloadError as e:
        if info["bytes"] < 1024:  # a short text reply ("404 File Not Found" sent as 200) explains more than the parser can
            try:
                said = re.sub(r"\s+", " ", raw_path.read_bytes().decode("utf-8")).strip()[:80]
            except (OSError, UnicodeDecodeError):
                said = ""
            if said and not said.startswith(("{", "[", "<?xml")):
                e = DownloadError(f'{e}. The server sent only: "{said}"')
        raw_path.unlink(missing_ok=True)
        raise e
    except Exception as e:  # a malformed file must not take the request down
        raw_path.unlink(missing_ok=True)
        raise DownloadError(f"The file could not be read ({type(e).__name__})")
    host = urlparse(info["final_url"]).hostname or ""
    layers, notes = [], []
    for k, (stem, feats) in enumerate(parts[:MAX_LAYERS_PER_FILE]):
        fc, note, total = vector.slim(feats, s.scope.get("bbox"))
        lt = (title or stem) if len(parts) == 1 else f"{title or info['name']}: {stem}"
        if fc is None:
            notes.append({"layer": lt, "shown": False, "note": note})
            continue
        layer = save_layer(fc, lt[:100], f"Downloaded from {host} (kept for this session only)", style="download")
        layer["session_only"] = True
        layers.append(layer)
        notes.append({k2: v for k2, v in {"layer": lt, "shown": True, "features": layer["n"], "of": total if total != layer["n"] else None,
                                           "note": note}.items() if v})
    rec = {"url": url, "name": info["name"], "bytes": info["bytes"], "format": fmt, "file": raw_path.name,
           "layers": [l["id"] for l in layers], "at": time.time()}
    with _dir_lock:
        m = read_manifest(d)
        m["sid"] = s.sid
        m.setdefault("files", []).append(rec)
        _write_manifest(d, m)
    s.downloads.append(rec)
    return {"file": info["name"], "bytes": info["bytes"], "format": fmt, "layers": layers, "notes": notes}
