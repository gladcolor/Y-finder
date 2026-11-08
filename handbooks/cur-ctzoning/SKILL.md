---
name: cur-ctzoning
description: "How to access Connecticut Zoning Atlas (Desegregate CT / CTData). Use when a study needs this dataset."
ref: cur:ctzoning
source_url: https://www.desegregatect.org/atlas
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: Connecticut Zoning Atlas

## What it is

The Connecticut Zoning Atlas maps zoning districts and standardized housing rules across Connecticut, using district polygons rather than individual parcels. The catalog describes a 2021 snapshot; the atlas is now part of the National Zoning Atlas, but a regular update cycle for the Connecticut data was not confirmed. ([pro-homesct.org](https://pro-homesct.org/atlas))

## How to get access

The interactive map is open to view. The National Zoning Atlas FAQ directs users who want downloadable data to its licensing arm, Land Use Labs; confirm whether it can provide the **2021 Connecticut snapshot** and on what terms. A Yale license or free downloadable copy was not confirmed. For questions, contact the atlas team at `info@desegregatect.org`. ([pro-homesct.org](https://pro-homesct.org/atlas))

## Programmatic access

- **Endpoint:** No official download or query endpoint for the 2021 snapshot was confirmed. Do not treat the map page as a data API.
- **Parameters:** Bounding-box and place-query parameters were not confirmed.
- **Authentication:** The public map needs no confirmed API key. Authentication for a licensed download was not confirmed; store any credentials supplied by the provider in environment variables.
- **Formats and rate limits:** Download formats and rate limits were not confirmed. Ask the provider before writing an automated client.

## Python example

No working sample-download endpoint was confirmed. The example below is **conditional**: after obtaining an authorized GeoJSON file URL from the provider, it downloads the file and saves districts intersecting a small Hartford-area bounding box. It may download the whole file before filtering. Install `requests` and `geopandas` first.

```python
import os
import requests
import geopandas as gpd
from io import BytesIO

url = os.environ.get("ZONING_GEOJSON_URL")
if not url:
    raise SystemExit("Set ZONING_GEOJSON_URL to an authorized GeoJSON file URL.")

response = requests.get(url, timeout=120)
response.raise_for_status()
districts = gpd.read_file(BytesIO(response.content))

if districts.crs is None:
    raise SystemExit("Source CRS is missing; confirm it with the provider.")

districts = districts.to_crs("EPSG:4326")
sample = districts.cx[-72.70:-72.67, 41.75:41.78]
sample.to_file("hartford_zoning_sample.geojson", driver="GeoJSON")
print(f"Saved {len(sample)} features.")
```

## Pitfalls

- The 2021 snapshot is not a statement of current law. Check current local regulations before relying on it.
- Minimum lot sizes in the original map are shown in **acres**. Check each downloaded field’s units and definitions. ([ct-data-collaborative.github.io](https://ct-data-collaborative.github.io/desegregate-ct-map/map/))
- Confirm the source coordinate reference system before calculating area or distance. Use an appropriate projected system rather than longitude and latitude.
- Statewide coverage does not mean every water body or public land area has a zoning polygon. The 2023 study notes exclusions including some parks and water bodies. ([urban.org](https://www.urban.org/sites/default/files/2023-06/Bringing%20Zoning%20into%20Focus.pdf))
- Open map viewing does not establish permission to redistribute a downloaded dataset. Confirm license terms and citation requirements.

## Sources checked

- Provider atlas page: https://www.desegregatect.org/atlas
- National Zoning Atlas FAQ: https://www.zoningatlas.org/faqs
- CTData project description: https://www.ctdata.org/interactive-data-projects
- Original interactive map: https://ct-data-collaborative.github.io/desegregate-ct-map/map/
- Study describing coverage: https://www.urban.org/sites/default/files/2023-06/Bringing%20Zoning%20into%20Focus.pdf
