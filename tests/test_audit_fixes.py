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

    def osm(tags, bbox, max_elements=4000, max_area_km2=2500, timeout_s=65):
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


def test_geocoder_prefers_the_district_to_its_office_and_retries_without_district(monkeypatch):
    from yfinder import sources
    asked = []

    def fake_get(url, params=None, timeout=0):
        asked.append(params["q"])
        if params["q"] == "Soroti District, Uganda":  # Nominatim's real answers: an office and a village of the same name
            return [{"display_name": "Soroti District Local Government", "lat": "1.70", "lon": "33.69", "category": "office",
                     "type": "government", "boundingbox": ["1.704", "1.705", "33.697", "33.698"]},
                    {"display_name": "Soroti, Kamuli, Eastern Region, Uganda", "lat": "0.94", "lon": "33.12", "category": "place",
                     "type": "village", "boundingbox": ["0.92", "0.96", "33.10", "33.14"]}]
        if params["q"] == "Soroti, Uganda":
            return [{"display_name": "Soroti, Eastern Region, Uganda", "lat": "1.75", "lon": "33.60", "category": "boundary",
                     "type": "administrative", "boundingbox": ["1.45", "2.05", "33.40", "34.00"]}]
        if params["q"] == "Fair Haven, New Haven":
            return [{"display_name": "Fair Haven, New Haven", "lat": "41.31", "lon": "-72.89", "category": "place",
                     "type": "suburb", "boundingbox": ["41.30", "41.32", "-72.90", "-72.88"]}]
        if params["q"] == "Kaberamaido, Uganda":
            return [{"display_name": "Kaberamaido, Eastern Region, Uganda", "lat": "1.7", "lon": "33.1", "category": "boundary",
                     "type": "administrative", "boundingbox": ["1.46", "1.91", "32.94", "33.31"]}]
        return []

    monkeypatch.setattr(sources, "_get", fake_get)
    monkeypatch.setattr(sources, "_cached", lambda key, ttl, fn: fn())
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    g = sources.geocode("Soroti District, Uganda")
    assert g["type"] == "administrative" and g["bbox"] == [33.40, 1.45, 34.00, 2.05]
    g = sources.geocode("Kaberamaido District, Uganda")
    assert g and g["name"].startswith("Kaberamaido") and asked[-2:] == ["Kaberamaido District, Uganda", "Kaberamaido, Uganda"]
    assert sources.geocode("Atlantis") is None and asked[-1] == "Atlantis"  # no admin word, no retry
    assert sources.geocode("Fair Haven, New Haven")["type"] == "suburb"  # no boundary and no admin word: the first answer


def test_the_united_states_geocodes_to_the_contiguous_states(monkeypatch):
    from yfinder import sources
    us = [{"display_name": "United States", "name": "United States", "addresstype": "country", "lat": "39.7", "lon": "-100",
           "category": "boundary", "type": "administrative", "boundingbox": ["-14.76", "71.59", "-180.0", "180.0"]}]
    monkeypatch.setattr(sources, "_get", lambda url, params=None, timeout=0: us)
    monkeypatch.setattr(sources, "_cached", lambda key, ttl, fn: fn())
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    g = sources.geocode("United States")
    assert g["bbox"] == [-124.85, 24.4, -66.88, 49.39] and "contiguous" in g["name"] and g["geometry"] is None


