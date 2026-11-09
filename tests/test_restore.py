"""Restoring a conversation the browser saved (no language model calls; the model client is faked)."""
import pytest

from yfinder import agent, session, watch
from yfinder.app import create_app


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    watch.WATCH_DIR = tmp_path_factory.mktemp("watches")
    session.LAYER_DIR = tmp_path_factory.mktemp("layers")
    return create_app().test_client()


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeResponses:
    """Answers every call with one sentence; optionally rejects any previous_response_id as expired."""

    def __init__(self, stale_chain=False):
        self.calls, self.stale_chain = [], stale_chain

    def create(self, **kw):
        self.calls.append(kw)
        if self.stale_chain and kw.get("previous_response_id"):
            raise RuntimeError("Previous response with id 'resp_old' not found. (param: previous_response_id)")
        return iter([_Obj(type="response.output_text.delta", delta="Here is more."),
                     _Obj(type="response.completed", response=_Obj(id="resp_new0000001", usage=_Obj(input_tokens=1, output_tokens=1)))])


def _fake(monkeypatch, **kw):
    fr = FakeResponses(**kw)
    monkeypatch.setattr(agent, "client", lambda: _Obj(responses=fr))
    return fr


def _saved(layer_url, sid="", rid="resp_abcdef123456"):
    """What the browser sends: the end state it shows plus a recap of the last turns."""
    return {
        "sid": sid, "model": "gpt-6.1-terra", "prev_response_id": rid,
        "graph": {"question": "Heat and canopy", "outcome": "heat",
                  "nodes": [{"id": "heat", "label": "Heat", "role": "outcome", "core": True},
                            {"id": "canopy", "label": "Canopy", "role": "driver", "core": True}],
                  "edges": [{"source": "canopy", "target": "heat"}, {"source": "ghost", "target": "heat"}]},
        "scope": {"extent_name": "New Haven", "bbox": [-72.998, 41.246, -72.86, 41.351], "time_start": "2015",
                  "time_end": "2024", "spatial_resolution_m": 30, "unit_of_analysis": "census tract", "assumed": ["time"]},
        "picks": [
            {"ref": "pc:landsat-c2-l2", "title": "Landsat", "role": "core", "factors": ["heat"], "verified": "catalog",
             "fit": {"overall": "good"}},
            {"ref": "https://example.org/canopy", "title": "City canopy layer", "resolution": "1 m", "t0": "2016",
             "t1": "2016", "bbox": [-73.0, 41.2, -72.8, 41.4], "verified": "bogus", "fit": {"overall": "nonsense"}},
            {"title": "no ref"}],
        "layers": [{"id": "a", "kind": "geojson", "title": "Scope", "url": layer_url},
                   {"id": "b", "kind": "geojson", "title": "Elsewhere", "url": "https://evil.example/x.geojson"},
                   {"id": "c", "kind": "raster", "title": "Tiles", "tiles": ["http://plain/{z}/{x}/{y}"]}],
        "recap": [{"q": "How has canopy changed in New Haven?", "a": "Use Landsat and the city canopy layer."}],
    }


def test_restore_rebuilds_session(client):
    lay = session.save_layer({"type": "FeatureCollection", "features": []}, "Scope", "test", "scope")
    r = client.post("/api/session/restore", json=_saved(lay["url"]))
    assert r.status_code == 200 and r.json["restored"]
    s = session.peek(r.json["sid"])
    assert [e["source"] for e in s.graph["edges"]] == ["canopy"]          # edge to an unknown node dropped
    assert s.scope["spatial_resolution_m"] == 30.0
    assert [p["ref"] for p in s.picks] == ["pc:landsat-c2-l2", "https://example.org/canopy"]
    landsat, web = s.picks
    assert landsat["meta"]["title"].startswith("Landsat")                 # catalog metadata comes back
    assert web["meta"]["res_m"] == 1 and web["verified"] == "unverified"   # unknown label -> unverified
    # fit is recomputed by the rules: 1 m covering New Haven in 2016 vs a 2015-2024 window at 30 m
    assert web["fit"]["extent"]["label"] == "good" and web["fit"]["resolution"]["label"] == "good"
    assert web["fit"]["time"]["label"] in {"partial", "poor"} and set(landsat["fit"]) >= {"extent", "time", "resolution", "access"}
    assert [l["id"] for l in s.layers] == ["a"]                           # only this server's layers, https tiles
    assert s.prev_response_id == "resp_abcdef123456"
    assert "canopy changed" in s.recap and s.first_message.startswith("How has canopy")
    assert "Data tab: Landsat" in s.data_note and "City canopy layer (ref https://example.org/canopy" in s.data_note
    # the restored session works with the scope editor (picks have metadata again)
    assert client.post(f"/api/session/{s.sid}/scope", json={"spatial_resolution_m": 2}).status_code == 200


