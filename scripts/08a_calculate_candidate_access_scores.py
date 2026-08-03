"""Score vehicle-road/track access for the 295 final bog candidates.

Run this script from the QGIS 3.44 Python Console. It does not modify any raw
or earlier processed dataset.

The script reconstructs each candidate's screened-bog footprint, removes the
mapped commonage/private-forest union from step 07a, and measures the minimum
planar distance from that clear-bog geometry to an OpenStreetMap vehicle road
or land-access track.

The access bands follow the coherent thresholds in the source study:

* Good: road/track within 1,000 m (score 3)
* Moderate: road/track more than 1,000 m and within 2,000 m (score 2)
* Poor: no qualifying road/track within 2,000 m (score 1)
* Not assessed: less than 0.01 ha of clear bog (score 0)

The report's Table 1 contains a conflicting ">500m" phrase for Poor, but its
method text defines Poor as greater than 2 km and Moderate as 1-2 km. This
script therefore uses the mutually exclusive 1 km and 2 km thresholds.

OSM proximity is screening evidence only. It does not prove a usable entrance,
legal access, road condition, vehicle suitability, or permission to cross land.
"""

from pathlib import Path
import os
import time

from osgeo import gdal
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    NULL,
    Qgis,
    QgsApplication,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineSymbol,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsSpatialIndex,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)


PROJECT = QgsProject.instance()
PROJECT_PATH = Path(PROJECT.fileName())
if not PROJECT_PATH.is_file():
    raise RuntimeError("Save the QGIS project before running this script.")

ROOT = PROJECT_PATH.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

CANDIDATE_PATH = PROCESSED / "07a_candidate_commonage_forestry.gpkg"
CANDIDATE_LAYER = "candidate_commonage_forestry_metrics"
COMMONAGE_LAYER = "candidate_commonage_overlay"
FORESTRY_LAYER = "candidate_forestry_overlay"
BOG_PATH = PROCESSED / "04b1_screened_bog_query_pieces.gpkg"
BOG_LAYER = "screened_bog_query_pieces"
OSM_PATH = RAW / "osm_ireland_northern_ireland_2026-08-02.osm.pbf"
OSM_LAYER = "lines"

OUTPUT = PROCESSED / "08a_candidate_access_scores.gpkg"
TEMP_OUTPUT = PROCESSED / "08a_candidate_access_scores_IN_PROGRESS.gpkg"
METRICS_LAYER = "candidate_access_metrics"
CLEAR_BOG_LAYER = "candidate_clear_bog"
NEAREST_ROAD_LAYER = "candidate_nearest_access_roads"

EXPECTED_CANDIDATES = 295
MIN_CLEAR_BOG_HA = 0.01
GOOD_MAX_M = 1_000.0
MODERATE_MAX_M = 2_000.0
AREA_TOLERANCE_HA = 0.05
PROGRESS_INTERVAL = 50_000

# Vehicle roads plus highway=track, which OSM defines as a minor land-access
# road generally wide enough for a typical four-wheeled vehicle. Footways,
# paths, cycleways, bridleways, pedestrian ways, steps, proposed roads, and
# construction lines are intentionally excluded. Motorways are excluded
# because line proximity does not imply an available entrance.
ROAD_CLASSES = (
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "track",
    "road",
    "motorway_link",
)


def gpkg_source(path, layer_name):
    return f"{path}|layername={layer_name}"


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
    if copied.isNull() or copied.isEmpty():
        if allow_empty:
            return None
        raise RuntimeError(f"{label} has no geometry.")
    if not copied.isGeosValid():
        copied = copied.makeValid()
        if copied.isNull() or copied.isEmpty():
            if allow_empty:
                return None
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


