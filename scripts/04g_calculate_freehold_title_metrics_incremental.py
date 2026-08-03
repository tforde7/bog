"""Calculate screened-bog and leasehold-overlap metrics for freehold titles.

Run from the QGIS Python Console after both 04f extractions have completed.
The script processes the local freehold subset incrementally, queries only the
indexed 04b1 bog pieces and 04f leasehold subset, and writes to an in-progress
GeoPackage. Raw and earlier derived data are never modified.
"""

from pathlib import Path
import csv
from datetime import datetime, timezone
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
)
import processing


PROJECT_DIR = Path("/Users/tforde/projects/bog")
PROCESSED_DIR = PROJECT_DIR / "data" / "processed"
DOCS_DIR = PROJECT_DIR / "docs"

FREEHOLD_PATH = PROCESSED_DIR / "04f_cadastral_freehold_bog_overlap.gpkg"
FREEHOLD_LAYER = "cadastral_freehold_bog_overlap"
BOG_PATH = PROCESSED_DIR / "04b1_screened_bog_query_pieces.gpkg"
BOG_LAYER = "screened_bog_query_pieces"
LEASEHOLD_PATH = PROCESSED_DIR / "04f_cadastral_leasehold_bog_overlap.gpkg"
LEASEHOLD_LAYER = "cadastral_leasehold_bog_overlap"

OUTPUT_PATH = PROCESSED_DIR / "04g_freehold_title_bog_metrics.gpkg"
PARTIAL_PATH = PROCESSED_DIR / "04g_freehold_title_bog_metrics_IN_PROGRESS.gpkg"
OUTPUT_LAYER = "freehold_title_bog_metrics"
DISPLAY_NAME = "04g — Freehold title bog metrics"
TIMING_LOG = DOCS_DIR / "04g_freehold_title_metrics_timings.csv"

TITLES_PER_TICK = 100
LOG_EVERY_TITLES = 5_000
LOG_EVERY_SECONDS = 30


def format_duration(seconds):
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m {seconds:02d}s"
    return f"{minutes:d}m {seconds:02d}s"


