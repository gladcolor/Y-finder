"""HTTP API tests with the Flask test client (no language model calls)."""
import io

import pytest

from yfinder import agent, session, watch
from yfinder.app import create_app


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    watch.WATCH_DIR = tmp_path_factory.mktemp("watches")
    session.LAYER_DIR = tmp_path_factory.mktemp("layers")
    return create_app().test_client()


def test_health_and_config(client):
    assert client.get("/healthz").json["ok"]
    c = client.get("/api/config").json
    assert c["default_model"] in {m["id"] for m in c["models"]}
    assert c["catalog"]["counts"]["total"] > 1000


def test_index_and_static(client):
    assert b"Y-finder" in client.get("/").data
    assert client.get("/static/app.js").status_code == 200


def test_chat_validation(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 400
    assert client.post("/api/chat", json={"message": "x", "model": "not-a-model"}).status_code == 400
    assert client.post("/api/chat", json={"message": "x" * 7000}).status_code == 400


def test_catalog_api(client):
    r = client.get("/api/catalog/search?q=tree+canopy&limit=5").json["results"]
    assert len(r) == 5
    assert client.get("/api/catalog/item?id=cur:planet").json["item"]["title"].startswith("Planet")
    assert client.get("/api/catalog/item?id=nope").status_code == 404


def test_upload_txt_and_reject_types(client):
    text = b"We study heat exposure and tree canopy in New Haven census tracts from 2015 to 2024. " * 3
    r = client.post("/api/upload", data={"file": (io.BytesIO(text), "proposal.txt")}, content_type="multipart/form-data")
    assert r.status_code == 200 and r.json["words"] > 20
    s = session.peek(r.json["sid"])
    assert s.proposal["name"] == "proposal.txt"
    bad = client.post("/api/upload", data={"file": (io.BytesIO(b"x" * 100), "run.exe")}, content_type="multipart/form-data")
    assert bad.status_code == 400


def _seeded_session():
    s = session.get(None)
    s.citations = set()
    agent.h_update_influence_graph(s, {"question": "Heat and canopy", "outcome": "heat", "nodes": [
        {"id": "heat", "label": "Heat & <exposure>", "role": "outcome", "observable": "LST", "core": True},
        {"id": "canopy", "label": "Tree canopy", "role": "driver", "observable": "canopy share", "core": True}],
        "edges": [{"source": "canopy", "target": "heat", "note": "shade"}]})
    agent.h_set_scope(s, dict(extent_name="NH", bbox=[-72.998, 41.246, -72.86, 41.351], time_start="2015", time_end="2024",
                              spatial_resolution_m=30, unit_of_analysis="census tract", temporal_frequency="annual",
                              assumed=["resolution"], rationale=""))
    agent.h_recommend_data(s, {"picks": [dict(ref="pc:landsat-c2-l2", title="Landsat", factors=["heat"], role="core", why="",
                                              limitations="", spatial_resolution_m=None, time_start=None, time_end=None, coverage=None)],
                               "methods": [], "next_steps": []})
    return s


def test_graphml_escapes(client):
    s = _seeded_session()
    r = client.get(f"/api/session/{s.sid}/graph.graphml")
    assert r.status_code == 200
    assert b"Heat &amp; &lt;exposure&gt;" in r.data and b'attr.name="data_coverage"' in r.data


def test_scope_edit_rechecks(client):
    s = _seeded_session()
    assert s.picks[0]["fit"]["resolution"]["label"] == "good"
    r = client.post(f"/api/session/{s.sid}/scope", json={"spatial_resolution_m": 2})
    assert r.status_code == 200
    assert r.json["picks"][0]["fit"]["resolution"]["label"] == "poor"
    assert "resolution" not in r.json["scope"]["assumed"]


def test_layers_served_with_cors(client):
    s = _seeded_session()
    lid = s.layers[-1]["id"]
    r = client.get(f"/api/layers/{lid}.geojson")
    assert r.status_code == 200 and r.headers["Access-Control-Allow-Origin"] == "*"
    assert client.get("/api/layers/../../etc.geojson").status_code == 404
    assert client.get("/api/layers/zzzz.geojson").status_code == 404


def test_watch_requires_consent_and_can_stop(client):
    s = _seeded_session()
    assert client.post(f"/api/session/{s.sid}/watch", json={"consent": False}).status_code == 400
    r = client.post(f"/api/session/{s.sid}/watch", json={"consent": True, "rounds": 99, "cadence_days": 3, "email": "a@b.org"})
    assert r.status_code == 200
    w = r.json["watch"]
    assert w["rounds_left"] == watch.MAX_ROUNDS and w["cadence_days"] == 7 and not w["email_set"]  # no SMTP: email not stored
    tok = w["token"]
    assert client.get(f"/api/watch/{tok}").json["watch"]["status"] == "active"
    assert b"<feed" in client.get(f"/api/watch/{tok}/feed.xml").data
    assert client.post(f"/api/watch/{tok}/stop").json["watch"]["status"] == "stopped"
    assert client.post(f"/api/watch/{tok}/delete").json["ok"]
    assert client.get(f"/api/watch/{tok}").status_code == 404
    assert client.get("/api/watch/..%2f..%2fx").status_code == 404


def test_handbook_missing(client):
    r = client.get("/api/handbook?ref=cur:doesnotexist")
    assert r.status_code == 404


def test_toml_export_escapes_apostrophes():
    import tomllib
    from yfinder import handbooks
    hb = {"ref": "x", "meta": {"description": "How to access World's Rivers (Somebody's Lab). Use when..."},
          "markdown": "## What it is\nA 'quoted' line.\n## Programmatic access\n- GET https://a.org\n## Python example\n```python\nprint('''x''')\n```\n## Pitfalls\n- none"}
    d = tomllib.loads(handbooks.to_llmfind_toml(hb))
    assert d["data_source_name"] == "World's Rivers"


def test_upload_blocked_while_turn_runs(client):
    s = session.get(None)
    s.lock.acquire()
    try:
        r = client.post("/api/upload", data={"sid": s.sid, "file": (io.BytesIO(b"word " * 50), "p.txt")}, content_type="multipart/form-data")
        assert r.status_code == 409
        assert client.post(f"/api/session/{s.sid}/scope", json={"time_start": "2020"}).status_code == 409
    finally:
        s.lock.release()


def test_stopped_watch_is_not_resurrected(client):
    s = _seeded_session()
    tok = client.post(f"/api/session/{s.sid}/watch", json={"consent": True}).json["watch"]["token"]
    w = watch._claim(tok)
    assert w and watch._claim(tok) is None          # second runner cannot claim the same round
    client.post(f"/api/watch/{tok}/stop")
    assert watch.load(tok)["status"] == "stopped"


def test_layer_files_also_load_under_a_readable_name(tmp_path):
    from yfinder import session as _s
    _s.LAYER_DIR = tmp_path
    lay = _s.save_layer({"type": "FeatureCollection", "features": []}, "EPI Air Quality", "test")
    from yfinder.app import create_app
    c = create_app().test_client()
    a = c.get("/" + lay["url"])
    b = c.get("/" + lay["url"].replace(".geojson", "") + "/EPI-Air-Quality.geojson")
    assert a.status_code == b.status_code == 200 and a.data == b.data and b.headers["Access-Control-Allow-Origin"] == "*"
    assert c.get("/api/layers/notanid/x.geojson").status_code == 404


def test_dead_web_links_are_marked_unverified(monkeypatch):
    """A link found by web search that does not open never counts as data; the agent is told to replace it."""
    from yfinder import agent, session
    monkeypatch.setenv("YFINDER_LINKCHECK", "1")
    monkeypatch.setattr(agent, "_link_dead", lambda url: "broken" in url)
    s = session.get(None)
    try:
        s.scope = {"bbox": [-94, 41, -93, 42]}
        s.citations = {"https://broken.example.org/data", "https://works.example.org/data"}  # links from web search answers
        blank = {"factors": [], "role": "core", "why": "", "limitations": "", "spatial_resolution_m": None, "time_start": None,
                 "time_end": None, "coverage": None, "data_type": "spatial", "title": ""}
        res, _ev, _label = agent.h_recommend_data(s, {"picks": [dict(blank, ref="https://broken.example.org/data"),
                                                                  dict(blank, ref="https://works.example.org/data")],
                                                        "methods": [], "next_steps": []})
        by = {p["ref"]: p for p in s.picks}
        assert by["https://broken.example.org/data"]["verified"] == "unverified" and by["https://works.example.org/data"]["verified"] == "web"
        assert "broken.example.org" in res["dead_links"] and "works.example.org" not in res["dead_links"]
    finally:
        session.reset(s.sid)
