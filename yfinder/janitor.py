"""Periodic cleanup so downloads and caches never fill the server.

Every INTERVAL seconds (and once shortly after start):
  * end expired sessions (6 hours idle); each ending deletes that session's downloads and their layers
  * delete download folders whose session is gone (a restart, a crash) after ORPHAN_GRACE
  * delete map layers older than LAYER_MAX_AGE that no live session shows, then cap the layer store size
  * delete cached GeoLibre projects older than GEOLIBRE_MAX_AGE and half-written files

The app runs as one gunicorn worker, so one thread per process is enough. Set YFINDER_JANITOR=0 to turn it off
(the tests do).
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from . import downloads, geolibre, session

INTERVAL = 600
ORPHAN_GRACE = 30 * 60
LAYER_MAX_AGE = 3 * 86400
LAYER_MAX_BYTES = 500 * 1024 * 1024
GEOLIBRE_MAX_AGE = 7 * 86400
PART_MAX_AGE = 3600

_started = False
_start_lock = threading.Lock()
_sweep_lock = threading.Lock()
last_report: dict = {}


def _age(p: Path, now: float) -> float:
    try:
        return now - p.stat().st_mtime
    except OSError:
        return 0


def _live_layer_ids() -> set[str]:
    ids = set()
    for sid in session.live_ids():
        s = session.peek(sid)
        for l in (s.layers if s else []):
            u = str(l.get("url") or "")
            if u.startswith("api/layers/"):
                ids.add(u.rsplit("/", 1)[-1].split(".")[0])
    return ids


def sweep(now: float | None = None) -> dict:
    now = now or time.time()
    rep = {"sessions_ended": 0, "orphan_downloads": 0, "layers": 0, "geolibre": 0, "bytes_freed": 0}
    with _sweep_lock:
        rep["sessions_ended"] = session.sweep()
        live = session.live_ids()
        if downloads.DOWNLOAD_DIR.exists():
            for d in downloads.DOWNLOAD_DIR.iterdir():
                if d.is_dir() and d.name not in live and _age(d, now) > ORPHAN_GRACE:
                    rep["bytes_freed"] += downloads.purge_dir(d)
                    rep["orphan_downloads"] += 1
        keep = _live_layer_ids()
        ld = session.LAYER_DIR
        if ld.exists():
            files = sorted(ld.glob("*.geojson"), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            for p in files:  # oldest first
                old = _age(p, now) > LAYER_MAX_AGE
                if p.stem in keep or not (old or total > LAYER_MAX_BYTES):
                    continue
                size = p.stat().st_size
                p.unlink(missing_ok=True)
                total -= size
                rep["layers"] += 1
                rep["bytes_freed"] += size
        cd = geolibre.CACHE_DIR
        if cd.exists():
            for p in cd.iterdir():
                limit = PART_MAX_AGE if p.suffix == ".part" else GEOLIBRE_MAX_AGE
                if p.is_file() and _age(p, now) > limit:
                    rep["bytes_freed"] += p.stat().st_size
                    p.unlink(missing_ok=True)
                    rep["geolibre"] += 1
    rep["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rep["downloads_bytes"] = downloads.usage()
    last_report.clear()
    last_report.update(rep)
    if any(rep[k] for k in ("sessions_ended", "orphan_downloads", "layers", "geolibre")):
        print(f"janitor: {rep}", flush=True)
    return rep


def _loop():
    time.sleep(60)
    while True:
        try:
            sweep()
        except Exception as e:  # keep the thread alive
            print(f"janitor failed: {e}", flush=True)
        time.sleep(INTERVAL)


def start() -> bool:
    global _started
    if os.environ.get("YFINDER_JANITOR", "1") == "0":
        return False
    with _start_lock:
        if _started:
            return False
        threading.Thread(target=_loop, name="yfinder-janitor", daemon=True).start()
        _started = True
    return True