def load_layer(path, layer_name, display_name):
    if not path.is_file():
        raise FileNotFoundError(f"Required input is missing: {path}")
    layer = QgsVectorLayer(f"{path}|layername={layer_name}", display_name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Could not open {path.name}, layer {layer_name!r}.")
    if layer.crs().authid() != "EPSG:2157":
        raise RuntimeError(f"Unexpected CRS for {path.name}: {layer.crs().authid()}")
    return layer


def remove_loaded_path(path):
    project = QgsProject.instance()
    for layer in list(project.mapLayers().values()):
        if str(path) in layer.source():
            project.removeMapLayer(layer.id())


def output_fields():
    fields = QgsFields()
    fields.append(QgsField("source_fid", QVariant.LongLong))
    fields.append(QgsField("objectid", QVariant.LongLong))
    fields.append(QgsField("sp_id", QVariant.Double))
    county = QgsField("county_nam", QVariant.String)
    county.setLength(0)
    fields.append(county)
    fields.append(QgsField("title_ha", QVariant.Double))
    fields.append(QgsField("bog_ha", QVariant.Double))
    fields.append(QgsField("bog_pct", QVariant.Double))
    fields.append(QgsField("lease_flag", QVariant.Int))
    fields.append(QgsField("lease_ha", QVariant.Double))
    fields.append(QgsField("lease_pct", QVariant.Double))
    return fields


def polygonal_geometry(geometry, label):
    """Return a valid temporary geometry copy without changing the source."""
    copied = QgsGeometry(geometry)
    if copied.isNull() or copied.isEmpty():
        raise RuntimeError(f"{label} has empty geometry.")
    if not copied.isGeosValid():
        copied = copied.makeValid()
        if copied.isNull() or copied.isEmpty():
            raise RuntimeError(f"{label} could not be repaired: {copied.lastError()}")
    return copied


def union_area(intersections, label):
    if not intersections:
        return 0.0
    if len(intersections) == 1:
        return intersections[0].area()
    unioned = QgsGeometry.unaryUnion(intersections)
    if unioned.isNull():
        raise RuntimeError(f"Could not union {label} intersections: {unioned.lastError()}")
    return unioned.area()


def overlap_area(base_geometry, overlay_layer):
    intersections = []
    request = QgsFeatureRequest()
    request.setFilterRect(base_geometry.boundingBox())
    request.setNoAttributes()
    for overlay_feature in overlay_layer.getFeatures(request):
        overlay_geometry = overlay_feature.geometry()
        if overlay_geometry.isNull() or overlay_geometry.isEmpty():
            continue
        if not base_geometry.intersects(overlay_geometry):
            continue
        intersection = base_geometry.intersection(overlay_geometry)
        if intersection.isNull():
            raise RuntimeError(f"Intersection failed: {intersection.lastError()}")
        if not intersection.isEmpty() and intersection.area() > 0:
            intersections.append(intersection)
    return union_area(intersections, overlay_layer.name())


class IncrementalTitleMetrics:
    def __init__(self, freehold, bog, leasehold):
        self.freehold = freehold
        self.bog = bog
        self.leasehold = leasehold
        self.fields = output_fields()
        self.total = freehold.featureCount()
        self.processed = 0
        self.written = 0
        self.no_area = 0
        self.repaired_titles = 0
        self.leasehold_flagged = 0
        self.cancelled = False
        self.finished = False
        self.timing_recorded = False
        self.started = time.monotonic()
        self.last_log = self.started

        request = QgsFeatureRequest()
        request.setSubsetOfAttributes(
            ["source_fid", "objectid", "sp_id", "county_nam"],
            freehold.fields(),
        )
        self.iterator = freehold.getFeatures(request)

        remove_loaded_path(OUTPUT_PATH)
        remove_loaded_path(PARTIAL_PATH)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = OUTPUT_LAYER
        options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
        self.writer = QgsVectorFileWriter.create(
            str(PARTIAL_PATH),
            self.fields,
            QgsWkbTypes.flatType(freehold.wkbType()),
            freehold.crs(),
            QgsProject.instance().transformContext(),
            options,
        )
        if self.writer.hasError() != QgsVectorFileWriter.NoError:
            raise RuntimeError(f"Could not create in-progress output: {self.writer.errorMessage()}")

    def status(self, message, level=Qgis.Info):
        text = f"04g: {message}"
        print(text)
        iface.messageBar().pushMessage("Freehold title metrics", text, level=level, duration=8)

    def cancel(self):
        self.cancelled = True
        self.status("cancellation requested; the current batch will finish first.", Qgis.Warning)

    def close_writer(self, require_success=False):
        if self.writer is None:
            return
        flushed = self.writer.flushBuffer()
        error = self.writer.lastError()
        self.writer = None
        if require_success and not flushed:
            raise RuntimeError(f"Could not flush in-progress output: {error}")

    def record_timing(self, run_status):
        if self.timing_recorded:
            return
        elapsed = time.monotonic() - self.started
        row = {
            "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": run_status,
            "source_titles": self.total,
            "titles_processed": self.processed,
            "titles_written": self.written,
            "boundary_only_titles": self.no_area,
            "repaired_titles": self.repaired_titles,
            "leasehold_flagged": self.leasehold_flagged,
            "elapsed_seconds": f"{elapsed:.3f}",
            "titles_per_second": f"{self.processed / elapsed:.6f}" if elapsed else "",
        }
        TIMING_LOG.parent.mkdir(parents=True, exist_ok=True)
        write_header = not TIMING_LOG.is_file()
        with TIMING_LOG.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            if write_header:
                writer.writeheader()
            writer.writerow(row)
        self.timing_recorded = True

    def log_progress(self, force=False):
        now = time.monotonic()
        if (
            not force
            and self.processed % LOG_EVERY_TITLES != 0
            and now - self.last_log < LOG_EVERY_SECONDS
        ):
            return
        elapsed = now - self.started
        rate = self.processed / elapsed if elapsed else 0
        remaining = self.total - self.processed
        eta = remaining / rate if rate else 0
        self.status(
            f"{self.processed:,}/{self.total:,} titles; {self.written:,} positive-area candidates; "
            f"{format_duration(elapsed)} elapsed; {rate:.1f} titles/s; ETA {format_duration(eta)}"
        )
        self.last_log = now

    def process_title(self, feature):
        source_geometry = feature.geometry()
        title_geometry = QgsGeometry(source_geometry)
        if not title_geometry.isGeosValid():
            title_geometry = polygonal_geometry(title_geometry, f"freehold title {feature.id()}")
            self.repaired_titles += 1

        title_area = title_geometry.area()
        if title_area <= 0:
            self.no_area += 1
            return
        bog_area = overlap_area(title_geometry, self.bog)
        if bog_area <= 0:
            self.no_area += 1
            return
        lease_area = overlap_area(title_geometry, self.leasehold)
        lease_flag = int(lease_area > 0)
        self.leasehold_flagged += lease_flag

        output = QgsFeature(self.fields)
        output.setGeometry(title_geometry)
        output.setAttributes(
            [
                feature["source_fid"],
                feature["objectid"],
                feature["sp_id"],
                feature["county_nam"],
                title_area / 10_000,
                bog_area / 10_000,
                min(100.0, max(0.0, 100.0 * bog_area / title_area)),
                lease_flag,
                lease_area / 10_000,
                min(100.0, max(0.0, 100.0 * lease_area / title_area)),
            ]
        )
        if not self.writer.addFeature(output, QgsFeatureSink.FastInsert):
            raise RuntimeError(f"Could not write title metrics: {self.writer.lastError()}")
        self.written += 1

    def process_next_batch(self):
        if self.finished:
            return
        try:
            if self.cancelled:
                self.close_writer()
                self.finished = True
                self.record_timing("cancelled")
                self.status(
                    f"stopped safely. Partial output retained: {PARTIAL_PATH.name}",
                    Qgis.Warning,
                )
                return

            for _ in range(TITLES_PER_TICK):
                try:
                    feature = next(self.iterator)
                except StopIteration:
                    self.complete()
                    return
                self.process_title(feature)
                self.processed += 1

            self.log_progress()
            QTimer.singleShot(0, self.process_next_batch)
        except Exception as error:
            self.close_writer()
            self.finished = True
            self.record_timing("failed")
            self.status(f"failed safely after {self.processed:,} titles: {error}", Qgis.Critical)
            raise

    def complete(self):
        self.close_writer(require_success=True)
        partial = QgsVectorLayer(
            f"{PARTIAL_PATH}|layername={OUTPUT_LAYER}",
            "validated partial title metrics",
            "ogr",
        )
        if not partial.isValid():
            raise RuntimeError(f"In-progress output could not be reopened: {PARTIAL_PATH}")
        partial_count = partial.featureCount()
        if partial_count != self.written:
            raise RuntimeError(
                f"In-progress count mismatch: wrote {self.written:,}, reopened {partial_count:,}."
            )
        partial = None
        os.replace(PARTIAL_PATH, OUTPUT_PATH)

        saved = QgsVectorLayer(
            f"{OUTPUT_PATH}|layername={OUTPUT_LAYER}",
            DISPLAY_NAME,
            "ogr",
        )
        if not saved.isValid():
            raise RuntimeError(f"Completed output could not be reopened: {OUTPUT_PATH}")
        if saved.featureCount() != self.written:
            raise RuntimeError(
                f"Final count mismatch: wrote {self.written:,}, reopened {saved.featureCount():,}."
            )

        project = QgsProject.instance()
        project.addMapLayer(saved, False)
        project.layerTreeRoot().addLayer(saved).setItemVisibilityChecked(False)
        self.finished = True
        self.record_timing("complete")
        self.log_progress(force=True)
        elapsed = time.monotonic() - self.started
        self.status(
            f"complete. {self.written:,} positive-area titles saved; "
            f"{self.no_area:,} boundary-only/zero-area titles omitted; "
            f"{self.leasehold_flagged:,} titles flagged for leasehold overlap; "
            f"runtime {format_duration(elapsed)} ({elapsed:.3f} seconds). "
            f"Output added with visibility off: {OUTPUT_PATH.name}."
        )


freehold = load_layer(FREEHOLD_PATH, FREEHOLD_LAYER, "saved 04f freehold titles")
bog = load_layer(BOG_PATH, BOG_LAYER, "saved 04b1 bog query pieces")
leasehold = load_layer(LEASEHOLD_PATH, LEASEHOLD_LAYER, "saved 04f leasehold titles")

# Repair the small leasehold subset in memory before it is used in overlays.
leasehold = processing.run(
    "native:fixgeometries",
    {"INPUT": leasehold, "METHOD": 1, "OUTPUT": "memory:"},
)["OUTPUT"]
if not leasehold.dataProvider().createSpatialIndex():
    raise RuntimeError("Could not create a spatial index on the repaired leasehold memory layer.")

runner_04g = IncrementalTitleMetrics(freehold, bog, leasehold)
globals()["bog_title_metrics_runner"] = runner_04g
runner_04g.status(
    f"ready: {runner_04g.total:,} freehold titles; "
    f"{bog.featureCount():,} bog pieces; {leasehold.featureCount():,} repaired leasehold features. "
    f"Processing {TITLES_PER_TICK} titles per UI turn."
)
QTimer.singleShot(0, runner_04g.process_next_batch)
print("04g title-metrics calculation started. To stop safely, run: bog_title_metrics_runner.cancel()")
