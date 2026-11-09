# Y-finder handoff

Read this first if you are a coding agent (or a person) picking up the project. Keep it current: add to the
decision log and commit log when you change something important.

## Status (2026-11-08)

- **Live:** https://app.researcharchitect.ai/y-finder/ (health: `/y-finder/healthz`).
- **Repo:** https://github.com/gladcolor/Y-finder (private). Local folder: `F:\OneDrive_Emory\OneDrive - Emory\Research_doc\Y-finder`.
- **Purpose:** working prototype for Huan Ning's answer to the YCGS written question on an *interactive data guide*
  (Lead, Geospatial AI & ML interview, Yale Job 138060WD). The answer text lives in
  `E:\OneDrive_Huan\OneDrive\Job_hunting\2026\Yale\written_exam.md` (Huan edits it himself; do not overwrite it).
- **Tests:** `python -m pytest -q` (103 pass, no model calls); `scripts/mutation_check.py` (all 9 injected faults caught);
  personas 190/195 checks across 3 models (Terra 9/10 personas fully, Sol 8/10, Luna 8/10; fixes after the run listed in
  `docs/persona_results.md`); demo cases 40/40 checks (`docs/demo_case_results.md`).
- **Reviewed:** Codex CLI (gpt-6.1-sol) reviewed the code; all 15 findings fixed with regression tests (time-fit bug for
  same-month windows, SSRF via redirects, watch races, session-lock races, unverified picks counting as coverage, TOML escaping, ...).
- **Browser-tested** on the live URL in Chrome: welcome, live chat turn, Frame and Data tabs, demo replays (habitat, zoning,
  waterfront, smoke), GeoLibre hand-off with public layer URLs, live follow-up after a demo.

## Human input log (decisions made by Huan)

