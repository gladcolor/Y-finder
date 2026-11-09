"""Data exploration while the user looks at the data.

When previews of the recommended datasets are on the map, Y-finder reads them, guided by the research question and
the influence graph, and reports what it finds one step at a time. The user can pause or stop it at any point.

The plan follows the graph:
  1. the outcome's data: how values spread, where the highest and lowest are, whether neighbouring places are alike
     (Moran's I); for points, where they concentrate, what share matches the question (the chat model picks the
     matching categories), and when they were recorded; for tables with years, the trend
  2. graph links into the outcome where both ends have data, direct links first, then links through one or two
     intermediate factors: whether the two vary together across places (Spearman rank correlation after a map join:
     points counted per area, finer areas averaged into coarser ones, values inside and outside zones, point layers
     counted on a shared grid, tables with years matched by year)
  3. the other core factors, then the remaining links
  4. a short reading of the findings against the question (the chat model; skipped without a key)
Everything runs on the preview samples, so findings say what they rest on; a correlation across places is a lead.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import Counter

import numpy as np

from . import session, sources
from .config import DEFAULT_MODEL, MODEL_IDS, openai_key

MAX_PROFILES = 5
MAX_RELATIONS = 6
MAX_RUNS = 8                 # exploration runs per session (each can make up to three short model calls)
MAX_JOIN_POINTS = 20000      # points used in a join (an even sample above this)
WAIT_SECONDS = 600           # how long a run waits for previews that are still loading
PERMUTATIONS = 199
_slots = threading.BoundedSemaphore(2)   # explorations computing at once on the whole server

ID_FIELD = re.compile(r"(^|_)(id|fid|oid|objectid|geoid|gid|fips|code|cd|key|zip|zcta|lat|lon|lng|latitude|longitude|x|y|"
                      r"shape|area|len|length|perimeter|rows|moe|n|count_id)($|_)|^(it_|gz_)?(lat|lon)", re.I)
NAME_PRIORITY = ["name", "namelsad", "neighborhood", "neighbourhood", "town", "county", "country", "district", "province",
                 "name_1", "name_2", "state", "city", "place", "label", "title", "area_desc", "gz_label", "it_caja", "site", "station"]
UNIT_NAME = re.compile(r"^(census )?(tract|block group|block|bg)\b", re.I)
COUNT_WORDS = re.compile(r"permit|count|number|arrest|case|crime|incident|record|death|birth|visit|trip|unit|housing", re.I)
DATE_FIELD = re.compile(r"date|year|^yr|start|time|period|month|day", re.I)
ISO_DATE = re.compile(r"^(1[5-9]\d\d|20\d\d)(-(\d\d))?")
UNIT_WORDS = [("block group", "block groups"), ("tract", "tracts"), ("countr", "countries"), ("county", "counties"),
              ("counties", "counties"), ("town", "towns"), ("province", "provinces"), ("district", "districts"), ("state", "states"),
              ("neighbo", "neighborhoods"), ("municipal", "municipalities"), ("zip", "ZIP codes"), ("monitor", "monitors"),
              ("site", "sites"), ("station", "stations")]
STOP = set("""the and for with from that this which what where when how does did into over under between than then their
there about across after before during within without whose would could should being been were they them these those
more most much many each some only also data rate rates risk change changes changed level levels share shares number
total value values pattern patterns study area areas year years local""".split())


def enabled() -> bool:
    return os.environ.get("YFINDER_EXPLORE", "1") != "0"


def _model_on() -> bool:
    return os.environ.get("YFINDER_EXPLORE_MODEL", "1") != "0" and bool(openai_key())


def _names_on() -> bool:
    return os.environ.get("YFINDER_EXPLORE_NAMES", "1") != "0"


# ---------------------------------------------------------------------------- reading a preview layer
class Geo:
    """A preview layer read for analysis: kind, centres, areas, a value column, names and dates."""

    def __init__(self, desc: dict, fc: dict):
        self.desc, self.id = desc, desc["id"]
        feats = [f for f in fc.get("features", []) if isinstance(f.get("geometry"), dict)]
        self.props = [f.get("properties") or {} for f in feats]
        types = Counter(f["geometry"].get("type") for f in feats)
        top = types.most_common(1)[0][0] if types else None
        self.kind = "point" if top in ("Point", "MultiPoint") else "area" if top in ("Polygon", "MultiPolygon") else "other"
        xs, ys, self.parts, boxes, areas = [], [], [], [], []
        keep = []
        for i, f in enumerate(feats):
            g = f["geometry"]
            if self.kind == "point":
                c = g.get("coordinates")
                c = c[0] if g.get("type") == "MultiPoint" and c else c
                if isinstance(c, (list, tuple)) and len(c) >= 2 and _finite(c[0], c[1]):
                    xs.append(float(c[0]))
                    ys.append(float(c[1]))
                    keep.append(i)
            elif self.kind == "area":
                polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates") or []
                parts = []
                for poly in polys:
                    rings = [np.asarray(r, float)[:, :2] for r in poly if isinstance(r, list) and len(r) >= 4]
                    if rings:
                        parts.append(rings)
                if not parts:
                    continue
                a, cx, cy = _poly_stats(parts)
                if a <= 0:
                    continue
                allc = np.concatenate([p[0] for p in parts])
                boxes.append((allc[:, 0].min(), allc[:, 1].min(), allc[:, 0].max(), allc[:, 1].max()))
                self.parts.append(parts)
                xs.append(cx)
                ys.append(cy)
                areas.append(a)
                keep.append(i)
        self.props = [self.props[i] for i in keep]
        self.x, self.y = np.asarray(xs, float), np.asarray(ys, float)
        self.box = np.asarray(boxes, float).reshape(-1, 4)
        self.area_km2 = np.asarray(areas, float)
        self.n = len(self.props)
        paint = desc.get("paint") or {}
        self.prop = paint.get("property") if paint.get("mode") == "graduated" else None
        self.label = _clean_label(paint.get("label") or self.prop or "") if self.prop else ""
        self.values = np.asarray([_num(p.get(self.prop)) for p in self.props], float) if self.prop else None
        self.categorical = paint.get("property") if paint.get("mode") == "categorized" else None
        self.name_key = _name_key(self.props)
        self.title = _short(desc.get("title") or "")
        if self.title.lower() in ("data", "dataset", "layer", "table", "map", "file") and ":" in (desc.get("title") or ""):
            self.title = _short(desc["title"].split(":", 1)[1].strip())
        self.subset = None   # (column, values, mask): the records that match the question, once a profile found them

    def name(self, i: int) -> str:
        v = self.props[i].get(self.name_key) if self.name_key else None
        return str(v).strip()[:60] if v not in (None, "") else ""

    def units(self) -> str:
        p0 = self.props[0] if self.props else {}
        if any(re.fullmatch(r"(iso_?a?3|iso|key|adm0_a3|gid_0|country_code)", k, re.I) and isinstance(v, str) and re.fullmatch(r"[A-Z]{3}", v)
               for k, v in p0.items()) and self.n >= 20:
            return "countries"
        text = " ".join([self.desc.get("title") or "", self.name_key or "", self.name(0) if self.n else ""]).lower()
        for w, plural in UNIT_WORDS:
            if w in text:
                return plural
        return "places" if self.kind == "point" else "areas"


def _finite(*v) -> bool:
    return all(isinstance(x, (int, float)) and math.isfinite(x) for x in v)


def _num(v):
    if isinstance(v, bool) or v is None:
        return np.nan
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else np.nan
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return np.nan


def _clean_label(t: str) -> str:
    t = re.sub(r"\s*\((per town|per county|per tract)\)\s*$", "", str(t)).strip()
    return t[:1].upper() + t[1:] if t else t


def _short(t: str) -> str:
    from .previews import short_title
    return short_title(t)


def _poly_stats(parts) -> tuple[float, float, float]:
    """Area (km2) and centre of the largest part (shoelace on a local equirectangular projection)."""
    total, best = 0.0, (0.0, None, None)
    for rings in parts:
        r = rings[0]
        lat0 = math.radians(float(np.mean(r[:, 1])))
        kx, ky = 111.32 * math.cos(lat0), 110.57

        def signed(ring):
            x, y = ring[:, 0] * kx, ring[:, 1] * ky
            return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))
        a_out = abs(signed(r))
        a = a_out - sum(abs(signed(h)) for h in rings[1:])
        total += max(a, 0.0)
        if a_out > best[0]:
            x, y = r[:, 0], r[:, 1]
            cross = x * np.roll(y, -1) - np.roll(x, -1) * y
            s = cross.sum()
            if abs(s) > 1e-15:
                cx = float(((x + np.roll(x, -1)) * cross).sum() / (3 * s))
                cy = float(((y + np.roll(y, -1)) * cross).sum() / (3 * s))
            else:
                cx, cy = float(x.mean()), float(y.mean())
            best = (a_out, cx, cy)
    return total, best[1] if best[1] is not None else 0.0, best[2] if best[2] is not None else 0.0


def _name_key(props: list[dict]) -> str | None:
    if not props:
        return None
    lower = {k.lower(): k for k in props[0].keys()}
    sample = props[: min(400, len(props))]
    for want in NAME_PRIORITY:
        k = lower.get(want)
        if k and sum(1 for p in sample if isinstance(p.get(k), str) and p.get(k).strip()) >= 0.6 * len(sample):
            return k
    return None


def _ring_contains(ring: np.ndarray, px: np.ndarray, py: np.ndarray) -> np.ndarray:
    out = np.zeros(len(px), bool)
    x1, y1 = ring[:, 0], ring[:, 1]
    x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
    for s in range(0, len(px), 2048):
        X, Y = px[s:s + 2048, None], py[s:s + 2048, None]
        cond = (y1 > Y) != (y2 > Y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (x2 - x1) * (Y - y1) / (y2 - y1) + x1
        out[s:s + 2048] = (cond & (X < xint)).sum(1) % 2 == 1
    return out


def locate(px: np.ndarray, py: np.ndarray, areas: Geo) -> np.ndarray:
    """Index of the area holding each point (-1 outside every area)."""
    idx = np.full(len(px), -1, int)
    for i in range(areas.n):
        b = areas.box[i]
        m = (idx < 0) & (px >= b[0]) & (px <= b[2]) & (py >= b[1]) & (py <= b[3])
        if not m.any():
            continue
        cand = np.nonzero(m)[0]
        inside = np.zeros(len(cand), bool)
        for rings in areas.parts[i]:
            pin = _ring_contains(rings[0], px[cand], py[cand])
            for h in rings[1:]:
                pin &= ~_ring_contains(h, px[cand], py[cand])
            inside |= pin
        idx[cand[inside]] = i
    return idx


# ---------------------------------------------------------------------------- statistics
def rank(a: np.ndarray) -> np.ndarray:
    order = a.argsort(kind="mergesort")
    r = np.empty(len(a), float)
    r[order] = np.arange(len(a), dtype=float)
    _vals, inv, counts = np.unique(a, return_inverse=True, return_counts=True)
    return (np.bincount(inv, weights=r) / counts)[inv]


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 8 or np.all(a == a[0]) or np.all(b == b[0]):
        return None
    ra, rb = rank(a), rank(b)
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def _km(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lat0 = math.radians(float(np.mean(y))) if len(y) else 0.0
    return x * 111.32 * math.cos(lat0), y * 110.57


def knn(x: np.ndarray, y: np.ndarray, k: int) -> np.ndarray:
    X, Y = _km(x, y)
    n = len(X)
    out = np.empty((n, k), int)
    for s in range(0, n, 512):
        d = (X[s:s + 512, None] - X[None, :]) ** 2 + (Y[s:s + 512, None] - Y[None, :]) ** 2
        d[np.arange(d.shape[0]), np.arange(s, s + d.shape[0])] = np.inf
        out[s:s + 512] = np.argpartition(d, k, axis=1)[:, :k]
        del d
    return out


def moran(x: np.ndarray, y: np.ndarray, z: np.ndarray, k: int = 6, perms: int = PERMUTATIONS, seed: int = 7):
    """Global Moran's I with k-nearest-neighbour weights (row-standardised) and a permutation pseudo p-value."""
    n = len(z)
    if n < 12 or z.std() == 0:
        return None
    k = min(k, n - 1)
    nb = knn(x, y, k)
    zc = (z - z.mean()) / z.std()
    lag = zc[nb].mean(1)
    i_obs = float((zc * lag).sum() / (zc ** 2).sum())
    rng = np.random.default_rng(seed)
    sims = np.empty(perms)
    for j in range(perms):
        zp = rng.permutation(zc)
        sims[j] = (zp * zp[nb].mean(1)).sum() / (zp ** 2).sum()
    p = (np.sum(sims >= i_obs) + 1) / (perms + 1) if i_obs >= 0 else (np.sum(sims <= i_obs) + 1) / (perms + 1)
    return {"I": i_obs, "p": float(p), "k": k, "zc": zc, "lag": lag}