def linear_geometry(geometry, label, allow_empty=False):
    copied = QgsGeometry(geometry)
    if copied.isNull() or copied.isEmpty():
        if allow_empty:
            return None
        raise RuntimeError(f"{label} has no geometry.")
    if QgsWkbTypes.geometryType(copied.wkbType()) != Qgis.GeometryType.Line:
        converted = copied.convertGeometryCollectionToSubclass(
            Qgis.GeometryType.Line
        )
        if (
            not converted
            or copied.isNull()
            or copied.isEmpty()
            or QgsWkbTypes.geometryType(copied.wkbType())
            != Qgis.GeometryType.Line
        ):
            if allow_empty:
                return None
            raise RuntimeError(f"{label} has no usable line component.")
    if not QgsWkbTypes.isMultiType(copied.wkbType()):
        if not copied.convertToMultiType():
            raise RuntimeError(f"{label} could not be converted to MultiLineString.")
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


def overlay_geometries_by_source(layer, label):
    grouped = {}
    request = QgsFeatureRequest()
    request.setSubsetOfAttributes(["source_fid"], layer.fields())
    for feature in layer.getFeatures(request):
        source_fid = int(feature["source_fid"])
        geometry = polygonal_geometry(
            feature.geometry(),
            f"{label} geometry for source_fid {source_fid}",
            allow_empty=True,
        )
        if geometry is not None:
            grouped.setdefault(source_fid, []).append(geometry)
    return {
        source_fid: union_geometries(
            geometries, f"{label} union for source_fid {source_fid}"
        )
        for source_fid, geometries in grouped.items()
    }


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
    fields.append(QgsField("access_dist_m", QVariant.Double))
    fields.append(QgsField("access_band", QVariant.String))
    fields.append(QgsField("access_score", QVariant.Int))
    fields.append(QgsField("road_class", QVariant.String))
    fields.append(QgsField("road_name", QVariant.String))
    fields.append(QgsField("road_osm_id", QVariant.String))
    return fields, names


def clear_bog_fields():
    fields = QgsFields()
    fields.append(QgsField("source_fid", QVariant.LongLong))
    fields.append(QgsField("clear_bog_ha", QVariant.Double))
    return fields


def nearest_road_fields():
    fields = QgsFields()
    fields.append(QgsField("source_fid", QVariant.LongLong))
    fields.append(QgsField("access_dist_m", QVariant.Double))
    fields.append(QgsField("access_band", QVariant.String))
    fields.append(QgsField("access_score", QVariant.Int))
    fields.append(QgsField("road_class", QVariant.String))
    fields.append(QgsField("road_name", QVariant.String))
    fields.append(QgsField("road_osm_id", QVariant.String))
    return fields


def memory_layer(uri, name, fields):
    layer = QgsVectorLayer(uri, name, "memory")
    if not layer.isValid():
        raise RuntimeError(f"Could not create memory layer: {name}")
    provider = layer.dataProvider()
    if not provider.addAttributes(list(fields)):
        raise RuntimeError(
            f"Could not add fields to {name}: {provider.lastError()}"
        )
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


def clean_text(value):
    if value is None or value == NULL:
        return ""
    return str(value)


def style_polygon(layer, fill_colour, outline_colour, opacity):
    symbol = QgsFillSymbol.createSimple(
        {
            "color": fill_colour,
            "outline_color": outline_colour,
            "outline_width": "0.35",
        }
    )
    symbol.setOpacity(opacity)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def style_line(layer):
    symbol = QgsLineSymbol.createSimple(
        {
            "line_color": "#3d3832",
            "line_width": "0.65",
        }
    )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


for required_path in (CANDIDATE_PATH, BOG_PATH, OSM_PATH):
    if not required_path.is_file():
        raise FileNotFoundError(f"Required input is missing: {required_path}")

started = time.monotonic()

# The OSM driver streams several logical layers from one PBF. Interleaved
# reading prevents unused point features from accumulating while lines are read.
gdal.SetConfigOption("OGR_INTERLEAVED_READING", "YES")

