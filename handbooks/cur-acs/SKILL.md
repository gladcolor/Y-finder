---
name: cur-acs
description: "How to access American Community Survey 5-year estimates (U.S. Census Bureau). Use when a study needs this dataset."
ref: cur:acs
source_url: https://www.census.gov/programs-surveys/acs
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: American Community Survey 5-year estimates

## What it is

The U.S. Census Bureau’s American Community Survey (ACS) 5-year estimates cover income, poverty, housing, commuting, language, insurance, and other topics across the United States and Puerto Rico. Tables cover areas including counties, towns, census tracts, and block groups; each release pools five years of responses and is normally updated annually. The first 5-year release covers 2005–2009; the 2025 release date is not yet set. ([census.gov](https://www.census.gov/data/developers/data-sets/acs-5year.2009.html))

## How to get access

The data are open; no Yale license is needed. Download tables through [data.census.gov](https://data.census.gov/) or request a free Census API key and activate it using the email instructions. All API data queries require a key. ([census.gov](https://www.census.gov/library/video/2026/adrm/requesting-a-census-data-api-key.html))

## Programmatic access

- **API:** Use the confirmed detailed-table endpoint `https://api.census.gov/data/2024/acs/acs5` for the 2020–2024 estimates. The year in the URL is the final year of the five-year period. ([api.census.gov](https://api.census.gov/data/2024/acs/acs5/examples.html))
- **Parameters:** Use `get` for variables, `for` for the target geography, and `in` for its parent geography. For example, use `get=NAME,B01001_001E,B01001_001M`, `for=place:52000`, and `in=state:09` for New Haven, Connecticut. `E` is an estimate and `M` is its margin of error. ([api.census.gov](https://api.census.gov/data/2024/acs/acs5/examples.html))
- **Authentication:** Store the key in `CENSUS_API_KEY` and send it as the `key` query parameter. Avoid logging the full request URL, because it contains the key. ([census.gov](https://www.census.gov/data/developers/guidance/api-user-guide.API_Key.html))
- **Format:** The API returns JSON as an array of rows, with column names in the first row. The example converts it to CSV. The table has no geometry; download matching TIGER/Line boundaries separately. ([api.census.gov](https://api.census.gov/data/2024/acs/acs5/examples.html))
- **Rate limits:** Census’s February 2026 training says keys have no throttling or limitations, but its older reference to keyless requests is superseded by the current key requirement. No current numeric keyed limit was confirmed. ([www2.census.gov](https://www2.census.gov/about/training-workshops/2026/2026-02-19-getting-started-with-census-api-presentation.pdf))

## Python example

This saves the total population estimate and margin of error for the **city of New Haven**, not its individual tracts. Set `CENSUS_API_KEY` before running it. It uses only the Python standard library.

```python
import csv
import json
import os
from urllib.parse import urlencode
from urllib.request import urlopen

key = os.environ["CENSUS_API_KEY"]
base = "https://api.census.gov/data/2024/acs/acs5"
params = {
    "get": "NAME,B01001_001E,B01001_001M",
    "for": "place:52000",
    "in": "state:09",
    "key": key,
}
with urlopen(base + "?" + urlencode(params), timeout=30) as response:
    rows = json.load(response)

with open("new_haven_acs_2024.csv", "w", newline="", encoding="utf-8") as f:
    csv.writer(f).writerows(rows)

print("Saved new_haven_acs_2024.csv")
```

## Pitfalls

- **Estimates are not counts from a full census.** Check margins of error, especially for small areas. Adjacent 5-year releases overlap in their survey years.
- **Match the geography to the question.** A place estimate is not a set of tract estimates. Tracts can cross place boundaries.
- **Keep geography codes as strings.** Leading zeros matter when joining tables to boundary files.
- **Tables have no projection or geometry.** For mapping, join to a suitable TIGER/Line boundary vintage using geography codes; check the boundary file’s coordinate system. ([census.gov](https://www.census.gov/geographies/mapping-files/2024/geo/tiger-line-file.html))
- **Check missing and special values.** Census may use special codes or annotations rather than ordinary numeric estimates; do not treat them as real negative values.

## Sources checked

- [ACS 5-year data and geographic coverage](https://www.census.gov/data/developers/data-sets/acs-5year.2009.html)
- [2024 ACS API examples](https://api.census.gov/data/2024/acs/acs5/examples.html)
- [2024 ACS API variables](https://api.census.gov/data/2024/acs/acs5/variables.html)
- [Request and activate a Census API key](https://www.census.gov/library/video/2026/adrm/requesting-a-census-data-api-key.html)
- [Census API training and limits](https://www2.census.gov/about/training-workshops/2026/2026-02-19-getting-started-with-census-api-presentation.pdf)
- [2025 ACS release schedule](https://www.census.gov/programs-surveys/acs/news/data-releases/2025/release-schedule.html)
- [TIGER/Line shapefiles](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html)
