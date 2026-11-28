"""Manifest-to-layout preflight. Every input has an explicit result."""

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from time import perf_counter

from .composition import compose
from .detection_cache import DetectionCache
from .discovery import source_paths
from .manifests import TeamManifest
from .measurements import MeasurementCache
from .model import Athlete, Layout
from .rows import RowTable


@dataclass
class TeamAnalysis:
    report: dict
    layout: Layout | None
    sources: dict[str, Path]
    hashes: dict[str, str]


def analyze_team(manifest: TeamManifest, table: RowTable, detector,
                 cache: DetectionCache, refresh: bool = False,
                 measurements: MeasurementCache | None = None, *, retained_capture=False,
                 capture_canvas=None, fixed_scale=None, min_confidence=0.8, relaxed_heads=False) -> TeamAnalysis:
    started = perf_counter()
    issues = []
    records, people, sources, hashes = [], [], {}, {}
    measurements = measurements or MeasurementCache()
    cache_hits = 0
    canvas = tuple(capture_canvas) if capture_canvas else None
    detection_seconds = 0.0
    transparency = bool(manifest.settings.transparency_mode)
    declared = {e.path.resolve() for e in manifest.entries}
    all_paths = source_paths(manifest.source_folder)
    if any(p.is_symlink() for p in all_paths):
        issues.append("Source folder contains symbolic links; remove them before processing.")
    observed = {p for p in all_paths if p.is_file() and p.suffix.lower() == ".png"}
    for extra in sorted(observed - declared):
        issues.append("PNG missing from manifest: " + extra.relative_to(manifest.source_folder).as_posix())
    geometry_ids = {e.geometry_id for e in manifest.entries if not e.excluded and e.geometry_id}
    if len(geometry_ids) > 1:
        issues.append("Athletes use different capture geometries; calibrate them to one common geometry.")
    seen_hashes = {}
    for e in manifest.entries:
        record = {"id": e.id, "file": e.relative_path, "status": "excluded" if e.excluded else "review",
                  "issues": [], "capture": {"geometry_id": e.geometry_id, "source_scale": e.source_scale,
                                             "relative_height": e.relative_height, "provenance": e.provenance}}
        records.append(record)
        if e.excluded:
            record["exclusion_reason"] = e.exclusion_reason
            continue
        errors = record["issues"]
        try:
            tick = perf_counter()
            measured = measurements.measure(e, detector, cache, refresh, min_confidence=min_confidence, relaxed_heads=relaxed_heads, transparency=transparency)
            detection_seconds += perf_counter() - tick
            record.update(sha256=measured.sha256, image_size=measured.size,
                          body=asdict(measured.body), detection_method=measured.method,
                          faces=[asdict(f) for f in measured.faces], cache_hit=measured.cache_hit)
            cache_hits += int(measured.cache_hit)
            errors.extend(measured.issues)
            if transparency:
                canvas = canvas or measured.size
                if retained_capture and measured.size != canvas:
                    errors.append("PNG canvas differs from the saved camera setup. Verify original size or choose a separate job.")
                if not retained_capture and e.relative_height is None:
                    errors.append("Confirm original-size Player PNGs in Job setup, or supply a manual height order.")
                e = replace(e, source_scale=1.0,
                            relative_height=e.relative_height if e.relative_height is not None else measured.size[1] - measured.head.top,
                            geometry_id=e.geometry_id or "job-fixed-alpha",
                            provenance="Incoming pixel scale preserved. Silhouette top ranks athletes under the job's consistent capture setup.")
            elif retained_capture and e.relative_height is None:
                canvas = canvas or measured.size
                if measured.size != canvas:
                    errors.append("Canvas differs from this job's fixed capture setup; verify cropping/resizing.")
                elif measured.head is not None and len(measured.faces) == 1 and measured.faces[0].confidence >= min_confidence:
                    face = measured.faces[0].box
                    # A COMMON capture-plane reference, not a per-image silhouette
                    # height. Face position ranks stature under the confirmed fixed
                    # camera/canvas geometry. Hair never determines height ranking.
                    top = max(0, face.top - 0.35 * face.height)
                    e = replace(e, source_scale=1.0, relative_height=canvas[1] - top,
                                geometry_id="job-fixed-capture",
                                provenance="Job confirms unchanged canvas/scale and fixed camera. Height ranking proxy from anatomical face position; not a physical height measurement.")
                elif e.manual_head is not None and relaxed_heads:
                    top = e.manual_head.top + e.manual_head.height * 0.12
                    scale = e.source_scale or 1.0
                    e = replace(e, source_scale=scale, relative_height=(canvas[1] - top) / scale,
                                geometry_id=e.geometry_id or "job-fixed-capture",
                                provenance="Operator-corrected head position under confirmed fixed capture; approximate height ranking, not a physical measurement.")
            record["capture"] = {"geometry_id": e.geometry_id, "source_scale": e.source_scale,
                                 "relative_height": e.relative_height, "provenance": e.provenance}
            if e.source_scale is None or e.relative_height is None or not e.geometry_id or not e.provenance:
                errors.append("Height ranking needs confirmation. Check the fixed-capture job setting or correct this athlete's head/height.")
            if measured.sha256 in seen_hashes:
                errors.append("Duplicate image: exact copy of " + seen_hashes[measured.sha256] + ". Renaming does not change the image; remove or exclude the copy.")
            seen_hashes[measured.sha256] = e.id
            if measured.head is not None:
                record["head"] = asdict(measured.head)
                if not measured.body.contains(measured.head):
                    errors.append("Head bounds lie outside the visible athlete silhouette.")
            if not errors and measured.head is not None:
                people.append(Athlete(e.id, measured.size, measured.body, measured.head,
                                      e.relative_height, e.source_scale, measured.confidence))
                sources[e.id] = e.path
                hashes[e.id] = measured.sha256
                record["status"] = "ready"
        except (OSError, ValueError, RuntimeError) as error:
            errors.append(str(error))
    if any(r["issues"] for r in records):
        issues.append("Resolve athlete review items before composing this team.")
    layout = None
    if not issues:
        try:
            # Exclusions were deliberately accounted for above. compose receives
            # all included athletes and the remaining non-destructive corrections.
            excluded_ids = set(manifest.corrections.excluded)
            corrections = replace(manifest.corrections, excluded=(), offsets_in={
                key: value for key, value in manifest.corrections.offsets_in.items() if key not in excluded_ids})
            layout = compose(people, table, manifest.settings, corrections, fixed_scale=fixed_scale, min_confidence=min_confidence)
            issues.extend(layout.issues)
        except ValueError as error:
            issues.append(str(error))
    report = {"schema_version": 1, "team_id": manifest.team_id, "team_name": manifest.team_name,
              "status": "review" if issues else "ready", "issues": issues, "athletes": records,
              "settings": asdict(manifest.settings), "corrections": asdict(manifest.corrections),
              "layout": asdict(layout) if layout else None,
              "timings": {"source_measurement_seconds": detection_seconds,
                          "total_seconds": perf_counter() - started}, "cache_hits": cache_hits, "capture_canvas": canvas,
              "placement_method": "transparency" if transparency else "face"}
    return TeamAnalysis(report, layout, sources, hashes)
