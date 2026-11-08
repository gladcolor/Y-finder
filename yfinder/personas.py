"""Persona evaluation: run a scripted user through the agent and check the answer against expectations.

A run record is a plain dict built from the event stream (see record_from_events). Each check
returns (passed, detail). tests/test_persona_eval.py tests every check on synthetic records,
so a broken check cannot silently pass.
"""
from __future__ import annotations

import time

from .geo import valid_bbox


def record_from_events(events: list[dict], session, elapsed: float) -> dict:
    text = "".join(e.get("delta", "") for e in events if e["type"] == "text")
    choices = next((e for e in reversed(events) if e["type"] == "choices"), None)
    done = next((e for e in reversed(events) if e["type"] == "done"), {})
    return {
        "graph": session.graph, "scope": session.scope,
        "picks": [{k: p.get(k) for k in ("ref", "title", "role", "verified", "fit", "source")} for p in session.picks],
        "methods": session.methods,
        "tools": [e["name"] for e in events if e["type"] == "tool" and e.get("state") in ("done", "error")],
        "tool_errors": [f"{e['name']}: {e.get('summary', '')}" for e in events if e["type"] == "tool" and e.get("state") == "error"],
        "errors": [e["message"] for e in events if e["type"] == "error"],
        "text": text, "choices": choices["options"] if choices else [],
        "elapsed": round(elapsed, 1), "usage": done.get("usage", {}),
    }


def _refs(rec):
    return [(p.get("ref") or "") + " " + (p.get("title") or "") for p in rec["picks"]]


def _any_match(rec, needles):
    hay = [r.lower() for r in _refs(rec)]
    return any(n.lower() in h for n in needles for h in hay)


def _years(s):
    import re
    m = re.match(r"^\s*(\d{4})(?:-(\d{2}))?", str(s or ""))
    return (int(m.group(1)), int(m.group(2) or 0)) if m else None


def check(rec: dict, name: str, arg) -> tuple[bool, str]:
    sc = rec.get("scope") or {}
    bbox = sc.get("bbox")
    if name == "graph_min_nodes":
        n = len((rec.get("graph") or {}).get("nodes", []))
        return n >= arg, f"{n} nodes"
    if name == "scope_contains":
        lat, lon = arg
        ok = bool(bbox) and valid_bbox(bbox) and bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]
        return ok, f"bbox {bbox}"
    if name == "scope_inside":
        w, s, e, n = arg
        ok = bool(bbox) and valid_bbox(bbox) and bbox[0] >= w and bbox[1] >= s and bbox[2] <= e and bbox[3] <= n
        return ok, f"bbox {bbox}"
    if name == "scope_max_res_m":
        r = sc.get("spatial_resolution_m")
        return (r is not None and r <= arg), f"resolution {r}"
    if name == "scope_time_start_max":
        y = _years(sc.get("time_start"))
        return (y is not None and y[0] <= arg), f"start {sc.get('time_start')}"
    if name == "scope_time_includes":
        ty, tm = (int(x) for x in arg.split("-"))
        a, b = _years(sc.get("time_start")), _years(sc.get("time_end"))
        if not a or not b:
            return False, "no time window"
        start = (a[0], a[1] or 1)
        end = (b[0], b[1] or 12)
        return start <= (ty, tm) <= end, f"{sc.get('time_start')} to {sc.get('time_end')}"
    if name == "scope_unit_any":
        u = (sc.get("unit_of_analysis") or "").lower()
        return any(w in u for w in arg), f"unit '{u}'"
    if name == "picks_min":
        return len(rec["picks"]) >= arg, f"{len(rec['picks'])} picks"
    if name in ("picks_any", "picks_any_2"):
        return _any_match(rec, arg), "picks: " + ", ".join(p["ref"] for p in rec["picks"])[:300]
    if name == "no_unverified":
        bad = [p["ref"] for p in rec["picks"] if p.get("verified") == "unverified"]
        return not bad, ("unverified: " + ", ".join(bad)) if bad else "all verified"
    if name == "tool_used":
        return arg in rec["tools"], "tools: " + ", ".join(sorted(set(rec["tools"])))
    if name == "if_pick_then_fit":
        hits = [p for p in rec["picks"] if arg["ref_contains"].lower() in (p.get("ref") or "").lower()
                and (not arg.get("role") or p.get("role") == arg["role"])]
        bad = [p["ref"] for p in hits if p["fit"]["overall"] == arg["not_overall"]]
        return not bad, ("labelled " + arg["not_overall"] + ": " + ", ".join(bad)) if bad else f"{len(hits)} matching picks ok"
    if name == "no_core_pick_with_poor_extent":
        bad = [p["ref"] for p in rec["picks"] if p.get("role") == "core" and p["fit"]["extent"]["label"] == "poor"]
        return not bad, ("core picks outside area: " + ", ".join(bad)) if bad else "ok"
    if name == "methods_any":
        hay = " ".join((m.get("name", "") + " " + m.get("why", "")) for m in rec.get("methods") or []).lower()
        return any(w in hay for w in arg), "methods: " + ", ".join(m.get("name", "") for m in rec.get("methods") or [])
    if name == "text_contains_any":
        hay = rec["text"] + " " + " ".join(str(p) for p in rec["picks"])
        return any(w.lower() in hay.lower() for w in arg), "text check"
    if name == "ends_with_question":
        t = rec["text"].strip().rstrip("*_ )\n")
        return t.endswith("?"), "ends: " + t[-60:]
    if name == "choices_offered":
        return len(rec.get("choices") or []) >= 2, f"{len(rec.get('choices') or [])} choices"
    if name == "max_seconds":
        return rec["elapsed"] <= arg, f"{rec['elapsed']} s"
    raise KeyError(f"unknown check {name}")


def evaluate(rec: dict, checks: dict) -> dict:
    results = {}
    for name, arg in checks.items():
        try:
            ok, detail = check(rec, name, arg)
        except Exception as e:  # a crash in a check counts as a failure, never a pass
            ok, detail = False, f"check error: {type(e).__name__}: {e}"
        results[name] = {"pass": bool(ok), "detail": detail}
    if rec.get("errors"):
        results["no_errors"] = {"pass": False, "detail": "; ".join(rec["errors"])[:200]}
    passed = sum(r["pass"] for r in results.values())
    return {"passed": passed, "total": len(results), "all": passed == len(results), "checks": results}


def run_persona(persona: dict, model: str) -> dict:
    from . import agent
    from .session import Session

    s = Session(sid=f"eval-{persona['id']}-{model}"[:60])
    t = time.time()
    events = list(agent.run_turn(s, persona["message"], model=model))
    rec = record_from_events(events, s, time.time() - t)
    rec["persona"] = persona["id"]
    rec["model"] = model
    rec["eval"] = evaluate(rec, persona["checks"])
    return rec
