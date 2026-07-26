"""QA comparison of NPWS active raised bog and the 2024 peat map.

This corrected version repairs invalid geometries in memory before overlay.
Raw sources and the core evidence layer are never edited.
"""

import csv
from pathlib import Path

from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsVectorLayer
import processing


PROJECT = QgsProject.instance()
PROJECT_PATH = Path(PROJECT.fileName())
if not PROJECT_PATH.is_file():
    raise RuntimeError("Save the QGIS project before running this script.")

ROOT = PROJECT_PATH.parent
NPWS_ZIP = ROOT / "data" / "raw" / "npws_habitat_7110.zip"
CORE = ROOT / "data" / "processed" / "01_bog_core_evidence.gpkg"
NPWS_OUTPUT = ROOT / "data" / "processed" / "02_npws_7110_itm.gpkg"
OVERLAP_OUTPUT = ROOT / "data" / "processed" / "02_npws_7110_overlap_2024_raised.gpkg"
OUTSIDE_OUTPUT = ROOT / "data" / "processed" / "02_npws_7110_outside_2024_raised.gpkg"
REPORT = ROOT / "docs" / "qa_npws_7110_alignment.csv"
NPWS_MEMBERS = (
    "7110_Raised_Bog_(Active)/AR1719_7110_1of2.shp",
    "7110_Raised_Bog_(Active)/AR1719_7110_2of2.shp",
)


def remove_output(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            candidate.unlink()


def fixed(source):
    """Return a repaired temporary layer without changing the input."""
    return processing.run(
        "native:fixgeometries", {"INPUT": source, "METHOD": 1, "OUTPUT": "memory:"}
    )["OUTPUT"]


def area_hectares(source: str) -> float:
    layer = QgsVectorLayer(source, "temporary area check", "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Could not open QA output: {source}")
    return sum(feature.geometry().area() for feature in layer.getFeatures()) / 10_000


if not NPWS_ZIP.is_file() or not CORE.is_file():
    raise FileNotFoundError("Required NPWS or core-evidence source file is missing.")

npws_layers = []
for member in NPWS_MEMBERS:
    layer = QgsVectorLayer(f"/vsizip/{NPWS_ZIP}/{member}", Path(member).stem, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Could not read NPWS source layer: {member}")
    npws_layers.append(layer)

core = QgsVectorLayer(str(CORE), "bog core evidence", "ogr")
if not core.isValid():
    raise RuntimeError("Could not read the processed bog-core evidence layer.")

for path in (NPWS_OUTPUT, OVERLAP_OUTPUT, OUTSIDE_OUTPUT):
    remove_output(path)

merged = processing.run(
    "native:mergevectorlayers",
    {"LAYERS": npws_layers, "CRS": QgsCoordinateReferenceSystem("EPSG:2157"), "OUTPUT": "memory:"},
)["OUTPUT"]
npws_union = fixed(
    processing.run("native:dissolve", {"INPUT": fixed(merged), "FIELD": [], "OUTPUT": "memory:"})[
        "OUTPUT"
    ]
)
npws_itm = processing.run(
    "native:reprojectlayer",
    {
        "INPUT": npws_union,
        "TARGET_CRS": QgsCoordinateReferenceSystem("EPSG:2157"),
        "OPERATION": "",
        "OUTPUT": str(NPWS_OUTPUT),
    },
)["OUTPUT"]

raised_2024 = fixed(
    processing.run(
        "native:extractbyexpression",
        {"INPUT": core, "EXPRESSION": "\"descrip\" = 'Raised bogs'", "OUTPUT": "memory:"},
    )["OUTPUT"]
)
overlap = processing.run(
    "native:intersection",
    {"INPUT": npws_itm, "OVERLAY": raised_2024, "OUTPUT": str(OVERLAP_OUTPUT)},
)["OUTPUT"]
outside = processing.run(
    "native:difference",
    {"INPUT": npws_itm, "OVERLAY": raised_2024, "OUTPUT": str(OUTSIDE_OUTPUT)},
)["OUTPUT"]

total_ha = area_hectares(npws_itm)
overlap_ha = area_hectares(overlap)
outside_ha = area_hectares(outside)
overlap_pct = (overlap_ha / total_ha * 100) if total_ha else 0.0
outside_pct = (outside_ha / total_ha * 100) if total_ha else 0.0

with REPORT.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(
        handle, fieldnames=("metric", "hectares", "percent_of_npws_active_raised_bog", "notes")
    )
    writer.writeheader()
    writer.writerows(
        (
            {"metric": "NPWS active raised bog total", "hectares": f"{total_ha:.2f}", "percent_of_npws_active_raised_bog": "100.00", "notes": "Dissolved, repaired union of both NPWS 7110 source parts."},
            {"metric": "Overlap with 2024 Raised bogs class", "hectares": f"{overlap_ha:.2f}", "percent_of_npws_active_raised_bog": f"{overlap_pct:.2f}", "notes": "Agreement indicator only; not an eligibility rule."},
            {"metric": "NPWS active raised bog outside 2024 Raised bogs class", "hectares": f"{outside_ha:.2f}", "percent_of_npws_active_raised_bog": f"{outside_pct:.2f}", "notes": "Retained as independent NPWS evidence; not excluded."},
        )
    )

print("NPWS 7110 QA complete")
print(f"NPWS total: {total_ha:.2f} ha")
print(f"Overlap with 2024 Raised bogs: {overlap_ha:.2f} ha ({overlap_pct:.2f}%)")
print(f"Outside 2024 Raised bogs: {outside_ha:.2f} ha ({outside_pct:.2f}%)")
print(f"Report: {REPORT.relative_to(ROOT)}")
