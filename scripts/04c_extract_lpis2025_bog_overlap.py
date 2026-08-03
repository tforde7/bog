"""Extract 2025 LPIS parcels that overlap screened bog core.

This avoids a national Difference against 4.9 million LPIS features.  It asks
the GeoPackage for features only within each bog-piece bounding box (using its
RTree spatial index), confirms actual intersection, deduplicates parcel IDs,
and writes the much smaller overlap subset for inspection.

Run this only after 04b. It does NOT subtract LPIS from bog core.
"""

from pathlib import Path

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsFeature,
    QgsFeatureRequest,
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


PROJECT = QgsProject.instance()
PROJECT_PATH = Path(PROJECT.fileName())
if not PROJECT_PATH.is_file():
    raise RuntimeError("Save the QGIS project before running this script.")

ROOT = PROJECT_PATH.parent
LPIS = ROOT / "data" / "raw" / "GEO_860-PARCELS_GPK.gpkg"
LPIS_LAYER_NAME = "GEO_860_PARCELS_ANON"
SOURCE_NAME = "04b — Bog core outside known State land and SAC/NHA"
OUTPUT = ROOT / "data" / "processed" / "04c_lpis2025_bog_overlap.gpkg"
OUTPUT_LAYER_NAME = "lpis2025_bog_overlap"
DISPLAY_NAME = "04c — LPIS 2025 parcels intersecting screened bog core"
KEEP_FIELDS = ("claim_area", "crop", "olr", "commonage_ind", "par_lab", "digitised")
BATCH_SIZE = 2_000


def project_layer(name):
    matches = [layer for layer in PROJECT.mapLayers().values() if layer.name() == name]
    if not matches:
        raise RuntimeError(f"Required source layer is not loaded: {name}. Run 04b first.")
    return matches[0]


def remove_output(path):
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            candidate.unlink()


def style(layer):
    symbol = QgsFillSymbol.createSimple(
        {"color": "#d2872c", "outline_color": "#9a5613", "outline_width": "0.25"}
    )
    symbol.setOpacity(0.45)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


if not LPIS.is_file():
    raise FileNotFoundError(f"LPIS parcel GeoPackage is missing: {LPIS}")

screened_core = project_layer(SOURCE_NAME)
lpis = QgsVectorLayer(f"{LPIS}|layername={LPIS_LAYER_NAME}", "LPIS 2025 parcels", "ogr")
if not lpis.isValid():
    raise RuntimeError("QGIS could not open the LPIS 2025 parcel layer.")

# Use individual, repaired bog pieces as spatial-query windows.
pieces = processing.run(
    "native:multiparttosingleparts", {"INPUT": screened_core, "OUTPUT": "memory:"}
)["OUTPUT"]
pieces = processing.run(
    "native:fixgeometries", {"INPUT": pieces, "METHOD": 1, "OUTPUT": "memory:"}
)["OUTPUT"]
piece_features = list(pieces.getFeatures())

missing_fields = [name for name in KEEP_FIELDS if lpis.fields().indexOf(name) < 0]
if missing_fields:
    raise RuntimeError(f"Expected LPIS fields were not found: {', '.join(missing_fields)}")

# LPIS stores MultiPolygon ZM geometries. The temporary layer is deliberately
# 2D: downstream area and overlay work is planar, and height/measure values
# are not used. This avoids provider-type mismatches during insertion.
geometry_type = QgsWkbTypes.displayString(QgsWkbTypes.flatType(lpis.wkbType()))
subset = QgsVectorLayer(f"{geometry_type}?crs={lpis.crs().authid()}", "LPIS overlap subset", "memory")
if not subset.isValid():
    raise RuntimeError(f"Could not create the temporary LPIS subset layer ({geometry_type}).")
def preview_field(source_field):
    """Copy a source field without carrying across restrictive text widths."""
    field = QgsField(source_field)
    if field.type() == QVariant.String:
        field.setLength(0)
    return field


subset_provider = subset.dataProvider()
subset_provider.addAttributes(
    [QgsField("lpis_fid", QVariant.LongLong)]
    + [preview_field(lpis.fields().at(lpis.fields().indexOf(name))) for name in KEEP_FIELDS]
)
subset.updateFields()

field_indices = [lpis.fields().indexOf(name) for name in KEEP_FIELDS]
request_base = QgsFeatureRequest().setSubsetOfAttributes(list(KEEP_FIELDS), lpis.fields())
seen_fids = set()
batch = []


def flush_batch():
    global batch
    if not batch:
        return
    success, _ = subset_provider.addFeatures(batch)
    if not success:
        raise RuntimeError(
            "Could not add an LPIS parcel batch to the overlap subset: "
            f"{subset_provider.lastError()}"
        )
    batch = []


print(f"Screened bog pieces to query: {len(piece_features)}")
for position, bog_piece in enumerate(piece_features, start=1):
    bog_geometry = bog_piece.geometry()
    request = QgsFeatureRequest(request_base)
    request.setFilterRect(bog_geometry.boundingBox())
    for lpis_feature in lpis.getFeatures(request):
        lpis_fid = lpis_feature.id()
        if lpis_fid in seen_fids:
            continue
        if not bog_geometry.intersects(lpis_feature.geometry()):
            continue
        seen_fids.add(lpis_fid)
        copied = QgsFeature(subset.fields())
        # A provider allocates IDs for its own layer. Keep the LPIS ID as an
        # attribute, rather than reusing it as a memory-provider feature ID.
        geometry = QgsGeometry(lpis_feature.geometry())
        geometry.get().dropZValue()
        geometry.get().dropMValue()
        copied.setGeometry(geometry)
        copied.setAttributes([lpis_fid] + [lpis_feature[index] for index in field_indices])
        batch.append(copied)
        if len(batch) >= BATCH_SIZE:
            flush_batch()
    if position % 500 == 0 or position == len(piece_features):
        print(f"Queried {position:,}/{len(piece_features):,} bog pieces; LPIS overlaps found: {len(seen_fids):,}")
flush_batch()

for layer in list(PROJECT.mapLayers().values()):
    if layer.name() == DISPLAY_NAME:
        PROJECT.removeMapLayer(layer.id())
remove_output(OUTPUT)

options = QgsVectorFileWriter.SaveVectorOptions()
options.driverName = "GPKG"
options.layerName = OUTPUT_LAYER_NAME
error, message, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
    subset, str(OUTPUT), PROJECT.transformContext(), options
)
if error != QgsVectorFileWriter.NoError:
    raise RuntimeError(f"Could not write LPIS overlap subset: {message}")

output_uri = f"{OUTPUT}|layername={OUTPUT_LAYER_NAME}"
output_layer = QgsVectorLayer(output_uri, DISPLAY_NAME, "ogr")
if not output_layer.isValid():
    raise RuntimeError("QGIS could not reopen the written LPIS overlap subset.")
style(output_layer)
PROJECT.addMapLayer(output_layer)
iface.mapCanvas().setExtent(output_layer.extent())
iface.mapCanvas().refresh()

overlap_ha = sum(feature.geometry().area() for feature in output_layer.getFeatures()) / 10_000
print("LPIS 2025 overlap extract complete")
print(f"LPIS parcels intersecting screened bog core: {output_layer.featureCount():,}")
print(f"Total LPIS parcel area in the subset: {overlap_ha:,.2f} ha")
print(f"Created {OUTPUT.relative_to(ROOT)}")
print("Inspect this orange layer before running any LPIS subtraction.")
