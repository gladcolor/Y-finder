"""Recorded demo cases must replay cleanly: every turn finishes, no errors, layers are inlined,
the saved run passed its own checks, and the server can load the end state for live follow-ups."""
import json
from pathlib import Path

import pytest

from yfinder import session
from yfinder.app import create_app
from yfinder.personas import evaluate

ROOT = Path(__file__).resolve().parent.parent
DEMOS = sorted((ROOT / "static" / "demos").glob("*.json"))
DEMOS = [p for p in DEMOS if p.name != "index.json"]
CASES = {c["id"]: c for c in json.loads((ROOT / "yfinder" / "data" / "demo_cases.json").read_text(encoding="utf-8"))["cases"]}


@pytest.mark.skipif(not DEMOS, reason="no recorded demos yet (run scripts/record_demos.py)")
@pytest.mark.parametrize("path", DEMOS, ids=[p.stem for p in DEMOS])
def test_demo_replays_cleanly(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    assert d["id"] in CASES, "demo not defined in demo_cases.json"
    turns = [s for s in d["steps"] if s["type"] == "turn"]
    assert turns and len(turns) == sum(s["type"] == "turn" for s in CASES[d["id"]]["steps"])
    for t in turns:
        types = [e["type"] for e in t["events"]]
        assert types[-1] == "done", "turn did not finish"
        assert "error" not in types, [e for e in t["events"] if e["type"] == "error"]
        assert "text" in types, "no answer text"
    for e in (ev for s in d["steps"] for ev in s.get("events", []) if ev["type"] == "layer"):
        if e["layer"]["kind"] == "geojson":
            assert e["layer"]["id"] in d["layers"], "vector layer not inlined"
        else:
            assert e["layer"]["tiles"][0].startswith("https://")
    assert d["eval"]["all"], {k: v for k, v in d["eval"]["checks"].items() if not v["pass"]}
    assert d["final_state"]["graph"] and d["final_state"]["scope"]
    assert path.stat().st_size < 4_000_000, "demo file too large for quick loading"


@pytest.mark.skipif(not DEMOS, reason="no recorded demos yet")
def test_demo_checks_match_case_definitions():
    for path in DEMOS:
        d = json.loads(path.read_text(encoding="utf-8"))
        assert set(d["eval"]["checks"]) - {"no_errors"} == set(CASES[d["id"]]["checks"]), d["id"]


@pytest.mark.skipif(not DEMOS, reason="no recorded demos yet")
def test_demo_load_endpoint(tmp_path):
    session.LAYER_DIR = tmp_path
    c = create_app().test_client()
    d = json.loads(DEMOS[0].read_text(encoding="utf-8"))
    r = c.post(f"/api/demo/{d['id']}/load")
    assert r.status_code == 200
    s = session.peek(r.json["sid"])
    assert s.graph and s.picks and s.prev_response_id
    for old, new in r.json["layers"].items():
        assert c.get("/" + new["url"]).status_code == 200
    assert c.post("/api/demo/../../x/load").status_code == 404
    assert c.post("/api/demo/nope/load").status_code == 404


def test_every_demo_check_is_a_known_check():
    from tests.test_persona_eval import CASES as CHECK_CASES
    known = {c[0] for c in CHECK_CASES}
    for cid, case in CASES.items():
        assert case.get("checks"), f"{cid} has no checks"
        assert set(case["checks"]) <= known, (cid, set(case["checks"]) - known)
        evaluate({"graph": None, "scope": None, "picks": [], "methods": [], "tools": [], "errors": [], "text": "",
                  "choices": [], "elapsed": 0}, case["checks"])  # must not raise
