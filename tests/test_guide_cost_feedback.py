"""The cost shown under an answer, the Yes/No feedback, the design-notes page, and ACS age variables in previews."""
import json

import pytest

from yfinder import config, downloads, previews, session, sources, watch
from yfinder.app import create_app
from yfinder import app as app_module


@pytest.fixture()
def s(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "LAYER_DIR", tmp_path / "layers")
    monkeypatch.setattr(downloads, "DOWNLOAD_DIR", tmp_path / "downloads")
    ses = session.get(None)
    yield ses
    session.reset(ses.sid)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    watch.WATCH_DIR = tmp_path_factory.mktemp("watches")
    session.LAYER_DIR = tmp_path_factory.mktemp("layers")
    app_module.FEEDBACK_DIR = tmp_path_factory.mktemp("feedback")
    return create_app().test_client()


# ---------------------------------------------------------------------------- cost of a turn
def test_turn_cost_uses_list_prices_and_cached_tokens():
    usage = {"input": 53_506, "output": 2_000, "cached": 0}
    assert config.turn_cost(usage, "gpt-6.1-terra") == pytest.approx(0.0635, abs=1e-4)
    usage["cached"] = 40_000  # most of the input came from the cache: it costs a twentieth
    assert config.turn_cost(usage, "gpt-6.1-terra") == pytest.approx(0.0255, abs=1e-4)
    assert config.turn_cost(usage, "some-unknown-model") is None
    assert config.turn_cost({}, "gpt-6-luna") == 0


def test_config_lists_prices_for_every_model(client):
    c = client.get("/api/config").json
    for m in c["models"]:
        assert set(c["prices"][m["id"]]) == {"input", "cached", "output"}


# ---------------------------------------------------------------------------- feedback
def test_feedback_is_stored_without_the_conversation(client):
    r = client.post("/api/feedback", json={"vote": "down", "comment": "x" * 900, "question": "q" * 900, "model": "gpt-6.1-terra",
                                           "turn": "3", "demo": None, "sid": "abc", "conversation": "should not be stored"})
    assert r.status_code == 200 and r.json["ok"]
    rows = [json.loads(l) for l in (app_module.FEEDBACK_DIR / "feedback.jsonl").read_text(encoding="utf-8").splitlines()]
    row = rows[-1]
    assert row["vote"] == "down" and row["turn"] == 3 and row["demo"] is None
    assert len(row["comment"]) == 500 and len(row["question"]) == 300
    assert "conversation" not in row and len(row["who"]) == 12 and "." not in row["who"]  # hashed address, never the IP


def test_feedback_rejects_other_votes(client):
    assert client.post("/api/feedback", json={"vote": "maybe"}).status_code == 400
    assert client.post("/api/feedback", json={}).status_code == 400


def test_feedback_is_rate_limited(client, monkeypatch):
    app_module._fb_rate.clear()
    codes = [client.post("/api/feedback", json={"vote": "up"}).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429
    app_module._fb_rate.clear()


# ---------------------------------------------------------------------------- design notes
def test_design_notes_page_is_served_and_states_the_evidence(client):
    r = client.get("/design")
    assert r.status_code == 200 and b"Y-finder design notes" in r.data
    for phrase in (b"never grades a dataset", b"What I would not build", b"What it costs", b"Open Y-finder"):
        assert phrase in r.data
    assert "no-cache" in r.headers.get("Cache-Control", "")


def test_about_and_welcome_link_to_the_design_notes():
    js = (config.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    html = (config.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "[design notes](design)" in js and 'href="design"' in html


# ---------------------------------------------------------------------------- ACS: the variable the factor asks for
def test_acs_measure_reads_the_factors_before_the_reason():
    f = previews._acs_measure
    assert f("Child population", "") == "children"
    assert f("Children under 18 in each tract", "control for income") == "children"
    assert f("Older adults", "") == "older"
    assert f("Residents aged 65 and over", "") == "older"
    assert f("Population density", "denominator for rates") == "population"
    assert f("Household income", "children in poverty") == "income"
    assert f("Latino and Black resident shares", "") == "race"
    assert f("Housing demand", "") == "income"               # the default: socioeconomic conditions
    assert f("Exposure", "income of the residents") == "income"  # the reason names it when the factor does not


def square(x, y, d=0.05):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]}


def graph(*nodes):
    return {"nodes": [{"id": i, "label": l, "observable": ""} for i, l in nodes], "edges": []}


def test_acs_shows_the_share_under_18_for_a_child_factor(s, monkeypatch):
    seen = {}

    def query(url, bbox, max_features=2000, simplify_deg=None, out_fields="*", where="1=1"):
        seen["url"], seen["fields"] = url, out_fields
        feats = [{"type": "Feature", "geometry": square(-72.95 + i * 0.1, 41.3),
                  "properties": {"GEOID": f"0900{i}", "NAME": f"Census Tract {i}", "B01001_001E": pop,
                                 "B01001_calc_numLT18E": round(pop * kid / 100), "B01001_calc_pctLT18E": kid,
                                 "B01001_calc_numGE65E": round(pop * old / 100), "B01001_calc_pctGE65E": old}}
                 for i, (pop, kid, old) in enumerate([(4000, 28.4, 10.0), (3000, 15.2, 22.5), (40, 50.0, 0.0), (5000, 21.0, 14.0)])]
        return {"type": "FeatureCollection", "features": feats}
    monkeypatch.setattr(sources, "arcgis_query", query)
    s.scope = {"bbox": [-73.0, 41.2, -72.8, 41.4]}
    s.graph = graph(("asthma", "Childhood asthma emergency visits"), ("kids", "Child population"))
    r = previews.build(s, {"ref": "cur:acs", "role": "core", "title": "ACS", "factors": ["kids"], "why": "denominator"})
    assert "Total_Population" in seen["url"] and "B01001_calc_pctLT18E" in seen["fields"]
    lay = r["layers"][0]
    assert lay["paint"]["label"] == "Residents under 18 (%)" and lay["paint"]["property"] == "under_18_pct"
    shares = [f["properties"]["under_18_pct"] for f in json.loads(session.layer_path(lay["id"]).read_text())["features"]]
    assert shares == [28.4, 15.2, None, 21.0]  # the 40-person tract has no meaningful share
    assert r["note"].startswith("Share of residents under 18 for 4 tracts")
    # the elderly get the other share, and a plain population factor gets the count
    s.graph = graph(("falls", "Falls at home"), ("old", "Older adults"))
    r2 = previews.build(s, {"ref": "cur:acs", "role": "core", "title": "ACS", "factors": ["old"], "why": ""})
    assert r2["layers"][0]["paint"]["label"] == "Residents 65 and over (%)"
    s.graph = graph(("crime", "Burglaries"), ("pop", "Population"))
    r3 = previews.build(s, {"ref": "cur:acs", "role": "core", "title": "ACS", "factors": ["pop"], "why": "rates per resident"})
    assert r3["layers"][0]["paint"]["label"] == "Total population (ACS 5-year)"
    vals = [f["properties"]["population"] for f in json.loads(session.layer_path(r3["layers"][0]["id"]).read_text())["features"]]
    assert vals == [4000, 3000, 40, 5000]  # counts keep the small tract
