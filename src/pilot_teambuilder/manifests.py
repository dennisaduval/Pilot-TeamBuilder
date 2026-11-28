"""Versioned team input contracts; capture geometry is explicit, never guessed."""

import json
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from .discovery import is_system_entry, source_paths
from .model import CompositionSettings, Corrections, Rect


def positive(value, label):
    if type(value) not in (float, int) or not isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be a finite positive number.")
    return float(value)


def keys(data, allowed, label):
    if not isinstance(data, dict) or set(data) - set(allowed):
        raise ValueError(f"Invalid or unknown fields in {label}.")


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string.")
    return value


@dataclass(frozen=True)
class SourceEntry:
    id: str
    relative_path: str
    path: Path
    source_scale: float | None
    relative_height: float | None
    geometry_id: str | None
    provenance: str | None
    manual_head: Rect | None
    excluded: bool
    exclusion_reason: str | None


@dataclass(frozen=True)
class TeamManifest:
    team_id: str
    team_name: str
    source_folder: Path
    entries: tuple[SourceEntry, ...]
    settings: CompositionSettings
    corrections: Corrections


def inventory(folder: Path) -> dict:
    """Describe all PNGs recursively. Write the returned manifest outside sources."""
    folder = folder.resolve(strict=True)
    if not folder.is_dir():
        raise ValueError("Choose an existing team folder.")
    paths = source_paths(folder)
    if any(p.is_symlink() for p in paths):
        raise ValueError("Team folders cannot contain symbolic links.")
    images = [p for p in paths if p.is_file() and p.suffix.lower() == ".png"]
    if not images:
        raise ValueError("The team folder contains no PNG images.")
    return {"schema_version": 1, "team_id": folder.name, "team_name": folder.name,
            "source_folder": str(folder), "athletes": [
                {"id": f"athlete-{i:04}", "file": p.relative_to(folder).as_posix(),
                 "capture": {"geometry_id": None, "source_scale": None,
                             "relative_height": None, "provenance": None}}
                for i, p in enumerate(images, 1)]}


def load_manifest(path: Path) -> TeamManifest:
    def reject_constant(value):
        raise ValueError(f"Non-finite JSON constant: {value}")

    data = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    return manifest_from_data(data, path.parent)


def manifest_from_data(data: dict, base_dir: Path, *, require_sources=True) -> TeamManifest:
    """Validate an in-memory editor snapshot using the same contract as the CLI."""
    keys(data, ("schema_version", "team_id", "team_name", "source_folder", "athletes",
                "settings", "corrections"), "team manifest")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported team manifest schema_version; expected 1.")
    folder = Path(text(data.get("source_folder"), "source_folder"))
    if not folder.is_absolute():
        folder = base_dir / folder
    if folder.is_symlink():
        raise ValueError("Source folders cannot be symbolic links.")
    folder = folder.resolve(strict=require_sources)
    if (require_sources or folder.exists()) and not folder.is_dir():
        raise ValueError("source_folder must be a directory.")
    records = data.get("athletes")
    if not isinstance(records, list) or not records:
        raise ValueError("Manifest must list every athlete PNG, including exclusions.")
    entries = []
    for raw in records:
        keys(raw, ("id", "file", "capture", "manual_head", "excluded", "exclusion_reason"), "athlete")
        name = text(raw.get("file"), "athlete file")
        relative = Path(name)
        if is_system_entry(relative):
            raise ValueError("macOS metadata files cannot be athlete images.")
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise ValueError("Image paths must be relative paths within source_folder.")
        source = folder / relative
        if any(p.is_symlink() for p in (source, *source.parents)):
            raise ValueError("Symbolic-link source paths are not supported.")
        if not source.resolve().is_relative_to(folder) or source.suffix.lower() != ".png":
            raise ValueError("Athlete images must be PNG paths inside source_folder.")
        capture = raw.get("capture", {})
        keys(capture, ("source_scale", "relative_height", "geometry_id", "provenance"), "capture")
        scale, height = capture.get("source_scale"), capture.get("relative_height")
        if scale is not None:
            scale = positive(scale, "capture source_scale")
        if height is not None:
            height = positive(height, "capture relative_height")
        geometry = capture.get("geometry_id")
        provenance = capture.get("provenance")
        if geometry is not None:
            text(geometry, "capture geometry_id")
        if provenance is not None:
            text(provenance, "capture provenance")
        head = raw.get("manual_head")
        if head is not None:
            if not isinstance(head, list) or len(head) != 4 or any(type(v) not in (int, float) for v in head):
                raise ValueError("manual_head must be [left, top, right, bottom] in oriented source pixels.")
            head = Rect(*head)
        excluded = raw.get("excluded", False)
        if type(excluded) is not bool:
            raise ValueError("excluded must be boolean.")
        reason = raw.get("exclusion_reason")
        if excluded:
            text(reason, "exclusion_reason")
        entries.append(SourceEntry(text(raw.get("id"), "athlete id"), relative.as_posix(),
                                   source, scale, height, geometry, provenance, head, excluded, reason))
    if len({e.id for e in entries}) != len(entries) or len({e.path for e in entries}) != len(entries):
        raise ValueError("Athlete IDs and file paths must be unique.")
    settings = data.get("settings", {})
    keys(settings, CompositionSettings.__dataclass_fields__, "settings")
    if any(type(v) not in (float, int) for v in settings.values()):
        raise ValueError("Composition settings must be numbers.")
    settings = CompositionSettings(**settings)
    settings.validate()
    changes = data.get("corrections", {})
    keys(changes, ("rows", "offsets_in"), "corrections")
    rows = changes.get("rows")
    if rows is not None:
        if not isinstance(rows, list) or any(not isinstance(r, list) or
                any(not isinstance(i, str) for i in r) for r in rows):
            raise ValueError("corrections.rows must be arrays of athlete IDs.")
        rows = tuple(tuple(r) for r in rows)
    offsets = changes.get("offsets_in", {})
    if not isinstance(offsets, dict) or any(not isinstance(v, list) or len(v) != 2 or
            any(type(n) not in (float, int) or not isfinite(n) for n in v) for v in offsets.values()):
        raise ValueError("corrections.offsets_in must map IDs to [x, y] inches.")
    corrections = Corrections(rows, tuple(e.id for e in entries if e.excluded),
                              {i: tuple(v) for i, v in offsets.items()})
    return TeamManifest(text(data.get("team_id"), "team_id"),
                        text(data.get("team_name"), "team_name"), folder,
                        tuple(entries), settings, corrections)
