---
name: cur-tiger
description: "How to access TIGER/Line and cartographic boundary files (U.S. Census Bureau). Use when a study needs this dataset."
ref: cur:tiger
source_url: https://www.census.gov/geographies/mapping-files.html
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: TIGER/Line and cartographic boundary files

## What it is

TIGER/Line provides U.S. geographic boundaries and features, including states, counties, towns, tracts, block groups, ZCTAs, roads, and water. Cartographic boundary files are simplified versions for small-scale maps, available at scales such as 1:500,000; releases are generally annual, though not every geography is updated each year. ([www2.census.gov](https://www2.census.gov/geo/pdfs/maps-data/data/tiger/tgrshp2025/TGRSHP2025_TechDoc.pdf))

## How to get access

Download files directly from the Census Bureau’s mapping pages or archive. The files are open; no registration, API key, or Yale license is needed. Choose a year, geography, and, where offered, state and scale. ([census.gov](https://www.census.gov/cgi-bin/geo/shapefiles/index.php))

## Programmatic access

- **Archive:** `https://www2.census.gov/geo/tiger/` contains downloadable files organized by release and layer. This is a file download route, not a bounding-box query API. ([www2.census.gov](https://www2.census.gov/geo/pdfs/maps-data/data/tiger/tgrshp2025/TGRSHP2025_TechDoc_Ch6.pdf))
- **Example:** `https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_09_cousub_500k.zip` is the 2025 Connecticut county-subdivision layer. `09` is the state code and `500k` is the map scale. Download the state file, then filter it locally. ([www2.census.gov](https://www2.census.gov/geo/tiger/GENZ2025/shp/))
- **Authentication:** None for these public file downloads; no environment variable is needed.
- **Formats:** TIGER/Line is available as zipped shapefiles. Cartographic boundary releases also offer KML, geodatabase, and GeoPackage, depending on the release and layer. ([census.gov](https://www.census.gov/geographies/mapping-files/2025/geo/tiger-line-file.html))
- **Rate limits:** No published numeric limit for these file downloads was confirmed. Avoid unnecessary repeated downloads.

## Python example

This downloads the Connecticut file, selects the New Haven town boundary, and saves it as GeoJSON. Install dependencies first with `pip install geopandas requests`.

```python
from pathlib import Path
import tempfile

import geopandas as gpd
import requests

url = (
    "https://www2.census.gov/geo/tiger/GENZ2025/shp/"
    "cb_2025_09_cousub_500k.zip"
)

response = requests.get(url, timeout=60)
response.raise_for_status()

with tempfile.TemporaryDirectory() as tmp:
    archive = Path(tmp) / "connecticut.zip"
    archive.write_bytes(response.content)
    towns = gpd.read_file(f"zip://{archive}")
    new_haven = towns[towns["NAME"] == "New Haven"].copy()
    if new_haven.empty:
        raise ValueError("New Haven was not found in the downloaded layer.")
    new_haven.to_crs("EPSG:4326").to_file(
        "new_haven_town.geojson", driver="GeoJSON"
    )

print("Saved new_haven_town.geojson")
```

## Pitfalls

- Cartographic boundaries are simplified for mapping. Use TIGER/Line when you need more detailed geometry. ([census.gov](https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.2025.html))
- Check the coordinate reference system before analysis. Longitude and latitude are degrees, not meters; reproject before measuring distance or area.
- A ZIP shapefile contains several companion files. Keep them together if extracting it.
- Geographies and releases do not all match. For example, the 2025 cartographic page directs users to the 2020 tab for ZCTAs. ([census.gov](https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.2025.html))
- Join statistical data using geographic identifiers and compatible boundary years. Preserve leading zeros in codes.

## Sources checked

- Census TIGER/Line download page: https://www.census.gov/geographies/mapping-files/2025/geo/tiger-line-file.html ([census.gov](https://www.census.gov/geographies/mapping-files/2025/geo/tiger-line-file.html))
- Census cartographic boundary files: https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.2025.html ([census.gov](https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.2025.html))
- Census 2025 cartographic shapefile archive: https://www2.census.gov/geo/tiger/GENZ2025/shp/ ([www2.census.gov](https://www2.census.gov/geo/tiger/GENZ2025/shp/))
- TIGER/Line download documentation: https://www2.census.gov/geo/pdfs/maps-data/data/tiger/tgrshp2025/TGRSHP2025_TechDoc_Ch6.pdf ([www2.census.gov](https://www2.census.gov/geo/pdfs/maps-data/data/tiger/tgrshp2025/TGRSHP2025_TechDoc_Ch6.pdf))
