---
name: cur-svi
description: "How to access CDC/ATSDR Social Vulnerability Index (CDC / ATSDR). Use when a study needs this dataset."
ref: cur:svi
source_url: https://www.atsdr.cdc.gov/place-health/php/svi/index.html
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: CDC/ATSDR Social Vulnerability Index

## What it is

The CDC/ATSDR Social Vulnerability Index (SVI) ranks U.S. census tracts using census measures grouped into four themes; the 2022 version uses 16 measures. The available releases span 2000–2022, and CDC says it updates SVI every two years, with a delay after Census data releases. ([atsdr.cdc.gov](https://www.atsdr.cdc.gov/place-health/php/svi/svi-data-documentation-download.html?CDC_AAref_Val=https://www.atsdr.cdc.gov/placeandhealth/svi/data_documentation_download.html))

## How to get access

The data are open. No registration, API key, or Yale license is needed for the routes below. On the CDC **SVI Data & Documentation Download** page, select the year, geography, and tract-level data; download a CSV table or geodatabase. ([atsdr.cdc.gov](https://www.atsdr.cdc.gov/place-health/php/svi/svi-data-documentation-download.html?CDC_AAref_Val=https%3A%2F%2Fwww.atsdr.cdc.gov%2Fplaceandhealth%2Fsvi%2Fdata_documentation_download.html))

## Programmatic access

- **2022 U.S. tract query endpoint:** `https://onemap.cdc.gov/onemapservices/rest/services/SVI/CDC_ATSDR_Social_Vulnerability_Index_2022_USA/FeatureServer/2/query`. This layer uses nationwide rankings. ([onemap.cdc.gov](https://onemap.cdc.gov/onemapservices/rest/services/SVI/CDC_ATSDR_Social_Vulnerability_Index_2022_USA/FeatureServer/2))
- **Parameters:** Use `where` for an attribute filter, `geometry` and `geometryType` for a bounding box, `inSR` for its coordinate system, and `outFields` to select columns. Use `returnGeometry=true` and `outSR=4326` for geographic coordinates.
- **Authentication:** None is needed for this public query; no environment variable or key is required.
- **Formats:** The layer supports JSON, GeoJSON, and PBF. The download page also offers CSV and geodatabase files. ([onemap.cdc.gov](https://onemap.cdc.gov/onemapservices/rest/services/SVI/CDC_ATSDR_Social_Vulnerability_Index_2022_USA/FeatureServer/2))
- **Limits:** The layer lists a maximum of 2,000 records per request and supports pagination. A request-rate limit was not confirmed. For larger extracts, paginate and check for truncated results. ([onemap.cdc.gov](https://onemap.cdc.gov/onemapservices/rest/services/SVI/CDC_ATSDR_Social_Vulnerability_Index_2022_USA/FeatureServer/2))

## Python example

This uses the Python standard library. It saves tracts intersecting a small box near downtown New Haven as GeoJSON. The endpoint and its query operation are documented, but the example request could not be independently tested through the browser tool. ([onemap.cdc.gov](https://onemap.cdc.gov/onemapservices/rest/services/SVI/CDC_ATSDR_Social_Vulnerability_Index_2022_USA/FeatureServer/2))

```python
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

base = (
    "https://onemap.cdc.gov/onemapservices/rest/services/SVI/"
    "CDC_ATSDR_Social_Vulnerability_Index_2022_USA/FeatureServer/2/query"
)
params = {
    "where": "1=1",
    "geometry": "-72.94,41.30,-72.92,41.32",
    "geometryType": "esriGeometryEnvelope",
    "inSR": "4326",
    "spatialRel": "esriSpatialRelIntersects",
    "outFields": "FIPS,COUNTY,RPL_THEMES",
    "returnGeometry": "true",
    "outSR": "4326",
    "f": "geojson",
}
with urlopen(base + "?" + urlencode(params), timeout=60) as response:
    data = json.load(response)

if "error" in data:
    raise RuntimeError(data["error"])
if data.get("exceededTransferLimit"):
    raise RuntimeError("Result truncated; use a smaller box or paginate.")
if data.get("type") != "FeatureCollection":
    raise RuntimeError("Unexpected response format.")

Path("new_haven_svi_2022.geojson").write_text(
    json.dumps(data), encoding="utf-8"
)
print(f"Saved {len(data['features'])} tracts.")
```

## Pitfalls

- `RPL_THEMES` is a **relative percentile rank**, not a count or percentage of vulnerable people. Higher values indicate greater vulnerability. State and national ranks use different comparison groups. ([atsdr.cdc.gov](https://atsdr.cdc.gov/place-health/media/pdfs/2025/01/SVI2022Documentation.pdf))
- The box coordinates in the example are longitude and latitude in degrees. The service’s native projection is Web Mercator, in meters; specify coordinate systems when querying. ([onemap.cdc.gov](https://onemap.cdc.gov/onemapservices/rest/services/SVI/CDC_ATSDR_Social_Vulnerability_Index_2022_USA/FeatureServer/2))
- Treat `-999` as missing, not as a real measurement. Estimates also have margins of error. ([atsdr.cdc.gov](https://atsdr.cdc.gov/place-health/media/pdfs/2025/01/SVI2022Documentation.pdf))
- Keep tract `FIPS` identifiers as text so leading zeros are preserved. ([atsdr.cdc.gov](https://atsdr.cdc.gov/place-health/media/pdfs/2025/01/SVI2022Documentation.pdf))
- Do not directly compare ranks across years without care: tract boundaries, input variables, and the populations used for ranking can change. ([atsdr.cdc.gov](https://www.atsdr.cdc.gov/place-health/php/svi/svi-data-documentation-download.html?CDC_AAref_Val=https://www.atsdr.cdc.gov/placeandhealth/svi/data_documentation_download.html))
- Puerto Rico is in a separate dataset and is not included in the U.S.-wide rankings used by the example. ([atsdr.cdc.gov](https://atsdr.cdc.gov/place-health/php/svi/svi-frequently-asked-questions-faqs.html))

## Sources checked

- CDC data and documentation: https://www.atsdr.cdc.gov/place-health/php/svi/svi-data-documentation-download.html
- CDC 2022 tract layer and query operation: https://onemap.cdc.gov/onemapservices/rest/services/SVI/CDC_ATSDR_Social_Vulnerability_Index_2022_USA/FeatureServer/2
- CDC 2022 documentation and data dictionary: https://atsdr.cdc.gov/place-health/media/pdfs/2025/01/SVI2022Documentation.pdf
- CDC SVI frequently asked questions: https://atsdr.cdc.gov/place-health/php/svi/svi-frequently-asked-questions-faqs.html
