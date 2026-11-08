---
name: cur-goesaod
description: "How to access GOES-16/18 ABI aerosol optical depth on AWS (NOAA). Use when a study needs this dataset."
ref: cur:goesaod
source_url: https://registry.opendata.aws/noaa-goes/
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: GOES-16/18 ABI aerosol optical depth on AWS

## What it is

NOAA’s GOES ABI aerosol optical depth (AOD) product measures aerosols in the atmospheric column at 550 nm. It covers the continental United States (CONUS) on a nominal 2 km grid and is useful for studying smoke; daytime CONUS observations are normally produced every five minutes, and AWS adds data as available. ([star.nesdis.noaa.gov](https://www.star.nesdis.noaa.gov/atmospheric-composition-training/documents/G18_ABI_AOD_ReadMe.pdf))

## How to get access

Download openly from AWS S3. No registration, API key, AWS account, or Yale license is required. ([registry.opendata.aws](https://registry.opendata.aws/noaa-goes/))

## Programmatic access

- **Buckets:** `s3://noaa-goes16/` and `s3://noaa-goes18/`, in AWS region `us-east-1`. ([registry.opendata.aws](https://registry.opendata.aws/noaa-goes/))
- **Path:** `ABI-L2-AODC/YYYY/DDD/HH/`, where `DDD` is the zero-padded day of year and `HH` is the UTC hour. List objects to find available scans. ([github.com](https://github.com/awslabs/open-data-docs/blob/main/docs/noaa/noaa-goes16/README.md?plain=1))
- **Authentication:** Use unsigned S3 requests. No credentials or environment variables are needed. ([registry.opendata.aws](https://registry.opendata.aws/noaa-goes/))
- **Format:** NetCDF4 (`.nc`), with AOD and quality information. ([ncei.noaa.gov](https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc%3AC01511))
- **Rate limits:** No dataset-specific limit was confirmed. Handle temporary errors with retries and avoid unnecessary bulk requests.

## Python example

This downloads one historical CONUS file containing New Haven, Connecticut. The saved file covers all of CONUS; it is not clipped to the city. Install `boto3` first.

```python
import boto3
from botocore import UNSIGNED
from botocore.config import Config

bucket = "noaa-goes16"
prefix = "ABI-L2-AODC/2024/200/18/"  # July 18, 2024, 18 UTC
s3 = boto3.client(
    "s3", region_name="us-east-1",
    config=Config(signature_version=UNSIGNED)
)

response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=20)
files = [item["Key"] for item in response.get("Contents", [])
         if item["Key"].endswith(".nc")]
if not files:
    raise RuntimeError("No files found; try another daytime hour.")

key = sorted(files)[0]
s3.download_file(bucket, key, "new_haven_conus_aod.nc")
print("Saved", key, "to new_haven_conus_aod.nc")
```

## Pitfalls

- AOD is **dimensionless**, not a surface PM2.5 concentration. ([ospo.noaa.gov](https://www.ospo.noaa.gov/resources/documents/PUG/GS%20Series%20416-R-PUG-L2%20Plus-0349%20Vol%205%20v3.0%20final.pdf))
- The raster uses a satellite fixed grid, not a regular latitude–longitude grid. Use the file’s projection metadata when mapping or clipping it. ([ospo.noaa.gov](https://www.ospo.noaa.gov/resources/documents/PUG/GS%20Series%20416-R-PUG-L2%20Plus-0349%20Vol%205%20v3.0%20final.pdf))
- Clouds, darkness, and some bright surfaces cause missing retrievals. Check the data-quality flags before analysis. ([star.nesdis.noaa.gov](https://www.star.nesdis.noaa.gov/atmospheric-composition-training/documents/G18_ABI_AOD_ReadMe.pdf))
- The catalog’s 2017-to-present range does not mean both satellites have data throughout that period. GOES-19 replaced GOES-16 as GOES-East on April 7, 2025; its separate bucket is `s3://noaa-goes19/`. ([ncei.noaa.gov](https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc%3AC01511))
- NOAA requests attribution for unaltered data. Do not imply NOAA endorsement or present modified data as unaltered. ([registry.opendata.aws](https://registry.opendata.aws/noaa-goes/))

## Sources checked

- https://registry.opendata.aws/noaa-goes/
- https://github.com/awslabs/open-data-docs/blob/main/docs/noaa/noaa-goes16/README.md
- https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc%3AC01511
- https://www.star.nesdis.noaa.gov/atmospheric-composition-training/documents/G18_ABI_AOD_ReadMe.pdf
- https://www.ospo.noaa.gov/resources/documents/PUG/GS%20Series%20416-R-PUG-L2%20Plus-0349%20Vol%205%20v3.0%20final.pdf
- https://www.ncei.noaa.gov/products/satellite/goes-r