candidates = load_vector(
    gpkg_source(CANDIDATE_PATH, CANDIDATE_LAYER),
    "08a candidate metrics",
)
commonage = load_vector(
    gpkg_source(CANDIDATE_PATH, COMMONAGE_LAYER),
    "08a commonage overlay",
)
forestry = load_vector(
    gpkg_source(CANDIDATE_PATH, FORESTRY_LAYER),
    "08a forestry overlay",
)
bog = load_vector(gpkg_source(BOG_PATH, BOG_LAYER), "08a screened-bog pieces")
roads = load_vector(
    f"{OSM_PATH}|layername={OSM_LAYER}",
    "08a OSM roads and tracks",
)

if candidates.featureCount() != EXPECTED_CANDIDATES:
    raise RuntimeError(
        f"Expected {EXPECTED_CANDIDATES} candidates; "
        f"found {candidates.featureCount()}."
    )
if candidates.crs().authid() != "EPSG:2157":
    raise RuntimeError(f"Candidate CRS is not EPSG:2157: {candidates.crs().authid()}")
for layer, label in (
    (bog, "screened-bog pieces"),
    (commonage, "commonage overlay"),
    (forestry, "forestry overlay"),
):
    if layer.crs() != candidates.crs():
        raise RuntimeError(f"{label} does not use the candidate CRS.")
if roads.crs().authid() != "EPSG:4326":
    raise RuntimeError(f"OSM lines CRS is not EPSG:4326: {roads.crs().authid()}")
for required_field in ("osm_id", "name", "highway"):
    if roads.fields().indexOf(required_field) < 0:
        raise RuntimeError(f"OSM lines layer is missing field: {required_field}")

commonage_by_source = overlay_geometries_by_source(commonage, "commonage")
forestry_by_source = overlay_geometries_by_source(forestry, "forestry")

candidate_features = list(candidates.getFeatures())
candidate_features.sort(
    key=lambda feature: (
        -float(feature["clear_bog_ha"]),
        int(feature["source_fid"]),
    )
)

candidate_records = []
candidate_buffer_index = QgsSpatialIndex()
candidate_index_sources = {}
sum_rebuilt_clear_ha = 0.0
for index_id, candidate in enumerate(candidate_features, start=1):
    source_fid = int(candidate["source_fid"])
    title_geometry = polygonal_geometry(
        candidate.geometry(), f"title geometry for source_fid {source_fid}"
    )
    bog_footprint = title_bog_footprint(title_geometry, bog, source_fid)
    excluded_bog = union_geometries(
        [
            intersect_geometries(
                commonage_by_source.get(source_fid),
                bog_footprint,
                f"commonage bog overlap for source_fid {source_fid}",
            ),
            intersect_geometries(
                forestry_by_source.get(source_fid),
                bog_footprint,
                f"forestry bog overlap for source_fid {source_fid}",
            ),
        ],
        f"excluded bog union for source_fid {source_fid}",
    )
    if excluded_bog is None:
        clear_bog = QgsGeometry(bog_footprint)
    else:
        difference = bog_footprint.difference(excluded_bog)
        if difference.isNull():
            raise RuntimeError(
                f"Clear-bog difference failed for source_fid {source_fid}: "
                f"{difference.lastError()}"
            )
        clear_bog = polygonal_geometry(
            difference,
            f"clear bog for source_fid {source_fid}",
            allow_empty=True,
        )

    clear_bog_ha = 0.0 if clear_bog is None else clear_bog.area() / 10_000.0
    saved_clear_bog_ha = float(candidate["clear_bog_ha"])
    if abs(clear_bog_ha - saved_clear_bog_ha) > AREA_TOLERANCE_HA:
        raise RuntimeError(
            f"Clear-bog reconstruction mismatch for source_fid {source_fid}: "
            f"saved {saved_clear_bog_ha:.4f} ha, rebuilt {clear_bog_ha:.4f} ha."
        )

    assessed = clear_bog is not None and clear_bog_ha >= MIN_CLEAR_BOG_HA
    if assessed:
        search_bounds = clear_bog.boundingBox()
        search_bounds.grow(MODERATE_MAX_M)
        if not candidate_buffer_index.addFeature(index_id, search_bounds):
            raise RuntimeError(
                f"Could not index access search bounds for source_fid {source_fid}."
            )
        candidate_index_sources[index_id] = source_fid

    candidate_records.append(
        {
            "feature": candidate,
            "source_fid": source_fid,
            "title": title_geometry,
            "clear_bog": clear_bog,
            "clear_bog_ha": clear_bog_ha,
            "assessed": assessed,
        }
    )
    sum_rebuilt_clear_ha += clear_bog_ha

