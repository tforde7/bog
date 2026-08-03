"""Calculate commonage and private-forestry overlap for the 295 final candidates.

This script is for the QGIS 3.44 Python Console. It does not alter any raw or
earlier processed dataset. It reconstructs each candidate's screened-bog
footprint from 04b1, intersects the full title with two commonage sources and
the DAFM 2025 private-forest estate, and writes:

* one candidate metrics layer with clear_bog_ha, the screened-bog area outside
  the union of mapped commonage and mapped private forest;
* one candidate-specific commonage overlay clipped to the full title; and
* one candidate-specific forestry overlay clipped to the full title.

Commonage is a screening flag, not a legal-title determination. The current
LPIS indicator and historic NPWS Commonage Framework Plan geometry are unioned
so overlaps are not double counted. Forestry is limited to the DAFM private
forest estate through 2025 and may not include every wooded area.
"""

from pathlib import Path
import os
import time

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    Qgis,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsFillSymbol,
    QgsGeometry,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsSpatialIndex,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)
import processing


PROJECT = QgsProject.instance()
PROJECT_PATH = Path(PROJECT.fileName())
if not PROJECT_PATH.is_file():
    raise RuntimeError("Save the QGIS project before running this script.")

ROOT = PROJECT_PATH.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

CANDIDATE_PATH = PROCESSED / "05e_freehold_titles_low_slope_bog_at_least_100ha.gpkg"
CANDIDATE_LAYER = "freehold_titles_low_slope_bog_100ha"
BOG_PATH = PROCESSED / "04b1_screened_bog_query_pieces.gpkg"
BOG_LAYER = "screened_bog_query_pieces"
LPIS_PATH = RAW / "GEO_860-PARCELS_GPK.gpkg"
LPIS_LAYER = "GEO_860_PARCELS_ANON"
COMMONAGE_ZIP = RAW / "npws_commonage_2012.zip"
COMMONAGE_MEMBER = "Commonage_Base_Plan_2011_National_v04.shp"
FOREST_ZIP = RAW / "dafm_private_forest_estate_2025.zip"
FOREST_MEMBER = "PrivateForests2025_AllFields.shp"

OUTPUT = PROCESSED / "07a_candidate_commonage_forestry.gpkg"
TEMP_OUTPUT = PROCESSED / "07a_candidate_commonage_forestry_IN_PROGRESS.gpkg"
METRICS_LAYER = "candidate_commonage_forestry_metrics"
COMMONAGE_LAYER = "candidate_commonage_overlay"
FORESTRY_LAYER = "candidate_forestry_overlay"
EXPECTED_CANDIDATES = 295
DISPLAY_THRESHOLD_HA = 0.01
AREA_CHECK_TOLERANCE_HA = 0.05


def gpkg_source(path, layer_name):
    return f"{path}|layername={layer_name}"


def zip_source(path, member):
    return f"/vsizip/{path}/{member}"


def load_vector(source, label):
    layer = QgsVectorLayer(str(source), label, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"QGIS could not open {label}: {source}")
    return layer


def remove_loaded_path(path):
    path_text = str(path)
    for layer in list(PROJECT.mapLayers().values()):
        if path_text in layer.source():
            PROJECT.removeMapLayer(layer.id())


def remove_partial(path):
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


def union_geometries(geometries, label):
    usable = [geometry for geometry in geometries if geometry is not None]
    if not usable:
        return None
    combined = (
        QgsGeometry(usable[0])
        if len(usable) == 1
        else QgsGeometry.unaryUnion(usable)
    )
    return polygonal_geometry(combined, label, allow_empty=True)


def intersect_geometries(left, right, label):
    if left is None or right is None or not left.intersects(right):
        return None
    result = left.intersection(right)
    if result.isNull():
        raise RuntimeError(f"{label} failed: {result.lastError()}")
    return polygonal_geometry(result, label, allow_empty=True)


