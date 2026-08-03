"""Incremental local extraction of cadastral titles overlapping screened bog.

Run one tenure at a time from the QGIS Python Console by setting
CADASTRAL_DATASET to "freehold" or "leasehold" before executing this file.

The runner processes two screened-bog pieces per Qt event-loop turn. QGIS can
therefore repaint and respond between batches. It writes to an in-progress
GeoPackage and promotes it to the final output only after successful completion.
"""

from pathlib import Path
import csv
from datetime import datetime, timezone
import glob
import os
import time

from qgis.PyQt.QtCore import QTimer, QVariant
from qgis.core import (
    Qgis,
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
QUERY_PIECES = PROCESSED_DIR / "04b1_screened_bog_query_pieces.gpkg"
QUERY_PIECES_LAYER = "screened_bog_query_pieces"
TIMING_LOG = PROJECT_DIR / "docs" / "04f_cadastral_extraction_timings.csv"
DATASET = globals().get("CADASTRAL_DATASET", "freehold").lower()
PIECES_PER_TICK = 2
LOG_EVERY_PIECES = 25
LOG_EVERY_SECONDS = 30

CONFIGS = {
    "freehold": {
        "raw_pattern": "Cadastral_Parcels_Freehold_*.gpkg",
        "raw_layer": "Cadastral_Parcels_Freehold",
        "output": PROCESSED_DIR / "04f_cadastral_freehold_bog_overlap.gpkg",
        "output_layer": "cadastral_freehold_bog_overlap",
        "display_name": "04f — Freehold titles intersecting screened bog",
        "color": "#7c3aed",
    },
    "leasehold": {
        "raw_pattern": "Cadastral_Parcels_Leasehold_*.gpkg",
        "raw_layer": "Cadastral_Parcels_Leasehold",
        "output": PROCESSED_DIR / "04f_cadastral_leasehold_bog_overlap.gpkg",
        "output_layer": "cadastral_leasehold_bog_overlap",
        "display_name": "04f — Leasehold titles intersecting screened bog",
        "color": "#0f766e",
    },
}


def format_duration(seconds):
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m {seconds:02d}s"
    return f"{minutes:d}m {seconds:02d}s"


def one_raw_file(pattern):
    matches = sorted(glob.glob(str(RAW_DIR / pattern)))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one raw GeoPackage matching {pattern!r}; found: {matches}")
    return Path(matches[0])


def clean_output_layers(output_path):
    for layer in list(QgsProject.instance().mapLayers().values()):
        if str(output_path) in layer.source():
            QgsProject.instance().removeMapLayer(layer.id())


def extraction_fields():
    fields = QgsFields()
    fields.append(QgsField("source_fid", QVariant.LongLong))
    fields.append(QgsField("objectid", QVariant.LongLong))
    fields.append(QgsField("sp_id", QVariant.Double))
    county = QgsField("county_nam", QVariant.String)
    county.setLength(0)
    fields.append(county)
    return fields


class IncrementalTitleExtractor:
    def __init__(self, config, screened_bog):
        self.config = config
        self.screened_bog = screened_bog
        self.cancelled = False
        self.finished = False
        self.started = time.monotonic()
        self.last_log = self.started
        self.piece_number = 0
        self.written = 0
        self.bbox_candidates = 0
        self.seen_source_fids = set()
        self.fields = extraction_fields()
        self.timing_recorded = False

        self.raw_path = one_raw_file(config["raw_pattern"])
        self.source = QgsVectorLayer(
            f"{self.raw_path}|layername={config['raw_layer']}",
            f"raw {DATASET}",
            "ogr",
        )
        if not self.source.isValid():
            raise RuntimeError(f"Could not open raw GeoPackage: {self.raw_path}")
        if self.source.crs().authid() != "EPSG:2157":
            raise RuntimeError(f"Unexpected source CRS: {self.source.crs().authid()}")
        self.source_count = self.source.featureCount()
        for field_name in ("OBJECTID", "SP_ID", "COUNTY_NAM"):
            if self.source.fields().indexFromName(field_name) < 0:
                raise RuntimeError(f"Raw source is missing expected field {field_name!r}")

        self.bog_geometries = []
        for feature in screened_bog.getFeatures():
            geometry = feature.geometry()
            if geometry.isNull() or geometry.isEmpty():
                continue
            self.bog_geometries.append(QgsGeometry(geometry))
        if not self.bog_geometries:
            raise RuntimeError("The screened bog layer contained no usable geometry.")

        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        self.output_path = config["output"]
        self.partial_path = self.output_path.with_name(self.output_path.stem + "_IN_PROGRESS.gpkg")
        clean_output_layers(self.output_path)
        clean_output_layers(self.partial_path)

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = config["output_layer"]
        options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
        self.writer = QgsVectorFileWriter.create(
            str(self.partial_path),
            self.fields,
            QgsWkbTypes.flatType(self.source.wkbType()),
            self.source.crs(),
            QgsProject.instance().transformContext(),
            options,
        )
        if self.writer.hasError() != QgsVectorFileWriter.NoError:
            raise RuntimeError(f"Could not create in-progress GeoPackage: {self.writer.errorMessage()}")

    def status(self, message, level=Qgis.Info):
        text = f"{DATASET.title()}: {message}"
        print(text)
        iface.messageBar().pushMessage("Cadastral extraction", text, level=level, duration=8)

    def cancel(self):
        self.cancelled = True
        self.status("cancellation requested; the current small batch will finish, then the run will stop.", Qgis.Warning)

    def record_timing(self, run_status):
        if self.timing_recorded:
            return
        elapsed_seconds = time.monotonic() - self.started
        row = {
            "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dataset": DATASET,
            "status": run_status,
            "source_features": self.source_count,
            "query_pieces_total": len(self.bog_geometries),
            "pieces_processed": self.piece_number,
            "titles_written": self.written,
            "bbox_candidates_checked": self.bbox_candidates,
            "elapsed_seconds": f"{elapsed_seconds:.3f}",
            "pieces_per_second": f"{self.piece_number / elapsed_seconds:.6f}" if elapsed_seconds else "",
        }
        TIMING_LOG.parent.mkdir(parents=True, exist_ok=True)
        write_header = not TIMING_LOG.is_file()
        with TIMING_LOG.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            if write_header:
                writer.writeheader()
            writer.writerow(row)
        self.timing_recorded = True

    def close_partial(self, require_success=False):
        if self.writer is not None:
            flushed = self.writer.flushBuffer()
            error = self.writer.lastError()
            self.writer = None
            if require_success and not flushed:
                raise RuntimeError(f"Could not flush the in-progress GeoPackage: {error}")

    def log_progress(self, force=False):
        now = time.monotonic()
        if not force and self.piece_number % LOG_EVERY_PIECES != 0 and now - self.last_log < LOG_EVERY_SECONDS:
            return
        elapsed = now - self.started
        rate = self.piece_number / elapsed if elapsed else 0
        remaining = len(self.bog_geometries) - self.piece_number
        eta = remaining / rate if rate else 0
        self.status(
            f"{self.piece_number:,}/{len(self.bog_geometries):,} bog pieces; "
            f"{self.written:,} titles written; {format_duration(elapsed)} elapsed; "
            f"{rate:.2f} pieces/s; ETA {format_duration(eta)}"
        )
        self.last_log = now

    def process_next_batch(self):
        if self.finished:
            return
        try:
            if self.cancelled:
                self.close_partial()
                self.finished = True
                self.record_timing("cancelled")
                self.status(f"stopped safely. Partial output retained for diagnostics: {self.partial_path.name}", Qgis.Warning)
                return

            stop_at = min(self.piece_number + PIECES_PER_TICK, len(self.bog_geometries))
            while self.piece_number < stop_at:
                bog_geometry = self.bog_geometries[self.piece_number]
                request = QgsFeatureRequest()
                request.setFilterRect(bog_geometry.boundingBox())
                request.setSubsetOfAttributes(["OBJECTID", "SP_ID", "COUNTY_NAM"], self.source.fields())

                for title in self.source.getFeatures(request):
                    source_fid = title.id()
                    if source_fid in self.seen_source_fids:
                        continue
                    self.bbox_candidates += 1
                    title_geometry = title.geometry()
                    if title_geometry.isNull() or title_geometry.isEmpty():
                        continue
                    if not title_geometry.intersects(bog_geometry):
                        continue

                    self.seen_source_fids.add(source_fid)
                    out_feature = QgsFeature(self.fields)
                    out_feature.setGeometry(title_geometry)
                    out_feature.setAttributes(
                        [source_fid, title["OBJECTID"], title["SP_ID"], title["COUNTY_NAM"]]
                    )
                    if not self.writer.addFeature(out_feature, QgsFeatureSink.FastInsert):
                        raise RuntimeError(f"Could not write title: {self.writer.lastError()}")
                    self.written += 1

                self.piece_number += 1

            self.log_progress()
            if self.piece_number >= len(self.bog_geometries):
                self.complete()
                return

            QTimer.singleShot(0, self.process_next_batch)
        except Exception as error:
            self.close_partial()
            self.finished = True
            self.record_timing("failed")
            self.status(f"failed safely after {self.piece_number:,} pieces: {error}", Qgis.Critical)
            raise

    def complete(self):
        self.close_partial(require_success=True)
        partial = QgsVectorLayer(
            f"{self.partial_path}|layername={self.config['output_layer']}",
            f"validated partial {DATASET}",
            "ogr",
        )
        if not partial.isValid():
            raise RuntimeError(f"In-progress output could not be reopened: {self.partial_path}")
        partial_count = partial.featureCount()
        if partial_count != self.written:
            raise RuntimeError(
                f"In-progress output count mismatch: wrote {self.written:,}, "
                f"reopened {partial_count:,}"
            )
        partial = None
        os.replace(self.partial_path, self.output_path)
        layer = QgsVectorLayer(
            f"{self.output_path}|layername={self.config['output_layer']}",
            self.config["display_name"],
            "ogr",
        )
        if not layer.isValid():
            raise RuntimeError(f"Completed output could not be reopened: {self.output_path}")
        if layer.featureCount() != self.written:
            raise RuntimeError(f"Output count mismatch: wrote {self.written:,}, reopened {layer.featureCount():,}")

        if DATASET == "freehold":
            symbol = QgsFillSymbol.createSimple(
                {"color": self.config["color"], "outline_color": self.config["color"], "outline_width": "0.35"}
            )
            symbol.setOpacity(0.25)
        else:
            symbol = QgsLineSymbol.createSimple({"line_color": self.config["color"], "line_width": "0.7"})
        layer.renderer().setSymbol(symbol)
        QgsProject.instance().addMapLayer(layer)
        iface.mapCanvas().refresh()

        self.finished = True
        self.record_timing("complete")
        self.log_progress(force=True)
        elapsed = time.monotonic() - self.started
        self.status(
            f"complete. {self.written:,} titles saved to {self.output_path.name}; "
            f"{self.bbox_candidates:,} bounding-box candidates checked; "
            f"total runtime {format_duration(elapsed)} ({elapsed:.3f} seconds). "
            f"Timing saved to {TIMING_LOG.relative_to(PROJECT_DIR)}."
        )


if DATASET not in CONFIGS:
    raise RuntimeError("CADASTRAL_DATASET must be either 'freehold' or 'leasehold'.")

if not QUERY_PIECES.is_file():
    raise FileNotFoundError(
        f"Query-piece GeoPackage is missing: {QUERY_PIECES}. Run 04b1 first."
    )
screened = QgsVectorLayer(
    f"{QUERY_PIECES}|layername={QUERY_PIECES_LAYER}", "saved screened bog query pieces", "ogr"
)
if not screened.isValid():
    raise RuntimeError("Could not open the saved single-part query-piece GeoPackage. Run 04b1 again.")
if screened.crs().authid() != "EPSG:2157":
    raise RuntimeError(f"Unexpected query-piece CRS: {screened.crs().authid()}")

runner = IncrementalTitleExtractor(CONFIGS[DATASET], screened)
globals()["bog_cadastral_runner"] = runner
runner.status(
    f"ready: {runner.source_count:,} source titles and {len(runner.bog_geometries):,} screened bog pieces; "
    f"processing {PIECES_PER_TICK} pieces per UI turn. "
    "QGIS should remain responsive."
)
QTimer.singleShot(0, runner.process_next_batch)
print("Incremental cadastral extraction started. To stop safely, run: bog_cadastral_runner.cancel()")
