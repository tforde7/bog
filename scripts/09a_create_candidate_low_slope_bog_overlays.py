"""Create display polygons for 0–15% slope bog within the 295 candidates.

Run from the QGIS 3.44 Python Console after step 05d/05e. The script:

* reconstructs each candidate's exact screened-bog footprint from 04b1;
* reads the existing 05d binary 0–15% slope mask on its native 30 m grid;
* removes cells that are outside the 0–15% interval from the bog footprint;
* writes one MultiPolygon overlay feature per candidate.

The output is for map display. Its pixel-stepped boundaries reflect the
30 m screening raster and are not survey or engineering measurements.
No raw or earlier processed dataset is modified.
"""

from pathlib import Path
import math
import os
import time

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsFeature,
    QgsFeatureRequest,
    QgsFields,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
    QgsRasterLayer,
)


PROJECT = QgsProject.instance()
PROJECT_PATH = Path(PROJECT.fileName())
if not PROJECT_PATH.is_file():
    raise RuntimeError("Save the QGIS project before running this script.")

ROOT = PROJECT_PATH.parent
PROCESSED = ROOT / "data" / "processed"

CANDIDATE_PATH = PROCESSED / "05e_freehold_titles_low_slope_bog_at_least_100ha.gpkg"
CANDIDATE_LAYER = "freehold_titles_low_slope_bog_100ha"
BOG_PATH = PROCESSED / "04b1_screened_bog_query_pieces.gpkg"
BOG_LAYER = "screened_bog_query_pieces"
MASK_PATH = PROCESSED / "05d_low_slope_mask_0_to_15pct.tif"

OUTPUT = PROCESSED / "09a_candidate_low_slope_bog_overlay.gpkg"
TEMP_OUTPUT = PROCESSED / "09a_candidate_low_slope_bog_overlay_IN_PROGRESS.gpkg"
OUTPUT_LAYER = "candidate_low_slope_bog_overlay"
DISPLAY_NAME = "09a — Candidate low-slope bog overlay (0–15%)"

EXPECTED_CANDIDATES = 295
EXPECTED_CRS = "EPSG:2157"
EXPECTED_MASK_VALUE_MIN = 0.0
EXPECTED_MASK_VALUE_MAX = 1.0
BOG_AREA_TOLERANCE_HA = 0.05
AREA_WARNING_MIN_HA = 0.5
AREA_WARNING_FRACTION = 0.02
UNION_BATCH_SIZE = 500
LOG_EVERY = 25


def format_duration(seconds):
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m {seconds:02d}s"
    return f"{minutes:d}m {seconds:02d}s"


