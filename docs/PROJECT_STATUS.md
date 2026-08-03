# Bog restoration candidate screening — project status

Last updated: 2026-08-02

## Objective

Create a ranked, reproducible list of **bog-restoration candidate areas on private land** in Ireland using QGIS. This is a screening workflow, not a legal ownership determination.

Key project rules agreed so far:

- Start from mapped bog evidence, then identify land outside known State land and outside SAC/NHA boundaries.
- Do not claim that remaining land is verified private ownership. The result is only *outside known State land / a screening result*.
- Defer clustering and the slope criterion. Apply slope later at title/parcel level, closer to final eligibility/ranking.
- Do not use manual aerial-photo interpretation. Any condition assessment must be automated or treated as later due diligence.
- Do not run the LPIS subtraction at this stage. LPIS is available as an overlay/subset but is not yet being used as the title framework.

## Project and data locations

- QGIS project: `bog.qgz` (CRS EPSG:2157, Irish Transverse Mercator)
- Raw data: `data/raw/`
- Derived data: `data/processed/`
- QGIS Console scripts: `scripts/`

## Data acquired

| Dataset | Location | Purpose / limitation |
|---|---|---|
| Irish Peat Soils Map 2024 | `data/raw/irish_peat_soils_map_2024.zip` | National bog/peat evidence base. |
| Processed bog-core evidence | `data/processed/01_bog_core_evidence.gpkg` | Raised bog, lowland Atlantic blanket bog, and mountain blanket bog evidence. |
| NPWS SAC 2026 | `data/raw/npws_sac_itm_2026_01.zip` | Designation screen. |
| NPWS NHA 2019 | `data/raw/npws_nha_itm_2019_06.zip` | Designation screen; refresh before publication. |
| LDA/PRA State Assets Feature Services | loaded by `04a` | Known State-land evidence only; not a complete public/private ownership dataset. |
| LPIS 2025 parcels | `data/raw/GEO_860-PARCELS_GPK.gpkg` | Large national farmland-parcel dataset. Not yet subtracted. |
| Tailte freehold cadastral parcels | `data/raw/Cadastral_Parcels_Freehold_-4388988690564004250.gpkg` | 2.0 GB, 3,086,691 titles, EPSG:2157, spatially indexed. Main cadastral framework. |
| Tailte leasehold cadastral parcels | `data/raw/Cadastral_Parcels_Leasehold_-3465714256390258246.gpkg` | 48 MB, 131,073 titles, EPSG:2157, spatially indexed. Flag/tenure-complication overlay, not an exclusion. |
| Tailte townlands | `data/raw/tailte_townlands_2019_generalised20m.gpkg` | Context/aggregation only; not an ownership parcel layer. |

## Completed workflow steps

### 1. Bog-core evidence

- `01b_create_bog_core_evidence.py` created `data/processed/01_bog_core_evidence.gpkg`.
- `01c_show_bog_core_evidence.py` reloads and styles it for QGIS display.
- The current evidence layer has three multipart class features. This is valid as an evidence layer but **not** suitable directly as a spatial-query window.

### 2. NPWS habitat QA

- `02b_qa_npws_active_raised_bog.py` completed.
- NPWS 7110 total: 11,702.76 ha.
- Overlap with the 2024 raised-bog map: 9,758.23 ha (83.38%).
- Report: `docs/qa_npws_7110_alignment.csv`.

### 3. Cluster exploration (not currently active)

- Cluster filtering was explored with 100 ha minimum core bog and 250 m/500 m inter-patch gaps.
- The user decided to postpone clustering. Do not use the cluster outputs to drive the current title workflow.

### 4. Known State land and designation screen

- `04a_load_state_and_designation_sources.py` loads two State-asset sources and the NPWS SAC/NHA layers for preview.
- `04b_screen_bog_core_state_and_designations.py` performs sequential spatial differences:
  1. known State land source 1;
  2. known State land source 2;
  3. SAC boundaries;
  4. NHA boundaries.
- Latest successful 04b result:
  - original mapped bog core: **1,072,874.53 ha**;
  - outside LDA-known State land: **907,589.82 ha**;
  - outside known State land and SAC/NHA: **623,851.01 ha**.
- The primary 04b result is now persisted at `data/processed/04b_screened_bog_core.gpkg`.
- It contains **3 multipart features**. This explains the later cadastral-query failure: each multipart feature has a country-scale bounding box.

