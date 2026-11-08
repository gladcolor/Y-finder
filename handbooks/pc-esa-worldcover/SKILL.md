---
name: pc-esa-worldcover
description: "How to access ESA WorldCover (ESA, ESA WorldCover Consortium). Use when a study needs this dataset."
ref: pc:esa-worldcover
source_url: https://planetarycomputer.microsoft.com/dataset/esa-worldcover
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: ESA WorldCover

## What it is

ESA WorldCover is a global land-cover classification derived from Sentinel-1 and Sentinel-2 data, with approximately 10-meter resolution. The collection has one map each for 2020 and 2021; do not assume it is updated annually beyond those years. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/api/stac/v1/collections/esa-worldcover))

## How to get access

Download it free through Microsoft Planetary Computer’s public STAC API. No registration, API key, or Yale license is required for anonymous access. Downloads use short-lived access tokens, which the Python package can obtain automatically. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/))

## Programmatic access

- **STAC API:** `https://planetarycomputer.microsoft.com/api/stac/v1`; collection: `esa-worldcover`. The API supports search at `/search`. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/api/stac/v1/docs))
- **Search parameters:** Use `collections`, a `bbox` in west, south, east, north order, and a `datetime` interval. The map asset is named `map`. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/tutorials/hurricane-florence-animation/))
- **Authentication:** Anonymous STAC searches and token requests need no key. For an optional subscription key, set `PC_SDK_SUBSCRIPTION_KEY` in the environment; `planetary_computer` reads it when signing assets. Never put a key in code. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/concepts/sas/))
- **Formats:** STAC metadata is JSON; map tiles are Cloud-Optimized GeoTIFFs. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/api/stac/v1/collections/esa-worldcover))
- **Rate limits:** Anonymous data downloads are throttled. Limits depend on the subscription key and request location; the documentation does not give a fixed rate. Retry slowly if throttled. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/))

## Python example

Install with `pip install pystac-client planetary-computer rasterio`. This saves a small 2021 sample near Yale as `worldcover_yale_2021.tif`. It needs no key; if set, the package reads `PC_SDK_SUBSCRIPTION_KEY` from the environment. ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/concepts/sas/))

```python
import planetary_computer
import rasterio
from rasterio.windows import from_bounds
from pystac_client import Client

bbox = [-72.930, 41.305, -72.925, 41.310]  # west, south, east, north
catalog = Client.open(
    "https://planetarycomputer.microsoft.com/api/stac/v1",
    modifier=planetary_computer.sign_inplace,
)
items = list(catalog.search(
    collections=["esa-worldcover"],
    bbox=bbox,
    datetime="2021-01-01/2021-12-31",
).items())
if len(items) != 1:
    raise RuntimeError(f"Expected one tile; found {len(items)}")

with rasterio.open(items[0].assets["map"].href) as src:
    window = from_bounds(*bbox, transform=src.transform)
    window = window.round_offsets().round_lengths()
    data = src.read(1, window=window)
    profile = src.profile.copy()
    profile.update(
        driver="GTiff", height=data.shape[0], width=data.shape[1],
        transform=src.window_transform(window), compress="deflate"
    )
    with rasterio.open("worldcover_yale_2021.tif", "w", **profile) as dst:
        dst.write(data, 1)
```

## Pitfalls

- Pixel values are **land-cover class codes**, not measurements in meters. Use nearest-neighbor resampling for these categories. ([worldcover2021.esa.int](https://worldcover2021.esa.int/data/docs/WorldCover_PUM_V2.0.pdf))
- Coordinates are in EPSG:4326, in degrees. The nominal resolution is 10 meters; use an appropriate projected CRS for area calculations. ([worldcover2021.esa.int](https://worldcover2021.esa.int/data/docs/WorldCover_PUM_V2.0.pdf))
- Value `0` is no data, not a land-cover class. Check coverage before interpreting empty pixels. ([stac.terrabyte.lrz.de](https://stac.terrabyte.lrz.de/browser/collections/esa-worldcover-map-10m-2021-v2/items/ESA_WorldCover_10m_2021_v200_S57E159_Map))
- The 2020 and 2021 maps use different algorithms. Differences can reflect algorithm changes as well as real land-cover changes. ([esa-worldcover.org](https://esa-worldcover.org/en/data-access))
- Larger bounding boxes may cross several tiles. The example deliberately stops if it finds anything other than one tile; a larger workflow must read and mosaic all relevant tiles.
- The data is CC BY 4.0. Give the required ESA WorldCover attribution and cite the appropriate year’s dataset. ([github.com](https://github.com/ESA-WorldCover/esa-worldcover-datasets/blob/main/README.md))

## Sources checked

- Planetary Computer collection: https://planetarycomputer.microsoft.com/api/stac/v1/collections/esa-worldcover ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/api/stac/v1/collections/esa-worldcover))
- Planetary Computer STAC API: https://planetarycomputer.microsoft.com/api/stac/v1/docs ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/api/stac/v1/docs))
- Planetary Computer access tokens: https://planetarycomputer.microsoft.com/docs/concepts/sas/ ([planetarycomputer.microsoft.com](https://planetarycomputer.microsoft.com/docs/concepts/sas/))
- ESA WorldCover data access: https://esa-worldcover.org/en/data-access ([esa-worldcover.org](https://esa-worldcover.org/en/data-access))
- ESA WorldCover product manual: https://worldcover2021.esa.int/data/docs/WorldCover_PUM_V2.0.pdf ([worldcover2021.esa.int](https://worldcover2021.esa.int/data/docs/WorldCover_PUM_V2.0.pdf))
- ESA WorldCover repository and attribution: https://github.com/ESA-WorldCover/esa-worldcover-datasets ([github.com](https://github.com/ESA-WorldCover/esa-worldcover-datasets/blob/main/README.md))
- Planetary Computer catalog asset listing: https://github.com/opengeos/Planetary-Computer-Catalog/blob/master/pc_catalog.json ([github.com](https://github.com/opengeos/Planetary-Computer-Catalog/blob/master/pc_catalog.json))
