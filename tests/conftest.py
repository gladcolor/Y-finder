"""When YF_MUTANT is set, break one safeguard on purpose (used by scripts/mutation_check.py).

A good test suite must fail for every mutant. If a mutant survives, the tests are too weak.
"""
import os
import re

os.environ.setdefault("YFINDER_JANITOR", "0")  # tests call janitor.sweep() themselves
os.environ.setdefault("YFINDER_PREVIEWS", "0")  # no background network calls; tests run previews.build directly
MUTANT = os.environ.get("YF_MUTANT")

if MUTANT:
    from yfinder import agent, catalog, fit, sources

    def _good(*_a, **_k):
        return {"label": "good", "reason": "mutant"}

    if MUTANT == "resolution_always_good":
        fit.check_resolution = _good
    elif MUTANT == "extent_ignored":
        fit.check_extent = _good
    elif MUTANT == "time_ignored":
        fit.check_time = _good
    elif MUTANT == "overall_best_not_worst":
        _orig = fit.assess

        def assess(meta, scope):
            f = _orig(meta, scope)
            known = [f[k]["label"] for k in ("extent", "time", "resolution") if f[k]["label"] in fit.ORDER]
            f["overall"] = max(known, key=fit.ORDER.get) if known else "unknown"
            return f
        fit.assess = assess
    elif MUTANT == "everything_verified":
        agent._resolve = lambda s, ref, title: ({"title": title}, "catalog")
    elif MUTANT == "no_url_safety":
        sources.safe_url = lambda url: True
        sources._public_host = lambda host: True
    elif MUTANT == "tokenizer_drops_sensor_names":
        catalog._SENSOR = re.compile(r"(?!x)x")
        catalog._COMPOUND = re.compile(r"(?!x)x")
    elif MUTANT == "no_synonyms":
        catalog.SYNONYMS.clear()
    elif MUTANT == "graph_keeps_bad_edges":
        _orig_graph = agent.h_update_influence_graph

        def graph(s, a):
            res, ev, label = _orig_graph(s, a)
            res.pop("note", None)
            s.graph["edges"] += [{"source": "ghost", "target": s.graph["outcome"], "note": ""}]
            res["edges"] = len(s.graph["edges"])
            return res, ev, label
        agent.h_update_influence_graph = graph
    elif MUTANT == "downloads_kept_after_session":
        from yfinder import downloads, session
        session.END_HOOKS.clear()
    elif MUTANT == "download_follows_redirects":
        from yfinder import downloads
        downloads._http_get = lambda url: downloads.requests.get(url, headers=downloads.UA, stream=True, allow_redirects=True)
    elif MUTANT == "geolibre_any_url":
        from yfinder import geolibre
        geolibre.SLUG_RE = re.compile(r".+")
    elif MUTANT == "previews_not_session_only":
        from yfinder import downloads
        downloads.track = lambda s, layers, label: None
    else:
        raise SystemExit(f"unknown mutant {MUTANT}")
