"""Demo data audit (Nov 9): each preview shows the variable the question asks for, from the right file or service.
No network: services, portals and files are faked."""
import csv
import io
import json
import time
import zipfile

import pytest

from yfinder import browser, downloads, explore, previews, session, sources


@pytest.fixture()
def s(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "LAYER_DIR", tmp_path / "layers")
    monkeypatch.setattr(downloads, "DOWNLOAD_DIR", tmp_path / "downloads")
    ses = session.get(None)
    yield ses
    session.reset(ses.sid)


def square(x0, y0, d=0.1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x0 + d, y0], [x0 + d, y0 + d], [x0, y0 + d], [x0, y0]]]}


def graph(*nodes, edges=(), q="question"):
    return {"question": q, "outcome": nodes[0][0], "edges": [{"source": a, "target": b, "note": ""} for a, b in edges],
            "nodes": [{"id": i, "label": l, "role": "outcome" if k == 0 else "driver", "core": True, "observable": ""} for k, (i, l) in enumerate(nodes)]}


# ---------------------------------------------------------------------------- ACS: race shares when the factor is race
def test_acs_shows_race_shares_for_a_race_factor_and_skips_empty_tracts(s, monkeypatch):
    seen = {}

    def query(url, bbox, max_features=2000, simplify_deg=None, out_fields="*", where="1=1"):
        seen["url"], seen["fields"] = url, out_fields
        if "Income" in url:
            return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": square(-118.3 + i * 0.1, 34.0),
                    "properties": {"GEOID": str(i), "NAME": f"T{i}", "B19049_001E": 50000 + i * 1000}} for i in range(4)]}
        feats = [{"type": "Feature", "geometry": square(-118.3 + i * 0.1, 34.0),
                  "properties": {"GEOID": f"0603{i}", "NAME": f"Census Tract {i}", "B03002_001E": pop,
                                 "B03002_calc_pctHispLatE": lat, "B03002_calc_pctBlackE": blk, "B03002_calc_pctNHWhiteE": 100 - lat - blk}}
                 for i, (pop, lat, blk) in enumerate([(4000, 70, 10), (3000, 20, 5), (12, 100, 0), (5000, 40, 30)])]
        return {"type": "FeatureCollection", "features": feats}
    monkeypatch.setattr(sources, "arcgis_query", query)
    s.scope = {"bbox": [-118.4, 33.9, -117.8, 34.2]}
    s.graph = graph(("arrests", "Arrests"), ("race", "Latino and Black resident shares"))
    r = previews.build(s, {"ref": "cur:acs", "role": "core", "title": "ACS", "factors": ["race"], "why": "shares"})
    assert "Race_and_Hispanic_Origin" in seen["url"] and "B03002_calc_pctHispLatE" in seen["fields"]
    lay = r["layers"][0]
    assert lay["paint"]["label"] == "The share of Latino or Black residents (%)"
    shares = [f["properties"]["share_pct"] for f in json.loads(session.layer_path(lay["id"]).read_text())["features"]]
    assert shares == [80, 25, None, 70]  # the 12-person tract has no meaningful share
    s.graph = graph(("permits", "Housing permits"), ("demand", "Housing demand"))
    r2 = previews.build(s, {"ref": "cur:acs", "role": "core", "title": "ACS", "factors": ["demand"], "why": ""})
    assert "Income" in seen["url"] and r2["layers"][0]["paint"]["label"] == "Median household income (US$)"


# ---------------------------------------------------------------------------- CT ECO: the service the question needs
def test_ct_eco_picks_its_service_by_the_question(s):
    s.scope = {"bbox": [-72.99, 41.25, -72.86, 41.35], "time_start": "1850", "time_end": "1950"}
    s.graph = graph(("shoreline", "Shoreline position"), q="How did New Haven's waterfront shoreline change from 1850 to 1950?")
    r = previews.build(s, {"ref": "cur:cteco", "role": "core", "title": "CT ECO"})
    assert [l["title"] for l in r["layers"]] == ["CT ECO 1934 aerial photo mosaic", "CT ECO long-term shoreline change (historical shorelines)"]
    assert "Mosaic_1934_MAGIC/ImageServer/exportImage" in r["layers"][0]["tiles"][0] and r["layers"][1]["hidden"]
    s.scope = {"bbox": [-72.92, 41.29, -72.88, 41.33], "time_start": "2026-06-01", "time_end": "2026-08-31"}
    s.graph = graph(("larvae", "Culex larval risk"), ("canopy", "Tree canopy shade"), q="Which standing water sites support Culex mosquito larvae?")
    r = previews.build(s, {"ref": "cur:cteco", "role": "core", "title": "CT ECO"})
    assert r["layers"][0]["title"] == "CT ECO water 2021 (1 m)"  # the question's own words come first
    s.scope = {"bbox": [-118.4, 33.9, -117.8, 34.2]}
    assert previews.build(s, {"ref": "cur:cteco", "role": "core", "title": "CT ECO"})["status"] == "none"


