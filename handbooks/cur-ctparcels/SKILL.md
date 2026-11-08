---
name: cur-ctparcels
description: "How to access Connecticut CAMA and parcel layer 2025 (CT Office of Policy and Management). Use when a study needs this dataset."
ref: cur:ctparcels
source_url: https://catalog.data.gov/dataset/connecticut-cama-and-parcel-layer-2025
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: Connecticut CAMA and parcel layer 2025

## What it is

This layer combines parcel boundaries with assessor records, including land use, acreage, values and building information. It covers all 169 Connecticut municipalities at the parcel level, though assessor attributes are missing for one municipality; the 2025 layer was created from data collected in 2024–2025. Connecticut collects parcel and CAMA data annually. ([catalog.data.gov](https://catalog.data.gov/dataset/connecticut-cama-and-parcel-layer-2025))

## How to get access

The data is public. Use the CT Geodata Portal download page; no registration, API key or Yale license is listed as required. The catalog offers CSV, GeoJSON, KML and zipped Shapefile downloads. ([catalog.data.gov](https://catalog.data.gov/dataset/connecticut-cama-and-parcel-layer-2025))

For reproducible 2025 research, use the **2025-labeled layer**. The unversioned service is intended to provide the most current layer and may change to a later year. ([maps.cteco.uconn.edu](https://maps.cteco.uconn.edu/data/parcels/))

## Programmatic access

- **2025 layer URL:** `https://services3.arcgis.com/3FL1kr7L4LvwA2Kb/arcgis/rest/services/Connecticut_CAMA_and_Parcel_Layer_2025/FeatureServer/0`. The catalog lists this endpoint, but I could not verify a live query response during this check. ([catalog.data.gov](https://catalog.data.gov/dataset/connecticut-cama-and-parcel-layer-2025))
- **Query:** Append `/query`. Common ArcGIS parameters include `geometry`, `geometryType`, `inSR`, `spatialRel`, `outFields`, `outSR`, `resultRecordCount` and `f`.
- **Authentication:** The catalog marks the dataset public. No key or authentication environment variable is listed as required. ([catalog.data.gov](https://catalog.data.gov/dataset/connecticut-cama-and-parcel-layer-2025))
- **Formats:** The catalog lists full downloads in CSV, GeoJSON, KML and Shapefile. CSV is tabular; choose a spatial format for parcel boundaries. ([catalog.data.gov](https://catalog.data.gov/dataset/connecticut-cama-and-parcel-layer-2025))
- **Limits:** I could not confirm a request-rate limit or the 2025 service’s per-query record limit. Check the layer metadata before bulk retrieval; do not assume one response contains every matching parcel.

## Python example

This requests up to 10 parcels near central New Haven and saves GeoJSON. It requires `requests` (`pip install requests`). Because the live query could not be verified here, check any error returned by the service.

```python
import json
from pathlib import Path

import requests

url = (
    "https://services3.arcgis.com/3FL1kr7L4LvwA2Kb/arcgis/rest/services/"
    "Connecticut_CAMA_and_Parcel_Layer_2025/FeatureServer/0/query"
)
params = {
    "where": "1=1",
    "geometry": "-72.930,41.305,-72.925,41.309",
    "geometryType": "esriGeometryEnvelope",
    "inSR": "4326",
    "spatialRel": "esriSpatialRelIntersects",
    "outFields": "*",
    "returnGeometry": "true",
    "outSR": "4326",
    "resultRecordCount": 10,
    "f": "geojson",
}
response = requests.get(url, params=params, timeout=60)
response.raise_for_status()
data = response.json()
if "error" in data:
    raise RuntimeError(data["error"])
if data.get("type") != "FeatureCollection":
    raise ValueError("The service did not return a GeoJSON FeatureCollection")
Path("new_haven_parcels.geojson").write_text(
    json.dumps(data), encoding="utf-8"
)
print(f"Saved {len(data['features'])} features to new_haven_parcels.geojson")
```

## Pitfalls

- The source projection is **NAD83 Connecticut State Plane (2011), EPSG:6434**, with coordinates in feet. The example requests longitude and latitude in EPSG:4326 instead; do not treat those degrees as feet. ([catalog.data.gov](https://catalog.data.gov/dataset/connecticut-cama-and-parcel-layer-2025))
- `Land_Acres` is an acreage attribute. Do not assume it equals an area calculated from the polygon. ([geodata.ct.gov](https://geodata.ct.gov/datasets/ctmaps%3A%3Aconnecticut-cama-and-parcel-layer-2025))
- Not every parcel links to a CAMA record. Some towns suppress ownership names and replace them with generic labels. ([services3.arcgis.com](https://services3.arcgis.com/3FL1kr7L4LvwA2Kb/ArcGIS/rest/services/Connecticut_CAMA_and_Parcel_Layer/FeatureServer))
- The catalog lists a **CC0** license. The state disclaims legal responsibility for the information; check discrepancies with the relevant municipality. ([catalog.data.gov](https://catalog.data.gov/dataset/connecticut-cama-and-parcel-layer-2025))
- The example is a **sample**, not a complete bounding-box extract. For complete results, check the service’s record limit and paginate or retrieve object IDs in batches.

## Sources checked

- [Data.gov: Connecticut CAMA and Parcel Layer 2025](https://catalog.data.gov/dataset/connecticut-cama-and-parcel-layer-2025)
- [CT Geodata Portal: 2025 dataset](https://geodata.ct.gov/datasets/ctmaps::connecticut-cama-and-parcel-layer-2025)
- [CT GIS Office: Parcel and CAMA](https://portal.ct.gov/datapolicy/home/gis-office/parcel-and-cama)
- [CT ArcGIS: unversioned parcel service metadata](https://services3.arcgis.com/3FL1kr7L4LvwA2Kb/ArcGIS/rest/services/Connecticut_CAMA_and_Parcel_Layer/FeatureServer)
- [UConn CT ECO: CT parcels](https://maps.cteco.uconn.edu/data/parcels/)
