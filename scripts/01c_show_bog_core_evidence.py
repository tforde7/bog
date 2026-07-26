"""Reload and visibly style the existing bog-core evidence output in QGIS.

This script changes only QGIS display state. It does not modify GIS data files.
"""

from pathlib import Path

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
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

if not OUTPUT.is_file():
    raise FileNotFoundError(f"Processed output not found: {OUTPUT}")

for layer in list(PROJECT.mapLayers().values()):
    if layer.name() == DISPLAY_NAME:
        PROJECT.removeMapLayer(layer.id())

layer = QgsVectorLayer(str(OUTPUT), DISPLAY_NAME, "ogr")
if not layer.isValid():
    raise RuntimeError("QGIS could not reopen the processed GeoPackage.")

categories = []
field_index = layer.fields().indexOf("descrip")
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
canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:2157"))
extent = layer.extent()
extent.scale(1.03)
canvas.setExtent(extent)
canvas.refreshAllLayers()

print(f"Loaded {DISPLAY_NAME}")
print(f"Layer CRS: {layer.crs().authid()}")
print(f"Layer extent: {layer.extent().toString()}")
print(f"Canvas extent: {canvas.extent().toString()}")
