"""Create connectivity-aware bog clusters and apply a 20 ha cluster threshold.

Run from the QGIS Python Console.  It does not change the source peat layer.

Method
------
Each separate piece of the 2024 bog-core layer is buffered by half of a chosen
gap, dissolved, and treated as one cluster.  Therefore pieces no more than the
chosen gap apart are grouped.  The cluster's `core_ha` is the sum of the
*unbuffered* bog-core pieces it contains: the temporary buffer is used only to
identify connectivity, never to inflate its peat area.

The script tests 100 m, 250 m and 500 m gaps.  It writes a CSV comparison and
loads the 250 m result for review.  Clusters under 20 ha are preserved in an
audit layer; no source features are deleted.
"""

import csv
from pathlib import Path

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsProject,
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
MIN_CORE_HA = 20.0


def remove_output(path: Path) -> None:
    """Remove only an output that this script itself recreates."""
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


def make_clusters(pieces, gap_m):
    """Return clusters with a stable-in-run ID and summed original core area."""
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
    clusters = add_field(buffered, "cluster_id", 1, "$id + 1", length=10, precision=0)
    clusters = add_field(clusters, "link_gap_m", 0, str(float(gap_m)), length=10, precision=1)
    clusters = processing.run(
        "native:joinattributesbylocationsummary",
        {
            "INPUT": clusters,
            "JOIN": pieces,
            "PREDICATE": [0],  # intersects
            "JOIN_FIELDS": ["piece_ha"],
            "SUMMARIES": [5],  # sum
            "DISCARD_NONMATCHING": False,
            "OUTPUT": "memory:",
        },
    )["OUTPUT"]
    # QGIS names the location-summary field piece_ha_sum.
    clusters = add_field(clusters, "core_ha", 0, 'coalesce("piece_ha_sum", 0)', length=20, precision=3)
    clusters = add_field(clusters, "meets_20ha", 1, '"core_ha" >= 20', length=1, precision=0)
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
    layer.setRenderer(layer.renderer().create(symbol))


if not CORE.is_file():
    raise FileNotFoundError(f"Bog-core evidence layer is missing: {CORE}")

core = QgsVectorLayer(str(CORE), "bog core evidence", "ogr")
if not core.isValid():
    raise RuntimeError("QGIS could not open the bog-core evidence layer.")

# Separate multipart geometries so the process starts with actual individual pieces.
pieces = processing.run(
    "native:multiparttosingleparts", {"INPUT": core, "OUTPUT": "memory:"}
)["OUTPUT"]
pieces = processing.run(
    "native:fixgeometries", {"INPUT": pieces, "METHOD": 1, "OUTPUT": "memory:"}
)["OUTPUT"]
pieces = add_field(pieces, "piece_ha", 0, "$area / 10000.0", length=20, precision=3)

summary_rows = []
default_clusters = None
for gap_m in GAPS_M:
    clusters = make_clusters(pieces, gap_m)
    all_features = list(clusters.getFeatures())
    accepted = [f for f in all_features if f["core_ha"] >= MIN_CORE_HA]
    summary_rows.append(
        {
            "maximum_interpatch_gap_m": gap_m,
            "cluster_count": len(all_features),
            "clusters_at_least_20ha": len(accepted),
            "core_hectares_at_least_20ha": f"{sum(f['core_ha'] for f in accepted):.2f}",
            "core_hectares_below_20ha": f"{sum(f['core_ha'] for f in all_features if f['core_ha'] < MIN_CORE_HA):.2f}",
            "method_note": "Pieces are linked when their closest edges are no more than this distance apart; areas are unbuffered core-bog area.",
        }
    )
    if gap_m == DEFAULT_GAP_M:
        default_clusters = clusters

with REPORT.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=summary_rows[0].keys())
    writer.writeheader()
    writer.writerows(summary_rows)

ALL_OUTPUT = ROOT / "data" / "processed" / "03_bog_clusters_all_gap250m.gpkg"
INCLUDED_OUTPUT = ROOT / "data" / "processed" / "03_bog_clusters_included_20ha_gap250m.gpkg"
AUDIT_OUTPUT = ROOT / "data" / "processed" / "03_bog_clusters_audit_below20ha_gap250m.gpkg"

remove_output(ALL_OUTPUT)
processing.run("native:savefeatures", {"INPUT": default_clusters, "OUTPUT": str(ALL_OUTPUT)})
save_subset(default_clusters, '"core_ha" >= 20', INCLUDED_OUTPUT)
save_subset(default_clusters, '"core_ha" < 20', AUDIT_OUTPUT)

for layer in list(PROJECT.mapLayers().values()):
    if layer.name().startswith("03 — Bog clusters"):
        PROJECT.removeMapLayer(layer.id())

included_layer = QgsVectorLayer(str(INCLUDED_OUTPUT), "03 — Bog clusters ≥20 ha (250 m link)", "ogr")
audit_layer = QgsVectorLayer(str(AUDIT_OUTPUT), "03 — Bog clusters <20 ha — audit (250 m link)", "ogr")
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
print(f"Clusters: {default_row['cluster_count']}; included: {default_row['clusters_at_least_20ha']}")
print(f"Included core-bog area: {default_row['core_hectares_at_least_20ha']} ha")
print(f"Sensitivity report: {REPORT.relative_to(ROOT)}")
print("The orange audit layer is intentionally retained for later parcel/owner review.")
