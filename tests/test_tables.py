"""Tables without coordinates joined to boundaries, and download pages turned into map previews (no network)."""
import io
import json
import zipfile

import pytest

from yfinder import catalog, downloads, previews, session, sources, tables, watch

COUNTRIES = {iso: {"type": "Feature", "properties": {"iso": iso, "name": name},
                   "geometry": {"type": "Polygon", "coordinates": [[[x, 0], [x + 1, 0], [x + 1, 1], [x, 1], [x, 0]]]}}
             for x, (iso, name) in enumerate([("PAK", "Pakistan"), ("IND", "India"), ("AFG", "Afghanistan"), ("NPL", "Nepal")])}


def _xlsx(sheets: dict) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


EPI = {"README": [["Type", "TLA", "Component"], ["EPI", "EPI", "Environmental Performance Index"], ["IssueCategory", "AIR", "Air Quality"],
                  ["Indicator", "PMD", "Ambient PM2.5"], ["Indicator", "NOD", "NO2 exposure"], ["Indicator", "TCL", "Tree cover loss"]],
       "data": [["iso", "country", "EPI.new", "AIR.new", "AIR.chg", "AIR.rank", "PMD.new", "NOD.new", "TCL.new"],
                ["PAK", "Pakistan", 30.1, 7.54, 1.74, 176, 4.2, 20.0, 50.0], ["IND", "India", 28.0, 7.89, -0.15, 175, 3.9, 25.0, 40.0],
                ["AFG", "Afghanistan", 33.1, 24.44, 0.43, 138, 40.2, 65.6, -9999], ["NPL", "Nepal", 35.0, 20.0, 1.0, 140, 22.0, 55.0, 60.0]]}


def test_reads_csv_xlsx_and_zips():
    assert tables.read_tables(b"a,b\n1,2\n", "x.csv") == [("x.csv", [["a", "b"], ["1", "2"]])]
    sheets = dict(tables.read_tables(_xlsx(EPI), "results.xlsx"))
    assert sheets["data"][1][:3] == ["PAK", "Pakistan", "30.1"] and sheets["data"][1][5] == "176"  # whole floats lose ".0"
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("t/one.csv", "iso,v\nPAK,1\n")
        zf.writestr("t/notes.pdf", "x")
    assert [n for n, _ in tables.read_tables(z.getvalue(), "bundle.zip")] == ["t/one.csv"]
    with pytest.raises(tables.TableError):
        tables.read_tables(b"%PDF", "report.pdf")


def test_dictionary_keys_and_values():
    tbls = tables.read_tables(_xlsx(EPI), "results.xlsx")
    labels = tables.dictionary(tbls)
    assert labels["AIR"] == "Air Quality" and labels["NOD"] == "NO2 exposure"
    rows = dict(tbls)["data"]
    header, body = rows[0], rows[1:]
    assert tables.detect_key(header, body, set(COUNTRIES)) == (0, "country")
    cols = tables.value_columns(header, body, 0, labels, "Where did NO2 and PM2.5 and air quality change in Pakistan?")
    assert [header[c] for c in cols] == ["AIR.new", "PMD.new", "NOD.new"]   # one per measure; rank and change columns last
    assert tables._num("-9999") is None and tables._num("1,234.5") == 1234.5
    assert tables.label_for("PMD.new", labels) == "Ambient PM2.5 (PMD.new)"
    us = [["county_fips", "name", "rate"], ["9009", "New Haven", "11.2"], ["9001", "Fairfield", "9.0"], ["9003", "Hartford", "10.1"]]
    key = tables.detect_key(us[0], us[1:])
    assert key == (0, "county") and tables.key_value("county", "9009") == "09009"
    st = [["state", "value"], ["CT", "1"], ["NY", "2"], ["MA", "3"]]
    assert tables.detect_key(st[0], st[1:]) == (0, "state") and tables.key_value("state", "ct") == "09"
    tr = [["GEOID", "v"], ["09170141300", "1"], ["09170141400", "2"], ["09170141500", "3"]]
    assert tables.detect_key(tr[0], tr[1:]) == (0, "tract")


@pytest.fixture()
def s(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "LAYER_DIR", tmp_path / "layers")
    monkeypatch.setattr(downloads, "DOWNLOAD_DIR", tmp_path / "downloads")
    watch.WATCH_DIR = tmp_path / "watches"
    monkeypatch.setattr(tables, "countries", lambda: COUNTRIES)
    ses = session.get(None)
    ses.scope = {"bbox": [60.87, 23.69, 77.84, 37.1], "time_start": "2019", "time_end": "2024"}
    ses.graph = {"question": "Where within Pakistan did NO2 and PM2.5 change?", "nodes": [{"id": "aq", "label": "Air quality"}], "edges": []}
    yield ses
    session.reset(ses.sid)


def test_download_page_is_found_downloaded_joined_and_cleaned(s, monkeypatch):
    page = """<html><a href="/files/epi2026methods.xlsx">Methods</a><a href="/files/epi2026_technicalappendix.pdf">Appendix</a>
      <a href="/files/epi2026results2026-07-07.xlsx">Results</a><a href="https://other.example/x.zip">x</a></html>"""

    class Page:
        status_code, headers, url, text = 200, {"Content-Type": "text/html"}, "https://epi.yale.edu/2026/downloads", page
    monkeypatch.setattr(previews.requests, "get", lambda url, **kw: Page())
    monkeypatch.setattr(sources, "safe_url", lambda u: True)
    got = []

    def fetch(url, dest, max_bytes=None):
        got.append(url)
        dest.write_bytes(_xlsx(EPI))
        return {"name": url.rsplit("/", 1)[-1], "bytes": 1, "content_type": "", "final_url": url}
    monkeypatch.setattr(downloads, "fetch", fetch)
    meta = catalog.load().get("cur:epi")
    r = previews.build(s, {"ref": "cur:epi", "role": "core", "title": meta["title"], "meta": meta, "factors": ["aq"], "why": "air quality"})
    assert got == ["https://epi.yale.edu/files/epi2026results2026-07-07.xlsx"]          # results ranked above methods
    assert r["status"] == "ready" and r["kind"] == "slice" and [l.get("hidden", False) for l in r["layers"]] == [False, True, True]
    lay = r["layers"][0]
    assert lay["paint"]["label"] == "Air Quality (AIR.new)" and lay["session_only"]
    props = {f["properties"]["key"]: f["properties"] for f in json.load(open(session.layer_path(lay["id"]), encoding="utf-8"))["features"]}
    assert props["PAK"]["AIR.new"] == 7.54 and set(props) == set(COUNTRIES)
    d = downloads.session_dir(s.sid)
    assert not [p for p in d.iterdir() if p.name.startswith("pv-")]                    # the raw download is not kept
    assert (d / r["csv"]).read_text().splitlines()[0] == "iso,name,AIR.new,PMD.new,NOD.new"


def test_licensed_or_linkless_pages_get_a_reason(s, monkeypatch):
    class Page:
        status_code, headers, url, text = 200, {"Content-Type": "text/html"}, "https://x.example/", "<html>no data links</html>"
    monkeypatch.setattr(previews.requests, "get", lambda url, **kw: Page())
    monkeypatch.setattr(sources, "safe_url", lambda u: True)
    r = previews.build(s, {"ref": "https://x.example/", "role": "core", "meta": {"url": "https://x.example/", "title": "X"}})
    assert r["status"] == "none"
    assert "Licensed" in previews.build(s, {"ref": "cur:planet", "role": "core", "meta": catalog.load().get("cur:planet")})["note"]
