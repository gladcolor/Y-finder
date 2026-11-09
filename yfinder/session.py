"""In-memory conversation state plus the GeoJSON layer store.

The app runs as one process (gunicorn -w 1 --threads N), so a dict is enough.
Layers are also written to runtime/layers so their public URLs survive a restart;
GeoLibre loads them from those URLs.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field

from .config import RUNTIME_DIR
from .geo import geojson_bbox

SESSION_TTL = 6 * 3600
MAX_SESSIONS = 1000
LAYER_DIR = RUNTIME_DIR / "layers"
MAX_LAYER_BYTES = 6 * 1024 * 1024


@dataclass
class Session:
    sid: str
    created: float = field(default_factory=time.time)
    last: float = field(default_factory=time.time)
    prev_response_id: str | None = None
    model: str | None = None
    turns: int = 0
    graph: dict | None = None
    scope: dict = field(default_factory=dict)
    picks: list = field(default_factory=list)
    methods: list = field(default_factory=list)
    seen: dict = field(default_factory=dict)       # ref -> normalized metadata of every search result shown to the model
    stac_items: dict = field(default_factory=dict)  # item id -> STAC item summary
    layers: list = field(default_factory=list)      # layer descriptors sent to the UI
    proposal: dict | None = None                    # {"name", "text", "used"}
    downloads: list = field(default_factory=list)   # files downloaded for this session (deleted when it ends)
    previews: dict = field(default_factory=dict)    # ref -> map preview of a recommended dataset (previews.py)
    preview_running: bool = False
    browser_tries: int = 0                          # pages handed to Y-finder's browser (previews.MAX_BROWSER)
    plock: threading.Lock = field(default_factory=threading.Lock)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def remember(self, results: list[dict]) -> None:
        for r in results:
            ref = r.get("ref") or r.get("id")
            if ref:
                self.seen[ref] = r
                if r.get("url"):
                    self.seen.setdefault(r["url"], r)

    def public(self) -> dict:
        return {"sid": self.sid, "graph": self.graph, "scope": self.scope, "picks": self.picks,
                "methods": self.methods, "layers": self.layers, "turns": self.turns}


_sessions: dict[str, Session] = {}
_lock = threading.Lock()


# Called with the session after it ends (expired, evicted, reset). downloads.py registers the cleanup of the
# files and layers the session downloaded.
END_HOOKS: list = []


def _ended(gone: list[Session]) -> None:
    for s in gone:
        for hook in END_HOOKS:
            try:
                hook(s)
            except Exception as e:  # cleanup must never break a request
                print(f"session end hook failed for {s.sid}: {e}", flush=True)


def _expire(now: float) -> list[Session]:
    """Pop expired sessions (and the oldest quarter when there are too many). Caller holds _lock.
    Never evicts a session whose turn is still running (its lock is held)."""
    gone = []
    if len(_sessions) > MAX_SESSIONS:
        for k in sorted(_sessions, key=lambda k: _sessions[k].last)[: len(_sessions) // 4]:
            if not _sessions[k].lock.locked():
                gone.append(_sessions.pop(k))
    for k in [k for k, s in _sessions.items() if now - s.last > SESSION_TTL and not s.lock.locked()]:
        gone.append(_sessions.pop(k))
    return gone


def get(sid: str | None) -> Session:
    now = time.time()
    with _lock:
        gone = _expire(now)
        if sid and sid in _sessions:
            s = _sessions[sid]
            s.last = now
        else:
            sid = sid if (sid and 8 <= len(sid) <= 64 and sid.replace("-", "").isalnum()) else secrets.token_hex(12)
            s = Session(sid=sid)
            _sessions[sid] = s
    _ended(gone)
    return s


def peek(sid: str) -> Session | None:
    with _lock:
        return _sessions.get(sid)


def live_ids() -> set[str]:
    with _lock:
        return set(_sessions)


def sweep() -> int:
    """End expired sessions even when nobody is using the app (the janitor calls this)."""
    with _lock:
        gone = _expire(time.time())
    _ended(gone)
    return len(gone)


def reset(sid: str) -> None:
    with _lock:
        s = _sessions.pop(sid, None)
    if s:
        _ended([s])


# --------------------------------------------------------------------------- layers
_layer_lock = threading.Lock()


def save_layer(fc: dict, title: str, source: str, style: str = "default") -> dict:
    lid = secrets.token_hex(8)
    raw = json.dumps(fc, separators=(",", ":"))
    if len(raw) > MAX_LAYER_BYTES:
        # keep the first features that fit; tell the user it was truncated
        feats = fc.get("features", [])
        keep = max(1, int(len(feats) * MAX_LAYER_BYTES / len(raw) * 0.9))
        fc = {"type": "FeatureCollection", "features": feats[:keep]}
        raw = json.dumps(fc, separators=(",", ":"))
        title += f" (first {keep} features)"
    with _layer_lock:
        LAYER_DIR.mkdir(parents=True, exist_ok=True)
        (LAYER_DIR / f"{lid}.geojson").write_text(raw, encoding="utf-8")
        files = sorted(LAYER_DIR.glob("*.geojson"), key=lambda p: p.stat().st_mtime)
        for p in files[:-800]:  # keep the newest 800 layers on disk
            p.unlink(missing_ok=True)
    return {"id": lid, "kind": "geojson", "title": title, "source": source, "style": style,
            "url": f"api/layers/{lid}.geojson", "bbox": geojson_bbox(fc), "n": len(fc.get("features", []))}


def layer_path(lid: str):
    if not (len(lid) == 16 and all(c in "0123456789abcdef" for c in lid)):
        return None
    p = LAYER_DIR / f"{lid}.geojson"
    return p if p.exists() else None