def build_index(layer, label):
    index = QgsSpatialIndex()
    request = QgsFeatureRequest().setNoAttributes()
    count = 0
    for feature in layer.getFeatures(request):
        index.addFeature(feature)
        count += 1
    print(f"07a indexed {count:,} {label} features")
    return index


def overlay_union(mask, layer, index, label):
    candidate_ids = index.intersects(mask.boundingBox())
    if not candidate_ids:
        return None
    request = QgsFeatureRequest().setFilterFids(candidate_ids).setNoAttributes()
    intersections = []
    for feature in layer.getFeatures(request):
        source_geometry = polygonal_geometry(
            feature.geometry(),
            f"{label} source feature {feature.id()}",
            allow_empty=True,
        )
        if source_geometry is None or not mask.intersects(source_geometry):
            continue
        intersection = mask.intersection(source_geometry)
        if intersection.isNull():
            raise RuntimeError(
                f"{label} intersection failed for source feature {feature.id()}: "
                f"{intersection.lastError()}"
            )
        polygon = polygonal_geometry(
            intersection,
            f"{label} intersection for source feature {feature.id()}",
            allow_empty=True,
        )
        if polygon is not None:
            intersections.append(polygon)
    return union_geometries(intersections, f"{label} union")


def title_bog_footprint(title_geometry, bog_layer, source_fid):
    request = QgsFeatureRequest()
    request.setFilterRect(title_geometry.boundingBox())
    request.setNoAttributes()
    intersections = []
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
        polygon = polygonal_geometry(
            intersection,
            f"bog footprint part for source_fid {source_fid}",
            allow_empty=True,
        )
        if polygon is not None:
            intersections.append(polygon)
    footprint = union_geometries(
        intersections, f"bog footprint for source_fid {source_fid}"
    )
    if footprint is None:
        raise RuntimeError(
            f"No positive-area bog footprint found for source_fid {source_fid}."
        )
    return footprint


def copied_metric_fields(source_layer):
    fields = QgsFields()
    names = []
    for source_field in source_layer.fields():
        if source_field.name().lower() == "fid":
            continue
        field = QgsField(source_field)
        if field.type() == QVariant.String:
            field.setLength(0)
        fields.append(field)
        names.append(source_field.name())
    fields.append(QgsField("rank", QVariant.Int))
    fields.append(QgsField("common_title_ha", QVariant.Double))
    fields.append(QgsField("common_bog_ha", QVariant.Double))
    fields.append(QgsField("common_bog_pct", QVariant.Double))
    fields.append(QgsField("common_lpis_ha", QVariant.Double))
    fields.append(QgsField("common_hist_ha", QVariant.Double))
    fields.append(QgsField("forest_title_ha", QVariant.Double))
    fields.append(QgsField("forest_bog_ha", QVariant.Double))
    fields.append(QgsField("forest_bog_pct", QVariant.Double))
    fields.append(QgsField("excluded_bog_ha", QVariant.Double))
    fields.append(QgsField("clear_bog_ha", QVariant.Double))
    fields.append(QgsField("common_flag", QVariant.Int))
    fields.append(QgsField("forest_flag", QVariant.Int))
    return fields, names


def overlay_fields():
    fields = QgsFields()
    fields.append(QgsField("rank", QVariant.Int))
    fields.append(QgsField("source_fid", QVariant.LongLong))
    fields.append(QgsField("title_overlap_ha", QVariant.Double))
    fields.append(QgsField("bog_overlap_ha", QVariant.Double))
    return fields


def memory_polygon_layer(name, fields, crs):
    layer = QgsVectorLayer(
        f"MultiPolygon?crs={crs.authid()}",
        name,
        "memory",
    )
    if not layer.isValid():
        raise RuntimeError(f"Could not create memory layer: {name}")
    provider = layer.dataProvider()
    provider.addAttributes(list(fields))
    layer.updateFields()
    return layer


def add_features(layer, features, label):
    if not features:
        return
    provider = layer.dataProvider()
    ok, _ = provider.addFeatures(features)
    if not ok:
        raise RuntimeError(
            f"Could not add {label} features: {provider.lastError()}"
        )
    layer.updateExtents()


