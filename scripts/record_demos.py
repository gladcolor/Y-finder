"""Record the demo cases (yfinder/data/demo_cases.json) by running them against the live agent.

Each case becomes static/demos/<id>.json with:
  steps[]       the script, with the recorded event stream for every turn and scope edit
  layers{}      GeoJSON for every vector layer, inlined so the replay works on any server
  final_state   graph, scope, picks, methods and the OpenAI response id, so a replayed
                conversation can continue live after the demo
Usage: python scripts/record_demos.py [--only habitat,smoke] [--model gpt-6.1-terra]
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from yfinder import agent, fit, session  # noqa: E402
from yfinder.config import DATA_DIR  # noqa: E402
from yfinder.personas import evaluate, record_from_events  # noqa: E402
from yfinder.session import Session  # noqa: E402

OUT = os.path.join(ROOT, "static", "demos")
FORCE = False


def round_coords(obj, nd=5):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, list):
        return [round_coords(x, nd) for x in obj]
    if isinstance(obj, dict):
        return {k: round_coords(v, nd) for k, v in obj.items()}
    return obj


def _thin(ring, max_pts):
    """Drop repeated points and keep every k-th vertex of long rings (closure kept)."""
    out = [ring[0]]
    for p in ring[1:]:
        if p != out[-1]:
            out.append(p)
    if len(out) > max_pts:
        k = -(-len(out) // max_pts)
        out = out[::k] + ([out[-1]] if (len(out) - 1) % k else [])
    if ring[0] == ring[-1] and out[0] != out[-1]:
        out.append(out[0])
    return out if len(out) >= 4 or ring[0] != ring[-1] else ring


def slim_layer(fc, budget_bytes=900_000):
    """Keep demo files small: 4 decimals (about 10 m) and thinner rings for big layers."""
    fc = round_coords(fc, 5)
    if len(json.dumps(fc)) <= budget_bytes:
        return fc
    fc = round_coords(fc, 4)
    for max_pts in (300, 120, 60, 30):
        for f in fc.get("features", []):
            g = f.get("geometry") or {}
            if g.get("type") == "Polygon":
                g["coordinates"] = [_thin(r, max_pts) for r in g["coordinates"]]
            elif g.get("type") == "MultiPolygon":
                g["coordinates"] = [[_thin(r, max_pts) for r in poly] for poly in g["coordinates"]]
            elif g.get("type") == "LineString":
                g["coordinates"] = _thin(g["coordinates"], max_pts)
            if f.get("properties") and len(f["properties"]) > 8:
                f["properties"] = dict(list(f["properties"].items())[:8])
        if len(json.dumps(fc)) <= budget_bytes:
            break
    return fc


def record(case, model):
    s = Session(sid="demo-" + case["id"])
    s.citations = set()
    steps, layers = [], {}
    t0 = time.time()
    for st in case["steps"]:
        st = dict(st)
        if st["type"] == "upload":
            text = open(os.path.join(DATA_DIR, st.pop("text_file")), encoding="utf-8").read()
            s.proposal = {"name": st["name"], "text": text, "used": False}
            st["words"] = len(text.split())
        elif st["type"] == "turn":
            events = []
            for ev in agent.run_turn(s, st["message"], model=model):
                if ev["type"] in ("status", "session"):
                    continue
                if ev["type"] == "layer" and ev["layer"].get("kind") == "geojson":
                    p = session.layer_path(ev["layer"]["id"])
                    if p:
                        layers[ev["layer"]["id"]] = slim_layer(json.loads(p.read_text(encoding="utf-8")))
                events.append(ev)
            st["events"] = events
            print(f"  {case['id']}: turn done, {sum(e['type'] == 'tool' and e.get('state') == 'done' for e in events)} tools, "
                  f"{next((e['elapsed'] for e in events if e['type'] == 'done'), '?')}s", flush=True)
        elif st.get("action") == "scope_edit":
            s.scope = {**s.scope, **st["set"]}
            s.scope["assumed"] = [a for a in s.scope.get("assumed") or [] if a != "time"]
            for p in s.picks:
                p["fit"] = fit.assess(p["meta"], s.scope)
            agent._coverage(s)
            st["events"] = [{"type": "scope", "scope": s.scope},
                            {"type": "recommendations", "picks": agent._ui_picks(s), "methods": s.methods,
                             "next_steps": getattr(s, "next_steps", [])}]
            if s.graph:
                st["events"].append({"type": "graph", "graph": s.graph})
        steps.append(st)
    all_events = [e for st in steps for e in st.get("events", [])]
    rec = record_from_events(all_events, s, time.time() - t0)
    rec["eval"] = evaluate(rec, case.get("checks", {}))
    final = {"graph": s.graph, "scope": s.scope, "picks": s.picks, "methods": s.methods,
             "next_steps": getattr(s, "next_steps", []), "prev_response_id": s.prev_response_id, "model": model,
             "layers": s.layers}
    demo = {k: case[k] for k in ("id", "title", "field", "who", "shows")}
    demo.update({"recorded": dt.date.today().isoformat(), "model": model, "seconds": round(time.time() - t0),
                 "steps": steps, "layers": layers, "final_state": final, "eval": rec["eval"]})
    path = os.path.join(OUT, case["id"] + ".json")
    if not rec["eval"]["all"] and os.path.exists(path) and not FORCE:
        # keep the previous good recording; save the failed one beside it for inspection
        path = os.path.join(ROOT, "eval_runs", "demos", case["id"] + ".failed.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(demo, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"), default=str)
    return case["id"], os.path.getsize(path), demo["seconds"], rec["eval"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--model", default="gpt-6.1-terra")
    ap.add_argument("--force", action="store_true", help="overwrite the saved demo even if checks fail")
    ap.add_argument("--slim-only", action="store_true", help="only shrink the inlined layers of existing recordings")
    a = ap.parse_args()
    if a.slim_only:
        for p in sorted(os.listdir(OUT)):
            if p.endswith(".json") and p != "index.json":
                fp = os.path.join(OUT, p)
                d = json.load(open(fp, encoding="utf-8"))
                before = os.path.getsize(fp)
                d["layers"] = {k: slim_layer(v) for k, v in d.get("layers", {}).items()}
                json.dump(d, open(fp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"), default=str)
                print(f"{p}: {before / 1024:.0f} KB -> {os.path.getsize(fp) / 1024:.0f} KB")
        return
    cases = json.load(open(os.path.join(DATA_DIR, "demo_cases.json"), encoding="utf-8"))["cases"]
    if a.only:
        cases = [c for c in cases if c["id"] in a.only.split(",")]
    global FORCE
    FORCE = a.force
    os.makedirs(OUT, exist_ok=True)
    report = [f"# Demo case results\n", f"Recorded {dt.datetime.now():%Y-%m-%d %H:%M} with {a.model}. Checks are defined in "
              "yfinder/data/demo_cases.json and scored by yfinder/personas.py.", "",
              "| Case | Checks passed | Seconds | Failed checks |", "|---|---|---|---|"]
    with cf.ThreadPoolExecutor(3) as ex:
        for cid, size, sec, ev in ex.map(lambda c: record(c, a.model), cases):
            print(f"{cid}: {size / 1024:.0f} KB, {sec}s, {ev['passed']}/{ev['total']} checks", flush=True)
    # the report covers every saved demo, not only the ones recorded in this run
    for c in json.load(open(os.path.join(DATA_DIR, "demo_cases.json"), encoding="utf-8"))["cases"]:
        p = os.path.join(OUT, c["id"] + ".json")
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding="utf-8"))
        ev = d["eval"]
        bad = "; ".join(f"{k} ({v['detail']})" for k, v in ev["checks"].items() if not v["pass"]) or "none"
        report.append(f"| {c['id']} | {ev['passed']}/{ev['total']} | {d['seconds']} | {bad} | {d['recorded']} {d['model']} |")
    report[3:5] = ["| Case | Checks passed | Seconds | Failed checks | Recorded |", "|---|---|---|---|---|"]
    open(os.path.join(ROOT, "docs", "demo_case_results.md"), "w", encoding="utf-8").write("\n".join(report) + "\n")
    index = []
    for c in json.load(open(os.path.join(DATA_DIR, "demo_cases.json"), encoding="utf-8"))["cases"]:
        p = os.path.join(OUT, c["id"] + ".json")
        if os.path.exists(p):
            d = json.load(open(p, encoding="utf-8"))
            index.append({k: d[k] for k in ("id", "title", "field", "who", "shows", "recorded", "model")})
    json.dump({"cases": index}, open(os.path.join(OUT, "index.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("index:", [c["id"] for c in index])


if __name__ == "__main__":
    main()
