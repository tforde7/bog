"""Screen raw bog-core evidence against known State land, SACs and NHAs.

This is intentionally before any clustering. It produces a simple primary
screening layer: mapped bog core outside LDA-known State land and outside SAC/
NHA boundaries. It is *not* a verified-private-ownership layer: unregistered,
unknown, non-State public, or other ownership situations can remain.

The primary screening result is persisted as a GeoPackage so it survives QGIS
restarts. The intermediate "outside known State land" layer remains a preview.
"""

from pathlib import Path
import os

from qgis.PyQt.QtGui import QColor
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
CORE = ROOT / "data" / "processed" / "01_bog_core_evidence.gpkg"
PRA_NAME = "04a — LDA PRA State Assets (known State land)"
LDA_NAME = "04a — LDA-sourced State Assets (known State land)"
SAC_NAME = "04a — NPWS SAC boundaries (2026)"
NHA_NAME = "04a — NPWS NHA boundaries (2019 — verify update)"
AFTER_STATE_NAME = "04b — Bog core outside known State land (includes SAC/NHA)"
PRIMARY_NAME = "04b — Bog core outside known State land and SAC/NHA"
PRIMARY_OUTPUT = ROOT / "data" / "processed" / "04b_screened_bog_core.gpkg"
PRIMARY_OUTPUT_LAYER = "screened_bog_core"
PRIMARY_TEMP_OUTPUT = PRIMARY_OUTPUT.with_name("04b_screened_bog_core_IN_PROGRESS.gpkg")


def project_layer(name):
    matches = [layer for layer in PROJECT.mapLayers().values() if layer.name() == name]
    if not matches:
        raise RuntimeError(f"Required source layer is not loaded: {name}. Run 04a first.")
    return matches[0]


def fixed(layer):
    """Repair a temporary processing copy; never edit the source layer."""
    return processing.run(
        "native:fixgeometries", {"INPUT": layer, "METHOD": 1, "OUTPUT": "memory:"}
    )["OUTPUT"]


def area_ha(layer):
    return sum(feature.geometry().area() for feature in layer.getFeatures()) / 10_000


def style(layer, colour, opacity):
    symbol = QgsFillSymbol.createSimple(
        {"color": colour, "outline_color": colour, "outline_width": "0.25"}
    )
    symbol.setOpacity(opacity)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


if not CORE.is_file():
    raise FileNotFoundError(f"Bog-core evidence layer is missing: {CORE}")

core = QgsVectorLayer(str(CORE), "bog core evidence", "ogr")
if not core.isValid():
    raise RuntimeError("QGIS could not open the bog-core evidence layer.")

# These sources were deliberately loaded and inspected in step 04a.
pra = project_layer(PRA_NAME)
lda = project_layer(LDA_NAME)
sac = project_layer(SAC_NAME)
nha = project_layer(NHA_NAME)

# Work only with repaired temporary copies. Each Difference is spatial: it
# removes only the overlapping part, rather than discarding an entire bog.
core_fixed = fixed(core)
after_pra = processing.run(
    "native:difference", {"INPUT": core_fixed, "OVERLAY": fixed(pra), "OUTPUT": "memory:"}
)["OUTPUT"]
after_state = processing.run(
    "native:difference", {"INPUT": after_pra, "OVERLAY": fixed(lda), "OUTPUT": "memory:"}
)["OUTPUT"]
after_sac = processing.run(
    "native:difference", {"INPUT": after_state, "OVERLAY": fixed(sac), "OUTPUT": "memory:"}
)["OUTPUT"]
primary = processing.run(
    "native:difference", {"INPUT": after_sac, "OVERLAY": fixed(nha), "OUTPUT": "memory:"}
)["OUTPUT"]

for layer in list(PROJECT.mapLayers().values()):
    if (
        layer.name() in (AFTER_STATE_NAME, PRIMARY_NAME)
        or str(PRIMARY_OUTPUT) in layer.source()
        or str(PRIMARY_TEMP_OUTPUT) in layer.source()
    ):
        PROJECT.removeMapLayer(layer.id())

after_state.setName(AFTER_STATE_NAME)
PRIMARY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
save_options = QgsVectorFileWriter.SaveVectorOptions()
save_options.driverName = "GPKG"
save_options.layerName = PRIMARY_OUTPUT_LAYER
save_options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
save_result = QgsVectorFileWriter.writeAsVectorFormatV3(
    primary,
    str(PRIMARY_TEMP_OUTPUT),
    PROJECT.transformContext(),
    save_options,
)
save_error = save_result[0] if isinstance(save_result, tuple) else save_result
if save_error != QgsVectorFileWriter.NoError:
    detail = save_result[1] if isinstance(save_result, tuple) and len(save_result) > 1 else str(save_error)
    raise RuntimeError(f"Could not write temporary screened bog core to {PRIMARY_TEMP_OUTPUT}: {detail}")

temporary_primary = QgsVectorLayer(
    f"{PRIMARY_TEMP_OUTPUT}|layername={PRIMARY_OUTPUT_LAYER}", PRIMARY_NAME, "ogr"
)
if not temporary_primary.isValid():
    raise RuntimeError("The temporary screened-bog GeoPackage could not be reopened.")
if temporary_primary.featureCount() != primary.featureCount():
    raise RuntimeError(
        f"Saved feature-count mismatch: memory {primary.featureCount()}, GeoPackage {temporary_primary.featureCount()}."
    )
temporary_primary = None
os.replace(PRIMARY_TEMP_OUTPUT, PRIMARY_OUTPUT)

saved_primary = QgsVectorLayer(
    f"{PRIMARY_OUTPUT}|layername={PRIMARY_OUTPUT_LAYER}", PRIMARY_NAME, "ogr"
)
if not saved_primary.isValid():
    raise RuntimeError("The promoted screened-bog GeoPackage could not be reopened.")

style(after_state, "#5a7d8d", 0.25)
style(saved_primary, "#1f7a5a", 0.70)
PROJECT.addMapLayer(after_state)
PROJECT.addMapLayer(saved_primary)
iface.mapCanvas().setExtent(saved_primary.extent())
iface.mapCanvas().refresh()

total_ha = area_ha(core_fixed)
after_state_ha = area_ha(after_state)
primary_ha = area_ha(saved_primary)
print("Bog-core State/designation screen complete")
print(f"Original mapped bog core: {total_ha:.2f} ha")
print(f"Outside LDA-known State land: {after_state_ha:.2f} ha")
print(f"Outside known State land and SAC/NHA: {primary_ha:.2f} ha")
print(f"Saved persistent screening layer: {PRIMARY_OUTPUT.relative_to(ROOT)}")
print(f"Saved screening features: {saved_primary.featureCount():,}")
print("Primary layer is a screening result, not verified private ownership.")
print("NHA input is the 2019 layer and should be refreshed before final publication.")