def strength(r: float) -> str:
    a = abs(r)
    return "no clear" if a < 0.1 else "weak" if a < 0.3 else "moderate" if a < 0.5 else "strong"


def fmt(v: float) -> str:
    if v is None or not math.isfinite(v):
        return "n/a"
    a = abs(v)
    if a >= 100000:
        return f"{v:,.0f}"
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 100:
        return f"{v:.0f}"
    if a >= 10:
        return f"{v:.1f}"
    if a >= 1:
        return f"{v:.2f}"
    return f"{v:.3g}"


def _pct(x: float) -> str:
    return f"{x:.1f}%" if x < 10 else f"{x:.0f}%"


def _sample_xy(x, y, n_max=400, *rest):
    n = len(x)
    if n <= n_max:
        return [x, y, *rest]
    step = -(-n // n_max)
    return [a[::step] for a in (x, y, *rest)]


def _scatter(a, b, xl, yl):
    a, b = _sample_xy(np.asarray(a, float), np.asarray(b, float), 400)
    logx = bool(np.all(a > 0) and np.median(a) > 0 and a.max() / max(np.median(a), 1e-12) > 25)
    logy = bool(np.all(b > 0) and np.median(b) > 0 and b.max() / max(np.median(b), 1e-12) > 25)
    return {"type": "scatter", "x": [round(float(v), 4) for v in a], "y": [round(float(v), 4) for v in b],
            "xl": xl[:60], "yl": yl[:60], "logx": logx, "logy": logy}


def _hist(v, label):
    v = v[np.isfinite(v)]
    if len(v) < 5:
        return None
    lo, hi = np.percentile(v, [1, 99])
    if hi <= lo:
        lo, hi = float(v.min()), float(v.max()) or 1.0
    counts, edges = np.histogram(np.clip(v, lo, hi), bins=14, range=(lo, hi))
    return {"type": "hist", "bins": [[round(float(edges[i]), 4), round(float(edges[i + 1]), 4), int(c)] for i, c in enumerate(counts)],
            "label": label[:60], "clipped": bool(v.max() > hi), "max": round(float(v.max()), 4)}


# ---------------------------------------------------------------------------- what the session offers
def _variables(s, cache: dict) -> tuple[list[dict], list[str]]:
    """Ready previews as variables: an area or point layer (with or without a value column) or a yearly series."""
    nodes = {n["id"]: n for n in (s.graph or {}).get("nodes", [])}
    with s.plock:
        items = [(p, dict(s.previews.get(p["ref"]) or {})) for p in s.picks]
    out, skipped, seen = [], [], {}
    for p, r in items:
        if r.get("status") != "ready":
            continue
        factors = [f for f in p.get("factors") or [] if f in nodes]
        for l in r.get("layers") or []:
            if l.get("kind") != "geojson":
                skipped.append(f"{_short(l.get('title') or p.get('title') or '')}: map tiles, so there are no values to read yet")
                continue
            g = cache.get(l["id"])
            if g is None:
                path = session.layer_path(l["id"])
                if not path:
                    continue
                try:
                    g = Geo(l, json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError, KeyError, IndexError, TypeError):
                    continue
                cache[l["id"]] = g
            if g.n == 0 or g.kind == "other":
                continue
            fp = (g.kind, g.n, round(float(g.x[0]), 5), round(float(g.y[0]), 5), g.prop)  # the same file under two cards
            if fp in seen:
                seen[fp]["factors"] = list(dict.fromkeys(seen[fp]["factors"] + factors))
                continue
            v = {"type": g.kind, "geo": g, "ref": p["ref"], "pick": p, "factors": factors, "core": p.get("role") == "core",
                 "valued": g.values is not None and np.isfinite(g.values).sum() >= 3, "id": l["id"]}
            flab = nodes.get(factors[0], {}).get("label", "") if factors else ""
            v["label"], v["short"] = _var_label(g, flab) if v["valued"] else (g.title, g.title)
            v["flabels"] = {f: nodes.get(f, {}).get("label", "") for f in factors}
            seen[fp] = v
            out.append(v)
        sr = r.get("series")
        if sr and len(sr.get("points") or []) >= 3:
            flab = nodes.get(factors[0], {}).get("label", "") if factors else ""
            raw = _clean_label(sr.get("label") or "")
            how = re.search(r"\((sum|mean) over rows\)", raw)
            lab = (flab or raw.split(" (")[0]) + (f" ({how.group(1)} over the table's rows, by year)" if how else " (by year)")
            out.append({"type": "series", "series": sr, "ref": p["ref"], "pick": p, "factors": factors, "label": lab,
                        "short": flab or raw.split(" (")[0], "core": p.get("role") == "core", "valued": True, "id": "series:" + p["ref"]})
    return out, list(dict.fromkeys(skipped))


def _rank_var(v: dict, factor: str, nodes: dict) -> tuple:
    words = set(re.findall(r"[a-z]{4,}", (nodes.get(factor, {}).get("label", "") + " " + nodes.get(factor, {}).get("observable", "")).lower()))
    label = (v.get("label") or "") .lower() + " " + (v["geo"].title.lower() if v["type"] != "series" else "")
    hit = sum(w[:5] in label for w in words)
    kind = {"area": 0 if v["valued"] else 4, "point": 1 if v["valued"] else 2, "series": 3}[v["type"]]
    return (kind, not v["core"], -hit)


def _paths(g: dict, max_len: int = 3) -> dict[tuple[str, str], list[str]]:
    """Shortest directed path (up to max_len links) between every ordered pair of factors."""
    nxt: dict[str, list[str]] = {}
    for e in g.get("edges", []):
        nxt.setdefault(e["source"], []).append(e["target"])
    out = {}
    for a in [n["id"] for n in g.get("nodes", [])]:
        frontier, seen = [[a]], {a}
        for _ in range(max_len):
            new = []
            for path in frontier:
                for b in nxt.get(path[-1], []):
                    if b not in seen:
                        seen.add(b)
                        out[(a, b)] = path + [b]
                        new.append(path + [b])
            frontier = new
    return out


def plan(s, variables: list[dict]) -> list[dict]:
    """The exploration steps, in the order the graph suggests."""
    g = s.graph or {}
    nodes = {n["id"]: n for n in g.get("nodes", [])}
    outcome = g.get("outcome")
    by_factor: dict[str, list[dict]] = {}
    for v in variables:
        for f in v["factors"]:
            by_factor.setdefault(f, []).append(v)
    for f in by_factor:
        by_factor[f].sort(key=lambda v: _rank_var(v, f, nodes))
    measurable = {f: [v for v in vs if v["valued"] or v["type"] == "point"] for f, vs in by_factor.items()}
    measurable = {f: vs for f, vs in measurable.items() if vs}
    steps, done_vars = [], set()

    def profile(v, f):
        if v["id"] in done_vars or sum(st["kind"] == "profile" for st in steps) >= MAX_PROFILES:
            return
        done_vars.add(v["id"])
        what = f"the trend in {_vname(v)}" if v["type"] == "series" else _vname(v)
        steps.append({"id": "p:" + v["id"], "kind": "profile", "var": v, "factor": f,
                      "title": f"Look at {what}" + ("" if (nodes.get(f, {}).get("label", f) or "").lower() == (v.get("short") or "").lower()
                                                     else f" ({nodes.get(f, {}).get('label', f)})")})

    for v in measurable.get(outcome, [])[:2]:
        profile(v, outcome)
    paths = _paths(g)
    pairs, seen_pairs = [], set()
    order = sorted(paths.items(), key=lambda kv: (kv[0][1] != outcome, len(kv[1]),
                                                 not nodes.get(kv[0][0], {}).get("core"), kv[0][0]))
    for (a, b), path in order:
        if a not in measurable or b not in measurable:
            continue
        if len(path) > 2 and b != outcome:
            continue  # indirect links only toward the outcome
        va, vb = measurable[a][0], measurable[b][0]
        if a in vb["factors"] and b in va["factors"]:
            continue  # the same datasets measure both ends: nothing to compare across
        if va["id"] == vb["id"] or not _joinable(va, vb):
            alt = next(((x, y) for x in measurable[a] for y in measurable[b] if x["id"] != y["id"] and _joinable(x, y)), None)
            if not alt:
                continue
            va, vb = alt
        key = tuple(sorted([va["id"], vb["id"]]))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        pairs.append((a, b, path, va, vb))
    outs = measurable.get(outcome, [])
    if len(outs) >= 2 and _joinable(outs[0], outs[1]):  # two layers of the outcome (two periods, two sources)
        seen_pairs.add(tuple(sorted([outs[0]["id"], outs[1]["id"]])))
        pairs.insert(0, (outcome, outcome, [outcome], outs[0], outs[1]))
    zone_ids = {v["id"] for vs in by_factor.values() for v in vs
                if v["type"] == "area" and not v["valued"] and 2 <= v["geo"].n <= 3000 and v["geo"].units() == "areas"}
    for e in g.get("edges", []):  # a link from a factor drawn as zones (no values): inside versus outside
        za = next((v for v in by_factor.get(e["source"], []) if v["id"] in zone_ids), None)
        vb = measurable.get(e["target"], [None])[0]
        if za and vb and vb["valued"] and vb["type"] in ("point", "area") and za["id"] != vb["id"]:
            key = tuple(sorted([za["id"], vb["id"]]))
            if key not in seen_pairs:
                seen_pairs.add(key)
                pairs.append((e["source"], e["target"], [e["source"], e["target"]], za, vb))
    for a, b, path, va, vb in pairs[:MAX_RELATIONS]:
        via = [nodes.get(x, {}).get("label", x) for x in path[1:-1]]
        steps.append({"id": f"r:{va['id']}|{vb['id']}", "kind": "relate", "a": va, "b": vb, "fa": a, "fb": b, "via": via,
                      "same": a == b,
                      "title": f"Compare {_vname(va)} with {_vname(vb)}" + (f" (through {via[0]})" if via else "")})
    for f in sorted(measurable, key=lambda f: (not nodes.get(f, {}).get("core"), f)):
        if f != outcome and nodes.get(f, {}).get("core"):
            profile(measurable[f][0], f)
    for v in variables:  # other valued layers from core picks (an index with several measures)
        if v["core"] and (v["valued"] or v["type"] == "point") and v["factors"]:
            profile(v, v["factors"][0])
    rel = [st for st in steps if st["kind"] == "relate"]
    prof = [st for st in steps if st["kind"] == "profile"]
    first = [st for st in prof if st["factor"] == outcome]
    return first + rel + [st for st in prof if st not in first]


def _vname(v: dict) -> str:
    return _lc(v.get("short") or "") if v.get("valued") and v["type"] != "series" or v["type"] == "series" else (v.get("short") or "")


def _joinable(a: dict, b: dict) -> bool:
    ta, tb = a["type"], b["type"]
    if "series" in (ta, tb):
        return ta == tb == "series"
    if ta == tb == "area":
        return a["valued"] and b["valued"]
    if ta == "area" or tb == "area":
        area = a if ta == "area" else b
        return area["valued"]
    return True  # two point layers: a shared grid


# ---------------------------------------------------------------------------- one step
def _place(lon: float, lat: float) -> str | None:
    if not _names_on():
        return None
    try:
        return sources.reverse(lon, lat)
    except Exception:
        return None


def display(g: Geo, i: int) -> str:
    """A name people know: the layer's own name, or the neighbourhood for census units named only by number."""
    n = g.name(i)
    if n and not UNIT_NAME.match(n):
        return n
    place = _place(float(g.x[i]), float(g.y[i]))
    if place and n:
        return f"{place} ({re.sub(r',.*$', '', n)})"
    return place or n or f"{g.y[i]:.3f}, {g.x[i]:.3f}"


def _var_label(g: Geo, factor_label: str) -> tuple[str, str]:
    """(full label, short label for headlines) of a layer's value column."""
    lab = g.label or ""
    lab = re.sub(r"\s*\((?=[^)]*[._])[A-Za-z0-9_.]+\)$", "", lab)           # column codes such as (AIR.new)
    lab = lab.replace("Micrograms/cubic meter", "µg/m³").replace("(LC)", "").strip()
    lab = re.sub(r",?\s*\d{4}(-\d{2})?\s+to\s+\d{4}(-\d{2})?", "", lab)    # the study window is shown elsewhere
    if re.fullmatch(r"\d{4}|[A-Za-z0-9_.]{1,4}", lab or "x") and factor_label:
        lab = f"{factor_label} ({lab})" if lab else factor_label
    short = re.sub(r"\s*\([^)]*\)$", "", lab).split(",")[0].strip() or lab
    return lab, short


def _focus(g: Geo, idx, layers, label_vals=None) -> dict:
    idx = [int(i) for i in idx][:8]
    pts = [[round(float(g.x[i]), 5), round(float(g.y[i]), 5), (display(g, i) if g.name(i) and UNIT_NAME.match(g.name(i)) else g.name(i)) or "",
            label_vals[j] if label_vals else ""] for j, i in enumerate(idx)]
    if not pts:
        return {"layers": layers}
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    pad = 0.02 if g.kind == "point" else 0.0
    if g.kind == "area":
        bx = g.box[idx]
        return {"layers": layers, "points": pts, "bbox": [float(bx[:, 0].min()), float(bx[:, 1].min()), float(bx[:, 2].max()), float(bx[:, 3].max())]}
    return {"layers": layers, "points": pts, "bbox": [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]}


def _in_box(g: Geo, box) -> np.ndarray:
    if not box:
        return np.ones(g.n, bool)
    w, s_, e, n = box
    if g.kind == "point":
        return (g.x >= w) & (g.x <= e) & (g.y >= s_) & (g.y <= n)
    return (g.box[:, 2] >= w) & (g.box[:, 0] <= e) & (g.box[:, 3] >= s_) & (g.box[:, 1] <= n)


def profile_area(v: dict, s) -> dict | None:
    g: Geo = v["geo"]
    z = g.values
    ok = np.isfinite(z)
    if ok.sum() < 3:
        return None
    label, short = v["label"], v["short"]
    units = g.units()
    sb = (s.scope or {}).get("bbox")
    scope_note, focus_idx, head, parts = "", [], "", []
    if sb and len(ok) >= 20:
        ext = [float(g.box[:, 0].min()), float(g.box[:, 1].min()), float(g.box[:, 2].max()), float(g.box[:, 3].max())] if g.kind == "area" \
            else [float(g.x.min()), float(g.y.min()), float(g.x.max()), float(g.y.max())]
        ratio = max(ext[2] - ext[0], 1e-9) * max(ext[3] - ext[1], 1e-9) / max((sb[2] - sb[0]) * (sb[3] - sb[1]), 1e-9)
        centre_in = (g.x >= sb[0]) & (g.x <= sb[2]) & (g.y >= sb[1]) & (g.y <= sb[3]) & ok
        if ratio > 20 and g.kind == "area":  # a world or national layer: where does the study area stand?
            at = locate(np.array([(sb[0] + sb[2]) / 2]), np.array([(sb[1] + sb[3]) / 2]), g)[0]
            if at >= 0 and ok[at]:
                vals_all = z[ok]
                rank_hi = int((vals_all > z[at]).sum()) + 1
                head = f"{display(g, at)} ranks {rank_hi} of {len(vals_all)} {units} on {_lc(short)} ({fmt(z[at])})"
                parts.append(f"{display(g, at)}: {fmt(z[at])}, higher than {_pct(100 * float((vals_all < z[at]).sum()) / len(vals_all))} of {units} "
                             f"(rank 1 is the highest value).")
                focus_idx = [int(at)]
        elif 5 <= centre_in.sum() < 0.8 * ok.sum():  # neighbours drawn for context: describe the study area itself
            scope_note = f" in the study area ({int(centre_in.sum())} of the {int(ok.sum())} drawn)"
            ok = centre_in
    vals = z[ok]
    idx_ok = np.nonzero(ok)[0]
    q0, q1, q2, q3, q4 = np.percentile(vals, [0, 25, 50, 75, 100])
    order = idx_ok[np.argsort(-vals)]
    top, low = list(order[:3]), list(order[::-1][:2])
    parts.append(f"{label}{scope_note}: median {fmt(q2)}, middle half {fmt(q1)} to {fmt(q3)}, range {fmt(q0)} to {fmt(q4)} "
                 f"({len(vals):,} {units}).")
    parts.append("Highest: " + ", ".join(f"{display(g, i)} ({fmt(z[i])})" for i in top) + ". Lowest: "
                 + ", ".join(f"{display(g, i)} ({fmt(z[i])})" for i in low) + ".")
    mx, my, mv = g.x[ok], g.y[ok], vals
    if len(mv) > 5000:  # an even sample keeps the neighbour search small
        step = -(-len(mv) // 5000)
        mx, my, mv = mx[::step], my[::step], mv[::step]
    m = moran(mx, my, mv)
    if m:
        if m["I"] >= 0.1 and m["p"] <= 0.05:
            how = "strongly" if m["I"] >= 0.4 else "moderately" if m["I"] >= 0.2 else "slightly"
            hh = np.nonzero((m["zc"] > 0.5) & (m["lag"] > 0.5))[0]
            hh = idx_ok[hh[np.argsort(-m["zc"][hh])]] if len(mv) == len(vals) else np.array([], int)
            names = list(dict.fromkeys(display(g, int(i)) for i in hh[:4]))[:3]
            parts.append(f"Neighbouring {units} are {how} alike (Moran's I {m['I']:.2f}, pseudo p {m['p']:.3f}; "
                         f"{m['k']} nearest neighbours, {PERMUTATIONS} shuffles)" + (f"; high values group around {', '.join(names)}." if names else "."))
            if not focus_idx:
                focus_idx = list(hh[:6]) or top
            if m["I"] >= 0.2:
                head = head or f"{short}{' in the study area' if scope_note else ''}: high values sit together, near {names[0]}" if names else \
                    head or f"{short}{' in the study area' if scope_note else ''}: high values sit together"
        else:
            parts.append(f"No clear clustering: neighbouring {units} are no more alike than chance (Moran's I {m['I']:.2f}, pseudo p {m['p']:.2f}).")
    head = head or f"{short}{' in the study area' if scope_note else ''}: from {fmt(q0)} to {fmt(q4)} across {len(vals):,} {units}"
    if bool(np.all(np.mod(vals, 1) == 0)) and q4 > 20 and COUNT_WORDS.search(label + " " + g.title):
        parts.append("These are counts, not rates, so larger places can have more simply because they are larger.")
    return {"kind": "profile", "headline": head, "detail": " ".join(parts), "chart": _hist(vals, short),
            "focus": _focus(g, focus_idx or top, [g.id]), "numbers": {"median": q2, "q1": q1, "q3": q3, "min": q0, "max": q4,
                                                                       "n": int(len(vals)), "moran_I": m["I"] if m else None,
                                                                       "moran_p": m["p"] if m else None}}


def _lc(t: str) -> str:
    """Lower-case the first letter of a sentence-case label ("Median household income"), never of a name or acronym."""
    return t[:1].lower() + t[1:] if t and re.match(r"^[A-Z][a-z]+(\s[a-z(]|$)", t) else t


def _dates(g: Geo) -> tuple[str | None, np.ndarray | None]:
    """A date or year column of a point layer: decimal years."""
    if not g.props:
        return None, None
    best = (0, None, None)
    for k in g.props[0].keys():
        if not DATE_FIELD.search(k) or ID_FIELD.search(k):
            continue
        out = np.full(g.n, np.nan)
        for i, p in enumerate(g.props):
            v = p.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if 1e11 < v < 4.2e12:  # epoch milliseconds (ArcGIS)
                    t = time.gmtime(v / 1000)
                    out[i] = t.tm_year + (t.tm_mon - 1) / 12
                elif 1400 <= v <= 2100:
                    out[i] = float(v)
            elif isinstance(v, str):
                m_ = ISO_DATE.match(v.strip())
                if m_:
                    out[i] = int(m_.group(1)) + ((int(m_.group(3)) - 1) / 12 if m_.group(3) else 0)
        good = np.isfinite(out)
        score = good.sum() + (len(set(out[good].astype(int))) if good.any() else 0) * 3 + ("date" in k.lower()) * g.n
        if good.sum() >= 0.6 * g.n and score > best[0]:
            best = (score, k, out)
    return best[1], best[2]


def _time_part(g: Geo, mask: np.ndarray, sampled: bool) -> tuple[str, dict | None, str | None]:
    key, t = _dates(g)
    if key is None:
        return "", None, None
    t = t[mask & np.isfinite(t)]
    if len(t) < 10:
        return "", None, None
    span = t.max() - t.min()
    if span < 2:
        bins = np.floor(t * 12) / 12
        lab = lambda b: f"{int(b)}-{int(round((b % 1) * 12)) + 1:02d}"
    elif span <= 45:
        bins = np.floor(t)
        lab = lambda b: str(int(b))
    else:
        bins = np.floor(t / 10) * 10
        lab = lambda b: f"{int(b)}s"
    c = Counter(bins.tolist())
    items = sorted(c.items())
    chart = {"type": "bars", "items": [[lab(b), n] for b, n in items][-40:], "label": f"Records by {'month' if span < 2 else 'year' if span <= 45 else 'decade'}"}
    peak_b, peak_n = max(items, key=lambda kv: kv[1])
    share = peak_n / len(t)
    unit = "month" if span < 2 else "year" if span <= 45 else "decade"
    uneven = None
    if sampled and len(items) >= 4 and share > 0.5:
        uneven = (f"The preview is not an even sample over time: {_pct(share * 100)} of its records are from {lab(peak_b)} "
                  f"({len(t):,} records, {lab(items[0][0])} to {lab(items[-1][0])}). Counts by year here describe the preview, not all records")
        return "", chart, uneven
    return (f"Records run from {lab(items[0][0])} to {lab(items[-1][0])}; the busiest {unit} is {lab(peak_b)} "
            f"({_pct(share * 100)} of them).", chart, None)


def _hotspots(g: Geo, mask: np.ndarray, sb) -> tuple[str, list[int], str]:
    """Where points concentrate: share of grid cells holding half of them, and names for the busiest cells."""
    x, y = g.x[mask], g.y[mask]
    n = len(x)
    if n < 10:
        return "", [], ""
    key = np.round(x, 5) * 1e6 + np.round(y, 5)
    distinct = len(np.unique(key))
    if distinct <= 60 and n >= 3 * distinct:  # stacked records at a few places (one point per treasury, station, site)
        uniq, inv, counts = np.unique(key, return_inverse=True, return_counts=True)
        order = np.argsort(-counts)[:5]
        idx_all = np.nonzero(mask)[0]
        reps = [int(idx_all[np.nonzero(inv == o)[0][0]]) for o in order]
        names = [display(g, i) for i in reps]
        text = (f"The {n:,} records sit at {distinct} places; the most: " +
                ", ".join(f"{nm} ({int(counts[o]):,})" for nm, o in zip(names, order)) + ".")
        return text, reps, f"sit at {distinct} places, most at {names[0]}"
    X, Y = _km(x, y)
    ext_w, ext_h = max(X.max() - X.min(), 0.5), max(Y.max() - Y.min(), 0.5)
    raw = math.sqrt(ext_w * ext_h / 400)
    cell = min((c for c in (0.25, 0.5, 1, 2, 5, 10, 25, 50, 100, 250) if c >= raw), default=250)
    gx = np.floor((X - X.min()) / cell).astype(int)
    gy = np.floor((Y - Y.min()) / cell).astype(int)
    ncols, nrows = gx.max() + 1, gy.max() + 1
    cid = gy * ncols + gx
    counts = np.bincount(cid, minlength=ncols * nrows)
    srt = np.sort(counts)[::-1]
    k50 = int(np.searchsorted(np.cumsum(srt), 0.5 * n) + 1)
    share = 100 * k50 / (ncols * nrows)
    top_cells = np.argsort(-counts)[:3]
    idx_all = np.nonzero(mask)[0]
    reps, names = [], []
    for c in top_cells:
        members = np.nonzero(cid == c)[0]
        if not len(members):
            continue
        j = members[np.argmin((X[members] - X[members].mean()) ** 2 + (Y[members] - Y[members].mean()) ** 2)]
        reps.append(int(idx_all[j]))
        nm = _place(float(x[j]), float(y[j])) or g.name(int(idx_all[j]))  # the neighbourhood, else the layer's own name
        if nm and nm not in names:
            names.append(nm)
    cl = f"{cell:g} km"
    text = (f"Half of them fall in {k50} of {ncols * nrows} grid cells of {cl} ({_pct(share)} of the area the points span)"
            + (f"; the busiest cells are around {', '.join(names)}." if names else "."))
    around = f", around {' and '.join(names[:2])}" if names else ""
    return text, reps, f"half of them sit in {_pct(share)} of the area they span{around}"


def _question_subset(g: Geo, s) -> tuple[str, list[str]] | None:
    """The categories of a point layer that match the outcome (the chat model reads the category list)."""
    if not _model_on() or not g.props:
        return None  # unknown: no model to read the categories
    outcome = next((n for n in (s.graph or {}).get("nodes", []) if n["id"] == (s.graph or {}).get("outcome")), {})
    fields = []
    for k in g.props[0].keys():
        if ID_FIELD.search(k) or DATE_FIELD.search(k):
            continue
        c = Counter(str(p.get(k)).strip() for p in g.props if isinstance(p.get(k), str) and p.get(k).strip())
        if 3 <= len(c) <= 2000 and sum(c.values()) >= 0.5 * g.n and any(re.search(r"[a-z]{3}", w, re.I) for w in list(c)[:20]):
            fields.append((k, c))
    if not fields:
        return None
    fields.sort(key=lambda kc: -len(kc[1]))
    menu = {k: [v for v, _n in c.most_common(150)] for k, c in fields[:4]}
    prompt = ("A researcher's question: " + (s.graph or {}).get("question", "") + "\nThe outcome they study: "
              + outcome.get("label", "") + " (" + outcome.get("observable", "") + ")\n"
              "A point layer has these text columns with their most common values. If one column has values that pick out "
              "the records the outcome is about (for example the charges that are street vending), return that column and "
              "exactly those values. Return an empty column when no values clearly match.\n" + json.dumps(menu, ensure_ascii=False)[:24000])
    schema = {"type": "object", "additionalProperties": False, "required": ["column", "values", "why"],
              "properties": {"column": {"type": "string"}, "values": {"type": "array", "items": {"type": "string"}},
                             "why": {"type": "string"}}}
    try:
        j = _model_json(s, prompt, schema, "subset", 1500)
    except Exception:
        return None
    col, vals = (j or {}).get("column") or "", [v for v in (j or {}).get("values") or [] if v in set(menu.get((j or {}).get("column"), []))]
    if col not in menu or not vals:
        return "", [], ""  # the model read the categories and none picks out the outcome
    return col, vals, str((j or {}).get("why") or "")[:200]


def profile_points(v: dict, s) -> dict | None:
    g: Geo = v["geo"]
    sb = (s.scope or {}).get("bbox")
    mask = _in_box(g, sb)
    if mask.sum() < 3:
        mask = np.ones(g.n, bool)
    note = (s.previews.get(v["ref"]) or {}).get("note") or ""
    sampled = "sample" in note.lower() or "first" in note.lower()
    n = int(mask.sum())
    if v["valued"]:
        return profile_area(v, s)
    parts, chart, focus_idx, sub_layer = [], None, [], None
    subset = _question_subset(g, s)
    head = ""
    outcome = next((x["label"] for x in (s.graph or {}).get("nodes", []) if x["id"] == (s.graph or {}).get("outcome")), "the outcome")
    if subset and not subset[0]:
        parts.append(f"No category in this preview picks out {_lc(outcome)}, so the patterns below cover all its records.")
        subset = None
    if subset:
        col, vals, why = subset
        sel = np.array([str(p.get(col)).strip() in set(vals) for p in g.props]) & mask
        k = int(sel.sum())
        shown = ", ".join(vals[:4]) + (f" and {len(vals) - 4} more" if len(vals) > 4 else "")
        parts.append(f"{k:,} of the {n:,} records ({_pct(100 * k / max(n, 1))}) match the question by {col}: {shown}"
                     + (f" (chosen by the AI model: {why.rstrip('.')})." if why else "."))
        if k >= 10:
            text, reps, h = _hotspots(g, sel, sb)
            if text:
                parts.append("Those records: " + text[0].lower() + text[1:])
                focus_idx = reps
            head = f"{k:,} of {n:,} preview records match the question" + (f"; {h}" if h else "")
            g.subset = (col, vals, sel)
            sub_layer = _subset_layer(s, g, sel, f"{g.title}: {_lc(outcome)} only")
        else:
            head = f"Only {k} of {n:,} preview records match the question ({shown})"
    text, reps, h = _hotspots(g, mask, sb)
    if text:
        parts.append(("All records: " if subset else "") + text)
        focus_idx = focus_idx or reps
        head = head or (f"The {n:,} records of {g.title} {h}" if h.startswith("sit at") else f"{n:,} records of {g.title}: {h}")
    tpart, chart, uneven = _time_part(g, mask, sampled)
    if uneven:
        parts.append(uneven + ".")
    elif tpart:
        parts.append(tpart)
    if sampled:
        parts.append(f"From the preview: {note}.")
    if not head:
        head = f"{n:,} records of {g.title} in the study area"
    focus = _focus(g, focus_idx, [g.id])
    if sub_layer:  # "Show on map" draws only the matching records, in their own colour
        focus.update(layers=[sub_layer["id"]], add=[sub_layer], hide=[g.id])
    return {"kind": "profile", "headline": head, "detail": " ".join(parts), "chart": chart,
            "focus": focus, "numbers": {"n": n}, "coverage_warning": bool(uneven)}


def _subset_layer(s, g: Geo, sel: np.ndarray, title: str) -> dict | None:
    """The records that match the question as their own layer (kept for this session only)."""
    from . import downloads
    idx = np.nonzero(sel)[0][:20000]
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(float(g.x[i]), 5), round(float(g.y[i]), 5)]},
              "properties": g.props[i]} for i in idx]
    try:
        d = session.save_layer({"type": "FeatureCollection", "features": feats}, title[:100], "Y-finder exploration of the preview", style="preview")
        downloads.track(s, [d], title)
    except OSError:
        return None
    d["paint"] = {"fill": "#e8590c", "stroke": "#ffffff", "radius": 8, "label": title[:80]}
    return d


