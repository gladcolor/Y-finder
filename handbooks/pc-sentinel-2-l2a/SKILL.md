---
name: pc-sentinel-2-l2a
description: "How to access Sentinel-2 Level-2A (ESA, Esri). Use when a study needs this dataset."
ref: pc:sentinel-2-l2a
source_url: https://planetarycomputer.microsoft.com/dataset/sentinel-2-l2a
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: Sentinel-2 Level-2A

## What it is

Sentinel-2 Level-2A contains global satellite imagery processed to surface reflectance. Bands have 10 m, 20 m, or 60 m resolution; the collection description says 2016 to present, although its STAC time extent begins in June 2015. Sentinel-2 revisits approximately every five days, and new items are added continuously. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a))

## How to get access

Access is free and open through Microsoft Planetary Computer. No account, API key, or Yale license is required for the route below. File downloads require a short-lived signed URL, which can be obtained anonymously. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/))

## Programmatic access

- **STAC API:** `https://planetarycomputer.microsoft.com/api/stac/v1`; collection: `sentinel-2-l2a`. ([learn.microsoft.com](https://learn.microsoft.com/en-us/azure/planetary-computer/api-tutorial))
- **Search parameters:** Use `collections`, `bbox` in west, south, east, north longitude/latitude order, and `datetime` as a date range. Optionally filter `eo:cloud_cover`. ([learn.microsoft.com](https://learn.microsoft.com/en-us/azure/planetary-computer/api-tutorial))
- **Authentication:** Search anonymously. Sign asset URLs with the `planetary-computer` Python package before reading them. An optional subscription key can be supplied in `PC_SDK_SUBSCRIPTION_KEY`; never put it in code. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/concepts/sas/))
- **Formats:** STAC metadata is JSON. Imagery bands and the scene classification layer are Cloud Optimized GeoTIFFs. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a))
- **Rate limits:** Anonymous downloads may be throttled. Limits depend on request location and whether a subscription key is supplied; the documentation does not specify a fixed request quota. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/))

## Python example

Install dependencies with `pip install pystac-client planetary-computer rasterio`. This saves a small red-band GeoTIFF near Yale. It reads the optional subscription key from the environment through the SDK. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/concepts/sas/))

```python
import planetary_computer
import rasterio
from pystac_client import Client
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds

bbox = [-72.930, 41.305, -72.925, 41.310]
catalog = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
search = catalog.search(
    collections=["sentinel-2-l2a"],
    bbox=bbox,
    datetime="2024-06-01/2024-09-30",
    query={"eo:cloud_cover": {"lt": 20}},
    max_items=20,
)
items = list(search.items())
if not items:
    raise RuntimeError("No matching scenes; expand the dates or cloud limit.")

item = min(items, key=lambda x: x.properties.get("eo:cloud_cover", 100))
url = planetary_computer.sign(item.assets["B04"].href)

with rasterio.open(url) as src:
    bounds = transform_bounds("EPSG:4326", src.crs, *bbox)
    window = from_bounds(*bounds, transform=src.transform)
    window = window.round_offsets().round_lengths()
    window = window.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
    data = src.read(1, window=window)
    profile = src.profile.copy()
    profile.update(
        driver="GTiff", height=data.shape[0], width=data.shape[1],
        count=1, transform=src.window_transform(window)
    )
    with rasterio.open("yale_red_band.tif", "w", **profile) as dst:
        dst.write(data, 1)

print("Saved yale_red_band.tif from", item.id)
```

## Pitfalls

- The sample saves raw band values, not a finished reflectance product. Check the item and product metadata for scaling and offsets before analysis.
- Search bounding boxes use longitude and latitude. Downloaded rasters use their native projected coordinate system; the example transforms the box before cropping.
- A matching scene may cover only part of the box, or contain clouds or missing pixels. Scene-wide cloud percentage does not guarantee clear pixels at Yale. Use the `SCL` layer to help mask unwanted pixels. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a))
- Signed URLs expire. Sign again if a download fails because its token is old. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/concepts/sas/))
- Open access does not remove usage conditions. Review the Copernicus Sentinel data terms linked in the collection metadata. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a))

## Sources checked

- Collection metadata and data terms: https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a
- Planetary Computer access and signed URLs: https://planetarycomputer.microsoft.com/docs/concepts/sas/
- Planetary Computer overview: https://planetarycomputer.microsoft.com/docs/
- Microsoft STAC search example: https://learn.microsoft.com/en-us/azure/planetary-computer/api-tutorial
- Update history: https://planetarycomputer.microsoft.com/docs/changelogs/history