print(
    f"08a reconstructed clear bog for {EXPECTED_CANDIDATES:,} candidates; "
    f"{len(candidate_index_sources):,} have at least {MIN_CLEAR_BOG_HA:.2f} ha"
)
print("08a scanning the dated national OSM extract once; QGIS may take a few minutes")

quoted_classes = ", ".join(f"'{value}'" for value in ROAD_CLASSES)
road_request = QgsFeatureRequest()
road_request.setFilterExpression(f'"highway" IN ({quoted_classes})')
road_request.setSubsetOfAttributes(["osm_id", "name", "highway"], roads.fields())
road_request.setDestinationCrs(candidates.crs(), PROJECT.transformContext())

road_index = QgsSpatialIndex(QgsSpatialIndex.FlagStoreFeatureGeometries)
road_records = {}
eligible_scanned = 0
retained_roads = 0

for road in roads.getFeatures(road_request):
    eligible_scanned += 1
    geometry = linear_geometry(
        road.geometry(),
        f"OSM line {clean_text(road['osm_id'])}",
        allow_empty=True,
    )
    if geometry is not None:
        nearby_candidates = candidate_buffer_index.intersects(
            geometry.boundingBox()
        )
        if nearby_candidates:
            retained_roads += 1
            indexed = QgsFeature()
            indexed.setId(retained_roads)
            indexed.setGeometry(geometry)
            if not road_index.addFeature(indexed):
                raise RuntimeError(
                    f"Could not index retained OSM line {clean_text(road['osm_id'])}."
                )
            road_records[retained_roads] = {
                "geometry": geometry,
                "osm_id": clean_text(road["osm_id"]),
                "name": clean_text(road["name"]),
                "highway": clean_text(road["highway"]),
            }

    if eligible_scanned % PROGRESS_INTERVAL == 0:
        print(
            f"08a scanned {eligible_scanned:,} eligible OSM lines; "
            f"retained {retained_roads:,} near candidates"
        )
        QgsApplication.processEvents()

if candidate_index_sources and retained_roads == 0:
    raise RuntimeError("No qualifying OSM roads/tracks were retained near candidates.")

metric_fields, source_attribute_names = copied_metric_fields(candidates)
clear_fields = clear_bog_fields()
nearest_fields = nearest_road_fields()
metrics_memory = memory_layer(
    f"MultiPolygon?crs={candidates.crs().authid()}",
    "08a candidate access metrics",
    metric_fields,
)
clear_memory = memory_layer(
    f"MultiPolygon?crs={candidates.crs().authid()}",
    "08a candidate clear bog",
    clear_fields,
)
nearest_memory = memory_layer(
    f"MultiLineString?crs={candidates.crs().authid()}",
    "08a nearest access roads",
    nearest_fields,
)

metric_features = []
clear_features = []
nearest_features = []
score_counts = {0: 0, 1: 0, 2: 0, 3: 0}
nearest_track_count = 0
measured_distances = []