# ---------------------------------------------------------------------------- NOAA HMS smoke, day by day
def _smoke_zip(density):
    import shapefile
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON)
    for f in ("Satellite", "Start", "End", "Density"):
        w.field(f, "C")
    w.poly([[[-73.5, 41.0], [-72.0, 41.0], [-72.0, 42.0], [-73.5, 42.0], [-73.5, 41.0]]])
    w.record("GOES-EAST", "2023158 1310", "2023158 1710", density)
    w.close()
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        for ext, b in (("shp", shp), ("shx", shx), ("dbf", dbf)):
            zf.writestr(f"hms.{ext}", b.getvalue())
    return z.getvalue()


def test_hms_maps_smoke_polygons_for_each_day(s, monkeypatch):
    class R:
        def __init__(self, url):
            day = int(url[-6:-4])
            self.status_code, self.content = (200, _smoke_zip("Heavy" if day == 7 else "Light")) if day in (6, 7) else (404, b"")
    monkeypatch.setattr(previews.requests, "get", lambda url, **kw: R(url))
    s.scope = {"bbox": [-73.7, 40.9, -71.8, 42.1], "time_start": "2023-06-05", "time_end": "2023-06-08"}
    r = previews.build(s, {"ref": "cur:hms", "role": "supporting", "title": "HMS"})
    assert r["status"] == "ready" and r["note"] == "Smoke on 2 of 4 days; heavy smoke on 1 days, first 2023-06-07"
    fc = json.loads(session.layer_path(r["layers"][0]["id"]).read_text())
    assert sorted(f["properties"]["date"] for f in fc["features"]) == ["2023-06-06", "2023-06-07"]


# ---------------------------------------------------------------------------- Socrata: an even sample over the years
def test_socrata_samples_every_year_and_keeps_the_portal_counts(s, monkeypatch):
    asked = []

    class R:
        def __init__(self, url, params):
            self.url, self.params = url, params or {}

        def json(self):
            if "/api/views/" in self.url:
                return {"columns": [{"fieldName": "arst_date", "dataTypeName": "calendar_date"}, {"fieldName": "chrg_desc", "dataTypeName": "text"}]}
            return [{"y": "2018", "n": "40000"}, {"y": "2019", "n": "30000"}, {"y": "2020", "n": "25000"}]
    monkeypatch.setattr(previews.requests, "get", lambda url, params=None, **kw: R(url, params))

    def fetch(url, dest, max_bytes=None):
        asked.append(url)
        y = 2018 if "2018-01-01" in url else 2019 if "2019-01-01" in url else 2020
        rows = [["arst_date", "chrg_desc", "lat", "lon"]] + [[f"{y}-03-01T00:00:00", "X", 34.0 + i * 1e-3, -118.2] for i in range(20)]
        buf = io.StringIO()
        csv.writer(buf).writerows(rows)
        dest.write_text(buf.getvalue())
        return {}
    monkeypatch.setattr(downloads, "fetch", fetch)
    monkeypatch.setattr(sources, "safe_url", lambda u: True)
    s.scope = {"bbox": [-118.5, 33.9, -118.0, 34.3], "time_start": "2018", "time_end": "2020"}
    meta = {"title": "Arrest Data", "soda": "https://data.lacity.org/resource/yru6-6re4.csv"}
    r = previews.build(s, {"ref": "https://data.lacity.org/d/yru6-6re4", "role": "core", "title": "Arrests", "meta": meta})
    assert len(asked) == 3 and all("%24limit=16666" in u for u in asked)                 # one even slice per year
    assert r["series"]["points"] == [[2018, 40000], [2019, 30000], [2020, 25000]]       # counts of every row on the portal
    assert "an even sample of up to 16,666 rows per year" in r["note"] and r["soda"]["date"] == "arst_date"