### 5. LPIS extraction (available, not used for subtraction)

- `04c_extract_lpis2025_bog_overlap.py` succeeded and created:
  `data/processed/04c_lpis2025_bog_overlap.gpkg`.
- It extracted 490,002 LPIS parcels intersecting screened bog core.
- The reported 6,366,922.81 ha is the sum of full LPIS parcel areas in the subset, not the bog-overlap area.
- `04d_subtract_lpis2025_from_screened_bog.py` exists but has **not** been run and should remain paused.

### 6. Cadastral source acquisition

- Both Tailte national cadastral GeoPackages were downloaded locally.
- Freehold should be the primary title-boundary framework.
- Leasehold should later be calculated as an overlap/tenure flag, not used to exclude candidates.
- The open data contains boundary geometry and anonymous `SP_ID`, but no owner names or addresses. It is reference mapping, not legal ownership proof.

## Usable cadastral query windows

### What happened

1. The first local title-extraction script (`04e`) and the first incremental version (`04f`) queried the three huge 04b multipart geometries directly.
2. QGIS became unresponsive because the first query window covered a very large part of the country and caused an enormous freehold-title scan.
3. The correct approach is to split 04b into small single-part polygons before any title query.

### Completed result

`scripts/04b1_prepare_screened_bog_query_pieces.py`

It takes the persisted 04b GeoPackage, converts the three multipart features to single-part polygons, repairs them temporarily, assigns new unique feature IDs, then writes:

`data/processed/04b1_screened_bog_query_pieces.gpkg`

The first attempt failed because `multiparttosingleparts` produced child features with duplicated inherited FIDs. The corrected script copies the features to a fresh memory layer whose provider allocates new IDs; the inherited ID is preserved as `source_fid`. It also splits the repaired geometries back to true single parts because `fixgeometries` always returns multipart geometry.

Latest successful 04b1 result:

- saved single-part query pieces: **21,520**;
- area check: **623,851.01 ha**;
- CRS/geometry: **EPSG:2157 Polygon**;
- spatial index: present.

## Cadastral title extraction results

`04f_extract_local_cadastral_titles_incremental.py` completed successfully for both tenure datasets. Timing records are stored in `docs/04f_cadastral_extraction_timings.csv`.

1. Leasehold:
   - source titles: **131,073**;
   - intersecting titles saved: **1,319**;
   - bounding-box candidates checked: **3,289**;
   - runtime: **7.070 seconds**;
   - output: `data/processed/04f_cadastral_leasehold_bog_overlap.gpkg`.
2. Freehold:
   - source titles: **3,086,691**;
   - intersecting titles saved: **249,872**;
   - bounding-box candidates checked: **530,094**;
   - runtime: **43.182 seconds**;
   - output: `data/processed/04f_cadastral_freehold_bog_overlap.gpkg`.

Both outputs independently validate as EPSG:2157 MultiPolygon GeoPackages with spatial indexes.

## Title-level bog and tenure metrics

`scripts/04g_calculate_freehold_title_metrics_incremental.py` completed successfully. It uses the freehold title geometry as the candidate framework and calculates:

- full title area in hectares;
- unique positive-area screened-bog overlap in hectares and percent of title;
- positive-area leasehold overlap in hectares and percent of title;
- a `lease_flag` tenure-complication indicator.

Completed result:

- source titles processed: **249,872**;
- positive-area title candidates saved: **249,269**;
- boundary-only/zero-area contacts omitted: **603**;
- titles flagged for positive-area leasehold overlap: **1,094**;
- repaired title geometries required: **0**;
- runtime: **92.191 seconds**;
- output: `data/processed/04g_freehold_title_bog_metrics.gpkg`.

The output independently validates as EPSG:2157 MultiPolygon with 249,269 unique `source_fid` values. No bog overlap exceeds its title area, all percentages are within 0–100, and lease flags agree with positive leasehold area.

Selected distribution results:

| Measure | Result |
|---|---:|
| Median bog overlap/title | 0.49 ha |
| 90th percentile bog overlap/title | 4.64 ha |
| 95th percentile bog overlap/title | 8.49 ha |
| 99th percentile bog overlap/title | 28.64 ha |
| Titles with at least 1 ha bog | 82,845 |
| Titles with at least 5 ha bog | 23,058 |
| Titles with at least 10 ha bog | 10,174 |
| Titles with at least 20 ha bog | 4,055 |
| Titles with at least 50 ha bog | 1,151 |
| Titles with at least 100 ha bog | 397 |

