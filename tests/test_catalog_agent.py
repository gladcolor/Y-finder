"""Catalog search quality and the agent's tool handlers, without calling a language model."""
import json

import pytest

from yfinder import agent, catalog, handbooks, session

SCOPE = {"bbox": [-72.998, 41.246, -72.86, 41.351], "time_start": "2025-06-01", "time_end": "2025-08-31",
         "spatial_resolution_m": 1, "unit_of_analysis": "water site"}


@pytest.fixture(scope="module")
def cat():
    return catalog.load()


@pytest.mark.parametrize("query,expected", [
    ("foot traffic mobility", "cur:dewey"),
    ("pm2.5 air quality monitors", "cur:aqs"),
    ("historical maps", "cur:yale_geoblacklight"),
    ("social vulnerability index", "cur:svi"),
    ("municipal bonds official statements", "cur:emma"),
    ("sentinel-2 surface reflectance", None),   # any Sentinel-2 card in the top 5
    ("parcels assessor connecticut", "cur:ctparcels"),
])
def test_search_hits_top5(cat, query, expected):
    ids = [r["id"] for r in cat.search(query, limit=5)]
    if expected:
        assert expected in ids, (query, ids)
    else:
        assert any("sentinel-2" in i.lower() or "s2" in i.lower() for i in ids), ids


def test_synonyms_reach_cards_without_the_query_word(cat):
    # CHIRPS says "precipitation", not "rainfall"; NDVI cards rarely say "greenness".
    assert any("CHIRPS" in r["id"] for r in cat.search("rainfall", limit=5))
    green = cat.search("greenness", limit=8)  # GeoLibre's "Seasonal greenness" map rightly ranks first
    assert len(green) >= 4 and any("NDVI" in r["id"] or "ndvi" in (r["title"] or "").lower() for r in green)


def test_search_filters(cat):
    assert all(r["access_class"] == "yale" for r in cat.search("imagery", access="yale", limit=10))
    assert all(r["kind"] == "method" for r in cat.search("validation", kind="method", limit=5))


def test_scope_aware_rank_demotes_poor_fits(cat):
    plain = [r["id"] for r in cat.search("aerial imagery", limit=8)]
    scoped = cat.search("aerial imagery", limit=8, scope=SCOPE)
    assert scoped and scoped[0]["fit"]["overall"] in ("good", "unknown"), [(r["id"], r["fit"]["overall"]) for r in scoped]
    assert plain  # sanity


def _session():
    s = session.get(None)
    s.citations = set()
    return s


def test_graph_handler_validates_ids():
    s = _session()
    res, ev, _ = agent.h_update_influence_graph(s, {
        "question": "q", "outcome": "Larval Abundance",
        "nodes": [{"id": "Larval Abundance", "label": "Larval abundance", "role": "outcome", "observable": "counts", "core": True},
                  {"id": "rain", "label": "Rainfall", "role": "driver", "observable": "daily precipitation", "core": True}],
        "edges": [{"source": "rain", "target": "Larval Abundance", "note": "fills containers"},
                  {"source": "ghost", "target": "rain", "note": "bad"}]})
    assert res["nodes"] == 2 and res["edges"] == 1 and "dropped" in res["note"]
    assert s.graph["outcome"] == "larval_abundance"
    assert ev[0]["type"] == "graph"


def test_scope_rejects_bad_bbox():
    s = _session()
    with pytest.raises(agent.ToolError):
        agent.h_set_scope(s, {"extent_name": "x", "bbox": [10, 10, 5, 5], "time_start": "2020", "time_end": "2021",
                              "spatial_resolution_m": None, "unit_of_analysis": "tract", "temporal_frequency": "annual",
                              "assumed": [], "rationale": ""})


def test_recommend_marks_unverified_and_labels_fit(cat):
    s = _session()
    agent.h_update_influence_graph(s, {"question": "q", "outcome": "y", "nodes": [
        {"id": "y", "label": "Y", "role": "outcome", "observable": "", "core": True},
        {"id": "water", "label": "Standing water", "role": "driver", "observable": "", "core": True}], "edges": []})
    agent.h_set_scope(s, dict(extent_name="New Haven", bbox=SCOPE["bbox"], time_start="2025-06-01", time_end="2025-08-31",
                              spatial_resolution_m=1, unit_of_analysis="water site", temporal_frequency="weekly",
                              assumed=[], rationale=""))
    pick = dict(title="t", factors=["water"], role="core", why="w", limitations="l", spatial_resolution_m=None,
                time_start=None, time_end=None, coverage=None)
    res, ev, _ = agent.h_recommend_data(s, {"picks": [
        dict(pick, ref="cur:eagleview"),
        dict(pick, ref="pc:sentinel-2-l2a"),
        dict(pick, ref="https://example.org/made-up-dataset", spatial_resolution_m=0.1)],
        "methods": [{"name": "Terrain and hydrology", "why": "find depressions", "tools": "WhiteboxTools"}], "next_steps": []})
    by = {p["ref"]: p for p in res["picks"]}
    assert by["cur:eagleview"]["verified"] == "catalog"
    assert by["pc:sentinel-2-l2a"]["fit"].startswith("poor")          # 10 m vs 1 m
    assert by["https://example.org/made-up-dataset"]["verified"] == "unverified"
    assert "warning" in res
    assert s.graph["nodes"][1]["data_coverage"] in ("covered", "partial")
    assert s.methods[0]["card"] == "method:terrain"
    assert any(e["type"] == "recommendations" for e in ev)


