"""Non-spatial data: finding tables (Harvard Dataverse, Socrata portals, World Bank), marking picks as tables,
and previewing them: joined maps, table previews and trend lines. No network: outside calls are faked."""
import json

import pytest

from yfinder import agent, catalog, downloads, fit, previews, session, sources, tables, watch

CT = [-73.73, 40.95, -71.78, 42.06]


def _sq(x, y, d=1.0):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]}


@pytest.fixture()
def s(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "LAYER_DIR", tmp_path / "layers")
    monkeypatch.setattr(downloads, "DOWNLOAD_DIR", tmp_path / "downloads")
    watch.WATCH_DIR = tmp_path / "watches"
    ses = session.get(None)
    ses.scope = {"bbox": CT, "time_start": "2016", "time_end": "2024", "unit_of_analysis": "town"}
    ses.graph = {"question": "Did zoning reform raise housing permits in Connecticut towns?", "nodes": [{"id": "p", "label": "Housing permits"}], "edges": []}
    yield ses
    session.reset(ses.sid)


# ---------------------------------------------------------------------------- finding tables
def test_sources_parse_dataverse_and_socrata(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        if "dataverse" in url:
            return {"data": {"items": [{"name": "Smoke and ER visits", "url": "https://doi.org/10.7910/DVN/X", "global_id": "doi:10.7910/DVN/X",
                                        "fileCount": 3, "published_at": "2024-01-02", "authors": ["A. Researcher"]}]}}
        return {"results": [{"resource": {"name": "Permits by town", "id": "stm9-38x4", "columns_name": ["Towns", "2024"], "data_updated_at": "2026-01-02T00"},
                             "metadata": {"domain": "data.ct.gov"}, "permalink": "https://data.ct.gov/d/stm9-38x4"},
                            {"resource": {"name": "bad", "id": "../etc"}, "metadata": {"domain": "x.example"}}]}
    monkeypatch.setattr(sources, "_get", fake_get)
    monkeypatch.setattr(sources, "_cached", lambda key, ttl, fn: fn())
    dv = sources.dataverse("smoke")
    assert dv[0]["doi"] == "doi:10.7910/DVN/X" and dv[0]["data_type"] == "table" and dv[0]["source"] == "Harvard Dataverse"
    soc = sources.socrata("permits", domains="data.ct.gov")
    assert len(soc) == 1 and soc[0]["soda"] == "https://data.ct.gov/resource/stm9-38x4.csv" and soc[0]["columns"] == ["Towns", "2024"]


def test_search_tables_puts_the_state_portal_first_and_survives_failures(s, monkeypatch):
    calls = []

    def soc(q, limit=8, domains=None):
        calls.append(domains)
        return [{"ref": f"https://{domains or 'data.cdc.gov'}/d/aaaa-bbbb", "title": f"T {domains}", "url": "u", "source": "portal",
                 "columns": ["Town", "Rate"], "data_type": "table"}]
    monkeypatch.setattr(sources, "socrata", soc)
    monkeypatch.setattr(sources, "dataverse", lambda q, limit=8: (_ for _ in ()).throw(RuntimeError("down")))
    res, ev, label = agent.h_search_tables(s, {"query": "housing permits"})
    assert calls[0] == "data.ct.gov" and res["results"][0]["columns"] == ["Town", "Rate"] and res["results"][0]["data_type"] == "table"
    assert "https://data.ct.gov/d/aaaa-bbbb" in s.seen and label.startswith("2 tables")


def test_table_picks_are_marked_and_judged_without_spatial_detail(s):
    s.seen["https://data.cdc.gov/d/aaaa-bbbb"] = {"title": "Asthma ER visits", "url": "https://data.cdc.gov/d/aaaa-bbbb", "data_type": "table"}
    agent.h_recommend_data(s, {"picks": [
        {"ref": "https://data.cdc.gov/d/aaaa-bbbb", "title": "", "factors": [], "role": "core", "why": "", "limitations": "",
         "spatial_resolution_m": None, "time_start": None, "time_end": None, "coverage": None, "data_type": "spatial"},
        {"ref": "wb:NY.GDP.PCAP.KD.ZG", "title": "", "factors": [], "role": "supporting", "why": "", "limitations": "",
         "spatial_resolution_m": None, "time_start": None, "time_end": None, "coverage": None, "data_type": "table"}], "methods": [], "next_steps": []})
    by = {p["ref"]: p for p in s.picks}
    assert by["https://data.cdc.gov/d/aaaa-bbbb"]["data_type"] == "table"           # the source says table, whatever the model said
    assert "A table without spatial detail" in by["https://data.cdc.gov/d/aaaa-bbbb"]["fit"]["resolution"]["reason"]
    assert by["wb:NY.GDP.PCAP.KD.ZG"]["data_type"] == "table" and "country" in by["wb:NY.GDP.PCAP.KD.ZG"]["fit"]["resolution"]["reason"].lower()
    assert agent.TOOLS[[t["name"] for t in agent.TOOLS].index("recommend_data")]["parameters"]["properties"]["picks"]["items"]["properties"]["data_type"]["enum"] == ["spatial", "table"]


def test_world_bank_cards_are_in_the_catalog():
    cat = catalog.load()
    assert cat.meta["counts"]["world_bank"] > 1000
    assert any(r["id"].startswith("wb:NY.GDP.PCAP") for r in cat.search("GDP per capita", limit=3))
    assert catalog.card(cat.get(next(i["id"] for i in cat.items if i["source"] == "World Bank WDI")))["data_type"] == "table"


# ---------------------------------------------------------------------------- previews of tables
def test_world_bank_map_and_trend_for_the_study_country(s, monkeypatch):
    shapes = {"PAK": {"type": "Feature", "geometry": _sq(60, 24, 18), "properties": {"name": "Pakistan"}},
              "USA": {"type": "Feature", "geometry": {"type": "MultiPolygon", "coordinates": [_sq(-125, 25, 60)["coordinates"], _sq(172, 51, 7)["coordinates"]]},
                      "properties": {"name": "United States"}}}       # its box spans the globe, like the real one
    monkeypatch.setattr(tables, "countries", lambda: shapes)

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"page": 1}, [{"countryiso3code": "PAK", "date": str(y), "value": 50 + y - 2015} for y in range(2015, 2025)]
                    + [{"countryiso3code": "USA", "date": "2024", "value": 7.0}, {"countryiso3code": "WLD", "date": "2024", "value": 30}]]
    monkeypatch.setattr(previews.requests, "get", lambda url, **kw: R())
    s.scope = {"bbox": [61, 25, 77, 36], "time_start": "2015", "time_end": "2024"}
    m = catalog.load().get("wb:EN.ATM.PM25.MC.M3")
    r = previews.build(s, {"ref": m["id"], "role": "core", "title": m["title"], "meta": m})
    assert r["layers"][0]["n"] == 2 and r["series"]["label"] == "Pakistan" and r["series"]["points"][-1] == [2024, 59]


