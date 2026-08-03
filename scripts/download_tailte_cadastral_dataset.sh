#!/usr/bin/env bash
# Download one official Tailte cadastral GeoPackage at a time.
#
# Usage:
#   bash scripts/download_tailte_cadastral_dataset.sh freehold
#   bash scripts/download_tailte_cadastral_dataset.sh leasehold
#
# Optional: change the status-check interval (seconds), e.g. POLL_SECONDS=120.

set -euo pipefail

DATASET="${1:-}"
POLL_SECONDS="${POLL_SECONDS:-60}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_DIR="$PROJECT_DIR/data/raw"

if ! [[ "$POLL_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "POLL_SECONDS must be a positive whole number." >&2
  exit 2
fi

case "$DATASET" in
  freehold)
    LABEL="Freehold cadastral parcels"
    URL="https://data-osi.opendata.arcgis.com/api/download/v1/items/4ed4c7d8775a4c80a3d4d5c73c9a1185/geoPackage?layers=12"
    OUTPUT="$RAW_DIR/tailte_cadastral_parcels_freehold.gpkg"
    ;;
  leasehold)
    LABEL="Leasehold cadastral parcels"
    URL="https://data-osi.opendata.arcgis.com/api/download/v1/items/9811f102cba04a01b0873d7e3bc98e70/geoPackage?layers=13"
    OUTPUT="$RAW_DIR/tailte_cadastral_parcels_leasehold.gpkg"
    ;;
  *)
    echo "Usage: bash scripts/download_tailte_cadastral_dataset.sh {freehold|leasehold}" >&2
    exit 2
    ;;
esac

mkdir -p "$RAW_DIR"

is_geopackage() {
  [[ -f "$1" ]] && [[ "$(head -c 16 "$1" 2>/dev/null || true)" == $'SQLite format 3\0' ]]
}

if is_geopackage "$OUTPUT"; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') $LABEL is already downloaded: $OUTPUT"
  exit 0
fi

if [[ -e "$OUTPUT" ]]; then
  echo "Refusing to overwrite a non-GeoPackage file: $OUTPUT" >&2
  echo "Inspect or rename it first, then rerun this command." >&2
  exit 1
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') Requesting official Tailte export: $LABEL"
echo "Status will be checked every $POLL_SECONDS seconds. Press Ctrl-C to stop safely."

while true; do
  TEMP_FILE="$(mktemp "$RAW_DIR/.tailte-${DATASET}.XXXXXX")"
  trap 'rm -f "$TEMP_FILE"' EXIT INT TERM

  if ! curl --fail --location --show-error --progress-bar "$URL" --output "$TEMP_FILE"; then
    rm -f "$TEMP_FILE"
    trap - EXIT INT TERM
    echo "$(date '+%Y-%m-%d %H:%M:%S') Network/export check failed; retrying in $POLL_SECONDS seconds."
    sleep "$POLL_SECONDS"
    continue
  fi

  if is_geopackage "$TEMP_FILE"; then
    SIZE="$(du -h "$TEMP_FILE" | awk '{print $1}')"
    echo "$(date '+%Y-%m-%d %H:%M:%S') Export ready ($SIZE). Saving verified GeoPackage..."
    mv "$TEMP_FILE" "$OUTPUT"
    trap - EXIT INT TERM
    echo "$(date '+%Y-%m-%d %H:%M:%S') Complete: $OUTPUT"
    exit 0
  fi

  # The endpoint normally returns a small JSON message while its export is queued.
  STATUS="$(tr '\n' ' ' < "$TEMP_FILE" | head -c 300)"
  rm -f "$TEMP_FILE"
  trap - EXIT INT TERM
  echo "$(date '+%Y-%m-%d %H:%M:%S') Tailte export not ready: $STATUS"
  echo "Waiting $POLL_SECONDS seconds before the next check..."
  sleep "$POLL_SECONDS"
done