def test_portal_tables_get_their_region_and_years(monkeypatch):
    """A table from data.ct.gov covers Connecticut, and its date column gives its years (it used to be 'unknown')."""
    from yfinder import agent, sources
    from yfinder.session import Session
    cat = {"results": [{"resource": {"id": "fvw8-89kt", "name": "Residential Solar Investment Program (RSIP) Enrollment",
                                     "columns_field_name": ["kw_stc", "approved_date", "municipality"],
                                     "columns_datatype": ["Number", "Calendar date", "Text"], "data_updated_at": "2026-01-02"},
                        "metadata": {"domain": "data.ct.gov"}, "permalink": "https://data.ct.gov/d/fvw8-89kt"}]}
    calls = []

    def fake_get(url, params=None, timeout=0):
        calls.append((url, params))
        if "api/catalog" in url:
            return cat
        assert url == "https://data.ct.gov/resource/fvw8-89kt.json" and params == {"$select": "min(approved_date) as a, max(approved_date) as b"}
        return [{"a": "2012-03-01T00:00:00.000", "b": "2024-12-31T00:00:00.000"}]

    monkeypatch.setattr(sources, "_get", fake_get)
    monkeypatch.setattr(sources, "_cached", lambda key, ttl, fn: fn())
    monkeypatch.setenv("YFINDER_TABLE_SPANS", "1")
    rows = sources.socrata("solar", 3, "data.ct.gov")
    assert rows[0]["region"] == "Connecticut" and rows[0]["date_field"] == "approved_date"
    s = Session(sid="t-span")
    s.scope = {"bbox": [-73.7, 41.0, -71.8, 42.0], "time_start": "2012", "time_end": "2024"}
    s.remember(rows)
    agent.h_recommend_data(s, {"picks": [{"ref": rows[0]["ref"], "title": rows[0]["title"], "factors": [], "role": "core", "why": "",
                                          "limitations": "", "spatial_resolution_m": None, "time_start": None, "time_end": None,
                                          "coverage": None, "data_type": "table"}], "methods": [], "next_steps": []})
    p = s.picks[0]
    assert p["fit"]["extent"]["label"] == "good" and p["fit"]["time"]["label"] == "good", p["fit"]
    assert p["t0"] == 2012 and p["t1"] == 2024 and "dates in the table" in p["time"]
    assert sources._date_field(["x", "date"], ["Number", "Text"]) == "date"  # CDC's wastewater table keeps dates as text
    assert sources.socrata_span("https://evil.example/resource/abcd-1234.csv; drop", "date") is None


def test_a_mapped_boundary_layer_reports_its_extent():
    from yfinder import agent
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[-80.1, 8.8], [-79.3, 8.8], [-79.3, 9.4], [-80.1, 8.8]]]}}]}
    assert agent._fc_extent(fc) == [-80.1, 8.8, -79.3, 9.4]
    assert agent._fc_extent({"features": []}) is None


def test_scope_refuses_a_box_that_wraps_around_the_globe():
    """The model once set [-180, -14.6, 180, 71.6] for "the United States including territories": every US dataset
    then read as a partial fit. A world study is still fine."""
    import pytest
    from yfinder import agent
    from yfinder.session import Session
    s = Session(sid="t-wrap")
    base = {"extent_name": "x", "time_start": "2000", "time_end": "2020", "spatial_resolution_m": None,
            "unit_of_analysis": "county", "temporal_frequency": "annual", "assumed": [], "rationale": ""}
    with pytest.raises(agent.ToolError, match="180th meridian"):
        agent.h_set_scope(s, {**base, "bbox": [-180, -14.6, 180, 71.6]})
    agent.h_set_scope(s, {**base, "bbox": [-180, -60, 180, 80]})
    assert s.scope["bbox"] == [-180, -60, 180, 80]
    agent.h_set_scope(s, {**base, "bbox": [-124.85, 24.4, -66.88, 49.39]})
    assert s.scope["bbox"][0] == -124.85


def test_web_links_lose_their_tracking_parameters():
    from yfinder.websearch import clean_url
    u = "https://ysph.yale.edu/hrl/publications/reports/?utm_medium=email&_hsenc=p2AN&_hsmi=1132&utm_source=hs_email"
    assert clean_url(u) == "https://ysph.yale.edu/hrl/publications/reports/"
    assert clean_url("https://data.ct.gov/resource/x.csv?$limit=5&fbclid=abc") == "https://data.ct.gov/resource/x.csv?$limit=5"
    assert clean_url("https://a.org/q?b=1&a=%20x") == "https://a.org/q?b=1&a=%20x"  # untouched when nothing is dropped
    assert clean_url("https://example.org/page?id=7#top") == "https://example.org/page?id=7#top"


