"""Downloading data files for one session, drawing them, deleting them when the session ends, periodic cleanup."""
import io
import json
import os
import time
import zipfile

import pytest

from yfinder import downloads, geolibre, janitor, session, sources, watch
from yfinder.app import create_app

NEW_HAVEN = [-72.998, 41.246, -72.86, 41.351]


class Resp:
    def __init__(self, body=b"", status=200, headers=None):
        self.body, self.status_code, self.headers = body, status, headers or {}

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]

    def close(self):
        pass


@pytest.fixture()
def web(monkeypatch, tmp_path):
    """A fake internet: {url: Resp}. Only https://data.example and https://cdn.example are 'public'."""
    pages = {}
    calls = []
    monkeypatch.setattr(sources, "safe_url", lambda u: u.startswith(("https://data.example/", "https://cdn.example/")))
    monkeypatch.setattr(downloads.requests, "get", lambda url, **kw: calls.append((url, kw)) or pages.get(url, Resp(status=404)))
    monkeypatch.setattr(downloads, "DOWNLOAD_DIR", tmp_path / "downloads")
    monkeypatch.setattr(session, "LAYER_DIR", tmp_path / "layers")
    watch.WATCH_DIR = tmp_path / "watches"
    pages["calls"] = calls
    return pages


def _fc(*pts):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": f"p{i}"}, "geometry": {"type": "Point", "coordinates": list(p)}} for i, p in enumerate(pts)]}


def _shp_zip(epsg=None, n=3):
    import shapefile
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POINT)
    w.field("NAME", "C")
    if epsg:  # Connecticut State Plane (US feet) around New Haven
        from pyproj import Transformer
        tr = Transformer.from_crs(4326, epsg, always_xy=True)
        pts = [tr.transform(-72.93 + i * 0.001, 41.31) for i in range(n)]
    else:
        pts = [(-72.93 + i * 0.001, 41.31) for i in range(n)]
    for i, (x, y) in enumerate(pts):
        w.point(x, y)
        w.record(f"tree {i}")
    w.close()
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("trees/trees.shp", shp.getvalue())
        zf.writestr("trees/trees.shx", shx.getvalue())
        zf.writestr("trees/trees.dbf", dbf.getvalue())
        if epsg:
            from pyproj import CRS
            zf.writestr("trees/trees.prj", CRS.from_epsg(epsg).to_wkt("WKT1_ESRI"))
        zf.writestr("__MACOSX/._trees.shp", b"junk")
    return z.getvalue()


# ---------------------------------------------------------------------------- fetching safely
def test_fetch_checks_every_redirect_and_the_size(web, tmp_path):
    web["https://data.example/a.geojson"] = Resp(status=302, headers={"Location": "https://10.0.0.5/secret"})
    with pytest.raises(downloads.DownloadError, match="public https"):
        downloads.fetch("https://data.example/a.geojson", tmp_path / "x")
    assert all(kw.get("allow_redirects") is False for _, kw in web["calls"])  # redirects are never followed blindly
    web["https://data.example/b"] = Resp(status=301, headers={"Location": "/c.csv"})
    web["https://data.example/c.csv"] = Resp(b"lat,lon\n41.3,-72.9\n", headers={"Content-Type": "text/csv"})
    info = downloads.fetch("https://data.example/b", tmp_path / "y")
    assert info["final_url"] == "https://data.example/c.csv" and info["name"] == "c.csv" and info["bytes"] == 19
    web["https://data.example/big"] = Resp(headers={"Content-Length": str(downloads.MAX_FILE_BYTES + 1)})
    with pytest.raises(downloads.DownloadError, match="limit"):
        downloads.fetch("https://data.example/big", tmp_path / "z")


def test_fetch_stops_streams_over_the_limit(web, tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "MAX_FILE_BYTES", 1000)
    web["https://data.example/lie"] = Resp(b"x" * 5000)  # no Content-Length
    with pytest.raises(downloads.DownloadError, match="larger"):
        downloads.fetch("https://data.example/lie", tmp_path / "f")
    assert not (tmp_path / "f").exists()


@pytest.mark.parametrize("name,ctype,head,fmt", [
    ("a.geojson", "", b'{"type":"FeatureCollection"}', "geojson"),
    ("export", "application/json", b"[]", "geojson"),
    ("sites.csv", "", b"a,b", "csv"),
    ("x.kml", "", b"<?xml?><kml>", "kml"),
    ("x.kmz", "", b"PK\x03\x04", "kmz"),
    ("x.zip", "", b"PK\x03\x04", "zip"),
])
def test_sniff(name, ctype, head, fmt):
    assert downloads.sniff(name, ctype, head) == fmt


@pytest.mark.parametrize("name,ctype,head,msg", [
    ("page", "text/html", b"<!DOCTYPE html>", "web page"),
    ("dem.tif", "", b"II*\x00", "Raster"),
    ("x.gpkg", "", b"SQLite format 3", "cannot be read"),
])
def test_sniff_explains_what_it_cannot_read(name, ctype, head, msg):
    with pytest.raises(downloads.DownloadError, match=msg):
        downloads.sniff(name, ctype, head)


