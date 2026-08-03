"""Create a 30 m percent-slope raster for the retained-title study extent.

Run from the QGIS Python Console after 05b DEM preparation. The input is the
projected Copernicus GLO-30 DSM in EPSG:2157. The output expresses slope as
percent using a 1:1 vertical/horizontal unit ratio and the Zevenbergen–Thorne
formula. No title eligibility filtering occurs in this script.
"""

from pathlib import Path
import os

from qgis.core import (
    QgsProject,
    QgsRasterBandStats,
    QgsRasterLayer,
)
import processing


PROJECT_DIR = Path("/Users/tforde/projects/bog")
INPUT_PATH = PROJECT_DIR / "data" / "processed" / "05b_copernicus_glo30_itm_397_titles.tif"
OUTPUT_PATH = PROJECT_DIR / "data" / "processed" / "05c_copernicus_glo30_slope_percent.tif"
PARTIAL_PATH = PROJECT_DIR / "data" / "processed" / "05c_copernicus_glo30_slope_percent_IN_PROGRESS.tif"
DISPLAY_NAME = "05c — Copernicus GLO-30 slope (%)"


def remove_loaded_path(path):
    project = QgsProject.instance()
    for layer in list(project.mapLayers().values()):
        if str(path) in layer.source() or layer.name() == DISPLAY_NAME:
            project.removeMapLayer(layer.id())


if not INPUT_PATH.is_file():
    raise FileNotFoundError(f"Projected Copernicus DEM is missing: {INPUT_PATH}")

dem = QgsRasterLayer(str(INPUT_PATH), "05b projected Copernicus GLO-30 DSM")
if not dem.isValid():
    raise RuntimeError("Could not open the projected Copernicus DEM.")
if dem.crs().authid() != "EPSG:2157":
    raise RuntimeError(f"Unexpected DEM CRS: {dem.crs().authid()}")
if dem.bandCount() != 1:
    raise RuntimeError(f"Expected one DEM band; found {dem.bandCount()}.")

remove_loaded_path(OUTPUT_PATH)
remove_loaded_path(PARTIAL_PATH)
result = processing.run(
    "gdal:slope",
    {
        "INPUT": dem,
        "BAND": 1,
        "SCALE": 1.0,
        "AS_PERCENT": True,
        "COMPUTE_EDGES": True,
        "ZEVENBERGEN": True,
        "CREATION_OPTIONS": "TILED=YES|COMPRESS=DEFLATE|PREDICTOR=3|BIGTIFF=IF_SAFER",
        "EXTRA": "",
        "OUTPUT": str(PARTIAL_PATH),
    },
)
written_path = Path(result["OUTPUT"])
if not written_path.is_file():
    raise RuntimeError(f"Slope algorithm did not create its reported output: {written_path}")

partial = QgsRasterLayer(str(PARTIAL_PATH), "validated temporary percent slope")
if not partial.isValid():
    raise RuntimeError("Temporary percent-slope raster could not be reopened.")
if partial.crs().authid() != "EPSG:2157":
    raise RuntimeError(f"Unexpected slope CRS: {partial.crs().authid()}")
if partial.width() != dem.width() or partial.height() != dem.height():
    raise RuntimeError(
        f"Slope dimensions differ from DEM: slope {partial.width()}×{partial.height()}, "
        f"DEM {dem.width()}×{dem.height()}."
    )

stats = partial.dataProvider().bandStatistics(1, QgsRasterBandStats.All)
if stats.minimumValue < 0:
    raise RuntimeError(f"Unexpected negative valid slope value: {stats.minimumValue}")
if stats.maximumValue <= 0:
    raise RuntimeError(f"Unexpected slope maximum: {stats.maximumValue}")

partial = None
os.replace(PARTIAL_PATH, OUTPUT_PATH)
partial_aux = Path(f"{PARTIAL_PATH}.aux.xml")
if partial_aux.is_file():
    os.replace(partial_aux, Path(f"{OUTPUT_PATH}.aux.xml"))

saved = QgsRasterLayer(str(OUTPUT_PATH), DISPLAY_NAME)
if not saved.isValid():
    raise RuntimeError("Completed percent-slope raster could not be reopened.")
project = QgsProject.instance()
project.addMapLayer(saved)
project.layerTreeRoot().findLayer(saved.id()).setItemVisibilityChecked(True)
iface.mapCanvas().setExtent(saved.extent())
iface.mapCanvas().refresh()

print("05c Copernicus percent-slope raster complete")
print("Method: Zevenbergen–Thorne")
print("Units: percent slope")
print("Vertical/horizontal scale: 1.0 (metres/metres)")
print(f"Raster size: {saved.width():,} × {saved.height():,} cells")
print(f"Cell size: {saved.rasterUnitsPerPixelX():.2f} × {saved.rasterUnitsPerPixelY():.2f} m")
print(f"Slope range: {stats.minimumValue:.4f}–{stats.maximumValue:.4f}%")
print(f"Mean slope: {stats.mean:.4f}%")
print(f"Output: {OUTPUT_PATH.relative_to(PROJECT_DIR)}")
