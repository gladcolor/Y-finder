"""Browser smoke test of the page itself, without model calls:  python scripts/ui_smoke.py [--url URL] [--shots DIR]

Starts the app on a free local port (unless --url is given), opens it in headless Chromium (Playwright) and does what
a visitor does: the welcome screen, a recorded demo, the cost line, a Yes and a No with a comment, the data guide, a
reload, the design notes and a phone-sized screen. Exits 1 when a check fails or the page throws a script error.

With --url the votes are skipped (they would land in the live feedback file). --shots DIR saves two pictures of the
finished demo (chat with the map, and the Data tab) for the README.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOISE = re.compile(r"Failed to load resource|net::ERR|WebGL|GPU stall|tile|favicon", re.I)  # network and graphics chatter
failures: list[str] = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        failures.append(what)


def serve():
    """The app on a free local port, with a throwaway runtime folder and nothing that calls out (previews, browser, model)."""
    tmp = tempfile.mkdtemp(prefix="yf_smoke_")
    os.environ.update(YFINDER_RUNTIME_DIR=tmp, YFINDER_PREVIEWS="0", YFINDER_BROWSER="0", YFINDER_EXPLORE="0",
                      YFINDER_JANITOR="0", YFINDER_LINKCHECK="0")
    sys.path.insert(0, str(ROOT))
    import logging

    from werkzeug.serving import make_server
    logging.getLogger("werkzeug").setLevel(logging.ERROR)   # no request log between the checks

    from yfinder.app import create_app
    srv = make_server("127.0.0.1", 0, create_app(), threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}/", Path(tmp)


def rows(tmp):
    p = tmp / "feedback" / "feedback.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def main():
    for stream in (sys.stdout, sys.stderr):   # the cost line has a cent sign; Windows consoles are not always UTF-8
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="test a deployed copy instead of a local one (votes are skipped)")
    ap.add_argument("--shots", help="folder for two pictures of the finished demo")
    ap.add_argument("--demo", default="", help="demo id to replay (default: the first card)")
    ap.add_argument("--settle", type=float, default=2, help="seconds to wait before the pictures (previews draw on a live site)")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    base, tmp = (a.url.rstrip("/") + "/", None) if a.url else serve()
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        ctx = b.new_context(viewport={"width": 1600, "height": 900})
        ctx.add_init_script("try { sessionStorage.setItem('yf_demo_speed', '100'); } catch (e) {}")  # replay at once
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"script error: {e}"))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and not NOISE.search(m.text) else None)

        page.goto(base)
        page.wait_for_selector("#demos .demo-card")
        check(page.locator(".welcome-foot a[href='design']").count() == 1, "welcome links to the design notes")
        card = page.locator(f"#demos .demo-card[data-id='{a.demo}']") if a.demo else page.locator("#demos .demo-card").first
        if a.demo and not card.count():   # a demo that sits under "More demos"
            page.locator("details.more-demos summary").first.click()
            card = page.locator(f".demo-card[data-id='{a.demo}']").first
        card.click()
        page.wait_for_selector(".demo-end", timeout=180_000)
        n_bot = page.locator(".msg.bot").count()
        check(n_bot > 0 and page.locator(".msg.bot .fb").count() == n_bot, f"a Yes/No row under each of the {n_bot} answers")
        cnt = page.locator(".msg.bot .act .cnt").first.inner_text()
        check(bool(re.search(r"\d+k? tokens · (about (\d+¢|\$\d)|under 1¢) · recorded", cnt)), f"cost line reads '{cnt.strip()}'")

        if tmp:  # votes: local runs only
            page.locator(".msg.bot .fb button[data-v=up]").first.click()
            page.wait_for_function("document.querySelector('.msg.bot .fb').textContent.trim() === 'Thanks.'")
            r = rows(tmp)
            check(len(r) == 1 and r[0]["vote"] == "up" and r[0]["turn"] == 0 and bool(r[0]["demo"]) and bool(r[0]["id"])
                  and r[0]["model"].startswith("gpt-"), "Yes is stored with its turn, demo and model")

        page.click(".tabs button[data-tab=data]")
        page.wait_for_selector("#btnGuide")
        with ctx.expect_page() as pi:
            page.click("#btnGuide")
        g = pi.value
        g.wait_for_load_state()
        text = g.inner_text(".page")
        check(text.startswith("Data guide:"), "the data guide opens in its own tab")
        heads = [h.strip() for h in g.locator("h2").all_inner_texts()]
        check(all(h in heads for h in ("Scope", "Factors behind the question", "Data")), f"guide sections: {heads}")
        check(bool(re.search(r"Fit: (fits your scope|partly fits your scope|a poor fit for your scope|not checked)", text)), "each dataset has a fit line")
        check(not re.search(r"undefined|\[object|NaN", text), "no undefined values in the guide")
        check(g.locator("#copy").count() == 1 and g.locator("#src").text_content().startswith("# Data guide:"), "the guide can be copied as Markdown")
        g.close()

        page.reload()
        page.wait_for_selector(".msg.bot .fb")
        check(page.locator(".msg.bot").count() == n_bot and page.locator(".msg.bot .fb").count() == n_bot,
              "a reloaded conversation shows every answer again, each with its Yes/No row")
        if tmp:
            last = page.locator(".msg.bot .fb").last
            last.locator("button[data-v=down]").click()
            last.locator("input").fill("smoke test: what was missing")
            last.locator("input").press("Enter")
            page.wait_for_function("[...document.querySelectorAll('.msg.bot .fb')].pop().textContent.trim() === 'Thanks.'")
            r = rows(tmp)
            check(len(r) == 3 and r[1]["vote"] == r[2]["vote"] == "down" and r[1]["id"] == r[2]["id"] and r[1]["id"] != r[0]["id"]
                  and r[2]["comment"].startswith("smoke test") and bool(r[2]["question"]) and r[2]["turn"] >= r[0]["turn"],
                  "No and its comment share one id and name their own turn")

        if a.shots:
            out = Path(a.shots)
            out.mkdir(parents=True, exist_ok=True)
            page.click(".tabs button[data-tab=chat]")
            page.wait_for_timeout(int(a.settle * 1000))
            page.screenshot(path=str(out / "screenshot.jpg"), type="jpeg", quality=88)
            page.click(".tabs button[data-tab=data]")
            page.wait_for_timeout(600)
            page.screenshot(path=str(out / "screenshot_data.jpg"), type="jpeg", quality=88)
            print(f"      pictures saved in {out}")

        d = ctx.new_page()
        resp = d.goto(base + "design")
        check(resp.status == 200 and d.locator("h1").inner_text() == "Y-finder design notes", "the design notes open")
        d.close()

        phone = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        pp = phone.new_page()
        pp.on("pageerror", lambda e: errors.append(f"script error (phone): {e}"))
        pp.goto(base)
        pp.wait_for_selector("#demos .demo-card")
        w = pp.evaluate("[document.documentElement.scrollWidth, innerWidth]")
        check(w[0] <= w[1] + 1, f"no sideways scrolling on a phone ({w[0]} px wide in a {w[1]} px screen)")
        b.close()

    check(not errors, "no script errors" + (": " + " | ".join(errors[:5]) if errors else ""))
    print("PASS" if not failures else f"{len(failures)} check(s) failed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
