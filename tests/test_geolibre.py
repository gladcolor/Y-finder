"""Layers collected in GeoLibre: reading projects, drawing them, the services, the catalog cards and the build."""
import json

import pytest

from yfinder import agent, catalog, geolibre, session, vector, watch
from yfinder.app import create_app

NEW_HAVEN = [-72.998, 41.246, -72.86, 41.351]


def _poly(w, s, e, n):
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def _project(n_far=3500):
    """A small GeoLibre project: a styled choropleth, a hidden layer, a big point layer, tiles, a basemap, vector tiles."""
    near = {"type": "Feature", "properties": {"county": "New Haven", "rate": 11.2, "nested": {"x": 1}},
            "geometry": _poly(-73.1, 41.2, -72.7, 41.6)}
    far = {"type": "Feature", "properties": {"county": "Far", "rate": 8.0}, "geometry": _poly(-100.123456789, 40, -99, 41)}
    pts = [{"type": "Feature", "properties": {"id": i}, "geometry": {"type": "Point", "coordinates": [-120 + i * 1e-4, 35.0]}}
           for i in range(n_far)] + [{"type": "Feature", "properties": {"id": "here"},
                                      "geometry": {"type": "Point", "coordinates": [-72.92, 41.30]}}]
    graduated = {"fillColor": "#3b82f6", "strokeColor": "#ffffff", "fillOpacity": 0.85, "vectorStyleMode": "graduated",
                 "vectorStyleProperty": "rate",
                 "vectorStyleStops": [{"value": 10, "color": "#fc9779"}, {"value": 7.8, "color": "#fff5f0"},
                                      {"value": 10, "color": "#000000"}, {"value": "x", "color": "#111111"},
                                      {"value": 12, "color": "javascript:alert(1)"}]}
    return {
        "name": "Test map", "metadata": {"description": "County rates, 2019 to 2023."}, "mapView": {"center": [-96, 38], "zoom": 3.6},
        "layers": [
            {"id": "a", "name": "Rate (%)", "type": "geojson", "visible": True, "style": graduated,
             "source": {"type": "geojson"}, "geojson": {"type": "FeatureCollection", "features": [near, far]}},
            {"id": "b", "name": "Other rate", "type": "geojson", "visible": False, "style": {"fillColor": "#00ff00"},
             "source": {"type": "geojson"}, "geojson": {"type": "FeatureCollection", "features": [far]}},
            {"id": "c", "name": "Many points", "type": "geojson", "visible": True,
             "source": {"type": "geojson"}, "geojson": {"type": "FeatureCollection", "features": pts}},
            {"id": "d", "name": "NO2 tiles", "type": "xyz", "visible": True,
             "source": {"type": "raster", "tiles": ["https://gibs.example/{z}/{y}/{x}.png"], "attribution": "NASA"}},
            {"id": "e", "name": "Plain http tiles", "type": "xyz", "source": {"tiles": ["http://insecure/{z}/{x}/{y}.png"]}},
            {"id": "basemap-esri-world-imagery", "name": "World Imagery", "type": "raster",
             "source": {"tiles": ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"]}},
            {"id": "f", "name": "Buildings", "type": "vector-tiles", "source": {"type": "vector", "tiles": ["https://x/{z}/{x}/{y}.pbf"]}},
        ]}


# ---------------------------------------------------------------------------- reading and drawing projects
def test_summarize_reads_extent_layers_and_fields():
    sm = geolibre.summarize(_project())
    names = [l["name"] for l in sm["layers"]]
    assert "World Imagery" not in names                                      # basemaps are left out
    assert sm["mappable"] == 4 and {"name": "Buildings", "type": "vector-tiles", "map": False} in sm["layers"]
    assert {"name": "Plain http tiles", "type": "xyz", "map": False} in sm["layers"]  # http tiles are not drawn
    assert sm["bbox"][0] == pytest.approx(-120) and sm["bbox"][3] == pytest.approx(41.6)
    assert "rate" in sm["fields"] and sm["bbox_from"] == "layers"


def test_imagery_only_maps_take_their_extent_from_the_saved_view():
    p = {"mapView": {"center": [-118.55, 34.07], "zoom": 10}, "layers": [
        {"name": "Fire scars", "type": "xyz", "source": {"tiles": ["https://t/{z}/{x}/{y}.png"]}}]}
    sm = geolibre.summarize(p)
    assert sm["bbox_from"] == "map view" and sm["bbox"][0] < -118.55 < sm["bbox"][2] and sm["bbox"][2] - sm["bbox"][0] < 2
    world = geolibre.summarize({"mapView": {"center": [0, 20], "zoom": 1.5}, "layers": p["layers"]})
    assert world["bbox"][2] - world["bbox"][0] >= 359


def test_paint_hint_keeps_geolibre_colors_and_drops_bad_values():
    h = geolibre.paint_hint(_project()["layers"][0]["style"])
    # sorted, de-duplicated lower bounds; non-numeric values and non-hex colors dropped
    assert h["mode"] == "graduated" and h["property"] == "rate" and h["stops"] == [[7.8, "#fff5f0"], [10.0, "#fc9779"]]
    assert h["fill"] == "#3b82f6" and h["opacity"] == 0.85
    cat = geolibre.paint_hint({"vectorStyleMode": "categorized", "vectorStyleProperty": "fuel",
                               "vectorStyleStops": [{"value": "Coal", "color": "#333333"}, {"value": {"x": 1}, "color": "#ffffff"}]})
    assert cat["stops"] == [["Coal", "#333333"]]
    assert geolibre.paint_hint({"vectorStyleMode": "graduated", "vectorStyleProperty": "a" * 200,
                                "vectorStyleStops": [{"value": 1, "color": "#000"}, {"value": 2, "color": "#fff"}]}) is None
    assert geolibre.paint_hint("not a style") is None


def test_map_layers_cuts_big_layers_to_the_study_area():
    parts = {p["title"]: p for p in geolibre.map_layers(_project(), NEW_HAVEN)}
    assert parts["Rate (%)"]["kind"] == "geojson" and len(parts["Rate (%)"]["fc"]["features"]) == 2  # small: drawn whole
    assert parts["Other rate"]["hidden"] is True
    pts = parts["Many points"]
    assert [f["properties"]["id"] for f in pts["fc"]["features"]] == ["here"] and "touch the study area" in pts["note"]
    assert parts["NO2 tiles"]["kind"] == "raster" and parts["NO2 tiles"]["tiles"] == ["https://gibs.example/{z}/{y}/{x}.png"]
    assert parts["Buildings"]["kind"] == "skipped" and "World Imagery" not in parts
    far = parts["Rate (%)"]["fc"]["features"][1]
    assert far["geometry"]["coordinates"][0][0][0] == -100.12346          # rounded to about 1 m
    assert "nested" not in parts["Rate (%)"]["fc"]["features"][0]["properties"]  # only plain values pass


def test_map_layers_samples_evenly_without_a_study_area(monkeypatch):
    monkeypatch.setattr(vector, "MAX_FEATURES", 1000)
    pts = next(p for p in geolibre.map_layers(_project()) if p["title"] == "Many points")
    xs = [f["geometry"]["coordinates"][0] for f in pts["fc"]["features"]]
    assert len(xs) <= 1000 and max(xs) - min(xs) > 0.3 and "a sample of" in pts["note"]  # spread out, not the first 1,000


def test_time_series_keep_one_year_but_events_keep_all():
    series = [{"type": "Feature", "properties": {"country": c, "year": y}, "geometry": _poly(i, 0, i + 1, 1)}
              for y in range(2000, 2010) for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXY")]
    events = [{"type": "Feature", "properties": {"year": 1900 + i % 100}, "geometry": {"type": "Point", "coordinates": [i * 0.01, 5]}}
              for i in range(400)]
    proj = {"layers": [{"name": "S", "type": "geojson", "geojson": {"type": "FeatureCollection", "features": series}},
                       {"name": "E", "type": "geojson", "geojson": {"type": "FeatureCollection", "features": events}}]}
    s, e = geolibre.map_layers(proj, study_year=2004)
    assert {f["properties"]["year"] for f in s["fc"]["features"]} == {2004} and len(s["fc"]["features"]) == 25
    assert "2004 only (2000 to 2009" in s["note"]
    assert len(e["fc"]["features"]) == 400 and not e["note"]
    latest = geolibre.map_layers(proj)[0]
    assert {f["properties"]["year"] for f in latest["fc"]["features"]} == {2009}


def test_fetch_project_only_by_slug_and_cached(monkeypatch, tmp_path):
    calls = []

    class R:
        status_code = 200

        def iter_content(self, n):
            yield json.dumps(_project(10)).encode()

    monkeypatch.setattr(geolibre.requests, "get", lambda url, **kw: calls.append(url) or R())
    with pytest.raises(ValueError):
        geolibre.fetch_project("../../etc/passwd", cache_dir=tmp_path)
    with pytest.raises(ValueError):
        geolibre.fetch_project("https://evil.example/x", cache_dir=tmp_path)
    assert geolibre.fetch_project("test-map", cache_dir=tmp_path)["name"] == "Test map"
    geolibre.fetch_project("test-map", cache_dir=tmp_path)
    assert calls == ["https://assets.geolibre.app/projects/test-map.geolibre.json"]  # one download, then the cache


def test_fetch_project_refuses_huge_files(monkeypatch, tmp_path):
    class R:
        status_code = 200

        def iter_content(self, n):
            for _ in range(10):
                yield b"x" * 1000

    monkeypatch.setattr(geolibre, "MAX_PROJECT_BYTES", 5000)
    monkeypatch.setattr(geolibre.requests, "get", lambda url, **kw: R())
    with pytest.raises(ValueError, match="too large"):
        geolibre.fetch_project("huge-map", cache_dir=tmp_path)
    assert not list(tmp_path.glob("*"))


def test_service_tiles():
    wms11 = geolibre.service_layer({"kind": "wms", "endpoint": "https://w.example/wms", "layers": "a:b", "version": "1.1.1"})["tiles"][0]
    wms13 = geolibre.service_layer({"kind": "wms", "endpoint": "https://w.example/wms", "layers": "x", "version": "1.3.0"})["tiles"][0]
    assert "SRS=EPSG%3A3857" in wms11 and "LAYERS=a%3Ab" in wms11 and wms11.endswith("&BBOX={bbox-epsg-3857}")
    assert "CRS=EPSG%3A3857" in wms13 and "SRS=" not in wms13
    assert geolibre.service_layer({"kind": "wmts", "tiles": "https://t/{z}/{y}/{x}.jpg"})["tiles"] == ["https://t/{z}/{y}/{x}.jpg"]
    with pytest.raises(ValueError):
        geolibre.service_layer({"kind": "xyz", "tiles": "http://plain/{z}/{x}/{y}"})


# ---------------------------------------------------------------------------- catalog cards
def test_catalog_has_geolibre_layers():
    cat = catalog.load()
    assert cat.meta["counts"]["geolibre_gallery"] >= 100 and cat.meta["counts"]["geolibre_services"] == 6
    asthma = cat.get("gl:us-adult-asthma-copd-by-county")
    assert asthma["region"] == "US" and asthma["unit"] == "County" and asthma["geolibre"]["mappable"] == 2
    card = catalog.card(asthma)
    assert card["map"] is True and card["viewer"].startswith("https://web.geolibre.app/?url=https%3A%2F%2Fassets.geolibre.app")
    assert catalog.card(cat.get("gls:nlcd-2021-wms"))["map"] is True
    assert not catalog.card(cat.get("gl:manhattan-buildings-through-time"))["map"]  # vector tiles only: GeoLibre only
    nhd = cat.get("geec:nhd")
    assert nhd["gee_parts"] > 100 and nhd["source"] == "GEE community catalog" and "map" not in catalog.card(nhd)
    assert not any("Insiders" in (it.get("description") or "") for it in cat.items if it["source"] == "GEE community catalog")


def test_search_finds_geolibre_maps():
    cat = catalog.load()
    assert "gl:us-adult-asthma-copd-by-county" in [r["id"] for r in cat.search("asthma county", limit=5)]
    assert "gls:nlcd-2021-wms" in [r["id"] for r in cat.search("nlcd land cover web map service", limit=5)]
    only = cat.search("land cover", source="GeoLibre", limit=10)
    assert only and all(r["source"].startswith("GeoLibre") for r in only)


def test_build_groups_community_assets(monkeypatch):
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location("build_catalog", pathlib.Path(__file__).parents[1] / "scripts" / "build_catalog.py")
    bc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc)
    rows = [{"title": f"National Hydrography Dataset(NHD) {st} : NHDArea", "id": f"projects/x/NHD_{st}", "type": "table",
             "provider": "USGS", "tags": "Hydrography, United States", "license": "custom",
             "docs": "https://gee-community-catalog.org/projects/nhd/", "thematic_group": "Hydrology"} for st in ("CT", "MA", "RI")]
    rows += [{"title": "Secret layer", "id": "projects/x/secret", "type": "image", "provider": "", "tags": "",
              "license": "", "docs": "https://gee-community-catalog.org/projects/secret/", "thematic_group": "Insiders only datasets"},
             {"title": "Global canopy 10m 2020", "id": "projects/x/c1", "type": "image", "provider": "Lab", "tags": "Canopy",
              "license": "CC BY 4.0", "docs": "https://other.example/a/canopy/", "thematic_group": "Forests"},
             {"title": "Canopy of Peru 2019", "id": "projects/x/c2", "type": "image", "provider": "Lab2", "tags": "Canopy",
              "license": "CC BY 4.0", "docs": "https://third.example/canopy", "thematic_group": "Forests"}]
    monkeypatch.setattr(bc, "_get_json", lambda url, tries=3: rows)
    out = {c["id"]: c for c in bc.harvest_gee_community()}
    assert set(out) == {"geec:nhd", "geec:canopy", "geec:canopy-2"}          # insiders left out; ids kept unique
    nhd = out["geec:nhd"]
    assert nhd["title"] == "National Hydrography Dataset(NHD)" and nhd["gee_parts"] == 3 and nhd["region"] == "US"
    assert out["geec:canopy"]["res_m"] == 10 and out["geec:canopy"]["region"] == "Global" and out["geec:canopy"]["t0"] == 2020


# ---------------------------------------------------------------------------- the map tool and its endpoint
@pytest.fixture()
def client(tmp_path, monkeypatch):
    watch.WATCH_DIR = tmp_path / "watches"
    session.LAYER_DIR = tmp_path / "layers"
    monkeypatch.setattr(geolibre, "fetch_project", lambda slug, **kw: _project())
    return create_app().test_client()


def test_map_layer_endpoint_draws_a_gallery_map(client):
    s = session.get(None)
    s.scope = {"bbox": NEW_HAVEN, "time_end": "2023"}
    r = client.post("/api/map_layer", json={"sid": s.sid, "ref": "gl:us-adult-asthma-copd-by-county"})
    assert r.status_code == 200, r.json
    layers = [e["layer"] for e in r.json["events"] if e["type"] == "layer"]
    rate = next(l for l in layers if l["title"] == "Rate (%)")
    assert rate["kind"] == "geojson" and rate["style"] == "geolibre" and rate["paint"]["mode"] == "graduated"
    assert rate["source"].startswith("GeoLibre gallery") and "CDC PLACES" in rate["source"]
    assert next(l for l in layers if l["title"] == "Other rate")["hidden"] is True
    assert any(l["kind"] == "raster" for l in layers) and r.json["events"][-1] == {"type": "zoom", "bbox": NEW_HAVEN}
    res = r.json["result"]
    assert res["styled_original"].startswith("https://web.geolibre.app/") and "snapshot" in res["note"]
    assert any(l["layer"] == "Buildings" and not l["shown"] for l in res["layers"])
    assert len(s.layers) == len(layers)                                     # remembered for the GeoLibre hand-off


def test_map_layer_draws_services_and_rejects_other_refs(client, monkeypatch):
    r = client.post("/api/map_layer", json={"ref": "gls:nlcd-2021-wms"})
    lay = r.json["events"][0]["layer"]
    assert r.status_code == 200 and lay["kind"] == "raster" and "NLCD_2021_Land_Cover_L48" in lay["tiles"][0] and r.json["sid"]
    got = {}
    monkeypatch.setattr(agent.sources, "arcgis_query", lambda url, bbox: got.update(url=url) or
                        {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"NAME": "New Haven"},
                                                                    "geometry": {"type": "Point", "coordinates": [-72.92, 41.3]}}]})
    r = client.post("/api/map_layer", json={"ref": "gls:usa-major-cities"})
    assert r.status_code == 200 and got["url"].endswith("USA_Major_Cities/FeatureServer/0")
    for bad in ("pc:naip", "gl:not-in-catalog", "https://evil.example/x.geolibre.json", "gl:manhattan-buildings-through-time"):
        assert client.post("/api/map_layer", json={"ref": bad}).status_code == 400


def test_picks_know_they_can_be_drawn():
    s = session.get(None)
    agent.h_search_catalog(s, {"query": "asthma county", "kind": "any", "access": "any", "limit": 5})
    agent.h_recommend_data(s, {"picks": [
        {"ref": "gl:us-adult-asthma-copd-by-county", "title": "", "factors": [], "role": "core", "why": "", "limitations": "",
         "spatial_resolution_m": None, "time_start": None, "time_end": None, "coverage": None},
        {"ref": "cur:places", "title": "", "factors": [], "role": "core", "why": "", "limitations": "",
         "spatial_resolution_m": None, "time_start": None, "time_end": None, "coverage": None}], "methods": [], "next_steps": []})
    assert {p["ref"]: p["map"] for p in s.picks} == {"gl:us-adult-asthma-copd-by-county": True, "cur:places": False}


def test_catalog_browse_by_source(client):
    r = client.get("/api/catalog/search?q=&source=GeoLibre&limit=150")
    res = r.json["results"]
    assert len(res) == 111 and all(c["source"].startswith("GeoLibre") for c in res)
    assert [c["source"] for c in res[:6]] == ["GeoLibre services"] * 6  # the few services are listed first
    assert client.get("/api/catalog/search?q=&source=evil").json["results"] == []
