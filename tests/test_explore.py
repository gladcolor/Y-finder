"""Y-finder explores the previews: statistics, the plan the graph suggests, pause and stop, and honest wording.
No model calls and no geocoder here (conftest turns both off)."""
import time

import numpy as np
import pytest

from yfinder import agent, explore, session
from yfinder.app import create_app

BOX = [-73.0, 41.0, -72.0, 42.0]


def square(x0, y0, d):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x0 + d, y0], [x0 + d, y0 + d], [x0, y0 + d], [x0, y0]]]}


def grid(fn, n=8, box=BOX, name="Tract"):
    d = (box[2] - box[0]) / n
    feats = []
    for i in range(n):
        for j in range(n):
            feats.append({"type": "Feature", "geometry": square(box[0] + j * d, box[1] + i * d, d),
                          "properties": {"name": f"{name} {i}{j}", **fn(i, j)}})
    return {"type": "FeatureCollection", "features": feats}


def layer(fc, title, prop=None, label=None):
    d = session.save_layer(fc, title, "test")
    if prop:
        d["paint"] = {"mode": "graduated", "property": prop, "label": label or prop}
    return d


def points(rows, title):
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]}, "properties": p}
                                                    for x, y, p in rows]}
    return session.save_layer(fc, title, "test")


@pytest.fixture()
def s(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "LAYER_DIR", tmp_path / "layers")
    ses = session.get(None)
    ses.scope = {"bbox": BOX, "time_start": "2020", "time_end": "2024", "extent_name": "Test county"}
    yield ses
    run = getattr(ses, "explore", None)
    if run:
        run.stopped = True
        run.gate.set()
    session.reset(ses.sid)


def use(s, graph, data):
    """data: [(ref, factors, role, layers, series)]"""
    s.graph = graph
    s.picks, s.previews = [], {}
    for ref, factors, role, layers, series in data:
        s.picks.append({"ref": ref, "title": ref, "factors": factors, "role": role, "meta": {}})
        s.previews[ref] = {"status": "ready", "layers": layers, "series": series, "note": "", "sig": "x"}


def wait(s, until=("done", "stopped"), timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        p = explore.public(s)
        if p["status"] in until:
            return p
        time.sleep(0.05)
    raise AssertionError(f"exploration still {explore.public(s)['status']}")


def heat_graph():
    return {"question": "Do poorer tracts have more asthma?", "outcome": "asthma",
            "nodes": [{"id": "asthma", "label": "Asthma rate", "role": "outcome", "core": True, "observable": "asthma prevalence"},
                      {"id": "income", "label": "Household income", "role": "driver", "core": True, "observable": "median household income"}],
            "edges": [{"source": "income", "target": "asthma", "note": "poverty raises exposure"}]}


def heat_data(rng=np.random.default_rng(3)):
    inc = layer(grid(lambda i, j: {"inc": 30000 + 10000 * j + float(rng.normal(0, 800))}), "ACS median household income by tract",
                "inc", "Median household income (US$)")
    ast = layer(grid(lambda i, j: {"rate": 15 - j + float(rng.normal(0, 0.3))}), "CDC PLACES asthma by tract", "rate", "Current asthma (%)")
    return [("cur:acs", ["income"], "core", [inc], None), ("cur:places", ["asthma"], "core", [ast], None)]


# ---------------------------------------------------------------------------- statistics
def test_rank_correlation_and_ties():
    a = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], float)
    assert explore.spearman(a, a ** 3) == pytest.approx(1.0)
    assert explore.spearman(a, -a) == pytest.approx(-1.0)
    assert list(explore.rank(np.array([3.0, 1.0, 3.0]))) == [1.5, 0.0, 1.5]
    assert explore.spearman(a, np.ones(10)) is None


def test_morans_i_sees_clustering_and_not_noise():
    xs, ys = np.meshgrid(np.arange(10.0), np.arange(10.0))
    x, y = xs.ravel() * 0.01, ys.ravel() * 0.01
    m = explore.moran(x, y, xs.ravel().copy())
    assert m["I"] > 0.5 and m["p"] <= 0.01
    noise = explore.moran(x, y, np.random.default_rng(1).normal(size=100))
    assert abs(noise["I"]) < 0.25 and noise["p"] > 0.01