def profile_series(v: dict, s) -> dict | None:
    pts = sorted([(float(a), float(b)) for a, b in v["series"]["points"] if _finite(_num(a), _num(b))])
    if len(pts) < 3:
        return None
    xs, ys = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
    label, short = v["label"], v["short"]
    r = spearman(xs, ys) if len(xs) >= 8 else (float(np.corrcoef(rank(xs), rank(ys))[0, 1]) if ys.std() > 0 else 0.0)
    first, last = ys[0], ys[-1]
    ch = (last - first) / abs(first) * 100 if first else None
    i_peak = int(np.argmax(ys))
    y0, y1, peak = int(xs[0]), int(xs[-1]), int(xs[i_peak])
    if 0 < i_peak < len(ys) - 1 and ys[i_peak] > 1.2 * max(first, last):
        head = f"{short} peaked in {peak} ({fmt(ys[i_peak])}) and stood at {fmt(last)} in {y1}"
    elif ch is not None and abs(ch) >= 10 and (r is None or r * ch > 0):
        head = f"{short} {'rose' if ch > 0 else 'fell'} {abs(ch):.0f}% from {y0} to {y1}"
    else:
        head = f"{short}: no steady trend from {y0} to {y1}"
    detail = (f"{label}: {fmt(first)} in {y0}, {fmt(last)} in {y1}, highest in {peak} ({fmt(ys[i_peak])}), "
              f"lowest in {int(xs[int(np.argmin(ys))])} ({fmt(ys.min())})." + (f" Rank correlation with year {r:+.2f} over {len(xs)} years." if r is not None else ""))
    return {"kind": "profile", "headline": head, "detail": detail,
            "chart": {"type": "line", "points": [[int(a), round(b, 4)] for a, b in pts], "label": short[:60]},
            "focus": {}, "numbers": {"first": first, "last": last, "change_pct": ch, "rho_year": r}}