# ---------------------------------------------------------------------------- other pages and files
def test_malaria_atlas_pages_draw_maps_wms(s):
    s.scope = {"bbox": [34.4, -0.4, 35.3, 0.0]}
    r = previews.build(s, {"ref": "https://data.malariaatlas.org/printables?x=1", "role": "core", "title": "MAP",
                           "meta": {"url": "https://data.malariaatlas.org/printables?x=1"}})
    assert r["status"] == "ready" and "layers=Malaria:202406_Global_Pf_Parasite_Rate" in r["layers"][0]["tiles"][0]


def test_an_empty_table_is_not_a_preview(s):
    assert previews._table_result(s, {"ref": "x", "title": "t"}, {}, b"a,b\n", "f.txt", "src") is None
    links, _r = browser.rank([["https://googleads.g.doubleclick.net/pagead/f.txt", "", True],
                              ["https://data.example.org/files/towns.csv", "towns.csv", False]], ["https://www.google-analytics.com/x.json"])
    assert links == ["https://data.example.org/files/towns.csv"]


def test_hourly_profile_for_rush_hour_questions(s, monkeypatch):
    head = ['State Code', 'County Code', 'Site Num', 'Parameter Code', 'POC', 'Latitude', 'Longitude', 'Datum', 'Parameter Name',
            'Date Local', 'Time Local', 'Date GMT', 'Time GMT', 'Sample Measurement', 'Units of Measure']
    buf = io.StringIO()
    wr = csv.writer(buf, quoting=csv.QUOTE_ALL)
    wr.writerow(head)
    for day in ("2024-03-04", "2024-03-09"):  # a Monday and a Saturday
        for h in range(24):
            v = (30 if h == 7 else 12) if day == "2024-03-04" else 12
            wr.writerow(["06", "037", "1103", "42602", "1", "34.06", "-118.22", "WGS84", "NO2", day, f"{h:02d}:00", day, "", v, "Parts per billion"])
    wr.writerow(["06", "059", "0007", "42602", "1", "33.8", "-117.9", "WGS84", "NO2", "2024-03-04", "07:00", "", "", 99, "Parts per billion"])
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("hourly_42602_2024.csv", buf.getvalue())
    monkeypatch.setattr(previews, "_airdata_files", lambda: {"hourly_42602_2024.zip": 10})
    monkeypatch.setattr(downloads, "fetch", lambda url, dest, max_bytes=None: dest.write_bytes(z.getvalue()) or {})
    sr, note = previews._aqs_hourly(s, "42602", "NO2", 2024, {"06-037-1103": {}}, "2024-01-01", "2024-12-31")
    assert sr["x"] == "hour" and dict(sr["points"])[7] == 30 and dict(sr["compare"]["points"])[7] == 12
    assert note == "weekday NO2 peaks at 07:00 (30 ppb), 150% above weekends at that hour"   # the other county's monitor is left out


# ---------------------------------------------------------------------------- the explorer reads them right
def _wait(s, timeout=15):
    t0 = time.time()
    while explore.public(s)["status"] in ("running", "waiting") and time.time() - t0 < timeout:
        time.sleep(0.05)
    return explore.public(s)


def test_an_hourly_series_is_read_as_a_daily_cycle(s):
    wk = [[h, 30 if h == 7 else 10 + (h % 5)] for h in range(24)]
    we = [[h, 12] for h in range(24)]
    s.scope = {"bbox": [-118.5, 33.9, -118.0, 34.3]}
    s.graph = graph(("no2", "Hourly NO2 patterns"))
    s.picks = [{"ref": "cur:aqs", "title": "AQS", "factors": ["no2"], "role": "core"}]
    s.previews = {"cur:aqs": {"status": "ready", "layers": [], "note": "", "series": {"label": "NO2 by hour of day, weekdays (ppb)", "x": "hour",
                                                                                      "points": wk, "compare": {"label": "weekends", "points": we}}}}
    explore.control(s, "start")
    f = _wait(s)["findings"][0]
    assert f["headline"].startswith("NO2 by hour of day, weekdays: on weekdays it peaks at 07:00") and "+150%" in f["detail"]
    assert "rose" not in f["headline"] and f["chart"]["points2"]


