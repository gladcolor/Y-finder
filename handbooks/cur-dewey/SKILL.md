---
name: cur-dewey
description: "How to access Dewey Data (Yale subscription) (Dewey). Use when a study needs this dataset."
ref: cur:dewey
source_url: https://app.deweydata.io
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: Dewey Data (Yale subscription)

## What it is

Dewey is a marketplace of commercial research tables, including US points of interest, foot traffic, mobility, and consumer data. Coverage, observation level, historical period, and refresh schedule vary by dataset; check the dataset’s description and data dictionary. ([docs.deweydata.io](https://docs.deweydata.io/article/discover-data))

## How to get access

Access is through a **Yale subscription**, not an open download. At `https://app.deweydata.io`, sign in or register with your Yale email; the Y-finder note says approval can take up to 48 hours. Yale subscribes to a subset of Dewey data, so check the Library’s available-datasets list or ask the Library before choosing a dataset. ([guides.library.yale.edu](https://guides.library.yale.edu/c.php?g=295839&p=10464780))

Choose a licensed dataset, select **Get Data**, apply available place filters, and add it to a project. For a quick inspection, the dataset page also offers **Get Sample**, a 100-row download that is not necessarily specific to a place. ([docs.deweydata.io](https://docs.deweydata.io/article/access-and-publish))

## Programmatic access

- **API:** In the project, choose **Get data → Bulk API** and copy the project-specific API URL and folder ID. Dewey documents URLs under `https://api.deweydata.io/api/v1/external/data/`; use the URL supplied for your project rather than constructing one. ([docs.deweydata.io](https://docs.deweydata.io/article/working-with-r))
- **Parameters:** Choose geographic filters in the app where available. For date-partitioned data, the client supports inclusive `partition-key-after` and `partition-key-before` dates. I could not confirm a general bounding-box API parameter. ([docs.deweydata.io](https://docs.deweydata.io/article/access-and-publish))
- **Authentication:** Under **Bulk API**, issue a key. Store it in `DEWEY_API_KEY`; do not put it in code or share it. Store the project folder ID in `DEWEY_FOLDER_ID` for the example below. ([docs.deweydata.io](https://docs.deweydata.io/article/troubleshooting-tips))
- **Formats:** CSV downloads are available for some datasets; bulk files and compression vary by dataset. Check the selected dataset and downloaded filenames. ([guides.nyu.edu](https://guides.nyu.edu/dewey-data/access))
- **Rate limits:** Dewey says API requests are rate-limited but does not specify a general numeric limit in the client documentation. It recommends the default eight download workers. ([github.com](https://github.com/Dewey-Data/deweypy))

## Python example

First, in Dewey, add a dataset to a project and filter it to a small place if that dataset supports geographic filtering. Copy the folder ID for **that project**. Install with `pip install deweypy`, then set `DEWEY_API_KEY` and `DEWEY_FOLDER_ID`.

This downloads the first available file from the filtered project into `dewey_sample/`. **A file is not necessarily small**; check the project’s estimated size before running. If place filtering is unavailable, this is not a place-specific sample. ([docs.deweydata.io](https://docs.deweydata.io/article/troubleshooting-tips))

```python
import os
import deweypy as ddp

ddp.set_api_key(os.environ["DEWEY_API_KEY"])
folder_id = os.environ["DEWEY_FOLDER_ID"]

files = ddp.get_dataset_files(folder_id=folder_id)
if files.empty:
    raise RuntimeError("No files found; check the project and its filters.")

ddp.download_files(files.head(1), output_dir="./dewey_sample")
print("Saved the first available file in dewey_sample/")
```

## Pitfalls

- A row may represent a place, a place and time period, or another unit. Check the dataset dictionary before counting places.
- Check coordinate column names, coordinate reference system, and missing coordinates before mapping; do not assume every table is ready for GIS.
- Coverage and missing values vary. A missing place or visit count is not automatically zero. ([docs.deweydata.io](https://docs.deweydata.io/article/discover-data))
- Data is licensed. Do not publish or redistribute raw files; check Yale’s agreement and Dewey’s rules before sharing or retaining data after access ends. ([docs.deweydata.io](https://docs.deweydata.io/article/post-term-use-replication-requirements))
- A `403` may mean an expired or missing key. A permissions error can also mean the folder ID belongs to the wrong project. ([docs.deweydata.io](https://docs.deweydata.io/article/troubleshooting-tips))

## Sources checked

- [Yale Library: Economics Research Guide](https://guides.library.yale.edu/c.php?g=295839&p=10464780)
- [Dewey: Discover Data](https://docs.deweydata.io/article/discover-data)
- [Dewey: Access and Publish](https://docs.deweydata.io/article/access-and-publish)
- [Dewey: Working with R](https://docs.deweydata.io/article/working-with-r)
- [Dewey: Troubleshooting Tips](https://docs.deweydata.io/article/troubleshooting-tips)
- [Dewey Python client](https://github.com/Dewey-Data/deweypy)
- [Dewey: Post-Term Use and Replication Requirements](https://docs.deweydata.io/article/post-term-use-replication-requirements)
- [NYU Library: Dewey Data access options](https://guides.nyu.edu/dewey-data/access)