def test_showing_part_of_the_area_is_a_view_not_a_new_scope(monkeypatch):
    """Asked for an image of Lekki, the agent once reset the Lagos study to Lekki in 2026. A view box now searches imagery
    without touching the scope."""
    from yfinder import agent, sources
    from yfinder.session import Session
    s = Session(sid="t-view")
    s.scope = {"bbox": [2.7, 6.37, 4.35, 6.7], "time_start": "2000", "time_end": "2025"}
    seen = {}

    def fake_search(coll, bbox, dt_, cloud, limit):
        seen["bbox"] = bbox
        return [{"id": "S2_1", "collection": coll, "bbox": [3.6, 6.3, 4.3, 6.6], "datetime": "2026-01-24T10:00:00Z",
                 "geometry": {"type": "Polygon", "coordinates": [[[3.6, 6.3], [4.3, 6.3], [4.3, 6.6], [3.6, 6.3]]]}}]
    monkeypatch.setattr(sources, "stac_search", fake_search)
    view = [3.62, 6.38, 4.24, 6.56]
    agent.h_search_stac_items(s, {"collection": "sentinel-2-l2a", "datetime": "2026-01-01/2026-11-09", "max_cloud": 10,
                                  "limit": 3, "view_bbox": view})
    assert seen["bbox"] == view and s.scope["bbox"] == [2.7, 6.37, 4.35, 6.7]
    assert s.stac_items["S2_1"]["covers_pct"] == 100 and s.stac_items["S2_1"]["view_bbox"] == view
    agent.h_search_stac_items(s, {"collection": "sentinel-2-l2a", "datetime": None, "max_cloud": None, "limit": 3, "view_bbox": None})
    assert seen["bbox"] == [2.7, 6.37, 4.35, 6.7]


def test_download_pages_rank_files_that_name_the_cards_topic():
    """On the Atack page the browser took 1880StateSamples.xlsx (a census sample) for the railroad card; on STRI's page it
    took lake levels for a streamflow card. Link text and file names that name the topic now come first."""
    from yfinder import browser, previews
    words = browser.topic_words("Historical US railroads and canals, 1826-1911 Railroad access: historical railroad lines by year")
    anchors = [["https://my.vanderbilt.edu/x/1880StateSamples.xlsx", "1880 State Samples", False],
               ["https://my.vanderbilt.edu/x/RR1826-1911Modified103123.zip", "Railroads, 1826-1911", False]]
    ranked, _ = browser.rank(anchors, [], 1916, words)
    assert ranked[0].endswith("RR1826-1911Modified103123.zip")
    html = ('<a href="downloads/acp_lakelevel_15min.zip">Download</a> <a href="downloads/acp_discharge_15min.zip">Download</a>')
    links = previews._data_links("https://striresearch.si.edu/pm/", html, 2025,
                                 browser.topic_words("Dry-season streamflow: gauge discharge per basin"))
    assert links[0].endswith("acp_discharge_15min.zip")
    assert browser.topic_hits("acp_discharge_15min.zip", "", browser.topic_words("river discharge")) == 1
    assert browser.rank(anchors, [], 1880)[0][0].endswith("1880StateSamples.xlsx")  # no topic words: the old order


def test_code_columns_are_never_values():
    from yfinder import tables
    header = ["STATE", "CNTYCODE", "CITYCODE", "INDUSTRY", "GEOID", "StationID", "site_id", "No. of deaths", "humidity"]
    rows = [["IL", str(10 + i), "999", str(200 + i), f"17{i:03d}", str(i), str(i), str(i % 7), str(50 + i)] for i in range(40)]
    cols = [header[j] for j in tables.value_columns(header, rows, 0, {}, "deaths humidity", k=9)]
    assert "CNTYCODE" not in cols and "CITYCODE" not in cols and "GEOID" not in cols and "StationID" not in cols and "site_id" not in cols
    assert "No. of deaths" in cols and "humidity" in cols
    assert tables.is_code_column("CITYCODE") and tables.is_code_column("anything", [999.0] * 5 + [1.0, 2.0])
    assert not tables.is_code_column("total_system_cost", [1.0, 2.0, 3.0])