def save_layer(layer, path, layer_name, action):
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer_name
    options.actionOnExistingFile = action
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer,
        str(path),
        PROJECT.transformContext(),
        options,
    )
    error = result[0] if isinstance(result, tuple) else result
    if error != QgsVectorFileWriter.NoError:
        detail = result[1] if isinstance(result, tuple) and len(result) > 1 else str(error)
        raise RuntimeError(f"Could not write {layer_name}: {detail}")


def style_layer(layer, fill_colour, outline_colour, opacity):
    symbol = QgsFillSymbol.createSimple(
        {
            "color": fill_colour,
            "outline_color": outline_colour,
            "outline_width": "0.35",
        }
    )
    symbol.setOpacity(opacity)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


for required_path in (
    CANDIDATE_PATH,
    BOG_PATH,
    LPIS_PATH,
    COMMONAGE_ZIP,
    FOREST_ZIP,
):
    if not required_path.is_file():
        raise FileNotFoundError(f"Required input is missing: {required_path}")

started = time.monotonic()
candidates = load_vector(
    gpkg_source(CANDIDATE_PATH, CANDIDATE_LAYER),
    "07a final candidates",
)
bog = load_vector(gpkg_source(BOG_PATH, BOG_LAYER), "07a screened-bog pieces")
lpis_commonage = load_vector(
    gpkg_source(LPIS_PATH, LPIS_LAYER),
    "07a LPIS 2025 parcels",
)
if not lpis_commonage.setSubsetString("\"commonage_ind\" = 'Y'"):
    raise RuntimeError(
        f"Could not apply LPIS commonage filter: {lpis_commonage.dataProvider().lastError()}"
    )
historic_commonage_raw = load_vector(
    zip_source(COMMONAGE_ZIP, COMMONAGE_MEMBER),
    "07a historic commonage",
)
forest = load_vector(
    zip_source(FOREST_ZIP, FOREST_MEMBER),
    "07a DAFM private forest 2025",
)

if candidates.featureCount() != EXPECTED_CANDIDATES:
    raise RuntimeError(
        f"Expected {EXPECTED_CANDIDATES} final candidates; "
        f"found {candidates.featureCount()}."
    )
if candidates.crs().authid() != "EPSG:2157":
    raise RuntimeError(f"Candidate CRS is not EPSG:2157: {candidates.crs().authid()}")
if bog.crs() != candidates.crs():
    raise RuntimeError("Screened-bog pieces do not use the candidate CRS.")
if lpis_commonage.crs() != candidates.crs():
    raise RuntimeError("LPIS commonage layer does not use the candidate CRS.")
if forest.crs() != candidates.crs():
    raise RuntimeError(
        f"DAFM private forest CRS does not match candidates: {forest.crs().authid()}"
    )
if historic_commonage_raw.crs().authid() != "EPSG:29902":
    raise RuntimeError(
        "Historic commonage source did not open as EPSG:29902: "
        f"{historic_commonage_raw.crs().authid()}"
    )

historic_commonage = processing.run(
    "native:reprojectlayer",
    {
        "INPUT": historic_commonage_raw,
        "TARGET_CRS": candidates.crs(),
        "OUTPUT": "memory:",
    },
)["OUTPUT"]
if not historic_commonage.isValid() or historic_commonage.featureCount() == 0:
    raise RuntimeError("Historic commonage reprojection produced no usable features.")

lpis_index = build_index(lpis_commonage, "LPIS commonage")
historic_index = build_index(historic_commonage, "historic commonage")
forest_index = build_index(forest, "private-forest")

metric_fields, source_attribute_names = copied_metric_fields(candidates)
overlap_fields = overlay_fields()
metrics_memory = memory_polygon_layer(
    "07a candidate commonage/forestry metrics",
    metric_fields,
    candidates.crs(),
)
commonage_memory = memory_polygon_layer(
    "07a candidate commonage overlay",
    overlap_fields,
    candidates.crs(),
)
forestry_memory = memory_polygon_layer(
    "07a candidate forestry overlay",
    overlap_fields,
    candidates.crs(),
)