| Date | Input | What was done |
|---|---|---|
| 2026-11-08 | Build the data guide app from the written answer: AI agent "Y-finder", extends LLM-Find, tri-projection (real world -> influence graph -> scope -> data, as in Research Architect), web search + internal catalog (STAC, Earth Engine, ArcGIS Online, Data.gov, Dewey Data, Yale Dataverse), GeoLibre as the GIS, Google-Maps-like UI, chat with map visualization, data catalog. | Built as described. |
| 2026-11-08 | Use GPT-6.1 Terra or Sol, also test GPT-6 Luna. Key in the project folder. | Model picker; default Terra. Key read from git-ignored `OpenAI_key.txt` locally, `/etc/yfinder.env` on the server. |
| 2026-11-08 | Save in this folder, write handoff.md, create a GitHub repo and push. | Private repo `gladcolor/Y-finder` (make public with `gh repo edit gladcolor/Y-finder --visibility public --accept-visibility-change-consequences`). |
| 2026-11-08 | Deploy on the Research Architect server, a URL like researcharchitect.ai/y-finder if possible. | Deployed at `app.researcharchitect.ai/y-finder/`. The root domain `researcharchitect.ai` is a landing page hosted elsewhere (see Research Architect SERVER_MAINTENANCE.md: "Do not repoint"), so the path lives on the app subdomain. A `y-finder.researcharchitect.ai` subdomain would need a Cloudflare DNS record (Huan's dashboard); the origin certificate already covers `*.researcharchitect.ai`. |
| 2026-11-08 | Make up users and use cases from YCGS, its projects and Yale research; test the tests; Codex CLI and browser tools allowed. | 10 fictional personas (`tests/personas.json`), check tests (`tests/test_persona_eval.py`), mutation check, Codex review (15 findings, all fixed), Chrome testing on the live URL. |
| 2026-11-08 | Y-finder can search sources and write data access handbooks (like Anthropic Skills) when compute is available. | `handbooks/<id>/SKILL.md`, `scripts/write_handbooks.py`, on-demand writing in the UI, export to LLM-Find TOML. 22 drafts written. |
| 2026-11-08 | With consent, keep searching and inform users periodically. | Opt-in watches (`yfinder/watch.py`), daily systemd timer, private link + Atom feed; email only if SMTP is configured (it is not). |
| 2026-11-08 | Focus this version on the demo: UI, data and user interaction. | Stepper, inline cards, quick replies, fit matrix, time bars, coverage preview, scope editor, catalog themes, consultation summary. |
| 2026-11-08 | Add a Yale logo for the interview. | **Not added to the public site** (Yale restricts its marks; a Yale logo on researcharchitect.ai could read as endorsement). Instead: interview banner, Yale Blue theme, "independent prototype" note, and a local-only slot: put an image at `static/brand/logo.png` to show it when running locally (folder is git-ignored and excluded from deploys). |
| 2026-11-08 | The UI is crowded with too much text; make it concise. | Answers capped at about 90 words by the prompt (demo answers now 29 to 78 words, were about 250); one collapsible status line per answer instead of tool chips; result pills (Graph, Scope, Map, Data) instead of cards; tabs show progress (stepper removed); two-line welcome with a 2x3 demo grid and three short starters; one-line data cards that expand on click; shorter banner, demo personas and notes. |
| 2026-11-08 | Enlarge the chat box like ChatGPT and show the conversation history. | Large chat by default on screens 1280 px and wider (the button toggles it, choice remembered): conversation list on the left, centered message column, ChatGPT-style composer at the bottom. Every conversation (live or demo) is saved in the browser and listed by day; reopening replays it in full, and a page reload restores the open one (it used to say "Earlier messages are not shown"). If the server session is gone, `/api/session/restore` rebuilds it so follow-ups keep their context. |
| 2026-11-08 | Use Research Architect's style for the graph. | Frame tab ported from Research Architect `app_v2/static/app_v2.js`: node colour by graph role (source green, internal sky blue, sink violet), labels below on white chips, unbundled-bezier edges, coverage as opacity (1 / 0.5 / 0.18), dagre top-down layout (cytoscape-dagre from jsdelivr; concentric fallback), hover and tap-to-focus dimming, pulse on the outcome, dot-grid canvas and legend cards. Changes: coverage fades the circle but keeps labels readable; core factors get a dark outline (Y-finder concept). |
| 2026-11-08 | Create realistic, easy-to-understand use cases to show the functions; use these synthetic cases for automatic testing. | Six demo cases (`yfinder/data/demo_cases.json`) with checks, recorded by `scripts/record_demos.py`, replayed in the app with narration, then continued live. |

## Agent decisions (change them if Huan disagrees)

- Flask + gunicorn (same stack as Research Architect on the server); vanilla JS front end (no Node on the workstation).
- Fit labels are computed by rules in `fit.py`, never by the model. Unverified picks are shown but do not count toward graph coverage.
- BM25 search with synonyms and sensor tokens instead of embeddings: zero cost, good enough (search tests pass); embeddings are the next step.
- GeoLibre is embedded by URL parameters (`data=`, `layout=compact`, `panels=collapsed`); its postMessage API is off on the public site.
- Conversation history lives in the browser (`localStorage` key `yf_conversations_v1`, 30 conversations, 2.5 MB cap, oldest dropped), not on the server: no accounts, nothing kept server-side beyond the 6-hour session. Each turn is stored as the event stream the UI received (text deltas merged, only the last graph/scope/recommendations kept), so reopening replays the same view.
- `POST /api/session/restore` rebuilds an expired session from what the browser shows (graph, scope, picks, layers, the OpenAI response id from the `done` event, and a recap of the last 4 turns). It never overwrites a live session; fit labels are recomputed by the rules, layers must be files in this server's store, and the response id must look like `resp_...`. The next message gets the Data-tab list once; the turn recap is used only when the response chain is missing or expired.
- "New conversation" no longer deletes the server session (the old one stays reopenable); deleting a conversation from the list does.
- Demo cases are recorded and replayed so interviews do not depend on live latency; `/api/demo/<id>/load` restores the end state (including the OpenAI response id) so the conversation continues live.

## How to run, test, deploy

```bash
pip install -r requirements.txt
python -m yfinder.app                       # http://127.0.0.1:8770 (key from OpenAI_key.txt or OPENAI_API_KEY)
python -m pytest -q
python scripts/mutation_check.py
python scripts/run_personas.py              # ~10 min, ~30 live runs
python scripts/record_demos.py [--only id]  # keeps the old recording if checks fail (use --force to overwrite)
python scripts/record_demos.py --slim-only  # shrink inlined layers in existing recordings
python scripts/build_catalog.py --check-urls   # rebuild yfinder/data/catalog.json (~2 min)
python scripts/write_handbooks.py --refs cur:acs,pc:naip   # or --curated
bash deploy/deploy.sh                       # sync code to the server and restart (first time: --setup)
```

## Server (shared with Research Architect)

- Host `173.212.218.20` (Contabo, Ubuntu 24.04), SSH key auth as root. Research Architect runs on 127.0.0.1:8050 and must not be disturbed.
- Y-finder: code `/opt/yfinder` (previous version kept in `/opt/yfinder.prev`), venv `/opt/yfinder/venv`, runtime data
  `/var/lib/yfinder` (layers, watches), secrets `/etc/yfinder.env` (root, 0600), user `yfinder`.
- Services: `yfinder.service` (gunicorn, 127.0.0.1:8060, 1 worker, 16 threads) and `yfinder-watch.timer` (daily 07:30).
- nginx: `/etc/nginx/snippets/yfinder.conf`, included in both server blocks of `/etc/nginx/sites-available/causalai`.
  The original vhost was backed up to `/root/causalai.nginx.bak-*`. Always run `nginx -t` before reloading.
- Logs: `journalctl -u yfinder -n 100`. Rollback: `rm -rf /opt/yfinder && mv /opt/yfinder.prev /opt/yfinder && systemctl restart yfinder`.
- Handbooks written on demand on the server stay in `/opt/yfinder/handbooks`; copy them back with
  `scp -r root@173.212.218.20:/opt/yfinder/handbooks/* handbooks/` before committing.

## Gotchas

- **Cloudflare caches .js and .css for 4 hours** whatever the origin says. The index route stamps `?v=<content hash>`
  on app.js, app.css and demo JSON, so every deploy is picked up. Keep that if you add static files.
- Codex CLI on this workstation cannot run its file tools (`codex-code-mode-host.exe` missing). For reviews, pipe the files
  in: `for f in yfinder/*.py static/app.js; do echo "== $f"; nl -ba $f; done | codex exec -s read-only "review ..."`.
- GeoLibre iframe: iframes do not stretch between `left` and `right`; the CSS sets an explicit width.
- Imagery previews: `map_stac_preview` adds same-week neighbor scenes until the study box is covered (NAIP quarter quads are small).

- `localStorage` on `app.researcharchitect.ai` is shared with Research Architect (same origin); keep Y-finder keys prefixed `yf_` and the history under its 2.5 MB budget.
- The Chrome connected to Claude Code on this workstation is on another computer, so it cannot open `127.0.0.1`. Test the UI on the deployed URL.
- Bash sandbox: SSH works; a local server started inside the sandbox is not visible outside it (use `dangerouslyDisableSandbox`).
- Never commit `OpenAI_key.txt` (git-ignored, also excluded by `deploy.sh`). Scan before pushing: `git grep -n "sk-"`.
- Public Overpass servers are slow at times; the app falls back to a mirror and caps the area at about 50 x 50 km.
- Cloudflare cuts responses that send nothing for 100 s; streaming keeps the chat alive, but a slow single tool call can still hit it.
- Some government sites block scripted URL checks (403); that does not mean the curated link is broken.
- Yale access facts on curated cards come from YCGS and Yale Library pages (checked Oct and Nov 2026). Confirm with YCGS before a real launch.

## Open items and next steps

1. Huan: decide repo visibility, and whether to add a `y-finder.researcharchitect.ai` DNS record.
2. Review the 22 draft handbooks (run each example, set `status: reviewed`).
3. Build the evaluation set from real (de-identified) YCGS consult questions; report hit@5 and MRR (see literature review).
4. Add embedding search and an OGC API Records / CKAN harvester for Yale and Connecticut portals.
5. Optional SMTP settings for watch emails (`YFINDER_SMTP_HOST`, `YFINDER_SMTP_FROM`, ...).
6. "Ask YCGS" could post the consultation summary to a staff queue instead of copy and paste.

## Commit log (important commits)

See `git log --oneline`. Add a line here for any commit that changes behavior users see.

- `8e58c88` (2026-11-08) First prototype: agent, catalog, fit rules, UI, handbooks, watches, tests, deploy files.
- `98b1d85` (2026-11-08) Demo cases, UI pass (stepper, quick replies, fit matrix, time bars), Codex-review and persona fixes, cache-busting.
- `cfe44ee` (2026-11-08) Concise UI: shorter answers, status line, result pills, one-line data cards.
- `c188b01` (2026-11-08) ChatGPT-style large chat, conversation history in the browser, session restore endpoint.