def load_vector(path, layer_name, display_name):
    if not path.is_file():
        raise FileNotFoundError(f"Required input is missing: {path}")
    layer = QgsVectorLayer(f"{path}|layername={layer_name}", display_name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Could not open {path.name}, layer {layer_name!r}.")
    if layer.crs().authid() != EXPECTED_CRS:
        raise RuntimeError(
            f"Unexpected CRS for {path.name}: {layer.crs().authid()}."
        )
    return layer


def remove_loaded_path(path):
    path_text = str(path)
    for layer in list(PROJECT.mapLayers().values()):
        if path_text in layer.source() or layer.name() == DISPLAY_NAME:
            PROJECT.removeMapLayer(layer.id())


def remove_file_and_sidecars(path):
    for candidate in (
        path,
        Path(f"{path}-journal"),
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
    ):
        if candidate.is_file():
            candidate.unlink()


def polygonal_geometry(geometry, label, allow_empty=False):
    copied = QgsGeometry(geometry)
    if copied.isNull() or copied.isEmpty() or copied.area() <= 0:
        if allow_empty:
            return None
        raise RuntimeError(f"{label} has no positive-area geometry.")
    if not copied.isGeosValid():
        copied = copied.makeValid()
        if copied.isNull() or copied.isEmpty():
            raise RuntimeError(f"{label} could not be repaired: {copied.lastError()}")
    if QgsWkbTypes.geometryType(copied.wkbType()) != Qgis.GeometryType.Polygon:
        converted = copied.convertGeometryCollectionToSubclass(
            Qgis.GeometryType.Polygon
        )
        if (
            not converted
            or copied.isNull()
            or copied.isEmpty()
            or QgsWkbTypes.geometryType(copied.wkbType())
            != Qgis.GeometryType.Polygon
        ):
            if allow_empty:
                return None
            raise RuntimeError(f"{label} has no usable polygon component.")
        print(f"09a: retained polygon components from a mixed geometry: {label}")
    if copied.area() <= 0:
        if allow_empty:
            return None
        raise RuntimeError(f"{label} has no positive polygon area.")
    if not QgsWkbTypes.isMultiType(copied.wkbType()):
        if not copied.convertToMultiType():
            raise RuntimeError(f"{label} could not be converted to MultiPolygon.")
    if QgsWkbTypes.hasZ(copied.wkbType()):
        copied.get().dropZValue()
    if QgsWkbTypes.hasM(copied.wkbType()):
        copied.get().dropMValue()
    return copied


def title_bog_footprint(title_geometry, bog_layer, source_fid):
    intersections = []
    request = QgsFeatureRequest()
    request.setFilterRect(title_geometry.boundingBox())
    request.setNoAttributes()
    for bog_feature in bog_layer.getFeatures(request):
        bog_geometry = bog_feature.geometry()
        if bog_geometry.isNull() or bog_geometry.isEmpty():
            continue
        if not title_geometry.intersects(bog_geometry):
            continue
        intersection = title_geometry.intersection(bog_geometry)
        if intersection.isNull():
            raise RuntimeError(
                f"Bog intersection failed for source_fid {source_fid}: "
                f"{intersection.lastError()}"
            )
        if not intersection.isEmpty() and intersection.area() > 0:
            intersections.append(intersection)
    if not intersections:
        raise RuntimeError(
            f"No positive-area bog footprint found for source_fid {source_fid}."
        )
    footprint = (
        intersections[0]
        if len(intersections) == 1
        else QgsGeometry.unaryUnion(intersections)
    )
    if footprint.isNull() or footprint.isEmpty():
        raise RuntimeError(
            f"Could not union bog footprint for source_fid {source_fid}: "
            f"{footprint.lastError()}"
        )
    return polygonal_geometry(
        footprint, f"bog footprint for source_fid {source_fid}"
    )


def batched_union(geometries, label):
    if not geometries:
        return None
    level = [QgsGeometry(geometry) for geometry in geometries]
    while len(level) > 1:
        next_level = []
        for offset in range(0, len(level), UNION_BATCH_SIZE):
            batch = level[offset : offset + UNION_BATCH_SIZE]
            combined = (
                batch[0] if len(batch) == 1 else QgsGeometry.unaryUnion(batch)
            )
            if combined.isNull() or combined.isEmpty():
                raise RuntimeError(
                    f"{label} union failed at batch {offset // UNION_BATCH_SIZE + 1}: "
                    f"{combined.lastError()}"
                )
            next_level.append(combined)
        level = next_level
    return level[0]


def aligned_raster_window(mask, geometry, source_fid):
    raster_extent = mask.extent()
    bbox = geometry.boundingBox()
    pixel_x = abs(mask.rasterUnitsPerPixelX())
    pixel_y = abs(mask.rasterUnitsPerPixelY())
    if pixel_x <= 0 or pixel_y <= 0:
        raise RuntimeError("The low-slope mask has invalid pixel dimensions.")

    tolerance = max(pixel_x, pixel_y) * 1e-6
    if (
        bbox.xMinimum() < raster_extent.xMinimum() - tolerance
        or bbox.xMaximum() > raster_extent.xMaximum() + tolerance
        or bbox.yMinimum() < raster_extent.yMinimum() - tolerance
        or bbox.yMaximum() > raster_extent.yMaximum() + tolerance
    ):
        raise RuntimeError(
            f"Bog footprint for source_fid {source_fid} extends outside "
            "the low-slope raster."
        )

    col_start = max(
        0, math.floor((bbox.xMinimum() - raster_extent.xMinimum()) / pixel_x)
    )
    col_end = min(
        mask.width() - 1,
        math.ceil((bbox.xMaximum() - raster_extent.xMinimum()) / pixel_x) - 1,
    )
    row_start = max(
        0, math.floor((raster_extent.yMaximum() - bbox.yMaximum()) / pixel_y)
    )
    row_end = min(
        mask.height() - 1,
        math.ceil((raster_extent.yMaximum() - bbox.yMinimum()) / pixel_y) - 1,
    )
    if col_end < col_start or row_end < row_start:
        raise RuntimeError(
            f"Could not derive a raster window for source_fid {source_fid}."
        )

    width = col_end - col_start + 1
    height = row_end - row_start + 1
    block_extent = QgsRectangle(
        raster_extent.xMinimum() + col_start * pixel_x,
        raster_extent.yMaximum() - (row_end + 1) * pixel_y,
        raster_extent.xMinimum() + (col_end + 1) * pixel_x,
        raster_extent.yMaximum() - row_start * pixel_y,
    )
    return (
        block_extent,
        col_start,
        row_start,
        width,
        height,
        pixel_x,
        pixel_y,
    )


def exclusion_rectangles(mask, footprint, source_fid):
    (
        block_extent,
        col_start,
        row_start,
        width,
        height,
        pixel_x,
        pixel_y,
    ) = aligned_raster_window(mask, footprint, source_fid)

    block = mask.dataProvider().block(1, block_extent, width, height)
    if block is None or not block.isValid():
        detail = "" if block is None else block.error().summary()
        raise RuntimeError(
            f"Could not read the low-slope raster window for source_fid "
            f"{source_fid}: {detail}"
        )
    if block.width() != width or block.height() != height:
        raise RuntimeError(
            f"Raster block size mismatch for source_fid {source_fid}: "
            f"expected {width}×{height}, got {block.width()}×{block.height()}."
        )

    # Merge identical horizontal exclusion runs through consecutive rows.
    # Since most candidate bog is low-slope, building the smaller exclusion
    # geometry and subtracting it is much lighter than unioning every low cell.
    active_runs = {}
    closed_rectangles = []
    raster_extent = mask.extent()

    def close_run(run, first_local_row, last_local_row):
        first_column, after_last_column = run
        global_first_row = row_start + first_local_row
        global_last_row = row_start + last_local_row
        rectangle = QgsRectangle(
            raster_extent.xMinimum() + (col_start + first_column) * pixel_x,
            raster_extent.yMaximum() - (global_last_row + 1) * pixel_y,
            raster_extent.xMinimum()
            + (col_start + after_last_column) * pixel_x,
            raster_extent.yMaximum() - global_first_row * pixel_y,
        )
        rectangle_geometry = QgsGeometry.fromRect(rectangle)
        if footprint.intersects(rectangle_geometry):
            closed_rectangles.append(rectangle_geometry)

    for local_row in range(height):
        row_runs = []
        local_column = 0
        while local_column < width:
            value_is_low = (
                not block.isNoData(local_row, local_column)
                and float(block.value(local_row, local_column)) >= 0.5
            )
            if value_is_low:
                local_column += 1
                continue
            run_start = local_column
            local_column += 1
            while local_column < width:
                value_is_low = (
                    not block.isNoData(local_row, local_column)
                    and float(block.value(local_row, local_column)) >= 0.5
                )
                if value_is_low:
                    break
                local_column += 1
            row_runs.append((run_start, local_column))

        current_runs = set(row_runs)
        for run, first_row in active_runs.items():
            if run not in current_runs:
                close_run(run, first_row, local_row - 1)
        active_runs = {
            run: active_runs.get(run, local_row) for run in current_runs
        }

    for run, first_row in active_runs.items():
        close_run(run, first_row, height - 1)
    return closed_rectangles


def low_slope_overlay(mask, footprint, source_fid):
    excluded_cells = exclusion_rectangles(mask, footprint, source_fid)
    if not excluded_cells:
        return QgsGeometry(footprint)
    excluded = batched_union(
        excluded_cells, f"non-low-slope cells for source_fid {source_fid}"
    )
    if excluded is None or not footprint.intersects(excluded):
        return QgsGeometry(footprint)
    result = footprint.difference(excluded)
    if result.isNull():
        raise RuntimeError(
            f"Low-slope Difference failed for source_fid {source_fid}: "
            f"{result.lastError()}"
        )
    return polygonal_geometry(
        result, f"low-slope bog overlay for source_fid {source_fid}"
    )


def output_fields():
    fields = QgsFields()
    fields.append(QgsField("source_fid", QVariant.LongLong))
    county = QgsField("county_nam", QVariant.String)
    county.setLength(0)
    fields.append(county)
    fields.append(QgsField("bog_geom_ha", QVariant.Double))
    fields.append(QgsField("low15_pct", QVariant.Double))
    fields.append(QgsField("low15_ha", QVariant.Double))
    fields.append(QgsField("overlay_ha", QVariant.Double))
    fields.append(QgsField("area_delta", QVariant.Double))
    return fields


def create_writer(path, fields, crs):
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = OUTPUT_LAYER
    options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
    options.layerOptions = ["SPATIAL_INDEX=YES"]
    writer = QgsVectorFileWriter.create(
        str(path),
        fields,
        QgsWkbTypes.MultiPolygon,
        crs,
        PROJECT.transformContext(),
        options,
    )
    if writer.hasError() != QgsVectorFileWriter.NoError:
        raise RuntimeError(
            f"Could not create {path.name}: {writer.errorMessage()}"
        )
    return writer


# Release a writer that may remain in the shared Console namespace after a
# failed previous run, then remove only the incomplete output.
previous_writer = globals().get("low_slope_writer")
if previous_writer is not None:
    try:
        previous_writer.flushBuffer()
    finally:
        globals()["low_slope_writer"] = None
previous_writer = None
remove_loaded_path(TEMP_OUTPUT)
remove_file_and_sidecars(TEMP_OUTPUT)

started = time.monotonic()
candidates = load_vector(
    CANDIDATE_PATH, CANDIDATE_LAYER, "saved 05e final candidates"
)
bog = load_vector(BOG_PATH, BOG_LAYER, "saved 04b1 screened-bog pieces")
if candidates.featureCount() != EXPECTED_CANDIDATES:
    raise RuntimeError(
        f"Expected {EXPECTED_CANDIDATES} candidates; "
        f"found {candidates.featureCount()}."
    )
required_fields = (
    "source_fid",
    "county_nam",
    "bog_geom_ha",
    "low15_pct",
    "low15_ha",
)
for field_name in required_fields:
    if candidates.fields().indexFromName(field_name) < 0:
        raise RuntimeError(f"05e input is missing field {field_name!r}.")

if not MASK_PATH.is_file():
    raise FileNotFoundError(f"Required low-slope mask is missing: {MASK_PATH}")
mask = QgsRasterLayer(str(MASK_PATH), "saved 05d 0–15% slope mask")
if not mask.isValid():
    raise RuntimeError("Could not open the 05d low-slope mask.")
if mask.crs().authid() != EXPECTED_CRS:
    raise RuntimeError(f"Unexpected low-slope-mask CRS: {mask.crs().authid()}.")
if mask.bandCount() != 1:
    raise RuntimeError(f"Expected one mask band; found {mask.bandCount()}.")
mask_stats = mask.dataProvider().bandStatistics(1)
if (
    mask_stats.minimumValue < EXPECTED_MASK_VALUE_MIN
    or mask_stats.maximumValue > EXPECTED_MASK_VALUE_MAX
):
    raise RuntimeError(
        f"Unexpected valid mask range: "
        f"{mask_stats.minimumValue}–{mask_stats.maximumValue}."
    )

fields = output_fields()
low_slope_writer = create_writer(TEMP_OUTPUT, fields, candidates.crs())
request = QgsFeatureRequest()
request.setSubsetOfAttributes(required_fields, candidates.fields())
candidate_features = list(candidates.getFeatures(request))
candidate_features.sort(
    key=lambda feature: (
        -float(feature["low15_ha"]),
        int(feature["source_fid"]),
    )
)

processed = 0
warning_count = 0
sum_target_ha = 0.0
sum_overlay_ha = 0.0
seen_source_fids = set()

for candidate in candidate_features:
    source_fid = int(candidate["source_fid"])
    if source_fid in seen_source_fids:
        raise RuntimeError(f"Duplicate candidate source_fid: {source_fid}.")
    seen_source_fids.add(source_fid)

    title_geometry = polygonal_geometry(
        candidate.geometry(), f"title geometry for source_fid {source_fid}"
    )
    footprint = title_bog_footprint(title_geometry, bog, source_fid)
    bog_geom_ha = footprint.area() / 10_000.0
    saved_bog_geom_ha = float(candidate["bog_geom_ha"])
    if abs(bog_geom_ha - saved_bog_geom_ha) > BOG_AREA_TOLERANCE_HA:
        raise RuntimeError(
            f"Bog-area reconstruction mismatch for source_fid {source_fid}: "
            f"saved {saved_bog_geom_ha:.4f} ha, "
            f"reconstructed {bog_geom_ha:.4f} ha."
        )

    overlay = low_slope_overlay(mask, footprint, source_fid)
    overlay_ha = overlay.area() / 10_000.0
    target_low15_ha = float(candidate["low15_ha"])
    area_delta = overlay_ha - target_low15_ha
    warning_tolerance = max(
        AREA_WARNING_MIN_HA, target_low15_ha * AREA_WARNING_FRACTION
    )
    if abs(area_delta) > warning_tolerance:
        warning_count += 1
        print(
            f"09a warning: source_fid {source_fid} vector overlay differs "
            f"from the 05d estimate by {area_delta:+.2f} ha "
            f"(overlay {overlay_ha:.2f} ha; estimate {target_low15_ha:.2f} ha)"
        )
    if overlay_ha <= 0 or overlay_ha > bog_geom_ha + BOG_AREA_TOLERANCE_HA:
        raise RuntimeError(
            f"Invalid low-slope overlay area for source_fid {source_fid}: "
            f"{overlay_ha:.4f} ha within {bog_geom_ha:.4f} ha bog."
        )

    output_feature = QgsFeature(fields)
    output_feature.setGeometry(overlay)
    output_feature.setAttributes(
        [
            source_fid,
            candidate["county_nam"],
            bog_geom_ha,
            float(candidate["low15_pct"]),
            target_low15_ha,
            overlay_ha,
            area_delta,
        ]
    )
    if not low_slope_writer.addFeature(output_feature):
        raise RuntimeError(
            f"Could not write low-slope overlay for source_fid {source_fid}: "
            f"{low_slope_writer.lastError()}"
        )

    processed += 1
    sum_target_ha += target_low15_ha
    sum_overlay_ha += overlay_ha
    if processed % LOG_EVERY == 0 or processed == EXPECTED_CANDIDATES:
        print(
            f"09a processed {processed:,}/{EXPECTED_CANDIDATES:,}; "
            f"overlay area {sum_overlay_ha:.2f} ha; "
            f"elapsed {format_duration(time.monotonic() - started)}"
        )
        QgsApplication.processEvents()

if processed != EXPECTED_CANDIDATES:
    raise RuntimeError(
        f"Processed {processed} candidates; expected {EXPECTED_CANDIDATES}."
    )
if not low_slope_writer.flushBuffer():
    raise RuntimeError(
        f"Could not flush temporary low-slope overlay: "
        f"{low_slope_writer.lastError()}"
    )
low_slope_writer = None

temporary_check = QgsVectorLayer(
    f"{TEMP_OUTPUT}|layername={OUTPUT_LAYER}",
    "validated temporary low-slope overlay",
    "ogr",
)
if not temporary_check.isValid():
    raise RuntimeError("Temporary low-slope overlay could not be reopened.")
if temporary_check.featureCount() != EXPECTED_CANDIDATES:
    raise RuntimeError(
        f"Temporary output contains {temporary_check.featureCount()} features; "
        f"expected {EXPECTED_CANDIDATES}."
    )
saved_ids = {
    int(feature["source_fid"])
    for feature in temporary_check.getFeatures(
        QgsFeatureRequest().setSubsetOfAttributes(
            ["source_fid"], temporary_check.fields()
        )
    )
}
if len(saved_ids) != EXPECTED_CANDIDATES:
    raise RuntimeError(
        f"Temporary output contains {len(saved_ids)} unique source_fid values; "
        f"expected {EXPECTED_CANDIDATES}."
    )
temporary_check = None

# Promote only after the complete temporary GeoPackage has passed validation.
remove_loaded_path(OUTPUT)
remove_file_and_sidecars(OUTPUT)
os.replace(TEMP_OUTPUT, OUTPUT)

completed = QgsVectorLayer(
    f"{OUTPUT}|layername={OUTPUT_LAYER}", DISPLAY_NAME, "ogr"
)
if not completed.isValid():
    raise RuntimeError("Completed low-slope overlay could not be reopened.")
symbol = QgsFillSymbol.createSimple(
    {
        "color": "#efaa3d",
        "outline_color": "#9a5b0b",
        "outline_width": "0.2",
    }
)
symbol.setOpacity(0.58)
completed.setRenderer(QgsSingleSymbolRenderer(symbol))
PROJECT.addMapLayer(completed)
tree_node = PROJECT.layerTreeRoot().findLayer(completed.id())
if tree_node is not None:
    tree_node.setItemVisibilityChecked(False)

runtime = time.monotonic() - started
sum_delta_ha = sum_overlay_ha - sum_target_ha
print("09a candidate low-slope bog overlay complete")
print("Slope interval displayed: 0–15% inclusive")
print(f"Candidates processed: {processed:,}")
print(f"Overlay features saved: {completed.featureCount():,}")
print(f"Summed 05d estimated low-slope bog: {sum_target_ha:.2f} ha")
print(f"Summed vector-overlay area: {sum_overlay_ha:.2f} ha")
print(f"Summed overlay-minus-estimate difference: {sum_delta_ha:+.2f} ha")
print(f"Candidates outside the area warning tolerance: {warning_count:,}")
print(f"Runtime: {format_duration(runtime)} ({runtime:.1f} seconds)")
print("Output: data/processed/09a_candidate_low_slope_bog_overlay.gpkg")
print(
    "Note: overlay edges follow the 30 m raster grid and are screening "
    "estimates, not survey measurements."
)
