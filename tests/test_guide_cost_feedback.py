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
    assert b"window.YF_PRICES={" in client.get("/").data  # inlined, so restored conversations show their cost at once


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


# ---------------------------------------------------------------------------- review round (2026-11-10)
def test_price_override_is_merged_and_never_breaks_a_turn():
    base = config.LIST_PRICES
    p = config.price_overrides('{"gpt-6.1-terra": {"input": 0.8}}', base)
    assert p["gpt-6.1-terra"] == {"input": 0.8, "cached": 0.05, "output": 5.0}  # the other prices stay
    assert base["gpt-6.1-terra"]["input"] == 1.0                                 # the list itself is untouched
    assert config.price_overrides("[1]", base) == base and config.price_overrides("not json", base) == base
    p = config.price_overrides('{"new-model": {"input": 3, "output": "x", "cached": -1, "extra": 9}, "gpt-6-luna": 5}', base)
    assert p["new-model"] == {"input": 3.0, "cached": 0.0, "output": 0.0} and p["gpt-6-luna"] == base["gpt-6-luna"]


def test_feedback_survives_odd_bodies_and_keeps_the_vote_id(client):
    app_module._fb_rate.clear()
    assert client.post("/api/feedback", json=[1, 2]).status_code == 400
    r = client.post("/api/feedback", data='{"vote": "up", "turn": 1e999, "id": "ab-12<x>"}', content_type="application/json")
    assert r.status_code == 200
    row = json.loads((app_module.FEEDBACK_DIR / "feedback.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["turn"] == 0 and row["id"] == "ab12x"


def test_feedback_needs_the_access_code_when_one_is_set(client, monkeypatch):
    app_module._fb_rate.clear()
    monkeypatch.setattr(app_module, "ACCESS_CODE", "open-sesame")
    assert client.post("/api/feedback", json={"vote": "up"}).status_code == 401
    assert client.post("/api/feedback", json={"vote": "up"}, headers={"X-Access-Code": "open-sesame"}).status_code == 200


def test_feedback_file_stops_growing(client, monkeypatch):
    app_module._fb_rate.clear()
    assert client.post("/api/feedback", json={"vote": "up"}).status_code == 200
    size = (app_module.FEEDBACK_DIR / "feedback.jsonl").stat().st_size
    monkeypatch.setattr(app_module, "FEEDBACK_MAX_BYTES", size)
    assert client.post("/api/feedback", json={"vote": "up"}).status_code == 429
    assert (app_module.FEEDBACK_DIR / "feedback.jsonl").stat().st_size == size


def test_rate_limit_address_cannot_be_spoofed_past_the_proxy():
    app = create_app()
    cf_edge, visitor, attacker = "172.64.1.1", "203.0.113.7", "198.51.100.9"
    with app.test_request_context(headers={"X-Real-IP": cf_edge, "CF-Connecting-IP": visitor}):
        assert app_module._client_ip() == visitor          # through Cloudflare: the visitor
    with app.test_request_context(headers={"X-Real-IP": attacker, "CF-Connecting-IP": "1.2.3.4"}):
        assert app_module._client_ip() == attacker         # straight to the server: the header is ignored
    with app.test_request_context(headers={"X-Real-IP": visitor}):
        assert app_module._client_ip() == visitor          # nginx's real-ip module already swapped the visitor in
    with app.test_request_context(headers={"X-Real-IP": "2606:4700::1", "CF-Connecting-IP": "2001:db8::5"}):
        assert app_module._client_ip() == "2001:db8::5"
    with app.test_request_context(headers={"X-Real-IP": "garbage", "CF-Connecting-IP": "1.2.3.4"}):
        assert app_module._client_ip() == "garbage"


def test_acs_measure_ignores_words_that_say_nothing_about_the_measure():
    f = previews._acs_measure
    assert f("Neighborhood residents", "racial composition by tract") == "race"
    assert f("Unemployed people", "") == "income"
    assert f("University students", "") == "income"
    assert f("School-age children", "") == "children"
    assert f("Road density", "") == "income" and f("Population density", "") == "population"
    assert previews._plural("county") == "counties" and previews._plural("tract") == "tracts"


def test_failed_turns_get_no_feedback_row_and_votes_carry_an_id():
    js = (config.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "!m.failed) feedbackRow(m)" in js and "case 'error': m.failed = true;" in js
    assert "m.fbInfo = { id: " in js


def test_the_server_address_is_not_in_the_repository():
    """The repository is public: deploy files and notes name no server address (deploy/host.txt is git-ignored)."""
    import re
    ip = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
    for name in ("deploy/deploy.sh", "handoff.md", "README.md", "deploy/nginx-yfinder.conf", "deploy/yfinder.service"):
        found = [m for m in ip.findall((config.ROOT / name).read_text(encoding="utf-8")) if m not in ("127.0.0.1", "0.0.0.0")]
        assert not found, (name, found)
    assert "deploy/host.txt" in (config.ROOT / ".gitignore").read_text(encoding="utf-8")
