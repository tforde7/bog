"""Extract Tailte public cadastral titles that intersect screened bog core.

The Freehold and Leasehold Feature Services are queried remotely in 20 km ITM
tiles. Only returned titles that also pass a local exact-intersection check are
saved. This avoids downloading the national 3.2-million-title dataset.

Outputs are anonymous title-boundary screening layers. SP_ID is a stable public
spatial identifier, not an owner name and not a legal ownership confirmation.
"""

from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import math
import time

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsSpatialIndex,
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
TILE_SIZE_M = 20_000
PAGE_SIZE = 2_000
TIMEOUT_S = 90
# This is deliberately sequential and capped at four requests per second.
# It is a public service: do not increase this without the provider's consent.
REQUEST_INTERVAL_S = 0.25

SOURCES = (
    {
        "tenure": "freehold",
        "url": (
            "https://services-eu1.arcgis.com/FH5XCsx8rYXqnjF5/arcgis/rest/services/"
            "Cadastral_Parcels_Freehold/FeatureServer/12/query"
        ),
        "output": ROOT / "data" / "processed" / "04e_cadastral_freehold_bog_overlap.gpkg",
        "layer_name": "cadastral_freehold_bog_overlap",
        "display_name": "04e — Freehold titles intersecting screened bog core",
        "colour": "#6a51a3",
    },
    {
        "tenure": "leasehold",
        "url": (
            "https://services-eu1.arcgis.com/FH5XCsx8rYXqnjF5/arcgis/rest/services/"
            "Cadastral_Parcels_Leasehold/FeatureServer/13/query"
        ),
        "output": ROOT / "data" / "processed" / "04e_cadastral_leasehold_bog_overlap.gpkg",
        "layer_name": "cadastral_leasehold_bog_overlap",
        "display_name": "04e — Leasehold titles intersecting screened bog core",
        "colour": "#008c95",
    },
)


def project_layer(name):
    matches = [layer for layer in PROJECT.mapLayers().values() if layer.name() == name]
    if not matches:
        raise RuntimeError(f"Required source layer is not loaded: {name}. Run 04b first.")
    return matches[0]


def remove_output(path):
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            candidate.unlink()


def style(layer, colour):
    symbol = QgsFillSymbol.createSimple(
        {"color": colour, "outline_color": colour, "outline_width": "0.35"}
    )
    symbol.setOpacity(0.34)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def fetch_json(url, parameters):
    time.sleep(REQUEST_INTERVAL_S)
    request_url = f"{url}?{urlencode(parameters)}"
    request = Request(request_url, headers={"User-Agent": "QGIS bog-restoration screening"})
    try:
        with urlopen(request, timeout=TIMEOUT_S) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise RuntimeError(f"Tailte Feature Service request failed: {error}") from error
    if "error" in payload:
        details = payload["error"].get("message", str(payload["error"]))
        raise RuntimeError(f"Tailte Feature Service returned an error: {details}")
    return payload


def make_piece_index(layer):
    """Return an exact-geometry lookup and an R-tree with safe sequential IDs."""
    single_parts = processing.run(
        "native:multiparttosingleparts", {"INPUT": layer, "OUTPUT": "memory:"}
    )["OUTPUT"]
    repaired = processing.run(
        "native:fixgeometries", {"INPUT": single_parts, "METHOD": 1, "OUTPUT": "memory:"}
    )["OUTPUT"]
    index = QgsSpatialIndex()
    geometries = {}
    for piece_id, feature in enumerate(repaired.getFeatures()):
        geometry = feature.geometry()
        if geometry.isNull() or geometry.isEmpty():
            continue
        geometries[piece_id] = geometry
        index.addFeature(piece_id, geometry.boundingBox())
    if not geometries:
        raise RuntimeError("The screened bog layer has no usable geometries.")
    return geometries, index


def tile_keys(geometries):
    keys = set()
    for geometry in geometries.values():
        bounds = geometry.boundingBox()
        for x_index in range(
            math.floor(bounds.xMinimum() / TILE_SIZE_M),
            math.floor(bounds.xMaximum() / TILE_SIZE_M) + 1,
        ):
            for y_index in range(
                math.floor(bounds.yMinimum() / TILE_SIZE_M),
                math.floor(bounds.yMaximum() / TILE_SIZE_M) + 1,
            ):
                keys.add((x_index, y_index))
    return sorted(keys)


def tile_rectangle(key):
    x_index, y_index = key
    xmin = x_index * TILE_SIZE_M
    ymin = y_index * TILE_SIZE_M
    return QgsRectangle(xmin, ymin, xmin + TILE_SIZE_M, ymin + TILE_SIZE_M)


def intersects_screened_bog(title_geometry, bog_index, bog_geometries):
    for piece_id in bog_index.intersects(title_geometry.boundingBox()):
        if title_geometry.intersects(bog_geometries[piece_id]):
            return True
    return False