def test_scope_change_rechecks_picks():
    s = _session()
    agent.h_set_scope(s, dict(extent_name="NH", bbox=SCOPE["bbox"], time_start="2025", time_end="2025",
                              spatial_resolution_m=30, unit_of_analysis="pixel", temporal_frequency="annual", assumed=[], rationale=""))
    agent.h_recommend_data(s, {"picks": [dict(ref="pc:sentinel-2-l2a", title="S2", factors=[], role="core", why="", limitations="",
                                              spatial_resolution_m=None, time_start=None, time_end=None, coverage=None)],
                               "methods": [], "next_steps": []})
    assert s.picks[0]["fit"]["resolution"]["label"] == "good"
    agent.h_set_scope(s, dict(extent_name="NH", bbox=SCOPE["bbox"], time_start="2025", time_end="2025",
                              spatial_resolution_m=1, unit_of_analysis="pixel", temporal_frequency="annual", assumed=[], rationale=""))
    assert s.picks[0]["fit"]["resolution"]["label"] == "poor"


def test_tool_schemas_are_strict():
    for t in agent.TOOLS:
        p = t["parameters"]
        assert t["strict"] and p["additionalProperties"] is False
        assert set(p["required"]) == set(p["properties"]), t["name"]
        assert t["name"] in agent.HANDLERS, t["name"]
    json.dumps(agent.TOOLS)


def test_handbook_validator():
    good = ("## What it is\nx\n## How to get access\nx\n## Programmatic access\nx\n## Python example\n```python\nprint(1)\n```\n"
            "## Pitfalls\nx\n## Sources checked\n- https://example.org\n")
    assert handbooks.validate(good) == []
    assert any("parse" in p for p in handbooks.validate(good.replace("print(1)", "print(")))
    assert any("secret" in p for p in handbooks.validate(good + "\nsk-" + "a" * 30))


def test_norm_url_matching():
    assert agent.norm_url("https://www.EPA.gov/air/?utm_source=x#top") == agent.norm_url("http://epa.gov/air")
    assert agent.norm_url("https://a.org/x?y=1") != agent.norm_url("https://a.org/z")


def test_web_sources_count_as_found_but_unseen_urls_do_not():
    s = _session()
    s.citations = {"https://aqs.epa.gov/aqsweb/airdata/download_files.html"}
    assert agent._resolve(s, "https://aqs.epa.gov/aqsweb/airdata/download_files.html?", "AQS")[1] == "web"
    assert agent._resolve(s, "https://made-up.example/data", "x")[1] == "unverified"
    assert agent._resolve(s, "cur:aqs", "AQS")[1] == "catalog"


def test_scene_id_resolves_to_collection():
    s = _session()
    s.stac_items["S2B_MSIL2A_TEST"] = {"id": "S2B_MSIL2A_TEST", "collection": "sentinel-2-l2a"}
    meta, how = agent._resolve(s, "S2B_MSIL2A_TEST", "scene")
    assert how == "search" and meta["id"] == "pc:sentinel-2-l2a"


def test_stac_preview_adds_mosaic_partners(monkeypatch):
    s = _session()
    s.scope = {"bbox": [-72.90, 41.29, -72.86, 41.32]}
    a = {"id": "A", "collection": "naip", "datetime": "2023-07-05T00:00:00Z", "bbox": [-72.88, 41.28, -72.80, 41.33], "tilejson": "x"}
    b = {"id": "B", "collection": "naip", "datetime": "2023-07-06T00:00:00Z", "bbox": [-72.95, 41.28, -72.87, 41.33], "tilejson": "y"}
    far = {"id": "C", "collection": "naip", "datetime": "2021-07-06T00:00:00Z", "bbox": [-72.95, 41.28, -72.87, 41.33], "tilejson": "z"}
    s.stac_items = {"A": a, "B": b, "C": far}
    monkeypatch.setattr(agent.sources, "stac_tiles", lambda u: {"tiles": [f"https://t/{u}/{{z}}/{{x}}/{{y}}"], "bounds": None, "minzoom": 0, "maxzoom": 18})
    res, ev, _ = agent.h_map_stac_preview(s, {"item_id": "A", "title": "NAIP"})
    assert res["shown"] == ["A", "B"]                      # same-week neighbor added, 2021 scene not
    assert res["approx_study_area_covered_pct"] == 100
    lay = [e["layer"] for e in ev if e["type"] == "layer"]
    assert len(lay) == 1 and len(lay[0]["parts"]) == 1      # one map entry; the neighbor is a part of it
    assert lay[0]["title"] == "NAIP · 2023-07-05 · 2 scenes" and lay[0]["date"] == "2023-07-05"
