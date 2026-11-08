---
name: pc-naip
description: "How to access NAIP: National Agriculture Imagery Program (USDA Farm Service Agency, Esri). Use when a study needs this dataset."
ref: pc:naip
source_url: https://planetarycomputer.microsoft.com/dataset/naip
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: NAIP: National Agriculture Imagery Program

## What it is

NAIP provides aerial imagery of the United States, including visible-color and near-infrared bands. The Planetary Computer collection lists imagery from 2010 through 2023, although its description says “to the present”; pixel sizes vary from 0.3 to 1 meter. USDA acquires imagery for roughly half of the contiguous United States each year, so a location is not necessarily updated annually. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/api/stac/v1/collections/naip))

## How to get access

Access is free. You do not need a Yale license, registration, or API key for the public STAC API and anonymous data download. Image URLs need a temporary access signature; the Python example obtains it automatically. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/))

## Programmatic access

- **STAC API:** `https://planetarycomputer.microsoft.com/api/stac/v1`. The collection ID is `naip`. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/api/stac/v1/collections/naip))
- **Search parameters:** Use `collections`, `bbox` in west, south, east, north order, and `datetime` for a date range. Bounding-box coordinates are longitude and latitude in degrees. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/quickstarts/reading-stac-r/))
- **Authentication:** Search anonymously. Sign the `image` asset before reading it. If you already have a subscription key, the Python SDK reads `PC_SDK_SUBSCRIPTION_KEY` from the environment; it is optional. ([github.com](https://github.com/microsoft/planetary-computer-sdk-for-python))
- **Formats:** STAC responses are JSON; the `image` asset is a cloud-optimized GeoTIFF. A separate text metadata asset is available. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/api/stac/v1/collections/naip))
- **Rate limits:** Anonymous access is throttled. The documentation does not specify a numeric limit here. Retry rate-limit errors with backoff. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/))

## Python example

Install dependencies with `pip install pystac-client planetary-computer rasterio`. This example searches a small area near Yale and saves the overlapping part of one image as a GeoTIFF.

```python
from pystac_client import Client
import planetary_computer as pc
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds, Window

bbox = [-72.928, 41.307, -72.927, 41.308]
catalog = Client.open(
    "https://planetarycomputer.microsoft.com/api/stac/v1",
    modifier=pc.sign_inplace,
)
search = catalog.search(
    collections=["naip"],
    bbox=bbox,
    datetime="2021-01-01/2023-12-31",
    max_items=1,
)
item = next(search.items(), None)
if item is None:
    raise SystemExit("No imagery found; try another date range or location.")

with rasterio.open(item.assets["image"].href) as src:
    bounds = transform_bounds("EPSG:4326", src.crs, *bbox)
    window = from_bounds(*bounds, transform=src.transform)
    window = window.round_offsets().round_lengths()
    window = window.intersection(Window(0, 0, src.width, src.height))
    data = src.read(window=window)
    profile = src.profile.copy()
    profile.update(
        driver="GTiff", height=data.shape[1], width=data.shape[2],
        transform=src.window_transform(window)
    )
    with rasterio.open("naip_sample.tif", "w", **profile) as dst:
        dst.write(data)

print(f"Saved naip_sample.tif from {item.id}")
```

## Pitfalls

- **Resolution varies:** Do not assume every image has 0.3-meter pixels. Check the item metadata and raster resolution. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/api/stac/v1/collections/naip))
- **Coverage and dates vary:** A search can return no items, or an image that covers only part of your box. The example saves one overlapping item, not a mosaic or necessarily the newest image.
- **Projections differ:** Search boxes use longitude and latitude; image pixels may use a projected coordinate system. Reproject before combining layers.
- **Signed URLs expire:** If a download fails after a delay, sign the asset again. Avoid sharing signed URLs. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/concepts/sas/))
- **Terms:** The collection lists its license as `other`, not a named open license. Check the provider’s terms and credit requirements for your intended use. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/api/stac/v1/collections/naip))

## Sources checked

- [Planetary Computer NAIP collection metadata](https://planetarycomputer.microsoft.com/api/stac/v1/collections/naip)
- [Planetary Computer documentation](https://planetarycomputer.microsoft.com/docs/)
- [Planetary Computer token access documentation](https://planetarycomputer.microsoft.com/docs/concepts/sas/)
- [Planetary Computer Python SDK](https://github.com/microsoft/planetary-computer-sdk-for-python)
- [USDA geospatial research and science](https://www.fpacbc.usda.gov/geospatial-services/data-inspection-enhancement-and-delivery/geospatial-research-and-science)
