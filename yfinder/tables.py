"""Tables without coordinates (CSV, Excel, zipped CSV) joined to boundaries for a map preview.

A table is mappable when one column holds a geography key: ISO 3166 alpha-3 country codes, US state FIPS or
postal codes, 5-digit county FIPS, or 11-digit census tract GEOIDs. Value columns are the numeric ones; up to three
are chosen by how well their names (and labels from a data dictionary sheet such as a README with code and name
columns) match the question. Sentinel values such as -9999 count as missing.
"""
from __future__ import annotations

import csv
import io
import json
import re
import threading
import zipfile

import requests

from .config import RUNTIME_DIR

NE_COUNTRIES = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson"
BOUNDARY_DIR = RUNTIME_DIR / "boundaries"
MAX_ROWS = 60000
MAX_COLS = 600
STATE_ABBR = {"AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10", "DC": "11", "FL": "12",
              "GA": "13", "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23",
              "MD": "24", "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
              "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44",
              "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54", "WI": "55",
              "WY": "56", "PR": "72"}
STOP = set("a an and are as at by for from how in into is it of on or the to with what where which who why data map show".split())
_lock = threading.Lock()


class TableError(ValueError):
    pass


# ---------------------------------------------------------------------------- reading
def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _from_csv(raw: bytes) -> list[list[str]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    try:
        dialect = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for i, r in enumerate(csv.reader(io.StringIO(text), dialect)):
        if i > MAX_ROWS:
            break
        rows.append([c.strip() for c in r[:MAX_COLS]])
    return rows


def _from_xlsx(raw: bytes) -> list[tuple[str, list[list[str]]]]:
    import openpyxl  # imported here: only spreadsheet downloads need it
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as e:  # openpyxl raises many types for damaged files
        raise TableError(f"The spreadsheet could not be read ({type(e).__name__})")
    out = []
    for ws in wb.worksheets[:12]:
        rows = []
        for i, r in enumerate(ws.iter_rows(values_only=True)):
            if i > MAX_ROWS:
                break
            rows.append([_cell(c) for c in r[:MAX_COLS]])
        out.append((ws.title, rows))
    return out


def read_tables(raw: bytes, name: str) -> list[tuple[str, list[list[str]]]]:
    """[(table name, rows)] from a CSV, an Excel workbook, or a zip of them."""
    low = name.lower()
    if low.endswith(".csv") or low.endswith(".txt"):
        return [(name, _from_csv(raw))]
    if low.endswith((".xlsx", ".xlsm")):
        return _from_xlsx(raw)
    if raw[:4] == b"PK\x03\x04" and not low.endswith((".xlsx", ".xlsm")):
        out = []
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir() and "__MACOSX" not in i.filename]
            if sum(i.file_size for i in infos) > 300 * 1024 * 1024:
                raise TableError("The zip file is too large to unpack here")
            for i in infos[:80]:
                if i.filename.lower().endswith((".csv", ".xlsx")):
                    out += read_tables(zf.read(i), i.filename)
        return out
    raise TableError("Not a table file")


def _header_row(rows: list[list[str]]) -> int:
    """The first row that looks like a header: mostly non-numeric, several filled cells."""
    for i, r in enumerate(rows[:15]):
        filled = [c for c in r if c]
        if len(filled) >= 2 and sum(not _num(c) for c in filled) >= 0.6 * len(filled):
            return i
    return 0


def _num(v) -> float | None:
    try:
        x = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return None if x != x or x <= -999 else x  # NaN and sentinels such as -9999 / -6666 are missing


def dictionary(tables) -> dict[str, str]:
    """Labels from a data-dictionary sheet: a code column (TLA, code, variable, field) next to a name column."""
    labels = {}
    for _name, rows in tables:
        if not rows:
            continue
        h = [c.lower() for c in rows[0]]
        code = next((i for i, c in enumerate(h) if c in ("tla", "code", "variable", "field", "column", "abbreviation")), None)
        desc = next((i for i, c in enumerate(h) if c in ("component", "name", "label", "description", "meaning", "indicator")), None)
        if code is None or desc is None or code == desc:
            continue
        for r in rows[1:500]:
            if len(r) > max(code, desc) and r[code] and r[desc] and len(r[code]) <= 40:
                labels.setdefault(r[code], r[desc])
    return labels