# ---------------------------------------------------------------------------- reading formats
def test_geojson_with_an_old_crs_member_is_reprojected():
    from pyproj import Transformer
    x, y = Transformer.from_crs(4326, 3857, always_xy=True).transform(-72.93, 41.31)
    doc = _fc((x, y))
    doc["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3857"}}
    f = downloads._geojson(json.dumps(doc).encode())
    assert f[0]["geometry"]["coordinates"] == pytest.approx([-72.93, 41.31], abs=1e-6)
    with pytest.raises(downloads.DownloadError, match="not longitude/latitude"):
        downloads._geojson(json.dumps(_fc((x, y))).encode())  # projected numbers without a crs
    with pytest.raises(downloads.DownloadError, match="Esri JSON"):
        downloads._geojson(json.dumps({"features": [{"attributes": {}, "geometry": {"x": 1, "y": 2}}]}).encode())


def test_csv_finds_coordinate_columns():
    f = downloads._csv(b"Site;LATITUDE;Longitude\nA;41.31;-72.93\nB;bad;-72.9\nC;0;0\n")
    assert len(f) == 1 and f[0]["geometry"]["coordinates"] == [-72.93, 41.31] and f[0]["properties"]["Site"] == "A"
    wkt = downloads._csv(b'id,location\n1,"POINT (-72.93 41.31)"\n2,"(41.30, -72.92)"\n')
    assert [x["geometry"]["coordinates"] for x in wkt] == [[-72.93, 41.31], [-72.92, 41.30]]
    with pytest.raises(downloads.DownloadError, match="columns: name, value"):
        downloads._csv(b"name,value\na,1\n")


def test_kml_and_kmz(tmp_path):
    kml = b"""<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>Pond</name><description><![CDATA[<b>Wet</b> area]]></description>
        <ExtendedData><Data name="depth"><value>2</value></Data></ExtendedData>
        <Polygon><outerBoundaryIs><LinearRing><coordinates>-72.93,41.31 -72.92,41.31 -72.92,41.32 -72.93,41.31</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
      <Placemark><name>Trail</name><MultiGeometry><LineString><coordinates>-72.9,41.3 -72.8,41.3</coordinates></LineString>
        <LineString><coordinates>-72.8,41.3 -72.7,41.4</coordinates></LineString></MultiGeometry></Placemark>
      <Placemark><name>No geometry</name></Placemark></Document></kml>"""
    f = downloads._kml(kml)
    assert [x["properties"]["name"] for x in f] == ["Pond", "Trail"]
    assert f[0]["properties"] == {"name": "Pond", "description": "Wet area", "depth": "2"} and f[1]["geometry"]["type"] == "MultiLineString"
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("doc.kml", kml)
    p = tmp_path / "ponds.kmz"
    p.write_bytes(z.getvalue())
    assert len(downloads.read_layers(p, "kmz", "ponds.kmz")[0][1]) == 2


def test_zipped_shapefile_in_state_plane_is_reprojected(tmp_path):
    p = tmp_path / "trees.zip"
    p.write_bytes(_shp_zip(epsg=2234))
    [(name, feats)] = downloads.read_layers(p, "zip", "trees.zip")
    assert name == "trees" and len(feats) == 3 and feats[0]["properties"] == {"NAME": "tree 0"}
    assert feats[0]["geometry"]["coordinates"] == pytest.approx([-72.93, 41.31], abs=1e-5)


def test_zip_bombs_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "MAX_UNZIPPED", 100)
    p = tmp_path / "bomb.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.geojson", b" " * 10_000)
    with pytest.raises(downloads.DownloadError, match="too large"):
        downloads.read_layers(p, "zip", "bomb.zip")


# ---------------------------------------------------------------------------- one session: download, draw, delete
@pytest.fixture()
def client(web):
    return create_app().test_client()


def test_download_endpoint_draws_and_session_end_deletes(client, web):
    web["https://data.example/trees.zip"] = Resp(_shp_zip(epsg=2234, n=5), headers={"Content-Type": "application/zip"})
    s = session.get(None)
    s.scope = {"bbox": NEW_HAVEN}
    r = client.post("/api/download", json={"sid": s.sid, "url": "https://data.example/trees.zip"})
    assert r.status_code == 200, r.json
    lay = r.json["events"][0]["layer"]
    assert lay["session_only"] is True and lay["style"] == "download" and lay["n"] == 5 and "data.example" in lay["source"]
    assert r.json["events"][-1] == {"type": "zoom", "bbox": NEW_HAVEN} and "deleted when the session ends" in r.json["result"]["note"]
    d = downloads.session_dir(s.sid)
    man = downloads.read_manifest(d)
    assert man["files"][0]["layers"] == [lay["id"]] and len(list(d.glob("*.bin"))) == 1
    assert session.layer_path(lay["id"]) and len(s.downloads) == 1 and s.layers[-1]["id"] == lay["id"]
    session.reset(s.sid)  # the user deleted the conversation: the session ends
    assert not d.exists() and session.layer_path(lay["id"]) is None


