# Data access handbooks

One folder per data source, each with a `SKILL.md` in the style of an Agent Skill:
YAML front matter (`name`, `description`, `ref`, `status`) and fixed sections
(What it is, How to get access, Programmatic access, Python example, Pitfalls, Sources checked).

- Written by `scripts/write_handbooks.py` in batches, or on demand from the app (`YFINDER_HANDBOOK_ONDEMAND=1`).
- New handbooks start as `status: draft`. Change to `reviewed` after a person runs the example.
- Export to LLM-Find's TOML handbook format through `/api/handbook.toml?ref=<id>` or `yfinder.handbooks.to_llmfind_toml`.