# ---------------------------------------------------------------------------- geography keys
def countries() -> dict[str, dict]:
    """ISO alpha-3 -> Natural Earth 1:50m country feature (downloaded once to the runtime cache)."""
    p = BOUNDARY_DIR / "ne_50m_admin_0_countries.geojson"
    with _lock:
        if not p.exists():
            r = requests.get(NE_COUNTRIES, timeout=60)
            r.raise_for_status()
            BOUNDARY_DIR.mkdir(parents=True, exist_ok=True)
            p.write_bytes(r.content)
    out = {}
    for f in json.loads(p.read_text(encoding="utf-8"))["features"]:
        pr = f.get("properties") or {}
        for k in ("ISO_A3", "ISO_A3_EH", "ADM0_A3"):  # France and Norway carry -99 in ISO_A3
            v = pr.get(k)
            if v and v != "-99" and v not in out:
                out[v] = {"type": "Feature", "geometry": f["geometry"], "properties": {"iso": v, "name": pr.get("NAME")}}
                break
    return out


def detect_key(header: list[str], rows: list[list[str]], iso3: set[str] | None = None) -> tuple[int, str] | None:
    """(column, kind) for the first column whose values are a geography key."""
    best = None
    for j, name in enumerate(header):
        vals = [r[j] for r in rows[:400] if len(r) > j and r[j]]
        if len(vals) < 3:
            continue
        n = name.lower()
        if iso3 and all(re.fullmatch(r"[A-Z]{3}", v) for v in vals[:200]) and sum(v in iso3 for v in vals) >= 0.6 * len(vals):
            return j, "country"
        digits = [re.sub(r"\.0$", "", v) for v in vals]
        if all(re.fullmatch(r"\d{11}", v) for v in digits[:200]):
            return j, "tract"
        if ("fips" in n or "geoid" in n or "county" in n) and all(re.fullmatch(r"\d{4,5}", v) for v in digits[:200]):
            best = best or (j, "county")
        if ("state" in n or "fips" in n or n in ("st", "abbr", "postal")) and (
                all(re.fullmatch(r"\d{1,2}", v) for v in digits[:200]) or all(v.upper() in STATE_ABBR for v in vals[:200])):
            best = best or (j, "state")
    return best


TOWN_COLUMN = re.compile(r"^((residence|death|injury|incident|host[ _]?customer|customer|property|owner|site|facility|location)[ _]?)?"
                         r"(towns?|town[ _]?name|municipalit(y|ies)|municipality[ _]name|city|city[/_ ]town|jurisdiction|community|place)$", re.I)
YEAR_NAME = re.compile(r"^[_ ]?((?:19|20)\d\d)$")


def year_columns(header: list[str]) -> list[tuple[int, int]]:
    """Wide tables with one column per year (1990, _1991, ...): [(year, column)], oldest first."""
    out = [(int(m.group(1)), j) for j, h in enumerate(header) if (m := YEAR_NAME.match(h.strip()))]
    return sorted(out) if len(out) >= 3 else []


def wide_series(header: list[str], rows: list[list[str]], label: str = "") -> dict | None:
    """The trend across year columns: the sum over rows when all values are whole numbers (counts), else the mean."""
    yc = year_columns(header)
    if not yc:
        return None
    pts, whole = [], True
    for y, j in yc:
        vals = [v for v in (_num(r[j]) for r in rows if len(r) > j) if v is not None]
        if vals:
            whole = whole and all(float(v).is_integer() for v in vals)
            pts.append([y, vals])
    if len(pts) < 3:
        return None
    agg = (lambda v: sum(v)) if whole else (lambda v: sum(v) / len(v))
    return {"label": (label or "value") + (" (sum over rows)" if whole else " (mean over rows)"),
            "points": [[y, round(agg(v), 4)] for y, v in pts][-60:]}


def detect_town(header: list[str], rows: list[list[str]], towns: set[str]) -> int | None:
    """A town-name column (Connecticut data is often by town) whose values match the towns in the area. The town of
    residence comes first (overdose records also give the towns of injury and death), then plain town columns."""
    order = sorted(range(len(header)), key=lambda j: (0 if header[j].strip().lower().startswith("residence") else
                                                      1 if re.match(r"^(towns?|town[ _]?name|municipalit|city$)", header[j].strip(), re.I) else 2, j))
    for j in order:
        name = header[j]
        if not TOWN_COLUMN.match(name.strip()):
            continue
        vals = [r[j].strip().lower() for r in rows[:400] if len(r) > j and r[j].strip()]
        if len(vals) >= 3 and sum(v in towns for v in vals) >= 0.5 * len(vals):
            return j
    return None


