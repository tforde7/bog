"""Create connectivity-aware bog clusters and apply a 100 ha cluster threshold.

Run from the QGIS Python Console. This version avoids provider-specific
location-summary tools so it works consistently in the installed QGIS build.

Pieces are connected when their closest edges are no more than the chosen gap
apart. The temporary buffer identifies a cluster only; `core_ha` is always the
sum of the original, unbuffered bog-core pieces in that cluster.
"""

import csv
from pathlib import Path

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsFeature,
    QgsWkbTypes,
    QgsField,
    QgsFillSymbol,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsSpatialIndex,
    QgsVectorLayer,
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


def remove_output(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            candidate.unlink()


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


def clone_with_new_ids(source):
    """Copy features to a fresh memory layer, assigning unique provider IDs."""
    geometry_type = QgsWkbTypes.displayString(source.wkbType())
    uri = f"{geometry_type}?crs={source.crs().authid()}"
    target = QgsVectorLayer(uri, "export-ready clusters", "memory")
    target.dataProvider().addAttributes(source.fields())
    target.updateFields()
    copied = []
    for feature in source.getFeatures():
        new_feature = QgsFeature(target.fields())
        new_feature.setGeometry(feature.geometry())
        new_feature.setAttributes(feature.attributes())
        copied.append(new_feature)
    if not target.dataProvider().addFeatures(copied)[0]:
        raise RuntimeError("Could not prepare a clean temporary cluster layer for export.")
    return target


def make_clusters(pieces, piece_index, piece_geometries, piece_areas, gap_m):
    """Build one feature per cluster and calculate its unbuffered core area."""
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
    # Dissolve returns a multipart geometry. Split it so each connected group is a row.
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
    cluster_id_idx = clusters.fields().indexOf("cluster_id")
    gap_idx = clusters.fields().indexOf("link_gap_m")
    core_idx = clusters.fields().indexOf("core_ha")
    meets_idx = clusters.fields().indexOf("meets_100ha")

    if not clusters.startEditing():
        raise RuntimeError("Could not open the temporary cluster layer for attribute calculation.")
    for cluster_number, feature in enumerate(clusters.getFeatures(), start=1):
        geometry = feature.geometry()
        core_ha = sum(
            piece_areas[piece_id]
            for piece_id in piece_index.intersects(geometry.boundingBox())
            if geometry.intersects(piece_geometries[piece_id])
        )
        clusters.changeAttributeValue(feature.id(), cluster_id_idx, cluster_number)
        clusters.changeAttributeValue(feature.id(), gap_idx, float(gap_m))
        clusters.changeAttributeValue(feature.id(), core_idx, core_ha)
        clusters.changeAttributeValue(feature.id(), meets_idx, int(core_ha >= MIN_CORE_HA))
    if not clusters.commitChanges():
        raise RuntimeError("Could not save calculated cluster attributes.")
    return clusters


def save_subset(layer, expression, output):
    remove_output(output)
    return processing.run(
        "native:extractbyexpression",
        {"INPUT": layer, "EXPRESSION": expression, "OUTPUT": str(output)},
    )["OUTPUT"]


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
piece_index = QgsSpatialIndex(pieces.getFeatures())
piece_features = list(pieces.getFeatures())
piece_geometries = {feature.id(): feature.geometry() for feature in piece_features}
piece_areas = {feature.id(): feature["piece_ha"] for feature in piece_features}

summary_rows = []
default_clusters = None
for gap_m in GAPS_M:
    clusters = make_clusters(pieces, piece_index, piece_geometries, piece_areas, gap_m)
    all_features = list(clusters.getFeatures())
    accepted = [feature for feature in all_features if feature["core_ha"] >= MIN_CORE_HA]
    summary_rows.append(
        {
            "maximum_interpatch_gap_m": gap_m,
            "cluster_count": len(all_features),
            "clusters_at_least_100ha": len(accepted),
            "core_hectares_at_least_100ha": f"{sum(feature['core_ha'] for feature in accepted):.2f}",
            "core_hectares_below_100ha": f"{sum(feature['core_ha'] for feature in all_features if feature['core_ha'] < MIN_CORE_HA):.2f}",
            "method_note": "Pieces are linked when their closest edges are no more than this distance apart; areas are unbuffered core-bog area.",
        }
    )
    if gap_m == DEFAULT_GAP_M:
        default_clusters = clone_with_new_ids(clusters)

with REPORT.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=summary_rows[0].keys())
    writer.writeheader()
    writer.writerows(summary_rows)

ALL_OUTPUT = ROOT / "data" / "processed" / "03_bog_clusters_all_gap250m.gpkg"
INCLUDED_OUTPUT = ROOT / "data" / "processed" / "03_bog_clusters_included_100ha_gap250m.gpkg"
AUDIT_OUTPUT = ROOT / "data" / "processed" / "03_bog_clusters_audit_below100ha_gap250m.gpkg"
remove_output(ALL_OUTPUT)
processing.run("native:savefeatures", {"INPUT": default_clusters, "OUTPUT": str(ALL_OUTPUT)})
save_subset(default_clusters, '"core_ha" >= 100', INCLUDED_OUTPUT)
save_subset(default_clusters, '"core_ha" < 100', AUDIT_OUTPUT)

for layer in list(PROJECT.mapLayers().values()):
    if layer.name().startswith("03 — Bog clusters"):
        PROJECT.removeMapLayer(layer.id())

included_layer = QgsVectorLayer(str(INCLUDED_OUTPUT), "03 — Bog clusters ≥100 ha (250 m link)", "ogr")
audit_layer = QgsVectorLayer(str(AUDIT_OUTPUT), "03 — Bog clusters <100 ha — audit (250 m link)", "ogr")
if not included_layer.isValid() or not audit_layer.isValid():
    raise RuntimeError("QGIS could not open one or more cluster output layers.")
style(included_layer, "#207567")
style(audit_layer, "#d2872c")
PROJECT.addMapLayer(audit_layer)
PROJECT.addMapLayer(included_layer)
iface.mapCanvas().setExtent(included_layer.extent())
iface.mapCanvas().refresh()

default_row = next(row for row in summary_rows if row["maximum_interpatch_gap_m"] == DEFAULT_GAP_M)
print("Connectivity-aware bog clusters complete")
print(f"Default link gap: {DEFAULT_GAP_M} m; minimum combined core area: {MIN_CORE_HA:.0f} ha")
print(f"Clusters: {default_row['cluster_count']}; included: {default_row['clusters_at_least_100ha']}")
print(f"Included core-bog area: {default_row['core_hectares_at_least_100ha']} ha")
print(f"Sensitivity report: {REPORT.relative_to(ROOT)}")
print("The orange audit layer is intentionally retained for later parcel/owner review.")