def test_restore_never_overwrites_a_live_session(client):
    live = session.get(None)
    live.graph = {"question": "keep me", "outcome": "x", "nodes": [{"id": "x"}], "edges": []}
    r = client.post("/api/session/restore", json={**_saved("api/layers/0000000000000000.geojson"), "sid": live.sid})
    assert r.json == {"sid": live.sid, "restored": False}
    assert live.graph["question"] == "keep me"


def test_restore_rejects_bad_response_id_and_big_bodies(client):
    r = client.post("/api/session/restore", json={**_saved("x"), "prev_response_id": "../../etc", "model": "nope"})
    s = session.peek(r.json["sid"])
    assert s.prev_response_id is None and s.model is None and s.layers == []
    big = client.post("/api/session/restore", data="x" * 2_100_000, content_type="application/json")
    assert big.status_code == 413


def test_recap_used_when_there_is_no_chain(client, monkeypatch):
    fr = _fake(monkeypatch)
    s = session.peek(client.post("/api/session/restore", json=_saved("x", rid=None)).json["sid"])
    evs = list(agent.run_turn(s, "And for 2024?", model="gpt-6.1-terra"))
    first = fr.calls[0]
    assert "previous_response_id" not in first
    assert "canopy changed" in first["input"][0]["content"] and "And for 2024?" in first["input"][0]["content"]
    assert s.recap is None
    assert evs[-1]["type"] == "done" and evs[-1]["response_id"] == "resp_new0000001"
    # the next turn continues the chain and does not repeat the recap
    list(agent.run_turn(s, "Thanks", model="gpt-6.1-terra"))
    assert fr.calls[-1]["previous_response_id"] == "resp_new0000001"
    assert "canopy changed" not in fr.calls[-1]["input"][0]["content"]


def test_live_chain_gets_dataset_list_but_no_recap(client, monkeypatch):
    fr = _fake(monkeypatch)
    s = session.peek(client.post("/api/session/restore", json=_saved("x")).json["sid"])
    list(agent.run_turn(s, "Which are free?", model="gpt-6.1-terra"))
    sent = fr.calls[0]["input"][0]["content"]
    assert fr.calls[0]["previous_response_id"] == "resp_abcdef123456"
    assert "Data tab: Landsat" in sent and "canopy changed" not in sent      # chain carries the turns; list sent once
    list(agent.run_turn(s, "Thanks", model="gpt-6.1-terra"))
    assert "Data tab" not in fr.calls[-1]["input"][0]["content"]


def test_recap_used_when_the_saved_chain_expired(client, monkeypatch):
    fr = _fake(monkeypatch, stale_chain=True)
    s = session.peek(client.post("/api/session/restore", json=_saved("x")).json["sid"])
    evs = list(agent.run_turn(s, "And for 2024?", model="gpt-6.1-terra"))
    assert fr.calls[0]["previous_response_id"] == "resp_abcdef123456"     # tried the saved chain first
    retry = fr.calls[1]
    assert "previous_response_id" not in retry and "canopy changed" in retry["input"][0]["content"]
    assert not any(e["type"] == "error" for e in evs)
