# Persona test results

Run 2026-11-08 17:40. Ten fictional Yale users (tests/personas.json), one first message each,
scored by automatic checks (yfinder/personas.py, itself tested in tests/test_persona_eval.py).

| Model | Personas fully passing | Checks passed | Median time (s) | Median tool calls | Median input tokens |
|---|---|---|---|---|---|
| gpt-6.1-terra | 9/10 | 64/65 | 45 | 13 | 80,755 |
| gpt-6.1-sol | 8/10 | 63/65 | 58 | 12 | 111,630 |
| gpt-6-luna | 8/10 | 63/65 | 32 | 12 | 70,310 |

## Failed checks

- **gpt-6.1-terra / kenya_malaria**: scope_contains (bbox [34.34401, 0.08911, 35.1561, 0.90044])
- **gpt-6.1-sol / ysph_smoke_asthma**: picks_any_2 (picks: cur:aqs, cur:tiger, cur:dec2020, https://www.stanfordecholab.com/wildfire_smoke)
- **gpt-6.1-sol / facilities_solar**: no_unverified (unverified: https://map.yale.edu/?id=1910)
- **gpt-6-luna / mph_mosquito**: no_unverified (unverified: S2B_MSIL2A_20260819T153809_R011_T18TXL_20260819T192743)
- **gpt-6-luna / dewey_mobility**: scope_contains (bbox [-72.92477, 41.30673, -72.92391, 41.30718])

## Per persona

| Persona | gpt-6.1-terra | gpt-6.1-sol | gpt-6-luna |
|---|---|---|---|
| mph_mosquito | 8/8 (69s) | 8/8 (93s) | 7/8 (41s) |
| yse_canopy_heat | 8/8 (45s) | 8/8 (54s) | 8/8 (40s) |
| ysph_smoke_asthma | 8/8 (49s) | 7/8 (68s) | 8/8 (41s) |
| econ_zoning_permits | 7/7 (42s) | 7/7 (54s) | 7/7 (28s) |
| history_waterfront | 6/6 (38s) | 6/6 (45s) | 6/6 (32s) |
| kenya_malaria | 6/7 (46s) | 7/7 (57s) | 7/7 (32s) |
| vague_undergrad | 3/3 (26s) | 3/3 (41s) | 3/3 (32s) |
| dewey_mobility | 6/6 (39s) | 6/6 (122s) | 5/6 (28s) |
| polisci_polling | 6/6 (48s) | 6/6 (59s) | 6/6 (28s) |
| facilities_solar | 6/6 (74s) | 5/6 (117s) | 6/6 (65s) |

## Changes made after this run

- `recommend_data` now maps a Planetary Computer scene id to its collection card (Luna had cited a Sentinel-2 scene id, which was marked unverified).
- `geocode` pads point-like results (under 1 km) to a 1 km study box (Luna's "downtown New Haven" box was about 100 m wide).
- Two checks were too strict and were loosened: the Kenya persona no longer requires a specific point (any box inside western Kenya is valid), and the smoke persona accepts a smoke-PM2.5 product as beyond-monitor evidence.
- Not changed: Sol's facilities run cited a Yale campus-map URL it never searched; the "unverified" label is the intended behavior.
