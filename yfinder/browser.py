"""Y-finder's own browser: a headless Chromium that opens a dataset's page the way a person would.

Some data pages only show their file links after the page's scripts run, put the file behind a Download button, or
load a data file for their own web map. When the plain download pass in previews.py finds nothing, previews hand the page
to this browser. It reports the data files and map services it finds; the files themselves are fetched by
downloads.fetch, which checks every redirect hop and the 100 MB cap, so the same safety rules apply.

It never signs in, never types into forms, never accepts terms and stops at robot checks; those pages are left to the
user with a plain reason. One page at a time on the whole server, in one worker thread that owns the browser, which is
closed after five idle minutes. Progress goes to the card through a callback ("Opening zenodo.org", "Clicking Download").
Turned off with YFINDER_BROWSER=0 (tests, recordings) or when Playwright is not installed.
"""
from __future__ import annotations

import functools
import logging
import os
import queue
import re
import shutil
import tempfile
import threading
import time
from urllib.parse import urlparse

from . import sources

log = logging.getLogger("yfinder.browser")

PAGE_MS = 25_000          # first load of the page
SETTLE_MS = 6_000         # then wait this long for the page's scripts to fetch what they need
CLICK_MS = 9_000          # a Download button has this long to start a download
IDLE_SECONDS = 300        # close the browser after five idle minutes
MAX_SAVED = 100 * 1024 * 1024
MAX_BUTTONS = 3
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36 "
      "Y-finder/0.1 (+https://app.researcharchitect.ai/y-finder/)")

DATA_EXT = (".csv", ".xlsx", ".zip", ".geojson", ".json", ".kml", ".kmz")
RASTER_EXT = (".tif", ".tiff", ".nc", ".nc4", ".h5", ".hdf", ".grib", ".grib2", ".zarr", ".img")
FORMAT_Q = re.compile(r"(?:^|&)(?:f|format|outputformat|output|type|filetype)=(csv|geojson|json|kml|kmz|shapefile|shp|zip|xlsx)\b", re.I)
BAD_NAME = re.compile(r"method|technical|appendix|readme|codebook|dictionary|template|report|press|summary|glossary|metadata|"
                      r"manifest|config|package|schema|style|sprite|font|i18n|locale|translation|analytics", re.I)
GOOD_NAME = re.compile(r"result|score|data|indicator|value|estimate|table|count|records|layer|boundar|points", re.I)
FMT_TEXT = re.compile(r"\b(csv|geojson|xlsx|excel|shapefile|kml|kmz|zip)\b", re.I)
ROBOT = re.compile(r"verify you are (a )?human|are you a robot|checking your browser|just a moment|unusual traffic|captcha|"
                   r"access denied|attention required", re.I)
SERVICE = re.compile(r"^(https://[^?#]+/rest/services/[^?#]+?/(?:FeatureServer|MapServer))(?:/(\d+))?(?:[/?#]|$)", re.I)


def service_layer(url: str) -> str | None:
    """An ArcGIS FeatureServer or MapServer layer address (layer 0 when the page names only the service)."""
    m = SERVICE.match(url or "")
    return f"{m.group(1)}/{m.group(2) or 0}" if m else None
SIGNIN_PATH = re.compile(r"/(login|log-in|signin|sign-in|auth|sso|oauth|cas|account/login)\b", re.I)
SIGNIN_HOSTS = ("urs.earthdata.nasa.gov", "accounts.google.com", "login.microsoftonline.com", "identity.dataspace.copernicus.eu")

# buttons and links worth one click: they name a download or a format, never a sign-in, terms or purchase
_FIND_BUTTONS = r"""(maxN) => {
  const out = [];
  const els = Array.from(document.querySelectorAll('button, a, [role=button], input[type=button]'));
  for (const e of els) {
    const r = e.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const t = (e.innerText || e.value || e.getAttribute('aria-label') || e.title || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length > 40) continue;
    if (!/\b(download|export|get (the )?data|csv|geojson|shapefile|kml)\b/i.test(t)) continue;
    if (/sign ?in|log ?in|register|subscribe|buy|purchase|agree|accept|terms|request/i.test(t)) continue;
    e.setAttribute('data-yf-btn', String(out.length)); out.push(t.slice(0, 30));
    if (out.length >= maxN) break;
  }
  return out;
}"""
_ANCHORS = r"""() => Array.from(document.querySelectorAll('a[href]')).slice(0, 4000).map(a =>
  [a.href, (a.innerText || a.title || a.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 80), a.hasAttribute('download')])"""