def test_points_fall_in_the_right_area_and_not_in_holes():
    ring = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring, hole]}, "properties": {}},
        {"type": "Feature", "geometry": square(20, 0, 5), "properties": {}}]}
    g = explore.Geo({"id": "x", "title": "t"}, fc)
    got = explore.locate(np.array([1.0, 5.0, 22.0, 50.0]), np.array([1.0, 5.0, 2.0, 50.0]), g)
    assert list(got) == [0, -1, 1, -1]
    assert g.area_km2[1] == pytest.approx(5 * 111.32 * np.cos(np.radians(2.5)) * 5 * 110.57, rel=0.02)


# ---------------------------------------------------------------------------- the plan and its findings
def test_the_graph_sets_the_plan_and_the_link_is_measured(s):
    use(s, heat_graph(), heat_data())
    explore.control(s, "start")
    p = wait(s)
    kinds = [x["id"].split(":")[0] for x in p["plan"]]
    assert kinds[0] == "p" and "asthma" in p["plan"][0]["title"].lower() or "Look at" in p["plan"][0]["title"]
    assert kinds[1] == "r"  # the link into the outcome comes right after the outcome itself
    rel = next(f for f in p["findings"] if f["kind"] == "relate")
    assert rel["edge"] == ["income", "asthma"] and rel["rho"] < -0.8
    assert "tends to be lower" in rel["headline"] and "Spearman" in rel["detail"]
    prof = p["findings"][0]
    assert "high values sit together" in prof["headline"] and "Moran's I" in prof["detail"]
    assert prof["chart"]["type"] == "hist" and rel["chart"]["type"] == "scatter"
    assert p["synthesis"] is None  # no model in tests
    assert "exploration of the previews found" in agent._state_note(s)


def test_pause_holds_the_next_step_and_stop_keeps_what_was_found(s, monkeypatch):
    use(s, heat_graph(), heat_data())
    real = explore.run_step

    def slow(st, ses):
        time.sleep(0.25)
        return real(st, ses)
    monkeypatch.setattr(explore, "run_step", slow)
    explore.control(s, "start")
    assert explore.public(s)["status"] in ("running", "waiting")
    explore.control(s, "pause")
    assert explore.public(s)["status"] == "paused"
    time.sleep(0.4)  # the step under way may finish, but no new one starts
    n = len(explore.public(s)["findings"])
    time.sleep(0.6)
    assert len(explore.public(s)["findings"]) == n and explore.public(s)["status"] == "paused"
    assert any(x["status"] == "todo" for x in explore.public(s)["plan"])
    explore.control(s, "resume")
    p = wait(s)
    assert p["status"] == "done" and len(p["findings"]) > n
    explore.control(s, "start")
    explore.control(s, "stop")
    p = wait(s, ("stopped",))
    time.sleep(0.5)
    p = explore.public(s)
    assert p["status"] == "stopped" and any(x["status"] == "todo" for x in p["plan"])
    assert p["step"].startswith("Stopped after")


def test_a_finished_run_turns_stale_when_the_scope_changes(s):
    use(s, heat_graph(), heat_data())
    explore.control(s, "start")
    wait(s)
    s.scope = dict(s.scope, time_start="2010")
    assert explore.public(s)["status"] == "stale"


