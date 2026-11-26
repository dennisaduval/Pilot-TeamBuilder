"""Manifest-to-layout preflight. Every input has an explicit result."""

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from time import perf_counter

from .composition import compose
from .discovery import source_paths
from .detection import measure_head
from .detection_cache import DetectionCache
from .manifests import TeamManifest
from .model import Athlete, Layout
from .rows import RowTable
from .sources import load_png


@dataclass
class TeamAnalysis:
    report: dict
    layout: Layout | None
    sources: dict[str, Path]
    hashes: dict[str, str]


def analyze_team(manifest: TeamManifest, table: RowTable, detector,
                 cache: DetectionCache, refresh: bool = False) -> TeamAnalysis:
    started = perf_counter()
    issues = []
    records, people, sources, hashes = [], [], {}, {}
    cache_hits = 0
    detection_seconds = 0.0
    decode_seconds = 0.0
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
        if e.source_scale is None or e.relative_height is None or not e.geometry_id or not e.provenance:
            errors.append("Capture scale, relative height, geometry ID and provenance require confirmation.")
        try:
            tick = perf_counter()
            with load_png(e.path) as source:
                decode_seconds += perf_counter() - tick
                record.update(sha256=source.sha256, image_size=source.image.size, body=asdict(source.body))
                if source.sha256 in seen_hashes:
                    errors.append("Duplicate image content also used by " + seen_hashes[source.sha256] + ".")
                seen_hashes[source.sha256] = e.id
                head = e.manual_head
                confidence = 1.0
                if head is not None:
                    record["detection_method"] = "reviewed-manual"
                    record["faces"] = []
                else:
                    tick = perf_counter()
                    identity = detector.identity
                    key = cache.key(source.sha256, identity)
                    faces = None if refresh else cache.get(key, source.image.size)
                    record["cache_hit"] = faces is not None
                    if faces is None:
                        faces = detector.detect(source.image)
                        cache.put(key, source.image.size, faces)
                    else:
                        cache_hits += 1
                    detection_seconds += perf_counter() - tick
                    record["detection_method"] = identity
                    record["faces"] = [asdict(f) for f in faces]
                    if len(faces) != 1:
                        errors.append(f"Detected {len(faces)} faces; expected one. Correct detection or explicitly exclude this image.")
                    else:
                        confidence = faces[0].confidence
                        if confidence < 0.8:
                            errors.append("Face detection confidence is below 0.8.")
                        head, uncertain = measure_head(source.image, faces[0])
                        if uncertain:
                            errors.append("Head/hair silhouette reaches the analysis window; review head bounds.")
                if head is not None:
                    record["head"] = asdict(head)
                    if not source.body.contains(head):
                        errors.append("Head bounds lie outside the visible athlete silhouette.")
                if not errors and head is not None:
                    people.append(Athlete(e.id, source.image.size, source.body, head,
                                          e.relative_height, e.source_scale, confidence))
                    sources[e.id] = e.path
                    hashes[e.id] = source.sha256
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
            corrections = replace(manifest.corrections, excluded=())
            layout = compose(people, table, manifest.settings, corrections)
            issues.extend(layout.issues)
        except ValueError as error:
            issues.append(str(error))
    report = {"schema_version": 1, "team_id": manifest.team_id, "team_name": manifest.team_name,
              "status": "review" if issues else "ready", "issues": issues, "athletes": records,
              "settings": asdict(manifest.settings), "corrections": asdict(manifest.corrections),
              "layout": asdict(layout) if layout else None,
              "timings": {"decode_seconds": decode_seconds, "detection_seconds": detection_seconds,
                          "total_seconds": perf_counter() - started}, "cache_hits": cache_hits}
    return TeamAnalysis(report, layout, sources, hashes)