for position, record in enumerate(candidate_records, start=1):
    source_fid = record["source_fid"]
    clear_bog = record["clear_bog"]
    nearest_record = None
    access_distance = None

    if not record["assessed"]:
        access_band = "Not assessed"
        access_score = 0
    else:
        nearest_ids = road_index.nearestNeighbor(
            clear_bog,
            8,
            MODERATE_MAX_M,
        )
        best_distance = None
        for road_id in nearest_ids:
            candidate_road = road_records.get(int(road_id))
            if candidate_road is None:
                raise RuntimeError(f"Road index returned unknown ID: {road_id}")
            distance = clear_bog.distance(candidate_road["geometry"])
            if distance < 0:
                raise RuntimeError(
                    f"Distance calculation failed for source_fid {source_fid}."
                )
            if best_distance is None or distance < best_distance:
                best_distance = distance
                nearest_record = candidate_road

        if best_distance is None or best_distance > MODERATE_MAX_M:
            access_band = "Poor"
            access_score = 1
            nearest_record = None
        else:
            access_distance = float(best_distance)
            measured_distances.append(access_distance)
            if access_distance <= GOOD_MAX_M:
                access_band = "Good"
                access_score = 3
            else:
                access_band = "Moderate"
                access_score = 2

    score_counts[access_score] += 1
    road_class = "" if nearest_record is None else nearest_record["highway"]
    road_name = "" if nearest_record is None else nearest_record["name"]
    road_osm_id = "" if nearest_record is None else nearest_record["osm_id"]
    nearest_track_count += int(road_class == "track")

    metric = QgsFeature(metric_fields)
    metric.setGeometry(record["title"])
    metric.setAttributes(
        [record["feature"][name] for name in source_attribute_names]
        + [
            access_distance,
            access_band,
            access_score,
            road_class,
            road_name,
            road_osm_id,
        ]
    )
    metric_features.append(metric)

    if record["assessed"]:
        clear_feature = QgsFeature(clear_fields)
        clear_feature.setGeometry(clear_bog)
        clear_feature.setAttributes([source_fid, record["clear_bog_ha"]])
        clear_features.append(clear_feature)

    if nearest_record is not None:
        nearest_feature = QgsFeature(nearest_fields)
        nearest_feature.setGeometry(nearest_record["geometry"])
        nearest_feature.setAttributes(
            [
                source_fid,
                access_distance,
                access_band,
                access_score,
                road_class,
                road_name,
                road_osm_id,
            ]
        )
        nearest_features.append(nearest_feature)

    if position % 50 == 0 or position == EXPECTED_CANDIDATES:
        print(
            f"08a scored {position}/{EXPECTED_CANDIDATES}; "
            f"Good {score_counts[3]}, Moderate {score_counts[2]}, "
            f"Poor {score_counts[1]}, Not assessed {score_counts[0]}"
        )

add_features(metrics_memory, metric_features, "candidate access metric")
add_features(clear_memory, clear_features, "candidate clear-bog")
add_features(nearest_memory, nearest_features, "nearest access-road")

if metrics_memory.featureCount() != EXPECTED_CANDIDATES:
    raise RuntimeError("Memory access metrics do not contain 295 candidates.")
if clear_memory.featureCount() != sum(score_counts[value] for value in (1, 2, 3)):
    raise RuntimeError("Clear-bog feature count does not match assessed candidates.")
if nearest_memory.featureCount() != score_counts[2] + score_counts[3]:
    raise RuntimeError("Nearest-road feature count does not match measured candidates.")

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
    clear_memory,
    TEMP_OUTPUT,
    CLEAR_BOG_LAYER,
    QgsVectorFileWriter.CreateOrOverwriteLayer,
)
save_layer(
    nearest_memory,
    TEMP_OUTPUT,
    NEAREST_ROAD_LAYER,
    QgsVectorFileWriter.CreateOrOverwriteLayer,
)

saved_metrics = load_vector(
    gpkg_source(TEMP_OUTPUT, METRICS_LAYER),
    "08a validated access metrics",
)
saved_clear = load_vector(
    gpkg_source(TEMP_OUTPUT, CLEAR_BOG_LAYER),
    "08a validated clear bog",
)
saved_nearest = load_vector(
    gpkg_source(TEMP_OUTPUT, NEAREST_ROAD_LAYER),
    "08a validated nearest roads",
)

if saved_metrics.featureCount() != EXPECTED_CANDIDATES:
    raise RuntimeError("Saved access metrics do not contain 295 candidates.")
if saved_clear.featureCount() != clear_memory.featureCount():
    raise RuntimeError("Saved clear-bog feature count changed during export.")
if saved_nearest.featureCount() != nearest_memory.featureCount():
    raise RuntimeError("Saved nearest-road feature count changed during export.")