def test_an_uneven_preview_is_reported_instead_of_a_trend(s):
    rng = np.random.default_rng(5)
    rows = [(-72.5 + rng.normal(0, 0.05), 41.5 + rng.normal(0, 0.05), {"arst_date": "2023-05-01T00:00:00"}) for _ in range(180)]
    rows += [(-72.3 + rng.normal(0, 0.2), 41.4 + rng.normal(0, 0.2), {"arst_date": f"{y}-03-01"}) for y in (2019, 2020, 2021, 2022) for _ in range(5)]
    pts = points(rows, "Arrest data 2019 to 2023")
    g = {"question": "Where were arrests concentrated?", "outcome": "arrests",
         "nodes": [{"id": "arrests", "label": "Arrests", "role": "outcome", "core": True, "observable": "arrest records"}], "edges": []}
    use(s, g, [("https://data.example.org/arrests", ["arrests"], "core", [pts], None)])
    s.previews["https://data.example.org/arrests"]["note"] = "a sample of 200 of 50000 features"
    explore.control(s, "start")
    f = wait(s)["findings"][0]
    assert f["coverage_warning"] and "not an even sample" in f["detail"] and "busiest year" not in f["detail"]
    assert "Half of them fall in" in f["detail"] and f["focus"]["points"]


def test_an_indirect_link_and_a_stand_in_measure_are_said_plainly(s):
    rng = np.random.default_rng(9)
    inc = layer(grid(lambda i, j: {"inc": 90000 - 9000 * j + float(rng.normal(0, 500))}), "ACS median household income by tract",
                "inc", "Median household income (US$)")
    rows = [(-73.0 + rng.uniform(0, 0.4), 41.0 + rng.uniform(0, 1), {}) for _ in range(300)] + \
           [(-73.0 + rng.uniform(0, 1), 41.0 + rng.uniform(0, 1), {}) for _ in range(60)]
    pts = points(rows, "Vending arrests")
    g = {"question": "How do arrests relate to Latino and Black shares?", "outcome": "arrests",
         "nodes": [{"id": "arrests", "label": "Street-vending arrests", "role": "outcome", "core": True, "observable": "arrests"},
                   {"id": "race", "label": "Latino and Black resident shares", "role": "context", "core": True, "observable": "shares"},
                   {"id": "enf", "label": "Police enforcement", "role": "mediator", "core": False, "observable": "patrols"}],
         "edges": [{"source": "race", "target": "enf", "note": ""}, {"source": "enf", "target": "arrests", "note": ""}]}
    use(s, g, [("cur:acs", ["race"], "core", [inc], None), ("lapd", ["arrests"], "core", [pts], None)])
    explore.control(s, "start")
    p = wait(s)
    rel = next(f for f in p["findings"] if f["kind"] == "relate")
    assert rel["edge"] == ["race", "arrests"] and "(indirect)" in rel["detail"] and "Police enforcement" in rel["detail"]
    assert "stood in for by median household income" in rel["detail"]
    assert "per km²" in rel["headline"] and rel["rho"] > 0.3  # arrests sit in the west, where income is higher here


def test_a_world_layer_says_where_the_study_area_ranks(s):
    rng = np.random.default_rng(2)
    world = grid(lambda i, j: {"key": "C" + chr(65 + i) + chr(65 + j), "score": float(rng.uniform(10, 90))}, n=6, box=[60.0, 20.0, 80.0, 40.0], name="Land")
    world["features"][7]["properties"].update(name="Pakistan", score=7.5)
    lay = layer(world, "Environmental Performance Index 2026: Air Quality", "score", "Air Quality (AIR.new)")
    s.scope = dict(s.scope, bbox=[64.0, 24.0, 66.0, 26.0])
    g = {"question": "Pakistan air quality", "outcome": "air", "nodes": [{"id": "air", "label": "Air quality", "role": "outcome", "core": True, "observable": ""}], "edges": []}
    use(s, g, [("cur:epi", ["air"], "core", [lay], None)])
    explore.control(s, "start")
    f = wait(s)["findings"][0]
    assert f["headline"].startswith("Pakistan ranks 36 of 36") and "AIR.new" not in f["headline"]
    assert "rank 1 is the highest value" in f["detail"]


