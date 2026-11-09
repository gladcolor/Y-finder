"""Flask app: serves the single-page UI and the JSON / server-sent-event API.

Local:   python -m yfinder.app            (http://127.0.0.1:8770)
Server:  gunicorn "yfinder.app:create_app()" -w 1 --threads 16 -b 127.0.0.1:8060
         behind nginx at /y-finder/ (see deploy/).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from xml.sax.saxutils import escape

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory, stream_with_context

from . import agent, catalog, downloads, fit, handbooks, janitor, previews, session, watch
from .config import (ACCESS_CODE, DAILY_CAP, DEFAULT_MODEL, MODEL_IDS, MODELS, RATE_PER_HOUR, STATIC_DIR,
                     UPLOAD_MAX_BYTES, openai_key)

_rate: dict[str, deque] = defaultdict(deque)
_day = {"date": time.strftime("%Y-%m-%d"), "n": 0}
_rate_lock = threading.Lock()


_dl_rate: dict[str, deque] = defaultdict(deque)


def _allow_download() -> str | None:
    """At most 20 downloads per hour from one address, so nobody can use the server as a file mirror."""
    ip, now = _client_ip(), time.time()
    with _rate_lock:
        q = _dl_rate[ip]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= 20:
            return "Too many downloads from your address this hour; try again later."
        q.append(now)
    return None


def _client_ip() -> str:
    return (request.headers.get("CF-Connecting-IP") or request.headers.get("X-Real-IP")
            or request.remote_addr or "?").split(",")[0].strip()


def _allow_turn() -> str | None:
    """Return an error message when this request is over a limit, else None."""
    now = time.time()
    with _rate_lock:
        today = time.strftime("%Y-%m-%d")
        if _day["date"] != today:
            _day.update(date=today, n=0)
        if _day["n"] >= DAILY_CAP:
            return "Y-finder reached today's usage limit. Please try again tomorrow."
        q = _rate[_client_ip()]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= RATE_PER_HOUR:
            return "You have sent many messages this hour. Please wait a few minutes."
        q.append(now)
        _day["n"] += 1
    return None


def _check_code():
    if ACCESS_CODE and request.headers.get("X-Access-Code", "") != ACCESS_CODE:
        abort(Response(json.dumps({"error": "Access code required"}), 401, mimetype="application/json"))


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = UPLOAD_MAX_BYTES + 1024 * 64
    catalog.load()  # build the search index at startup, not on the first request
    janitor.start()  # periodic cleanup of expired sessions, downloads and old layers (YFINDER_JANITOR=0 turns it off)

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return resp

    def _asset_version() -> str:
        """Content hash of the front-end files. Cloudflare caches .js and .css for hours regardless of our
        headers, so asset URLs carry ?v=<hash> and change whenever the files change."""
        h = hashlib.sha1()
        for name in ("app.js", "app.css", "demos/index.json"):
            p = STATIC_DIR / name
            if p.exists():
                h.update(p.read_bytes())
        return h.hexdigest()[:10]

    @app.get("/")
    def index():
        v = _asset_version()
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        html = html.replace('src="static/app.js"', f'src="static/app.js?v={v}"')
        html = html.replace('href="static/app.css"', f'href="static/app.css?v={v}"')
        html = html.replace("</head>", f'<script>window.YF_VERSION="{v}";</script>\n</head>', 1)
        resp = Response(html, mimetype="text/html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/static/<path:name>")
    def static_files(name):
        resp = send_from_directory(STATIC_DIR, name)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/healthz")
    def healthz():
        cat = catalog.load()
        return jsonify(ok=True, items=len(cat.items), key=bool(openai_key()))

    @app.get("/api/config")
    def config():
        cat = catalog.load()
        return jsonify(models=MODELS, default_model=DEFAULT_MODEL, catalog=cat.meta,
                       access_code=bool(ACCESS_CODE), handbooks=handbooks.count(),
                       handbook_ondemand=handbooks.can_generate(), watch_email=watch.smtp_ready(),
                       watch_consent=watch.CONSENT_TEXT)

    @app.post("/api/chat")
    def chat():
        _check_code()
        body = request.get_json(silent=True) or {}
        text = (body.get("message") or "").strip()
        if not text:
            return jsonify(error="Empty message"), 400
        if len(text) > 6000:
            return jsonify(error="Message is too long (6,000 characters max). Attach long text as a file."), 400
        model = body.get("model") or DEFAULT_MODEL
        if model not in MODEL_IDS:
            return jsonify(error="Unknown model"), 400
        limit = _allow_turn()
        if limit:
            return jsonify(error=limit), 429
        s = session.get(body.get("sid"))
        if not s.lock.acquire(blocking=False):
            return jsonify(error="Still working on your previous message."), 409

        def gen():
            try:
                yield f"data: {json.dumps({'type': 'session', 'sid': s.sid})}\n\n"
                for ev in agent.run_turn(s, text, model=model, web=bool(body.get("web", True))):
                    yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
            finally:
                s.lock.release()

        return Response(stream_with_context(gen()), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/upload")
    def upload():
        _check_code()
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify(error="No file"), 400
        name = os.path.basename(f.filename)[:120]
        raw = f.read(UPLOAD_MAX_BYTES + 1)
        if len(raw) > UPLOAD_MAX_BYTES:
            return jsonify(error="File is larger than 12 MB"), 400
        try:
            text = _extract_text(name, raw)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if len(text.strip()) < 40:
            return jsonify(error="Could not read text from this file. Try a PDF with selectable text, DOCX or TXT."), 400
        s = session.get(request.form.get("sid"))
        if not s.lock.acquire(blocking=False):
            return jsonify(error="Wait for the current answer to finish, then attach the file."), 409
        try:
            s.proposal = {"name": name, "text": text, "used": False}
        finally:
            s.lock.release()
        return jsonify(sid=s.sid, name=name, words=len(text.split()))

    @app.post("/api/session/<sid>/proposal/clear")
    def clear_proposal(sid):
        s = session.peek(sid)
        if s and s.lock.acquire(blocking=False):
            try:
                s.proposal = None
            finally:
                s.lock.release()
        return jsonify(ok=True)

    @app.get("/api/catalog/search")
    def catalog_search():
        q = request.args.get("q", "")
        kind = request.args.get("kind", "any")
        access = request.args.get("access", "any")
        source = request.args.get("source", "any")
        source = source if source in ("any", "GeoLibre", "GEE community", "Planetary Computer", "Earth Engine", "YCGS curated") else "any"
        s = session.peek(request.args.get("sid", "")) if request.args.get("sid") else None
        limit = min(int(request.args.get("limit", 30) or 30), 60)
        cat = catalog.load()
        if not q.strip() and source != "any":  # browse one source, e.g. everything collected in GeoLibre
            items = [it for it in cat.items if source.lower() in (it.get("source") or "").lower()]
            size = {}
            for it in items:
                size[it.get("source")] = size.get(it.get("source"), 0) + 1
            # small groups first (GeoLibre's 6 services before its 105 maps), then by title
            items.sort(key=lambda it: (size[it.get("source")], it.get("source") or "", it.get("title") or ""))
            limit = min(int(request.args.get("limit", 30) or 30), 150)
            sc = s.scope if s and s.scope else None
            return jsonify(results=[catalog.card(it, fit.assess(it, sc) if sc else None) for it in items[:limit]])
        res = cat.search(q, kind=kind if kind in ("any", "dataset", "tool", "method") else "any",
                         access=access if access in ("any", "open", "yale") else "any",
                         limit=limit, scope=s.scope if s else None, source=source)
        return jsonify(results=res)

    @app.get("/api/catalog/item")
    def catalog_item():
        it = catalog.load().get(request.args.get("id", ""))
        if not it:
            return jsonify(error="Not found"), 404
        s = session.peek(request.args.get("sid", "")) if request.args.get("sid") else None
        return jsonify(item=catalog.card(it, fit.assess(it, s.scope) if s and s.scope else None))

    @app.post("/api/map_layer")
    def map_layer():
        """Draw a GeoLibre gallery map or service from a card button, without a chat turn."""
        _check_code()
        b = request.get_json(silent=True) or {}
        s = session.get(str(b.get("sid") or "") or None)
        if not s.lock.acquire(blocking=False):
            return jsonify(error="Wait for the current answer to finish."), 409
        try:
            res, events, summary = agent.h_map_catalog_layer(s, {"ref": str(b.get("ref") or "")[:200]})
        except agent.ToolError as e:
            return jsonify(error=str(e), sid=s.sid), 400
        finally:
            s.lock.release()
        return jsonify(sid=s.sid, events=events, summary=summary, result=res)

    @app.post("/api/download")
    def download():
        """Download a data file from a link for this session and draw it (the Layers panel's "Add from a link")."""
        _check_code()
        limit = _allow_download()
        if limit:
            return jsonify(error=limit), 429
        b = request.get_json(silent=True) or {}
        s = session.get(str(b.get("sid") or "") or None)
        if not s.lock.acquire(blocking=False):
            return jsonify(error="Wait for the current answer to finish."), 409
        try:
            res, events, summary = agent.h_download_data(s, {"url": str(b.get("url") or "")[:2000],
                                                             "title": str(b.get("title") or "")[:100]})
        except agent.ToolError as e:
            return jsonify(error=str(e), sid=s.sid), 400
        finally:
            s.lock.release()
        return jsonify(sid=s.sid, events=events, summary=summary, result=res)

    @app.route("/api/session/<sid>/previews", methods=["GET", "POST"])
    def session_previews(sid):
        """Map previews of the recommended datasets (POST starts any that are missing, e.g. after a demo loads)."""
        s = session.peek(sid)
        if not s:
            return jsonify(error="Not found"), 404
        if request.method == "POST":
            _check_code()
            previews.start(s)
        return jsonify(previews.public(s))

    @app.get("/api/session/<sid>/sample/<name>")
    def session_sample(sid, name):
        """A small CSV slice made for a preview (kept only for this session)."""
        p = downloads.sample_path(sid, name) if session.peek(sid) else None
        if not p:
            return jsonify(error="Not found"), 404
        return send_file(p, mimetype="text/csv", as_attachment=True, download_name=name, max_age=0)

    @app.get("/api/session/<sid>")
    def session_state(sid):
        s = session.peek(sid)
        if not s:
            return jsonify(error="Not found"), 404
        st = s.public()
        st["picks"] = agent._ui_picks(s)
        return jsonify(st)

    @app.post("/api/session/<sid>/scope")
    def edit_scope(sid):
        """Let the user edit the scope in the Frame tab; picks are re-checked immediately."""
        s = session.peek(sid)
        if not s:
            return jsonify(error="Not found"), 404
        if not s.lock.acquire(blocking=False):
            return jsonify(error="Wait for the current answer to finish, then edit the scope."), 409
        try:
            return _edit_scope(s, request.get_json(silent=True) or {})
        finally:
            s.lock.release()

    def _edit_scope(s, body):
        merged = dict(s.scope or {})
        for k in ("time_start", "time_end", "unit_of_analysis", "extent_name"):
            if k in body:
                merged[k] = str(body[k])[:80]
        if "spatial_resolution_m" in body:
            v = body["spatial_resolution_m"]
            merged["spatial_resolution_m"] = float(v) if v not in (None, "", 0, "0") else None
        if not merged.get("bbox"):
            return jsonify(error="Ask Y-finder to set a study area first"), 400
        merged["assumed"] = [a for a in merged.get("assumed") or []
                             if not ({"time": {"time_start", "time_end"}, "resolution": {"spatial_resolution_m"},
                                      "unit": {"unit_of_analysis"}}.get(a, set()) & body.keys())]
        s.scope = merged
        for p in s.picks:
            p["fit"] = fit.assess(p["meta"], s.scope)
        agent._coverage(s)
        previews.start(s)  # the study area or window changed: previews follow
        return jsonify(scope=s.scope, picks=agent._ui_picks(s), graph=s.graph)

    @app.post("/api/session/restore")
    def session_restore():
        """Bring back a conversation saved in the browser after its server session expired or the server restarted.

        A session that is still in memory is never overwritten."""
        _check_code()
        if (request.content_length or 0) > 2_000_000:
            return jsonify(error="This conversation is too large to restore."), 413
        b = request.get_json(silent=True) or {}
        sid = str(b.get("sid") or "")
        live = session.peek(sid) if sid else None
        if live:
            return jsonify(sid=live.sid, restored=False)
        s = session.get(sid or None)
        if not s.lock.acquire(blocking=False):
            return jsonify(error="Busy"), 409
        try:
            agent.restore(s, b)
        finally:
            s.lock.release()
        return jsonify(sid=s.sid, restored=True)

    @app.post("/api/session/<sid>/reset")
    def reset(sid):
        session.reset(sid)
        return jsonify(ok=True)

    @app.get("/api/session/<sid>/graph.graphml")
    def graphml(sid):
        s = session.peek(sid)
        if not s or not s.graph:
            return jsonify(error="No graph yet"), 404
        return Response(_graphml(s.graph, s.scope), mimetype="application/graphml+xml",
                        headers={"Content-Disposition": "attachment; filename=yfinder_influence_graph.graphml"})

    @app.get("/api/layers/<lid>.geojson")
    @app.get("/api/layers/<lid>/<name>.geojson")  # same file under a readable name: GeoLibre names layers by file
    def layer(lid, name=None):
        p = session.layer_path(lid)
        if not p:
            abort(404)
        resp = send_file(p, mimetype="application/geo+json")
        resp.headers["Access-Control-Allow-Origin"] = "*"  # GeoLibre and other viewers load these by URL
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    @app.get("/api/handbook")
    def handbook():
        ref = request.args.get("ref", "")
        hb = handbooks.get(ref)
        if hb:
            return jsonify(handbook=hb)
        if request.args.get("generate") == "1" and handbooks.can_generate():
            _check_code()
            limit = _allow_turn()
            if limit:
                return jsonify(error=limit), 429
            it = catalog.load().get(ref)
            s = session.peek(request.args.get("sid", "")) if request.args.get("sid") else None
            meta = it or (s.seen.get(ref) if s else None)
            if not meta:
                return jsonify(error="Unknown dataset"), 404
            try:
                return jsonify(handbook=handbooks.generate(ref, meta))
            except Exception as e:  # model or network failure
                return jsonify(error=f"Could not write a handbook: {str(e)[:200]}"), 502
        return jsonify(error="No handbook yet", can_generate=handbooks.can_generate()), 404

    @app.get("/api/handbook.md")
    def handbook_md():
        ref = request.args.get("ref", "")
        p = handbooks._path(ref)
        if not handbooks.get(ref):
            abort(404)
        return send_file(p, mimetype="text/markdown", as_attachment=True, download_name=f"{handbooks.slug(ref)}-SKILL.md")

    @app.get("/api/handbook.toml")
    def handbook_toml():
        hb = handbooks.get(request.args.get("ref", ""))
        if not hb:
            abort(404)
        return Response(handbooks.to_llmfind_toml(hb), mimetype="application/toml",
                        headers={"Content-Disposition": f"attachment; filename={handbooks.slug(hb['ref'])}.toml"})

    @app.post("/api/demo/<demo_id>/load")
    def demo_load(demo_id):
        """Start a live session at the end state of a recorded demo, so the viewer can keep asking.

        Inline demo layers are saved to this server's layer store (so GeoLibre can load them by URL);
        the response maps recorded layer ids to the new descriptors for the replay."""
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", demo_id):
            abort(404)
        p = STATIC_DIR / "demos" / f"{demo_id}.json"
        if not p.exists():
            abort(404)
        demo = json.loads(p.read_text(encoding="utf-8"))
        layer_map = {}
        for e in (ev for st in demo["steps"] for ev in st.get("events", []) if ev["type"] == "layer"):
            d = e["layer"]
            if d.get("kind") == "geojson" and d["id"] in demo.get("layers", {}) and d["id"] not in layer_map:
                layer_map[d["id"]] = session.save_layer(demo["layers"][d["id"]], d["title"], d.get("source", ""), d.get("style", "default"))
        fs = demo["final_state"]
        s = session.get(None)
        s.graph, s.scope, s.picks, s.methods = fs["graph"], fs["scope"] or {}, fs["picks"], fs["methods"]
        s.next_steps = fs.get("next_steps", [])
        s.prev_response_id, s.model, s.turns = fs.get("prev_response_id"), fs.get("model"), 1
        s.citations = set()
        s.layers = [layer_map.get(l["id"], l) for l in fs.get("layers", [])]
        for p_ in s.picks:  # make the demo's sources count as already seen
            s.seen[p_["ref"]] = p_.get("meta") or {}
        return jsonify(sid=s.sid, layers=layer_map)

    watch.register(app, _check_code, _allow_turn)
    return app


def _extract_text(name: str, raw: bytes) -> str:
    low = name.lower()
    if low.endswith(".pdf"):
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(raw))
            return "\n".join((p.extract_text() or "") for p in reader.pages[:40])
        except Exception as e:
            raise ValueError(f"Could not read this PDF ({type(e).__name__})")
    if low.endswith(".docx"):
        import docx
        try:
            d = docx.Document(io.BytesIO(raw))
            return "\n".join(p.text for p in d.paragraphs)
        except Exception as e:
            raise ValueError(f"Could not read this Word file ({type(e).__name__})")
    if low.endswith((".txt", ".md", ".rtf", ".tex")):
        return raw.decode("utf-8", errors="ignore")
    raise ValueError("Use a PDF, DOCX, TXT or MD file")


