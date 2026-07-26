"""Load the unmodified 2024 Irish Peat Soils Map type layer into QGIS.

Run from the QGIS Python Console. This is a preview only: it does not create
outputs, reproject data, or modify the source ZIP archive.
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


project = QgsProject.instance()
project_path = Path(project.fileName())
if not project_path:
    raise RuntimeError("Save the QGIS project before running this script.")

project_root = project_path.parent
source_zip = project_root / "data" / "raw" / "irish_peat_soils_map_2024.zip"
source_member = "IPSM_shared_june24/vector_dataset/IPSM_soiltype.shp"
source_uri = f"/vsizip/{source_zip}/{source_member}"
layer_name = "RAW — 2024 Irish Peat Soil Type"

if not source_zip.is_file():
    raise FileNotFoundError(f"Source archive not found: {source_zip}")

# Avoid duplicate preview layers when the script is run again.
for existing_layer in project.mapLayers().values():
    if existing_layer.source() == source_uri:
        iface.mapCanvas().setExtent(existing_layer.extent())
        iface.mapCanvas().refresh()
        print(f"Preview layer is already loaded: {existing_layer.name()}")
        break
else:
    layer = QgsVectorLayer(source_uri, layer_name, "ogr")
    if not layer.isValid():
        raise RuntimeError("QGIS could not read the peat soil type layer from the ZIP archive.")

    colours = {
        "Raised bogs": "#7a3e8e",
        "Lowland Atlantic blanket bogs": "#2d7f5e",
        "Mountain blanket bogs": "#588157",
        "Fens": "#4f9da6",
        "Other peat soils": "#b08d57",
        "non-peat": "#d9d9d9",
    }
    categories = []
    for value in sorted(layer.uniqueValues(layer.fields().indexOf("descrip"))):
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(QColor(colours.get(value, "#808080")))
        categories.append(QgsRendererCategory(value, symbol, value))

    layer.setRenderer(QgsCategorizedSymbolRenderer("descrip", categories))
    project.addMapLayer(layer)
    iface.mapCanvas().setExtent(layer.extent())
    iface.mapCanvas().refresh()
    print(f"Loaded {layer_name} with {len(categories)} classes.")