def available() -> bool:
    if os.environ.get("YFINDER_BROWSER", "1") == "0":
        return False
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------- ranking what the page offers
def link_score(name: str, text: str = "", year: int | None = None, download_attr: bool = False) -> int:
    score = (3 if GOOD_NAME.search(name) else 0) - (6 if BAD_NAME.search(name) else 0)
    score += 2 if name.endswith((".csv", ".xlsx", ".geojson")) else 0
    score += 1 if year and str(year) in name else 0
    score += 2 if download_attr else 0
    score += 1 if FMT_TEXT.search(text or "") else 0
    return score


def rank(anchors: list, loaded: list, year: int | None = None) -> tuple[list[str], list[str]]:
    """File links worth trying (best first) and raster files the page offers (reported, not drawn)."""
    cands: dict[str, int] = {}
    rasters = []
    for item in anchors:
        href, text, dl = (list(item) + ["", False])[:3]
        if not isinstance(href, str) or not href.startswith("https://"):
            continue
        u = urlparse(href)
        if "/preview/" in u.path:
            continue  # a viewer page for the file, not the file
        path = u.path.lower()
        name = path.rsplit("/", 1)[-1]
        if path.endswith(RASTER_EXT):
            rasters.append(href)
            continue
        fmt = FORMAT_Q.search(u.query or "")
        by_ext = path.endswith(DATA_EXT)
        by_text = bool(dl) or bool(re.search(r"download|export", path)) and bool(FMT_TEXT.search(text or "") or fmt)
        if not (by_ext or fmt or by_text):
            continue
        s = link_score(name or (text or "").lower(), text, year, bool(dl))
        if path.endswith(".json") and "geo" not in path and not fmt:
            s -= 4  # plain JSON is usually page configuration, not data
        cands[href] = max(cands.get(href, -99), s)
    for href in loaded:  # files the page's own map loaded
        cands[href] = max(cands.get(href, -99), 4)
    ranked = [u for u, s in sorted(cands.items(), key=lambda kv: -kv[1]) if s > -3]
    return ranked, rasters[:5]


@functools.lru_cache(maxsize=512)
def _public(host: str) -> bool:
    return sources._public_host(host)


def allowed(url: str) -> bool:
    """Requests the page may make: public hosts only (no localhost, private networks or cloud metadata)."""
    if url.startswith(("data:", "blob:", "about:")):
        return True
    try:
        u = urlparse(url)
    except ValueError:
        return False
    return u.scheme in ("https", "http") and bool(u.hostname) and _public(u.hostname)


# ---------------------------------------------------------------------------- the worker thread that owns the browser
_jobs: "queue.Queue[tuple]" = queue.Queue()
_busy = threading.Event()
_st: dict = {"thread": None, "pw": None, "browser": None}
_start_lock = threading.Lock()


def ahead() -> int:
    """Jobs ahead of a new one: the one running plus the queue."""
    return _jobs.qsize() + (1 if _busy.is_set() else 0)


def submit(url: str, progress, done, year: int | None = None, alive=lambda: True) -> int:
    """Queue a page. progress(text) reports steps; done(found) is called on its own thread with the result."""
    n = ahead()
    _jobs.put((url, progress, done, year, alive))
    with _start_lock:
        if not (_st["thread"] and _st["thread"].is_alive()):
            _st["thread"] = threading.Thread(target=_worker, name="yfinder-browser", daemon=True)
            _st["thread"].start()
    return n


def _worker():
    while True:
        try:
            url, progress, done, year, alive = _jobs.get(timeout=IDLE_SECONDS)
        except queue.Empty:
            _close()
            continue
        host = urlparse(url).hostname or ""
        if not alive():
            continue  # the session ended while the job waited
        _busy.set()
        try:
            found = find(url, progress, year)
        except Exception as e:  # a crashed page must not stop the queue
            log.exception("browser job failed: %s", url)
            found = {"host": host, "wall": "error", "error": type(e).__name__, "links": [], "files": [], "services": [], "rasters": []}
            _close()
        finally:
            _busy.clear()
        threading.Thread(target=done, args=(found,), name="yfinder-browser-done", daemon=True).start()


def _browser():
    b = _st["browser"]
    if b is not None and b.is_connected():
        return b
    from playwright.sync_api import sync_playwright
    if _st["pw"] is None:
        _st["pw"] = sync_playwright().start()
    _st["browser"] = _st["pw"].chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--disable-gpu", "--no-first-run",
                                                                     "--disable-background-networking", "--mute-audio"])
    return _st["browser"]


def _close():
    b, pw = _st["browser"], _st["pw"]
    _st["browser"] = _st["pw"] = None
    for fn in ((lambda: b.close()) if b else None, (lambda: pw.stop()) if pw else None):
        if fn:
            try:
                fn()
            except Exception:
                pass


