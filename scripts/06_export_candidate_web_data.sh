#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/tforde/projects/bog"
INPUT="${PROJECT_DIR}/data/processed/07a_candidate_commonage_forestry.gpkg"
LOW_SLOPE_INPUT="${PROJECT_DIR}/data/processed/09a_candidate_low_slope_bog_overlay.gpkg"
CANDIDATE_OUTPUT="${PROJECT_DIR}/web/data/candidates.geojson"
COMMONAGE_OUTPUT="${PROJECT_DIR}/web/data/candidate_commonage.geojson"
FORESTRY_OUTPUT="${PROJECT_DIR}/web/data/candidate_forestry.geojson"
LOW_SLOPE_OUTPUT_DIR="${PROJECT_DIR}/web/data/low_slope"
CANDIDATE_TEMP="${PROJECT_DIR}/web/data/candidates_IN_PROGRESS.geojson"
COMMONAGE_TEMP="${PROJECT_DIR}/web/data/candidate_commonage_IN_PROGRESS.geojson"
FORESTRY_TEMP="${PROJECT_DIR}/web/data/candidate_forestry_IN_PROGRESS.geojson"
LOW_SLOPE_TEMP="${PROJECT_DIR}/web/data/candidate_low_slope_IN_PROGRESS.geojson"
LOW_SLOPE_RAW_TEMP="${PROJECT_DIR}/web/data/candidate_low_slope_RAW_IN_PROGRESS.geojson"
LOW_SLOPE_TEMP_DIR="${PROJECT_DIR}/web/data/low_slope_IN_PROGRESS"
OGR2OGR="/Applications/QGIS.app/Contents/MacOS/ogr2ogr"

export PROJ_DATA="/Applications/QGIS.app/Contents/Resources/qgis/proj"
export GDAL_DATA="/Applications/QGIS.app/Contents/Resources/qgis/gdal"

if [[ ! -f "${INPUT}" ]]; then
  echo "Missing 07a candidate overlay GeoPackage: ${INPUT}" >&2
  exit 1
fi
if [[ ! -f "${LOW_SLOPE_INPUT}" ]]; then
  echo "Missing 09a candidate low-slope overlay GeoPackage: ${LOW_SLOPE_INPUT}" >&2
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

mkdir -p "$(dirname "${CANDIDATE_OUTPUT}")"
mkdir -p "${LOW_SLOPE_TEMP_DIR}"
find "${LOW_SLOPE_TEMP_DIR}" -maxdepth 1 -type f -name '*.geojson' -delete
rm -f \
  "${CANDIDATE_TEMP}" \
  "${COMMONAGE_TEMP}" \
  "${FORESTRY_TEMP}" \
  "${LOW_SLOPE_TEMP}" \
  "${LOW_SLOPE_RAW_TEMP}"

"${OGR2OGR}" \
  -f GeoJSON \
  "${CANDIDATE_TEMP}" \
  "${INPUT}" \
  -dialect SQLITE \
  -sql "
    SELECT
      geom,
      web_rank AS rank,
      source_fid,
      county_nam AS county,
      CAST(ROUND(ST_X(ST_PointOnSurface(geom))) AS INTEGER) AS itm_easting,
      CAST(ROUND(ST_Y(ST_PointOnSurface(geom))) AS INTEGER) AS itm_northing,
      title_ha,
      bog_ha,
      bog_pct,
      bog_geom_ha,
      low15_pct,
      low15_ha,
      common_title_ha,
      common_bog_ha,
      forest_title_ha,
      forest_bog_ha,
      excluded_bog_ha,
      clear_bog_ha,
      common_flag,
      forest_flag
    FROM (
      SELECT
        *,
        ROW_NUMBER() OVER (
          ORDER BY clear_bog_ha DESC, source_fid ASC
        ) AS web_rank
      FROM candidate_commonage_forestry_metrics
    )
    ORDER BY web_rank
  " \
  -t_srs EPSG:4326 \
  -lco RFC7946=YES \
  -lco COORDINATE_PRECISION=6

"${OGR2OGR}" \
  -f GeoJSON \
  "${COMMONAGE_TEMP}" \
  "${INPUT}" \
  -dialect SQLITE \
  -sql "
    SELECT
      overlay.geom,
      ranked.web_rank AS rank,
      overlay.source_fid,
      overlay.title_overlap_ha,
      overlay.bog_overlap_ha
    FROM candidate_commonage_overlay AS overlay
    JOIN (
      SELECT
        source_fid,
        ROW_NUMBER() OVER (
          ORDER BY clear_bog_ha DESC, source_fid ASC
        ) AS web_rank
      FROM candidate_commonage_forestry_metrics
    ) AS ranked
      ON overlay.source_fid = ranked.source_fid
    ORDER BY ranked.web_rank
  " \
  -t_srs EPSG:4326 \
  -lco RFC7946=YES \
  -lco COORDINATE_PRECISION=6

