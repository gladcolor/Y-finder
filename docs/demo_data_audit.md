# Demo data audit (2026-11-09)

Are the datasets Y-finder recommends in the 18 demo cases suitable for their questions? Each recommended dataset was
checked against four things:

1. **Measure:** does it measure the graph factor it was picked for, or only a stand-in?
2. **Scope:** does it cover the place, the years and the detail the question needs?
3. **Access:** is access realistic (open, Yale-licensed, or registration)?
4. **Preview:** does Y-finder's preview show the variable the question needs?

Method: every dataset link was opened (73 links: all work; FEMA answers scripts with 403, and the Zoning Atlas moved
to pro-homesct.org). Every preview was rebuilt live and Y-finder's exploration was run on it.

Verdicts: **Fits** (right data, right preview), **Fits, fixed** (right data, the preview was wrong and is fixed),
**Partial** (useful, but a stand-in or a narrower scope), **Weak** (little use for this question), **Gap** (a needed
dataset is missing).

## What was wrong and is now fixed

| Problem found | Demo | Fix |
|---|---|---|
| The ACS preview always showed median household income, even when the factor was "Latino and Black resident shares" | Street vending | ACS previews choose the measure from the pick's factors: race and Hispanic-origin shares (ACS B03002) for race or ethnicity, income otherwise. Tracts with fewer than 200 residents (parks, airports) get no share |
| The LAPD previews were the portal's first 50,000 rows: 81% from 2010 in one file, 69% from 2023 in the other | Street vending | Portal tables with a date column are sampled evenly over the study years; counts per year come from the portal's own count of every row |
| Vending arrests were only the few in the sample | Street vending | Once the AI model picks the matching charge (ILLEGAL STREET/SIDEWALK SALES), Y-finder fetches every matching record from the portal: 4,228 arrests from 2010 to 2019, most in 2013 (1,145), 17 in 2017 |
| The silver registry preview drew the treasury points without any silver amounts; the amounts sit in a CSV beside the shapefile | Silver and treasuries | Dataverse previews join a dataset's table to its own map file by a shared ID (never by a year column or a row number); pesos are summed per treasury over the study years, and the trend covers only the places on the map |
| "NOAA HMS smoke" showed three prescribed-fire perimeters that Y-finder's browser found on the HMS page | Wildfire smoke | The HMS card draws NOAA's daily smoke polygons for each day of the window, by density; the explorer classes each monitor-day by the smoke over the monitor |
| CT ECO previews offered a 1.6 GB contour file | Mosquito habitat, lost shoreline | The CT ECO card draws the service the question needs: the 1934 aerial mosaic and historical shorelines for historical questions, 1 m water or tree canopy (2021), else 2023 orthophotos |
| A question about weekday rush hours got daily NO2 averages | TEMPO rush hour | EPA AirData previews add the weekday and weekend hour-of-day profile when the question is about hours |
| The Malaria Atlas link is a "printables" page; nothing was drawn | Malaria in Kenya | Malaria Atlas pages draw MAP's own map service (2024 parasite rate) |
| "f.txt, 0 rows" counted as a table preview (an ad-tracker pixel) | Zoning, Yale-Myers Forest | Files with fewer than three rows are never previews; Y-finder's browser ignores advertising and analytics hosts |
| The Census race service answers 50 tracts at a time; only 550 of 1,754 tracts came back | Street vending | ArcGIS paging continues up to 80 pages |

## Datasets the catalog lacked (now added)

| Card | For |
|---|---|
| Connecticut mosquito trapping and arbovirus testing (CAES) | Mosquito habitat: the outcome itself (where Culex are caught and test positive) |
| USGS Water Data: daily streamflow at streamgages | Des Moines streams: measured discharge, not only gage locations |
| NOAA Shoreline Data Explorer: historical T-sheets and national shoreline | Lost shoreline, Long Wharf fill: 1800s Coast Survey shorelines as vectors |
| Malaria Atlas Project: modelled parasite rate and incidence | Malaria in Kenya: a proper card instead of a printables link |

## Demo by demo

