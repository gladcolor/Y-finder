"""Write data access handbooks (handbooks/<id>/SKILL.md) when compute and tokens are available.

Usage:
  python scripts/write_handbooks.py --refs cur:aqs,cur:acs,pc:naip
  python scripts/write_handbooks.py --curated            # every curated dataset without a handbook
  python scripts/write_handbooks.py --refs ... --force   # rewrite existing drafts (never overwrites status: reviewed)
"""
import argparse
import concurrent.futures as cf
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from yfinder import catalog, handbooks  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--refs", default="")
ap.add_argument("--curated", action="store_true")
ap.add_argument("--force", action="store_true")
ap.add_argument("--model", default=handbooks.HANDBOOK_MODEL)
ap.add_argument("--workers", type=int, default=3)
a = ap.parse_args()

cat = catalog.load()
refs = [r for r in a.refs.split(",") if r]
if a.curated:
    refs += [i["id"] for i in cat.items if i["id"].startswith("cur:")]
todo = []
for r in dict.fromkeys(refs):
    it = cat.get(r)
    if not it:
        print("unknown ref", r)
        continue
    hb = handbooks.get(r)
    if hb and (not a.force or hb["meta"].get("status") == "reviewed"):
        continue
    todo.append((r, it))
print(f"writing {len(todo)} handbooks with {a.model}")


def one(job):
    r, it = job
    try:
        hb = handbooks.generate(r, it, model=a.model)
        return f"{r}: {hb['meta'].get('status')}"
    except Exception as e:
        return f"{r}: FAILED {type(e).__name__}: {str(e)[:120]}"


with cf.ThreadPoolExecutor(a.workers) as ex:
    for line in ex.map(one, todo):
        print(line, flush=True)