candidate_features = list(candidates.getFeatures())
candidate_features.sort(
    key=lambda feature: (
        -float(feature["low15_ha"]),
        int(feature["source_fid"]),
    )
)

metric_features = []
commonage_features = []
forestry_features = []
sum_bog_ha = 0.0
sum_common_bog_ha = 0.0
sum_forest_bog_ha = 0.0
sum_excluded_bog_ha = 0.0
sum_clear_bog_ha = 0.0
lpis_candidate_count = 0
historic_candidate_count = 0
commonage_candidate_count = 0
forest_candidate_count = 0

for rank, candidate in enumerate(candidate_features, start=1):
    source_fid = int(candidate["source_fid"])
    title_geometry = polygonal_geometry(
        candidate.geometry(), f"title geometry for source_fid {source_fid}"
    )
    bog_footprint = title_bog_footprint(title_geometry, bog, source_fid)
    bog_area_ha = bog_footprint.area() / 10_000.0
    saved_bog_area_ha = float(candidate["bog_geom_ha"])
    if abs(bog_area_ha - saved_bog_area_ha) > AREA_CHECK_TOLERANCE_HA:
        raise RuntimeError(
            f"Bog-area reconstruction mismatch for source_fid {source_fid}: "
            f"saved {saved_bog_area_ha:.4f} ha, reconstructed {bog_area_ha:.4f} ha."
        )

    lpis_title = overlay_union(
        title_geometry,
        lpis_commonage,
        lpis_index,
        f"LPIS commonage for source_fid {source_fid}",
    )
    historic_title = overlay_union(
        title_geometry,
        historic_commonage,
        historic_index,
        f"historic commonage for source_fid {source_fid}",
    )
    commonage_title = union_geometries(
        [lpis_title, historic_title],
        f"combined commonage for source_fid {source_fid}",
    )
    forest_title = overlay_union(
        title_geometry,
        forest,
        forest_index,
        f"private forest for source_fid {source_fid}",
    )

    lpis_bog = intersect_geometries(
        lpis_title,
        bog_footprint,
        f"LPIS commonage bog overlap for source_fid {source_fid}",
    )
    historic_bog = intersect_geometries(
        historic_title,
        bog_footprint,
        f"historic commonage bog overlap for source_fid {source_fid}",
    )
    commonage_bog = intersect_geometries(
        commonage_title,
        bog_footprint,
        f"combined commonage bog overlap for source_fid {source_fid}",
    )
    forest_bog = intersect_geometries(
        forest_title,
        bog_footprint,
        f"private forest bog overlap for source_fid {source_fid}",
    )
    excluded_bog = union_geometries(
        [commonage_bog, forest_bog],
        f"combined excluded bog for source_fid {source_fid}",
    )

    lpis_bog_ha = 0.0 if lpis_bog is None else lpis_bog.area() / 10_000.0
    historic_bog_ha = (
        0.0 if historic_bog is None else historic_bog.area() / 10_000.0
    )
    commonage_title_ha = (
        0.0 if commonage_title is None else commonage_title.area() / 10_000.0
    )
    commonage_bog_ha = (
        0.0 if commonage_bog is None else commonage_bog.area() / 10_000.0
    )
    forest_title_ha = (
        0.0 if forest_title is None else forest_title.area() / 10_000.0
    )
    forest_bog_ha = 0.0 if forest_bog is None else forest_bog.area() / 10_000.0
    excluded_bog_ha = (
        0.0 if excluded_bog is None else excluded_bog.area() / 10_000.0
    )
    clear_bog_ha = max(0.0, bog_area_ha - excluded_bog_ha)

    if excluded_bog_ha > bog_area_ha + AREA_CHECK_TOLERANCE_HA:
        raise RuntimeError(
            f"Excluded bog exceeds bog footprint for source_fid {source_fid}: "
            f"{excluded_bog_ha:.4f} > {bog_area_ha:.4f} ha."
        )

    commonage_flag = int(commonage_title_ha >= DISPLAY_THRESHOLD_HA)
    forest_flag = int(forest_title_ha >= DISPLAY_THRESHOLD_HA)
    lpis_candidate_count += int(
        lpis_title is not None
        and lpis_title.area() / 10_000.0 >= DISPLAY_THRESHOLD_HA
    )
    historic_candidate_count += int(
        historic_title is not None
        and historic_title.area() / 10_000.0 >= DISPLAY_THRESHOLD_HA
    )
    commonage_candidate_count += commonage_flag
    forest_candidate_count += forest_flag

    metric = QgsFeature(metric_fields)
    metric.setGeometry(title_geometry)
    metric.setAttributes(
        [candidate[name] for name in source_attribute_names]
        + [
            rank,
            commonage_title_ha,
            commonage_bog_ha,
            commonage_bog_ha / bog_area_ha * 100.0,
            lpis_bog_ha,
            historic_bog_ha,
            forest_title_ha,
            forest_bog_ha,
            forest_bog_ha / bog_area_ha * 100.0,
            excluded_bog_ha,
            clear_bog_ha,
            commonage_flag,
            forest_flag,
        ]
    )
    metric_features.append(metric)

    if commonage_flag:
        feature = QgsFeature(overlap_fields)
        feature.setGeometry(commonage_title)
        feature.setAttributes(
            [rank, source_fid, commonage_title_ha, commonage_bog_ha]
        )
        commonage_features.append(feature)
    if forest_flag:
        feature = QgsFeature(overlap_fields)
        feature.setGeometry(forest_title)
        feature.setAttributes([rank, source_fid, forest_title_ha, forest_bog_ha])
        forestry_features.append(feature)

    sum_bog_ha += bog_area_ha
    sum_common_bog_ha += commonage_bog_ha
    sum_forest_bog_ha += forest_bog_ha
    sum_excluded_bog_ha += excluded_bog_ha
    sum_clear_bog_ha += clear_bog_ha
    if rank % 25 == 0 or rank == EXPECTED_CANDIDATES:
        print(
            f"07a processed {rank}/{EXPECTED_CANDIDATES}; "
            f"commonage candidates {commonage_candidate_count}; "
            f"forestry candidates {forest_candidate_count}"
        )

