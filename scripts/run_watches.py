"""Run the follow-up rounds that are due. Scheduled daily by deploy/yfinder-watch.timer.

Usage: python scripts/run_watches.py            run due watches
       python scripts/run_watches.py --token T  run one watch now (testing)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from yfinder import watch  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--token")
a = ap.parse_args()
if a.token:
    u = watch.run_one(a.token)
    if not u:
        sys.exit("watch not found, not active, or already running")
    print(u["summary"][:800], "\nnew:", [n["title"] for n in u["new"]])
else:
    print("ran:", watch.run_due())
