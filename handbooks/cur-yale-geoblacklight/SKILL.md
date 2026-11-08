---
name: cur-yale-geoblacklight
description: "How to access Yale Library GeoBlacklight (Yale Library). Use when a study needs this dataset."
ref: cur:yale_geoblacklight
source_url: https://geoblacklight.library.yale.edu
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: Yale Library GeoBlacklight

## What it is

Yale Library GeoBlacklight is a discovery portal for maps and geospatial data, including historical map sheets and GIS layers. Coverage is global and spans the 1700s to the present; scale and resolution vary by item, and no fixed update cycle was confirmed. ([library.yale.edu](https://www.library.yale.edu/collection-development/statements/maps-and-geospatial-information))

## How to get access

Search the portal without registration. Open a record and use its download link if one is available; a public record does not necessarily include a downloadable file.

Some items are restricted to eligible Yale users. Follow the record’s sign-in instructions, or contact Yale’s geospatial team at `geospatialhelp@yale.edu` for help with access. ([guides.library.yale.edu](https://guides.library.yale.edu/gis))

## Programmatic access

- **Portal:** `https://geoblacklight.library.yale.edu`. Its indexed New Haven search uses the place facet `f[dct_spatial_sm][]=New Haven Region.` and `per_page=20`. ([geoblacklight.library.yale.edu](https://geoblacklight.library.yale.edu/?f%5Bdct_spatial_sm%5D%5B%5D=New+Haven+Region.&per_page=20&sort=solr_year_i+desc%2C+dc_title_sort+asc))
- **API:** A third-party registry lists `https://geoblacklight.library.yale.edu/catalog.json`, but I could not verify its response or parameters: the portal returned HTTP 403 to the search tool. Do not assume that this endpoint currently works. ([dateno.io](https://dateno.io/registry/catalog/cdi00005005/))
- **Authentication:** No API key or environment-variable authentication method was confirmed. For restricted items, use the item’s Yale access route rather than trying to automate a login.
- **Formats:** Downloads depend on the record and its source. GeoBlacklight software supports formats including Shapefile, GeoJSON, GeoTIFF, and scanned images; this does not guarantee each format is available from Yale. ([geoblacklight.org](https://geoblacklight.org/about/))
- **Rate limits:** No published Yale portal limit was confirmed. Make requests slowly and stop on HTTP 429 or repeated errors.

## Python example

This example requires a manual discovery step because I could not confirm a direct file endpoint. In the portal, search for **New Haven Region**, open a public record with a download, and set `YALE_MAP_DOWNLOAD_URL` to its direct file URL. Set `OUTPUT_FILE` to a filename with the correct extension. The code downloads the selected file, not a bounding-box clip. ([geoblacklight.library.yale.edu](https://geoblacklight.library.yale.edu/?f%5Bdct_spatial_sm%5D%5B%5D=New+Haven+Region.&per_page=20&sort=solr_year_i+desc%2C+dc_title_sort+asc))

```python
import os
from pathlib import Path
from urllib.request import Request, urlopen

url = os.environ["YALE_MAP_DOWNLOAD_URL"]
output = Path(os.environ["OUTPUT_FILE"])

request = Request(url, headers={"User-Agent": "Yale GIS research example"})
with urlopen(request, timeout=60) as response:
    content_type = response.headers.get("Content-Type", "")
    if "text/html" in content_type.lower():
        raise ValueError("Received HTML, not a data file; check the URL or login.")
    output.write_bytes(response.read())

print(f"Saved {output}")
```

## Pitfalls

- A map sheet is not a uniform spatial unit. Check each item’s scale and geographic extent.
- Check the coordinate reference system and measurement units before combining layers or measuring distances.
- A scanned map may not be georeferenced. It may need alignment before use with GIS layers.
- Search results and metadata are not the map data itself. Some records have no downloadable file.
- Check each item’s rights and license before reuse or redistribution. A public catalog record does not guarantee unrestricted reuse.
- A 403, a login page, or an HTML file instead of data can indicate restricted access or a blocked request.

## Sources checked

- Yale GeoBlacklight, New Haven results: https://geoblacklight.library.yale.edu/?f%5Bdct_spatial_sm%5D%5B%5D=New+Haven+Region.&per_page=20&sort=solr_year_i+desc%2C+dc_title_sort+asc
- Yale Library GIS guide: https://guides.library.yale.edu/gis
- Yale Library collection policy: https://www.library.yale.edu/collection-development/statements/maps-and-geospatial-information
- GeoBlacklight project overview and supported formats: https://geoblacklight.org/about/
- Third-party endpoint listing, not independently verified: https://dateno.io/registry/catalog/cdi00005005/
