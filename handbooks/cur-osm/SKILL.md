---
name: cur-osm
description: "How to access OpenStreetMap extracts (Geofabrik) (OpenStreetMap contributors). Use when a study needs this dataset."
ref: cur:osm
source_url: https://download.geofabrik.de
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: OpenStreetMap extracts (Geofabrik)

## What it is

Geofabrik publishes regional extracts of OpenStreetMap vector features, including roads, buildings, amenities, and land use, with global coverage and U.S. state-level extracts. Each record represents a mapped feature rather than a fixed-size grid cell; extracts are normally updated daily, although publication can lag behind OSM edits. ([download.geofabrik.de](https://download.geofabrik.de/))

## How to get access

Download public extracts directly from Geofabrik for free. No registration, API key, or Yale license is needed for the public files. Full contributor metadata and history files are restricted to the separate internal server. ([download.geofabrik.de](https://download.geofabrik.de/north-america/us/district-of-columbia.html))

## Programmatic access

- **Base URL:** `https://download.geofabrik.de/`. Navigate to a region page and use its download links. ([download.geofabrik.de](https://download.geofabrik.de/))
- **Selection:** Choose a region and file format in the URL. These are ready-made regional files, not a confirmed bounding-box query API. For a smaller area, download a region and filter it locally. ([download.geofabrik.de](https://download.geofabrik.de/technical.html))
- **Authentication:** None for public downloads; no environment variable is needed.
- **Formats:** Region pages may offer OSM PBF, zipped shapefiles, and zipped GeoPackages. Check the chosen page for availability. ([download.geofabrik.de](https://download.geofabrik.de/north-america/us/district-of-columbia.html))
- **Rate limits:** No numeric limit was confirmed. Avoid unnecessary repeat downloads and handle HTTP errors.

## Python example

This downloads the District of Columbia place extract, about 20 MB, and saves the complete PBF file. It uses only Python’s standard library. ([download.geofabrik.de](https://download.geofabrik.de/north-america/us/district-of-columbia.html))

```python
from pathlib import Path
from urllib.request import Request, urlopen
import shutil

url = (
    "https://download.geofabrik.de/north-america/us/"
    "district-of-columbia-latest.osm.pbf"
)
output = Path("district-of-columbia-latest.osm.pbf")
request = Request(url, headers={"User-Agent": "Yale-GIS-research/1.0"})

with urlopen(request, timeout=120) as response, output.open("wb") as file:
    shutil.copyfileobj(response, file)

print(f"Saved {output} ({output.stat().st_size:,} bytes)")
```

## Pitfalls

- Coordinates are geographic coordinates, not distances in meters. Reproject to an appropriate projected coordinate system before measuring distances or areas.
- Mapping completeness and tag detail vary. A missing feature does not necessarily mean it does not exist.
- Shapefile and GeoPackage layers are simplified selections of OSM data; use PBF when you need the fuller set of OSM objects and tags. ([download.geofabrik.de](https://download.geofabrik.de/osm-data-in-gis-formats-free.pdf))
- Credit OpenStreetMap contributors and review the ODbL terms, including share-alike obligations for adapted databases. ([openstreetmap.org](https://www.openstreetmap.org/copyright))
- `latest` changes over time. Record the download date and the extract’s data timestamp for reproducibility. Public extracts omit contributor IDs, names, and changeset IDs. ([download.geofabrik.de](https://download.geofabrik.de/north-america/us/district-of-columbia.html))

## Sources checked

- Geofabrik download server: https://download.geofabrik.de/ ([download.geofabrik.de](https://download.geofabrik.de/))
- Geofabrik technical details: https://download.geofabrik.de/technical.html ([download.geofabrik.de](https://download.geofabrik.de/technical.html))
- District of Columbia downloads: https://download.geofabrik.de/north-america/us/district-of-columbia.html ([download.geofabrik.de](https://download.geofabrik.de/north-america/us/district-of-columbia.html))
- Geofabrik GIS format guide: https://download.geofabrik.de/osm-data-in-gis-formats-free.pdf ([download.geofabrik.de](https://download.geofabrik.de/osm-data-in-gis-formats-free.pdf))
- OpenStreetMap copyright and license: https://www.openstreetmap.org/copyright ([openstreetmap.org](https://www.openstreetmap.org/copyright))