def _measure(v: dict, s, subsets: bool = True) -> tuple[str, np.ndarray | None, np.ndarray | None]:
    """A variable's short label and, for points, the records used (the question's subset when there is a good one)."""
    g = v.get("geo")
    if v["type"] == "point" and not v["valued"]:
        sub = g.subset
        if subsets and sub and sub[2].sum() >= 30:
            return f"matching records of {g.title} per km²", g.x[sub[2]], g.y[sub[2]]
        return f"{g.title} per km²", g.x, g.y
    return _vname(v), None, None


def relate(st: dict, s) -> dict | None:
    a, b = st["a"], st["b"]
    if a["type"] == b["type"] == "series":
        pa = {int(x): float(y) for x, y in a["series"]["points"]}
        pb = {int(x): float(y) for x, y in b["series"]["points"]}
        yrs = sorted(set(pa) & set(pb))
        if len(yrs) < 6:
            return {"skip": f"only {len(yrs)} years in common"}
        xa, xb, unit, how = np.array([pa[y] for y in yrs]), np.array([pb[y] for y in yrs]), "years", f"{yrs[0]} to {yrs[-1]}, matched by year"
        la, lb = _vname(a), _vname(b)
    else:
        out = _spatial_pairs(a, b, s, bool(st.get("same")))
        if out is None or isinstance(out, str):
            return {"skip": out or "the layers do not overlap"}
        if out.get("groups"):
            return _zones_finding(st, out, s)
        xa, xb, unit, how, la, lb = out["a"], out["b"], out["unit"], out["how"], out["la"], out["lb"]
    r = spearman(xa, xb)
    if r is None:
        return {"skip": "too few matched places or no variation"}
    na, nb = (st["fa"], st["fb"])
    nodes = {n["id"]: n["label"] for n in (s.graph or {}).get("nodes", [])}
    if abs(r) < 0.1:
        head = f"No clear link between {la} and {lb} across {len(xa):,} {unit}"
    else:
        head = f"Where {la} is higher, {lb} tends to be {'higher' if r > 0 else 'lower'}"
    via = f" through {', '.join(st['via'])}" if st.get("via") else ""
    if st.get("same"):
        head = (f"{la[:1].upper() + la[1:]} and {lb} {'follow the same places' if r >= 0.5 else 'partly share places' if r >= 0.2 else 'sit in different places'}")
        link = f"Both layers measure {nodes.get(na, na)}, so this shows how far the pattern held between them."
    else:
        link = f"Graph link{' (indirect)' if via else ''}: {nodes.get(na, na)} → {nodes.get(nb, nb)}{via}."
    detail = f"Spearman rank correlation {r:+.2f} ({strength(r)}) across {len(xa):,} {unit}; {how}. {link}"
    proxy = [_proxy(st[k], st[f]) for k, f in (("a", "fa"), ("b", "fb"))]
    detail += "".join(f" {x}" for x in proxy if x)
    if st.get("via"):
        detail += " An indirect link can hide or reverse along the way, so read it as a pattern to check."
    return {"kind": "relate", "headline": head, "detail": detail, "chart": _scatter(xa, xb, la, lb),
            "edge": None if st.get("same") else [na, nb], "rho": round(r, 2), "n": int(len(xa)),
            "focus": {"layers": [x for x in (a.get("id"), b.get("id")) if x and not x.startswith("series:")]},
            "numbers": {"rho": r, "n": int(len(xa))}}