def test_download_refuses_pages_bad_links_and_too_many_files(client, web, monkeypatch):
    web["https://data.example/page"] = Resp(b"<!DOCTYPE html><html>", headers={"Content-Type": "text/html"})
    s = session.get(None)
    r = client.post("/api/download", json={"sid": s.sid, "url": "https://data.example/page"})
    assert r.status_code == 400 and "web page" in r.json["error"]
    assert not list(downloads.session_dir(s.sid).glob("*.bin"))           # unreadable files are not kept
    assert client.post("/api/download", json={"sid": s.sid, "url": "http://data.example/x.csv"}).status_code == 400
    assert client.post("/api/download", json={"sid": s.sid, "url": "https://intranet.local/x.csv"}).status_code == 400
    web["https://data.example/ok.csv"] = Resp(b"lat,lon\n41.3,-72.9\n")
    monkeypatch.setattr(downloads, "MAX_SESSION_FILES", 1)
    assert client.post("/api/download", json={"sid": s.sid, "url": "https://data.example/ok.csv"}).status_code == 200
    r = client.post("/api/download", json={"sid": s.sid, "url": "https://data.example/ok.csv"})
    assert r.status_code == 400 and "already has 1" in r.json["error"]


def test_download_is_rate_limited(client, web):
    from yfinder import app as app_mod
    app_mod._dl_rate.clear()
    codes = [client.post("/api/download", json={"url": "https://data.example/missing.csv"}).status_code for _ in range(21)]
    assert codes[:20] == [400] * 20 and codes[20] == 429
    app_mod._dl_rate.clear()


# ---------------------------------------------------------------------------- the janitor
def _old(p, days):
    t = time.time() - days * 86400
    os.utime(p, (t, t))


def test_janitor_ends_expired_sessions_and_removes_orphans(web, monkeypatch, tmp_path):
    web["https://data.example/a.geojson"] = Resp(json.dumps(_fc((-72.93, 41.31))).encode())
    s = session.get(None)
    with s.lock:
        lid = downloads.add(s, "https://data.example/a.geojson")["layers"][0]["id"]
    s.last = time.time() - session.SESSION_TTL - 10                   # idle for more than 6 hours
    orphan = downloads.DOWNLOAD_DIR / "deadbeef0001"                   # left behind by a restart
    orphan.mkdir(parents=True)
    (orphan / "x.bin").write_bytes(b"1234")
    _old(orphan, 1)
    fresh = downloads.DOWNLOAD_DIR / "deadbeef0002"                    # may be restored soon: kept for now
    fresh.mkdir()
    rep = janitor.sweep()
    assert rep["sessions_ended"] >= 1 and session.peek(s.sid) is None and session.layer_path(lid) is None
    assert not downloads.session_dir(s.sid).exists() and not orphan.exists() and fresh.exists()
    assert rep["orphan_downloads"] == 1 and rep["bytes_freed"] >= 4


def test_janitor_ages_out_layers_and_cache_but_keeps_live_ones(web, monkeypatch, tmp_path):
    monkeypatch.setattr(geolibre, "CACHE_DIR", tmp_path / "gl")
    keep_s = session.get(None)
    live = session.save_layer(_fc((-72.9, 41.3)), "live", "test")
    keep_s.layers.append(live)
    stale = session.save_layer(_fc((-72.9, 41.3)), "stale", "test")
    new = session.save_layer(_fc((-72.9, 41.3)), "new", "test")
    for l in (live, stale):
        _old(session.layer_path(l["id"]), 10)
    geolibre.CACHE_DIR.mkdir()
    (geolibre.CACHE_DIR / "old-map.json").write_text("{}")
    _old(geolibre.CACHE_DIR / "old-map.json", 30)
    (geolibre.CACHE_DIR / "recent-map.json").write_text("{}")
    rep = janitor.sweep()
    assert session.layer_path(stale["id"]) is None and session.layer_path(live["id"]) and session.layer_path(new["id"])
    assert not (geolibre.CACHE_DIR / "old-map.json").exists() and (geolibre.CACHE_DIR / "recent-map.json").exists()
    assert rep["layers"] == 1 and rep["geolibre"] == 1
    monkeypatch.setattr(janitor, "LAYER_MAX_BYTES", 0)                 # over the size cap: oldest unused go first
    janitor.sweep()
    assert session.layer_path(new["id"]) is None and session.layer_path(live["id"])


def test_janitor_thread_is_off_in_tests_and_starts_once(monkeypatch):
    assert janitor.start() is False                                    # YFINDER_JANITOR=0 from conftest
    monkeypatch.setenv("YFINDER_JANITOR", "1")
    monkeypatch.setattr(janitor, "_started", True)
    assert janitor.start() is False                                    # already running: no second thread
