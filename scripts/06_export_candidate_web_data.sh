#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/tforde/projects/bog"
INPUT="${PROJECT_DIR}/data/processed/05e_freehold_titles_low_slope_bog_at_least_100ha.gpkg"
OUTPUT="${PROJECT_DIR}/web/data/candidates.geojson"
TEMP_OUTPUT="${PROJECT_DIR}/web/data/candidates_IN_PROGRESS.geojson"
OGR2OGR="/Applications/QGIS.app/Contents/MacOS/ogr2ogr"

export PROJ_DATA="/Applications/QGIS.app/Contents/Resources/qgis/proj"
export GDAL_DATA="/Applications/QGIS.app/Contents/Resources/qgis/gdal"

if [[ ! -f "${INPUT}" ]]; then
  echo "Missing final candidate GeoPackage: ${INPUT}" >&2
  exit 1
fi
if [[ ! -x "${OGR2OGR}" ]]; then
  echo "QGIS ogr2ogr is unavailable." >&2
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required to validate the generated GeoJSON." >&2
  exit 1
fi

mkdir -p "$(dirname "${OUTPUT}")"
rm -f "${TEMP_OUTPUT}"

"${OGR2OGR}" \
  -f GeoJSON \
  "${TEMP_OUTPUT}" \
  "${INPUT}" \
  -dialect SQLITE \
  -sql "
    SELECT
      geom,
      ROW_NUMBER() OVER (ORDER BY low15_ha DESC, source_fid ASC) AS rank,
      county_nam AS county,
      CAST(ROUND(ST_X(ST_PointOnSurface(geom))) AS INTEGER) AS itm_easting,
      CAST(ROUND(ST_Y(ST_PointOnSurface(geom))) AS INTEGER) AS itm_northing,
      title_ha,
      bog_ha,
      bog_pct,
      bog_geom_ha,
      low15_pct
    FROM freehold_titles_low_slope_bog_100ha
    ORDER BY low15_ha DESC, source_fid ASC
  " \
  -t_srs EPSG:4326 \
  -lco RFC7946=YES \
  -lco COORDINATE_PRECISION=6

feature_count="$(jq '.features | length' "${TEMP_OUTPUT}")"
rank_count="$(jq '[.features[].properties.rank] | unique | length' "${TEMP_OUTPUT}")"
geometry_types="$(jq -r '[.features[].geometry.type] | unique | join(",")' "${TEMP_OUTPUT}")"
first_rank="$(jq '.features[0].properties.rank' "${TEMP_OUTPUT}")"
last_rank="$(jq '.features[-1].properties.rank' "${TEMP_OUTPUT}")"

if [[ "${feature_count}" != "295" || "${rank_count}" != "295" ]]; then
  echo "Candidate count/rank validation failed: ${feature_count} features, ${rank_count} ranks." >&2
  exit 1
fi
if [[ "${geometry_types}" != "MultiPolygon" ]]; then
  echo "Unexpected GeoJSON geometry types: ${geometry_types}" >&2
  exit 1
fi
if [[ "${first_rank}" != "1" || "${last_rank}" != "295" ]]; then
  echo "Rank ordering validation failed: first ${first_rank}, last ${last_rank}." >&2
  exit 1
fi

mv "${TEMP_OUTPUT}" "${OUTPUT}"

echo "Candidate web-data export complete"
echo "Features: ${feature_count}"
echo "CRS: RFC 7946 WGS 84 (EPSG:4326)"
echo "Geometry: ${geometry_types}, full unsimplified title boundaries"
echo "Output: ${OUTPUT}"