"${OGR2OGR}" \
  -f GeoJSON \
  "${FORESTRY_TEMP}" \
  "${INPUT}" \
  -dialect SQLITE \
  -sql "
    SELECT
      overlay.geom,
      ranked.web_rank AS rank,
      overlay.source_fid,
      overlay.title_overlap_ha,
      overlay.bog_overlap_ha
    FROM candidate_forestry_overlay AS overlay
    JOIN (
      SELECT
        source_fid,
        ROW_NUMBER() OVER (
          ORDER BY clear_bog_ha DESC, source_fid ASC
        ) AS web_rank
      FROM candidate_commonage_forestry_metrics
    ) AS ranked
      ON overlay.source_fid = ranked.source_fid
    ORDER BY ranked.web_rank
  " \
  -t_srs EPSG:4326 \
  -lco RFC7946=YES \
  -lco COORDINATE_PRECISION=6

"${OGR2OGR}" \
  -f GeoJSON \
  "${LOW_SLOPE_RAW_TEMP}" \
  "${LOW_SLOPE_INPUT}" \
  -dialect SQLITE \
  -sql "
    SELECT
      geom,
      source_fid,
      county_nam AS county,
      bog_geom_ha,
      low15_pct,
      low15_ha,
      overlay_ha,
      area_delta
    FROM candidate_low_slope_bog_overlay
    ORDER BY source_fid
  " \
  -t_srs EPSG:4326 \
  -lco RFC7946=YES \
  -lco COORDINATE_PRECISION=6

# A small number of valid QGIS overlay results retain coincident line/point
# fragments alongside their polygons. RFC 7946 exposes these as geometry
# collections, so retain only their polygon components for a consistent
# Leaflet MultiPolygon layer.
jq '
  def polygonal_geometry:
    if .type == "MultiPolygon" then
      .
    elif .type == "Polygon" then
      {type: "MultiPolygon", coordinates: [.coordinates]}
    elif .type == "GeometryCollection" then
      {
        type: "MultiPolygon",
        coordinates: (
          [
            .geometries[]
            | if .type == "Polygon" then
                [.coordinates]
              elif .type == "MultiPolygon" then
                .coordinates
              else
                empty
              end
          ]
          | add
        )
      }
    else
      error("Unsupported low-slope geometry type: \(.type)")
    end;
  .features |= map(.geometry |= polygonal_geometry)
' "${LOW_SLOPE_RAW_TEMP}" > "${LOW_SLOPE_TEMP}"
rm -f "${LOW_SLOPE_RAW_TEMP}"

feature_count="$(jq '.features | length' "${CANDIDATE_TEMP}")"
rank_count="$(jq '[.features[].properties.rank] | unique | length' "${CANDIDATE_TEMP}")"
geometry_types="$(jq -r '[.features[].geometry.type] | unique | join(",")' "${CANDIDATE_TEMP}")"
first_rank="$(jq '.features[0].properties.rank' "${CANDIDATE_TEMP}")"
last_rank="$(jq '.features[-1].properties.rank' "${CANDIDATE_TEMP}")"
commonage_count="$(jq '.features | length' "${COMMONAGE_TEMP}")"
forestry_count="$(jq '.features | length' "${FORESTRY_TEMP}")"
commonage_flags="$(jq '[.features[].properties.common_flag] | add' "${CANDIDATE_TEMP}")"
forestry_flags="$(jq '[.features[].properties.forest_flag] | add' "${CANDIDATE_TEMP}")"
commonage_ranks="$(jq '[.features[].properties.rank] | unique | length' "${COMMONAGE_TEMP}")"
forestry_ranks="$(jq '[.features[].properties.rank] | unique | length' "${FORESTRY_TEMP}")"
low_slope_count="$(jq '.features | length' "${LOW_SLOPE_TEMP}")"
low_slope_source_ids="$(jq '[.features[].properties.source_fid] | unique | length' "${LOW_SLOPE_TEMP}")"
low_slope_geometry_types="$(jq -r '[.features[].geometry.type] | unique | join(",")' "${LOW_SLOPE_TEMP}")"
invalid_low_slope="$(jq '[.features[] | select(
  .properties.low15_ha < 100 or
  .properties.overlay_ha <= 0 or
  .properties.overlay_ha > .properties.bog_geom_ha + 0.000001
)] | length' "${LOW_SLOPE_TEMP}")"
invalid_clear_bog="$(jq '[.features[] | select(
  .properties.clear_bog_ha < -0.000001 or
  .properties.clear_bog_ha > .properties.bog_geom_ha + 0.000001
)] | length' "${CANDIDATE_TEMP}")"
clear_bog_sorted="$(jq '
  [.features[].properties.clear_bog_ha]
  == ([.features[].properties.clear_bog_ha] | sort | reverse)
