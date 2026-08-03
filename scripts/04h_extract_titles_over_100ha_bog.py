"""Persist the working subset of freehold titles with more than 100 ha of bog.

Input metrics are read from the validated 04g output. The strict filter is
bog_ha > 100, referring to screened-bog overlap inside each mapped freehold
title—not total title area. Earlier outputs are not modified.
"""

from pathlib import Path
import os

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsFeature,
    QgsFields,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)
import processing


PROJECT_DIR = Path("/Users/tforde/projects/bog")
INPUT_PATH = PROJECT_DIR / "data" / "processed" / "04g_freehold_title_bog_metrics.gpkg"
INPUT_LAYER = "freehold_title_bog_metrics"
OUTPUT_PATH = PROJECT_DIR / "data" / "processed" / "04h_freehold_titles_bog_over_100ha.gpkg"
PARTIAL_PATH = PROJECT_DIR / "data" / "processed" / "04h_freehold_titles_bog_over_100ha_IN_PROGRESS.gpkg"
OUTPUT_LAYER = "freehold_titles_bog_over_100ha"
DISPLAY_NAME = "04h — Freehold titles with >100 ha screened bog"
FILTER_EXPRESSION = '"bog_ha" > 100'


def remove_loaded_path(path):
    project = QgsProject.instance()
    for layer in list(project.mapLayers().values()):
        if str(path) in layer.source() or layer.name() == DISPLAY_NAME:
            project.removeMapLayer(layer.id())


def copy_with_fresh_ids(source):
    """Copy features to a memory provider that allocates unique feature IDs."""
    fields = QgsFields()
    for source_field in source.fields():
        field = QgsField(source_field)
        if field.name().lower() == "fid":
            field.setName("inherited_fid")
        if field.type() == QVariant.String:
            field.setLength(0)
        fields.append(field)

    flat_wkb = QgsWkbTypes.displayString(QgsWkbTypes.flatType(source.wkbType()))
    copied_layer = QgsVectorLayer(
        f"{flat_wkb}?crs={source.crs().authid()}",
        "fresh-ID >100 ha bog titles",
        "memory",
    )
    if not copied_layer.isValid():
        raise RuntimeError("Could not create the fresh-ID memory layer.")
    provider = copied_layer.dataProvider()
    if not provider.addAttributes(fields):
        raise RuntimeError(f"Could not create output fields: {provider.lastError()}")
    copied_layer.updateFields()

    batch = []
    for feature in source.getFeatures():
        geometry = QgsGeometry(feature.geometry())
        if QgsWkbTypes.hasZ(source.wkbType()):
            geometry.get().dropZValue()
        if QgsWkbTypes.hasM(source.wkbType()):
            geometry.get().dropMValue()
        copied = QgsFeature(copied_layer.fields())
        copied.setGeometry(geometry)
        copied.setAttributes(feature.attributes())
        batch.append(copied)

    added, _ = provider.addFeatures(batch)
    if not added:
        raise RuntimeError(f"Could not copy selected features: {provider.lastError()}")
    if copied_layer.featureCount() != source.featureCount():
        raise RuntimeError(
            f"Fresh-ID count mismatch: selected {source.featureCount():,}, "
            f"copied {copied_layer.featureCount():,}."
        )
    return copied_layer


if not INPUT_PATH.is_file():
    raise FileNotFoundError(f"Required 04g input is missing: {INPUT_PATH}")

source = QgsVectorLayer(
    f"{INPUT_PATH}|layername={INPUT_LAYER}",
    "saved 04g title metrics",
    "ogr",
)
if not source.isValid():
    raise RuntimeError("Could not open the 04g title-metrics GeoPackage.")
if source.crs().authid() != "EPSG:2157":
    raise RuntimeError(f"Unexpected input CRS: {source.crs().authid()}")
for field_name in ("source_fid", "title_ha", "bog_ha", "lease_flag"):
    if source.fields().indexFromName(field_name) < 0:
        raise RuntimeError(f"04g input is missing field {field_name!r}.")

selected = processing.run(
    "native:extractbyexpression",
    {
        "INPUT": source,
        "EXPRESSION": FILTER_EXPRESSION,
        "OUTPUT": "memory:",
    },
)["OUTPUT"]
if selected.featureCount() == 0:
    raise RuntimeError("The >100 ha screened-bog filter returned no titles.")
if any(feature["bog_ha"] <= 100 for feature in selected.getFeatures()):
    raise RuntimeError("The temporary selection contains a title that does not satisfy bog_ha > 100.")

selected = copy_with_fresh_ids(selected)
remove_loaded_path(OUTPUT_PATH)
remove_loaded_path(PARTIAL_PATH)

options = QgsVectorFileWriter.SaveVectorOptions()
options.driverName = "GPKG"
options.layerName = OUTPUT_LAYER
options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
result = QgsVectorFileWriter.writeAsVectorFormatV3(
    selected,
    str(PARTIAL_PATH),
    QgsProject.instance().transformContext(),
    options,
)
error = result[0] if isinstance(result, tuple) else result
if error != QgsVectorFileWriter.NoError:
    detail = result[1] if isinstance(result, tuple) and len(result) > 1 else str(error)
    raise RuntimeError(f"Could not write the temporary >100 ha subset: {detail}")

partial = QgsVectorLayer(
    f"{PARTIAL_PATH}|layername={OUTPUT_LAYER}",
    "validated temporary >100 ha titles",
    "ogr",
)
if not partial.isValid():
    raise RuntimeError("The temporary >100 ha GeoPackage could not be reopened.")
if partial.featureCount() != selected.featureCount():
    raise RuntimeError(
        f"Temporary output count mismatch: memory {selected.featureCount():,}, "
        f"GeoPackage {partial.featureCount():,}."
    )
partial = None
os.replace(PARTIAL_PATH, OUTPUT_PATH)

saved = QgsVectorLayer(
    f"{OUTPUT_PATH}|layername={OUTPUT_LAYER}",
    DISPLAY_NAME,
    "ogr",
)
if not saved.isValid():
    raise RuntimeError("The completed >100 ha GeoPackage could not be reopened.")
if saved.featureCount() != selected.featureCount():
    raise RuntimeError(
        f"Final output count mismatch: expected {selected.featureCount():,}, "
        f"reopened {saved.featureCount():,}."
    )

bog_values = [feature["bog_ha"] for feature in saved.getFeatures()]
title_area = sum(feature["title_ha"] for feature in saved.getFeatures())
leasehold_flagged = sum(feature["lease_flag"] for feature in saved.getFeatures())

symbol = QgsFillSymbol.createSimple(
    {
        "color": "#dc6b32",
        "outline_color": "#7c2d12",
        "outline_width": "0.35",
    }
)
symbol.setOpacity(0.45)
saved.setRenderer(QgsSingleSymbolRenderer(symbol))
project = QgsProject.instance()
project.addMapLayer(saved)
project.layerTreeRoot().findLayer(saved.id()).setItemVisibilityChecked(True)
iface.mapCanvas().setExtent(saved.extent())
iface.mapCanvas().refresh()

print("04h >100 ha screened-bog title subset complete")
print(f"Filter: {FILTER_EXPRESSION}")
print(f"Titles saved: {saved.featureCount():,}")
print(f"Screened-bog area range: {min(bog_values):.2f}–{max(bog_values):.2f} ha per title")
print(f"Summed screened-bog overlap: {sum(bog_values):.2f} ha")
print(f"Summed full title area: {title_area:.2f} ha")
print(f"Titles flagged for leasehold overlap: {leasehold_flagged:,}")
print(f"Output: {OUTPUT_PATH.relative_to(PROJECT_DIR)}")
