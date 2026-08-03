# Bog restoration candidate screening

This repository contains the reproducible QGIS screening workflow and a public
web atlas for the final 295 candidate title boundaries.

## Candidate atlas

The static app lives in `web/` and is deployed to GitHub Pages by
`.github/workflows/pages.yml`.

To preview it locally:

```bash
python3 -m http.server 8080 --directory web
```

Then open `http://localhost:8080`.

The map uses Leaflet, OpenStreetMap standard tiles and Esri World Imagery.
Candidate boundaries are stored as unsimplified RFC 7946 GeoJSON in WGS 84, so
the browser renders them at their correct geographic location and scale at
every zoom level.

## Rebuilding web data

After regenerating the final 05e GeoPackage, run:

```bash
scripts/06_export_candidate_web_data.sh
```

The script transforms the 295 candidate geometries from EPSG:2157 to RFC 7946
WGS 84, preserves their full boundary detail, calculates whole-metre interior
ITM lookup points, and validates feature count and rank order.

Large raw and processed GIS datasets are intentionally excluded from Git.