def test_curated_socrata_pages_are_read_through_the_api():
    from yfinder.previews import soda_from_url
    assert soda_from_url("https://data.ct.gov/d/rybz-nyjw") == "https://data.ct.gov/resource/rybz-nyjw.csv"
    assert soda_from_url("https://data.ct.gov/Health-and-Human-Services/Accidental-Drug-Related-Deaths-2012-2024/rybz-nyjw") == \
        "https://data.ct.gov/resource/rybz-nyjw.csv"
    assert soda_from_url("https://www.cdc.gov/nwss/") == "" and soda_from_url("https://example.org/a/b/c") == ""


def test_record_tables_map_rows_per_town_and_medians(monkeypatch):
    """One row per record (a death, a solar system): rows per town when no column matches the question."""
    from yfinder import previews
    from yfinder.session import Session
    s = Session(sid="t-rows")
    s.scope = {"bbox": [-73.7, 41.0, -71.8, 42.0]}
    s.graph = {"nodes": [{"id": "deaths", "label": "Fentanyl overdose deaths", "observable": "deaths by town"}]}
    sq = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    towns = {t: {"geometry": sq, "properties": {"name": t.title()}} for t in ("hartford", "new haven", "bristol")}
    monkeypatch.setattr(previews, "_towns", lambda s_: towns)
    seen = {}

    def fake_layer(s_, pick, feats, title, src, paint=None, *a, **k):
        seen["feats"], seen["paint"] = feats, paint
        return {"id": "x", "n": len(feats)}, ""
    monkeypatch.setattr(previews, "_vector_layer", fake_layer)
    rows = ["Date,Age,ResidenceCity"] + [f"2020-01-0{i % 9 + 1},{30 + i},{t}" for i, t in enumerate(["Hartford"] * 6 + ["New Haven"] * 4 + ["Bristol"] * 3)]
    pick = {"ref": "cur:ct_overdose", "factors": ["deaths"], "why": "deaths by town"}
    res = previews._table_result(s, pick, {"title": "Overdose deaths"}, "\n".join(rows).encode(), "deaths.csv", "test")
    by = {f["properties"]["town"]: f["properties"]["rows per town"] for f in seen["feats"]}
    assert by == {"Hartford": 6.0, "New Haven": 4.0, "Bristol": 3.0} and "one row per record" in res["note"]
    rows = ["SaleAmount,Town"] + [f"{p},{t}" for p, t in [(100, "Hartford"), (200, "Hartford"), (900, "Hartford"), (50, "Bristol"),
                                                           (60, "Bristol"), (70, "Bristol")]]
    s.graph = {"nodes": [{"id": "price", "label": "Home sale amount", "observable": "sale amount"}]}
    pick = {"ref": "cur:ct_real_estate", "factors": ["price"], "why": "sale amount"}
    res = previews._table_result(s, pick, {"title": "Sales"}, "\n".join(rows).encode(), "sales.csv", "test")
    by = {f["properties"]["town"]: f["properties"]["SaleAmount"] for f in seen["feats"]}
    assert by == {"Hartford": 200.0, "Bristol": 60.0} and "median" in res["note"]  # medians, not means pulled up by one sale


def test_a_zipped_csv_becomes_points():
    import io
    import zipfile
    from yfinder import previews
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mrds.csv", "site_name,latitude,longitude\nA,40.1,-89.2\nB,40.2,-89.3\n")
        z.writestr("mrds.met", "metadata")
    assert previews._zipped_csv(buf.getvalue())[0] == "mrds.csv"
    assert previews._zipped_csv(b"name,lat\n") is None


def test_explorer_names_must_tell_places_apart_and_small_steady_change_is_named():
    from yfinder import explore
    props = [{"state": "CT", "city": c} for c in ["Hartford", "New Haven", "Bristol", "Norwich", "Danbury"] * 3]
    assert explore._name_key(props) == "city"
    v = {"label": "Town population", "short": "Town population", "series": {"points": [[2015 + 5 * i, 2238 + 8 * i] for i in range(6)]}}
    f = explore.profile_series(v, None)
    assert "steadily but only" in f["headline"] and "no steady trend" not in f["headline"]


