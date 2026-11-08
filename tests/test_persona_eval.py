"""Test the persona checks themselves on synthetic run records: each check must pass a good
record and fail a bad one. This keeps the live persona results honest."""
import json
from pathlib import Path

import pytest

from yfinder.personas import check, evaluate

FIT_GOOD = {"overall": "good", "extent": {"label": "good"}, "time": {"label": "good"}, "resolution": {"label": "good"}}
FIT_POOR_RES = {"overall": "poor", "extent": {"label": "good"}, "time": {"label": "good"}, "resolution": {"label": "poor"}}
FIT_POOR_EXT = {"overall": "poor", "extent": {"label": "poor"}, "time": {"label": "good"}, "resolution": {"label": "good"}}


def rec(**kw):
    base = {"graph": {"nodes": [{"id": str(i)} for i in range(7)]},
            "scope": {"bbox": [-73.0, 41.24, -72.86, 41.35], "time_start": "2023-06-01", "time_end": "2023-06-30",
                      "spatial_resolution_m": 1, "unit_of_analysis": "census tract"},
            "picks": [{"ref": "cur:aqs", "title": "EPA AQS", "role": "core", "verified": "catalog", "fit": FIT_GOOD},
                      {"ref": "pc:sentinel-2-l2a", "title": "Sentinel-2", "role": "core", "verified": "catalog", "fit": FIT_POOR_RES}],
            "methods": [{"name": "Difference-in-differences", "why": "policy effect"}],
            "tools": ["search_catalog", "search_yale_geoblacklight"], "errors": [], "text": "Here is a plan. Which towns?",
            "choices": ["A", "B"], "elapsed": 60}
    base.update(kw)
    return base


CASES = [
    ("graph_min_nodes", 6, rec(), rec(graph={"nodes": [{"id": "a"}]})),
    ("scope_contains", [41.308, -72.925], rec(), rec(scope={"bbox": [34, -1, 35, 1]})),
    ("scope_inside", [-74, 40, -71, 43], rec(), rec(scope={"bbox": [-80, 40, -71, 43]})),
    ("scope_max_res_m", 5, rec(), rec(scope={"spatial_resolution_m": 30})),
    ("scope_max_res_m", 5, rec(), rec(scope={"spatial_resolution_m": None})),
    ("scope_time_start_max", 2013, rec(scope={"time_start": "2012"}), rec(scope={"time_start": "2015"})),
    ("scope_time_includes", "2023-06", rec(), rec(scope={"time_start": "2022-01-01", "time_end": "2022-12-31"})),
    ("scope_time_includes", "2023-06", rec(scope={"time_start": "2023", "time_end": "2023"}), rec(scope={"time_start": "2023-07-01", "time_end": "2023-08-01"})),
    ("scope_unit_any", ["tract"], rec(), rec(scope={"unit_of_analysis": "county"})),
    ("picks_min", 2, rec(), rec(picks=[])),
    ("picks_any", ["cur:aqs"], rec(), rec(picks=[{"ref": "cur:hms", "title": "smoke", "fit": FIT_GOOD}])),
    ("picks_any_2", ["sentinel"], rec(), rec(picks=[{"ref": "cur:hms", "title": "smoke", "fit": FIT_GOOD}])),
    ("no_unverified", True, rec(), rec(picks=[{"ref": "x", "title": "x", "verified": "unverified", "fit": FIT_GOOD}])),
    ("tool_used", "search_yale_geoblacklight", rec(), rec(tools=["search_catalog"])),
    ("if_pick_then_fit", {"ref_contains": "sentinel-2", "role": "core", "not_overall": "good"}, rec(),
     rec(picks=[{"ref": "pc:sentinel-2-l2a", "title": "S2", "role": "core", "verified": "catalog", "fit": FIT_GOOD}])),
    ("no_core_pick_with_poor_extent", True, rec(), rec(picks=[{"ref": "pc:naip", "title": "NAIP", "role": "core", "fit": FIT_POOR_EXT}])),
    ("methods_any", ["difference"], rec(), rec(methods=[{"name": "Kriging", "why": ""}])),
    ("text_contains_any", ["plan"], rec(), rec(text="Nothing relevant.")),
    ("ends_with_question", True, rec(), rec(text="Done.")),
    ("ends_with_question", True, rec(text="Which towns?**\n"), rec(text="Is it? Yes.")),
    ("choices_offered", True, rec(), rec(choices=[])),
    ("max_seconds", 120, rec(), rec(elapsed=300)),
]


@pytest.mark.parametrize("name,arg,good,bad", CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
def test_check_separates_good_from_bad(name, arg, good, bad):
    assert check(good, name, arg)[0] is True, check(good, name, arg)
    assert check(bad, name, arg)[0] is False, check(bad, name, arg)


def test_errors_fail_the_run_and_crashing_checks_fail():
    r = evaluate(rec(errors=["model error"]), {"picks_min": 1})
    assert not r["all"] and not r["checks"]["no_errors"]["pass"]
    r2 = evaluate(rec(scope=None), {"scope_contains": [0, 0]})
    assert not r2["all"]


def test_every_check_in_personas_file_is_implemented():
    data = json.loads((Path(__file__).parent / "personas.json").read_text(encoding="utf-8"))
    implemented = {c[0] for c in CASES}
    for p in data["personas"]:
        for name in p["checks"]:
            assert name in implemented, f"{p['id']}: check {name} has no good/bad test"
