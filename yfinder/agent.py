"""The Y-finder agent: tri-projection prompt, tool schemas, tool handlers and the streaming loop.

run_turn() is a generator of UI events (dicts). The Flask app streams them as
server-sent events; scripts/run_personas.py consumes them headless.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import traceback

from openai import OpenAI

from . import catalog as catalog_mod
from . import fit, handbooks, sources
from .config import DEFAULT_MODEL, LOG_TURNS, MAX_OUTPUT_TOKENS, MAX_STEPS, MODELS, RUNTIME_DIR, openai_key
from .geo import bbox_size_km, clamp_bbox, valid_bbox
from .session import Session, save_layer

SYSTEM = """You are Y-finder, the geospatial data guide of the Yale Center for Geospatial Solutions (YCGS).
Your users are Yale students, faculty and staff. Many are new to GIS. Help them turn a research question
into a well-scoped study and find the datasets, tools and methods that fit it. Today is {today}.

Work in three projections (tri-projection), in order, and keep the user in the loop:
1. Real world -> influence graph. Name the outcome and the factors that drive, mediate or confound it.
   Call update_influence_graph with 7 to 14 nodes. For each factor say what could be observed to measure it.
   Mark as core only the factors the question cannot be answered without.
2. Influence graph -> scope. Settle geographic extent (call geocode, then use its bbox), time window,
   the spatial resolution the process needs (cell size in meters that can still see it, e.g. about 1 m for
   small pools or individual trees, 10 to 30 m for neighborhood land cover, null when the study uses reporting
   units such as tracts), the unit of analysis and the temporal frequency. Call set_scope. If the user did not
   state an element, choose a sensible default, list it in "assumed", and say so. Do not stall on questions.
3. Scope -> data. For each core factor, search. Start with search_catalog (YCGS curated cards with Yale access
   routes, plus Planetary Computer and Earth Engine). Use live sources when they can add something:
   search_stac_items for imagery over the study area and dates, search_yale_dataverse and
   search_yale_geoblacklight for Yale holdings and historical maps, search_arcgis_online and search_datagov for
   local and government layers, and web_search for anything else. Then call recommend_data once with your picks
   (usually 4 to 8 datasets, plus 2 to 4 methods and tools). The server checks every pick against the scope and
   returns fit labels for extent, time and resolution. Those labels are authoritative: report them, and explain
   poor fits plainly (for example, 10 m Sentinel-2 cannot resolve individual larval habitats; suggest sub-meter
   imagery, drone surveys through YCGS, or field data).
Put useful data on the map: the study area appears after geocode; add one or two previews when they help
(map_stac_preview for an imagery item, map_census_boundaries for reporting units, map_osm_features for
local features such as water or parks, map_feature_service for an ArcGIS layer you found).

Rules:
- Recommend only datasets returned by a tool in this conversation. Use the exact id from search_catalog
  (for example "cur:planet" or "pc:naip") or the exact ref/url from a live search. Never invent names, URLs,
  resolutions or dates.
- Prefer Yale-licensed sources when they fit better, and say how to get access (NetID, request form, YCGS).
- Keep replies short and plain: a two-sentence summary, then bullets. No tables. Do not repeat the cards the
  interface already shows; point to them ("see the Data tab").
- End with one question that would most improve the study, unless the user only asked a quick factual question.
  Before that final question, call offer_choices with 2 to 4 short, likely answers so the user can click one.
