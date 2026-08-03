"""Preview connectivity-aware bog clusters in QGIS, without GeoPackage export.

This script intentionally separates the analysis from persistent-vector export.
QGIS's multipart-to-singlepart tool can retain duplicate internal feature IDs;
those IDs do not affect the cluster calculation or map display, but they make
some GeoPackage exports fail.  This step therefore produces an inspectable
QGIS preview and a small CSV sensitivity report only.

Rule under review: link bog-core pieces no more than 250 m apart, then retain
clusters containing at least 100 ha of original (unbuffered) core bog.
"""

import csv
from pathlib import Path

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFillSymbol,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsSpatialIndex,
    QgsVectorLayer,
    QgsWkbTypes,
)
import processing


PROJECT = QgsProject.instance()
PROJECT_PATH = Path(PROJECT.fileName())
if not PROJECT_PATH.is_file():
    raise RuntimeError("Save the QGIS project before running this script.")

ROOT = PROJECT_PATH.parent
CORE = ROOT / "data" / "processed" / "01_bog_core_evidence.gpkg"
REPORT = ROOT / "docs" / "03_cluster_gap_sensitivity.csv"
DEFAULT_GAP_M = 250
GAPS_M = (100, DEFAULT_GAP_M, 500)
MIN_CORE_HA = 100.0


def add_field(layer, name, field_type, formula, length=20, precision=3):
    return processing.run(
        "native:fieldcalculator",
        {
            "INPUT": layer,
            "FIELD_NAME": name,
            "FIELD_TYPE": field_type,
            "FIELD_LENGTH": length,
            "FIELD_PRECISION": precision,
            "FORMULA": formula,
            "OUTPUT": "memory:",
        },
    )["OUTPUT"]


def make_clusters(pieces, piece_index, piece_geometries, piece_areas, gap_m):
    """Create one buffered footprint per connected bog system."""
    buffered = processing.run(
        "native:buffer",
        {
            "INPUT": pieces,
            "DISTANCE": gap_m / 2.0,
            "SEGMENTS": 8,
            "END_CAP_STYLE": 0,
            "JOIN_STYLE": 0,
            "MITER_LIMIT": 2,
            "DISSOLVE": True,
            "OUTPUT": "memory:",
        },
    )["OUTPUT"]
    buffered = processing.run(
        "native:fixgeometries", {"INPUT": buffered, "METHOD": 1, "OUTPUT": "memory:"}
    )["OUTPUT"]
    clusters = processing.run(
        "native:multiparttosingleparts", {"INPUT": buffered, "OUTPUT": "memory:"}
    )["OUTPUT"]

    provider = clusters.dataProvider()
    provider.addAttributes(
        [
            QgsField("cluster_id", QVariant.Int),
            QgsField("link_gap_m", QVariant.Double, len=10, prec=1),
            QgsField("core_ha", QVariant.Double, len=20, prec=3),
            QgsField("meets_100ha", QVariant.Int),
        ]
    )
    clusters.updateFields()
    fields = clusters.fields()
    id_idx = fields.indexOf("cluster_id")
    gap_idx = fields.indexOf("link_gap_m")
    core_idx = fields.indexOf("core_ha")
    meets_idx = fields.indexOf("meets_100ha")

    if not clusters.startEditing():
        raise RuntimeError("Could not calculate attributes on the temporary cluster layer.")
    for number, feature in enumerate(clusters.getFeatures(), start=1):
        footprint = feature.geometry()
        core_ha = sum(
            piece_areas[piece_id]
            for piece_id in piece_index.intersects(footprint.boundingBox())
            if footprint.intersects(piece_geometries[piece_id])
        )
        clusters.changeAttributeValue(feature.id(), id_idx, number)
        clusters.changeAttributeValue(feature.id(), gap_idx, float(gap_m))
        clusters.changeAttributeValue(feature.id(), core_idx, core_ha)
        clusters.changeAttributeValue(feature.id(), meets_idx, int(core_ha >= MIN_CORE_HA))
    if not clusters.commitChanges():
        raise RuntimeError("Could not save calculated cluster attributes.")
    return clusters