saved_source_ids = set()
saved_score_counts = {0: 0, 1: 0, 2: 0, 3: 0}
for feature in saved_metrics.getFeatures():
    source_fid = int(feature["source_fid"])
    if source_fid in saved_source_ids:
        raise RuntimeError(f"Duplicate source_fid in saved metrics: {source_fid}")
    saved_source_ids.add(source_fid)
    score = int(feature["access_score"])
    if score not in saved_score_counts:
        raise RuntimeError(f"Invalid access score for source_fid {source_fid}: {score}")
    saved_score_counts[score] += 1
    distance_value = feature["access_dist_m"]
    distance = None if distance_value == NULL else float(distance_value)
    if score == 3 and (distance is None or distance > GOOD_MAX_M):
        raise RuntimeError(f"Invalid Good distance for source_fid {source_fid}.")
    if score == 2 and (
        distance is None
        or distance <= GOOD_MAX_M
        or distance > MODERATE_MAX_M
    ):
        raise RuntimeError(f"Invalid Moderate distance for source_fid {source_fid}.")
    if score in (0, 1) and distance is not None:
        raise RuntimeError(
            f"Unexpected measured distance for score {score}, source_fid {source_fid}."
        )

if len(saved_source_ids) != EXPECTED_CANDIDATES:
    raise RuntimeError("Saved metrics do not contain 295 unique source_fid values.")
if saved_score_counts != score_counts:
    raise RuntimeError("Saved access-score counts differ from calculated counts.")

saved_metrics = None
saved_clear = None
saved_nearest = None
os.replace(TEMP_OUTPUT, OUTPUT)

metrics_output = load_vector(
    gpkg_source(OUTPUT, METRICS_LAYER),
    "08a — Candidate access metrics",
)
clear_output = load_vector(
    gpkg_source(OUTPUT, CLEAR_BOG_LAYER),
    "08a — Candidate clear bog",
)
nearest_output = load_vector(
    gpkg_source(OUTPUT, NEAREST_ROAD_LAYER),
    "08a — Nearest mapped access roads/tracks",
)
style_polygon(metrics_output, "#d6e4dc", "#245b4d", 0.12)
style_polygon(clear_output, "#7cab78", "#2f6948", 0.45)
style_line(nearest_output)
PROJECT.addMapLayer(metrics_output)
PROJECT.addMapLayer(clear_output)
PROJECT.addMapLayer(nearest_output)
metrics_node = PROJECT.layerTreeRoot().findLayer(metrics_output.id())
if metrics_node is not None:
    metrics_node.setItemVisibilityChecked(False)

runtime = time.monotonic() - started
print("08a candidate access scoring complete")
print(f"Candidates assessed: {EXPECTED_CANDIDATES:,}")
print(
    f"Clear-bog candidates assessed for access: "
    f"{score_counts[1] + score_counts[2] + score_counts[3]:,}"
)
print(f"Good (<=1,000 m), score 3: {score_counts[3]:,}")
print(f"Moderate (>1,000-2,000 m), score 2: {score_counts[2]:,}")
print(f"Poor (>2,000 m), score 1: {score_counts[1]:,}")
print(f"Not assessed (<0.01 ha clear bog), score 0: {score_counts[0]:,}")
print(f"Eligible OSM road/track lines scanned: {eligible_scanned:,}")
print(f"OSM road/track lines retained near candidates: {retained_roads:,}")
print(f"Candidates whose nearest mapped access line is a track: {nearest_track_count:,}")
if measured_distances:
    print(
        f"Measured access-distance range: "
        f"{min(measured_distances):.1f}-{max(measured_distances):.1f} m"
    )
print(f"Summed rebuilt clear bog: {sum_rebuilt_clear_ha:.2f} ha")
print(f"Runtime: {runtime:.1f} seconds")
print("Output: data/processed/08a_candidate_access_scores.gpkg")
print(
    "Note: OSM proximity does not establish road condition, legal access, "
    "a usable entrance, or land-crossing permission."
)