Four demos were re-recorded with the new cards in the catalog (all their checks pass); the others keep their
recordings, and their previews changed only through the fixes above.

### Yale research cases

**Where should New Haven plant street trees next?** Landsat (summer surface heat): fits. NAIP (canopy gaps): fits,
leaf-on 0.6 m. URI street tree inventory: fits (on request). Census block groups and New Haven neighborhoods: fit.
*Gap:* a ready-made 1 m tree canopy layer (now offered by the CT ECO card) and the YCGS Surface UHI Explorer card,
which the agent did not pick. *Explorer:* nothing to measure (imagery and boundaries only).

**Filled shoreline, redlining and flood risk (Digital Atlas).** New Haven Digital Atlas, NAIP, HOLC grades (Mapping
Inequality) and FEMA flood zones: all fit, and HOLC and FEMA preview. *Gap:* the 1934 aerial mosaic and historical
shorelines (CT ECO, NOAA) would help date the fill.

**Small streams behind Des Moines' drinking water (re-recorded).** Ephemeral stream results (Science 2024): fit, but
the 150 MB file is over the preview limit. NHDPlus HR: fits (live service). USGS Water Data: *new pick*, fits (the
earlier pick was a list of gage locations without flow); the preview maps active gages with their annual mean flow.
ERA5-Land runoff: fits (Earth Engine).

**Street vending arrests in Los Angeles.** LAPD arrest data 2010-2019 and 2020-2025: fit, *preview fixed* (even sample
over the years; every vending arrest fetched from the portal). ACS: fits, *preview fixed* (Latino and Black shares,
not income). NHGIS (registration) and TIGER: fit. *Explorer:* 4,228 vending arrests 2010-2019, most in 2013 (1,145),
down to 17 in 2017; half of them in 1.7% of the area, around Downtown and Westlake; a weak positive link between
their density and the share of Latino or Black residents (rho +0.17 across 1,730 tracts, through police
enforcement in the graph). The 2020-2025 file has no vending charge the model could pick, consistent with sidewalk
vending being decriminalized in Los Angeles in 2017 and statewide in 2018; the card says the later map covers all
arrests.

**Silver and treasuries in New Spain, 1701-1808.** The Harvard Dataverse silver registry and HGIS de las Indias: fit,
*preview fixed* (pesos joined to the treasury points). Yale GeoBlacklight historical map: fits (a scan, no preview).
*Explorer:* Guanajuato registered 314.5 million pesos over 1701-1808, Zacatecas 200.9 million and Durango 167.0
million; New Spain's yearly total rose from 4.7 million (1701) to a peak of 25.2 million (1804). *Gap:* mining-town
locations other than the treasuries (HGIS de las Indias has a gazetteer).

**Rush hour in Los Angeles seen from orbit (TEMPO).** TEMPO NO2 (Earth Engine), EPA AirData, Caltrans PeMS
(registration), ERA5 hourly: all fit. *Preview fixed:* the hourly profile. *Explorer:* at the ground monitors NO2
peaks at 07:00 on weekdays, 31% above weekends at that hour.

**Behind Pakistan's air quality score (EPI).** EPI 2026, Sentinel-5P NO2, WashU surface PM2.5, GADM, WorldPop: all
fit; the agent rightly says the EPI is national context and the change must come from the satellite grids.
*Explorer:* Pakistan ranks 176 of 177 countries on Air Quality and 157 on the overall EPI.

**Climate worry versus extreme heat in Connecticut.** Yale Climate Opinion Maps (short registration form), nClimGrid,
NHGIS, TIGER counties, PRISM: all fit. *Preview fixed:* nClimGrid now maps days at or above 32.2 °C (90 °F) for an
extreme-heat factor, not the mean maximum. *Limit:* the preview is one month (July 2024); the worry estimates need the
form, so the explorer cannot compare the two.