- If the user asks something outside geospatial research, answer briefly and steer back.
"""


def _tool(name: str, description: str, props: dict) -> dict:
    return {"type": "function", "name": name, "description": description, "strict": True,
            "parameters": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}}


S = {"type": "string"}
NODE = {"type": "object", "additionalProperties": False, "required": ["id", "label", "role", "observable", "core"],
        "properties": {"id": {"type": "string", "description": "short snake_case id"},
                       "label": {"type": "string"},
                       "role": {"type": "string", "enum": ["outcome", "driver", "mediator", "confounder", "context"]},
                       "observable": {"type": "string", "description": "what could be measured, e.g. 'standing water area from sub-meter imagery'"},
                       "core": {"type": "boolean"}}}
EDGE = {"type": "object", "additionalProperties": False, "required": ["source", "target", "note"],
        "properties": {"source": S, "target": S, "note": {"type": "string", "description": "why this influence exists, one short clause"}}}
PICK = {"type": "object", "additionalProperties": False,
        "required": ["ref", "title", "factors", "role", "why", "limitations", "spatial_resolution_m", "time_start", "time_end", "coverage"],
        "properties": {
            "ref": {"type": "string", "description": "exact catalog id (e.g. cur:planet, pc:naip, gee:USDA/NAIP/DOQQ) or the exact ref/url from a live search result"},
            "title": S,
            "factors": {"type": "array", "items": S, "description": "ids of graph nodes this dataset measures"},
            "role": {"type": "string", "enum": ["core", "supporting", "alternative"]},
            "why": {"type": "string", "description": "one sentence"},
            "limitations": {"type": "string", "description": "one sentence"},
            "spatial_resolution_m": {"type": ["number", "null"], "description": "only for web results without catalog metadata; else null"},
            "time_start": {"type": ["string", "null"], "description": "only for web results; else null"},
            "time_end": {"type": ["string", "null"], "description": "only for web results; else null"},
            "coverage": {"type": ["string", "null"], "description": "only for web results, e.g. 'US' or 'Connecticut'; else null"}}}
METHOD = {"type": "object", "additionalProperties": False, "required": ["name", "why", "tools"],
          "properties": {"name": S, "why": S, "tools": {"type": "string", "description": "Yale or open tools to do it"}}}

TOOLS = [
    _tool("update_influence_graph", "Create or replace the influence graph for the research question. The user sees it in the Frame tab.", {
        "question": {"type": "string", "description": "the research question restated in one plain sentence"},
        "outcome": {"type": "string", "description": "id of the outcome node"},
        "nodes": {"type": "array", "items": NODE}, "edges": {"type": "array", "items": EDGE}}),
    _tool("geocode", "Find a place by name. Returns its bounding box and draws its boundary on the map.", {"place": S}),
    _tool("set_scope", "Set the study scope used to check data fit. Draws the study box on the map.", {
        "extent_name": S,
        "bbox": {"type": "array", "items": {"type": "number"}, "description": "[west, south, east, north] in degrees"},
        "time_start": {"type": "string", "description": "YYYY or YYYY-MM-DD"},
        "time_end": {"type": "string", "description": "YYYY or YYYY-MM-DD"},
        "spatial_resolution_m": {"type": ["number", "null"], "description": "coarsest cell size in meters that still resolves the process; null if the study uses reporting units"},
        "unit_of_analysis": {"type": "string", "description": "e.g. census tract, parcel, 10 m pixel, household, town"},
        "temporal_frequency": {"type": "string", "enum": ["once", "daily", "weekly", "monthly", "seasonal", "annual", "multi-year"]},
        "assumed": {"type": "array", "items": {"type": "string", "enum": ["extent", "time", "resolution", "unit", "frequency"]},
                    "description": "scope elements you chose as defaults because the user did not state them"},
        "rationale": {"type": "string", "description": "one sentence on why this scope fits the question"}}),
    _tool("search_catalog", "Search the YCGS catalog: curated cards with Yale access routes, Planetary Computer and Earth Engine datasets, tools and methods. Results include a fit label against the current scope.", {
        "query": {"type": "string", "description": "a few keywords, e.g. 'tree canopy lidar'"},
        "kind": {"type": "string", "enum": ["any", "dataset", "tool", "method"]},
        "access": {"type": "string", "enum": ["any", "open", "yale"]},
        "limit": {"type": "integer", "description": "1 to 12"}}),
    _tool("search_stac_items", "Search Planetary Computer for imagery items over the study area. Draws footprints on the map. Common collections: sentinel-2-l2a, landsat-c2-l2, naip, sentinel-1-rtc, 3dep-lidar-dsm, esa-worldcover.", {
        "collection": S,
        "datetime": {"type": ["string", "null"], "description": "YYYY-MM-DD/YYYY-MM-DD; null to use the scope window"},
        "max_cloud": {"type": ["number", "null"], "description": "max cloud cover percent, or null"},
        "limit": {"type": "integer", "description": "1 to 12"}}),
    _tool("search_yale_dataverse", "Search Yale Dataverse, Yale's research data repository.", {"query": S}),
    _tool("search_yale_geoblacklight", "Search Yale Library GeoBlacklight for historical maps and GIS layers.", {
        "query": S, "within_study_area": {"type": "boolean"}}),
    _tool("search_arcgis_online", "Search public ArcGIS Online layers (feature, map and image services).", {
        "query": S, "types": {"type": "string", "enum": ["any", "feature", "imagery", "map"]},
        "within_study_area": {"type": "boolean"}}),
    _tool("search_datagov", "Search Data.gov, the US government open data catalog.", {"query": S}),
    _tool("map_stac_preview", "Show one imagery item from search_stac_items on the map.", {"item_id": S, "title": S}),
    _tool("map_osm_features", "Fetch OpenStreetMap features inside the study area (up to about 50 x 50 km) and map them.", {
        "tags": {"type": "array", "items": S, "description": "1 to 4 filters like natural=water, leisure=park, amenity=school, building"},
        "title": S}),
    _tool("map_feature_service", "Fetch features from an ArcGIS FeatureServer or MapServer layer URL inside the study area and map them.", {
        "url": {"type": "string", "description": "service URL from a search result, e.g. https://.../FeatureServer/0"},
        "title": S}),
    _tool("map_census_boundaries", "Map US Census boundaries inside the study area.", {
        "level": {"type": "string", "enum": ["tract", "block_group", "town", "place", "county", "state"]}}),
    _tool("recommend_data", "Record your recommended datasets, methods and tools. The server labels each dataset's fit to the scope and updates graph coverage. The user sees them in the Data tab.", {
        "picks": {"type": "array", "items": PICK}, "methods": {"type": "array", "items": METHOD},
        "next_steps": {"type": "array", "items": S}}),
    _tool("offer_choices", "Offer 2 to 4 short answers to the question you end your reply with. The user can click one instead of typing.", {
        "question": S, "options": {"type": "array", "items": S, "description": "2 to 4 answers, each under 60 characters"}}),
    _tool("get_access_handbook", "Get the data access handbook (endpoint, authentication, Python example, pitfalls) for a catalog dataset, when one exists. Use when the user asks how to download or access a dataset.", {
        "ref": {"type": "string", "description": "catalog id, e.g. cur:acs or pc:naip"}}),
]
TOOLS_WITH_WEB = TOOLS + [{"type": "web_search"}]

LABELS = {
    "update_influence_graph": "Building the influence graph", "geocode": "Finding the place",
    "set_scope": "Setting the study scope", "search_catalog": "Searching the YCGS catalog",
    "search_stac_items": "Searching imagery on Planetary Computer", "search_yale_dataverse": "Searching Yale Dataverse",
    "search_yale_geoblacklight": "Searching Yale GeoBlacklight", "search_arcgis_online": "Searching ArcGIS Online",
    "search_datagov": "Searching Data.gov", "map_stac_preview": "Adding an imagery preview",
    "map_osm_features": "Fetching OpenStreetMap features", "map_feature_service": "Fetching an ArcGIS layer",
    "map_census_boundaries": "Fetching Census boundaries", "recommend_data": "Checking each dataset against the scope",
    "get_access_handbook": "Opening the access handbook", "offer_choices": "Preparing quick replies",
}


# ============================================================================ tool handlers
class ToolError(Exception):
    pass


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")
    return s[:40] or "node"


def _compact_results(results: list[dict], scope: dict) -> list[dict]:
    out = []
    for r in results:
        f = fit.assess(r, scope)
        out.append({k: v for k, v in {
            "ref": r.get("ref"), "title": r.get("title"), "provider": r.get("provider"), "time": r.get("time"),
            "access": r.get("access"), "type": r.get("type"), "service_url": r.get("service_url"),
            "description": (r.get("description") or "")[:160], "fit": fit.short(f)}.items() if v})
    return out


def _coverage(session: Session) -> None:
    g = session.graph
    if not g:
        return
    rank = {"good": 3, "partial": 2, "unknown": 2, "poor": 1}
    best: dict[str, int] = {}
    for p in session.picks:
        # Unverified picks never count, and a "good" with unchecked dimensions counts only as partial coverage.
        if p.get("role") == "alternative" or p.get("verified") == "unverified":
            continue
        r = rank.get(p["fit"]["overall"], 1)
        if r == 3 and any(p["fit"][k]["label"] == "unknown" for k in ("extent", "time", "resolution")):
            r = 2
        for nid in p.get("factors", []):
            best[nid] = max(best.get(nid, 0), r)
    for n in g["nodes"]:
        r = best.get(n["id"], 0)
        n["data_coverage"] = "covered" if r == 3 else "partial" if r == 2 else "uncovered"


def h_update_influence_graph(s: Session, a: dict):
    nodes, ids = [], {}
    for n in a.get("nodes", [])[:30]:
        nid = _slug(n.get("id") or n.get("label"))
        while nid in ids.values():
            nid += "_2"
        ids[n.get("id")] = nid
        nodes.append({"id": nid, "label": (n.get("label") or nid)[:60], "role": n.get("role", "driver"),
                      "observable": (n.get("observable") or "")[:160], "core": bool(n.get("core"))})
    if len(nodes) < 2:
        raise ToolError("Give at least an outcome and one factor")
    edges, seen = [], set()
    for e in a.get("edges", [])[:60]:
        src, tgt = ids.get(e.get("source")), ids.get(e.get("target"))
        if src and tgt and src != tgt and (src, tgt) not in seen:
            seen.add((src, tgt))
            edges.append({"source": src, "target": tgt, "note": (e.get("note") or "")[:160]})
    outcome = ids.get(a.get("outcome")) or next((n["id"] for n in nodes if n["role"] == "outcome"), nodes[0]["id"])
    for n in nodes:
        if n["id"] == outcome:
            n["role"], n["core"] = "outcome", True
    s.graph = {"question": a.get("question", "")[:300], "outcome": outcome, "nodes": nodes, "edges": edges}
    _coverage(s)
    dropped = len(a.get("edges", [])) - len(edges)
    res = {"ok": True, "nodes": len(nodes), "edges": len(edges), "node_ids": [n["id"] for n in nodes]}
    if dropped:
        res["note"] = f"{dropped} edges referenced unknown nodes and were dropped"
    return res, [{"type": "graph", "graph": s.graph}], f"{len(nodes)} factors, {len(edges)} links"


def h_geocode(s: Session, a: dict):
    g = sources.geocode(a["place"])
    if not g:
        raise ToolError(f"Place not found: {a['place']}. Try a fuller name, e.g. 'New Haven, Connecticut'.")
    ev = [{"type": "zoom", "bbox": g["bbox"]}]
    if g.get("geometry"):
        layer = save_layer({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": g["geometry"], "properties": {"name": g["name"]}}]},
            g["name"].split(",")[0], "OpenStreetMap Nominatim", style="boundary")
        s.layers.append(layer)
        ev.insert(0, {"type": "layer", "layer": layer})
    wkm, hkm = bbox_size_km(g["bbox"])
    return ({"ok": True, "name": g["name"], "bbox": [round(x, 5) for x in g["bbox"]],
             "size_km": [round(wkm, 1), round(hkm, 1)]}, ev, g["name"].split(",")[0])


def h_set_scope(s: Session, a: dict):
    b = a.get("bbox") or []
    if len(b) != 4 or not valid_bbox(b):
        raise ToolError("bbox must be [west, south, east, north] in degrees; call geocode first")
    b = clamp_bbox(b)
    scope = {k: a.get(k) for k in ("extent_name", "time_start", "time_end", "spatial_resolution_m",
                                   "unit_of_analysis", "temporal_frequency", "assumed", "rationale")}
    scope["bbox"] = [round(x, 5) for x in b]
    if scope.get("spatial_resolution_m") is not None and scope["spatial_resolution_m"] <= 0:
        scope["spatial_resolution_m"] = None
    s.scope = scope
    for p in s.picks:  # re-check earlier picks against the new scope
        p["fit"] = fit.assess(p["meta"], s.scope)
    _coverage(s)
    layer = save_layer({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "Study area"},
                        "geometry": {"type": "Polygon", "coordinates": [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]]}}]},
                       "Study area box", "Y-finder scope", style="scope")
    s.layers = [l for l in s.layers if l.get("style") != "scope"] + [layer]
    ev = [{"type": "scope", "scope": scope}, {"type": "layer", "layer": layer}]
    if s.picks:
        ev.append({"type": "recommendations", "picks": _ui_picks(s), "methods": s.methods})
    if s.graph:
        ev.append({"type": "graph", "graph": s.graph})
    wkm, hkm = bbox_size_km(b)
    return {"ok": True, "scope": scope, "size_km": [round(wkm, 1), round(hkm, 1)]}, ev, scope.get("extent_name") or "Scope set"


def h_search_catalog(s: Session, a: dict):
    cat = catalog_mod.load()
    res = cat.search(a["query"], kind=a.get("kind", "any"), access=a.get("access", "any"),
                     limit=max(1, min(int(a.get("limit") or 8), 12)), scope=s.scope)
    for r in res:
        s.seen[r["id"]] = cat.get(r["id"])
    compact = []
    for r in res:
        c = {k: r.get(k) for k in ("id", "title", "source", "access", "region", "time", "resolution", "unit") if r.get(k)}
        if r.get("kind") != "dataset":
            c["kind"] = r["kind"]
            c["description"] = (r.get("description") or "")[:140]
        if r.get("fit"):
            c["fit"] = fit.short(r["fit"])
        if r.get("stac_collection"):
            c["stac_collection"] = r["stac_collection"]
        compact.append(c)
    ui = [{"title": r["title"], "url": r.get("url"), "meta": r.get("source")} for r in res]
    return {"results": compact}, [{"type": "results", "items": ui}], f"{len(res)} matches for '{a['query']}'"


def _scope_datetime(scope: dict) -> str | None:
    t0, t1 = scope.get("time_start"), scope.get("time_end")
    if not t0 and not t1:
        return None

    def norm(t, end):
        t = str(t)
        if re.fullmatch(r"\d{4}", t):
            return t + ("-12-31" if end else "-01-01")
        if re.fullmatch(r"\d{4}-\d{2}", t):
            return t + ("-28" if end else "-01")
        return t[:10]
    return f"{norm(t0 or t1, False)}/{norm(t1 or t0, True)}"


def h_search_stac_items(s: Session, a: dict):
    if not s.scope.get("bbox"):
        raise ToolError("Set the scope first (set_scope) so the search has a study area")
    coll = a["collection"].strip().removeprefix("pc:")
    items = sources.stac_search(coll, s.scope["bbox"], a.get("datetime") or _scope_datetime(s.scope),
                                a.get("max_cloud"), a.get("limit") or 8)
    for it in items:
        s.stac_items[it["id"]] = it
    ev = []
    if items:
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": it["geometry"], "properties": {"id": it["id"], "date": (it["datetime"] or "")[:10],
             "cloud_cover": it.get("cloud_cover")}} for it in items if it.get("geometry")]}
        layer = save_layer(fc, f"{coll} footprints", "Microsoft Planetary Computer", style="footprint")
        s.layers.append(layer)
        ev.append({"type": "layer", "layer": layer})
    cat = catalog_mod.load()
    if cat.get("pc:" + coll):
        s.seen["pc:" + coll] = cat.get("pc:" + coll)
    out = [{"item_id": it["id"], "date": (it["datetime"] or "")[:10], "cloud_cover": it.get("cloud_cover"),
            "gsd_m": it.get("gsd")} for it in items]
    res = {"collection_ref": "pc:" + coll, "items": out}
    if not items:
        res["note"] = "No items matched. Widen the dates or drop the cloud filter."
    return res, ev, f"{len(items)} {coll} scenes"


def _live(s: Session, results: list[dict], label: str):
    s.remember(results)
    ui = [{"title": r["title"], "url": r.get("url"), "meta": r.get("provider") or r.get("source")} for r in results]
    return {"results": _compact_results(results, s.scope)}, [{"type": "results", "items": ui}], f"{len(results)} results for '{label}'"


def h_search_yale_dataverse(s, a):
    return _live(s, sources.yale_dataverse(a["query"]), a["query"])


def h_search_yale_geoblacklight(s, a):
    bbox = s.scope.get("bbox") if a.get("within_study_area") else None
    y0, y1 = re.match(r"\d{4}", str(s.scope.get("time_start") or "")), re.match(r"\d{4}", str(s.scope.get("time_end") or ""))
    years = (int(y0.group()), int(y1.group()) if y1 else None) if y0 else None
    res = sources.yale_geoblacklight(a["query"], bbox, years=years)
    if not res and years:  # nothing in the window: show the closest holdings instead of nothing
        res = sources.yale_geoblacklight(a["query"], bbox)
    return _live(s, res, a["query"])


def h_search_arcgis_online(s, a):
    bbox = s.scope.get("bbox") if a.get("within_study_area") else None
    return _live(s, sources.arcgis_online(a["query"], bbox, a.get("types", "any")), a["query"])


def h_search_datagov(s, a):
    return _live(s, sources.datagov(a["query"]), a["query"])


def h_map_stac_preview(s: Session, a: dict):
    it = s.stac_items.get(a["item_id"])
    if not it:
        raise ToolError("Unknown item_id; use an item_id returned by search_stac_items")
    t = sources.stac_tiles(it.get("tilejson"))
    if not t or not t.get("tiles"):
        raise ToolError("This item has no map preview")
    layer = {"id": "stac-" + re.sub(r"[^A-Za-z0-9]", "", it["id"])[-40:], "kind": "raster", "title": a.get("title") or it["id"],
             "source": "Microsoft Planetary Computer", "tiles": t["tiles"], "bbox": t.get("bounds") or it.get("bbox"),
             "minzoom": t["minzoom"], "maxzoom": t["maxzoom"], "preview": it.get("preview"),
             "date": (it.get("datetime") or "")[:10], "collection": it["collection"]}
    s.layers.append(layer)
    return {"ok": True, "shown": it["id"]}, [{"type": "layer", "layer": layer}, {"type": "zoom", "bbox": s.scope.get("bbox") or layer["bbox"]}], layer["title"]


def _vector(s: Session, fc: dict, title: str, source: str, style: str = "default"):
    summ = sources.fc_summary(fc)
    if not summ["n"]:
        return {"ok": True, "features": 0, "note": "Nothing found inside the study area"}, [], "No features found"
    layer = save_layer(fc, title, source, style)
    s.layers.append(layer)
    return ({"ok": True, "features": summ["n"], "geometry_types": summ["geometry_types"]},
            [{"type": "layer", "layer": layer}], f"{summ['n']} features")


def h_map_osm_features(s: Session, a: dict):
    try:
        fc = sources.osm_features(a.get("tags") or [], s.scope.get("bbox"))
    except ValueError as e:
        raise ToolError(str(e))
    return _vector(s, fc, a.get("title") or "OpenStreetMap features", "OpenStreetMap contributors (ODbL)")


def h_map_feature_service(s: Session, a: dict):
    try:
        fc = sources.arcgis_query(a["url"], s.scope.get("bbox"))
    except ValueError as e:
        raise ToolError(str(e))
    return _vector(s, fc, a.get("title") or "ArcGIS layer", "ArcGIS service")


def h_map_census_boundaries(s: Session, a: dict):
    if not s.scope.get("bbox"):
        raise ToolError("Set the scope first")
    w, h = bbox_size_km(s.scope["bbox"])
    if a["level"] in ("tract", "block_group") and w * h > 40000:
        raise ToolError("Study area is too large for tract or block group preview; use county or town")
    fc = sources.census_boundaries(a["level"], s.scope["bbox"])
    name = {"tract": "Census tracts", "block_group": "Census block groups", "town": "Towns (county subdivisions)",
            "place": "Places", "county": "Counties", "state": "States"}[a["level"]]
    return _vector(s, fc, name, "U.S. Census Bureau TIGERweb", style="boundary")


def norm_url(u: str) -> str:
    """Normalize a URL for matching: no scheme, lower-case host, no tracking query, no fragment or trailing slash."""
    u = (u or "").strip().rstrip(".,;)?")
    u = re.sub(r"#.*$", "", u)
    u = re.sub(r"[?&]utm_[^=&]+=[^&]*", "", u)
    u = re.sub(r"^https?://(www\.)?", "", u, flags=re.I)
    host, _, path = u.partition("/")
    return host.lower() + "/" + path.rstrip("/?")


def _resolve(s: Session, ref: str, title: str) -> tuple[dict | None, str]:
    cat = catalog_mod.load()
    ref = (ref or "").strip()
    it = cat.get(ref)
    if it:
        return it, "catalog"
    if ref in s.seen:
        return s.seen[ref], "search"
    n = norm_url(ref)
    for k, v in s.seen.items():  # model used the url of a catalog card or result
        if v and (norm_url(v.get("url", "")) == n or norm_url(v.get("ref", "")) == n):
            return v, "search" if v.get("ref") else "catalog"
    cites = getattr(s, "citations", set())
    if n in {norm_url(c) for c in cites} or n.split("?")[0] in {norm_url(c).split("?")[0] for c in cites}:
        return None, "web"
    return None, "unverified"


def _ui_picks(s: Session) -> list[dict]:
    out = []
    for p in s.picks:
        q = {k: v for k, v in p.items() if k != "meta"}
        out.append(q)
    return out


def h_recommend_data(s: Session, a: dict):
    node_ids = {n["id"] for n in (s.graph or {}).get("nodes", [])}
    new = []
    for p in a.get("picks", [])[:14]:
        meta, how = _resolve(s, p.get("ref"), p.get("title"))
        model_meta = {"res_m": p.get("spatial_resolution_m"), "t0": p.get("time_start"), "t1": p.get("time_end"),
                      "region": p.get("coverage")}
        if meta is None:
            meta = {"title": p.get("title"), "url": p.get("ref") if str(p.get("ref", "")).startswith("http") else "",
                    "access": "Check the source", "access_class": "", **model_meta}
            filled = True
        else:
            meta = dict(meta)
            filled = False
            for k, v in model_meta.items():  # fill gaps only; never overwrite catalog metadata
                if v and not meta.get(k) and not (k == "region" and meta.get("bbox")):
                    meta[k] = v
                    filled = True
        f = fit.assess(meta, s.scope)
        new.append({
            "ref": p.get("ref"), "title": meta.get("title") or p.get("title"), "url": meta.get("url") or "",
            "source": meta.get("source") or ("Web" if how == "web" else ""), "provider": meta.get("provider") or "",
            "access": meta.get("access") or "", "access_class": meta.get("access_class") or "",
            "time": meta.get("time") or "", "resolution": catalog_mod.fmt_res(meta.get("res_m")), "unit": meta.get("unit") or "",
            "factors": [x for x in p.get("factors", []) if x in node_ids], "role": p.get("role", "supporting"),
            "why": p.get("why", ""), "limitations": p.get("limitations", ""), "fit": f, "verified": how,
            "metadata_from_model": filled, "yale_note": meta.get("yale_note") or "", "thumbnail": meta.get("thumbnail") or "",
            "stac_collection": (meta.get("stac") or {}).get("collection") or "",
            "t0": meta.get("t0"), "t1": meta.get("t1"), "bbox": fit.dataset_bbox(meta), "meta": meta})
    refs = {p["ref"] for p in new}
    s.picks = [p for p in s.picks if p["ref"] not in refs] + new
    s.picks = s.picks[-25:]
    cat = catalog_mod.load()
    methods = []
    for m in a.get("methods", [])[:8]:
        hit = cat.search(m.get("name", ""), kind="method", limit=1)
        methods.append({**m, "card": hit[0]["id"] if hit else None})
    if methods:
        s.methods = methods
    if a.get("next_steps"):
        s.next_steps = a["next_steps"][:5]
    _coverage(s)
    core_uncovered = [n["label"] for n in (s.graph or {}).get("nodes", []) if n.get("core") and n.get("data_coverage") == "uncovered"]
    res = {"picks": [{"ref": p["ref"], "verified": p["verified"], "fit": fit.short(p["fit"])} for p in new],
           "core_factors_without_data": core_uncovered}
    unver = [p["ref"] for p in new if p["verified"] == "unverified"]
    if unver:
        res["warning"] = ("These refs were not returned by any tool in this conversation and are marked unverified: "
                          + ", ".join(unver) + ". Search for them or remove them.")
    ev = [{"type": "recommendations", "picks": _ui_picks(s), "methods": s.methods,
           "next_steps": getattr(s, "next_steps", [])}]
    if s.graph:
        ev.append({"type": "graph", "graph": s.graph})
    good = sum(p["fit"]["overall"] == "good" for p in new)
    return res, ev, f"{len(new)} datasets checked, {good} fit well"


def h_offer_choices(s: Session, a: dict):
    opts = [str(o).strip()[:80] for o in a.get("options", []) if str(o).strip()][:4]
    if len(opts) < 2:
        raise ToolError("Give 2 to 4 options")
    return {"ok": True, "shown": len(opts)}, [{"type": "choices", "question": a.get("question", "")[:200], "options": opts}], f"{len(opts)} quick replies"


def h_get_access_handbook(s: Session, a: dict):
    hb = handbooks.get(a["ref"])
    if not hb:
        return ({"found": False, "note": "No handbook yet. The user can open the dataset card and choose 'How to access' "
                 "to write one, or use the source link."}, [], "No handbook yet")
    md = hb["markdown"]
    return ({"found": True, "status": hb["meta"].get("status"), "handbook": md[:6000]},
            [{"type": "handbook", "ref": a["ref"], "title": hb["meta"].get("description", a["ref"])}], "Handbook found")


HANDLERS = {name[2:]: fn for name, fn in globals().items() if name.startswith("h_")}


# ============================================================================ the loop
_client = None


def client() -> OpenAI:
    global _client
    if _client is None:
        key = openai_key()
        if not key:
            raise RuntimeError("No OpenAI API key. Set OPENAI_API_KEY or put OpenAI_key.txt in the project root.")
        _client = OpenAI(api_key=key, timeout=120, max_retries=2)
    return _client


def _state_note(s: Session) -> str:
    bits = []
    if s.scope:
        sc = s.scope
        bits.append(f"current scope: {sc.get('extent_name')} bbox {sc.get('bbox')}, {sc.get('time_start')} to {sc.get('time_end')}, "
                    f"resolution {sc.get('spatial_resolution_m')} m, unit {sc.get('unit_of_analysis')}")
    if s.graph:
        bits.append(f"graph has {len(s.graph['nodes'])} factors (outcome: {s.graph['outcome']})")
    if s.picks:
        bits.append(f"{len(s.picks)} datasets already recommended")
    return ("[Y-finder state: " + "; ".join(bits) + "]") if bits else ""


def _model_cfg(model: str) -> dict:
    return next((m for m in MODELS if m["id"] == model), {"id": model, "effort": "low"})


def run_turn(s: Session, text: str, model: str | None = None, web: bool = True):
    """Yield UI events for one user message."""
    t_start = time.time()
    model = model or s.model or DEFAULT_MODEL
    cfg = _model_cfg(model)
    s.model = model
    s.turns += 1
    if not getattr(s, "first_message", None):
        s.first_message = text.strip()[:300]
    if not hasattr(s, "citations"):
        s.citations = set()
    content = text.strip()
    if s.proposal and not s.proposal.get("used"):
        content += (f"\n\n[The user attached '{s.proposal['name']}'. Excerpt:]\n" + s.proposal["text"][:14000])
        s.proposal["used"] = True
    note = _state_note(s)
    if note:
        content = note + "\n\n" + content
    inputs: list = [{"role": "user", "content": content}]
    usage = {"input": 0, "output": 0, "steps": 0, "tools": 0}
    tool_log = []
    prev = s.prev_response_id
    instructions = SYSTEM.format(today=dt.date.today().isoformat())
    emitted_text = False
    yield {"type": "status", "text": "Thinking"}
    try:
        for step in range(MAX_STEPS):
            last = step == MAX_STEPS - 1
            kwargs = dict(model=model, instructions=instructions, input=inputs, tools=TOOLS_WITH_WEB if web else TOOLS,
                          reasoning={"effort": cfg.get("effort", "low")}, max_output_tokens=MAX_OUTPUT_TOKENS, stream=True)
            if web:
                kwargs["include"] = ["web_search_call.action.sources"]
            if prev:
                kwargs["previous_response_id"] = prev
            if last:
                kwargs["tool_choice"] = "none"
            try:
                stream = client().responses.create(**kwargs)
            except Exception as e:  # stale previous_response_id: start fresh with a state note
                if prev and "previous_response" in str(e):
                    kwargs.pop("previous_response_id")
                    prev = None
                    stream = client().responses.create(**kwargs)
                else:
                    raise
            calls, resp = [], None
            step_text = False
            for ev in stream:
                et = getattr(ev, "type", "")
                if et == "response.output_text.delta":
                    if not step_text and emitted_text:  # text from a new step starts a new paragraph
                        yield {"type": "text", "delta": "\n\n"}
                    step_text = emitted_text = True
                    yield {"type": "text", "delta": ev.delta}
                elif et == "response.output_item.added" and getattr(ev.item, "type", "") == "web_search_call":
                    yield {"type": "tool", "id": ev.item.id, "name": "web_search", "label": "Searching the web", "state": "start"}
                elif et == "response.output_item.done":
                    item = ev.item
                    it = getattr(item, "type", "")
                    if it == "function_call":
                        calls.append(item)
                    elif it == "web_search_call":
                        act = getattr(item, "action", None)
                        q = getattr(act, "query", None) or ""
                        srcs = [getattr(x, "url", None) or (x.get("url") if isinstance(x, dict) else None)
                                for x in (getattr(act, "sources", None) or [])]
                        srcs = [u for u in srcs if u]
                        s.citations.update(srcs)  # pages the search returned count as found, so picks from them are verifiable
                        tool_log.append({"name": "web_search", "query": q, "sources": len(srcs)})
                        yield {"type": "tool", "id": item.id, "name": "web_search", "label": "Searching the web",
                               "state": "done", "summary": q[:80]}
                        if srcs:
                            yield {"type": "results", "items": [{"title": re.sub(r"^https?://(www\.)?", "", u)[:90], "url": u, "meta": "web"} for u in srcs[:8]]}
                    elif it == "message":
                        cites = []
                        for part in getattr(item, "content", []) or []:
                            for ann in getattr(part, "annotations", []) or []:
                                if getattr(ann, "type", "") == "url_citation":
                                    s.citations.add(ann.url)
                                    cites.append({"url": ann.url, "title": getattr(ann, "title", "") or ann.url})
                        if cites:
                            yield {"type": "citations", "items": cites}
                elif et == "response.completed":
                    resp = ev.response
                elif et == "response.incomplete":  # e.g. hit the output-token limit: keep the chain, tell the user
                    resp = ev.response
                    yield {"type": "text", "delta": "\n\n*(The answer was cut short. Ask me to continue.)*"}
                elif et in ("response.failed", "error"):
                    msg = getattr(getattr(ev, "response", None), "error", None) or getattr(ev, "message", "model error")
                    raise RuntimeError(str(msg))
            if resp is not None:
                prev = resp.id
                u = getattr(resp, "usage", None)
                if u:
                    usage["input"] += u.input_tokens or 0
                    usage["output"] += u.output_tokens or 0
            usage["steps"] += 1
            if not calls:
                break
            inputs = []
            for c in calls:
                usage["tools"] += 1
                name = c.name
                yield {"type": "tool", "id": c.call_id, "name": name, "label": LABELS.get(name, name), "state": "start"}
                t0 = time.time()
                try:
                    args = json.loads(c.arguments or "{}")
                    fn = HANDLERS.get(name)
                    if not fn:
                        raise ToolError("Unknown tool")
                    result, events, summary = fn(s, args)
                    for e in events:
                        yield e
                    yield {"type": "tool", "id": c.call_id, "name": name, "label": LABELS.get(name, name), "state": "done", "summary": summary}
                    tool_log.append({"name": name, "args": args, "ok": True, "summary": summary, "sec": round(time.time() - t0, 1)})
                except ToolError as e:
                    result = {"ok": False, "error": str(e)}
                    yield {"type": "tool", "id": c.call_id, "name": name, "label": LABELS.get(name, name), "state": "error", "summary": str(e)[:140]}
                    tool_log.append({"name": name, "ok": False, "error": str(e)})
                except Exception as e:  # network or parsing failure inside a connector
                    result = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
                    yield {"type": "tool", "id": c.call_id, "name": name, "label": LABELS.get(name, name), "state": "error", "summary": "Service did not respond"}
                    tool_log.append({"name": name, "ok": False, "error": result["error"]})
                inputs.append({"type": "function_call_output", "call_id": c.call_id,
                               "output": json.dumps(result, ensure_ascii=False, default=str)[:24000]})
        s.prev_response_id = prev
        yield {"type": "done", "usage": usage, "elapsed": round(time.time() - t_start, 1), "model": model}
    except Exception as e:
        # The last response may be waiting for tool outputs we never sent; continuing from it would fail,
        # so drop the chain. The next turn starts fresh with the state note (scope, graph, picks).
        s.prev_response_id = None
        traceback.print_exc()
        yield {"type": "error", "message": f"Something went wrong: {str(e)[:300]}"}
        yield {"type": "done", "usage": usage, "elapsed": round(time.time() - t_start, 1), "model": model}
    finally:
        s.last_tool_log = tool_log
        if LOG_TURNS:
            try:
                d = RUNTIME_DIR / "logs"
                d.mkdir(parents=True, exist_ok=True)
                with open(d / f"{dt.date.today().isoformat()}.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"t": time.time(), "sid": s.sid, "model": model, "text": text[:2000],
                                         "tools": tool_log, "usage": usage}, default=str) + "\n")
            except OSError:
                pass
