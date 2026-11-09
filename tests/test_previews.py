"""Map previews of recommended datasets: which rule applies, what is drawn, what is kept and when it is deleted.
No network: every outside call is faked."""
import json

import pytest

from yfinder import catalog, downloads, previews, session, sources, watch
from yfinder.app import create_app

NEW_HAVEN = [-72.998, 41.246, -72.86, 41.351]


def _poly(w, s, e, n, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}}


@pytest.fixture()
def s(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "LAYER_DIR", tmp_path / "layers")
    monkeypatch.setattr(downloads, "DOWNLOAD_DIR", tmp_path / "downloads")
    watch.WATCH_DIR = tmp_path / "watches"
    ses = session.get(None)
    ses.scope = {"bbox": NEW_HAVEN, "time_start": "2015", "time_end": "2024", "unit_of_analysis": "census tract"}
    ses.graph = {"question": "Which tracts have the most asthma?", "nodes": [{"id": "a", "label": "Asthma burden"}], "edges": []}
    yield ses
    session.reset(ses.sid)


def _pick(ref, role="core"):
    meta = catalog.load().get(ref) or {"title": ref, "url": ref}
    return {"ref": ref, "role": role, "title": meta.get("title"), "meta": meta}


def test_services_are_drawn_directly(s):
    r = previews.build(s, _pick("cur:nfhl"))
    lay = r["layers"][0]
    assert r["status"] == "ready" and r["kind"] == "service" and lay["kind"] == "raster"
    assert lay["tiles"][0].startswith("https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/export?bbox={bbox-epsg-3857}")
    r = previews.build(s, _pick("gls:nlcd-2021-wms", role="supporting"))
    assert "NLCD_2021_Land_Cover_L48" in r["layers"][0]["tiles"][0] and r["layers"][0]["hidden"] is True  # supporting: hidden
    assert all(l in s.layers for l in r["layers"])                                                 # part of the GeoLibre hand-off


def test_unviewable_sources_say_why(s):
    assert "Earth Engine account" in previews.build(s, _pick("gee:COPERNICUS/S5P/OFFL/L3_NO2"))["note"]
    assert "Licensed" in previews.build(s, _pick("cur:planet"))["note"]
    assert "registration" in previews.build(s, _pick("cur:ycom"))["note"]
    assert previews.build(s, {"ref": "https://example.org/about", "role": "core", "meta": {"url": "https://example.org/about"}})["status"] == "none"


def test_imagery_uses_the_best_scene_and_mosaics_small_tiles(s, monkeypatch):
    calls = []

    def search(coll, bbox, when, cloud, limit):
        calls.append((when, cloud))
        if cloud is not None:
            return []  # NAIP has no cloud cover: the filtered search finds nothing
        return [{"id": "a", "datetime": "2023-07-06T00:00:00Z", "bbox": [-73.0, 41.24, -72.93, 41.30], "tilejson": "https://planetarycomputer.microsoft.com/api/data/v1/a"},
                {"id": "b", "datetime": "2023-07-06T00:00:00Z", "bbox": [-72.93, 41.24, -72.86, 41.36], "tilejson": "https://planetarycomputer.microsoft.com/api/data/v1/b"},
                {"id": "c", "datetime": "2021-05-01T00:00:00Z", "bbox": NEW_HAVEN, "tilejson": None}]
    monkeypatch.setattr(sources, "stac_search", search)
    monkeypatch.setattr(sources, "stac_tiles", lambda u: {"tiles": [u + "/{z}/{x}/{y}"], "bounds": None, "minzoom": 10, "maxzoom": 18})
    r = previews.build(s, _pick("pc:naip"))
    assert calls[:2] == [("2015-01-01/2024-12-31", 30), ("2015-01-01/2024-12-31", None)]
    assert [l["id"] for l in r["layers"]] == ["pv-stac-b", "pv-stac-a"] and "2 scenes" in r["note"]


def test_nclimgrid_slice_is_downloaded_joined_and_kept_for_the_session(s, monkeypatch):
    days = ",".join(f"{v:9.2f}" for v in [30.0, 33.0, 35.0] + [28.0] * 28)
    files = {"tmax": [f"cty,06009,CT: New Haven County,2024,07,TMAX,{days}", f"cty,06001,CT: Fairfield County,2024,07,TMAX,{days.replace('35.00', '29.00')}",
                      "cty,01001,AL: Autauga,2024,07,TMAX," + days],
             "prcp": ["cty,06009,CT: New Haven County,2024,07,PRCP," + ",".join(["   1.00"] * 31)]}
    monkeypatch.setattr(previews, "_nclimgrid_month", lambda var, y, m: files[var] if (y, m) == (2024, 7) else None)
    got = {}

    def counties(url, bbox, **kw):
        got["url"], got["bbox"] = url, bbox
        return {"features": [_poly(-73.2, 41.2, -72.7, 41.6, GEOID="09009"), _poly(-73.7, 41.0, -73.2, 41.5, GEOID="09001")]}
    monkeypatch.setattr(sources, "arcgis_query", counties)
    r = previews.build(s, _pick("cur:nclimgrid"))
    assert got["url"].endswith("tigerWMS_Census2020/MapServer/82") and got["bbox"][0] < NEW_HAVEN[0] - 0.3  # neighbours included
    lay = r["layers"][0]
    assert r["kind"] == "slice" and lay["n"] == 2 and lay["session_only"] and lay["paint"]["property"] == "mean_tmax_c"
    props = {f["properties"]["GEOID"]: f["properties"] for f in json.load(open(session.layer_path(lay["id"]), encoding="utf-8"))["features"]}
    assert props["09009"]["days_over_32c"] == 2 and props["09009"]["total_prcp_mm"] == 31.0 and props["09009"]["month"] == "2024-07"
    csv_path = downloads.sample_path(s.sid, r["csv"])
    assert csv_path and csv_path.read_text().splitlines()[1].startswith("09009,CT: New Haven County,2024-07-01,30.0,1.0")
    session.reset(s.sid)  # the session ends: the slice and its layer are deleted
    assert session.layer_path(lay["id"]) is None and not csv_path.exists()


