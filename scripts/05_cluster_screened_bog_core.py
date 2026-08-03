"""Cluster the screened bog-core layer after State/SAC/NHA subtraction.

This is the first candidate-cluster layer in the intended order:
raw bog core -> State/designation screen -> 250 m connectivity -> 100 ha rule.

It creates QGIS preview layers and a one-row CSV summary. No GeoPackage is
written. A cluster is an ecological proximity grouping, not a landholding.
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

SOURCE_NAME = "04b — Bog core outside known State land and SAC/NHA"
INCLUDED_NAME = "05 — Candidate bog clusters ≥100 ha (250 m link)"
AUDIT_NAME = "05 — Bog clusters <100 ha — audit (250 m link)"
REPORT = PROJECT_PATH.parent / "docs" / "05_postscreen_cluster_summary.csv"
LINK_GAP_M = 250
MIN_CORE_HA = 100.0


def source_layer(name):
    matches = [layer for layer in PROJECT.mapLayers().values() if layer.name() == name]
    if not matches:
        raise RuntimeError(f"Required screening layer is not loaded: {name}. Run 04b first.")
    return matches[0]


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


def display_subset(source, include, name):
    geometry_type = QgsWkbTypes.displayString(source.wkbType())
    target = QgsVectorLayer(f"{geometry_type}?crs={source.crs().authid()}", name, "memory")
    target.dataProvider().addAttributes(source.fields())
    target.updateFields()
    features = []
    for number, feature in enumerate(source.getFeatures(), start=1):
        if bool(feature["core_ha"] >= MIN_CORE_HA) != include:
            continue
        copied = QgsFeature(target.fields())
        copied.setId(number)
        copied.setGeometry(feature.geometry())
        copied.setAttributes(feature.attributes())
        features.append(copied)
    success, _ = target.dataProvider().addFeatures(features)
    if not success:
        raise RuntimeError(f"Could not create preview layer: {name}")
    return target


def style(layer, colour, opacity):
    symbol = QgsFillSymbol.createSimple(
        {"color": colour, "outline_color": colour, "outline_width": "0.35"}
    )
    symbol.setOpacity(opacity)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


screened_core = source_layer(SOURCE_NAME)
pieces = processing.run(
    "native:multiparttosingleparts", {"INPUT": screened_core, "OUTPUT": "memory:"}
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

buffered = processing.run(
    "native:buffer",
    {
        "INPUT": pieces,
        "DISTANCE": LINK_GAP_M / 2.0,
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
    clusters.changeAttributeValue(feature.id(), gap_idx, float(LINK_GAP_M))
    clusters.changeAttributeValue(feature.id(), core_idx, core_ha)
    clusters.changeAttributeValue(feature.id(), meets_idx, int(core_ha >= MIN_CORE_HA))
if not clusters.commitChanges():
    raise RuntimeError("Could not save temporary cluster attributes.")

all_clusters = list(clusters.getFeatures())
included = [feature for feature in all_clusters if feature["core_ha"] >= MIN_CORE_HA]
with REPORT.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(
        handle,
        fieldnames=("link_gap_m", "minimum_core_ha", "cluster_count", "included_cluster_count", "included_core_ha", "audit_core_ha"),
    )
    writer.writeheader()
    writer.writerow(
        {
            "link_gap_m": LINK_GAP_M,
            "minimum_core_ha": MIN_CORE_HA,
            "cluster_count": len(all_clusters),
            "included_cluster_count": len(included),
            "included_core_ha": f"{sum(feature['core_ha'] for feature in included):.2f}",
            "audit_core_ha": f"{sum(feature['core_ha'] for feature in all_clusters if feature['core_ha'] < MIN_CORE_HA):.2f}",
        }
    )

for layer in list(PROJECT.mapLayers().values()):
    if layer.name() in (INCLUDED_NAME, AUDIT_NAME):
        PROJECT.removeMapLayer(layer.id())

included_layer = display_subset(clusters, True, INCLUDED_NAME)
audit_layer = display_subset(clusters, False, AUDIT_NAME)
style(included_layer, "#14735c", 0.65)
style(audit_layer, "#d2872c", 0.40)
PROJECT.addMapLayer(audit_layer)
PROJECT.addMapLayer(included_layer)
if included_layer.featureCount():
    iface.mapCanvas().setExtent(included_layer.extent())
else:
    iface.mapCanvas().setExtent(clusters.extent())
iface.mapCanvas().refresh()

print("Post-screen bog clustering complete")
print(f"Rule: {LINK_GAP_M} m maximum gap; at least {MIN_CORE_HA:.0f} ha unbuffered core bog")
print(f"Clusters: {len(all_clusters)}; included: {len(included)}")
print(f"Included core-bog area: {sum(feature['core_ha'] for feature in included):.2f} ha")
print(f"Summary: {REPORT.relative_to(PROJECT_PATH.parent)}")
