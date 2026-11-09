# Persona test results

Run 2026-11-08 23:53. Ten fictional Yale users (tests/personas.json), one first message each,
scored by automatic checks (yfinder/personas.py, itself tested in tests/test_persona_eval.py).

| Model | Personas fully passing | Checks passed | Median time (s) | Median tool calls | Median input tokens |
|---|---|---|---|---|---|
| gpt-6.1-terra | 10/10 | 64/64 | 37 | 14 | 59,610 |

## Failed checks

None.

## Per persona

| Persona | gpt-6.1-terra |
|---|---|
| mph_mosquito | 8/8 (106s) |
| yse_canopy_heat | 8/8 (42s) |
| ysph_smoke_asthma | 8/8 (34s) |
| econ_zoning_permits | 7/7 (35s) |
| history_waterfront | 6/6 (31s) |
| kenya_malaria | 6/6 (39s) |
| vague_undergrad | 3/3 (33s) |
| dewey_mobility | 6/6 (59s) |
| polisci_polling | 6/6 (35s) |
| facilities_solar | 6/6 (39s) |
