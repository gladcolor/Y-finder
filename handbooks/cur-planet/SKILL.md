---
name: cur-planet
description: "How to access Planet PlanetScope, RapidEye and basemaps (Planet Labs). Use when a study needs this dataset."
ref: cur:planet
source_url: https://geospatial.yale.edu/planet-labs
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: Planet PlanetScope, RapidEye and basemaps

## What it is

Planet Labs provides global satellite imagery: daily 3 m PlanetScope scenes from 2014, with 8-band imagery available from 2020, and 5 m RapidEye scenes from 2009–2020. Monthly basemaps combine scenes into mosaics; their pixel size and projection differ from those of the original scenes. ([geospatial.yale.edu](https://geospatial.yale.edu/planet-labs))

## How to get access

This is **not an open download**. Current Yale affiliates with an active NetID can request a limited named account for research or teaching through the request form on the [Yale Planet Labs page](https://geospatial.yale.edu/planet-labs). Do not create a Planet account with your request email before receiving an invitation. Yale says most requests take 2–5 business days, but approval is not guaranteed. ([geospatial.yale.edu](https://geospatial.yale.edu/planet-labs))

Once invited, find your API key in Planet Account under **My Settings**. Treat it like a password. Basemap downloads also require a plan with Mosaics access; check your account’s permissions. ([docs.planet.com](https://docs.planet.com/develop/authentication/))

## Programmatic access

- **Scene API:** `https://api.planet.com/data/v1/` searches for scenes by item type, geometry, acquisition date and other filters. Assets must be activated before download; activation can take several minutes. ([docs.planet.com](https://docs.planet.com/develop/apis/data/))
- **Basemaps API:** `https://api.planet.com/basemaps/v1/` lists mosaics and finds downloadable quads. Select a mosaic ID from the mosaics list, then query its `quads` endpoint with `bbox=west,south,east,north` in degrees. ([docs.planet.com](https://docs.planet.com/develop/apis/basemaps/))
- **Authentication:** Store the key in `PL_API_KEY`. For these APIs, use HTTP Basic authentication with the key as the username and an empty password. Never put it in source code or share it. ([docs.planet.com](https://docs.planet.com/develop/authentication/))
- **Formats:** Basemap quads are Cloud Optimized GeoTIFFs. API search and metadata responses are JSON; scene assets include imagery files and metadata, depending on the product. ([docs.planet.com](https://docs.planet.com/develop/apis/basemaps/))
- **Rate limits:** The Data API permits 2 activation, 5 download and 5 search requests per second. The Basemaps API lists 100 quad requests and 600 other requests per second per key. On HTTP 429, respect `Retry-After` when present and use exponential backoff. ([docs.planet.com](https://docs.planet.com/develop/apis/data/))

## Python example

This downloads **one whole basemap quad** intersecting a small box near Yale, not a clipped image. Install `requests`, set `PL_API_KEY`, and set `PLANET_MOSAIC_ID` to an accessible mosaic ID found through the Basemaps API or Planet interface. The account must have permission to download that mosaic. ([docs.planet.com](https://docs.planet.com/develop/apis/basemaps/))

```python
import os
import requests

key = os.environ["PL_API_KEY"]
mosaic_id = os.environ["PLANET_MOSAIC_ID"]
base = "https://api.planet.com/basemaps/v1/mosaics"
bbox = "-72.931,41.307,-72.929,41.309"  # west,south,east,north

session = requests.Session()
session.auth = (key, "")
response = session.get(
    f"{base}/{mosaic_id}/quads/",
    params={"bbox": bbox, "_page_size": 1},
    timeout=60,
)
response.raise_for_status()
items = response.json().get("items", [])
if not items:
    raise SystemExit("No quad found. Check the mosaic, area, and permissions.")

url = items[0]["_links"]["download"]
with session.get(url, stream=True, timeout=300) as response:
    response.raise_for_status()
    with open("yale_basemap_quad.tif", "wb") as output:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                output.write(chunk)
print("Saved yale_basemap_quad.tif")
```

## Pitfalls

- Bounding boxes use longitude and latitude in **degrees**, not meters. Basemap quads use Web Mercator, EPSG:3857; their ground pixel size varies with latitude. ([docs.planet.com](https://docs.planet.com/develop/apis/basemaps/))
- A small bounding box can return a much larger whole quad. Download only what you need and avoid repeated downloads; Yale has shared annual and lab-level quotas. ([geospatial.yale.edu](https://geospatial.yale.edu/planet-labs))
- Global coverage does not guarantee a usable image for every place and date. Check acquisition dates, cloud cover and masks before analysis.
- Yale licenses the data for academic, non-commercial use. Do not redistribute original datasets or significant derivatives. Follow Yale’s attribution instructions for publications. ([geospatial.yale.edu](https://geospatial.yale.edu/planet-labs))
- A 401 can indicate a missing or incorrect key; a 403 can indicate insufficient permission. A 429 means the request rate was exceeded. ([docs.planet.com](https://docs.planet.com/develop/errors/))

## Sources checked

- [Yale Center for Geospatial Solutions: Planet Labs](https://geospatial.yale.edu/planet-labs)
- [Planet: Authentication](https://docs.planet.com/develop/authentication/)
- [Planet: Data API overview](https://docs.planet.com/develop/apis/data/)
- [Planet: Item search](https://docs.planet.com/develop/apis/data/item-search/)
- [Planet: Items and assets](https://docs.planet.com/develop/apis/data/items/)
- [Planet: Basemaps API overview](https://docs.planet.com/develop/apis/basemaps/)
- [Planet: Basemaps API reference](https://docs.planet.com/develop/apis/basemaps/reference/)
- [Planet: Mosaics products](https://docs.planet.com/data/imagery/basemaps/)
- [Planet: Rate limiting](https://docs.planet.com/develop/rate-limiting/)
- [Planet: Errors](https://docs.planet.com/develop/errors/)
