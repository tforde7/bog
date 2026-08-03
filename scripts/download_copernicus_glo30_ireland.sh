#!/usr/bin/env bash
set -euo pipefail

project_dir="/Users/tforde/projects/bog"
output_dir="${project_dir}/data/raw/copernicus_dem_glo30_ireland"
base_url="https://copernicus-dem-30m.s3.amazonaws.com"
tile_index="${output_dir}/tileList.txt"

mkdir -p "${output_dir}"
curl --fail --location --retry 3 \
    --output "${tile_index}.part" "${base_url}/tileList.txt"
mv "${tile_index}.part" "${tile_index}"

downloaded=0
skipped=0
unavailable=0
for latitude in 51 52 53 54 55; do
    for longitude in 6 7 8 9 10 11; do
        printf -v longitude_padded "%03d" "${longitude}"
        tile_name="Copernicus_DSM_COG_10_N${latitude}_00_W${longitude_padded}_00_DEM"
        tile_path="${output_dir}/${tile_name}.tif"
        partial_path="${tile_path}.part"
        tile_url="${base_url}/${tile_name}/${tile_name}.tif"

        if ! grep --quiet --fixed-strings "${tile_name}" "${tile_index}"; then
            printf 'No published tile (ocean): %s\n' "${tile_name}.tif"
            if [[ -e "${partial_path}" ]]; then
                rm -f "${partial_path}"
            fi
            unavailable=$((unavailable + 1))
            continue
        fi

        if [[ -s "${tile_path}" ]]; then
            printf 'Already present: %s\n' "${tile_name}.tif"
            skipped=$((skipped + 1))
            continue
        fi

        printf 'Downloading: %s\n' "${tile_name}.tif"
        curl --fail --location --retry 3 --retry-delay 2 \
            --continue-at - --output "${partial_path}" "${tile_url}"
        mv "${partial_path}" "${tile_path}"
        downloaded=$((downloaded + 1))
    done
done

(
    cd "${output_dir}"
    shasum -a 256 ./*.tif > SHA256SUMS
)

tile_count=$(find "${output_dir}" -maxdepth 1 -type f -name '*.tif' | wc -l | tr -d ' ')
printf 'Copernicus GLO-30 Ireland download complete\n'
printf 'Downloaded this run: %d\n' "${downloaded}"
printf 'Already present: %d\n' "${skipped}"
printf 'Unpublished ocean tiles: %d\n' "${unavailable}"
printf 'Total tiles: %s\n' "${tile_count}"
printf 'Output: %s\n' "${output_dir}"
printf 'Checksums: %s\n' "${output_dir}/SHA256SUMS"