def extract_titles(source, tiles, bog_index, bog_geometries):
    """Query one tenure service and return a memory layer of exact matches."""
    layer = QgsVectorLayer("MultiPolygon?crs=EPSG:2157", source["display_name"], "memory")
    if not layer.isValid():
        raise RuntimeError("Could not create the temporary cadastral-title layer.")
    provider = layer.dataProvider()
    provider.addAttributes(
        [
            QgsField("title_key", QVariant.String),
            QgsField("sp_id", QVariant.LongLong),
            QgsField("tenure", QVariant.String),
            QgsField("county", QVariant.String),
            QgsField("source_oid", QVariant.LongLong),
        ]
    )
    layer.updateFields()

    seen_keys = set()
    batch = []
    queried = 0

    def flush_batch():
        nonlocal batch
        if not batch:
            return
        success, _ = provider.addFeatures(batch)
        if not success:
            raise RuntimeError(
                "Could not add a cadastral-title batch to the temporary layer: "
                f"{provider.lastError()}"
            )
        batch = []

    for tile_number, key in enumerate(tiles, start=1):
        rectangle = tile_rectangle(key)
        geometry = ",".join(
            str(value)
            for value in (
                rectangle.xMinimum(),
                rectangle.yMinimum(),
                rectangle.xMaximum(),
                rectangle.yMaximum(),
            )
        )
        offset = 0
        while True:
            payload = fetch_json(
                source["url"],
                {
                    "where": "1=1",
                    "geometry": geometry,
                    "geometryType": "esriGeometryEnvelope",
                    "inSR": "2157",
                    "spatialRel": "esriSpatialRelIntersects",
                    "outFields": "OBJECTID,SP_ID,COUNTY_NAM",
                    "returnGeometry": "true",
                    "returnZ": "false",
                    "returnM": "false",
                    "outSR": "2157",
                    "resultOffset": str(offset),
                    "resultRecordCount": str(PAGE_SIZE),
                    "f": "geojson",
                },
            )
            features = payload.get("features", [])
            queried += len(features)
            for remote_feature in features:
                properties = remote_feature.get("properties", {})
                source_oid = properties.get("OBJECTID", remote_feature.get("id"))
                sp_id = properties.get("SP_ID")
                if source_oid is None:
                    continue
                identifier = sp_id if sp_id is not None else source_oid
                title_key = f"{source['tenure']}:{identifier}"
                if title_key in seen_keys:
                    continue
                geojson_geometry = remote_feature.get("geometry")
                if not geojson_geometry:
                    continue
                title_geometry = QgsGeometry.fromGeoJson(json.dumps(geojson_geometry))
                if title_geometry.isNull() or title_geometry.isEmpty():
                    continue
                if not intersects_screened_bog(title_geometry, bog_index, bog_geometries):
                    continue
                seen_keys.add(title_key)
                copied = QgsFeature(layer.fields())
                copied.setGeometry(title_geometry)
                copied.setAttributes(
                    [
                        title_key,
                        sp_id,
                        source["tenure"],
                        properties.get("COUNTY_NAM"),
                        source_oid,
                    ]
                )
                batch.append(copied)
                if len(batch) >= PAGE_SIZE:
                    flush_batch()
            if len(features) < PAGE_SIZE:
                break
            offset += len(features)
        if tile_number % 25 == 0 or tile_number == len(tiles):
            print(
                f"{source['tenure'].title()}: queried {tile_number:,}/{len(tiles):,} tiles; "
                f"server titles read: {queried:,}; exact matches: {len(seen_keys):,}"
            )
    flush_batch()
    return layer, queried


def save_and_show(layer, source):
    for existing in list(PROJECT.mapLayers().values()):
        if existing.name() == source["display_name"]:
            PROJECT.removeMapLayer(existing.id())
    remove_output(source["output"])
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = source["layer_name"]
    error, message, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, str(source["output"]), PROJECT.transformContext(), options
    )
    if error != QgsVectorFileWriter.NoError:
        raise RuntimeError(f"Could not write {source['tenure']} title subset: {message}")
    output_layer = QgsVectorLayer(
        f"{source['output']}|layername={source['layer_name']}",
        source["display_name"],
        "ogr",
    )
    if not output_layer.isValid():
        raise RuntimeError(f"QGIS could not reopen the {source['tenure']} title subset.")
    style(output_layer, source["colour"])
    PROJECT.addMapLayer(output_layer)
    return output_layer


screened_bog = project_layer(SOURCE_NAME)
bog_geometries, bog_index = make_piece_index(screened_bog)
tiles = tile_keys(bog_geometries)
print(f"Screened bog pieces used for local exact checks: {len(bog_geometries):,}")
print(f"Remote query tiles ({TILE_SIZE_M / 1000:.0f} km): {len(tiles):,}")

outputs = []
for source in SOURCES:
    print(f"Starting {source['tenure']} title extraction")
    matches, read_count = extract_titles(source, tiles, bog_index, bog_geometries)
    output_layer = save_and_show(matches, source)
    outputs.append(output_layer)
    print(
        f"{source['tenure'].title()} titles saved: {output_layer.featureCount():,} "
        f"(remote records read: {read_count:,})"
    )

iface.mapCanvas().setExtent(screened_bog.extent())
iface.mapCanvas().refresh()
print("Cadastral-title extraction complete")
print(f"Freehold titles: {outputs[0].featureCount():,}")
print(f"Leasehold titles: {outputs[1].featureCount():,}")
print("These are anonymous title-boundary screening layers, not owner-identification data.")
