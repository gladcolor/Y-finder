---
name: cur-yale-dataverse
description: "How to access Yale Dataverse (Yale Library). Use when a study needs this dataset."
ref: cur:yale_dataverse
source_url: https://dataverse.yale.edu
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: Yale Dataverse

## What it is

Yale Dataverse is Yale Library’s repository for research datasets, not a single GIS layer. Geographic and time coverage, spatial resolution, units, and update cycles vary by deposit; some datasets are not geographic. ([dataverse.yale.edu](https://dataverse.yale.edu/))

## How to get access

Search the repository and download public files without a Yale license or API key. Some files are restricted and may require an access request. Yale researchers can deposit data free of charge using their Yale login. ([dataverse.yale.edu](https://dataverse.yale.edu/))

## Programmatic access

- **Base:** `https://dataverse.yale.edu`. The documented Dataverse routes are `/api/search`, `/api/datasets/:persistentId/versions/:latest`, and `/api/access/datafile/{id}`. I could not independently test these API routes on Yale’s server during this check. ([guides.dataverse.org](https://guides.dataverse.org/en/latest/api/search.html))
- **Search:** Use `q`, `type=dataset` or `type=file`, and `per_page`. The documented `geo_point` and `geo_radius` search dataset bounding-box metadata; they do not clip files. ([guides.dataverse.org](https://guides.dataverse.org/en/latest/api/search.html))
- **Authentication:** Public files normally need no key. For authorized access, obtain a Dataverse API token from your account and pass the environment variable `DATAVERSE_API_TOKEN` in the `X-Dataverse-key` header. A token does not itself grant restricted-file permission. ([guides.dataverse.org](https://guides.dataverse.org/en/latest/api/getting-started.html))
- **Formats:** Metadata responses are JSON. Deposited formats vary; the example below is a zipped shapefile. Ingested tabular files may have a tab-delimited download or an original-format option. ([dataverse.yale.edu](https://dataverse.yale.edu/dataset.xhtml?persistentId=doi%3A10.60600%2FYU%2FKCMUTC))
- **Rate limits:** I could not confirm a Yale-specific limit. Make requests conservatively and handle errors and retries.

## Python example

This downloads the approximately 26 KB New Haven ZIP-code-area shapefile in meters. It downloads the whole place file, not a clipped sample. Install `requests` first. ([dataverse.yale.edu](https://dataverse.yale.edu/dataset.xhtml?persistentId=doi%3A10.60600%2FYU%2FKCMUTC))

```python
import os
from pathlib import Path
import requests

base = "https://dataverse.yale.edu"
doi = "doi:10.60600/YU/KCMUTC"
filename = "NewHaven_ZipCodeAreas_2025_polygons_meters.zip"
token = os.getenv("DATAVERSE_API_TOKEN")
headers = {"X-Dataverse-key": token} if token else {}

metadata = requests.get(
    f"{base}/api/datasets/:persistentId/versions/:latest",
    params={"persistentId": doi}, headers=headers, timeout=30,
)
metadata.raise_for_status()
files = metadata.json()["data"]["files"]
match = next((f for f in files if f["dataFile"]["filename"] == filename), None)
if match is None:
    raise RuntimeError("File not found; check the dataset page and version.")

file_id = match["dataFile"]["id"]
with requests.get(
    f"{base}/api/access/datafile/{file_id}",
    headers=headers, stream=True, timeout=60,
) as response:
    response.raise_for_status()
    with Path(filename).open("wb") as output:
        for chunk in response.iter_content(chunk_size=65536):
            output.write(chunk)
print(f"Saved {filename}")
```

## Pitfalls

- Check each dataset’s units and coordinate reference system before mapping or combining layers. Some Yale deposits offer separate feet and meters files. ([dataverse.yale.edu](https://dataverse.yale.edu/dataset.xhtml?persistentId=doi%3A10.60600%2FYU%2FCNYR3J))
- A geographic search can miss relevant datasets with missing bounding-box metadata. A match does not guarantee that every file covers your area. ([guides.dataverse.org](https://guides.dataverse.org/en/6.9/user/find-use-data.html))
- Check missing values, dates, and the dataset version before analysis.
- Read each dataset’s terms and cite its requested version. Licenses and access restrictions vary. ([ask.library.yale.edu](https://ask.library.yale.edu/dataverse/faq/425314))
- If a download fails, check whether the file is restricted, whether its name or version changed, and whether access has been granted.

## Sources checked

- Yale Dataverse: https://dataverse.yale.edu ([dataverse.yale.edu](https://dataverse.yale.edu/))
- New Haven ZIP Code Areas deposit: https://dataverse.yale.edu/dataset.xhtml?persistentId=doi%3A10.60600%2FYU%2FKCMUTC ([dataverse.yale.edu](https://dataverse.yale.edu/dataset.xhtml?persistentId=doi%3A10.60600%2FYU%2FKCMUTC))
- Dataverse Search API: https://guides.dataverse.org/en/latest/api/search.html ([guides.dataverse.org](https://guides.dataverse.org/en/latest/api/search.html))
- Dataverse Native API: https://guides.dataverse.org/en/6.10/api/native-api.html ([guides.dataverse.org](https://guides.dataverse.org/en/6.10/api/native-api.html))
- Dataverse Data Access API: https://guides.dataverse.org/en/6.5/api/dataaccess.html ([guides.dataverse.org](https://guides.dataverse.org/en/6.5/api/dataaccess.html))
- Yale Library on deposits: https://web.library.yale.edu/printpdf/news/2019/10/library-provides-new-open-access-data-repository-researchers ([web.library.yale.edu](https://web.library.yale.edu/printpdf/news/2019/10/library-provides-new-open-access-data-repository-researchers))
