"""Create the first national bog-core evidence layer from the 2024 peat map.

Run from the QGIS Python Console. The script retains only the three peat soil
classes in scope for this project, reprojects them to EPSG:2157, and adds the
processed result to the open QGIS project.
"""

from pathlib import Path

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
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

PROJECT_ROOT = PROJECT_PATH.parent
SOURCE_ZIP = PROJECT_ROOT / "data" / "raw" / "irish_peat_soils_map_2024.zip"
SOURCE_MEMBER = "IPSM_shared_june24/vector_dataset/IPSM_soiltype.shp"
SOURCE_URI = f"/vsizip/{SOURCE_ZIP}/{SOURCE_MEMBER}"
OUTPUT = PROJECT_ROOT / "data" / "processed" / "01_bog_core_evidence.gpkg"
OUTPUT_LAYER = "bog_core_evidence_itm"
DISPLAY_NAME = "01 — Bog core evidence (2024 peat map)"

TARGET_CLASSES = (
    "Raised bogs",
    "Lowland Atlantic blanket bogs",
    "Mountain blanket bogs",
)
COLOURS = {
    "Raised bogs": "#7a3e8e",
    "Lowland Atlantic blanket bogs": "#2d7f5e",
    "Mountain blanket bogs": "#588157",
}


def style_by_bog_type(layer: QgsVectorLayer) -> None:
    categories = []
    field_index = layer.fields().indexOf("descrip")
    for value in sorted(layer.uniqueValues(field_index)):
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(QColor(COLOURS.get(value, "#808080")))
        categories.append(QgsRendererCategory(value, symbol, value))
    layer.setRenderer(QgsCategorizedSymbolRenderer("descrip", categories))


if not SOURCE_ZIP.is_file():
    raise FileNotFoundError(f"Source archive not found: {SOURCE_ZIP}")

source = QgsVectorLayer(SOURCE_URI, "2024 peat soil type source", "ogr")
if not source.isValid():
    raise RuntimeError("QGIS could not load the 2024 peat soil type source layer.")

# These are precisely the three peat classes in the project scope. Fens and
# other peat remain available in the raw preview layer for later context work.
expression = "\"descrip\" IN (" + ", ".join(repr(value) for value in TARGET_CLASSES) + ")"
selected = processing.run(
    "native:extractbyexpression",
    {"INPUT": source, "EXPRESSION": expression, "OUTPUT": "memory:"},
)["OUTPUT"]

# Replace only the output owned by this script, making the result reproducible.
for path in (OUTPUT, Path(f"{OUTPUT}-wal"), Path(f"{OUTPUT}-shm")):
    if path.exists():
        path.unlink()

result = processing.run(
    "native:reprojectlayer",
    {
        "INPUT": selected,
        "TARGET_CRS": QgsCoordinateReferenceSystem("EPSG:2157"),
        "OPERATION": "",
        "OUTPUT": f"{OUTPUT}|layername={OUTPUT_LAYER}",
    },
)["OUTPUT"]

for layer in list(PROJECT.mapLayers().values()):
    if layer.name() == DISPLAY_NAME:
        PROJECT.removeMapLayer(layer.id())

output_layer = QgsVectorLayer(result, DISPLAY_NAME, "ogr")
if not output_layer.isValid():
    raise RuntimeError("The processed bog-core evidence layer could not be opened.")

style_by_bog_type(output_layer)
PROJECT.addMapLayer(output_layer)
iface.mapCanvas().setExtent(output_layer.extent())
iface.mapCanvas().refresh()

print(f"Created {OUTPUT.relative_to(PROJECT_ROOT)}")
print(f"Loaded {DISPLAY_NAME}: {output_layer.featureCount()} multipart class features.")
