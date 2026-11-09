# Twenty more Yale demo cases, and what inspecting them fixed (2026-11-09)

Huan asked: "create 20 more cases, you need to inspect the results." The 20 cases below are grounded in real Yale
programs. Their people are fictional, and every program link was opened. Each case was then:

1. **Recorded** with the live agent (gpt-6.1-terra) and scored by its checks in `yfinder/data/demo_cases.json`.
2. **Read in full:** every answer, influence graph, study scope, data pick and fit label.
3. **Previewed and explored:** live previews were built for every pick, and the explorer ran on them.
4. **Fixed and re-recorded:** problems were fixed in Y-finder itself (prompt, geocoder, previews, explorer, catalog),
   not in the demo. The case was then re-recorded and inspected again.

Every case ended with all of its checks passing: 38 demos in all, listed in `docs/demo_case_results.md`.

## The cases

| Case | Field | Yale program (real) | What Y-finder finds |
|---|---|---|---|
| Election swings and split tickets in Connecticut | Political science | YCGS and ISPS election-maps panel | The state's elections database, OpenElections, MIT precinct returns, town boundaries |
| Traveling circuses from wagons to rails | Architectural history | Yale GIS Accelerator circus project (Library news) | Illinois circus-routes map, Atack railroads 1826-1911 (time flagged as partial), NHGIS, a Yale route-survey map |
| Tsetse habitat near cattle and villages in Uganda | Vector biology | Aksoy Lab, YSPH | Dynamic World, FAO cattle grid (flagged as too coarse and from 2015), Overture places, GBIF, Sentinel-2 |
| Damage and displacement around El Fasher, Sudan | Humanitarian research | Humanitarian Research Lab, YSPH | VIIRS fires and Sentinel-2 (both flagged as too coarse for buildings), the Lab's reports, IOM displacement alerts |
| Endemic species and forest loss in Tanzania's Eastern Arc | Biodiversity | Map of Life | Map of Life ranges, WDPA, Hansen loss, ecoregions, GBIF |
| Where Americans say "the car needs washed" | Linguistics | Yale Grammatical Diversity Project | The project's survey maps (with a finding from its page), NHGIS, Social Explorer, county boundaries |
| Wastewater warnings before hospital visits | Environmental engineering | Peccia Lab, Yale Engineering | CDC wastewater surveillance, Connecticut hospital tables (with their years), the plant's service towns |
| Flooding, rats and leptospirosis in Salvador | Global health | Albert Ko's Pau da Lima cohort, YSPH | OpenStreetMap drains, a 15 m elevation model, CHIRPS, Planet, YCGS drones |
| The lean season in northern Bangladesh | Development economics | Economic Growth Center migration study | MODIS crop condition, CHIRPS, upazila boundaries, the Yale migration experiment's data on Dataverse |
| Did Solarize campaigns keep rooftop solar growing? | Energy economics | Gillingham's Solarize Connecticut studies | The state's solar incentive records (which flag Solarize systems), campaign dates from a research appendix, Tracking the Sun, ACS |
| Where to spread crushed rock on Illinois farms | Biogeochemistry | Yale Center for Natural Carbon Capture | SSURGO soils, PRISM, crop field boundaries, Cropland Data Layer, USGS quarries |
| Fine particles in Dwight and East Rock | Environmental justice | Yale Planetary Solutions sensor network | The Yale sensor network, PurpleAir, EPA monitors, neighborhood boundaries |
| Family forests at risk of being split up | Forest policy | Sustaining Family Forests Initiative | Connecticut and Massachusetts parcels, NLCD, PAD-US easements and fee lands, CoreLogic |
| Young forests and dry-season water in Panama | Tropical restoration | ELTI and the Agua Salud Project | Landsat, the canal's sub-watersheds, Canal Authority discharge records (via STRI), CHIRPS, NICFI |
| Lagos spreading onto farmland and wetlands | Urban science | Karen Seto's urban expansion forecasts | Landsat, the 2030 forecast grid (flagged as coarse), 30 m wetland and impervious-surface maps |
| Yale's Dura-Europos excavations on today's imagery | Archaeology | Dura-Europos archive, Yale University Art Gallery | The Gallery's archive, a Yale digital-archive story map, SkySat, CORONA film, Sentinel-2 |
| Fentanyl deaths and distance to help | Public health | YSPH overdose mapping in Connecticut | Medical examiner records, town population, syringe-service and treatment directories, town boundaries |
| Are Connecticut wildflowers blooming earlier? | Botany | Yale Peabody Museum herbarium | Peabody specimen records, GBIF, NOAA nClimGrid (the 1890-1894 gap flagged), PRISM |
| Repeated fires in the Xingu Indigenous Territory | Fire ecology | Paulo Brando's fire research, YSE | MapBiomas Fire and land cover, Hansen, JRC surface water, FUNAI territory boundaries |
| Do coastal flood zones lower home prices? | Finance | Yale SOM study of sea level rise and home prices | Every Connecticut home sale since 2001, FEMA flood zones, parcels, USGS Sandy high-water marks |

The welcome screen still features six Yale cases. The other 32 sit under "More demos", with theme chips (Health,
Environment, Cities and society, History and culture) and a search box.

## What the inspection found, and the fixes

