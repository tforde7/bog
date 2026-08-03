"""Load the source layers for the public-land and designation screen.

This is a source-preview step only. It does not subtract or erase anything.
It lets us inspect the two LDA State Lands sources alongside NPWS SAC and NHA
boundaries before applying the exclusion policy.

LDA State Lands are shown as *known State land*, not definitive public/private
ownership. NHA data available in this project is the 2019 publication and is
explicitly labelled as such pending a newer downloadable national source.
"""

from pathlib import Path

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsDataSourceUri,
    QgsFillSymbol,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)


PROJECT = QgsProject.instance()
PROJECT_PATH = Path(PROJECT.fileName())
if not PROJECT_PATH.is_file():
    raise RuntimeError("Save the QGIS project before running this script.")

ROOT = PROJECT_PATH.parent
SAC_ZIP = ROOT / "data" / "raw" / "npws_sac_itm_2026_01.zip"
NHA_ZIP = ROOT / "data" / "raw" / "npws_nha_itm_2019_06.zip"
SAC_URI = f"/vsizip/{SAC_ZIP}/SAC_ITM_2026_01.shp"
NHA_URI = f"/vsizip/{NHA_ZIP}/NHA_ITM_2019_06.shp"

PRA_STATE_ASSETS_URL = (
    "https://services6.arcgis.com/Vx9miIJ7oMVDgH95/arcgis/rest/services/"
    "PRA_State_Assets_OpenData_Live/FeatureServer/0"
)
LDA_SOURCED_STATE_ASSETS_URL = (
    "https://services6.arcgis.com/Vx9miIJ7oMVDgH95/arcgis/rest/services/"
    "State_Assets_Sourced_by_LDA_OpenData_Live/FeatureServer/0"
)


def arcgis_layer(url, name):
    uri = QgsDataSourceUri()
    uri.setParam("url", url)
    layer = QgsVectorLayer(bytes(uri.encodedUri()).decode("utf-8"), name, "arcgisfeatureserver")
    if not layer.isValid():
        raise RuntimeError(f"QGIS could not load the LDA Feature Service: {name}")
    return layer


def zipped_layer(uri, name):
    layer = QgsVectorLayer(uri, name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"QGIS could not load the NPWS source: {name}")
    return layer


def style(layer, colour, opacity):
    symbol = QgsFillSymbol.createSimple(
        {"color": colour, "outline_color": colour, "outline_width": "0.25"}
    )
    symbol.setOpacity(opacity)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


if not SAC_ZIP.is_file() or not NHA_ZIP.is_file():
    raise FileNotFoundError("The existing NPWS SAC or NHA source archive is missing.")

for layer in list(PROJECT.mapLayers().values()):
    if layer.name().startswith("04a —"):
        PROJECT.removeMapLayer(layer.id())

pra = arcgis_layer(PRA_STATE_ASSETS_URL, "04a — LDA PRA State Assets (known State land)")
lda = arcgis_layer(LDA_SOURCED_STATE_ASSETS_URL, "04a — LDA-sourced State Assets (known State land)")
sac = zipped_layer(SAC_URI, "04a — NPWS SAC boundaries (2026)")
nha = zipped_layer(NHA_URI, "04a — NPWS NHA boundaries (2019 — verify update)")

style(pra, "#5f78a8", 0.45)
style(lda, "#3c5c91", 0.45)
style(sac, "#b35a44", 0.25)
style(nha, "#c7972a", 0.25)

PROJECT.addMapLayer(nha)
PROJECT.addMapLayer(sac)
PROJECT.addMapLayer(lda)
PROJECT.addMapLayer(pra)
iface.mapCanvas().refresh()

print("Loaded public-land and designation source layers")
print(f"PRA State Assets: {pra.featureCount()} features")
print(f"LDA-sourced State Assets: {lda.featureCount()} features")
print(f"SAC boundaries (2026): {sac.featureCount()} features")
print(f"NHA boundaries (2019): {nha.featureCount()} features")
print("No candidates have been excluded at this stage.")
