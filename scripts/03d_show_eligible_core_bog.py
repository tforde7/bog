"""Show unbuffered bog-core pieces inside the active eligible clusters.

The 250 m cluster layer is a connectivity footprint. This script derives the
actual mapped bog pieces within eligible clusters, so later screens act on bog
evidence rather than temporary connection buffers.
"""

from pathlib import Path

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsProject,
    QgsRendererCategory,
    QgsSymbol,
    QgsVectorLayer,
)
import processing


PROJECT = QgsProject.instance()
PROJECT_PATH = Path(PROJECT.fileName())
if not PROJECT_PATH.is_file():
    raise RuntimeError("Save the QGIS project before running this script.")

ROOT = PROJECT_PATH.parent
CORE = ROOT / "data" / "processed" / "01_bog_core_evidence.gpkg"
CLUSTER_NAME = "03 — Bog clusters ≥100 ha (250 m link) — preview"
DISPLAY_NAME = "03d — Eligible unbuffered bog core (250 m / ≥100 ha clusters)"
COLOURS = {
    "Raised bogs": "#7a3e8e",
    "Lowland Atlantic blanket bogs": "#2d7f5e",
    "Mountain blanket bogs": "#588157",
}


def style_by_type(layer):
    categories = []
    field_index = layer.fields().indexOf("descrip")
    for value in sorted(layer.uniqueValues(field_index)):
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(QColor(COLOURS.get(value, "#808080")))
        categories.append(QgsRendererCategory(value, symbol, value))
    layer.setRenderer(QgsCategorizedSymbolRenderer("descrip", categories))


if not CORE.is_file():
    raise FileNotFoundError(f"Bog-core evidence layer is missing: {CORE}")

cluster_layers = [layer for layer in PROJECT.mapLayers().values() if layer.name() == CLUSTER_NAME]
if not cluster_layers:
    raise RuntimeError(
        "The 250 m eligible-cluster preview is not loaded. Run 03c_preview_bog_clusters.py first."
    )
clusters = cluster_layers[0]
core = QgsVectorLayer(str(CORE), "bog core evidence", "ogr")
if not core.isValid():
    raise RuntimeError("QGIS could not open the bog-core evidence layer.")

pieces = processing.run(
    "native:multiparttosingleparts", {"INPUT": core, "OUTPUT": "memory:"}
)["OUTPUT"]
pieces = processing.run(
    "native:fixgeometries", {"INPUT": pieces, "METHOD": 1, "OUTPUT": "memory:"}
)["OUTPUT"]
eligible_core = processing.run(
    "native:extractbylocation",
    {
        "INPUT": pieces,
        "PREDICATE": [0],
        "INTERSECT": clusters,
        "OUTPUT": "memory:",
    },
)["OUTPUT"]

for layer in list(PROJECT.mapLayers().values()):
    if layer.name() == DISPLAY_NAME:
        PROJECT.removeMapLayer(layer.id())

eligible_core.setName(DISPLAY_NAME)
style_by_type(eligible_core)
PROJECT.addMapLayer(eligible_core)
iface.mapCanvas().setExtent(eligible_core.extent())
iface.mapCanvas().refresh()

area_ha = sum(feature.geometry().area() for feature in eligible_core.getFeatures()) / 10_000
print("Loaded eligible unbuffered bog core")
print(f"Pieces: {eligible_core.featureCount()}")
print(f"Mapped core area: {area_ha:.2f} ha")
print("This is the layer that will be screened against known State land and designations.")