def test_smoke_days_are_matched_to_each_monitors_daily_value(s):
    zones = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": square(-73.0, 41.0, 1.0), "properties": {"date": "2023-06-07", "density": "Heavy"}},
        {"type": "Feature", "geometry": square(-73.0, 41.0, 1.0), "properties": {"date": "2023-06-06", "density": "Light"}}]}
    zl = session.save_layer(zones, "NOAA HMS smoke polygons, 2023-06-01 to 2023-06-30", "test")
    mons = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-72.5 + i * 0.05, 41.5]}, "properties": {"site": f"s{i}", "name": f"m{i}", "pm": 10.0}}
        for i in range(6)]}
    ml = session.save_layer(mons, "EPA AirData: PM2.5 monitors", "test")
    ml["paint"] = {"mode": "graduated", "property": "pm", "label": "PM2.5 (µg/m³)"}
    rows = [["site", "name", "pollutant", "date", "daily_value", "units"]]
    for i in range(6):
        for day, v in (("2023-06-05", 5), ("2023-06-06", 9), ("2023-06-07", 60), ("2023-06-08", 6)):
            rows.append([f"s{i}", f"m{i}", "PM2.5", day, v + i, "ug"])
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    name = downloads.save_sample(s, "aqs.csv", buf.getvalue())
    s.scope = {"bbox": [-73.7, 40.9, -71.8, 42.1], "time_start": "2023-06-01", "time_end": "2023-06-30"}
    s.graph = graph(("pm25", "Ambient PM2.5"), ("smoke", "Wildfire smoke"), edges=[("smoke", "pm25")])
    s.picks = [{"ref": "cur:aqs", "title": "AQS", "factors": ["pm25"], "role": "core"}, {"ref": "cur:hms", "title": "HMS", "factors": ["smoke"], "role": "supporting"}]
    s.previews = {"cur:aqs": {"status": "ready", "layers": [ml], "csv": name, "note": ""}, "cur:hms": {"status": "ready", "layers": [zl], "note": ""}}
    explore.control(s, "start")
    rel = next(f for f in _wait(s)["findings"] if f["kind"] == "relate")
    assert rel["headline"].startswith("PM2.5 is higher on days a monitor sits under heavier smoke polygons: median 62.5 under heavy")
    assert "light 11.5 (median of 6)" in rel["detail"] and rel["rho"] > 0.5 and [g[0] for g in rel["chart"]["groups"]] == ["No smoke", "Light", "Heavy"]


def test_matching_records_are_fetched_from_the_portal(s, monkeypatch):
    monkeypatch.setattr(explore, "_model_on", lambda: True)
    monkeypatch.setattr(explore, "_model_json", lambda ses, prompt, schema, name, max_tokens=1500:
                        {"column": "chrg_desc", "values": ["ILLEGAL STREET/SIDEWALK SALES"], "why": "vending"} if name == "subset" else None)
    asked = {}

    class R:
        def json(self):
            return [{"y": "2010", "n": "820"}, {"y": "2013", "n": "1145"}, {"y": "2017", "n": "17"}]
    monkeypatch.setattr("requests.get", lambda url, params=None, **kw: asked.update(where=params["$where"]) or R())

    def fetch(url, dest, max_bytes=None):
        rows = [["chrg_desc", "lat", "lon"]] + [["ILLEGAL STREET/SIDEWALK SALES", 34.04 + (i % 7) * 1e-3, -118.25 + (i % 5) * 1e-3] for i in range(60)]
        buf = io.StringIO()
        csv.writer(buf).writerows(rows)
        dest.write_text(buf.getvalue())
        return {}
    monkeypatch.setattr(downloads, "fetch", fetch)
    pts = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-118.3 + i * 1e-3, 34.0]},
                                                       "properties": {"chrg_desc": "ILLEGAL STREET/SIDEWALK SALES" if i < 3 else ("BURGLARY" if i % 2 else "DUI")}} for i in range(40)]}
    lay = session.save_layer(pts, "Arrest Data from 2010 to 2019", "test")
    s.scope = {"bbox": [-118.7, 33.6, -118.1, 34.4], "time_start": "2010", "time_end": "2019"}
    s.graph = graph(("arrests", "Street-vending arrests"))
    ref = "https://data.lacity.org/d/yru6-6re4"
    s.picks = [{"ref": ref, "title": "Arrests", "factors": ["arrests"], "role": "core"}]
    s.previews = {ref: {"status": "ready", "layers": [lay], "note": "a sample of 40",
                        "soda": {"url": "https://data.lacity.org/resource/yru6-6re4.csv", "date": "arst_date", "from": 2010, "to": 2019}}}
    explore.control(s, "start")
    f = _wait(s)["findings"][0]
    assert "chrg_desc in ('ILLEGAL STREET/SIDEWALK SALES')" in asked["where"] and "arst_date >= '2010-01-01" in asked["where"]
    assert f["headline"].startswith("1,982 records on the portal match the question, most in 2013")
    assert "fetched them all" in f["detail"] and f["chart"]["items"][1] == ["2013", 1145] and f["focus"]["add"]