def test_osm_preview_takes_the_rule_with_most_matching_words(monkeypatch):
    from yfinder import previews, sources
    from yfinder.session import Session
    s = Session(sid="t-osm")
    s.scope = {"bbox": [-38.47, -12.94, -38.43, -12.91]}
    s.graph = {"nodes": [{"id": "sewers", "label": "Open sewers and drainage", "observable": "mapped open sewer channels"},
                         {"id": "blocks", "label": "Residential blocks", "observable": "community building clusters"}]}
    asked = {}
    monkeypatch.setattr(sources, "osm_features", lambda tags, bbox, **k: asked.update(tags=tags) or {"features": []})
    previews._osm(s, {"ref": "cur:osm", "factors": ["sewers", "blocks"], "why": "drains and sewers"}, {})
    assert asked["tags"][0] == "waterway=drain"


def test_a_big_zipped_csv_is_read_from_its_start(monkeypatch):
    """STRI's discharge zip unpacks to far more than the limit; it was skipped for the lake-level file."""
    import io
    import zipfile
    from yfinder import previews
    monkeypatch.setattr(previews, "CSV_PART", 200)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("acp_discharge_15min.csv", "station,date,discharge\n" + "".join(f"GAT,2020-01-01 00:{i % 60:02d},{i}\n" for i in range(100)))
    name, head, full = previews._zipped_csv(buf.getvalue())
    assert name == "acp_discharge_15min.csv" and full > 200 and len(head) <= 200 and head.endswith(b"\n")


def test_explorer_offers_yes_no_columns_to_the_model(monkeypatch):
    """Overdose records mark fentanyl with a Y column; the model only saw free-text injury descriptions (131 matches)."""
    from yfinder import explore
    props = [{"descriptionofinjury": d, "fentanyl": f, "residencecity": c}
             for d, f, c in [("Used fentanyl", "Y", "Hartford"), ("Drug use", "Y", "Bristol"), ("Ingestion", "", "Hartford"),
                             ("Injection", "Y", "Norwich"), ("Drug use", "", "Bristol")] * 4]
    g = explore.Geo.__new__(explore.Geo)
    g.props, g.n = props, len(props)
    seen = {}

    def fake_model(s, prompt, schema, name, max_tokens):
        seen["prompt"] = prompt
        return {"column": "fentanyl", "values": ["Y"], "why": "the fentanyl column marks fentanyl-involved deaths"}
    monkeypatch.setattr(explore, "_model_on", lambda: True)
    monkeypatch.setattr(explore, "_model_json", fake_model)
    s = type("S", (), {"graph": {"outcome": "d", "question": "fentanyl deaths", "nodes": [{"id": "d", "label": "Fentanyl deaths", "observable": ""}]}})()
    col, vals, why = explore._question_subset(g, s)
    assert col == "fentanyl" and vals == ["Y"] and '"fentanyl": ["Y"]' in seen["prompt"]


def test_heavy_point_layers_keep_their_name_columns():
    from yfinder import vector
    props = {f"metric_{i}": i for i in range(30)}
    props.update(hospital_name="Yale New Haven Hospital", city="New Haven")
    f = vector._feature({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [-72.9, 41.3]}}, 5, 16)
    assert f["properties"]["hospital_name"] == "Yale New Haven Hospital" and f["properties"]["city"] == "New Haven"
    assert len(f["properties"]) == 16


def test_heavy_point_layers_keep_columns_that_name_the_topic():
    from yfinder import vector
    props = {f"col_{i:02d}": "x" for i in range(30)}
    props.update(fentanyl="Y", heroin="")
    f = vector._feature({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [0, 0]}}, 5, 16, {"fentanyl"})
    assert f["properties"].get("fentanyl") == "Y"
    f = vector._feature({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [0, 0]}}, 5, 16)
    assert "fentanyl" not in f["properties"]  # without topic words the first columns win, as before