def _spatial_pairs(a: dict, b: dict, s, same: bool = False):
    ga, gb = a["geo"], b["geo"]
    both = not same or all(v["type"] == "point" and v["geo"].subset and v["geo"].subset[2].sum() >= 30 for v in (a, b))
    la, xa_pts, ya_pts = _measure(a, s, both)
    lb, xb_pts, yb_pts = _measure(b, s, both)
    sb = (s.scope or {}).get("bbox")
    if a["type"] == "area" and not a["valued"] or b["type"] == "area" and not b["valued"]:
        zones, other = (a, b) if a["type"] == "area" and not a["valued"] else (b, a)
        return _zone_groups(zones, other, s)
    if a["type"] == b["type"] == "area":
        fine, coarse = (a, b) if ga.n >= gb.n else (b, a)
        gf, gc = fine["geo"], coarse["geo"]
        key = _shared_key(gf, gc)
        if key:
            kf, kc = key
            look = {str(p.get(kc)): i for i, p in enumerate(gc.props)}
            pairs = [(i, look[str(p.get(kf))]) for i, p in enumerate(gf.props) if str(p.get(kf)) in look]
            fi, ci = np.array([p[0] for p in pairs], int), np.array([p[1] for p in pairs], int)
            vf, vc = gf.values[fi], gc.values[ci]
            how, unit = f"matched by {kf}", gc.units()
        else:
            at = locate(gf.x, gf.y, gc)
            ok = (at >= 0) & np.isfinite(gf.values)
            if ok.sum() < 8:
                return "the layers barely overlap"
            sums = np.bincount(at[ok], weights=gf.values[ok], minlength=gc.n)
            cnt = np.bincount(at[ok], minlength=gc.n)
            has = (cnt > 0) & np.isfinite(gc.values)
            vf, vc = (sums[has] / cnt[has]), gc.values[has]
            unit = gc.units()
            how = (f"{gf.units()} averaged into each of the {int(has.sum())} {unit} that holds their centres" if gf.n > gc.n * 1.3
                   else "matched through their centres")
        ok2 = np.isfinite(vf) & np.isfinite(vc)
        vf, vc = vf[ok2], vc[ok2]
        if fine is a:
            return {"a": vf, "b": vc, "unit": unit, "how": how, "la": la, "lb": lb}
        return {"a": vc, "b": vf, "unit": unit, "how": how, "la": la, "lb": lb}
    if "area" in (a["type"], b["type"]):
        area, pts = (a, b) if a["type"] == "area" else (b, a)
        g_area, g_pts = area["geo"], pts["geo"]
        if pts["valued"]:
            ok = np.isfinite(g_pts.values) & _in_box(g_pts, sb)
            px, py, pv = g_pts.x[ok], g_pts.y[ok], g_pts.values[ok]
            at = locate(px, py, g_area)
            m = (at >= 0) & np.isfinite(g_area.values[np.clip(at, 0, None)])
            va, vp = g_area.values[at[m]], pv[m]
            unit, how = g_pts.units(), f"each {g_pts.units()[:-1]} takes the value of the {g_area.units()[:-1]} it sits in"
        else:
            px, py = (xa_pts, ya_pts) if pts is a else (xb_pts, yb_pts)
            px, py = _thin(px, py)
            at = locate(px, py, g_area)
            inside = at >= 0
            if inside.sum() < 10:
                return "few points fall inside the areas"
            cnt = np.bincount(at[inside], minlength=g_area.n).astype(float)
            ok = np.isfinite(g_area.values) & (g_area.area_km2 > 0)
            ok &= _in_box(g_area, sb)
            vp, va = cnt[ok] / g_area.area_km2[ok], g_area.values[ok]
            unit = g_area.units()
            how = f"{int(inside.sum()):,} points counted in each of {int(ok.sum()):,} {unit} and divided by its area"
        return ({"a": va, "b": vp} if area is a else {"a": vp, "b": va}) | {"unit": unit, "how": how, "la": la, "lb": lb}
    # two point layers: counts (or mean values) on a shared grid over the study area
    box = sb or [min(ga.x.min(), gb.x.min()), min(ga.y.min(), gb.y.min()), max(ga.x.max(), gb.x.max()), max(ga.y.max(), gb.y.max())]
    W, H = _km(np.array([box[0], box[2]]), np.array([box[1], box[3]]))
    cell = max(0.25, math.sqrt(max((W[1] - W[0]) * (H[1] - H[0]), 1) / 400))
    cols = []
    for v, xs, ys in ((a, xa_pts, ya_pts), (b, xb_pts, yb_pts)):
        g = v["geo"]
        xs, ys = (g.x, g.y) if xs is None else (xs, ys)
        X, Y = _km(np.r_[xs, box[0]], np.r_[ys, box[1]])
        gx = np.floor((X[:-1] - X[-1]) / cell).astype(int)
        gy = np.floor((Y[:-1] - Y[-1]) / cell).astype(int)
        nx = int(math.ceil((W[1] - W[0]) / cell)) + 1
        inb = (gx >= 0) & (gy >= 0) & (gx < nx) & (gy < nx * 4)
        cid = gy[inb] * nx + gx[inb]
        if v["valued"]:
            vals = g.values[inb] if xs is g.x else np.full(inb.sum(), np.nan)
            okv = np.isfinite(vals)
            sm = np.bincount(cid[okv], weights=vals[okv], minlength=nx * nx * 4)
            ct = np.bincount(cid[okv], minlength=nx * nx * 4)
            with np.errstate(invalid="ignore", divide="ignore"):
                cols.append(np.where(ct > 0, sm / np.maximum(ct, 1), np.nan))
        else:
            cols.append(np.bincount(cid, minlength=nx * nx * 4).astype(float))
    ca, cb = cols
    ok = np.isfinite(ca) & np.isfinite(cb) & ((ca > 0) | (cb > 0))
    if ok.sum() < 8:
        return "the two point layers barely overlap"
    return {"a": ca[ok], "b": cb[ok], "unit": f"grid cells of {cell:.2g} km", "how": "both layers counted on the same grid",
            "la": la.replace(" per km²", " per cell"), "lb": lb.replace(" per km²", " per cell")}


