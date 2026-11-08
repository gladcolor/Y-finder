"""Run the fictional YCGS personas through the live agent and write docs/persona_results.md.

Usage: python scripts/run_personas.py [--models gpt-6.1-terra,gpt-6.1-sol,gpt-6-luna] [--only id1,id2] [--workers 4]
Raw records go to eval_runs/<model>/<persona>.json (git-ignored).
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import statistics
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from yfinder.personas import run_persona  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--models", default="gpt-6.1-terra,gpt-6.1-sol,gpt-6-luna")
ap.add_argument("--only", default="")
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--out", default=os.path.join(ROOT, "docs", "persona_results.md"))
a = ap.parse_args()

personas = json.load(open(os.path.join(ROOT, "tests", "personas.json"), encoding="utf-8"))["personas"]
if a.only:
    keep = set(a.only.split(","))
    personas = [p for p in personas if p["id"] in keep]
models = a.models.split(",")
jobs = [(p, m) for m in models for p in personas]
records = []


def job(pm):
    p, m = pm
    try:
        r = run_persona(p, m)
    except Exception as e:  # keep going; a crash is a failed run
        traceback.print_exc()
        r = {"persona": p["id"], "model": m, "elapsed": 0, "usage": {}, "tools": [], "picks": [], "text": "",
             "eval": {"passed": 0, "total": 1, "all": False, "checks": {"run": {"pass": False, "detail": repr(e)[:200]}}}}
    d = os.path.join(ROOT, "eval_runs", m)
    os.makedirs(d, exist_ok=True)
    json.dump(r, open(os.path.join(d, p["id"] + ".json"), "w", encoding="utf-8"), indent=1, default=str, ensure_ascii=False)
    print(f"{m:15s} {p['id']:22s} {r['eval']['passed']}/{r['eval']['total']} {r['elapsed']}s", flush=True)
    return r


with cf.ThreadPoolExecutor(a.workers) as ex:
    records = list(ex.map(job, jobs))

lines = [f"# Persona test results\n", f"Run {dt.datetime.now():%Y-%m-%d %H:%M}. Ten fictional Yale users (tests/personas.json), one first message each,",
         "scored by automatic checks (yfinder/personas.py, itself tested in tests/test_persona_eval.py).", ""]
lines += ["| Model | Personas fully passing | Checks passed | Median time (s) | Median tool calls | Median input tokens |", "|---|---|---|---|---|---|"]
for m in models:
    rs = [r for r in records if r["model"] == m]
    full = sum(r["eval"]["all"] for r in rs)
    ch = sum(r["eval"]["passed"] for r in rs)
    tot = sum(r["eval"]["total"] for r in rs)
    med = lambda xs: statistics.median(xs) if xs else 0  # noqa: E731
    lines.append(f"| {m} | {full}/{len(rs)} | {ch}/{tot} | {med([r['elapsed'] for r in rs]):.0f} | "
                 f"{med([len(r.get('tools', [])) for r in rs]):.0f} | {med([r.get('usage', {}).get('input', 0) for r in rs]):,.0f} |")
lines += ["", "## Failed checks", ""]
anyfail = False
for r in records:
    bad = {k: v for k, v in r["eval"]["checks"].items() if not v["pass"]}
    if bad:
        anyfail = True
        lines.append(f"- **{r['model']} / {r['persona']}**: " + "; ".join(f"{k} ({v['detail']})" for k, v in bad.items()))
if not anyfail:
    lines.append("None.")
lines += ["", "## Per persona", "", "| Persona | " + " | ".join(models) + " |", "|---|" + "---|" * len(models)]
for p in personas:
    row = [p["id"]]
    for m in models:
        r = next((x for x in records if x["model"] == m and x["persona"] == p["id"]), None)
        row.append(f"{r['eval']['passed']}/{r['eval']['total']} ({r['elapsed']:.0f}s)" if r else "")
    lines.append("| " + " | ".join(row) + " |")
open(a.out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("wrote", a.out)
