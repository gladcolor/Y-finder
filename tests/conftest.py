"""When YF_MUTANT is set, break one safeguard on purpose (used by scripts/mutation_check.py).

A good test suite must fail for every mutant. If a mutant survives, the tests are too weak.
"""
import os
import re

os.environ.setdefault("YFINDER_JANITOR", "0")  # tests call janitor.sweep() themselves
os.environ.setdefault("YFINDER_PREVIEWS", "0")  # no background network calls; tests run previews.build directly
os.environ.setdefault("YFINDER_BROWSER", "0")  # no headless browser in tests; they fake browser.find
os.environ.setdefault("YFINDER_LINKCHECK", "0")  # recommend_data does not open web links in tests
os.environ.setdefault("YFINDER_TABLE_SPANS", "0")  # nor asks open data portals for a table's years
os.environ.setdefault("YFINDER_EXPLORE_MODEL", "0")  # exploration never calls the model in tests
os.environ.setdefault("YFINDER_EXPLORE_NAMES", "0")  # nor the reverse geocoder
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
    elif MUTANT == "browser_cookies_to_any_site":
        from yfinder import downloads
        downloads._cookie_for = lambda url, cookies: {"Cookie": "; ".join(cookies.values())} if cookies else None
    elif MUTANT == "browser_opens_licensed_pages":
        from yfinder import browser, previews
        previews._browser_ok = lambda s, pick: browser.available() and s.browser_tries < previews.MAX_BROWSER
    elif MUTANT == "dead_links_trusted":
        agent._check_web_links = lambda picks: []
    elif MUTANT == "explore_ignores_pause":
        from yfinder import explore
        _orig_control = explore.control
        explore.control = lambda s, action: _orig_control(s, "resume" if action == "pause" else action)
    elif MUTANT == "explore_hides_uneven_sample":
        from yfinder import explore
        _orig_time = explore._time_part
        explore._time_part = lambda g, mask, sampled: _orig_time(g, mask, False)
    elif MUTANT == "explore_no_stand_in_note":
        from yfinder import explore
        explore._proxy = lambda v, factor: ""
    elif MUTANT == "acs_always_income":
        from yfinder import previews
        previews.ESRI_ACS_RACE = previews.ESRI_ACS_INCOME
    elif MUTANT == "socrata_first_rows":
        from yfinder import previews
        previews._socrata = lambda s, pick, meta: (previews._download_table(s, pick, meta, meta["soda"] + "?$limit=50000", "x.csv", "portal")
                                                   if meta.get("soda") else None)
    elif MUTANT == "dataverse_no_join":
        from yfinder import previews
        previews._join_dataset_table = lambda *a, **k: None
    elif MUTANT == "geocode_takes_first_answer":  # no boundary preference, no retry without "District"
        from yfinder import sources
        sources._boundary = lambda data: None
        sources.ADMIN_WORDS = __import__("re").compile(r"(?!x)x")
    elif MUTANT == "us_box_spans_globe":
        from yfinder import sources
        sources._is_united_states = lambda d: False
    elif MUTANT == "already_card_without_layer":  # "Already on the map" without naming or sending the layer
        from yfinder import previews
        previews._already = lambda layer: dict(previews.ALREADY, layers=[])
    elif MUTANT == "tables_without_years":
        from yfinder import sources
        sources.socrata_span = lambda soda, field: None
    else:
        raise SystemExit(f"unknown mutant {MUTANT}")