def _proxy(v: dict, factor: str) -> str:
    """Say so when a factor is measured with a stand-in (income for population shares, for example)."""
    lab = (v.get("label") or "").lower()
    flab = (v.get("flabels") or {}).get(factor, "")
    words = [w for w in re.findall(r"[a-z]{4,}", flab.lower()) if w not in STOP]
    if not flab or not words or any(w[:5] in lab or w[:5] in (v["geo"].title.lower() if v.get("geo") else "") for w in words):
        return ""
    return f"{flab} is stood in for by {_lc(v.get('short') or '')} here; check that it is a fair stand-in."


def _thin(x, y):
    if x is None or len(x) <= MAX_JOIN_POINTS:
        return x, y
    step = -(-len(x) // MAX_JOIN_POINTS)
    return x[::step], y[::step]


def _shared_key(gf: Geo, gc: Geo) -> tuple[str, str] | None:
    if not gf.props or not gc.props:
        return None
    for kf in gf.props[0]:
        if not re.search(r"geoid|fips|key|iso|code|gid", kf, re.I):
            continue
        vf = {str(p.get(kf)) for p in gf.props}
        for kc in gc.props[0]:
            if re.search(r"geoid|fips|key|iso|code|gid", kc, re.I):
                vc = {str(p.get(kc)) for p in gc.props}
                if len(vf & vc) >= 0.8 * min(len(vf), len(vc)) and len(vf & vc) >= 8:
                    return kf, kc
    return None


def _zone_groups(zones: dict, other: dict, s):
    gz, go = zones["geo"], other["geo"]
    if not other["valued"]:
        return "the other layer has no values to compare"
    ok = np.isfinite(go.values)
    at = locate(go.x[ok], go.y[ok], gz)
    vals = go.values[ok]
    ins, outs = vals[at >= 0], vals[at < 0]
    if len(ins) < 3 or len(outs) < 3:
        return f"only {len(ins)} of {len(vals)} {go.units()} fall inside {gz.title}"
    return {"groups": True, "in": ins, "out": outs, "zones": gz, "other": go}


def _zones_finding(st, out, s):
    gz, go = out["zones"], out["other"]
    ins, outs = out["in"], out["out"]
    mi, mo = float(np.median(ins)), float(np.median(outs))
    # rank-biserial effect size from the Mann-Whitney U statistic
    r_all = rank(np.r_[ins, outs])
    u = r_all[: len(ins)].sum() - len(ins) * (len(ins) - 1) / 2
    eff = 2 * u / (len(ins) * len(outs)) - 1
    nodes = {n["id"]: n["label"] for n in (s.graph or {}).get("nodes", [])}
    other = st["b"] if st["b"]["geo"] is go else st["a"]
    label = other.get("short") or go.label
    word = "higher" if mi > mo else "lower"
    head = (f"{label} is {word} inside {gz.title} than outside" if abs(eff) >= 0.1
            else f"{label} is about the same inside and outside {gz.title}")
    detail = (f"Median {fmt(mi)} for {len(ins)} {go.units()} inside, {fmt(mo)} for {len(outs)} outside "
              f"(rank-biserial effect {eff:+.2f}). Graph link: {nodes.get(st['fa'], st['fa'])} → {nodes.get(st['fb'], st['fb'])}.")
    return {"kind": "relate", "headline": head, "detail": detail,
            "chart": {"type": "groups", "groups": [["Inside", [round(float(v), 4) for v in ins[:300]]], ["Outside", [round(float(v), 4) for v in outs[:300]]]], "label": label[:60]},
            "edge": [st["fa"], st["fb"]], "rho": round(float(eff), 2), "n": int(len(ins) + len(outs)),
            "focus": {"layers": [gz.id, go.id]}, "numbers": {"median_in": mi, "median_out": mo, "effect": eff}}


def run_step(st: dict, s) -> dict | None:
    if st["kind"] == "profile":
        v = st["var"]
        fn = profile_series if v["type"] == "series" else profile_points if v["type"] == "point" else profile_area
        return fn(v, s)
    return relate(st, s)


# ---------------------------------------------------------------------------- reading the findings against the question
def _model_json(s, prompt: str, schema: dict, name: str, max_tokens: int = 1500) -> dict | None:
    from .agent import client
    model = s.model if s.model in MODEL_IDS else DEFAULT_MODEL
    r = client().responses.create(model=model, input=prompt, reasoning={"effort": "low"}, max_output_tokens=max_tokens,
                                  text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}})
    return json.loads(r.output_text or "{}")


