"""Runtime settings. Everything can be overridden with environment variables."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "yfinder" / "data"
STATIC_DIR = ROOT / "static"
RUNTIME_DIR = Path(os.environ.get("YFINDER_RUNTIME_DIR", ROOT / "runtime"))

# Models offered in the UI. The first entry is the default unless YFINDER_DEFAULT_MODEL is set.
# Speeds and notes come from docs/model_tests.md (persona runs, Nov 2026).
MODELS = [
    {"id": "gpt-6.1-terra", "name": "GPT-6.1 Terra", "effort": "low", "note": "Default: steady tool use and good judgment"},
    {"id": "gpt-6.1-sol", "name": "GPT-6.1 Sol", "effort": "low", "note": "Faster, similar quality"},
    {"id": "gpt-6-luna", "name": "GPT-6 Luna", "effort": "low", "note": "Fastest and cheapest"},
]
MODEL_IDS = {m["id"] for m in MODELS}
DEFAULT_MODEL = os.environ.get("YFINDER_DEFAULT_MODEL", MODELS[0]["id"])

MAX_STEPS = int(os.environ.get("YFINDER_MAX_STEPS", "14"))          # model calls per user turn
MAX_OUTPUT_TOKENS = int(os.environ.get("YFINDER_MAX_OUTPUT_TOKENS", "6000"))
RATE_PER_HOUR = int(os.environ.get("YFINDER_RATE_PER_HOUR", "40"))  # chat turns per IP per hour
DAILY_CAP = int(os.environ.get("YFINDER_DAILY_CAP", "600"))         # chat turns per day, all users
ACCESS_CODE = os.environ.get("YFINDER_ACCESS_CODE", "")             # optional shared code; empty = open
LOG_TURNS = os.environ.get("YFINDER_LOG_TURNS", "0") == "1"         # write turn logs to runtime/logs
PUBLIC_BASE = os.environ.get("YFINDER_PUBLIC_BASE", "")             # e.g. https://app.researcharchitect.ai/y-finder/
UPLOAD_MAX_BYTES = 12 * 1024 * 1024
USER_AGENT = "Y-finder/0.1 (YCGS geospatial data guide prototype; +https://github.com/gladcolor/Y-finder)"

# OpenAI list prices in US dollars per million tokens (standard tier, November 2026). They only give the estimated
# cost shown under each answer. Override: YFINDER_PRICES='{"gpt-6.1-terra": {"input": 1, "cached": 0.05, "output": 5}}'
PRICES = {
    "gpt-6.1-terra": {"input": 1.00, "cached": 0.05, "output": 5.00},
    "gpt-6.1-sol": {"input": 2.00, "cached": 0.10, "output": 10.00},
    "gpt-6-luna": {"input": 0.10, "cached": 0.01, "output": 0.50},
}
try:
    PRICES.update(json.loads(os.environ.get("YFINDER_PRICES") or "{}"))
except ValueError:
    pass


def turn_cost(usage: dict, model: str) -> float | None:
    """Estimated cost of one turn in US dollars at list prices; None for a model without a price."""
    p = PRICES.get(model)
    if not p:
        return None
    cached = int(usage.get("cached") or 0)
    fresh = max(0, int(usage.get("input") or 0) - cached)
    return round((fresh * p["input"] + cached * p["cached"] + int(usage.get("output") or 0) * p["output"]) / 1e6, 4)

_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{20,}")


def openai_key() -> str | None:
    """Return the OpenAI key from the environment or a local, git-ignored key file."""
    env = os.environ.get("OPENAI_API_KEY")
    if env:
        return env.strip()
    candidates = [os.environ.get("YFINDER_KEY_FILE"), ROOT / "OpenAI_key.txt", ROOT / "secrets" / "openai.txt"]
    for c in candidates:
        if not c:
            continue
        p = Path(c)
        if p.is_file():
            m = _KEY_RE.search(p.read_text(encoding="utf-8", errors="ignore"))
            if m:
                return m.group(0)
    return None
