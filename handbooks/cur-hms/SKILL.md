---
name: cur-hms
description: "How to access NOAA Hazard Mapping System smoke and fire (NOAA). Use when a study needs this dataset."
ref: cur:hms
source_url: https://www.ospo.noaa.gov/products/land/hms.html
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: NOAA Hazard Mapping System smoke and fire

## What it is

NOAA’s Hazard Mapping System (HMS) combines satellite observations and analyst interpretation to map smoke plumes and detect fires across North America, Hawaii, and the Caribbean; the smoke archive begins in 2005. Smoke is mapped as polygons classified light, medium, or heavy; fire detections are points, and there is no single fixed polygon resolution. Products are updated daily, with updates during the day. ([ospo.noaa.gov](https://ospo.noaa.gov/products/land/hms.html))

## How to get access

Download files openly from NOAA. No registration, API key, or Yale license is needed. On the HMS page, choose **Download Historical Fire/Smoke Data** to select a product, format, and date range. ([ospo.noaa.gov](https://ospo.noaa.gov/products/land/hms.html))

## Programmatic access

- **Smoke archive:** `https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/`. The Shapefile directory has year folders and annual bundles. ([satepsanone.nesdis.noaa.gov](https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/))
- **Fire archive:** `https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Fire_Points/Shapefile/Annual_Bundles/`. ([satepsanone.nesdis.noaa.gov](https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Fire_Points/Shapefile/Annual_Bundles/))
- **Selection:** Choose a year or date in the archive; the file download does not accept a bounding-box parameter. Download first, then filter locally. Annual smoke files use names such as `hms_smoke2023.zip`. ([satepsanone.nesdis.noaa.gov](https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/Shapefile/Annual_Bundles/))
- **Authentication:** None for these open downloads; no environment variable is needed.
- **Formats:** Zipped Shapefile and KML are available. A Shapefile ZIP contains companion files such as `.shp`, `.dbf`, `.shx`, and `.prj`. ([ospo.noaa.gov](https://www.ospo.noaa.gov/data/messages/2022/05/MSG_20220527_1253.html))
- **Rate limits:** I could not confirm a published limit. Download only what you need and avoid repeated requests.

## Python example

Install `geopandas` and `requests` first. This downloads the **full 2023 annual smoke file**, selects polygons intersecting a box around New Haven, and saves the selection as GeoJSON. The box is in longitude and latitude; output polygons are not clipped to it.

```python
from pathlib import Path
import geopandas as gpd
import requests
from shapely.geometry import box

url = (
    "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/"
    "Smoke_Polygons/Shapefile/Annual_Bundles/hms_smoke2023.zip"
)
response = requests.get(url, timeout=120)
response.raise_for_status()
archive = Path("hms_smoke2023.zip")
archive.write_bytes(response.content)

smoke = gpd.read_file(f"zip://{archive.resolve()}")
if smoke.crs is None:
    raise ValueError("Source CRS is missing; inspect the Shapefile first.")
smoke = smoke.to_crs("EPSG:4326")
new_haven_box = box(-73.1, 41.2, -72.8, 41.4)
sample = smoke[smoke.intersects(new_haven_box)]

if sample.empty:
    print("No intersecting polygons found.")
else:
    sample.to_file("new_haven_smoke_2023.geojson", driver="GeoJSON")
    print(f"Saved {len(sample)} polygons.")
```

## Pitfalls

- Smoke density is a category, not a measurement of surface PM2.5 or smoke height. Older files may use numeric density codes rather than labels. ([ospo.noaa.gov](https://www.ospo.noaa.gov/data/messages/2022/05/MSG_20220527_1253.html))
- Check the source CRS before mapping or combining layers. Reproject to a suitable projected CRS before calculating area; longitude and latitude are degrees, not meters.
- Cloud cover, darkness, and data interruptions can hide smoke or fires. A missing polygon is not proof of clear air; NOAA says some processing gaps are not backfilled. ([ospo.noaa.gov](https://www.ospo.noaa.gov/products/land/hms.html?os=vbk))
- Fire points are detections, not fire perimeters or burned area. Some detections can be other heat sources. ([ospo.noaa.gov](https://ospo.noaa.gov/products/land/hms.html))
- Keep all Shapefile companion files together. Check dates and attributes when combining historical files, since schemas have changed. ([ospo.noaa.gov](https://www.ospo.noaa.gov/data/messages/2022/05/MSG_20220527_1253.html))

## Sources checked

- NOAA HMS product and download page: https://www.ospo.noaa.gov/products/land/hms.html
- NOAA smoke Shapefile archive and annual bundles: https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/Shapefile/Annual_Bundles/
- NOAA fire Shapefile annual bundles: https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Fire_Points/Shapefile/Annual_Bundles/
- NOAA format and schema change notice: https://www.ospo.noaa.gov/data/messages/2022/05/MSG_20220527_1253.html
- NOAA smoke product notes: https://www.ospo.noaa.gov/smoke-data/2026A030245.html
