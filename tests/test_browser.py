"""Y-finder's own browser: ranking what a page offers, the hand-off from previews, walls, and the safety rules.
No real browser runs here: browser.submit is replaced by a fake that answers like the worker thread would."""
import json
import threading

import pytest

from yfinder import browser, downloads, previews, session

CT = [-73.73, 40.95, -71.78, 42.06]
GEO = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-72.9 + i * 0.01, 41.3]}, "properties": {"name": f"p{i}"}}
    for i in range(5)]}


# ---------------------------------------------------------------------------- what a page offers
def test_rank_prefers_data_files_and_skips_page_furniture():
    anchors = [["https://x.org/records/1/files/results_2024.csv?download=1", "results_2024.csv", False],
               ["https://x.org/records/1/preview/results_2024.csv", "Preview", False],
               ["https://x.org/static/config.json", "", False],
               ["https://x.org/docs/methodology.zip", "Methods", False],
               ["https://x.org/api/export?format=geojson", "GeoJSON", False],
               ["https://x.org/grid/pm25_2024.tif", "GeoTIFF", False],
               ["http://x.org/insecure.csv", "", False],
               ["https://x.org/about", "About", False]]
    links, rasters = browser.rank(anchors, ["https://x.org/map/layer.geojson"], 2024)
    assert links[0] == "https://x.org/records/1/files/results_2024.csv?download=1"
    assert "https://x.org/api/export?format=geojson" in links and "https://x.org/map/layer.geojson" in links
    assert not any("/preview/" in u or "config.json" in u or "insecure" in u or u.endswith("/about") for u in links)
    assert links.index("https://x.org/docs/methodology.zip") > links.index("https://x.org/api/export?format=geojson") \
        if "https://x.org/docs/methodology.zip" in links else True
    assert rasters == ["https://x.org/grid/pm25_2024.tif"]


def test_service_layers_from_portal_links():
    base = "https://services1.arcgis.com/abc/arcgis/rest/services/Towns/FeatureServer"
    assert browser.service_layer(base) == base + "/0"
    assert browser.service_layer(base + "/3/query?where=1%3D1&f=geojson") == base + "/3"
    assert browser.service_layer("https://example.org/data.csv") is None


def test_the_browser_only_reaches_public_hosts():
    assert not browser.allowed("http://127.0.0.1:8050/")
    assert not browser.allowed("http://169.254.169.254/latest/meta-data/")
    assert not browser.allowed("file:///etc/passwd")
    assert browser.allowed("data:text/plain,hi") and browser.allowed("blob:https://x.org/1")


def test_cookies_go_only_to_the_site_that_set_them():
    jar = {"zenodo.org": "session=abc"}
    assert downloads._cookie_for("https://zenodo.org/records/1/files/a.csv", jar) == {"Cookie": "session=abc"}
    assert downloads._cookie_for("https://www.zenodo.org/a.csv", jar) == {"Cookie": "session=abc"}
    assert downloads._cookie_for("https://evilzenodo.org/a.csv", jar) is None
    assert downloads._cookie_for("https://s3.amazonaws.com/a.csv", jar) is None


# ---------------------------------------------------------------------------- the hand-off from previews
@pytest.fixture()
def s(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "LAYER_DIR", tmp_path / "layers")
    monkeypatch.setattr(downloads, "DOWNLOAD_DIR", tmp_path / "downloads")
    monkeypatch.setattr(browser, "available", lambda: True)
    monkeypatch.setattr(previews.sources, "safe_url", lambda u: str(u).startswith("https://"))
    ses = session.get(None)
    ses.scope = {"bbox": CT, "time_start": "2020", "time_end": "2024"}
    yield ses
    session.reset(ses.sid)


def _fake_browser(monkeypatch, found: dict, steps: list):
    threads = []

    def submit(url, progress, done, year=None, alive=lambda: True):
        steps.append(("submit", url))
        progress(f"Opening {found.get('host')} in Y-finder's browser")
        steps.append(("step", "opened"))
        t = threading.Thread(target=done, args=(dict(found),))
        threads.append(t)
        t.start()
        return 0
    monkeypatch.setattr(browser, "submit", submit)
    return threads


PAGE = {"ref": "https://data.example.org/dataset/trees", "role": "core", "title": "Street trees",
        "meta": {"title": "Street trees", "url": "https://data.example.org/dataset/trees", "access_class": "open"}}


