"""Opt-in follow-up research ("Keep looking for me").

With the user's explicit consent, Y-finder stores the research question, graph and scope,
then a scheduled job (scripts/run_watches.py, run daily by a systemd timer) re-runs the
search on a cadence for a limited number of rounds. Each round records new sources and a
short preliminary note. Users read updates on a private link or an Atom feed; email is sent
only when the server has SMTP settings and the user typed an address. Users can stop a
watch or delete its data at any time.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import secrets
import smtplib
import threading
from email.message import EmailMessage
from xml.sax.saxutils import escape

from flask import Response, jsonify, request

from .config import PUBLIC_BASE, RUNTIME_DIR

WATCH_DIR = RUNTIME_DIR / "watches"
WATCH_MODEL = os.environ.get("YFINDER_WATCH_MODEL", "gpt-6-luna")
MAX_ROUNDS = 8
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,48}$")
_lock = threading.RLock()  # re-entrant: claim/stop hold it while calling save()

CONSENT_TEXT = ("I agree that Y-finder stores my research question, influence graph and scope on its server, "
                "searches again for new data on the schedule I chose, and keeps the results on a private link "
                "until I stop or delete the watch.")


def smtp_ready() -> bool:
    return all(os.environ.get(k) for k in ("YFINDER_SMTP_HOST", "YFINDER_SMTP_FROM"))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _path(token: str):
    if not _TOKEN_RE.match(token or ""):
        return None
    return WATCH_DIR / f"{token}.json"


def load(token: str) -> dict | None:
    p = _path(token)
    if not p or not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save(w: dict) -> None:
    with _lock:
        WATCH_DIR.mkdir(parents=True, exist_ok=True)
        tmp = WATCH_DIR / f".{w['token']}.tmp"
        tmp.write_text(json.dumps(w, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, WATCH_DIR / f"{w['token']}.json")


def create(s, rounds: int, cadence_days: int, email: str | None, ip: str) -> dict:
    token = secrets.token_urlsafe(18)
    question = (s.graph or {}).get("question") or getattr(s, "first_message", "") or "Research question"
    w = {
        "token": token, "created": _now().isoformat(), "status": "active",
        "consent": {"text": CONSENT_TEXT, "at": _now().isoformat(),
                    "ip_hash": hashlib.sha256((ip + token).encode()).hexdigest()[:12]},
        "question": question, "graph": s.graph, "scope": s.scope,
        "known": [{"ref": p["ref"], "title": p["title"]} for p in s.picks],
        "rounds_left": max(1, min(int(rounds), MAX_ROUNDS)), "cadence_days": 14 if int(cadence_days) >= 14 else 7,
        "next_run": (_now() + dt.timedelta(days=14 if int(cadence_days) >= 14 else 7)).isoformat(),
        "email": email if (email and smtp_ready() and re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}", email)) else None,
        "model": WATCH_MODEL, "updates": [],
    }
    save(w)
    return w


def public(w: dict) -> dict:
    d = {k: v for k, v in w.items() if k not in ("email", "consent")}
    d["email_set"] = bool(w.get("email"))
    d["consent_at"] = w["consent"]["at"]
    return d


def _prompt(w: dict) -> str:
    sc = w.get("scope") or {}
    g = w.get("graph") or {}
    uncovered = [n["label"] for n in g.get("nodes", []) if n.get("core") and n.get("data_coverage") != "covered"]
    known = "; ".join(f"{k['title']} ({k['ref']})" for k in w.get("known", [])[:40]) or "none yet"
    return (
        "[Scheduled follow-up the user asked for. They are not online; do not ask questions.]\n"
        f"Research question: {w['question']}\n"
        f"Scope: {sc.get('extent_name')} bbox {sc.get('bbox')}, {sc.get('time_start')} to {sc.get('time_end')}, "
        f"resolution {sc.get('spatial_resolution_m')} m, unit {sc.get('unit_of_analysis')}.\n"
        f"Already recommended: {known}.\n"
        f"Core factors still lacking good data: {', '.join(uncovered) or 'none'}.\n"
        "Search again: the catalog, live sources (Yale Dataverse, Data.gov, ArcGIS Online, new imagery on Planetary "
        "Computer for the scope window) and the web, looking for new releases or better-fitting sources. "
        "Call recommend_data only for sources that are not already recommended. Then write a short update: "
        "3 to 6 bullets on what is new, what it adds, and any preliminary insight (coverage, recency, a caveat). "
        "If nothing new fits, say so in one sentence."
    )


CLAIM_MINUTES = 20


def _claim(token: str) -> dict | None:
    """Mark a round as running in the watch file. Returns the watch, or None if it is not active
    or another runner (web request or the daily timer) claimed it recently."""
    with _lock:
        w = load(token)
        if not w or w.get("status") != "active":
            return None
        rs = w.get("running_since")
        if rs and _now() - dt.datetime.fromisoformat(rs) < dt.timedelta(minutes=CLAIM_MINUTES):
            return None
        w["running_since"] = _now().isoformat()
        save(w)
        return w


def run_one(token: str) -> dict | None:
    """Run one follow-up round now. Returns the update record, or None if the watch is not runnable."""
    from . import agent
    from .session import Session

    w = _claim(token)
    if not w:
        return None
    s = Session(sid="watch-" + w["token"][:10])
    s.graph = w.get("graph")
    s.scope = w.get("scope") or {}
    known = {k["ref"] for k in w.get("known", [])}
    text, err = "", None
    for ev in agent.run_turn(s, _prompt(w), model=w.get("model") or WATCH_MODEL, web=True):
        if ev["type"] == "text":
            text += ev["delta"]
        elif ev["type"] == "error":
            err = ev["message"]
    new = [{k: p.get(k) for k in ("ref", "title", "url", "source", "fit", "why", "limitations", "verified")}
           for p in s.picks if p["ref"] not in known]
    upd = {"at": _now().isoformat(), "summary": text.strip() or (err or "No update text."), "new": new,
           "tools": [t.get("name") for t in getattr(s, "last_tool_log", [])], "error": err}
    with _lock:  # re-read: the user may have stopped or deleted the watch while this round ran
        fresh = load(token)
        if not fresh or fresh.get("status") != "active":
            return None
        fresh["updates"].append(upd)
        fresh["known"] += [{"ref": p["ref"], "title": p["title"]} for p in new]
        fresh["rounds_left"] = max(0, int(fresh.get("rounds_left", 1)) - 1)
        fresh["next_run"] = (_now() + dt.timedelta(days=int(fresh.get("cadence_days", 7)))).isoformat()
        fresh.pop("running_since", None)
        if fresh["rounds_left"] == 0:
            fresh["status"] = "finished"
        save(fresh)
    _email(fresh, upd)
    return upd


def run_due(limit: int = 50) -> list[str]:
    done = []
    if not WATCH_DIR.exists():
        return done
    now = _now()
    for p in sorted(WATCH_DIR.glob("*.json")):
        try:
            w = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if w.get("status") != "active" or dt.datetime.fromisoformat(w["next_run"]) > now:
            continue
        if run_one(w["token"]):
            done.append(w["token"][:6])
        if len(done) >= limit:
            break
    return done


def _link(w: dict) -> str:
    base = PUBLIC_BASE or ""
    return f"{base}?watch={w['token']}"


def _email(w: dict, upd: dict) -> None:
    if not (w.get("email") and smtp_ready()):
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = f"Y-finder update: {w['question'][:70]}"
        msg["From"] = os.environ["YFINDER_SMTP_FROM"]
        msg["To"] = w["email"]
        body = upd["summary"] + "\n\nNew sources:\n" + "\n".join(f"- {n['title']} {n.get('url') or ''}" for n in upd["new"])
        msg.set_content(body + f"\n\nAll updates, and a link to stop: {_link(w)}\n")
        with smtplib.SMTP(os.environ["YFINDER_SMTP_HOST"], int(os.environ.get("YFINDER_SMTP_PORT", "587")), timeout=30) as smtp:
            smtp.starttls()
            if os.environ.get("YFINDER_SMTP_USER"):
                smtp.login(os.environ["YFINDER_SMTP_USER"], os.environ.get("YFINDER_SMTP_PASSWORD", ""))
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        upd["email_error"] = type(e).__name__
        save(w)


def atom(w: dict) -> str:
    link = escape(_link(w))
    items = []
    for i, u in enumerate(reversed(w["updates"])):
        new = "".join(f"<li>{escape(n['title'] or '')}</li>" for n in u["new"])
        content = escape(f"<p>{escape(u['summary'])}</p><ul>{new}</ul>")
        items.append(f"<entry><id>urn:yfinder:{escape(w['token'][:10])}:{len(w['updates']) - i}</id><title>Update {escape(u['at'][:10])}</title>"
                     f"<updated>{escape(u['at'])}</updated><link href=\"{link}\"/><content type=\"html\">{content}</content></entry>")
    upd = w["updates"][-1]["at"] if w["updates"] else w["created"]
    return ('<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">'
            f"<id>urn:yfinder:{escape(w['token'][:10])}</id><title>Y-finder: {escape(w['question'][:90])}</title>"
            f"<updated>{escape(upd)}</updated><link href=\"{link}\"/>" + "".join(items) + "</feed>")


def register(app, check_code, allow_turn):
    from . import session

    @app.post("/api/session/<sid>/watch")
    def watch_create(sid):
        check_code()
        s = session.peek(sid)
        if not s or not (s.graph or s.scope):
            return jsonify(error="Ask Y-finder about your question first"), 400
        body = request.get_json(silent=True) or {}
        if body.get("consent") is not True:
            return jsonify(error="Consent is required"), 400
        limit = allow_turn()
        if limit:
            return jsonify(error=limit), 429
        ip = (request.headers.get("CF-Connecting-IP") or request.remote_addr or "?")
        w = create(s, body.get("rounds", 4), body.get("cadence_days", 7), body.get("email"), ip)
        return jsonify(watch=public(w), link=f"?watch={w['token']}", feed=f"api/watch/{w['token']}/feed.xml")

    @app.get("/api/watch/<token>")
    def watch_get(token):
        w = load(token)
        return (jsonify(watch=public(w)) if w else (jsonify(error="Not found"), 404))

    @app.post("/api/watch/<token>/run")
    def watch_run(token):
        check_code()
        w = load(token)
        if not w or w["status"] != "active":
            return jsonify(error="This watch is not active"), 400
        limit = allow_turn()
        if limit:
            return jsonify(error=limit), 429
        upd = run_one(token)
        if upd is None:
            return jsonify(error="A round is already running, or the watch was stopped"), 409
        return jsonify(update=upd, watch=public(load(token)))

    @app.post("/api/watch/<token>/stop")
    def watch_stop(token):
        with _lock:
            w = load(token)
            if not w:
                return jsonify(error="Not found"), 404
            w["status"] = "stopped"
            w["email"] = None
            save(w)
        return jsonify(watch=public(w))

    @app.post("/api/watch/<token>/delete")
    def watch_delete(token):
        with _lock:
            p = _path(token)
            if p and p.exists():
                p.unlink()
        return jsonify(ok=True)

    @app.get("/api/watch/<token>/feed.xml")
    def watch_feed(token):
        w = load(token)
        if not w:
            return Response("Not found", 404)
        return Response(atom(w), mimetype="application/atom+xml")