def test_values_inside_and_outside_zones(s):
    rng = np.random.default_rng(4)
    zones = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": square(-72.9, 41.1, 0.3), "properties": {"kind": "smoke"}},
                                                       {"type": "Feature", "geometry": square(-72.3, 41.6, 0.3), "properties": {"kind": "smoke"}}]}
    zl = session.save_layer(zones, "Smoke plumes", "test")
    rows = [(-72.9 + rng.uniform(0, 0.3), 41.1 + rng.uniform(0, 0.3), {"name": f"in{i}", "pm": float(30 + rng.normal(0, 2))}) for i in range(10)]
    rows += [(-72.55 + rng.uniform(0, 0.15), 41.2 + rng.uniform(0, 0.8), {"name": f"out{i}", "pm": float(12 + rng.normal(0, 2))}) for i in range(20)]
    mon = points(rows, "PM2.5 monitors")
    mon["paint"] = {"mode": "graduated", "property": "pm", "label": "PM2.5 (µg/m³)"}
    g = {"question": "Smoke and PM2.5", "outcome": "pm25",
         "nodes": [{"id": "pm25", "label": "PM2.5", "role": "outcome", "core": True, "observable": ""},
                   {"id": "smoke", "label": "Smoke", "role": "driver", "core": True, "observable": ""}],
         "edges": [{"source": "smoke", "target": "pm25", "note": ""}]}
    use(s, g, [("cur:aqs", ["pm25"], "core", [mon], None), ("cur:hms", ["smoke"], "supporting", [zl], None)])
    explore.control(s, "start")
    rel = next(f for f in wait(s)["findings"] if f["kind"] == "relate")
    assert "higher inside Smoke plumes" in rel["headline"] and rel["chart"]["type"] == "groups" and rel["rho"] > 0.8


def test_counts_are_flagged_but_incomes_are_not(s):
    permits = layer(grid(lambda i, j: {"2025": float(i * 10 + j)}, name="Town"), "Connecticut annual housing permit data: 2025", "2025", "2025 (per town)")
    g = {"question": "Permits", "outcome": "permits", "nodes": [{"id": "permits", "label": "Housing permits", "role": "outcome", "core": True, "observable": ""}], "edges": []}
    use(s, g, [("permits", ["permits"], "core", [permits], None)])
    explore.control(s, "start")
    f = wait(s)["findings"][0]
    assert f["headline"].startswith("Housing permits") and "counts, not rates" in f["detail"]
    use(s, heat_graph(), heat_data())
    explore.control(s, "start")
    inc = next(f for f in wait(s)["findings"] if "income" in f["headline"].lower() and f["kind"] == "profile")
    assert "counts, not rates" not in inc["detail"]


def test_images_and_boundaries_are_not_measured(s):
    tiles = {"id": "t1", "kind": "raster", "title": "NAIP 2023"}
    bounds = session.save_layer(grid(lambda i, j: {}), "Census tracts", "test")
    g = {"question": "Trees", "outcome": "canopy", "nodes": [{"id": "canopy", "label": "Canopy", "role": "outcome", "core": True, "observable": ""}], "edges": []}
    use(s, g, [("pc:naip", ["canopy"], "core", [tiles], None), ("cur:tiger", ["canopy"], "core", [bounds], None)])
    explore.control(s, "start")
    p = wait(s)
    assert p["findings"] == [] and p["step"].startswith("Nothing to measure yet") and "NAIP" in p["skipped"][0]


# ---------------------------------------------------------------------------- the endpoint
def test_explore_endpoint(s):
    c = create_app().test_client()
    assert c.get("/api/session/nope1234/explore").status_code == 404
    assert c.post(f"/api/session/{s.sid}/explore", json={"action": "boom"}).status_code == 400
    assert c.get(f"/api/session/{s.sid}/explore").json["status"] == "idle"
    use(s, heat_graph(), heat_data())
    r = c.post(f"/api/session/{s.sid}/explore", json={"action": "start"}).json
    assert r["status"] in ("running", "waiting", "done")
    wait(s)
    assert c.get(f"/api/session/{s.sid}/explore").json["findings"]
    s.explore_runs = explore.MAX_RUNS
    assert c.post(f"/api/session/{s.sid}/explore", json={"action": "start"}).status_code == 429
