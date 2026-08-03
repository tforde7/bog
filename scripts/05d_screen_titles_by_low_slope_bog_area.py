"""Screen the 397 retained titles by low-slope screened-bog area.

Run from the QGIS Python Console after 05c. For each 04h title, this script
reconstructs the exact screened-bog footprint from the indexed 04b1 pieces,
samples the 30 m percent-slope raster over that footprint, and estimates the
screened-bog area at 0–15% slope (inclusive).

A title passes only when low15_ha >= 100. Earlier outputs are never modified.
The all-title metrics output uses screened-bog footprint geometry; the passing
subset uses the original freehold title geometry.
"""

from pathlib import Path
import os
import time

from qgis.PyQt.QtCore import QVariant
from qgis.analysis import QgsRasterCalculator, QgsRasterCalculatorEntry, QgsZonalStatistics
from qgis.core import (
    Qgis,
    QgsFeature,
    QgsFeatureRequest,
    QgsFields,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsProject,
    QgsRasterBandStats,
    QgsRasterLayer,
    QgsSingleSymbolRenderer,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)


PROJECT_DIR = Path("/Users/tforde/projects/bog")
PROCESSED_DIR = PROJECT_DIR / "data" / "processed"

TITLES_PATH = PROCESSED_DIR / "04h_freehold_titles_bog_over_100ha.gpkg"
TITLES_LAYER = "freehold_titles_bog_over_100ha"
BOG_PATH = PROCESSED_DIR / "04b1_screened_bog_query_pieces.gpkg"
BOG_LAYER = "screened_bog_query_pieces"
SLOPE_PATH = PROCESSED_DIR / "05c_copernicus_glo30_slope_percent.tif"

MASK_PATH = PROCESSED_DIR / "05d_low_slope_mask_0_to_15pct.tif"
MASK_PARTIAL_PATH = PROCESSED_DIR / "05d_low_slope_mask_0_to_15pct_IN_PROGRESS.tif"

METRICS_PATH = PROCESSED_DIR / "05d_freehold_title_bog_slope_metrics.gpkg"
METRICS_PARTIAL_PATH = PROCESSED_DIR / "05d_freehold_title_bog_slope_metrics_IN_PROGRESS.gpkg"
METRICS_LAYER = "freehold_title_bog_slope_metrics"
METRICS_DISPLAY = "05d — Title screened-bog slope metrics"

PASS_PATH = PROCESSED_DIR / "05e_freehold_titles_low_slope_bog_at_least_100ha.gpkg"
PASS_PARTIAL_PATH = (
    PROCESSED_DIR / "05e_freehold_titles_low_slope_bog_at_least_100ha_IN_PROGRESS.gpkg"
)
PASS_LAYER = "freehold_titles_low_slope_bog_100ha"
PASS_DISPLAY = "05e — Titles with at least 100 ha low-slope bog"

SLOPE_MIN_PERCENT = 0.0
SLOPE_MAX_PERCENT = 15.0
MIN_LOW_SLOPE_BOG_HA = 100.0
EXPECTED_TITLE_COUNT = 397
AREA_RECONCILIATION_TOLERANCE_HA = 0.05


