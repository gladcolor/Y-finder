"""Data access handbooks, written like Anthropic Agent Skills (one folder per source with a SKILL.md).

Each handbook tells a person or an agent how to get one dataset: access route, endpoint,
authentication, a short Python example and known pitfalls. They extend LLM-Find's
handbooks (TOML) and can be exported back to that format with to_llmfind_toml().

Handbooks are written by scripts/write_handbooks.py when compute and tokens are available,
or on demand from the UI when YFINDER_HANDBOOK_ONDEMAND=1. Generated handbooks start as
status: draft; a person changes it to reviewed after testing the example.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import os
import re
import threading

from .config import ROOT, openai_key

HB_DIR = ROOT / "handbooks"
_lock = threading.Lock()
HANDBOOK_MODEL = os.environ.get("YFINDER_HANDBOOK_MODEL", "gpt-6.1-terra")
REQUIRED = ["## What it is", "## How to get access", "## Programmatic access", "## Python example", "## Pitfalls", "## Sources checked"]

PROMPT = """Write a data access handbook for one dataset, in the style of an Agent Skill.
Readers: Yale researchers new to GIS, and coding agents (like LLM-Find) that must download the data.

Dataset (from the Y-finder catalog):
{meta}

Use web search to confirm the current official access route, endpoint URLs, authentication and file formats.
Prefer official documentation. Do not guess endpoints; if you cannot confirm one, say so.

Return Markdown only, with exactly these sections in this order:
## What it is            (2 to 3 sentences: content, coverage, resolution, update cycle)
## How to get access     (open download, free registration, API key, or Yale license; Yale route if relevant)
## Programmatic access   (bullets: base URL or API, key parameters, authentication via environment variables, formats, rate limits)
## Python example        (one ```python block, under 40 lines, that downloads a small sample for a bounding box or place
                          and saves it; read any key from an environment variable; no notebooks magic)
## Pitfalls              (3 to 6 bullets: units, projections, missing data, terms of use, common errors)
## Sources checked       (bullets with the URLs you used)
Plain English, short sentences. Never include real API keys."""


def slug(ref: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", ref or "").strip("-").lower()
    return s[:80] or "dataset"


def _path(ref: str):
    return HB_DIR / slug(ref) / "SKILL.md"


def _parse(text: str) -> dict:
    meta, body = {}, text
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"')
        body = m.group(2)
    return {"meta": meta, "markdown": body.strip()}


def get(ref: str) -> dict | None:
    p = _path(ref)
    if not p.exists():
        return None
    d = _parse(p.read_text(encoding="utf-8"))
    d["ref"] = ref
    d["path"] = str(p.relative_to(ROOT)).replace("\\", "/")
    return d


def count() -> int:
    return len(list(HB_DIR.glob("*/SKILL.md"))) if HB_DIR.exists() else 0


def can_generate() -> bool:
    return os.environ.get("YFINDER_HANDBOOK_ONDEMAND", "0") == "1" and bool(openai_key())


def validate(markdown: str) -> list[str]:
    """Return problems; an empty list means the handbook is usable."""
    problems = [f"missing section {h}" for h in REQUIRED if h not in markdown]
    code = re.findall(r"```python\n(.*?)```", markdown, re.S)
    if not code:
        problems.append("no python example")
    else:
        try:
            ast.parse(code[0])
        except SyntaxError as e:
            problems.append(f"python example does not parse: {e.msg} (line {e.lineno})")
    if re.search(r"sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}", markdown):
        problems.append("contains something that looks like a secret key")
    if not re.search(r"https?://", markdown.split("## Sources checked")[-1]):
        problems.append("no source URLs")
    return problems


def _describe(meta: dict) -> str:
    keys = ("title", "provider", "description", "url", "access", "region", "time", "unit", "type")
    lines = [f"- {k}: {meta.get(k)}" for k in keys if meta.get(k)]
    if meta.get("res_m"):
        lines.append(f"- resolution_m: {meta['res_m']}")
    if meta.get("stac"):
        lines.append(f"- STAC collection on Planetary Computer: {meta['stac'].get('collection')}")
    if meta.get("gee_id"):
        lines.append(f"- Earth Engine id: {meta['gee_id']}")
    if meta.get("yale_note"):
        lines.append(f"- Yale note: {meta['yale_note']}")
    return "\n".join(lines)


def generate(ref: str, meta: dict, model: str | None = None) -> dict:
    from openai import OpenAI

    model = model or HANDBOOK_MODEL
    client = OpenAI(api_key=openai_key(), timeout=180, max_retries=1)
    prompt = PROMPT.format(meta=_describe(meta))
    md, problems = "", ["not generated"]
    for attempt in range(2):
        from . import websearch  # DeepSeek Search/Fetch when configured, else OpenAI's web search
        md = websearch.respond(client, model, prompt if attempt == 0 else
                               prompt + "\n\nYour previous draft had these problems; fix them:\n- " + "\n- ".join(problems),
                               max_output_tokens=5000).strip()
        md = re.sub(r"^```(?:markdown)?\n|\n```$", "", md)
        md = re.sub(r"[?&]utm_source=openai", "", md)  # drop tracking parameters from cited links
        problems = validate(md)
        if not problems:
            break
    desc = f"How to access {meta.get('title')} ({meta.get('provider') or 'source'}). Use when a study needs this dataset."
    front = ["---", f"name: {slug(ref)}", f'description: "{desc.replace(chr(34), chr(39))}"', f"ref: {ref}",
             f"source_url: {meta.get('url') or ''}", f"generated_by: {model}", f"generated_on: {dt.date.today().isoformat()}",
             "status: " + ("draft" if not problems else "needs-fix"), "---", ""]
    title = f"# Access handbook: {meta.get('title')}\n\n"
    if problems:
        title += "> Automatic checks flagged: " + "; ".join(problems) + "\n\n"
    text = "\n".join(front) + title + md + "\n"
    with _lock:
        p = _path(ref)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return get(ref)


def to_llmfind_toml(hb: dict) -> str:
    """Convert a handbook to LLM-Find's TOML handbook format (data_source_name, brief_description, handbook, code_example)."""
    md = hb["markdown"]

    def section(name):
        m = re.search(rf"## {name}.*?\n(.*?)(?=\n## |\Z)", md, re.S)
        return m.group(1).strip() if m else ""
    code = re.findall(r"```python\n(.*?)```", md, re.S)
    name = hb["meta"].get("description", hb["ref"]).replace("How to access ", "").split(" (")[0]
    brief = section("What it is").split("\n")[0]
    rules = "\n".join(l.lstrip("-* ").strip() for l in (section("Programmatic access") + "\n" + section("Pitfalls")).splitlines() if l.strip())
    rules += "\nThe download code is only in a function named 'download_data()'. The last line is to execute this function."

    def q(s):  # TOML multi-line literal strings cannot contain '''
        return s.replace("'''", "' ' '")
    # json.dumps gives a valid TOML basic string (quotes, apostrophes and backslashes escaped)
    return (f"data_source_name = {json.dumps(name, ensure_ascii=False)}\n\nbrief_description = '''\n{q(brief)}\n'''\n\n"
            f"handbook = '''\n{q(rules)}\nThis is a program for your reference: {{code_example}}\n'''\n\n"
            f"code_example = '''\n{q(code[0] if code else '')}\n'''\n")