def synthesis(s, findings: list[dict]) -> dict | None:
    if not _model_on() or not findings:
        return None
    g = s.graph or {}
    lines = [f"[{i + 1}] {f['headline']}. {f['detail']}" for i, f in enumerate(findings)]
    prompt = ("You help a researcher read a first look at their data. Question: " + g.get("question", "") +
              "\nFactors: " + "; ".join(f"{n['label']} ({n['role']})" for n in g.get("nodes", [])) +
              "\nLinks: " + "; ".join(f"{e['source']} -> {e['target']}" for e in g.get("edges", [])) +
              f"\nStudy area: {(s.scope or {}).get('extent_name')}, {(s.scope or {}).get('time_start')} to {(s.scope or {}).get('time_end')}."
              "\nFindings computed by Y-finder on small preview samples of the data:\n" + "\n".join(lines) +
              "\n\nWrite in plain English for a researcher. 'summary': at most 60 words on what these findings suggest for the "
              "question, citing finding numbers like [2]; use only numbers that appear in the findings; no causal claims. "
              "'next': up to 3 short next checks (each under 16 words), such as a better measure, a control, or full data. "
              "'caveat': one sentence on the main reason to be careful (sampling, proxies, units, or neighbouring places "
              "not being independent).")
    schema = {"type": "object", "additionalProperties": False, "required": ["summary", "next", "caveat"],
              "properties": {"summary": {"type": "string"}, "next": {"type": "array", "items": {"type": "string"}},
                             "caveat": {"type": "string"}}}
    try:
        j = _model_json(s, prompt, schema, "reading", 1800)
    except Exception as e:
        print(f"explore synthesis failed: {type(e).__name__}: {e}", flush=True)
        return None
    if not j or not j.get("summary"):
        return None
    return {"summary": str(j["summary"])[:600], "next": [str(x)[:160] for x in (j.get("next") or [])][:3],
            "caveat": str(j.get("caveat") or "")[:300]}


