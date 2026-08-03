"""Prepare small, persistent spatial-query pieces from the 04b screening result.

The 04b GeoPackage contains three multipart geometries. This script converts
them to valid single-part polygons for efficient local cadastral spatial queries.
It does not change the underlying 04b audit layer or any source data.
"""

from pathlib import Path
import os

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsFillSymbol,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProject,
    QgsSingleSymbolRenderer,
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
INPUT = ROOT / "data" / "processed" / "04b_screened_bog_core.gpkg"
INPUT_LAYER = "screened_bog_core"
OUTPUT = ROOT / "data" / "processed" / "04b1_screened_bog_query_pieces.gpkg"
TEMP_OUTPUT = OUTPUT.with_name("04b1_screened_bog_query_pieces_IN_PROGRESS.gpkg")
OUTPUT_LAYER = "screened_bog_query_pieces"
DISPLAY_NAME = "04b1 — Screened bog core (single-part query pieces)"


def remove_loaded_source(path):
    for layer in list(PROJECT.mapLayers().values()):
        if str(path) in layer.source() or layer.name() == DISPLAY_NAME:
            PROJECT.removeMapLayer(layer.id())


def area_ha(layer):
    return sum(feature.geometry().area() for feature in layer.getFeatures()) / 10_000


def copy_with_fresh_feature_ids(source):
    """Create a memory copy with provider-assigned IDs for safe GeoPackage export."""
    source_names = [field.name().lower() for field in source.fields()]
    if "source_fid" in source_names:
        raise RuntimeError("The processing output already contains a source_fid field.")
    has_inherited_fid = "fid" in source_names

    fields = QgsFields()
    for source_field in source.fields():
        field = QgsField(source_field)
        # Processing may expose the input GeoPackage primary key as an ordinary
        # attribute. GeoPackage export otherwise promotes this duplicate-valued
        # field back to the output primary key and fails its UNIQUE constraint.
        if field.name().lower() == "fid":
            field.setName("source_fid")
        if field.type() == QVariant.String:
            field.setLength(0)
        fields.append(field)
    if not has_inherited_fid:
        fields.append(QgsField("source_fid", QVariant.LongLong))

    flat_wkb = QgsWkbTypes.displayString(QgsWkbTypes.flatType(source.wkbType()))
    fresh = QgsVectorLayer(f"{flat_wkb}?crs={source.crs().authid()}", "query pieces with fresh IDs", "memory")
    if not fresh.isValid():
        raise RuntimeError("Could not create the temporary memory layer for fresh feature IDs.")
    provider = fresh.dataProvider()
    if not provider.addAttributes(fields):
        raise RuntimeError(f"Could not create fields on fresh-ID memory layer: {provider.lastError()}")
    fresh.updateFields()

    batch = []
    for feature in source.getFeatures():
        geometry = QgsGeometry(feature.geometry())
        if QgsWkbTypes.hasZ(source.wkbType()):
            geometry.get().dropZValue()
        if QgsWkbTypes.hasM(source.wkbType()):
            geometry.get().dropMValue()
        copied = QgsFeature(fresh.fields())
        copied.setGeometry(geometry)
        attributes = feature.attributes()
        if not has_inherited_fid:
            attributes.append(feature.id())
        copied.setAttributes(attributes)
        batch.append(copied)
        if len(batch) >= 1_000:
            added, _ = provider.addFeatures(batch)
            if not added:
                raise RuntimeError(f"Could not add fresh-ID feature batch: {provider.lastError()}")
            batch = []
    if batch:
        added, _ = provider.addFeatures(batch)
        if not added:
            raise RuntimeError(f"Could not add final fresh-ID feature batch: {provider.lastError()}")
    if fresh.featureCount() != source.featureCount():
        raise RuntimeError(
            f"Fresh-ID copy count mismatch: source {source.featureCount():,}, copy {fresh.featureCount():,}."
        )
    return fresh