# ---------------------------------------------------------------------------- one page
def wall(page) -> str | None:
    """'robot' for a robot check, 'signin' for a sign-in page, else None."""
    try:
        if page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='challenges.cloudflare.com'], "
                        "#challenge-running, #cf-challenge-running").count():
            return "robot"
        title = page.title() or ""
        body = page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 1500)") or ""
    except Exception:
        return None
    if ROBOT.search(title) or (len(body) < 1500 and ROBOT.search(body)):
        return "robot"
    u = urlparse(page.url)
    if (u.hostname or "") in SIGNIN_HOSTS or SIGNIN_PATH.search(u.path or ""):
        return "signin"
    try:
        if page.locator("input[type=password]").count():
            return "signin"
    except Exception:
        pass
    return None


def _cookies(ctx) -> dict[str, str]:
    jar: dict[str, list[str]] = {}
    try:
        for c in ctx.cookies():
            jar.setdefault(c["domain"].lstrip("."), []).append(f"{c['name']}={c['value']}")
    except Exception:
        pass
    return {d: "; ".join(v) for d, v in jar.items()}


def find(url: str, progress=lambda _t: None, year: int | None = None) -> dict:
    """Open url and report what a person could download there."""
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import TimeoutError as PWTimeout
    host = urlparse(url).hostname or ""
    out = {"host": host, "wall": None, "links": [], "files": [], "services": [], "rasters": [], "cookies": {}}
    if not sources.safe_url(url):
        out["wall"] = "blocked"
        return out
    loaded, services = [], []

    def on_response(resp):
        try:
            u = resp.url
            svc = service_layer(u)
            if svc:
                if svc not in services:
                    services.append(svc)
                return
            if not u.startswith("https://") or resp.status != 200:
                return
            ct = (resp.headers.get("content-type") or "").lower()
            disp = (resp.headers.get("content-disposition") or "").lower()
            path = urlparse(u).path.lower()
            if "geo+json" in ct or path.endswith((".geojson", ".kml", ".kmz")) or "text/csv" in ct or "attachment" in disp:
                loaded.append(u)
        except Exception:
            pass

    def route(r):
        req = r.request
        if not allowed(req.url):
            return r.abort()
        if req.resource_type in ("image", "media", "font"):
            return r.abort()  # faster, and nothing to read there
        return r.continue_()

    ctx = _browser().new_context(accept_downloads=True, user_agent=UA, viewport={"width": 1280, "height": 900}, locale="en-US")
    ctx.set_default_timeout(CLICK_MS)
    try:
        ctx.route("**/*", route)
        page = ctx.new_page()
        page.on("response", on_response)
        progress(f"Opening {host} in Y-finder's browser")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=PAGE_MS)
        except (PWTimeout, PWError):
            out["wall"] = "unreachable"
            return out
        try:
            page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
        except PWTimeout:
            pass
        w = wall(page)
        if w:
            out["wall"] = w
            return out
        progress(f"Reading {host} for data files")
        anchors = page.evaluate(_ANCHORS)
        for a in anchors:  # "View data source" links to the map service behind a portal page
            svc = service_layer(a[0]) if a and isinstance(a[0], str) else None
            if svc and svc not in services:
                services.append(svc)
        links, rasters = rank(anchors, loaded, year)
        if not links and not services:
            labels = page.evaluate(_FIND_BUTTONS, MAX_BUTTONS) or []
            for i, label in enumerate(labels):
                progress(f"Clicking “{label}” on {host}")
                got = _click(page, page.locator(f"[data-yf-btn='{i}']").first, out, progress)
                if got:
                    links = got
                    break
                if out["files"]:
                    break
                if wall(page):
                    out["wall"] = wall(page)
                    break
                # the button may have opened a menu of formats or a panel with file links
                links, more = rank(page.evaluate(_ANCHORS), loaded, year)
                rasters += more
                if links or services:
                    break
        out.update(links=links[:6], services=services[:3], rasters=rasters[:5], cookies=_cookies(ctx))
        return out
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def _click(page, loc, out, progress) -> list[str]:
    """Click once; a download that starts is reported by address (fetched later with the usual checks), or saved when
    the page made it itself (blob or data address)."""
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import TimeoutError as PWTimeout
    try:
        with page.expect_download(timeout=CLICK_MS) as info:
            loc.click(timeout=4000)
        dl = info.value
    except (PWTimeout, PWError):
        return []
    u = dl.url or ""
    name = re.sub(r"[^\w.\-]+", "-", dl.suggested_filename or "download")[-80:]
    if u.startswith("https://"):
        try:
            dl.cancel()
        except Exception:
            pass
        return [u] if allowed(u) else []
    if u.startswith(("blob:", "data:")):
        progress(f"Saving {name}, made by the page")
        d = tempfile.mkdtemp(prefix="yf-browser-")
        dest = os.path.join(d, name)
        try:
            t = time.time()
            dl.save_as(dest)
            if os.path.getsize(dest) > MAX_SAVED or time.time() - t > 60:
                raise ValueError("too large")
            out["files"].append({"path": dest, "name": name, "dir": d})
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
    return []
