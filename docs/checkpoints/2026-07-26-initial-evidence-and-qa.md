# Checkpoint: initial evidence and QA

Date: 2026-07-26

## Purpose

Establish a reproducible national starting layer for identifying potential
raised-bog and blanket-bog restoration land before requesting private-parcel
geometry.

## Completed

- QGIS project created in EPSG:2157 (IRENET95 / Irish Transverse Mercator).
- Project data structure established: raw sources, processed outputs, scripts,
  and documentation.
- 2024 Irish Peat Soils Map selected as the primary national peat evidence.
- Processed bog-core evidence created from three map classes: Raised bogs,
  Lowland Atlantic blanket bogs, and Mountain blanket bogs.
- NPWS active raised-bog (7110) polygons compared to the 2024 Raised bogs class.

## QA result

| Metric | Result |
| --- | ---: |
| NPWS active raised-bog area assessed | 11,702.76 ha |
| Overlap with 2024 Raised bogs class | 9,758.23 ha (83.38%) |
| NPWS active raised bog outside the 2024 class | 1,944.53 ha (16.62%) |

The comparison is an agreement and confidence check only. NPWS overlap is not
used as an eligibility requirement, so potentially restorable land is not
discarded merely because it is absent from one dataset.

## Repository scope

The repository includes the QGIS project, scripts, documentation, and processed
evidence/QA outputs. Original downloaded datasets remain local in `data/raw/`
and are documented in `docs/data_sources.csv` rather than stored in Git.

## Next stage

Create connectivity-aware bog clusters that retain small fragments when their
combined nearby bog area reaches the project threshold, while separating truly
isolated low-area fragments from the parcel-request envelope.