def test_a_page_with_nothing_for_plain_requests_goes_to_the_browser_and_maps_its_file(s, monkeypatch):
    monkeypatch.setattr(previews, "_landing", lambda *a: None)  # the plain pass found no links
    steps = []
    threads = _fake_browser(monkeypatch, {"host": "data.example.org", "wall": None, "links": ["https://data.example.org/files/trees.geojson"],
                                          "files": [], "services": [], "rasters": [], "cookies": {"data.example.org": "s=1"}}, steps)
    got = {}

    def fetch(url, dest, max_bytes=None, cookies=None):
        got.update(url=url, cookies=cookies)
        dest.write_text(json.dumps(GEO))
        return {"name": "trees.geojson"}
    monkeypatch.setattr(downloads, "fetch", fetch)
    s.picks = [dict(PAGE)]
    previews._one(s, s.picks[0], previews._sig(s.scope))
    for t in threads:
        t.join(5)
    r = s.previews[PAGE["ref"]]
    assert steps[0] == ("submit", "https://data.example.org/dataset/trees")
    assert r["status"] == "ready" and r["layers"][0]["n"] == 5 and "Y-finder's browser" in r["note"]
    assert got == {"url": "https://data.example.org/files/trees.geojson", "cookies": {"data.example.org": "s=1"}}
    assert r["layers"][0] in s.layers and s.browser_tries == 1


def test_progress_shows_on_the_card_while_the_browser_works(s, monkeypatch):
    monkeypatch.setattr(previews, "_landing", lambda *a: None)
    seen = {}
    monkeypatch.setattr(browser, "submit", lambda url, progress, done, year=None, alive=None: (progress("Clicking “Download” on data.example.org"),
                                                                                                seen.update(pub=previews.public(s))))
    s.picks = [dict(PAGE)]
    previews._one(s, s.picks[0], previews._sig(s.scope))
    item = seen["pub"]["items"][PAGE["ref"]]
    assert item["status"] == "pending" and item["browser"] and "Clicking" in item["step"] and seen["pub"]["running"]


@pytest.mark.parametrize("wall,words", [("signin", "sign-in"), ("robot", "robot check"), ("unreachable", "could not open")])
def test_walls_are_reported_not_crossed(s, monkeypatch, wall, words):
    monkeypatch.setattr(previews, "_landing", lambda *a: None)
    threads = _fake_browser(monkeypatch, {"host": "data.example.org", "wall": wall, "links": [], "files": [], "services": [], "rasters": []}, [])
    monkeypatch.setattr(downloads, "fetch", lambda *a, **k: pytest.fail("nothing is downloaded past a wall"))
    s.picks = [dict(PAGE)]
    previews._one(s, s.picks[0], previews._sig(s.scope))
    for t in threads:
        t.join(5)
    r = s.previews[PAGE["ref"]]
    assert r["status"] == "none" and words in r["note"]


def test_too_large_files_and_rasters_are_explained(s, monkeypatch):
    monkeypatch.setattr(previews, "_landing", lambda *a: None)

    def big(url, dest, max_bytes=None, cookies=None):
        raise downloads.DownloadError("The file is 150 MB; the limit is 100 MB")
    monkeypatch.setattr(downloads, "fetch", big)
    threads = _fake_browser(monkeypatch, {"host": "zenodo.org", "wall": None, "links": ["https://zenodo.org/records/1/files/all.zip?download=1"],
                                          "files": [], "services": [], "rasters": []}, [])
    s.picks = [dict(PAGE)]
    previews._one(s, s.picks[0], previews._sig(s.scope))
    for t in threads:
        t.join(5)
    assert "150 MB" in s.previews[PAGE["ref"]]["note"]


@pytest.mark.parametrize("pick", [
    {"ref": "gee:COPERNICUS/S2_SR_HARMONIZED", "meta": {"url": "https://developers.google.com/earth-engine/datasets"}},
    {"ref": "cur:dewey", "meta": {"url": "https://www.deweydata.io/", "access_class": "yale"}},
    {"ref": "cur:pems", "meta": {"url": "https://dot.ca.gov/pems", "access_class": "request", "access": "Free account"}},
    {"ref": "https://example.org/x", "meta": {"url": "http://example.org/x", "access_class": "open"}},
])
def test_licensed_registration_and_earth_engine_pages_never_go_to_the_browser(s, pick):
    assert not previews._browser_ok(s, dict(pick, role="core", title="x"))


def test_a_session_sends_only_a_few_pages(s):
    s.browser_tries = previews.MAX_BROWSER
    assert not previews._browser_ok(s, dict(PAGE))


def test_a_result_after_the_scope_changed_is_dropped(s, monkeypatch):
    monkeypatch.setattr(previews, "_landing", lambda *a: None)
    held = {}
    monkeypatch.setattr(browser, "submit", lambda url, progress, done, year=None, alive=None: held.update(done=done))
    s.picks = [dict(PAGE)]
    previews._one(s, s.picks[0], previews._sig(s.scope))
    s.scope = dict(s.scope, time_start="2010")
    s.previews[PAGE["ref"]] = {"status": "pending", "sig": previews._sig(s.scope)}  # a new pass started
    held["done"]({"host": "data.example.org", "wall": "signin"})
    assert s.previews[PAGE["ref"]]["status"] == "pending"
