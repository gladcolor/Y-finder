# Y-finder

**A conversational geospatial data guide.** Describe a research question; Y-finder maps the factors behind it, agrees a study scope with you, then finds datasets, tools and methods and labels how well each one fits that scope.

Live prototype: **https://app.researcharchitect.ai/y-finder/**

Y-finder is an independent prototype by Huan Ning, prepared for the Yale Center for Geospatial Solutions (YCGS). It is not an official Yale or YCGS service.

## What it does

- **Tri-projection:** real world -> influence graph -> scope -> data. Shown as a graph, an editable scope card and a progress stepper.
- **Honest fit labels:** every dataset is checked by rules (not by the AI) for area, time window, detail and access, with plain reasons. Example: 10 m satellite imagery is a poor fit for small mosquito habitats.
- **Real catalogs:** about 3,100 cards (YCGS curated cards with Yale access routes, Microsoft Planetary Computer, Google Earth Engine and its community catalog, the layers collected in GeoLibre, World Bank indicators) plus live search of Yale and Harvard Dataverse, Yale Library GeoBlacklight, ArcGIS Online, Data.gov, US open data portals (Socrata, including Connecticut Open Data), Planetary Computer STAC and the web (DeepSeek Search and Fetch).
- **Maps and tables:** factors measured by statistics, surveys or records get tables, marked with a Table badge; tables with place codes or town names are joined to boundaries.
- **Previews:** every recommended dataset is drawn from its online service, or a small slice for the study area is downloaded (up to 100 MB, kept for the session only) and shown on the map or as a table preview with a trend line and CSV.
- **Map first:** study area, imagery previews, OpenStreetMap features, Census boundaries and dataset coverage on a Google-Maps-like map with a legend; open the same layers in GeoLibre for full GIS.
- **Access handbooks:** Agent-Skill style `SKILL.md` per source (endpoint, Python example, pitfalls), exportable to LLM-Find's TOML handbooks.
- **People in the loop:** quick replies, scope editing, consultation summary for YCGS staff, opt-in follow-up searches.
- **Demo cases:** six cases grounded in real YCGS projects and Yale programs (Urban Resources Initiative, New Haven Digital Atlas, YCGS lidar biomass, EPI, Yale Climate Opinion Maps, satellite measurement of economic activity) and six general ones, recorded from real runs and continued live.

## Run locally

```bash
pip install -r requirements.txt
# put your OpenAI key in OpenAI_key.txt (git-ignored) or set OPENAI_API_KEY;
# optional: a DeepSeek key in Deepseek_key.txt or DEEPSEEK_API_KEY for web search (else OpenAI's web search)
python -m yfinder.app            # http://127.0.0.1:8770
```

Models: GPT-6.1 Terra (default), GPT-6.1 Sol, GPT-6 Luna (picker in the top bar).

## Project layout

```
yfinder/        app.py (Flask API), agent.py (prompt, tools, loop), catalog.py (search), fit.py (scope-fit rules),
                sources.py (live connectors), session.py, handbooks.py, watch.py, personas.py (evaluation),
                previews.py (map previews), tables.py (table joins), downloads.py, janitor.py, websearch.py, geolibre.py
yfinder/data/   curated.json, catalog.json (built), demo_cases.json
static/         index.html, app.css, app.js, demos/ (recorded demo cases)
handbooks/      <source>/SKILL.md access handbooks
scripts/        build_catalog.py, write_handbooks.py, record_demos.py, run_personas.py, mutation_check.py, run_watches.py
tests/          pytest suite, personas.json
deploy/         systemd units, nginx snippet, deploy.sh
docs/           design.md, literature_review.md, persona_results.md, demo_case_results.md
```

## Tests

```bash
python -m pytest -q                    # unit, API and demo-file tests (no model calls)
python scripts/mutation_check.py       # tests the tests: every injected fault must be caught
python scripts/run_personas.py         # live: 10 fictional users x 3 models -> docs/persona_results.md
python scripts/record_demos.py         # live: re-record demo cases, scored by their checks
```

See `handoff.md` for deployment, decisions and next steps, and `docs/design.md` for the design.
