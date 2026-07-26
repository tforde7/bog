"""Reload and zoom to the bog-core evidence layer without changing canvas CRS."""

from pathlib import Path

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFillSymbol,
    QgsProject,
    QgsRendererCategory,
    QgsVectorLayer,
)


PROJECT = QgsProject.instance()
PROJECT_PATH = Path(PROJECT.fileName())
if not PROJECT_PATH.is_file():
    raise RuntimeError("Save the QGIS project before running this script.")

OUTPUT = PROJECT_PATH.parent / "data" / "processed" / "01_bog_core_evidence.gpkg"
DISPLAY_NAME = "01 — Bog core evidence (2024 peat map)"
COLOURS = {
    "Raised bogs": "#7a3e8e",
    "Lowland Atlantic blanket bogs": "#2d7f5e",
    "Mountain blanket bogs": "#588157",
}

for existing in list(PROJECT.mapLayers().values()):
    if existing.name() == DISPLAY_NAME:
        PROJECT.removeMapLayer(existing.id())

layer = QgsVectorLayer(str(OUTPUT), DISPLAY_NAME, "ogr")
if not layer.isValid():
    raise RuntimeError(f"QGIS could not open: {OUTPUT}")

field_index = layer.fields().indexOf("descrip")
categories = []
for value in sorted(layer.uniqueValues(field_index)):
    symbol = QgsFillSymbol.createSimple(
        {
            "color": COLOURS.get(value, "#808080"),
            "outline_color": "#202020",
            "outline_width": "0.15",
            "outline_width_unit": "MM",
        }
    )
    symbol.setOpacity(1.0)
    categories.append(QgsRendererCategory(value, symbol, value))

layer.setRenderer(QgsCategorizedSymbolRenderer("descrip", categories))
PROJECT.addMapLayer(layer)
PROJECT.layerTreeRoot().findLayer(layer.id()).setItemVisibilityChecked(True)

canvas = iface.mapCanvas()
canvas.zoomToFeatureExtent(layer.extent())
canvas.refreshAllLayers()

print(f"Loaded {DISPLAY_NAME}")
print(f"Project CRS: {PROJECT.crs().authid()}")
print(f"Canvas CRS: {canvas.mapSettings().destinationCrs().authid()}")
print(f"Layer extent: {layer.extent().toString()}")
print(f"Canvas extent: {canvas.extent().toString()}")