# ---------------------------------------------------------------------------- the run and its controls
class Run:
    def __init__(self, key):
        self.key = key
        self.status = "running"          # running, paused, waiting, stopped, done
        self.steps: list[dict] = []      # planned steps (with their variables), in order
        self.findings: list[dict] = []
        self.synthesis: dict | None = None
        self.step = "Planning from your question and graph"
        self.skipped: list[str] = []
        self.gate = threading.Event()
        self.gate.set()
        self.stopped = False
        self.started = time.time()
        self.lock = threading.Lock()
        self.cache: dict = {}


def key_of(s) -> str:
    from .previews import _sig
    g = s.graph or {}
    return repr((_sig(s.scope or {}), tuple(sorted(p["ref"] for p in s.picks)), g.get("outcome"), len(g.get("nodes", [])),
                 len(g.get("edges", []))))


def _previews_busy(s) -> int:
    with s.plock:
        n = sum(1 for r in s.previews.values() if r.get("status") == "pending")
        return n or (1 if s.preview_running else 0)


def _worker(s, run: Run):
    t0 = time.time()
    try:
        while not run.stopped:
            run.gate.wait()
            if run.stopped or session.peek(s.sid) is not s or key_of(s) != run.key:
                break
            variables, skipped = _variables(s, run.cache)
            with run.lock:
                run.skipped = skipped
                have = {st["id"] for st in run.steps}
                for st in plan(s, variables):
                    full = sum(x["kind"] == st["kind"] for x in run.steps) >= (MAX_PROFILES if st["kind"] == "profile" else MAX_RELATIONS)
                    if st["id"] not in have and not full:
                        st["status"] = "todo"
                        run.steps.append(st)
                todo = next((st for st in run.steps if st["status"] == "todo"), None)
            if todo:
                with run.lock:
                    todo["status"] = "doing"
                    k = sum(1 for st in run.steps if st["status"] != "todo")
                    run.step = f"{todo['title']} ({k} of {len(run.steps)})"
                    run.status = "running" if run.gate.is_set() else "paused"
                with _slots:
                    try:
                        f = run_step(todo, s)
                    except Exception as e:  # one odd layer must not end the exploration
                        print(f"explore step {todo['id']} failed: {type(e).__name__}: {e}", flush=True)
                        f = {"skip": f"could not be computed ({type(e).__name__})"}
                with run.lock:
                    if f and not f.get("skip"):
                        f.update(id=f"f{len(run.findings) + 1}", step=todo["id"], title=todo["title"],
                                 factors=[x for x in (todo.get("factor"), todo.get("fa"), todo.get("fb")) if x])
                        run.findings.append(f)
                        todo["status"] = "done"
                    else:
                        todo["status"] = "skipped"
                        todo["why"] = (f or {}).get("skip") or "nothing to report"
                continue
            busy = _previews_busy(s)
            if busy and time.time() - t0 < WAIT_SECONDS:
                with run.lock:
                    if run.gate.is_set():
                        run.status = "waiting"
                    run.step = f"Waiting for {busy} more preview{'s' if busy > 1 else ''} to load"
                time.sleep(2)
                continue
            if run.findings and run.synthesis is None:
                with run.lock:
                    run.step = "Reading the findings against your question"
                syn = synthesis(s, list(run.findings))
                with run.lock:
                    run.synthesis = syn or {}
                if run.stopped:
                    break
                run.gate.wait()
                continue
            with run.lock:
                run.status = "done"
                run.step = (f"{len(run.findings)} finding{'s' if len(run.findings) != 1 else ''}" if run.findings
                            else "Nothing to measure yet: the previews are images or boundaries")
            return
    finally:
        with run.lock:
            if run.status not in ("done",):
                run.status = "stopped" if run.stopped else "done"
            if run.status == "stopped":
                run.step = f"Stopped after {len(run.findings)} finding{'s' if len(run.findings) != 1 else ''}"
            for st in run.steps:
                if st["status"] == "doing":
                    st["status"] = "done" if any(f["step"] == st["id"] for f in run.findings) else "skipped"


def control(s, action: str) -> dict:
    """start, pause, resume or stop the exploration of this session's previews."""
    if not enabled():
        return public(s)
    run: Run | None = getattr(s, "explore", None)
    if action == "start":
        if run and run.key == key_of(s) and run.status in ("running", "paused", "waiting"):
            return public(s)
        run = Run(key_of(s))
        s.explore = run
        threading.Thread(target=_worker, args=(s, run), name=f"explore-{s.sid[:8]}", daemon=True).start()
    elif run and action == "pause" and run.status in ("running", "waiting"):
        with run.lock:
            run.gate.clear()
            run.status = "paused"
    elif run and action == "resume" and run.status == "paused":
        with run.lock:
            run.status = "running"
            run.gate.set()
    elif run and action == "stop" and run.status in ("running", "paused", "waiting"):
        with run.lock:
            run.stopped = True
            run.status = "stopped"
            run.gate.set()
    return public(s)


def _public_finding(f: dict) -> dict:
    return {k: f.get(k) for k in ("id", "kind", "title", "headline", "detail", "chart", "focus", "edge", "rho", "n",
                                  "factors", "coverage_warning")}


def public(s) -> dict:
    if not enabled():
        return {"status": "off"}
    run: Run | None = getattr(s, "explore", None)
    if run is None:
        return {"status": "idle"}
    with run.lock:
        st = run.status
        if st in ("done", "stopped") and run.key != key_of(s):
            st = "stale"
        done = sum(1 for x in run.steps if x["status"] in ("done", "skipped"))
        return {"status": st, "step": run.step, "done": done, "total": len(run.steps),
                "plan": [{"id": x["id"], "title": x["title"], "status": x["status"], "why": x.get("why")} for x in run.steps],
                "findings": [_public_finding(f) for f in run.findings], "synthesis": run.synthesis or None,
                "skipped": run.skipped[:6]}


def brief(s) -> str:
    """One line for the chat agent: what the exploration found so far."""
    run: Run | None = getattr(s, "explore", None)
    if not run or not run.findings:
        return ""
    return "exploration of the previews found: " + " | ".join(f["headline"][:150] for f in run.findings[:6])