def test_town_table_is_joined_with_the_latest_year_in_the_window(s, monkeypatch):
    csv_text = "towns,county,_2015,_2016,_2024,_2025\nConnecticut,,100,110,150,160\nNew Haven,New Haven,10,12,20,25\nHamden,New Haven,5,6,9,9\nOrange,New Haven,1,1,2,3\n"
    monkeypatch.setattr(downloads, "fetch", lambda url, dest, max_bytes=None: dest.write_bytes(csv_text.encode()) or {"name": "x"})
    monkeypatch.setattr(previews, "_towns", lambda ses: {t.lower(): {"type": "Feature", "geometry": _sq(-73 + i * .05, 41.3, .05), "properties": {"name": t}}
                                                          for i, t in enumerate(["New Haven", "Hamden", "Orange"])})
    meta = {"title": "Annual Housing Permit Data By Town", "soda": "https://data.ct.gov/resource/stm9-38x4.csv", "data_type": "table"}
    r = previews.build(s, {"ref": "https://data.ct.gov/d/stm9-38x4", "role": "core", "title": meta["title"], "meta": meta})
    lay = r["layers"][0]
    assert r["kind"] == "slice" and lay["n"] == 3 and lay["paint"]["label"] == "2024 (per town)"   # 2024: the window ends there
    props = {f["properties"]["town"]: f["properties"] for f in json.load(open(session.layer_path(lay["id"]), encoding="utf-8"))["features"]}
    assert props["New Haven"]["_2024"] == 20 and "Connecticut" not in props                       # the state total is not a town
    assert r["series"]["points"] == [[2015, 16], [2016, 19], [2024, 31], [2025, 37]] and "sum" in r["series"]["label"]


def test_plain_table_gets_a_table_preview_and_trend(s, monkeypatch):
    csv_text = "Year,Group,ER Visits Rate\n2016,A,400\n2017,A,420\n2018,A,410\n2019,A,450\n"
    monkeypatch.setattr(downloads, "fetch", lambda url, dest, max_bytes=None: dest.write_bytes(csv_text.encode()) or {"name": "x"})
    meta = {"title": "Childhood Asthma ER Visits", "soda": "https://data.oaklandca.gov/resource/8vxa-wdqe.csv", "data_type": "table"}
    r = previews.build(s, {"ref": "https://data.oaklandca.gov/d/8vxa-wdqe", "role": "core", "title": meta["title"], "meta": meta})
    assert r["kind"] == "table" and r["layers"] == [] and r["table"]["columns"] == ["Year", "Group", "ER Visits Rate"] and r["table"]["n_rows"] == 4
    assert r["series"]["points"] == [[2016, 400], [2017, 420], [2018, 410], [2019, 450]]
    assert downloads.sample_path(s.sid, r["csv"]) and not list(downloads.session_dir(s.sid).glob("pv-*"))


