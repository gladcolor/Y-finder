---
name: cur-places
description: "How to access CDC PLACES local health estimates (CDC). Use when a study needs this dataset."
ref: cur:places
source_url: https://www.cdc.gov/places/
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: CDC PLACES local health estimates

## What it is

CDC PLACES provides model-based estimates for asthma, diabetes, obesity, mental distress, and other health measures across the 50 states and DC, including at census tract and ZIP Code Tabulation Area (ZCTA) resolution. CDC updates PLACES annually; the current release identified in its release notes is December 2025. The portal also holds 2016–2019 data from the earlier **500 Cities** project, which did not have the same nationwide coverage. ([cdc.gov](https://cdc.gov/places/tools/data-portal.html))

## How to get access

The data are open. No Yale license or account is needed to download them. In the CDC PLACES Data Portal, choose a geographic level, filter if desired, and use **Export**; a free account is useful if you want to save filtered views. ([cdc.gov](https://www.cdc.gov/places/tools/explore-places-data-portal.html))

## Programmatic access

- **Socrata SODA 2 tract API:** `https://data.cdc.gov/resource/cwsq-ngmh.json`
- **Socrata SODA 2 ZCTA API:** `https://data.cdc.gov/resource/qnzd-25i4.json`  
  These dataset IDs point to the current-release tables; check the dataset pages before a repeat analysis. ([dev.socrata.com](https://dev.socrata.com/docs/endpoints))
- **Filters:** Use `$where` for fields such as `stateabbr`, `countyname`, and `measureid` in the tract table. Use `$select` to choose columns, `$limit` to cap rows, and `$offset` with a stable `$order` to page through results. The default limit is 1,000 rows. ([data.cdc.gov](https://data.cdc.gov/500-Cities-Places/PLACES-Local-Data-for-Better-Health-Census-Tract-D/cwsq-ngmh/data_preview))
- **Authentication:** Public SODA 2 queries do not require a key. For heavier use, register for an application token, store it in `SOCRATA_APP_TOKEN`, and send it in the `X-App-Token` header. ([dev.socrata.com](https://dev.socrata.com/docs/app-tokens.html))
- **Formats:** The portal offers CSV, JSON, and GeoJSON exports. Open Data format has one row per measure and geographic unit; GIS-friendly format has one row per geographic unit with measures in columns. ([cdc.gov](https://www.cdc.gov/places/tools/explore-places-data-portal.html))
- **Rate limits:** Untokened requests share an IP-based throttle. Socrata says tokened requests are not normally throttled, but abusive traffic may be; handle HTTP 429 by waiting and retrying. ([dev.socrata.com](https://dev.socrata.com/docs/app-tokens.html))

## Python example

This saves up to 100 obesity records for census tracts in New Haven County, Connecticut. Install `requests` first. ([data.cdc.gov](https://data.cdc.gov/500-Cities-Places/PLACES-Local-Data-for-Better-Health-Census-Tract-D/cwsq-ngmh/data_preview))

```python
import csv
import os
import requests

url = "https://data.cdc.gov/resource/cwsq-ngmh.json"
token = os.getenv("SOCRATA_APP_TOKEN")
headers = {"X-App-Token": token} if token else {}
params = {
    "$where": "stateabbr = 'CT' AND countyname = 'New Haven' "
              "AND measureid = 'OBESITY'",
    "$order": "locationid",
    "$limit": 100,
}

response = requests.get(url, params=params, headers=headers, timeout=60)
response.raise_for_status()
rows = response.json()

if not rows:
    raise RuntimeError("No records returned; check filters and dataset version.")

columns = sorted({key for row in rows for key in row})
with open("new_haven_obesity_sample.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)

print(f"Saved {len(rows)} records to new_haven_obesity_sample.csv")
```

## Pitfalls

- **Estimates are not individual records.** They are modeled area-level estimates, not direct counts. CDC cautions against using them to evaluate local programs or policies. ([data.cdc.gov](https://data.cdc.gov/500-Cities-Places/PLACES-Local-Data-for-Better-Health-Census-Tract-D/cwsq-ngmh/data_preview))
- **Check units and years.** Many values are percentages. The release year is not necessarily the survey year: the 2025 release uses 2023 or 2022 BRFSS data. ([cdc.gov](https://www.cdc.gov/places/current-release-notes/))
- **Missing does not mean zero.** Areas with fewer than 50 adult residents are excluded; in the 2025 release, many estimates are also unavailable for Kentucky and Pennsylvania. Inspect footnotes and confidence limits. ([cdc.gov](https://www.cdc.gov/places/about/))
- **Map joins need care.** Keep tract and ZCTA identifiers as text so leading zeroes survive. Match boundary vintage: releases from 2024 use 2020 Census geography. A centroid point is not an area boundary; join the table to a suitable boundary file and check its coordinate system before mapping. ([cdc.gov](https://www.cdc.gov/places/about/))
- **Check comparisons and attribution.** Tract and ZCTA estimates are crude, not age-adjusted. The CDC dataset page labels the tract data public domain; cite CDC and the release used. ([cdc.gov](https://www.cdc.gov/places/faqs/index.html))

## Sources checked

- CDC PLACES Data Portal and download instructions: https://www.cdc.gov/places/tools/data-portal.html ; https://www.cdc.gov/places/tools/explore-places-data-portal.html
- CDC tract and ZCTA dataset pages: https://data.cdc.gov/d/cwsq-ngmh ; https://data.cdc.gov/d/qnzd-25i4
- CDC current release notes and FAQ: https://www.cdc.gov/places/current-release-notes/ ; https://www.cdc.gov/places/faqs/index.html
- Socrata API endpoints, tokens, and limits: https://dev.socrata.com/docs/endpoints ; https://dev.socrata.com/docs/app-tokens.html ; https://dev.socrata.com/docs/queries/limit