add_features(metrics_memory, metric_features, "candidate metric")
add_features(commonage_memory, commonage_features, "commonage overlay")
add_features(forestry_memory, forestry_features, "forestry overlay")

if metrics_memory.featureCount() != EXPECTED_CANDIDATES:
    raise RuntimeError(
        f"Memory metrics count mismatch: {metrics_memory.featureCount()}."
    )
if commonage_memory.featureCount() != commonage_candidate_count:
    raise RuntimeError("Memory commonage overlay count does not match flags.")
if forestry_memory.featureCount() != forest_candidate_count:
    raise RuntimeError("Memory forestry overlay count does not match flags.")

remove_loaded_path(OUTPUT)
remove_loaded_path(TEMP_OUTPUT)
remove_partial(TEMP_OUTPUT)
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
save_layer(
    metrics_memory,
    TEMP_OUTPUT,
    METRICS_LAYER,
    QgsVectorFileWriter.CreateOrOverwriteFile,
)
save_layer(
    commonage_memory,
    TEMP_OUTPUT,
    COMMONAGE_LAYER,
    QgsVectorFileWriter.CreateOrOverwriteLayer,
)
save_layer(
    forestry_memory,
    TEMP_OUTPUT,
    FORESTRY_LAYER,
    QgsVectorFileWriter.CreateOrOverwriteLayer,
)

saved_metrics = load_vector(
    gpkg_source(TEMP_OUTPUT, METRICS_LAYER),
    "07a validated candidate metrics",
)
saved_commonage = load_vector(
    gpkg_source(TEMP_OUTPUT, COMMONAGE_LAYER),
    "07a validated commonage overlay",
)
saved_forestry = load_vector(
    gpkg_source(TEMP_OUTPUT, FORESTRY_LAYER),
    "07a validated forestry overlay",
)
if saved_metrics.featureCount() != EXPECTED_CANDIDATES:
    raise RuntimeError("Saved metrics count does not match 295 candidates.")