' "${CANDIDATE_TEMP}")"

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
if [[ "${commonage_count}" != "${commonage_flags}" || "${commonage_ranks}" != "${commonage_count}" ]]; then
  echo "Commonage overlay/flag validation failed: ${commonage_count} overlays, ${commonage_flags} flags, ${commonage_ranks} ranks." >&2
  exit 1
fi
if [[ "${forestry_count}" != "${forestry_flags}" || "${forestry_ranks}" != "${forestry_count}" ]]; then
  echo "Forestry overlay/flag validation failed: ${forestry_count} overlays, ${forestry_flags} flags, ${forestry_ranks} ranks." >&2
  exit 1
fi
if [[ "${low_slope_count}" != "295" || "${low_slope_source_ids}" != "295" ]]; then
  echo "Low-slope overlay validation failed: ${low_slope_count} features, ${low_slope_source_ids} unique source IDs." >&2
  exit 1
fi
if [[ "${low_slope_geometry_types}" != "MultiPolygon" ]]; then
  echo "Unexpected low-slope GeoJSON geometry types: ${low_slope_geometry_types}" >&2
  exit 1
fi
if [[ "${invalid_low_slope}" != "0" ]]; then
  echo "Found ${invalid_low_slope} invalid low-slope overlay features." >&2
  exit 1
fi
if [[ "${invalid_clear_bog}" != "0" ]]; then
  echo "Found ${invalid_clear_bog} candidates with invalid clear_bog_ha values." >&2
  exit 1
fi
if [[ "${clear_bog_sorted}" != "true" ]]; then
  echo "Candidate clear-bog ordering validation failed." >&2
  exit 1
fi

# Split the validated national overlay into small, candidate-specific files.
# The atlas loads only the selected candidate's file.
while IFS= read -r feature; do
  source_fid="$(printf '%s' "${feature}" | jq -r '.properties.source_fid')"
  if [[ ! "${source_fid}" =~ ^[0-9]+$ ]]; then
    echo "Invalid low-slope source_fid while splitting: ${source_fid}" >&2
    exit 1
  fi
  printf '{"type":"FeatureCollection","features":[%s]}\n' "${feature}" \
    > "${LOW_SLOPE_TEMP_DIR}/${source_fid}.geojson"
done < <(jq -c '.features[]' "${LOW_SLOPE_TEMP}")

low_slope_file_count="$(
  find "${LOW_SLOPE_TEMP_DIR}" -maxdepth 1 -type f -name '*.geojson' \
    | wc -l \
    | tr -d ' '
)"
if [[ "${low_slope_file_count}" != "295" ]]; then
  echo "Expected 295 candidate-specific low-slope files; found ${low_slope_file_count}." >&2
  exit 1
fi

mv "${CANDIDATE_TEMP}" "${CANDIDATE_OUTPUT}"
mv "${COMMONAGE_TEMP}" "${COMMONAGE_OUTPUT}"
mv "${FORESTRY_TEMP}" "${FORESTRY_OUTPUT}"
mkdir -p "${LOW_SLOPE_OUTPUT_DIR}"
find "${LOW_SLOPE_OUTPUT_DIR}" -maxdepth 1 -type f -name '*.geojson' -delete
mv "${LOW_SLOPE_TEMP_DIR}"/*.geojson "${LOW_SLOPE_OUTPUT_DIR}/"
rmdir "${LOW_SLOPE_TEMP_DIR}"
rm -f "${LOW_SLOPE_TEMP}"

echo "Candidate web-data export complete"
echo "Features: ${feature_count}"
echo "Ranking: clear_bog_ha descending, source_fid ascending for ties"
echo "Commonage overlays: ${commonage_count}"
echo "Forestry overlays: ${forestry_count}"
echo "Low-slope overlays: ${low_slope_count} in ${low_slope_file_count} on-demand files"
echo "CRS: RFC 7946 WGS 84 (EPSG:4326)"
echo "Geometry: ${geometry_types}, full unsimplified title boundaries"
echo "Outputs: ${CANDIDATE_OUTPUT}"
echo "         ${COMMONAGE_OUTPUT}"
echo "         ${FORESTRY_OUTPUT}"
echo "         ${LOW_SLOPE_OUTPUT_DIR}/"