def preview(header: list[str], rows: list[list[str]], k: int = 6) -> dict:
    """First rows of a table for the data card (at most 8 columns, cells trimmed)."""
    cols = list(range(min(8, len(header))))
    return {"columns": [header[c][:40] for c in cols], "rows": [[(r[c] if len(r) > c else "")[:40] for c in cols] for r in rows[:k]],
            "n_rows": len(rows), "n_cols": len(header)}


_YEAR_COL = re.compile(r"^(year|yr|date|period|time|fiscal year|calendar year|data year)$", re.I)


def series(header: list[str], rows: list[list[str]], value_col: int) -> dict | None:
    """A trend line when the table has a year (or date) column: the value per year (mean over rows of that year)."""
    yc = next((j for j, h in enumerate(header) if _YEAR_COL.match(h.strip())), None)
    if yc is None:
        return None
    by: dict[int, list[float]] = {}
    for r in rows:
        if len(r) <= max(yc, value_col):
            continue
        m = re.match(r"(1[89]\d\d|20\d\d)", r[yc].strip())
        v = _num(r[value_col])
        if m and v is not None:
            by.setdefault(int(m.group(1)), []).append(v)
    if len(by) < 3:
        return None
    pts = [[y, round(sum(v) / len(v), 4)] for y, v in sorted(by.items())]
    return {"label": header[value_col], "points": pts[-60:]}


def key_value(kind: str, v: str) -> str:
    v = re.sub(r"\.0$", "", v.strip())
    if kind == "county":
        return v.zfill(5)
    if kind == "state":
        return STATE_ABBR.get(v.upper(), v.zfill(2))
    return v


def _tokens(text: str) -> set[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")  # SaleAmount -> sale amount
    return {t for t in re.findall(r"[a-z0-9.]+", text.lower().replace("₂", "2").replace(".", " ")) if t not in STOP and len(t) > 1}


SENTINELS = {9.0, 99.0, 999.0, 9999.0, 99999.0, -9.0, -99.0, -999.0, -9999.0}


def is_code_column(name: str, nums: list[float] | None = None) -> bool:
    """An identifier, code, coordinate or year column (CNTYCODE, GEOID, site_id, StationID, zip), or one full of
    placeholder values such as 999: never a measure."""
    low = name.lower().strip()
    if re.search(r"(^|[._ \-])(year|yr|rank|lat|lon|lng|latitude|longitude|x|y|id|fid|oid|key|serial|seq)($|[._ \-])", low):
        return True
    if re.search(r"code|fips|geoid|objectid|zipcode|^zip|zcta", low) or re.search(r"(ID|Id)$", name.strip()) or low.endswith("_id"):
        return True
    if nums:
        top = max(set(nums), key=nums.count)
        if top in SENTINELS and nums.count(top) >= 0.3 * len(nums):
            return True
    return False


def value_columns(header: list[str], rows: list[list[str]], key: int, labels: dict[str, str], text: str, k: int = 3) -> list[int]:
    """Numeric columns, best match to the question first (names and dictionary labels both count)."""
    want = _tokens(text)
    cands = []
    for j, name in enumerate(header):
        if j == key or not name:
            continue
        vals = [r[j] for r in rows[:400] if len(r) > j and r[j] != ""]
        nums = [x for x in (_num(v) for v in vals) if x is not None]
        if len(vals) < 3 or len(nums) < 0.6 * len(vals):
            continue
        if is_code_column(name, nums) and not labels.get(name.split(".")[0]):
            continue
        label = labels.get(name) or labels.get(name.split(".")[0]) or ""
        hay = _tokens(name + " " + label)
        low = name.lower()
        score = len(want & hay) * 2 + (1 if re.search(r"(\.new|score|value|index|total|rate|pct)$", low) else 0)
        score -= 2 if re.search(r"[._ ](rank|chg|change|diff|old|se|moe|lo|hi|lower|upper)$", low) else 0  # derived columns last
        cands.append((score, -j, j))
    cands.sort(reverse=True)
    out, bases = [], set()
    for _s, _o, j in cands:  # one column per measure: AIR.new, PMD.new, NOD.new rather than AIR.new, AIR.chg, AIR.rank
        base = header[j].split(".")[0].lower()
        if base in bases:
            continue
        bases.add(base)
        out.append(j)
        if len(out) == k:
            break
    return out


def label_for(name: str, labels: dict[str, str]) -> str:
    base = name.split(".")[0]
    lab = labels.get(name) or labels.get(base)
    return f"{lab} ({name})" if lab else name.replace("_", " ")
