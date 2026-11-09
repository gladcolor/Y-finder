"""Web search for the agent and the handbook writer.

Default (when a DeepSeek key is set): DeepSeek's Search and Fetch API, offered to the model as two function tools,
web_search and read_web_page. Search costs $0.75 per 1,000 successful requests and Fetch the same (no token charge;
empty results and failures are free), against $10 per 1,000 calls plus content tokens for OpenAI's built-in tool.
The GPT models still do the reasoning. Without a DeepSeek key, or with YFINDER_WEB_SEARCH=openai, OpenAI's built-in
web_search tool is used as before.

Every URL a search returns is recorded with the session, so picks from those pages count as "From a web source".
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

from .config import ROOT

SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
FETCH_URL = "https://api.deepseek.com/v1/tools/fetch"
MAX_PAGE_CHARS = 8000
_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{16,}")


def deepseek_key() -> str | None:
    """DEEPSEEK_API_KEY from the environment (the server's root-only env file), else the git-ignored local file."""
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env:
        return env.strip()
    for c in (os.environ.get("YFINDER_DEEPSEEK_KEY_FILE"), ROOT / "Deepseek_key.txt"):
        if c and Path(c).is_file():
            m = _KEY_RE.search(Path(c).read_text(encoding="utf-8", errors="ignore"))
            if m:
                return m.group(0)
    return None


def provider() -> str:
    choice = os.environ.get("YFINDER_WEB_SEARCH", "").lower()
    if choice in ("deepseek", "openai"):
        return "deepseek" if choice == "deepseek" and deepseek_key() else "openai"
    return "deepseek" if deepseek_key() else "openai"


class WebError(Exception):
    pass


def _post(url: str, body: dict, timeout: int) -> dict:
    key = deepseek_key()
    if not key:
        raise WebError("Web search is not configured")
    for attempt in range(2):
        try:
            r = requests.post(url, json=body, timeout=timeout,
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        except requests.RequestException as e:
            if attempt:
                raise WebError(f"Web search did not answer ({type(e).__name__})")
            time.sleep(1)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (408, 429, 500, 502, 503, 504) and not attempt:  # retry once, as DeepSeek advises
            time.sleep(1.5)
            continue
        if r.status_code == 404 and url == FETCH_URL:
            raise WebError("That page could not be read (unavailable, blocked or no text)")
        if r.status_code == 402:
            raise WebError("The web search account is out of credit")
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = ""
        raise WebError(f"Web search error {r.status_code}{': ' + msg[:120] if msg else ''}")
    raise WebError("Web search did not answer")


def search(query: str, count: int = 8, question: str | None = None) -> list[dict]:
    q = re.sub(r"\s+", " ", query or "").strip()[:128]
    if not q:
        raise WebError("Empty search query")
    lang = os.environ.get("YFINDER_WEB_LANG", "en")
    if lang and "lang:" not in q and len(q) + len(lang) + 6 <= 128:
        q = f"{q} lang:{lang}"  # English pages unless the query names a language (YFINDER_WEB_LANG="" turns this off)
    body = {"query": q, "count": max(1, min(int(count or 8), 15))}
    if question and question.strip():
        body["ori_question"] = question.strip()[:128]
    out = []
    for r in _post(SEARCH_URL, body, 30).get("results") or []:
        if not isinstance(r, dict) or not str(r.get("url", "")).startswith(("http://", "https://")):
            continue
        out.append({"title": (r.get("title") or "")[:200], "url": r["url"], "site": r.get("site_name") or "",
                    "date": (r.get("date") or "")[:10], "snippet": (r.get("snippet") or "")[:240],
                    "summary": (r.get("summary") or "")[:800], "authority": r.get("authority"),
                    "score": r.get("rerank_score")})
    return out


def fetch(url: str) -> dict:
    url = (url or "").strip().split("#")[0]  # fragments never reach the server
    if not re.match(r"^https?://[^/@\s]+", url) or len(url) > 8000:
        raise WebError("Give a full http(s) link")
    page = _post(FETCH_URL, {"url": url}, 45)
    content = page.get("content") or ""
    return {"title": (page.get("title") or "")[:200], "url": url, "content": content[:MAX_PAGE_CHARS],
            "truncated": len(content) > MAX_PAGE_CHARS}


# ---------------------------------------------------------------------------- tool definitions
def _fn(name, description, props):
    return {"type": "function", "name": name, "description": description, "strict": True,
            "parameters": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}}


FUNCTION_TOOLS = [
    _fn("web_search", "Search the live web. Returns titles, URLs, dates and summaries. Supports site:, lang:, after:, before:, "
        "quotes and minus operators, e.g. 'street tree inventory site:newhavenct.gov'. Use for anything the catalog and "
        "the live data sources do not cover.", {
            "query": {"type": "string", "description": "1 to 128 characters"},
            "count": {"type": "integer", "description": "number of results, 3 to 10"}}),
    _fn("read_web_page", "Read one web page (returned as text, first 8,000 characters). Use before quoting a number, a date "
        "or an access rule from a page.", {"url": {"type": "string"}}),
]


def tool_specs() -> list[dict]:
    return FUNCTION_TOOLS if provider() == "deepseek" else [{"type": "web_search"}]


def run_tool(name: str, args: dict, question: str | None = None) -> tuple[dict, list[dict]]:
    """Run a web tool; returns (result for the model, pages found [{url, title}])."""
    if name == "web_search":
        res = search(args.get("query", ""), max(3, min(int(args.get("count") or 8), 10)), question)
        pages = [{"url": r["url"], "title": r["title"]} for r in res]
        slim = [{k: v for k, v in r.items() if k not in ("snippet", "score") and v not in ("", None)} for r in res]
        return {"query": args.get("query"), "results": slim or [], "note": None if res else "No results; try other words"}, pages
    if name == "read_web_page":
        p = fetch(args.get("url", ""))
        return p, [{"url": p["url"], "title": p["title"]}]
    raise WebError("Unknown web tool")


def respond(client, model: str, prompt: str, max_output_tokens: int = 5000, max_steps: int = 8) -> str:
    """One answer with web access, for callers outside the chat loop (the handbook writer)."""
    if provider() != "deepseek":
        r = client.responses.create(model=model, input=prompt, tools=[{"type": "web_search"}],
                                    reasoning={"effort": "low"}, max_output_tokens=max_output_tokens)
        return r.output_text or ""
    r = client.responses.create(model=model, input=prompt, tools=FUNCTION_TOOLS, reasoning={"effort": "low"},
                                max_output_tokens=max_output_tokens)
    for step in range(max_steps):
        calls = [o for o in (r.output or []) if getattr(o, "type", "") == "function_call"]
        if not calls:
            break
        outs = []
        for c in calls:
            try:
                result, _pages = run_tool(c.name, json.loads(c.arguments or "{}"))
            except (WebError, ValueError) as e:
                result = {"ok": False, "error": str(e)}
            outs.append({"type": "function_call_output", "call_id": c.call_id,
                         "output": json.dumps(result, ensure_ascii=False)[:24000]})
        kw = {"tool_choice": "none"} if step == max_steps - 1 else {}
        r = client.responses.create(model=model, previous_response_id=r.id, input=outs, tools=FUNCTION_TOOLS,
                                    reasoning={"effort": "low"}, max_output_tokens=max_output_tokens, **kw)
    return r.output_text or ""