def test_dataverse_picks_a_public_table_file(s, monkeypatch):
    class R:
        def json(self):
            return {"data": {"latestVersion": {"files": [
                {"restricted": True, "dataFile": {"id": 1, "originalFileName": "secret.csv", "originalFileSize": 10}},
                {"dataFile": {"id": 2, "filename": "readme.pdf", "filesize": 10}},
                {"dataFile": {"id": 3, "originalFileName": "visits.csv", "originalFileSize": 2000}}]}}}
    monkeypatch.setattr(previews.requests, "get", lambda url, **kw: R())
    got = []
    monkeypatch.setattr(downloads, "fetch", lambda url, dest, max_bytes=None: got.append(url) or dest.write_bytes(b"Year,Visits\n2019,1\n2020,2\n2021,3\n") or {})
    meta = {"title": "Smoke and ER visits", "doi": "doi:10.7910/DVN/X", "dataverse_host": "dataverse.harvard.edu", "data_type": "table"}
    r = previews.build(s, {"ref": "https://doi.org/10.7910/DVN/X", "role": "core", "title": meta["title"], "meta": meta})
    assert got == ["https://dataverse.harvard.edu/api/access/datafile/3?format=original"] and r["kind"] == "table"


def test_dataverse_host_comes_from_the_doi_and_maps_come_before_tables(s, monkeypatch):
    """Yale Dataverse lists harvested Harvard datasets; their files must be fetched from Harvard. A zipped shapefile
    (its .shp listed late in the zip) is drawn before the CSV."""
    import io
    import zipfile
    import shapefile  # pyshp
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POINT)
    w.field("caja", "C")
    for i in range(4):
        w.point(-72.95 + i * 0.01, 41.3)
        w.record(f"c{i}")
    w.close()
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:  # the large .dbf first, the .shp last, as in the real dataset
        z.writestr("cajas.dbf", dbf.getvalue() + b" " * 250000)
        z.writestr("cajas.shx", shx.getvalue())
        z.writestr("cajas.shp", shp.getvalue())
    asked = []

    class R:
        def __init__(self, url):
            self.url = url

        def json(self):
            if "dataverse.yale.edu" in self.url:
                return {"status": "ERROR", "message": "User :guest is not permitted to perform requested action."}
            return {"status": "OK", "data": {"latestVersion": {"files": [
                {"dataFile": {"id": 7, "originalFileName": "cajas.csv", "originalFileSize": 900}},
                {"dataFile": {"id": 8, "originalFileName": "cajas.zip", "originalFileSize": 5000, "contentType": "application/zipped-shapefile"}}]}}}
    monkeypatch.setattr(previews.requests, "get", lambda url, **kw: asked.append(url) or R(url))
    got = []
    monkeypatch.setattr(downloads, "fetch", lambda url, dest, max_bytes=None: got.append(url) or dest.write_bytes(zbuf.getvalue()) or {})
    meta = {"title": "Silver registry", "doi": "doi:10.60600/YU/TEST", "dataverse_host": "dataverse.yale.edu"}
    r = previews.build(s, {"ref": "https://doi.org/10.60600/YU/TEST", "role": "core", "title": "Silver", "meta": meta})
    assert asked[0].startswith("https://dataverse.yale.edu") and asked[1].startswith("https://dataverse.harvard.edu")   # retried at Harvard
    assert got == ["https://dataverse.harvard.edu/api/access/datafile/8?format=original"]                            # the map file
    assert r["status"] == "ready" and r["layers"][0]["n"] == 4
    assert previews._dv_host("doi:10.7910/DVN/NNFFCH", "dataverse.yale.edu") == "dataverse.harvard.edu"


def test_a_dataverse_collection_is_searched_with_the_question(s, monkeypatch):
    calls = []

    class R:
        def __init__(self, url, params):
            self.url, self.params = url, params or {}

        def json(self):
            if self.url.endswith("/api/search"):
                return {"data": {"items": [{"name": "Data: Housing permits by town", "global_id": "doi:10.7910/DVN/AAA"}]}}
            return {"status": "OK", "data": {"latestVersion": {"files": [{"dataFile": {"id": 3, "originalFileName": "permits.csv", "originalFileSize": 99}}]}}}
    monkeypatch.setattr(previews.requests, "get", lambda url, params=None, **kw: calls.append((url, params)) or R(url, params))
    monkeypatch.setattr(downloads, "fetch", lambda url, dest, max_bytes=None: dest.write_bytes(b"Year,Permits\n2019,1\n2020,2\n2021,3\n") or {})
    meta = {"title": "A collection", "url": "https://dataverse.yale.edu/dataverse/some-collection"}
    r = previews.build(s, {"ref": "cur:x", "role": "core", "title": "A collection", "meta": meta})
    search = calls[0]
    assert search[0] == "https://dataverse.yale.edu/api/search" and search[1]["subtree"] == "some-collection"
    assert "zoning" in search[1]["q"] or "housing" in search[1]["q"] or "permits" in search[1]["q"]
    assert r["status"] == "ready" and r["note"].startswith("Housing permits by town:")
