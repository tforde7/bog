"""Subtract LPIS-declared 2025 parcels from the screened bog-core layer.

This uses the 490,002-parcel subset produced by 04c, rather than processing
the 4.9-million-parcel national LPIS source. It removes only the parts of the
screened bog core inside those parcels. The result remains a screening layer,
not proof of private ownership.
"""

from pathlib import Path

from qgis.core import (
    QgsFillSymbol,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
import processing


PROJECT = QgsProject.instance()
PROJECT_PATH = Path(PROJECT.fileName())
if not PROJECT_PATH.is_file():
    raise RuntimeError("Save the QGIS project before running this script.")

ROOT = PROJECT_PATH.parent
SOURCE_NAME = "04b — Bog core outside known State land and SAC/NHA"
LPIS = ROOT / "data" / "processed" / "04c_lpis2025_bog_overlap.gpkg"
LPIS_LAYER_NAME = "lpis2025_bog_overlap"
OUTPUT = ROOT / "data" / "processed" / "04d_bog_core_outside_lpis2025.gpkg"
OUTPUT_LAYER_NAME = "bog_core_outside_lpis2025"
DISPLAY_NAME = "04d — Bog core outside State land, SAC/NHA and LPIS 2025"


def project_layer(name):
    matches = [layer for layer in PROJECT.mapLayers().values() if layer.name() == name]
    if not matches:
        raise RuntimeError(f"Required source layer is not loaded: {name}. Run 04b first.")
    return matches[0]


def remove_output(path):
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            candidate.unlink()


def fixed(layer):
    """Repair a temporary copy; never change the source layer."""
    return processing.run(
        "native:fixgeometries", {"INPUT": layer, "METHOD": 1, "OUTPUT": "memory:"}
    )["OUTPUT"]


def area_ha(layer):
    return sum(feature.geometry().area() for feature in layer.getFeatures()) / 10_000


def style(layer):
    symbol = QgsFillSymbol.createSimple(
        {"color": "#7353a8", "outline_color": "#43276d", "outline_width": "0.25"}
    )
    symbol.setOpacity(0.75)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


if not LPIS.is_file():
    raise FileNotFoundError(f"LPIS overlap subset is missing: {LPIS}. Run 04c first.")

screened_bog = project_layer(SOURCE_NAME)
lpis_overlap = QgsVectorLayer(
    f"{LPIS}|layername={LPIS_LAYER_NAME}", "LPIS 2025 overlap subset", "ogr"
)
if not lpis_overlap.isValid():
    raise RuntimeError("QGIS could not open the LPIS overlap subset written by 04c.")

# Difference retains the parts of INPUT outside the LPIS parcels. Repairing the
# small bog input avoids invalid-geometry failures without modifying 04b or 04c.
bog_fixed = fixed(screened_bog)
result = processing.run(
    "native:difference",
    {"INPUT": bog_fixed, "OVERLAY": lpis_overlap, "OUTPUT": "memory:"},
)["OUTPUT"]
result = fixed(result)

for layer in list(PROJECT.mapLayers().values()):
    if layer.name() == DISPLAY_NAME:
        PROJECT.removeMapLayer(layer.id())

# Only replace a previous 04d output after the spatial operation succeeded.
remove_output(OUTPUT)
options = QgsVectorFileWriter.SaveVectorOptions()
options.driverName = "GPKG"
options.layerName = OUTPUT_LAYER_NAME
error, message, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
    result, str(OUTPUT), PROJECT.transformContext(), options
)
if error != QgsVectorFileWriter.NoError:
    raise RuntimeError(f"Could not write the LPIS-screened bog layer: {message}")

output_layer = QgsVectorLayer(
    f"{OUTPUT}|layername={OUTPUT_LAYER_NAME}", DISPLAY_NAME, "ogr"
)
if not output_layer.isValid():
    raise RuntimeError("QGIS could not reopen the LPIS-screened bog output.")

style(output_layer)
PROJECT.addMapLayer(output_layer)
iface.mapCanvas().setExtent(output_layer.extent())
iface.mapCanvas().refresh()

before_ha = area_ha(bog_fixed)
after_ha = area_ha(output_layer)
print("LPIS 2025 subtraction complete")
print(f"Screened bog core before LPIS subtraction: {before_ha:,.2f} ha")
print(f"Outside LPIS-declared parcels: {after_ha:,.2f} ha")
print(f"Removed by LPIS footprint: {before_ha - after_ha:,.2f} ha")
print(f"Created {OUTPUT.relative_to(ROOT)}")
print("This is a screening result, not verified private ownership.")
