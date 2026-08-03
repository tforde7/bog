"""Extract only cadastral titles that intersect the screened bog-core layer.

Inputs (not modified):
  * 04b — Bog core outside known State land and SAC/NHA (QGIS memory layer)
  * data/raw/Cadastral_Parcels_Freehold_*.gpkg
  * data/raw/Cadastral_Parcels_Leasehold_*.gpkg

Outputs:
  * data/processed/04e_cadastral_freehold_bog_overlap.gpkg
  * data/processed/04e_cadastral_leasehold_bog_overlap.gpkg

This is an extraction and preview step. It does not establish private ownership,
and it does not subtract, score, or exclude any title.
"""

from pathlib import Path
import glob
import time

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsFeature,
    QgsFeatureRequest,
    QgsFeatureSink,
    QgsFields,
    QgsField,
    QgsGeometry,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
    QgsFillSymbol,
    QgsLineSymbol,
)


PROJECT_DIR = Path("/Users/tforde/projects/bog")
RAW_DIR = PROJECT_DIR / "data" / "raw"
PROCESSED_DIR = PROJECT_DIR / "data" / "processed"
SCREENED_LAYER_NAME = "04b — Bog core outside known State land and SAC/NHA"

SOURCES = [
    {
        "kind": "freehold",
        "raw_pattern": "Cadastral_Parcels_Freehold_*.gpkg",
        "raw_layer": "Cadastral_Parcels_Freehold",
        "output": PROCESSED_DIR / "04e_cadastral_freehold_bog_overlap.gpkg",
        "output_layer": "cadastral_freehold_bog_overlap",
        "display_name": "04e — Freehold titles intersecting screened bog",
        "color": "#7c3aed",
    },
    {
        "kind": "leasehold",
        "raw_pattern": "Cadastral_Parcels_Leasehold_*.gpkg",
        "raw_layer": "Cadastral_Parcels_Leasehold",
        "output": PROCESSED_DIR / "04e_cadastral_leasehold_bog_overlap.gpkg",
        "output_layer": "cadastral_leasehold_bog_overlap",
        "display_name": "04e — Leasehold titles intersecting screened bog",
        "color": "#0f766e",
    },
]


def one_file(pattern):
    matches = sorted(glob.glob(str(RAW_DIR / pattern)))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one raw file matching {pattern!r}; found {len(matches)}: {matches}"
        )
    return Path(matches[0])


def layer_named(name):
    matches = [layer for layer in QgsProject.instance().mapLayers().values() if layer.name() == name]
    if len(matches) != 1:
        available = "\n  - ".join(layer.name() for layer in QgsProject.instance().mapLayers().values())
        raise RuntimeError(
            f"Required QGIS layer not found exactly once: {name!r}.\nAvailable layers:\n  - {available}"
        )
    return matches[0]


def usable_bog_geometries(screened_layer):
    """Return valid screened-bog geometries with independent IDs."""
    geometries = {}
    next_id = 1

    for feature in screened_layer.getFeatures():
        geometry = feature.geometry()
        if geometry.isNull() or geometry.isEmpty():
            continue
        geometry = QgsGeometry(geometry)
        if not geometry.isGeosValid():
            geometry = geometry.makeValid()
        if geometry.isNull() or geometry.isEmpty():
            continue

        geometries[next_id] = geometry
        next_id += 1

    if not geometries:
        raise RuntimeError("The screened bog layer contained no usable geometries.")
    return geometries


def output_fields():
    fields = QgsFields()
    fields.append(QgsField("source_fid", QVariant.LongLong))
    fields.append(QgsField("objectid", QVariant.LongLong))
    fields.append(QgsField("sp_id", QVariant.Double))
    county = QgsField("county_nam", QVariant.String)
    county.setLength(0)
    fields.append(county)
    return fields


def remove_project_layer_by_source(output_path):
    target = str(output_path)
    for layer in list(QgsProject.instance().mapLayers().values()):
        if target in layer.source():
            QgsProject.instance().removeMapLayer(layer.id())


