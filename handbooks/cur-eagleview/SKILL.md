---
name: cur-eagleview
description: "How to access EagleView CONNECTExplorer aerial imagery (EagleView). Use when a study needs this dataset."
ref: cur:eagleview
source_url: https://geospatial.yale.edu/datasets
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: EagleView CONNECTExplorer aerial imagery

## What it is

EagleView CONNECTExplorer provides high-resolution, top-down and angled aerial imagery across much of the United States. The Yale catalog lists 0.5-meter resolution; actual resolution and capture dates vary by location. Imagery is updated regularly, but no uniform refresh cycle is guaranteed. ([guides.library.yale.edu](https://guides.library.yale.edu/onlinemapping/CONNECTExplorer))

## How to get access

This is **licensed data**, not an open download. Yale Library has 25 CONNECTExplorer accounts and grants access case by case. Email **geospatialhelp@yale.edu** with your project and how long you need access. ([guides.library.yale.edu](https://guides.library.yale.edu/onlinemapping/CONNECTExplorer))

For manual downloads, search for a place in CONNECTExplorer and export the image. Ask Yale whether its license also permits API access; a CONNECTExplorer account does not by itself establish API entitlement. EagleView also offers a separate developer sandbox and imagery API trial. ([guides.library.yale.edu](https://guides.library.yale.edu/onlinemapping/CONNECTExplorer))

## Programmatic access

- **API:** EagleView’s WMTS v2 serves top-down image tiles. Production base: `https://apis.eagleview.com`; sandbox base: `https://sandbox.apis.eagleview.com`. This is a separate API route, not a confirmed CONNECTExplorer download endpoint. ([developer.eagleview.com](https://developer.eagleview.com/documentation/wmts/v2/api-documentation))
- **Discovery:** Request `/imagery/wmts/v2/visual/capabilities.xml` to find available layers and tile matrix sets. Bounding-box filters use `min_longitude`, `min_latitude`, `max_longitude`, and `max_latitude`, all in WGS84 degrees. ([developer.eagleview.com](https://developer.eagleview.com/documentation/wmts/v2/api-documentation))
- **Tiles:** Request `/imagery/wmts/v2/visual/tile/{layer}/default/{tile_matrix_set}/{z}/{x}/{y}.{format}`. Use a layer and matrix set from the capabilities response; provide `datum` and `epoch`. ([developer.eagleview.com](https://developer.eagleview.com/documentation/wmts/v2/api-documentation))
- **Authentication:** Obtain an API-enabled client ID and secret. Store them in `EAGLEVIEW_CLIENT_ID` and `EAGLEVIEW_CLIENT_SECRET`; exchange them for a bearer token at `https://apicenter.eagleview.com/oauth2/v1/token`. Do not put secrets in source code. ([developer.eagleview.com](https://developer.eagleview.com/documentation/authentication-methods/v1/client-credentials))
- **Formats:** WMTS returns 256-by-256 JPEG or PNG tiles. CONNECTExplorer exports include JPEG, PNG, TIFF and, for top-down images only, GeoTIFF. ([developer.eagleview.com](https://developer.eagleview.com/documentation/wmts/v2/api-documentation))
- **Rate limits:** A numeric WMTS limit was not confirmed. Handle HTTP 429 with backoff, and reuse tokens until they expire. ([developer.eagleview.com](https://developer.eagleview.com/documentation/wmts/v2/api-documentation))

## Python example

This downloads one PNG tile containing a point in the **Omaha sandbox**. It needs `requests` and sandbox credentials with WMTS enabled. The matrix set shown is documented as an example; check the capabilities response if it is unavailable for your account. The saved PNG is not a GeoTIFF. ([developer.eagleview.com](https://developer.eagleview.com/documentation/wmts/v2/api-documentation))

```python
import math
import os
from pathlib import Path

import requests

client_id = os.environ["EAGLEVIEW_CLIENT_ID"]
secret = os.environ["EAGLEVIEW_CLIENT_SECRET"]
token_response = requests.post(
    "https://apicenter.eagleview.com/oauth2/v1/token",
    auth=(client_id, secret),
    data={"grant_type": "client_credentials"},
    timeout=30,
)
token_response.raise_for_status()
token = token_response.json()["access_token"]

# A point inside EagleView's Omaha sandbox.
lon, lat, z = -95.99, 41.25, 18
n = 2**z
x = int((lon + 180) / 360 * n)
y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
base = "https://sandbox.apis.eagleview.com/imagery/wmts/v2/visual"
url = f"{base}/tile/Latest/default/GoogleMapsCompatible_9-23/{z}/{x}/{y}.png"
response = requests.get(
    url,
    headers={"Authorization": f"Bearer {token}"},
    params={"datum": "EPSG:1383", "epoch": "2025.0"},
    timeout=30,
)
response.raise_for_status()
if not response.headers.get("Content-Type", "").startswith("image/"):
    raise ValueError("The server did not return an image.")
Path("omaha_tile.png").write_bytes(response.content)
print("Saved omaha_tile.png")
```

## Pitfalls

- **Resolution:** The catalog’s 0.5 meters is not a guarantee for every image. Check the image metadata before measuring or comparing captures.
- **Coordinates:** Bounding-box filters use longitude and latitude in degrees. Tile coordinates use a slippy-map grid; a PNG tile alone does not carry GeoTIFF georeferencing. ([developer.eagleview.com](https://developer.eagleview.com/documentation/wmts/v2/api-documentation))
- **Coverage and dates:** A place or year may be missing or outside your entitlement. Check the capabilities response and capture dates; “Latest” can combine imagery from different captures. ([developer.eagleview.com](https://developer.eagleview.com/documentation/wmts/v2/api-documentation))
- **Permissions:** Confirm permitted downloading, redistribution, publication and automated use with Yale before proceeding.
- **Errors:** A 401 can mean an expired or invalid token; a 403 can mean missing entitlement. A 429 means requests are arriving too quickly. ([developer.eagleview.com](https://developer.eagleview.com/documentation/wmts/v2/api-documentation))

## Sources checked

- Yale Library CONNECTExplorer guide: https://guides.library.yale.edu/onlinemapping/CONNECTExplorer ([guides.library.yale.edu](https://guides.library.yale.edu/onlinemapping/CONNECTExplorer))
- EagleView WMTS v2 API documentation: https://developer.eagleview.com/documentation/wmts/v2/api-documentation ([developer.eagleview.com](https://developer.eagleview.com/documentation/wmts/v2/api-documentation))
- EagleView client-credentials guide: https://developer.eagleview.com/documentation/authentication-methods/v1/client-credentials ([developer.eagleview.com](https://developer.eagleview.com/documentation/authentication-methods/v1/client-credentials))
- EagleView WMTS overview: https://developer.eagleview.com/documentation/wmts/v2/overview ([developer.eagleview.com](https://developer.eagleview.com/documentation/wmts/v2/overview))
- EagleView developer overview: https://www.eagleview.com/blog/developer-overview-the-geospatial-building-blocks-of-eagleviews-imagery-platform/ ([eagleview.com](https://www.eagleview.com/blog/developer-overview-the-geospatial-building-blocks-of-eagleviews-imagery-platform/))