def make_display_layer(source, include, name):
    """Copy selected rows to a clean memory layer for reliable QGIS display."""
    geometry_type = QgsWkbTypes.displayString(source.wkbType())
    target = QgsVectorLayer(f"{geometry_type}?crs={source.crs().authid()}", name, "memory")
    target.dataProvider().addAttributes(source.fields())
    target.updateFields()
    copied = []
    for row_number, feature in enumerate(source.getFeatures(), start=1):
        if bool(feature["core_ha"] >= MIN_CORE_HA) != include:
            continue
        copied_feature = QgsFeature(target.fields())
        copied_feature.setId(row_number)
        copied_feature.setGeometry(feature.geometry())
        copied_feature.setAttributes(feature.attributes())
        copied.append(copied_feature)
    success, _ = target.dataProvider().addFeatures(copied)
    if not success:
        raise RuntimeError(f"Could not create the display layer: {name}")
    return target


def style(layer, colour):
    symbol = QgsFillSymbol.createSimple(
        {"color": colour, "outline_color": colour, "outline_width": "0.35"}
    )
    symbol.setOpacity(0.55)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


if not CORE.is_file():
    raise FileNotFoundError(f"Bog-core evidence layer is missing: {CORE}")
core = QgsVectorLayer(str(CORE), "bog core evidence", "ogr")
if not core.isValid():
    raise RuntimeError("QGIS could not open the bog-core evidence layer.")

pieces = processing.run(
    "native:multiparttosingleparts", {"INPUT": core, "OUTPUT": "memory:"}
)["OUTPUT"]
pieces = processing.run(
    "native:fixgeometries", {"INPUT": pieces, "METHOD": 1, "OUTPUT": "memory:"}
)["OUTPUT"]
pieces = add_field(pieces, "piece_ha", 0, "$area / 10000.0", length=20, precision=3)
piece_features = list(pieces.getFeatures())
piece_index = QgsSpatialIndex()
for feature in piece_features:
    piece_index.addFeature(feature)
piece_geometries = {feature.id(): feature.geometry() for feature in piece_features}
piece_areas = {feature.id(): feature["piece_ha"] for feature in piece_features}

summary_rows = []
default_clusters = None
for gap_m in GAPS_M:
    clusters = make_clusters(pieces, piece_index, piece_geometries, piece_areas, gap_m)
    features = list(clusters.getFeatures())
    included = [feature for feature in features if feature["core_ha"] >= MIN_CORE_HA]
    summary_rows.append(
        {
            "maximum_interpatch_gap_m": gap_m,
            "cluster_count": len(features),
            "clusters_at_least_100ha": len(included),
            "core_hectares_at_least_100ha": f"{sum(feature['core_ha'] for feature in included):.2f}",
            "core_hectares_below_100ha": f"{sum(feature['core_ha'] for feature in features if feature['core_ha'] < MIN_CORE_HA):.2f}",
            "method_note": "Pieces are linked when their closest edges are no more than this distance apart; areas are unbuffered core-bog area.",
        }
    )
    if gap_m == DEFAULT_GAP_M:
        default_clusters = clusters

with REPORT.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=summary_rows[0].keys())
    writer.writeheader()
    writer.writerows(summary_rows)

for layer in list(PROJECT.mapLayers().values()):
    if layer.name().startswith("03 — Bog clusters"):
        PROJECT.removeMapLayer(layer.id())

included_layer = make_display_layer(
    default_clusters, True, "03 — Bog clusters ≥100 ha (250 m link) — preview"
)
audit_layer = make_display_layer(
    default_clusters, False, "03 — Bog clusters <100 ha — audit (250 m link) — preview"
)
style(included_layer, "#207567")
style(audit_layer, "#d2872c")
PROJECT.addMapLayer(audit_layer)
PROJECT.addMapLayer(included_layer)
if included_layer.featureCount():
    iface.mapCanvas().setExtent(included_layer.extent())
else:
    iface.mapCanvas().setExtent(default_clusters.extent())
iface.mapCanvas().refresh()

default_row = next(row for row in summary_rows if row["maximum_interpatch_gap_m"] == DEFAULT_GAP_M)
print("Connectivity-aware bog-cluster preview complete")
print(f"Rule: at least {MIN_CORE_HA:.0f} ha of core bog; {DEFAULT_GAP_M} m maximum inter-patch gap")
print(f"Clusters: {default_row['cluster_count']}; included: {default_row['clusters_at_least_100ha']}")
print(f"Included core-bog area: {default_row['core_hectares_at_least_100ha']} ha")
print(f"Sensitivity report: {REPORT.relative_to(ROOT)}")
print("These two map layers are preview layers. No GeoPackage has been written in this step.")