def load_vector(path, layer_name, display_name):
    if not path.is_file():
        raise FileNotFoundError(f"Required input is missing: {path}")
    layer = QgsVectorLayer(f"{path}|layername={layer_name}", display_name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Could not open {path.name}, layer {layer_name!r}.")
    if layer.crs().authid() != "EPSG:2157":
        raise RuntimeError(f"Unexpected CRS for {path.name}: {layer.crs().authid()}")
    return layer


def remove_loaded_path(path):
    project = QgsProject.instance()
    for layer in list(project.mapLayers().values()):
        if str(path) in layer.source():
            project.removeMapLayer(layer.id())


def remove_partial(path):
    if path.is_file():
        path.unlink()
    for suffix in ("-journal", "-wal", "-shm", ".aux.xml"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.is_file():
            sidecar.unlink()


def copied_output_fields(source):
    fields = QgsFields()
    source_attribute_names = []
    for source_field in source.fields():
        # The OGR provider exposes the GeoPackage's managed primary-key field
        # as "fid". It must not be copied as a normal attribute. The explicit
        # inherited_fid/source_fid audit fields are preserved.
        if source_field.name().lower() == "fid":
            continue
        field = QgsField(source_field)
        if field.type() == QVariant.String:
            field.setLength(0)
        fields.append(field)
        source_attribute_names.append(source_field.name())
    fields.append(QgsField("bog_geom_ha", QVariant.Double))
    fields.append(QgsField("sample_cells", QVariant.Double))
    fields.append(QgsField("low15_cells", QVariant.Double))
    fields.append(QgsField("low15_pct", QVariant.Double))
    fields.append(QgsField("low15_ha", QVariant.Double))
    fields.append(QgsField("over15_ha", QVariant.Double))
    fields.append(QgsField("slope_pass", QVariant.Int))
    return fields, source_attribute_names


def polygonal_geometry(geometry, label):
    copied = QgsGeometry(geometry)
    if copied.isNull() or copied.isEmpty():
        raise RuntimeError(f"{label} has empty geometry.")
    if not copied.isGeosValid():
        copied = copied.makeValid()
        if copied.isNull() or copied.isEmpty():
            raise RuntimeError(f"{label} could not be repaired: {copied.lastError()}")
    if QgsWkbTypes.geometryType(copied.wkbType()) != Qgis.GeometryType.Polygon:
        # Valid overlay results can be GeometryCollections containing polygon
        # area plus line/point fragments from coincident boundaries.
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
            raise RuntimeError(f"{label} has no usable polygon component.")
        print(f"05d: retained polygon components from a mixed geometry: {label}")
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
        raise RuntimeError(f"No positive-area bog footprint found for source_fid {source_fid}.")
    footprint = (
        intersections[0]
        if len(intersections) == 1
        else QgsGeometry.unaryUnion(intersections)
    )
    if footprint.isNull() or footprint.isEmpty():
        raise RuntimeError(
            f"Could not union the bog footprint for source_fid {source_fid}: "
            f"{footprint.lastError()}"
        )
    return polygonal_geometry(footprint, f"bog footprint for source_fid {source_fid}")


def create_writer(path, layer_name, fields, geometry_type, crs):
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer_name
    options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
    writer = QgsVectorFileWriter.create(
        str(path),
        fields,
        geometry_type,
        crs,
        QgsProject.instance().transformContext(),
        options,
    )
    if writer.hasError() != QgsVectorFileWriter.NoError:
        raise RuntimeError(f"Could not create {path.name}: {writer.errorMessage()}")
    return writer


def finish_writer(writer, label):
    flushed = writer.flushBuffer()
    error = writer.lastError()
    if not flushed:
        raise RuntimeError(f"Could not flush {label}: {error}")
    del writer


# A failed QGIS Console run can leave its top-level writer objects alive in the
# shared console namespace. Release those handles before replacing partial files.
for previous_writer_name in ("metrics_writer", "pass_writer"):
    previous_writer = globals().get(previous_writer_name)
    if previous_writer is not None:
        try:
            previous_writer.flushBuffer()
        finally:
            globals()[previous_writer_name] = None
previous_writer = None

started = time.monotonic()
titles = load_vector(TITLES_PATH, TITLES_LAYER, "saved 04h retained titles")
bog = load_vector(BOG_PATH, BOG_LAYER, "saved 04b1 screened-bog pieces")
if titles.featureCount() != EXPECTED_TITLE_COUNT:
    raise RuntimeError(
        f"Expected {EXPECTED_TITLE_COUNT} retained titles; found {titles.featureCount()}."
    )
required_fields = ("source_fid", "bog_ha", "title_ha", "lease_flag")
for field_name in required_fields:
    if titles.fields().indexFromName(field_name) < 0:
        raise RuntimeError(f"04h input is missing field {field_name!r}.")

if not SLOPE_PATH.is_file():
    raise FileNotFoundError(f"Required slope raster is missing: {SLOPE_PATH}")
slope = QgsRasterLayer(str(SLOPE_PATH), "05c Copernicus percent slope")
if not slope.isValid():
    raise RuntimeError("Could not open the 05c percent-slope raster.")
if slope.crs().authid() != "EPSG:2157":
    raise RuntimeError(f"Unexpected slope CRS: {slope.crs().authid()}")
if slope.bandCount() != 1:
    raise RuntimeError(f"Expected one slope band; found {slope.bandCount()}.")

# Build a 1/0 mask on exactly the 05c grid. Input NoData remains NoData.
remove_loaded_path(MASK_PATH)
remove_loaded_path(MASK_PARTIAL_PATH)
remove_partial(MASK_PARTIAL_PATH)
entry = QgsRasterCalculatorEntry()
entry.ref = "slope@1"
entry.raster = slope
entry.bandNumber = 1
formula = (
    f'("slope@1" >= {SLOPE_MIN_PERCENT}) AND '
    f'("slope@1" <= {SLOPE_MAX_PERCENT})'
)
calculator = QgsRasterCalculator(
    formula,
    str(MASK_PARTIAL_PATH),
    "GTiff",
    slope.extent(),
    slope.crs(),
    slope.width(),
    slope.height(),
    [entry],
    QgsProject.instance().transformContext(),
)
calculator.setNoDataValue(-9999.0)
calculator.setCreationOptions(
    ["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=IF_SAFER"]
)
calculation_result = calculator.processCalculation()
if calculation_result != QgsRasterCalculator.Result.Success:
    raise RuntimeError(
        f"Low-slope raster calculation failed ({calculation_result}): "
        f"{calculator.lastError()}"
    )

mask_check = QgsRasterLayer(str(MASK_PARTIAL_PATH), "temporary 0–15% slope mask")
if not mask_check.isValid():
    raise RuntimeError("Temporary low-slope mask could not be reopened.")
if mask_check.width() != slope.width() or mask_check.height() != slope.height():
    raise RuntimeError("Temporary low-slope mask does not match the source slope grid.")
mask_stats = mask_check.dataProvider().bandStatistics(1, QgsRasterBandStats.All)
if mask_stats.minimumValue < 0 or mask_stats.maximumValue > 1:
    raise RuntimeError(
        f"Unexpected valid mask range: {mask_stats.minimumValue}–{mask_stats.maximumValue}."
    )
mask_check = None
os.replace(MASK_PARTIAL_PATH, MASK_PATH)
partial_aux = Path(f"{MASK_PARTIAL_PATH}.aux.xml")
if partial_aux.is_file():
    os.replace(partial_aux, Path(f"{MASK_PATH}.aux.xml"))

mask = QgsRasterLayer(str(MASK_PATH), "05d 0–15% slope mask")
if not mask.isValid():
    raise RuntimeError("Completed low-slope mask could not be reopened.")

fields, source_attribute_names = copied_output_fields(titles)
metrics_geometry_type = QgsWkbTypes.MultiPolygon
pass_geometry_type = QgsWkbTypes.flatType(titles.wkbType())

for path in (METRICS_PATH, METRICS_PARTIAL_PATH, PASS_PATH, PASS_PARTIAL_PATH):
    remove_loaded_path(path)
for path in (METRICS_PARTIAL_PATH, PASS_PARTIAL_PATH):
    remove_partial(path)

metrics_writer = create_writer(
    METRICS_PARTIAL_PATH,
    METRICS_LAYER,
    fields,
    metrics_geometry_type,
    titles.crs(),
)
pass_writer = create_writer(
    PASS_PARTIAL_PATH,
    PASS_LAYER,
    fields,
    pass_geometry_type,
    titles.crs(),
)

processed = 0
passed = 0
sum_bog_ha = 0.0
sum_low15_ha = 0.0
sum_over15_ha = 0.0
low15_values = []
stats_flags = Qgis.ZonalStatistic.Count | Qgis.ZonalStatistic.Sum
title_request = QgsFeatureRequest()
title_request.setSubsetOfAttributes(source_attribute_names, titles.fields())

for title in titles.getFeatures(title_request):
    source_fid = title["source_fid"]
    title_geometry = polygonal_geometry(
        title.geometry(), f"title geometry for source_fid {source_fid}"
    )
    footprint = title_bog_footprint(title_geometry, bog, source_fid)
    bog_geom_ha = footprint.area() / 10_000.0
    recorded_bog_ha = float(title["bog_ha"])
    discrepancy_ha = abs(bog_geom_ha - recorded_bog_ha)
    if discrepancy_ha > AREA_RECONCILIATION_TOLERANCE_HA:
        raise RuntimeError(
            f"Bog-area reconciliation failed for source_fid {source_fid}: "
            f"04h={recorded_bog_ha:.6f} ha, rebuilt={bog_geom_ha:.6f} ha, "
            f"difference={discrepancy_ha:.6f} ha."
        )

    zonal = QgsZonalStatistics.calculateStatistics(
        mask.dataProvider(),
        footprint,
        abs(mask.rasterUnitsPerPixelX()),
        abs(mask.rasterUnitsPerPixelY()),
        1,
        stats_flags,
    )
    sample_cells = float(zonal.get(Qgis.ZonalStatistic.Count, 0.0))
    low15_cells = float(zonal.get(Qgis.ZonalStatistic.Sum, 0.0))
    if sample_cells <= 0:
        raise RuntimeError(f"No valid slope cells sampled for source_fid {source_fid}.")
    if low15_cells < -1e-9 or low15_cells > sample_cells + 1e-9:
        raise RuntimeError(
            f"Invalid low-slope sample for source_fid {source_fid}: "
            f"{low15_cells} of {sample_cells} cells."
        )

    low15_fraction = min(1.0, max(0.0, low15_cells / sample_cells))
    low15_pct = low15_fraction * 100.0
    low15_ha = bog_geom_ha * low15_fraction
    over15_ha = max(0.0, bog_geom_ha - low15_ha)
    slope_pass = int(low15_ha >= MIN_LOW_SLOPE_BOG_HA)
    output_attributes = [title[name] for name in source_attribute_names] + [
        bog_geom_ha,
        sample_cells,
        low15_cells,
        low15_pct,
        low15_ha,
        over15_ha,
        slope_pass,
    ]
    if len(output_attributes) != fields.count():
        raise RuntimeError(
            f"Output schema mismatch for source_fid {source_fid}: "
            f"{len(output_attributes)} values for {fields.count()} fields."
        )

    metrics_feature = QgsFeature(fields)
    metrics_feature.setGeometry(footprint)
    metrics_feature.setAttributes(output_attributes)
    if not metrics_writer.addFeature(metrics_feature):
        raise RuntimeError(
            f"Could not write slope metrics for source_fid {source_fid}: "
            f"{metrics_writer.lastError()}"
        )

    if slope_pass:
        pass_feature = QgsFeature(fields)
        pass_feature.setGeometry(title_geometry)
        pass_feature.setAttributes(output_attributes)
        if not pass_writer.addFeature(pass_feature):
            raise RuntimeError(
                f"Could not write passing title source_fid {source_fid}: "
                f"{pass_writer.lastError()}"
            )
        passed += 1

    processed += 1
    sum_bog_ha += bog_geom_ha
    sum_low15_ha += low15_ha
    sum_over15_ha += over15_ha
    low15_values.append(low15_ha)
    if processed % 50 == 0 or processed == EXPECTED_TITLE_COUNT:
        print(f"05d: processed {processed:,}/{EXPECTED_TITLE_COUNT:,}; passing {passed:,}")

finish_writer(metrics_writer, "temporary all-title slope metrics")
finish_writer(pass_writer, "temporary passing-title subset")
metrics_writer = None
pass_writer = None

metrics_check = QgsVectorLayer(
    f"{METRICS_PARTIAL_PATH}|layername={METRICS_LAYER}",
    "validated temporary slope metrics",
    "ogr",
)
if not metrics_check.isValid() or metrics_check.featureCount() != EXPECTED_TITLE_COUNT:
    raise RuntimeError(
        f"Temporary metrics validation failed: expected {EXPECTED_TITLE_COUNT}, "
        f"found {metrics_check.featureCount() if metrics_check.isValid() else 'invalid'}."
    )
pass_check = QgsVectorLayer(
    f"{PASS_PARTIAL_PATH}|layername={PASS_LAYER}",
    "validated temporary passing titles",
    "ogr",
)
if not pass_check.isValid() or pass_check.featureCount() != passed:
    raise RuntimeError(
        f"Temporary passing-subset validation failed: expected {passed}, "
        f"found {pass_check.featureCount() if pass_check.isValid() else 'invalid'}."
    )
if any(feature["low15_ha"] < MIN_LOW_SLOPE_BOG_HA for feature in pass_check.getFeatures()):
    raise RuntimeError("Temporary passing subset contains a title below the 100 ha threshold.")
metrics_check = None
pass_check = None

os.replace(METRICS_PARTIAL_PATH, METRICS_PATH)
os.replace(PASS_PARTIAL_PATH, PASS_PATH)

saved_metrics = QgsVectorLayer(
    f"{METRICS_PATH}|layername={METRICS_LAYER}",
    METRICS_DISPLAY,
    "ogr",
)
saved_pass = QgsVectorLayer(
    f"{PASS_PATH}|layername={PASS_LAYER}",
    PASS_DISPLAY,
    "ogr",
)
if not saved_metrics.isValid() or saved_metrics.featureCount() != EXPECTED_TITLE_COUNT:
    raise RuntimeError("Completed all-title slope-metrics output failed validation.")
if not saved_pass.isValid() or saved_pass.featureCount() != passed:
    raise RuntimeError("Completed passing-title output failed validation.")

pass_symbol = QgsFillSymbol.createSimple(
    {
        "color": "#31a354",
        "outline_color": "#006d2c",
        "outline_width": "0.45",
    }
)
pass_symbol.setOpacity(0.48)
saved_pass.setRenderer(QgsSingleSymbolRenderer(pass_symbol))
project = QgsProject.instance()
project.addMapLayer(saved_metrics)
project.layerTreeRoot().findLayer(saved_metrics.id()).setItemVisibilityChecked(False)
project.addMapLayer(saved_pass)
project.layerTreeRoot().findLayer(saved_pass.id()).setItemVisibilityChecked(True)
iface.mapCanvas().setExtent(saved_pass.extent())
iface.mapCanvas().refresh()

elapsed = time.monotonic() - started
print("05d/05e low-slope screened-bog title screening complete")
print("Slope interval counted: 0–15% inclusive")
print(f"Eligibility rule: low15_ha >= {MIN_LOW_SLOPE_BOG_HA:.0f} ha")
print(f"Titles assessed: {processed:,}")
print(f"Titles retained: {passed:,}")
print(f"Titles excluded: {processed - passed:,}")
print(f"Low-slope bog range: {min(low15_values):.2f}–{max(low15_values):.2f} ha per title")
print(f"Summed screened-bog footprint: {sum_bog_ha:.2f} ha")
print(f"Summed estimated 0–15% bog: {sum_low15_ha:.2f} ha")
print(f"Summed estimated >15% bog: {sum_over15_ha:.2f} ha")
print(f"Runtime: {elapsed:.1f} seconds")
print(f"Metrics output: {METRICS_PATH.relative_to(PROJECT_DIR)}")
print(f"Passing-title output: {PASS_PATH.relative_to(PROJECT_DIR)}")
print("Note: low-slope areas are 30 m raster-screening estimates, not survey measurements.")
