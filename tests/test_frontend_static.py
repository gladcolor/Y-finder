"""Static checks of the front end (there is no JavaScript test runner on this workstation).

Every element the script looks up by id must exist in index.html or in a template the script builds, scripts
from CDNs must be pinned to a version, and the page must not load a script twice."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "app.css").read_text(encoding="utf-8")


def test_every_id_the_script_uses_exists():
    used = set(re.findall(r"""\$\(\s*['"]#([A-Za-z][\w-]*)""", JS))
    defined = set(re.findall(r"""\bid=["']([A-Za-z][\w-]*)["']""", HTML)) | set(re.findall(r"""\bid=\\?["']([A-Za-z][\w-]*)""", JS))
    missing = sorted(used - defined)
    assert not missing, f"ids used in app.js but defined nowhere: {missing}"


def test_cdn_scripts_are_pinned_and_loaded_once():
    srcs = re.findall(r"""<script[^>]+src=["']([^"']+)["']""", HTML)
    cdn = [s for s in srcs if s.startswith("http")]
    assert cdn, "expected CDN scripts"
    for s in cdn:
        assert re.search(r"[@/]\d+\.\d+(\.\d+)?/", s + "/"), f"unpinned CDN script: {s}"
    assert len(srcs) == len(set(srcs)), "a script is loaded twice"


def test_dark_text_on_light_and_no_leftover_debug():
    assert "console.log(" not in JS, "remove debug logging"
    assert "debugger" not in JS
    # every colour token the CSS uses is defined on :root
    used = set(re.findall(r"var\(--([\w-]+)", CSS + JS))
    defined = set(re.findall(r"--([\w-]+)\s*:", CSS))
    fallback_ok = {"muted", "ink"}  # used with a fallback value where they appear
    assert not (used - defined - fallback_ok), f"undefined CSS variables: {sorted(used - defined - fallback_ok)}"


def test_no_control_characters_in_source():
    """A patch once turned the regex word boundary into a backspace character, so "hours" never matched."""
    bad = []
    for p in list((ROOT / "yfinder").glob("*.py")) + list((ROOT / "tests").glob("*.py")) + list((ROOT / "scripts").glob("*.py")) + \
            [ROOT / "static" / "app.js", ROOT / "static" / "app.css", ROOT / "static" / "index.html"]:
        for i, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
            if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", line):
                bad.append(f"{p.name}:{i}")
    assert not bad, bad
