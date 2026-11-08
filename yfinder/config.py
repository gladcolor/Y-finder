"""Runtime settings. Everything can be overridden with environment variables."""
from __future__ import annotations

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