def test_places_picks_the_measure_from_the_question(s, monkeypatch):
    seen = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"locationid": "09170141300", "measure": "Current asthma among adults", "data_value": "11.5",
                     "low_confidence_limit": "10.1", "high_confidence_limit": "13.0", "totalpopulation": "4000"}]
    monkeypatch.setattr(previews.requests, "get", lambda url, params=None, **kw: seen.update(url=url, params=params) or R())
    monkeypatch.setattr(sources, "census_boundaries", lambda level, bbox: {"features": [_poly(-72.95, 41.29, -72.93, 41.31, GEOID="09170141300"),
                                                                                         _poly(-72.93, 41.29, -72.91, 41.31, GEOID="09170141400")]})
    r = previews.build(s, _pick("cur:places"))
    assert "measureid='CASTHMA'" in seen["params"]["$where"] and "within_box(geolocation, 41.351, -72.998, 41.246, -72.86)" in seen["params"]["$where"]
    assert r["status"] == "ready" and r["layers"][0]["n"] == 1 and r["layers"][0]["title"] == "CDC PLACES: Current asthma among adults"


def test_acs_comes_from_the_keyless_living_atlas_service(s, monkeypatch):
    got = {}
    monkeypatch.setattr(sources, "arcgis_query", lambda url, bbox, **kw: got.update(url=url) or {"features": [
        _poly(-72.95, 41.29, -72.93, 41.31, GEOID="09170141300", NAME="Tract 1413", B19049_001E=52000, B19049_001M=8000),
        _poly(-72.93, 41.29, -72.91, 41.31, GEOID="09170141400", NAME="Tract 1414", B19049_001E=-666666666)]})
    r = previews.build(s, _pick("cur:acs"))
    assert got["url"].endswith("ACS_Median_Household_Income_View_Boundaries/FeatureServer/2")  # tracts for a city-sized area
    props = [f["properties"] for f in json.load(open(session.layer_path(r["layers"][0]["id"]), encoding="utf-8"))["features"]]
    assert [p["median_household_income"] for p in props] == [52000, None]  # Census sentinel values become empty


def test_arcgis_online_items_are_resolved_to_their_service(s, monkeypatch):
    class R:
        def json(self):
            return {"type": "Feature Service", "url": "https://services.example.com/arcgis/rest/services/Trees/FeatureServer"}
    monkeypatch.setattr(previews.requests, "get", lambda url, **kw: R())
    monkeypatch.setattr(sources, "safe_url", lambda u: True)
    got = {}
    monkeypatch.setattr(sources, "arcgis_query", lambda url, bbox, **kw: got.update(url=url) or {"features": [_poly(-72.95, 41.29, -72.93, 41.31)]})
    ref = "https://www.arcgis.com/home/item.html?id=31e6058fbf434a68bd23b5ff092afa14"
    r = previews.build(s, {"ref": ref, "role": "core", "meta": {"title": "Street trees", "url": ref}})
    assert got["url"] == "https://services.example.com/arcgis/rest/services/Trees/FeatureServer/0" and r["layers"][0]["n"] == 1


def test_start_runs_once_per_scope_and_endpoint_reports(s, monkeypatch):
    monkeypatch.setenv("YFINDER_PREVIEWS", "1")
    s.picks = [_pick("cur:nfhl"), _pick("cur:planet", role="supporting")]
    assert previews.start(s, sync=True) is True
    assert previews.start(s, sync=True) is False                      # same scope: nothing to redo
    c = create_app().test_client()
    j = c.get(f"/api/session/{s.sid}/previews").json
    assert j["running"] is False and j["items"]["cur:nfhl"]["status"] == "ready" and j["items"]["cur:planet"]["status"] == "none"
    s.scope = dict(s.scope, time_end="2025")                           # a new window: previews follow
    assert previews.start(s, sync=True) is True
    assert c.get("/api/session/nope/previews").status_code == 404
    assert c.get(f"/api/session/{s.sid}/sample/..%2F..%2Fsecret.csv").status_code == 404


def test_previews_are_off_when_disabled(s, monkeypatch):
    monkeypatch.setenv("YFINDER_PREVIEWS", "0")
    s.picks = [_pick("cur:nfhl")]
    assert previews.start(s, sync=True) is False and s.previews == {}