if saved_commonage.featureCount() != commonage_candidate_count:
    raise RuntimeError("Saved commonage count does not match calculated flags.")
if saved_forestry.featureCount() != forest_candidate_count:
    raise RuntimeError("Saved forestry count does not match calculated flags.")
saved_source_ids = {
    int(feature["source_fid"]) for feature in saved_metrics.getFeatures()
}
if len(saved_source_ids) != EXPECTED_CANDIDATES:
    raise RuntimeError("Saved metrics do not contain 295 unique source_fid values.")
for feature in saved_metrics.getFeatures():
    if float(feature["clear_bog_ha"]) < -AREA_CHECK_TOLERANCE_HA:
        raise RuntimeError("Saved metrics contain a negative clear_bog_ha value.")
    if (
        float(feature["clear_bog_ha"])
        > float(feature["bog_geom_ha"]) + AREA_CHECK_TOLERANCE_HA
    ):
        raise RuntimeError("Saved clear_bog_ha exceeds the screened-bog footprint.")

saved_metrics = None
saved_commonage = None
saved_forestry = None
os.replace(TEMP_OUTPUT, OUTPUT)

metrics_output = load_vector(
    gpkg_source(OUTPUT, METRICS_LAYER),
    "07a — Candidate commonage/forestry metrics",
)
commonage_output = load_vector(
    gpkg_source(OUTPUT, COMMONAGE_LAYER),
    "07a — Commonage within candidate titles",
)
forestry_output = load_vector(
    gpkg_source(OUTPUT, FORESTRY_LAYER),
    "07a — Private forest within candidate titles",
)
style_layer(metrics_output, "#d6e4dc", "#245b4d", 0.16)
style_layer(commonage_output, "#8d5a97", "#4f2458", 0.60)
style_layer(forestry_output, "#2f7d4a", "#17482a", 0.64)
PROJECT.addMapLayer(metrics_output)
PROJECT.addMapLayer(commonage_output)
PROJECT.addMapLayer(forestry_output)
for layer in (commonage_output, forestry_output):
    node = PROJECT.layerTreeRoot().findLayer(layer.id())
    if node is not None:
        node.setItemVisibilityChecked(False)

runtime = time.monotonic() - started
clear_values = [
    float(feature["clear_bog_ha"]) for feature in metrics_output.getFeatures()
]
print("07a candidate commonage and private-forestry screening complete")
print(f"Candidates assessed: {EXPECTED_CANDIDATES:,}")
print(
    f"Candidates overlapping LPIS commonage: {lpis_candidate_count:,}; "
    f"historic commonage: {historic_candidate_count:,}; "
    f"combined unique commonage: {commonage_candidate_count:,}"
)
print(f"Candidates overlapping DAFM private forest: {forest_candidate_count:,}")
print(f"Summed screened-bog footprint: {sum_bog_ha:.2f} ha")
print(f"Summed commonage within screened bog: {sum_common_bog_ha:.2f} ha")
print(f"Summed private forest within screened bog: {sum_forest_bog_ha:.2f} ha")
print(
    "Summed union of commonage/private forest within screened bog: "
    f"{sum_excluded_bog_ha:.2f} ha"
)
print(f"Summed bog outside mapped commonage/private forest: {sum_clear_bog_ha:.2f} ha")
print(
    f"Per-candidate clear bog range: {min(clear_values):.2f}–"
    f"{max(clear_values):.2f} ha"
)
print(f"Runtime: {runtime:.1f} seconds")
print("Output: data/processed/07a_candidate_commonage_forestry.gpkg")
print(
    "Note: commonage is a screening flag, not a legal determination; "
    "forestry coverage is limited to the DAFM private forest estate through 2025."
)