## Retained titles with more than 100 ha of screened bog

The user set a strict working-population rule of:

`bog_ha > 100`

This refers to screened-bog overlap within the mapped freehold title, not the full title area. The current 04g metrics contain **397** matching titles. Only this subset should proceed to later slope screening and ranking unless the user changes the rule.

`scripts/04h_extract_titles_over_100ha_bog.py` completed successfully.

Validated result:

- titles retained: **397**;
- unique `source_fid` values: **397**;
- screened-bog area per title: **100.01–951.17 ha**;
- summed screened-bog overlap: **75,693.61 ha**;
- summed full title area: **102,213.69 ha**;
- titles flagged for leasehold overlap: **20**;
- CRS/geometry: **EPSG:2157 MultiPolygon**;
- spatial index: present;
- output: `data/processed/04h_freehold_titles_bog_over_100ha.gpkg`.

No retained feature fails the strict threshold, no bog area exceeds its title area, and all leasehold flags are internally consistent.

Only these 397 titles should proceed to slope screening and later ranking unless the user changes the threshold.

## Current step: slope source and screening

Copernicus DEM GLO-30 Public was selected as the reproducible national screening source. It is a 30 m digital surface model, not a bare-earth DTM, so canopy and structures can bias slope. Treat results as screening evidence and validate later with higher-resolution DTM/LiDAR where needed.

Acquisition and preparation completed:

- 27 published one-degree COG tiles downloaded from the public AWS dataset;
- three ocean-only grid cells correctly omitted because the source publishes no raster there;
- all raw-tile SHA-256 checks passed;
- raw location: `data/raw/copernicus_dem_glo30_ireland/`;
- raw size: approximately **378 MB**;
- WGS 84 virtual mosaic: `data/processed/05a_copernicus_glo30_ireland.vrt`;
- projected study-extent DSM: `data/processed/05b_copernicus_glo30_itm_397_titles.tif`;
- projected raster specification: **EPSG:2157**, **30 m**, **7,417 × 13,217 cells**, approximately **185 MB**.

`scripts/05c_create_copernicus_slope_percent.py` completed successfully:

- method: Zevenbergen–Thorne;
- units: percent slope;
- vertical/horizontal scale: 1.0 metres/metres;
- raster size: **7,417 × 13,217** cells;
- cell size: **30 × 30 m**;
- valid slope range: **0.0000–324.1607%**;
- mean slope: **5.6651%**;
- output: `data/processed/05c_copernicus_glo30_slope_percent.tif`.

The user confirmed the next eligibility rule:

`estimated screened-bog area at 0–15% slope >= 100 ha`

The 15% upper boundary is inclusive. This is not a rule excluding every title
which contains any terrain above 15%; it retains a title when at least 100 ha of
its screened-bog footprint remains within the 0–15% interval.

`scripts/05d_screen_titles_by_low_slope_bog_area.py` completed successfully. It
reconstructed each of the 397 exact title/bog footprints, sampled the 30 m slope
classification, preserved continuous metrics for every title, and wrote a
separate passing-title subset. The estimated low-slope area uses the sampled
low-slope fraction multiplied by the exact vector bog area, so non-bog portions
of a title are not counted.

Validated result:

- titles assessed: **397**;
- titles retained with `low15_ha >= 100`: **295**;
- titles excluded: **102**;
- retained low-slope bog range: **100.01–651.13 ha per title**;
- all-title low-slope bog range: **2.79–651.13 ha per title**;
- retained titles contain **54,967.96 ha** of estimated 0–15% bog within
  **62,024.42 ha** of screened bog;
- summed screened-bog footprint: **75,693.61 ha**;
- summed estimated bog at 0–15% slope: **62,116.01 ha**;
- summed estimated bog above 15% slope: **13,577.61 ha**;
- runtime: **13.6 seconds**;
- all-title metrics: `data/processed/05d_freehold_title_bog_slope_metrics.gpkg`;
- retained title boundaries: `data/processed/05e_freehold_titles_low_slope_bog_at_least_100ha.gpkg`.

Independent GeoPackage checks confirm 397 unique source title IDs in the
metrics output, 295 unique source title IDs in the retained output, no
pass/threshold mismatches, no area-balance mismatches, and spatial indexes on
both outputs. The slope result remains a 30 m Copernicus DSM screening estimate,
not a survey or engineering measurement.

