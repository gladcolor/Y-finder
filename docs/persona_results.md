# Persona test results

Run 2026-11-08 17:31. Ten fictional Yale users (tests/personas.json), one first message each,
scored by automatic checks (yfinder/personas.py, itself tested in tests/test_persona_eval.py).

| Model | Personas fully passing | Checks passed | Median time (s) | Median tool calls | Median input tokens |
|---|---|---|---|---|---|
| gpt-6.1-terra | 6/10 | 60/64 | 46 | 12 | 75,850 |
| gpt-6.1-sol | 1/10 | 52/64 | 53 | 11 | 110,634 |
| gpt-6-luna | 9/10 | 63/64 | 30 | 12 | 74,460 |

## Failed checks

- **gpt-6.1-terra / econ_zoning_permits**: no_unverified (unverified: https://catalog.data.gov/dataset/annual-housing-permit-data-by-town-1990-2024?from_hint=eyJxIjoiZGVwYXJ0bWVudCBvZiBob3VzaW5nICYgY29tbXVuaXR5IGRldmVsb3BtZW50In0%3D)
- **gpt-6.1-terra / history_waterfront**: picks_any (picks: https://catalog.data.gov/dataset/topo-survey-sheet-1880s-shore, https://catalog.data.gov/dataset/shoreline-data-rescue-project-of-new-haven-harbor-ct-ct1909a, https://beinecke.library.yale.edu/beinecke/community/maps-new-haven, https://nhfpl.org/wp-content/uploads/2020/12/mapswebsite.pdf)
- **gpt-6.1-terra / kenya_malaria**: no_unverified (unverified: https://malariaatlas.org/open-access-policy/, https://production-raw-data-api.s3.amazonaws.com/ISO3/KEN/populated_places/hotosm_ken_populated_places_report.html)
- **gpt-6.1-terra / dewey_mobility**: no_unverified (unverified: https://static1.squarespace.com/static/5b54cf24f2e6b103c6cba86e/t/680fc668ca94aa26c4f26e8c/1745864313080/Town%2BGreen%2BAnnual%2BReport%2B2024%2B-%2BFINAL%2BPRINT.pdf)
- **gpt-6.1-sol / mph_mosquito**: no_unverified (unverified: https://portal.ct.gov/CAES/Mosquito-Testing/Introductory/State-of-Connecticut-Mosquito-Trapping-and-Arbovirus-Testing-Program)
- **gpt-6.1-sol / yse_canopy_heat**: no_unverified (unverified: https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature, https://www.ncei.noaa.gov/access/search/data-search/daily-summaries/)
- **gpt-6.1-sol / ysph_smoke_asthma**: no_unverified (unverified: https://datadryad.org/dataset/doi:10.5061/dryad.k0p2ngfhv, https://aqs.epa.gov/aqsweb/airdata/download_files.html, https://www.census.gov/cgi-bin/geo/shapefiles/index.php?layergroup=Census+Tracts&year=2020, https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.2020.html, https://zenodo.org/records/17051313); picks_any (picks: https://datadryad.org/dataset/doi:10.5061/dryad.k0p2ngfhv, https://aqs.epa.gov/aqsweb/airdata/download_files.html, https://www.census.gov/cgi-bin/geo/shapefiles/index.php?layergroup=Census+Tracts&year=2020, https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.2020.html, h); picks_any_2 (picks: https://datadryad.org/dataset/doi:10.5061/dryad.k0p2ngfhv, https://aqs.epa.gov/aqsweb/airdata/download_files.html, https://www.census.gov/cgi-bin/geo/shapefiles/index.php?layergroup=Census+Tracts&year=2020, https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.2020.html, h)
- **gpt-6.1-sol / econ_zoning_permits**: no_unverified (unverified: https://portal.ct.gov/DECD/Content/About_DECD/Research-and-Publications/01_Access-Research/Exports-and-Housing-and-Income-Data)
- **gpt-6.1-sol / history_waterfront**: picks_any (picks: https://collections.library.yale.edu/catalog/15675092, https://nhfpl.omeka.net/collections/show/6, https://catalog.data.gov/dataset/shoreline-data-rescue-project-of-new-haven-harbor-ct-ct1909a, https://collections.library.yale.edu/catalog?f%5BcallNumber_ssim%5D%5B%5D=766+N46+1924, https://newhavenhi)
- **gpt-6.1-sol / kenya_malaria**: scope_inside (bbox [31.97313, -2.09096, 37.17313, 3.10904]); no_unverified (unverified: https://malariaatlas.org/open-access-policy/, https://production-raw-data-api.s3.amazonaws.com/ISO3/KEN/populated_places/hotosm_ken_populated_places_report.html)
- **gpt-6.1-sol / dewey_mobility**: no_unverified (unverified: https://static1.squarespace.com/static/5b54cf24f2e6b103c6cba86e/t/680fc668ca94aa26c4f26e8c/1745864313080/Town%2BGreen%2BAnnual%2BReport%2B2024%2B-%2BFINAL%2BPRINT.pdf, https://storymaps.arcgis.com/stories/18b7a2ab8ea9492a89f95456d3879883)
- **gpt-6.1-sol / polisci_polling**: no_unverified (unverified: https://portal.ct.gov/sots/election-services/voter-information/where-and-how-do-i-vote, https://hamden.com/929/Elections-Voting-2026)
- **gpt-6.1-sol / facilities_solar**: no_unverified (unverified: https://facilities.yale.edu/services/building-superintendents, https://developer.nlr.gov/docs/solar/pvwatts/v8/)
- **gpt-6-luna / kenya_malaria**: scope_inside (bbox [31.97313, -2.09096, 37.17313, 3.10904])

## Per persona

| Persona | gpt-6.1-terra | gpt-6.1-sol | gpt-6-luna |
|---|---|---|---|
| mph_mosquito | 8/8 (108s) | 7/8 (53s) | 8/8 (29s) |
| yse_canopy_heat | 8/8 (50s) | 7/8 (51s) | 8/8 (72s) |
| ysph_smoke_asthma | 8/8 (52s) | 5/8 (65s) | 8/8 (37s) |
| econ_zoning_permits | 6/7 (46s) | 6/7 (54s) | 7/7 (27s) |
| history_waterfront | 5/6 (71s) | 5/6 (50s) | 6/6 (25s) |
| kenya_malaria | 5/6 (43s) | 4/6 (56s) | 5/6 (35s) |
| vague_undergrad | 3/3 (29s) | 3/3 (39s) | 3/3 (28s) |
| dewey_mobility | 5/6 (42s) | 5/6 (57s) | 6/6 (29s) |
| polisci_polling | 6/6 (45s) | 5/6 (66s) | 6/6 (42s) |
| facilities_solar | 6/6 (34s) | 5/6 (44s) | 6/6 (32s) |
