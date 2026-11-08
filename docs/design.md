# Y-finder design

## Intent

YCGS receives more and more questions from students, faculty and staff who want to use geospatial data but do not know where to start. Y-finder is a conversational data guide that answers the first consult question at scale: *which data, tools and methods fit my study?* It does not stop at a search box. It helps a user turn a vague question into a scoped study, then labels how well each data source fits that scope, and hands the user to a GIS (GeoLibre) or to a YCGS consultant for the next step.

## The tri-projection principle

Y-finder follows Huan Ning's tri-projection approach from Research Architect:

| Projection | What Y-finder does | Where the user sees it |
|---|---|---|
| Real world -> influence graph | The agent names the outcome and the factors that drive, mediate or confound it, each with an observable measure. | Frame tab: graph (outcome in the center, core factors on the inner ring) |
| Influence graph -> scope | The agent sets geographic extent (geocoded), time window, needed resolution, unit of analysis and frequency. Defaults it chose are tagged "default". Users edit them. | Frame tab: scope card; study box on the map |
| Scope -> data | The agent searches and proposes data. **Rules, not the model, label fit** for area, time and detail, plus access. Graph nodes are colored by data coverage. | Data tab: fit matrix, cards with reasons and time bars; map footprints |

## Architecture

```
Browser (static/index.html, app.js)          Flask app (yfinder/app.py), one process, gunicorn threads
  MapLibre map + Esri imagery toggle   <-->  /api/chat (server-sent events) -> agent.run_turn()
  Chat with tool chips + inline cards          OpenAI Responses API (GPT-6.1 Terra by default), strict function tools
  Frame (Cytoscape graph, scope editor)        tool handlers -> catalog.py (BM25 over 1,123 cards)
  Data (fit matrix, cards, handbooks)                         -> sources.py (live: Planetary Computer STAC, Yale Dataverse,
  Catalog (direct search, themes)                                Yale GeoBlacklight, ArcGIS Online, Data.gov, Nominatim,
  Full GIS (GeoLibre iframe, data= URLs)                         Overpass, Census TIGERweb)
  Demo cases (recorded replays)                               -> fit.py (deterministic scope-fit labels)
                                                              -> session.py (state + GeoJSON layer store, served with CORS)
                                               handbooks.py (Agent-Skill style access handbooks), watch.py (opt-in follow-ups)
```

### Agent tools

`update_influence_graph`, `geocode`, `set_scope`, `search_catalog`, `search_stac_items`, `search_yale_dataverse`, `search_yale_geoblacklight`, `search_arcgis_online`, `search_datagov`, built-in `web_search`, `map_stac_preview`, `map_osm_features`, `map_feature_service`, `map_census_boundaries`, `recommend_data`, `offer_choices`, `get_access_handbook`. All function tools use strict JSON schemas. Tool results go back to the model; UI events (graph, scope, layer, recommendations, choices) stream to the browser.

### Grounding and verification

- Every recommended dataset must come from a tool result in the same conversation. `recommend_data` resolves each reference to a catalog card, a live search result, or a page returned by web search. Anything else is labeled **"Not verified"** in the UI, and the model is told to search for it or drop it.
- Catalog metadata wins over model claims. The model may fill missing resolution or dates only for web results, and the card says "some details from the model".
- Fit labels (`fit.py`): extent by bounding-box coverage, time by window overlap (ongoing records cover near-future windows), resolution by cell size against the needed detail or by reporting-unit hierarchy (block < block group < tract < town < county < state), access by class (open, Yale, registration, paid). Overall fit is the worst known label.

### Data

- `yfinder/data/curated.json`: 48 hand-checked dataset cards (Census, health, hazards, air quality, Connecticut layers, Yale-licensed sources such as Planet, Esri Business Analyst, PolicyMap, Social Explorer, SimplyAnalytics, L2, CoreLogic, EagleView, Dewey Data, Yale Dataverse, Yale GeoBlacklight), 13 tools and 19 methods with Yale access notes.
- `scripts/build_catalog.py` harvests Planetary Computer (139 collections) and the Earth Engine STAC catalog (904 active collections) into `yfinder/data/catalog.json` with extent, dates and resolution (899 cards carry a resolution).
- Search: BM25 with field weights, a synonym table, sensor-name tokens (`sentinel2`, `landsat8`), a boost for curated cards, and scope-aware re-ranking.

### Interaction design (what the demo shows)

- Google-Maps-like layout: full map, floating search-and-chat panel, layers button, satellite toggle. The panel collapses to a search bar and widens for the graph and data views.
- Progress stepper (Frame, Scope, Data, Map) lights up as the agent works.
- Chat shows each tool step as a chip and adds inline visual cards (graph, scope, map layer with a footprint thumbnail, recommendation summary), similar to chat apps that render charts inline.
- Quick replies: the agent ends with one question and offers 2 to 4 clickable answers.
- Data tab: fit matrix, cards with reasons, a time bar (data years against the study window), Coverage button (draws the dataset's footprint against the study area), handbook, imagery preview.
- Scope editor: change dates, detail or unit, and every fit label is re-checked instantly without an AI call.
- Full GIS: the same layers open in GeoLibre (`web.geolibre.app?data=...&layout=compact&panels=collapsed`), with an "All menus" switch.
- Proposal upload (PDF, DOCX, TXT), GraphML export to Research Architect, consultation summary for a YCGS consultant, opt-in follow-up searches.
- Demo cases: six realistic cases recorded from real runs, replayed with narration, then continued live.

## Testing

| Layer | What | Where |
|---|---|---|
| Unit and API | fit rules, search quality, tool handlers, URL safety, uploads, GraphML escaping, watches, demo loading | `tests/` (pytest, no model calls) |
| Mutation check ("test the tests") | breaks nine safeguards one at a time; the suite must fail for each | `scripts/mutation_check.py` |
| Persona runs | ten fictional Yale users, three models, automatic checks | `scripts/run_personas.py`, `docs/persona_results.md` |
| Check tests | every persona check must pass a good record and fail a bad one | `tests/test_persona_eval.py` |
| Demo cases | six scripted cases with checks; recordings must replay cleanly | `scripts/record_demos.py`, `tests/test_demo_files.py`, `docs/demo_case_results.md` |
| Browser | live UI on the deployed server via Chrome | manual, see handoff.md |

## Safety, privacy and cost

- The OpenAI key lives in a git-ignored file locally and in a root-only `/etc/yfinder.env` on the server.
- Rate limits per IP and per day, at most 14 model calls per turn, 6,000-character messages, 12 MB uploads.
- Model-chosen URLs: HTTPS only, public IPs only (no private, loopback or link-local hosts), ArcGIS REST pattern only, where-clause whitelist, Overpass tag filter whitelist and area cap.
- No accounts. Sessions live in memory for six hours. Follow-up watches store data only after explicit consent and can be stopped or deleted from their private link.
- The interface states that it is an independent prototype, not an official Yale or YCGS service. No Yale logo on the public deployment.

## Not built (on purpose, for now)

- A new repository or data portal: data stays with Yale Library, Yale Dataverse, ArcGIS Online and the original providers.
- User accounts and saved projects.
- Embedding search and LLM re-ranking (next step; BM25 plus scope-aware ranking is enough for the prototype and keeps cost near zero).
- Running generated download code on the server.