def test_usgs_gages_show_annual_mean_flow_from_the_latest_published_year(s, monkeypatch):
    asked = []

    class R:
        def __init__(self, url, params):
            self.status_code = 200
            asked.append((url, dict(params)))
            if url.endswith("/site/"):
                rows = ["agency_cd\tsite_no\tstation_nm\tsite_tp_cd\tdec_lat_va\tdec_long_va", "5s\t15s\t50s\t7s\t16s\t16s"]
                rows += [f"USGS\t0548{i:04d}\tRiver {i}\tST\t{41.5 + i * 0.01}\t{-93.6 + i * 0.01}" for i in range(12)]
                self.text = "\n".join(rows)
            else:
                yr = params["startDt"]
                ids = params["sites"].split(",")
                assert len(ids) <= 10  # the statistics service refuses more
                self.text = "\n".join(["agency_cd\tsite_no\tparameter_cd\tts_id\tloc_web_ds\tyear_nu\tmean_va", "5s\t15s\t5s\t10n\t15s\t4s\t12n"]
                                      + ([f"USGS\t{i}\t00060\t1\t\t{yr}\t{100 + int(i[-2:])}" for i in ids] if yr == "2024" else []))
    monkeypatch.setattr(previews.requests, "get", lambda url, params=None, **kw: R(url, params or {}))
    s.scope = {"bbox": [-94.0, 41.3, -93.0, 42.0], "time_start": "2000", "time_end": "2026"}
    r = previews.build(s, {"ref": "cur:usgs_water", "role": "core", "title": "USGS"})
    assert r["note"].startswith("12 streamgages; annual mean flow 2024 for 12 of them")   # 2025 not published yet: 2024
    assert asked[0][1]["siteStatus"] == "active" and r["layers"][0]["paint"]["label"] == "Mean flow 2024 (cubic feet per second)"


def test_openstreetmap_previews_the_features_the_factor_names(s, monkeypatch):
    seen = {}

    def osm(tags, bbox, max_elements=4000, max_area_km2=2500):
        seen.update(tags=tags, area=max_area_km2)
        return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [34.7 + i * 0.01, -0.1]},
                                                           "properties": {"place": "village", "name": f"V{i}"}} for i in range(5)]}
    monkeypatch.setattr(sources, "osm_features", osm)
    s.scope = {"bbox": [34.4, -0.4, 35.3, 0.0]}
    s.graph = graph(("malaria", "Malaria risk"), ("villages", "Village locations"))
    r = previews.build(s, {"ref": "cur:osm", "role": "core", "title": "OSM", "factors": ["villages"], "why": "village buffers"})
    assert seen["tags"][0] == "place=village" and seen["area"] == 60000 and r["layers"][0]["n"] == 5
    s.graph = graph(("x", "Something else"))
    assert previews.build(s, {"ref": "cur:osm", "role": "core", "title": "OSM", "factors": ["x"]})["status"] == "none"


def test_scripts_and_pages_are_never_taken_for_data():
    links, _r = browser.rank([["https://cse.google.com/cse.js?cx=1", "", False], ["https://x.org/app.css", "", True],
                              ["https://x.org/data/trap_counts.csv", "Trap counts", False]], ["https://x.org/static/main.js"])
    assert links == ["https://x.org/data/trap_counts.csv"]