def extract_source(config, bog_geometries):
    raw_path = one_file(config["raw_pattern"])
    source_uri = f"{raw_path}|layername={config['raw_layer']}"
    source = QgsVectorLayer(source_uri, f"raw {config['kind']}", "ogr")
    if not source.isValid():
        raise RuntimeError(f"Could not open raw {config['kind']} GeoPackage: {raw_path}")
    if source.crs().authid() != "EPSG:2157":
        raise RuntimeError(f"Unexpected CRS for {raw_path.name}: {source.crs().authid()}")

    for required_field in ("OBJECTID", "SP_ID", "COUNTY_NAM"):
        if source.fields().indexFromName(required_field) < 0:
            raise RuntimeError(f"{raw_path.name} is missing expected field {required_field!r}.")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    remove_project_layer_by_source(config["output"])

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = config["output_layer"]
    options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
    writer = QgsVectorFileWriter.create(
        str(config["output"]),
        output_fields(),
        QgsWkbTypes.flatType(source.wkbType()),
        source.crs(),
        QgsProject.instance().transformContext(),
        options,
    )
    if writer.hasError() != QgsVectorFileWriter.NoError:
        raise RuntimeError(f"Could not create {config['output'].name}: {writer.errorMessage()}")

    source_fields = source.fields()
    requested_names = ["OBJECTID", "SP_ID", "COUNTY_NAM"]
    seen_source_fids = set()
    bbox_candidates = 0
    written = 0
    checked_pieces = 0
    started = time.monotonic()

    for bog_id, bog_geometry in bog_geometries.items():
        request = QgsFeatureRequest()
        request.setFilterRect(bog_geometry.boundingBox())
        request.setSubsetOfAttributes(requested_names, source_fields)

        for title in source.getFeatures(request):
            source_fid = title.id()
            if source_fid in seen_source_fids:
                continue
            bbox_candidates += 1
            title_geometry = title.geometry()
            if title_geometry.isNull() or title_geometry.isEmpty():
                continue
            if not title_geometry.intersects(bog_geometry):
                continue

            seen_source_fids.add(source_fid)
            output_feature = QgsFeature(output_fields())
            output_feature.setGeometry(title_geometry)
            output_feature.setAttributes(
                [
                    source_fid,
                    title["OBJECTID"],
                    title["SP_ID"],
                    title["COUNTY_NAM"],
                ]
            )
            if not writer.addFeature(output_feature, QgsFeatureSink.FastInsert):
                raise RuntimeError(
                    f"Could not write a {config['kind']} title: {writer.lastError()}"
                )
            written += 1

        checked_pieces += 1
        if checked_pieces % 500 == 0 or checked_pieces == len(bog_geometries):
            elapsed_min = (time.monotonic() - started) / 60
            print(
                f"{config['kind'].title()}: checked {checked_pieces:,}/{len(bog_geometries):,} bog pieces; "
                f"titles written: {written:,}; elapsed: {elapsed_min:.1f} min"
            )

    writer.flushBuffer()
    if writer.hasError() != QgsVectorFileWriter.NoError:
        raise RuntimeError(f"Error finalising {config['output'].name}: {writer.lastError()}")
    del writer

    extracted_uri = f"{config['output']}|layername={config['output_layer']}"
    extracted = QgsVectorLayer(extracted_uri, config["display_name"], "ogr")
    if not extracted.isValid():
        raise RuntimeError(f"The output could not be reopened: {config['output']}")
    if extracted.featureCount() != written:
        raise RuntimeError(
            f"Output feature-count mismatch for {config['kind']}: wrote {written:,}, reopened {extracted.featureCount():,}."
        )

    if config["kind"] == "freehold":
        symbol = QgsFillSymbol.createSimple(
            {"color": config["color"], "outline_color": config["color"], "outline_width": "0.35"}
        )
        symbol.setOpacity(0.25)
    else:
        symbol = QgsLineSymbol.createSimple({"line_color": config["color"], "line_width": "0.7"})
        extracted.renderer().setSymbol(symbol)
    if config["kind"] == "freehold":
        extracted.renderer().setSymbol(symbol)

    QgsProject.instance().addMapLayer(extracted)
    return extracted, written, bbox_candidates, raw_path


screened_bog = layer_named(SCREENED_LAYER_NAME)
if screened_bog.crs().authid() != "EPSG:2157":
    raise RuntimeError(f"Unexpected CRS for screened bog layer: {screened_bog.crs().authid()}")

print("Building spatial index for screened bog-core pieces...")
bog_geometries = usable_bog_geometries(screened_bog)
print(f"Screened bog pieces to query: {len(bog_geometries):,}")

results = []
for source_config in SOURCES:
    print(f"Extracting {source_config['kind']} titles locally (no Tailte web requests)...")
    results.append((source_config["kind"],) + extract_source(source_config, bog_geometries)[1:])

canvas = iface.mapCanvas()
canvas.setExtent(screened_bog.extent())
canvas.refresh()

print("Local cadastral-title extraction complete")
for kind, count, candidates, raw_path in results:
    print(f"{kind.title()} titles intersecting screened bog: {count:,} (bbox candidates checked: {candidates:,})")
    print(f"Raw source: {raw_path}")
print("Outputs: data/processed/04e_cadastral_freehold_bog_overlap.gpkg")
print("         data/processed/04e_cadastral_leasehold_bog_overlap.gpkg")
print("These are title-boundary screening layers, not proof of owner identity or private ownership.")