| Problem | Seen in | Fix |
|---|---|---|
| About 12 of 20 answers opened with what is not yet known ("cannot yet be quantified", "requires analysis") | Most cases, and 11 older demos | The agent now opens with the data route: what to combine, from which key source. Limits come after. |
| "Kaberamaido District" was not found, and "Soroti District" found the district office, so the Uganda study box covered Lake Victoria | Tsetse | The geocoder prefers administrative boundaries, and retries without "District", "County" and similar words. |
| A study of "the United States" got a box spanning the globe (the Aleutians cross 180°), so every US dataset read as a partial fit | Dialect | The US geocodes to the 48 contiguous states. A box that wraps the globe east to west is refused. The catalog's US box now covers all states and territories. |
| Natural regions fell back to whole countries: Tanzania for the Eastern Arc, Panama for the canal watershed. A one-site question was widened to its district. | Map of Life, Agua Salud, Dura | Try the official or local name; take the extent of a mapped boundary layer (the tool now returns it); keep a single site's own box. |
| Open-portal tables showed "unknown" time fit | Wastewater, solar, overdose | Y-finder reads each table's first and last date (one small query), and takes its extent from the portal (data.ct.gov is Connecticut). |
| Asked to "show an image of Lekki", the agent reset the whole Lagos study to Lekki in 2026 | Lagos | Image search takes a view box; showing part of the area no longer changes the scope. |
| A picked link carried email-tracking parameters | HRL | Tracking parameters (utm_*, _hsenc, fbclid and others) are removed from web results. |
| Curated cards linking a Connecticut Open Data page loaded nothing (the browser found no file) | Overdose, home sales | Socrata dataset pages are read through the SODA API: 12,885 overdose records and 22,288 sales now load. |
| ID columns were mapped as measures: the explorer reported "railroad access" from county codes and the 999 placeholder | Circus | Code, ID, FIPS and placeholder-filled columns are never values. |
| On download pages the first plausible file won: a census sample over the railroads, lake levels over discharge | Circus, Agua Salud | Files whose name or link text names the card's topic rank first (the railroads file and the discharge file now load). |
| A zipped CSV was read without its coordinates | Weathering (USGS mines) | The CSV inside a zip is read as a CSV: 6,980 mines in the Illinois box. A large CSV is read from its first 60 MB, and the card says so. |
| One-row-per-record tables were mapped as an average per town | Solar, overdose | A table with several rows per town maps rows per town when no column matches the question, and medians otherwise. |
| Town columns named residencecity, deathcity or injurycity were not recognized | Overdose | They are now recognized, with the town of residence first. |
| The OpenStreetMap preview drew villages for a question about drains | Lepto | The rule with the most matching words wins; drains, sewers and canals have their own rule (532 features in Pau da Lima). |
| Explorer findings named every hospital "CT", or used coordinates | Wastewater | A name column must tell places apart; large point layers keep name columns among their 16 attributes. |
| A steady rise (ρ +1.00) was headlined "no steady trend" | Overdose | "rose steadily but only 1.7%". |
| The explorer matched fentanyl deaths by free-text descriptions (131 records) and missed the table's fentanyl yes/no column | Overdose | Yes/no columns are offered to the model, and large point layers keep columns that name the topic. It now finds 8,685 fentanyl-involved deaths on the portal (189 in 2015, 762 in 2024, most in 2021), clustered around downtown Hartford and New Haven. |
| A regex word boundary had been saved as a backspace character, so the word "hours" never matched (from the earlier audit) | TEMPO | Fixed; a test now fails if source files contain control characters. |

New catalog cards (17): Yale Grammatical Diversity Project, Map of Life, Dura-Europos archive, CORONA film, Peabody
botany, Tracking the Sun, MIT election returns, Connecticut home sales, Connecticut overdose deaths, CDC wastewater
surveillance, SSURGO, the 2030 urban expansion grid, Atack railroads, MapBiomas Fire, USGS mines, the Yale air-sensor
network and FAO's 2015 cattle grid.

## The older demos

Eleven older demos opened negatively and were re-recorded. Picks were compared before and after, and the better
version was kept:

- **New version kept:** trees, vending (same picks), streams, TEMPO, EPI, opinion and Congo.
- **Second attempt kept:** HGIS. The first attempt spanned the Pacific.
- **First attempt kept:** Yale-Myers Forest, because the second dropped the YCGS drone card.
- **Old version kept:**
  - Atlas: two new runs missed Yale's Digital Atlas.
  - Downtown recovery: new runs widened the downtown box to the whole city.

The streams answer now opens with a figure: 36% of Raccoon River flow and 26% of Des Moines River flow come from
ephemeral streams. The agent read this on an Iowa Wildlife Federation page; it was checked against the National
Wildlife Federation release of June 29, 2026, which gives the same numbers.

## Still out of reach

- **Earth Engine-only data:** Dynamic World, Hansen, WDPA, MapBiomas land cover, PAD-US in Earth Engine and others
  need an Earth Engine account to preview.
- **Rasters:** rasters are drawn but not measured, including the FAO cattle grid, MapBiomas Fire and the 2030
  forecast grid.
- **Registration or request:** NHGIS, the dialect survey's respondent data, PurpleAir, the Yale sensor network and
  the Dura plans need an account or a request.
- **Blocked sites:** Tracking the Sun shows a robot check, one old-style Dataverse DOI returns 400, and the MassGIS
  statewide parcels file is 7.8 GB.
- **Run-to-run variation:** the same question can bring different picks on another run (one Atlas run picked a
  Wisconsin library record). The recordings are the reviewed versions; live follow-ups can still vary.
