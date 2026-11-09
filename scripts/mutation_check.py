"""Test the tests: break one safeguard at a time and confirm the suite fails.

Usage: python scripts/mutation_check.py
Exit code 0 only when the clean run passes and every mutant is killed.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUTANTS = ["resolution_always_good", "extent_ignored", "time_ignored", "overall_best_not_worst", "everything_verified",
           "no_url_safety", "tokenizer_drops_sensor_names", "no_synonyms", "graph_keeps_bad_edges",
           "downloads_kept_after_session", "download_follows_redirects", "geolibre_any_url",
           "previews_not_session_only",
           "browser_cookies_to_any_site", "browser_opens_licensed_pages", "dead_links_trusted",
           "explore_ignores_pause", "explore_hides_uneven_sample", "explore_no_stand_in_note",
           "acs_always_income", "socrata_first_rows", "dataverse_no_join",
           "geocode_takes_first_answer", "us_box_spans_globe", "tables_without_years",
           "already_card_without_layer"]


def run(mutant=None):
    env = dict(os.environ)
    env.pop("YF_MUTANT", None)
    if mutant:
        env["YF_MUTANT"] = mutant
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "tests"], cwd=ROOT, env=env, capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    return r.returncode, last


code, last = run()
print(f"clean run: {'PASS' if code == 0 else 'FAIL'}  ({last})")
ok = code == 0
for m in MUTANTS:
    code, last = run(m)
    killed = code != 0
    ok &= killed
    print(f"{'killed  ' if killed else 'SURVIVED'} {m:32s} {last}")
sys.exit(0 if ok else 1)
