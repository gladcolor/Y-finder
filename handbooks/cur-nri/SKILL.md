---
name: cur-nri
description: "How to access FEMA National Risk Index (FEMA). Use when a study needs this dataset."
ref: cur:nri
source_url: https://hazards.fema.gov/nri/
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: FEMA National Risk Index

## What it is

FEMA’s National Risk Index estimates expected annual loss, social vulnerability, community resilience, and natural hazard risk for 18 hazards across the United States and territories, at county and census-tract resolution. The latest version identified in FEMA’s layer metadata is December 2025, v1.20; a fixed update schedule was not confirmed. ([arcgis.com](https://www.arcgis.com/sharing/rest/content/items/39485e8035d446a5bff03259508ae355/info/metadata/metadata.xml?format=default&output=html))

## How to get access

The data are open. Use FEMA’s ArcGIS feature services for selected places or the OpenFEMA downloads for full datasets; no Yale license or registration is needed for the public feature services. The original NRI site points users toward the newer access route. ([dejavu.org](https://www.dejavu.org/cgi-bin/get.cgi?url=https%3A%2F%2Fwww.fema.gov%2Fflood-maps%2Fproducts-tools%2Fnational-risk-index&ver=95))

## Programmatic access

- **County query:** `https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer/0/query`
- **Tract query:** `https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Census_Tracts/FeatureServer/0/query` ([services.arcgis.com](https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Census_Tracts/FeatureServer/0))
- **Parameters:** Use `where` for a place filter, `outFields` for columns, `returnGeometry=true` for polygons, `outSR=4326` for longitude and latitude, and `f=geojson` for GeoJSON. Spatial queries also support `geometry`, `geometryType`, and `inSR`. ([services.arcgis.com](https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer/0/query))
- **Authentication:** The public services do not require a key, so no environment variable is needed.
- **Formats:** The feature layers support GeoJSON, Esri JSON, and PBF. FEMA also documents CSV tables and shapefiles for bulk use. ([services.arcgis.com](https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer/layers))
- **Limits:** The county and tract layers list a maximum of 2,000 records per query. Paginate larger requests and check for truncation. A requests-per-minute limit was not confirmed. ([services.arcgis.com](https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer/layers))

## Python example

This downloads the county polygon for New Haven County, Connecticut. Install `requests` first. No key is required.

```python
import json
from pathlib import Path

import requests

url = (
    "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/"
    "National_Risk_Index_Counties/FeatureServer/0/query"
)
params = {
    "where": "STCOFIPS='09009'",
    "outFields": "STCOFIPS,STATE,COUNTY,RISK_SCORE,EAL_SCORE",
    "returnGeometry": "true",
    "outSR": 4326,
    "f": "geojson",
}
response = requests.get(url, params=params, timeout=60)
response.raise_for_status()
data = response.json()
if "error" in data:
    raise RuntimeError(data["error"])
if not data.get("features"):
    raise RuntimeError("No features returned")
Path("new_haven_county_nri.geojson").write_text(
    json.dumps(data), encoding="utf-8"
)
```

## Pitfalls

- Scores, ratings, and loss values are different measures. Consult the technical documentation before interpreting or comparing them. ([fema.gov](https://www.fema.gov/sites/default/files/documents/fema_national-risk-index_technical-documentation.pdf))
- Preserve FIPS codes as strings: leading zeros matter. County and tract identifiers have different lengths.
- The service’s native projection is Web Mercator. Request `outSR=4326` for longitude and latitude, or choose an appropriate projected coordinate system for area calculations. ([services.arcgis.com](https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer/layers))
- Missing or “insufficient data” is not the same as zero risk. ([services.arcgis.com](https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Census_Tracts/FeatureServer/0))
- Cite FEMA, the dataset version, and the date retrieved. Do not use FEMA or DHS seals without permission. ([arcgis.com](https://www.arcgis.com/sharing/rest/content/items/9da4eeb936544335a6db0cd7a8448a51/info/metadata/metadata.xml?format=default&output=html))
- The code downloads a **county**, not a city boundary. The returned polygon and scores describe the whole county.

## Sources checked

- [FEMA county layer metadata](https://www.arcgis.com/sharing/rest/content/items/39485e8035d446a5bff03259508ae355/info/metadata/metadata.xml?format=default&output=html)
- [FEMA county feature layer](https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer/0/query)
- [FEMA tract feature layer](https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Census_Tracts/FeatureServer/0)
- [FEMA technical documentation, December 2025](https://www.fema.gov/sites/default/files/documents/fema_national-risk-index_technical-documentation.pdf)
- [FEMA National Risk Index primer](https://www.fema.gov/sites/default/files/2020-11/fema_national-risk-index_primer.pdf)
