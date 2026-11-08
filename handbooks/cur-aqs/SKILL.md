---
name: cur-aqs
description: "How to access EPA Air Quality System (AirData) (U.S. EPA). Use when a study needs this dataset."
ref: cur:aqs
source_url: https://www.epa.gov/outdoor-air-quality-data
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: EPA Air Quality System (AirData)

## What it is

EPA AirData provides tables of monitored air pollution, including PM2.5, ozone, and NO2, at U.S. monitor points. Coverage includes 1980 onward, but varies by pollutant and monitor; files include hourly, daily, and annual data. EPA refreshes its pre-generated files twice a year, in June and December; AQS is not a real-time source. ([aqs.epa.gov](https://aqs.epa.gov/aqsweb/airdata/download_files.html))

## How to get access

Download pre-generated files openly from EPA. No registration, API key, or Yale license is needed for these files. The separate AQS API requires free registration and a key. ([epa.gov](https://www.epa.gov/outdoor-air-quality-data/do-i-need-request-permission-use-monitoring-data-and-graphics-airdata))

## Programmatic access

- **Bulk downloads:** Select files at `https://aqs.epa.gov/aqsweb/airdata/download_files.html`. They are organized by pollutant or parameter, year, and time resolution. Each file covers the nation, so filter locally for a place or bounding box. ([aqs.epa.gov](https://aqs.epa.gov/aqsweb/documents/about_aqs_data.html))
- **Example file:** `https://aqs.epa.gov/aqsweb/airdata/daily_88101_2023.zip` contains 2023 daily data for PM2.5 parameter `88101`. ([aqs.epa.gov](https://aqs.epa.gov/aqsweb/airdata/download_files.html))
- **Authentication:** Bulk files need none. If using the separate API, register through EPA’s API documentation and store the email and key in environment variables rather than code. The example below uses bulk download and needs no key. ([aqs.epa.gov](https://aqs.epa.gov/aqsweb/documents/data_api.html))
- **Formats:** Bulk files are ZIP-compressed CSV tables with headers. ([aqs.epa.gov](https://aqs.epa.gov/aqsweb/airdata/FileFormats.html))
- **Rate limits:** I could not confirm a published rate limit for bulk downloads. Avoid unnecessary repeat downloads.

## Python example

This downloads the national 2023 file, then saves rows in a bounding box around New Haven, Connecticut. It uses only the Python standard library.

```python
import csv
import io
import urllib.request
import zipfile

url = "https://aqs.epa.gov/aqsweb/airdata/daily_88101_2023.zip"
output = "new_haven_pm25_2023.csv"
south, north, west, east = 41.2, 41.4, -73.0, -72.8

with urllib.request.urlopen(url, timeout=120) as response:
    archive = response.read()

with zipfile.ZipFile(io.BytesIO(archive)) as z:
    name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    with z.open(name) as raw, open(output, "w", newline="", encoding="utf-8") as out:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        writer = csv.DictWriter(out, fieldnames=reader.fieldnames)
        writer.writeheader()
        count = 0
        for row in reader:
            try:
                lat, lon = float(row["Latitude"]), float(row["Longitude"])
            except (ValueError, TypeError):
                continue
            if south <= lat <= north and west <= lon <= east:
                writer.writerow(row)
                count += 1

print(f"Saved {count} rows to {output}")
```

## Pitfalls

- Check the **units** and pollutant parameter code before comparing values. Different parameters and summaries are not interchangeable.
- Latitude and longitude are in decimal degrees. Check the reported **datum** before assigning a coordinate system or combining data with other GIS layers. ([aqs.epa.gov](https://aqs.epa.gov/aqsweb/airdata/FileFormats.html))
- Missing records do not mean zero pollution. Monitor locations and operating periods vary.
- A site can have multiple monitors for the same pollutant. Preserve state, county, site, parameter, and POC identifiers. Summary files can also contain multiple pollutant standards. ([aqs.epa.gov](https://aqs.epa.gov/aqsweb/airdata/FileFormats.html))
- Recent data can be delayed or revised. Check the file’s “as of” date and EPA’s PM2.5 advisory when relevant. ([aqs.epa.gov](https://aqs.epa.gov/aqsweb/documents/data_api.html))

## Sources checked

- [EPA pre-generated data files](https://aqs.epa.gov/aqsweb/airdata/download_files.html)
- [EPA file format documentation](https://aqs.epa.gov/aqsweb/airdata/FileFormats.html)
- [EPA AQS API documentation](https://aqs.epa.gov/aqsweb/documents/data_api.html)
- [EPA introduction to AQS data](https://aqs.epa.gov/aqsweb/documents/about_aqs_data.html)
- [EPA statement on use of AirData](https://www.epa.gov/outdoor-air-quality-data/do-i-need-request-permission-use-monitoring-data-and-graphics-airdata)