**Tracking steel output from space.** Sentinel-5P NO2, VIIRS night lights, Global Energy Monitor steel tracker,
ERA5-Land: fit. *Gap:* the outcome itself, plant output (company reports or India's Joint Plant Committee figures), is
not in any open catalog; the demo should say that validation needs those.

**Forest carbon at Yale-Myers Forest.** HLS (previewed from Planetary Computer), 3DEP lidar, FIA, GEDI, TreeMap and
YCGS drone support: fit. *Preview fixed:* FIA no longer shows a tracker file as a table. *Gap:* if Yale Forests keeps
inventory plots at Yale-Myers, they are the best ground reference.

**Carbon in a Congo swamp forest reserve.** WDPA, Sentinel-1 (previewed from Planetary Computer), Dynamic World, ESA
CCI biomass, GEDI, global peatland database: fit, all Earth Engine. *Partial:* a regional peat map of the Cuvette
Centrale would fit better than the global database.

**Checking coffee farmers' climate reports in Colombia (iKON).** CHIRPS, IMERG, SMAP, Sentinel-2 (previewed from
Planetary Computer), coffee probability, geoBoundaries municipalities, ERA5-Land: fit. *Gap:* the SMS reports are the
project's own data; municipal crop statistics would be an outside check.

### General demos

**Where do mosquitoes breed? (re-recorded).** Now: CAES mosquito trapping (*new pick*, the outcome itself; results
are web tables, no file), CT ECO (fits; previews 1 m water 2021), NAIP and EagleView (fit), gridMET (*weak*, 4 km
weather for a site-level question; marked a poor fit). The earlier recording relied on generic CT DEEP pages and 10 m
Sentinel-2.

**Wildfire smoke and asthma.** EPA AirData, TIGER tracts, Decennial Census 2020: fit. NOAA HMS: fits, *preview
fixed*. NOAA NAQFC: *partial* (forecasts, not observations). *Explorer:* on heavy-smoke days monitor PM2.5 had a median
of 54.5 µg/m³, against 4.4 µg/m³ without smoke (rho +0.65 over 926 monitor-days, June 2023).

**Zoning reform and new housing.** CT permits by town: fits. Public Act 21-29 findings (opt-outs): fits, but the site
blocks scripts. Census BPS: fits. Zoning Atlas: fits (*link updated*). ACS income for demand and land cost: *partial*
(a stand-in; the explorer says so). CT DEEP for sewers: *weak*. *Explorer:* permits peaked in 2005 (11,885) and stood
at 9,834 in 2025; no clear link with town income.

**New Haven's lost shoreline (re-recorded).** Two Yale GeoBlacklight maps (an 1800s Coast Survey harbor chart and a
city map): fit. NOAA Shoreline Data Explorer: *new pick*, fits. Connecticut historic shoreline (1880s, Data.gov):
fits, previews 154 features. Beinecke New Haven maps: fit (Yale access). CT ECO: fits, *preview fixed* (1934 aerial
mosaic and historical shorelines).

**Downtown recovery after COVID.** Dewey Data (Yale): fits. LODES: context only.

**Malaria and land cover in western Kenya (re-recorded).** Dynamic World and the Esri 10 m land cover: fit. Malaria
Atlas card: *new pick*, fits (previews MAP's parasite-rate map; marked a poor fit for 30 m village buffers, which is
right: the model is about 5 km). OpenStreetMap: fits, *preview fixed* (villages from Overpass, not a Geofabrik file of
Antarctica; 1,006 villages and towns when the server answered in 45 s; on 2026-11-09 it was often overloaded, and
the card then says so). CHIRPS: fits (5 km).

## What stays out of reach

- **Earth Engine-only data:** GEDI, WDPA, CHIRPS, SMAP, IMERG, TEMPO and others still need an Earth Engine account
  to preview. The Congo, coffee and steel demos therefore rest on picks that are right but not previewable.
- **Raster values:** imagery and raster layers (Landsat heat, NAIP canopy, MAP parasite rate) are drawn but not measured,
  so the street-trees demo has nothing for the explorer to measure.
- **Registration and licences:** YCOM (short form), PeMS, NHGIS, Dewey Data and EagleView need an account; the cards
  say so.
- **Large or blocked files:** the ephemeral-streams results (150 MB zip) exceed the 100 MB preview limit, and WashU's
  site refuses the server.