def _graphml(g: dict, scope: dict) -> str:
    """GraphML with the attribute names Research Architect reads (label, description, data_coverage, rationale)."""
    keys = [("label", "node"), ("description", "node"), ("role", "node"), ("observable", "node"), ("core", "node"),
            ("data_coverage", "node"), ("rationale", "edge")]
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">']
    for k, dom in keys:
        out.append(f'  <key id="{k}" for="{dom}" attr.name="{k}" attr.type="string"/>')
    out.append('  <key id="question" for="graph" attr.name="question" attr.type="string"/>')
    out.append('  <key id="scope" for="graph" attr.name="scope" attr.type="string"/>')
    out.append('  <graph id="G" edgedefault="directed">')
    out.append(f'    <data key="question">{escape(g.get("question", ""))}</data>')
    out.append(f'    <data key="scope">{escape(json.dumps(scope or {}))}</data>')
    for n in g["nodes"]:
        out.append(f'    <node id="{escape(n["id"])}">')
        vals = {"label": n["label"], "description": n.get("observable", ""), "role": n.get("role", ""),
                "observable": n.get("observable", ""), "core": str(bool(n.get("core"))).lower(),
                "data_coverage": n.get("data_coverage", "uncovered")}
        for k, v in vals.items():
            out.append(f'      <data key="{k}">{escape(str(v))}</data>')
        out.append("    </node>")
    for i, e in enumerate(g["edges"]):
        out.append(f'    <edge id="e{i}" source="{escape(e["source"])}" target="{escape(e["target"])}">'
                   f'<data key="rationale">{escape(e.get("note", ""))}</data></edge>')
    out.append("  </graph>")
    out.append("</graphml>")
    return "\n".join(out)


app = None
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8770"))
    create_app().run(host="127.0.0.1", port=port, threaded=True, debug=False)
