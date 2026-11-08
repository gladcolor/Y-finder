---
name: cur-bps
description: "How to access Census Building Permits Survey (U.S. Census Bureau). Use when a study needs this dataset."
ref: cur:bps
source_url: https://www.census.gov/construction/bps/
generated_by: gpt-6.1-terra
generated_on: 2026-11-08
status: draft
---
# Access handbook: Census Building Permits Survey

## What it is

The U.S. Census Bureau’s Building Permits Survey counts new privately owned housing units authorized by permits, along with buildings and permit valuation, across U.S. permit-issuing jurisdictions. Tables are available by place and county, among other geographies, with monthly releases and final annual estimates. ([census.gov](https://www.census.gov/construction/bps/about.html))

## How to get access

Download the files openly from the Census Bureau. No registration, API key, or Yale license is needed for the direct file route. ([www2.census.gov](https://www2.census.gov/econ/bps/Place/))

## Programmatic access

- **Place files:** Start at the place directory listed under Sources checked, then choose a Census region. **County files** are in a separate directory. ([www2.census.gov](https://www2.census.gov/econ/bps/Place/))
- **Selection:** Choose a file by geography, year, and period. Place filenames use `a` for annual, `c` for current month, `y` for year-to-date, and `r` for cumulative monthly data. Download the regional file and filter locally; this route has no bounding-box parameter. ([census.gov](https://www.census.gov/construction/bps/sample/placeasc.pdf))
- **Authentication:** None for direct downloads; no environment variable is needed. A BPS API endpoint was not confirmed.
- **Formats:** Place and county files are comma-delimited `.txt` files with two header rows. Some summary tables are Excel files. ([www2.census.gov](https://www2.census.gov/econ/bps/Place/Northeast%20Region/ne2024a.txt))
- **Rate limits:** No published limit for these file downloads was confirmed. Avoid repeated downloads; save a local copy.

## Python example

This standard-library example downloads the Northeast annual file, filters for New Haven, Connecticut, and saves the matching record with both original header rows. It uses the 2024 file for reproducibility. ([www2.census.gov](https://www2.census.gov/econ/bps/Place/Northeast%20Region/ne2024a.txt))

```python
import csv
import io
from urllib.request import urlopen

url = (
    "https://www2.census.gov/econ/bps/Place/"
    "Northeast%20Region/ne2024a.txt"
)
with urlopen(url, timeout=30) as response:
    text = response.read().decode("utf-8-sig")

rows = list(csv.reader(io.StringIO(text)))
matches = [
    row for row in rows[2:]
    if len(row) > 16 and row[1].strip() == "09"
    and row[16].strip().lower() == "new haven"
]
if not matches:
    raise ValueError("New Haven record not found; check the file layout.")

with open("new_haven_bps_2024.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerows(rows[:2])
    writer.writerows(matches)

print(f"Saved {len(matches)} record(s) to new_haven_bps_2024.csv")
```

## Pitfalls

- **Units are homes, not buildings.** Each structure-size group has separate buildings, housing units, and valuation columns. ([census.gov](https://www.census.gov/construction/bps/methodology.html))
- **This is a table, not a map.** There is no projection or geometry. Join to suitable boundary data using geographic codes, and check whether a permit jurisdiction matches the boundary you intend to map.
- **Reported and estimated values differ.** “Reported only” excludes imputation; other estimates can include it. Missing reports are not necessarily zero permits. ([census.gov](https://www.census.gov/construction/bps/about.html))
- **Check time coverage.** Monthly place data and annual data need not cover the same offices. Earlier monthly estimates, especially before 2022, have different coverage and sampling considerations. ([census.gov](https://www.census.gov/construction/bps/methodology.html))
- **Do not simply sum published monthly files** to reproduce a year-to-date or annual total. Late reports, corrections, and annual processing can change totals. ([census.gov](https://www.census.gov/construction/bps/methodology.html))
- **Keep geographic codes as strings** so leading zeros are not lost. Read both header rows and the file documentation before interpreting columns. ([www2.census.gov](https://www2.census.gov/econ/bps/Place/Northeast%20Region/ne2024a.txt))

## Sources checked

- Census Bureau survey overview: https://www.census.gov/construction/bps/about.html
- Collection and methodology: https://www.census.gov/construction/bps/methodology.html
- Release schedule: https://www.census.gov/construction/bps/schedule.html
- Place file directory: https://www2.census.gov/econ/bps/Place/
- County file directory: https://www2.census.gov/econ/bps/County/
- Place file documentation: https://www.census.gov/construction/bps/sample/placeasc.pdf
- Example 2024 Northeast place file: https://www2.census.gov/econ/bps/Place/Northeast%20Region/ne2024a.txt
- Current summary data: https://www.census.gov/construction/bps/current.html