A final candidate workbook has also been produced:

- `outputs/019fc366-ff97-7953-b9b9-1443ffdd34a8/final_candidate_list_ranked.xlsx`;
- all **295** candidates are ordered by `low15_ha` from largest to smallest;
- each row includes an interior ITM Easting/Northing coordinate pair in
  EPSG:2157, rounded to the nearest whole metre and revalidated inside its
  title geometry, for manual Landdirect map lookup;
- all internal ID, lease-overlap, raster-cell-count, low-slope-hectare,
  over-15%-hectare, and eligibility-flag columns were removed at the user's
  request;
- the workbook retains rank, county, title/bog areas, bog percentages, summary
  totals, filters, field definitions, lookup instructions, provenance, and
  interpretation limitations.

Candidate sources:

- **Copernicus DEM GLO-30:** reproducible open national coverage at 30 m. It is a digital surface model, so vegetation and structures can bias slope; use it for screening, not final engineering eligibility.
- **Geological Survey Ireland open topographic LiDAR DTM:** bare-earth and much higher resolution, but survey coverage and resolution vary and are not nationally complete.
- **Tailte Éireann/OSI DTM:** national terrain products exist, but licensing/access must be confirmed and may be commercial.

Recommended staged approach:

1. Use Copernicus GLO-30 for consistent national title-level slope screening.
2. Calculate each title's bog area falling within 0–15% slope and apply the user-confirmed threshold of at least 100 ha.
3. Flag rather than automatically reject areas where DSM canopy bias could matter.
4. Use open LiDAR DTM or licensed Tailte DTM for later high-resolution validation where available.
5. Develop transparent ranking criteria and scores after the slope benchmark is calculated.
6. Revisit clustering only after title-level screening and ranking are established.
7. Refresh the NHA source and validate all screening assumptions before publication.

## Script failure patterns and safeguards

| Problem encountered | Cause | Current safeguard |
|---|---|---|
| Empty or vanished preview after restart | QGIS memory layers are not durable outputs. | Persist important derived layers to GeoPackage; 04b now does so. |
| QGIS beachball/no progress | A long synchronous loop held QGIS’s UI thread; later, huge multipart query windows caused country-scale title scans. | Use persisted single-part query polygons; 04f uses small event-loop batches and logs progress. |
| `UNIQUE constraint failed: ...fid` | Processing-created child features inherited non-unique parent IDs; export tried to preserve them. | Copy to a fresh memory layer without `setId()` and retain old ID as `source_fid`. |
| `String ... exceeds maximum field length` | Copied field schemas carried restrictive string lengths. | Set copied string-field lengths to 0/unbounded for temporary/output layers. |
| `setSubsetOfAttributes` type error | QGIS expects field names when a fields object is supplied. | Use field names, never numeric indices with the `fields` argument. |
| Invalid geometry errors in overlays | Source/intermediate geometries were invalid. | Use `native:fixgeometries` on temporary copies only; never modify raw data. |
| GeoPackage output cannot be reopened or is invalid | A writer/export failed or a layer was written directly to final output. | Write to an `IN_PROGRESS` file where possible, reopen/validate it, then promote it. |
| National-layer overload | Rendering or iterating a national layer indiscriminately. | Use local GeoPackage spatial indexes via `setFilterRect`, exact geometry checks, deduplication, and extract only the relevant subset. |
| Remote-service load / rate concern | Bounding-box querying could create many HTTP requests. | Full Tailte datasets are downloaded locally; future cadastral processing is offline. |

## QGIS display recovery after a restart

The following are safe display/source restoration steps:

```python
exec(compile(open('/Users/tforde/projects/bog/scripts/01c_show_bog_core_evidence.py', encoding='utf-8').read(), '/Users/tforde/projects/bog/scripts/01c_show_bog_core_evidence.py', 'exec'))
```

```python
exec(compile(open('/Users/tforde/projects/bog/scripts/04a_load_state_and_designation_sources.py', encoding='utf-8').read(), '/Users/tforde/projects/bog/scripts/04a_load_state_and_designation_sources.py', 'exec'))
```

The persistent 04b and future 04b1/04f GeoPackages can then be added directly through QGIS **Layer → Add Layer → Add Vector Layer**, or through the relevant script.

## Git

- Git repository was initialised and an initial checkpoint committed/pushed earlier in the project.
- Preserve raw downloaded national data outside Git unless an explicit large-file-data policy is adopted.