if not INPUT.is_file():
    raise FileNotFoundError(f"Persistent 04b screening output is missing: {INPUT}")

screened = QgsVectorLayer(f"{INPUT}|layername={INPUT_LAYER}", "saved 04b screening", "ogr")
if not screened.isValid():
    raise RuntimeError("Could not open the saved 04b screening GeoPackage.")
if screened.crs().authid() != "EPSG:2157":
    raise RuntimeError(f"Unexpected 04b CRS: {screened.crs().authid()}")

print(f"04b source features (multipart): {screened.featureCount():,}")
single_parts = processing.run(
    "native:multiparttosingleparts", {"INPUT": screened, "OUTPUT": "memory:"}
)["OUTPUT"]
repaired = processing.run(
    "native:fixgeometries", {"INPUT": single_parts, "METHOD": 1, "OUTPUT": "memory:"}
)["OUTPUT"]
# Fix geometries always returns a multipart layer, and a repair may split a
# polygon into several parts. Split again so every cadastral query window is
# guaranteed to be a single polygon with a local bounding box.
query_pieces = processing.run(
    "native:multiparttosingleparts", {"INPUT": repaired, "OUTPUT": "memory:"}
)["OUTPUT"]
if query_pieces.featureCount() == 0:
    raise RuntimeError("No valid single-part query pieces were produced.")
query_pieces = copy_with_fresh_feature_ids(query_pieces)

remove_loaded_source(OUTPUT)
remove_loaded_source(TEMP_OUTPUT)
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
options = QgsVectorFileWriter.SaveVectorOptions()
options.driverName = "GPKG"
options.layerName = OUTPUT_LAYER
options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
result = QgsVectorFileWriter.writeAsVectorFormatV3(
    query_pieces, str(TEMP_OUTPUT), PROJECT.transformContext(), options
)
error = result[0] if isinstance(result, tuple) else result
if error != QgsVectorFileWriter.NoError:
    detail = result[1] if isinstance(result, tuple) and len(result) > 1 else str(error)
    raise RuntimeError(f"Could not write temporary query pieces: {detail}")

temporary = QgsVectorLayer(f"{TEMP_OUTPUT}|layername={OUTPUT_LAYER}", "temporary query pieces", "ogr")
if not temporary.isValid():
    raise RuntimeError("Temporary query-pieces GeoPackage could not be reopened.")
if temporary.featureCount() != query_pieces.featureCount():
    raise RuntimeError(
        f"Saved piece-count mismatch: memory {query_pieces.featureCount():,}, "
        f"GeoPackage {temporary.featureCount():,}."
    )
temporary = None
os.replace(TEMP_OUTPUT, OUTPUT)

saved = QgsVectorLayer(f"{OUTPUT}|layername={OUTPUT_LAYER}", DISPLAY_NAME, "ogr")
if not saved.isValid():
    raise RuntimeError("Saved query-pieces GeoPackage could not be reopened.")

symbol = QgsFillSymbol.createSimple(
    {"color": "#1f7a5a", "outline_color": "#1f7a5a", "outline_width": "0.10"}
)
symbol.setOpacity(0.55)
saved.setRenderer(QgsSingleSymbolRenderer(symbol))
PROJECT.addMapLayer(saved)
PROJECT.layerTreeRoot().findLayer(saved.id()).setItemVisibilityChecked(True)
for layer in PROJECT.mapLayers().values():
    if layer.id() != saved.id() and str(INPUT) in layer.source():
        node = PROJECT.layerTreeRoot().findLayer(layer.id())
        if node is not None:
            node.setItemVisibilityChecked(False)
canvas = iface.mapCanvas()
canvas.setExtent(saved.extent())
canvas.refresh()

print("04b single-part query-piece preparation complete")
print(f"Source multipart features: {screened.featureCount():,}")
print(f"Saved single-part query pieces: {saved.featureCount():,}")
print(f"Area check: {area_ha(saved):.2f} ha")
print(f"Output: {OUTPUT.relative_to(ROOT)}")
