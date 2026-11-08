---
name: cur-cteco
description: "How to access CT ECO (UConn): orthophotos, lidar, land cover (UConn CLEAR / CT DEEP). Use when a study needs this dataset."
ref: cur:cteco
source_url: https://cteco.uconn.edu
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: CT ECO (UConn): orthophotos, lidar, land cover

## What it is

CT ECO is a collection of Connecticut aerial imagery, lidar-derived elevation data, lidar point clouds, and land cover data maintained by UConn CLEAR and CT DEEP. Coverage and resolution vary by layer: historical aerial photos go back to 1934, the 2016 imagery has 3-inch pixels, and some land cover layers have 30-meter pixels; the catalog’s 0.5 m value is not universal. Updates follow individual mapping projects rather than one confirmed fixed cycle. ([maps.cteco.uconn.edu](https://maps.cteco.uconn.edu/data/))

## How to get access

Access is open. No Yale license, registration, or API key is needed for the public image-service example below.

For original imagery tiles, town mosaics, elevation tiles, contours, and point clouds, start at the CT ECO download page and select the download tool. For land cover, follow the download instructions on the page for the chosen land cover product; some downloads come from USGS or MRLC rather than CT ECO. ([maps.cteco.uconn.edu](https://maps.cteco.uconn.edu/download/))

## Programmatic access

- **2016 imagery API:** `https://cteco.uconn.edu/ctraster/rest/services/images/Ortho_2016/ImageServer` supports the `exportImage` operation. This is a rendered or resampled image export, not necessarily an original download tile. ([cteco.uconn.edu](https://cteco.uconn.edu/ctraster/rest/services/images/Ortho_2016/ImageServer))
- **Other layers:** Browse the CT ECO map-services page for imagery, elevation, and land cover endpoints. For example, the 1985 land cover map service is `https://cteco.uconn.edu/ctmaps/rest/services/Landcover/LandCover_1985_ANLCD/MapServer`. ([maps.cteco.uconn.edu](https://maps.cteco.uconn.edu/map-services/))
- **Export parameters:** Supply `bbox` in west, south, east, north order; set `bboxSR` and `imageSR` to the coordinate reference system, `size` to width and height in pixels, `format` to the image format, and `f` to the response format. ([cteco.uconn.edu](https://cteco.uconn.edu/ctraster/rest/services/images/Ortho_2016/ImageServer/exportImage?bbox=-8208572.6952,5008472.534071529,-7990330.048514707,5169936.770800003))
- **Authentication:** The example uses the public service without a key, so no authentication environment variable is needed. Do not add credentials to the script.
- **Formats:** The example requests TIFF. Original lidar point clouds can be downloaded as LAS files; available formats and download routes vary by product. ([maps.cteco.uconn.edu](https://maps.cteco.uconn.edu/projects/lidar3d/lidar3d_layers/))
- **Limits:** The 2016 service lists a maximum export width of 15,000 pixels and height of 4,100 pixels. I could not confirm a published request-rate limit; make modest requests and retry slowly if the server is busy. ([cteco.uconn.edu](https://cteco.uconn.edu/ctraster/rest/services/images/Ortho_2016/ImageServer))

## Python example

This uses `requests` (`pip install requests`) to save a small 2016 imagery export near Yale as `yale_2016.tif`. The bounding box uses longitude and latitude in EPSG:4326. The requested 512 × 512 image is resampled and is not a native-resolution tile. ([cteco.uconn.edu](https://cteco.uconn.edu/ctraster/rest/services/images/Ortho_2016/ImageServer/exportImage?bbox=-8208572.6952,5008472.534071529,-7990330.048514707,5169936.770800003))

```python
from pathlib import Path
import requests

url = (
    "https://cteco.uconn.edu/ctraster/rest/services/"
    "images/Ortho_2016/ImageServer/exportImage"
)
params = {
    "bbox": "-72.928,41.310,-72.926,41.312",
    "bboxSR": "4326",
    "imageSR": "4326",
    "size": "512,512",
    "format": "tiff",
    "f": "json",
}
response = requests.get(url, params=params, timeout=60)
response.raise_for_status()
result = response.json()
if "error" in result:
    raise RuntimeError(result["error"])
image = requests.get(result["href"], timeout=60)
image.raise_for_status()
Path("yale_2016.tif").write_bytes(image.content)
print("Saved yale_2016.tif")
```

## Pitfalls

- **Check units and vertical datum.** For example, the 2023 statewide lidar uses Connecticut State Plane feet and NAVD88 elevations in feet; other lidar products use meters. ([maps.cteco.uconn.edu](https://maps.cteco.uconn.edu/data/lidar/ct_lidar/))
- **Check projections.** The example supplies geographic coordinates in degrees, while the 2016 image service reports its extent in Web Mercator. Set the bounding-box reference system explicitly. ([cteco.uconn.edu](https://cteco.uconn.edu/ctraster/rest/services/images/Ortho_2016/ImageServer))
- **Check coverage and dates.** Some lidar and imagery products cover only coastal or regional areas. A blank export can mean the requested place lies outside that product’s coverage. ([maps.cteco.uconn.edu](https://maps.cteco.uconn.edu/data/lidar/ct_lidar/))
- **Do not treat an export as an original tile.** Output dimensions can resample the data. For analysis requiring source pixels or point clouds, use the product’s download route and metadata.
- **Check product-specific terms and attribution.** CT ECO links to data from several providers; review the metadata and terms for the exact product before reuse.

## Sources checked

- [CT ECO data overview](https://maps.cteco.uconn.edu/data/)
- [CT ECO download page](https://maps.cteco.uconn.edu/download/)
- [CT ECO map and image services](https://maps.cteco.uconn.edu/map-services/)
- [2016 imagery service](https://cteco.uconn.edu/ctraster/rest/services/images/Ortho_2016/ImageServer)
- [2016 imagery export operation](https://cteco.uconn.edu/ctraster/rest/services/images/Ortho_2016/ImageServer/exportImage)
- [Connecticut lidar datasets](https://maps.cteco.uconn.edu/data/lidar/ct_lidar/)
- [Land cover download guidance](https://maps.cteco.uconn.edu/projects/landcover/cl_nlcd/)
- [2016 lidar download layer](https://maps.cteco.uconn.edu/projects/lidar3d/lidar3d_layers/)
