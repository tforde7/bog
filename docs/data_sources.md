# Data source register

Generated: 2026-07-26 by `scripts/01_validate_source_register.py`.

This is the audit trail for spatial inputs used by the bog-restoration workflow. Files remain `planned` until an original source file is downloaded and its access date is recorded.

| ID | Dataset | Provider | Local file | Status | Purpose |
| --- | --- | --- | --- | --- | --- |
| npws_habitat_7130 | Active blanket bog (7130) | National Parks and Wildlife Service | data/raw/npws_habitat_7130.zip | planned | Core evidence for the blanket-bog restoration search envelope |
| npws_habitat_7110 | Active raised bog (7110) | National Parks and Wildlife Service | data/raw/npws_habitat_7110.zip | planned | Core evidence for the raised-bog restoration search envelope |
| npws_habitat_7120 | Degraded raised bogs capable of natural regeneration (7120) | National Parks and Wildlife Service | data/raw/npws_habitat_7120.zip | planned | Evidence for restorable raised-bog habitat |
| npws_designations | NPWS SAC NHA and SPA boundaries | National Parks and Wildlife Service | data/raw/npws_designations.zip | planned | Context for protection status and statutory constraints |
| copernicus_dem_glo30 | Copernicus DEM GLO-30 Public | Copernicus Programme / AWS Public Dataset | data/raw/copernicus_dem_glo30_ireland/ | acquired 2026-08-02 | National 30 m elevation surface for title-level slope screening |

## Copernicus DEM GLO-30 acquisition

- Access date: 2026-08-02.
- Public source: `https://copernicus-dem-30m.s3.amazonaws.com/`.
- Dataset index: `tileList.txt` from the public bucket.
- Download script: `scripts/download_copernicus_glo30_ireland.sh`.
- Ireland coverage: 27 published one-degree Cloud Optimized GeoTIFF tiles; three grid cells in the requested bounding range are ocean-only and are not published.
- Local raw size: approximately 378 MB.
- Integrity record: `data/raw/copernicus_dem_glo30_ireland/SHA256SUMS`; all downloaded tiles passed checksum verification.
- Native raster: WGS 84 (EPSG:4326), float32 elevation, nominal 30 m / 1 arc-second resolution.
- Prepared virtual mosaic: `data/processed/05a_copernicus_glo30_ireland.vrt`.
- Prepared study-extent raster: `data/processed/05b_copernicus_glo30_itm_397_titles.tif`, EPSG:2157, 30 m, 7,417 × 13,217 cells.
- Important limitation: this product is a digital surface model, not a bare-earth DTM. Buildings, infrastructure, and vegetation can affect derived slope. Use it for reproducible screening, with higher-resolution DTM/LiDAR validation later where needed.

## Validation

- Register structure is complete.
