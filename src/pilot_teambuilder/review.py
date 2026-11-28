"""Desktop review model and durable jobs, independent of Tk and image rendering."""

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path

from .composition import center_out_slots
from .discovery import team_folders
from .manifests import inventory, load_manifest, manifest_from_data
from .model import CompositionSettings
from .rows import validate_rows


def fingerprint(data) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def new_job(incoming: Path, name: str | None = None, *, allow_empty=False) -> dict:
    incoming = incoming.resolve(strict=True)
    folders = team_folders(incoming)
    if not folders and not allow_empty:
        raise ValueError(
            "Choose a team folder or the incoming folder containing your team folders."
        )
    teams = []
    for folder in folders:
        manifest = inventory(folder)
        manifest["team_name"] = folder.name
        teams.append(
            {"manifest": manifest, "anchors": {}, "templates": "", "exports": []}
        )
    return {
        "schema_version": 1,
        "hotfolder_mode": True,
        "framing_version": 4,
        "name": name or incoming.name,
        "incoming": str(incoming),
        "templates": str(incoming.parent / "Templates/Team"),
        "output": str(incoming.parent / "Completed Teams"),
        "row_csv": "",
        "template_presets": {},
        "retained_capture": True,
        "job_settings": {"crop_body_fraction": 1.0, "foreground_framing": 1.0,
                         "transparency_mode": 1.0, "v_step_in": 0.125, "v_max_drop_in": 0.5},
        "relaxed_detection": True,
        "base_scale": None,
        "settle_seconds": 20,
        "require_ready": False,
        "routes": [],
        "teams": teams,
        "selected_team": 0,
        "preview_team_source": "",
    }


def import_manifest(job: dict | None, path: Path) -> dict:
    parsed = load_manifest(path)
    data = json.loads(path.read_text())
    data["source_folder"] = str(parsed.source_folder)
    result = (
        deepcopy(job)
        if job is not None
        else new_job(parsed.source_folder, parsed.team_name)
    )
    if job is None:
        result["teams"] = []
    if any(t["manifest"]["team_id"] == parsed.team_id for t in result["teams"]):
        raise ValueError(
            "This team is already in the job. Its existing corrections were kept."
        )
    result["teams"].append(
        {"manifest": data, "anchors": {}, "templates": "", "exports": []}
    )
    result["selected_team"] = len(result["teams"]) - 1
    validate_job(result, path.parent)
    return result


def add_image_folder(job: dict | None, folder: Path) -> dict:
    """Load PNG directories directly; selecting an existing source retains edits."""
    incoming = new_job(folder)
    if job is None:
        return incoming
    result = deepcopy(job)
    for team in incoming["teams"]:
        source = Path(team["manifest"]["source_folder"]).resolve()
        existing = next((i for i, t in enumerate(result["teams"])
                         if Path(t["manifest"]["source_folder"]).resolve() == source), None)
        if existing is not None:
            result["selected_team"] = existing
            continue
        if any(t["manifest"]["team_id"] == team["manifest"]["team_id"] for t in result["teams"]):
            from uuid import uuid4
            team["manifest"]["team_id"] += ":" + uuid4().hex
        if result.get("job_settings"):
            team["manifest"]["settings"] = deepcopy(result["job_settings"])
        result["teams"].append(team)
        result["selected_team"] = len(result["teams"]) - 1
    validate_job(result, folder.parent)
    return result


def select_preview_folder(job: dict, folder: Path) -> dict:
    """Select one real team for setup without changing the watched hotfolder."""
    folders = team_folders(folder.resolve(strict=True))
    if len(folders) != 1:
        raise ValueError("Choose one team folder containing athlete PNGs, rather than a parent with several teams.")
    result = add_image_folder(job, folders[0])
    source = folders[0].resolve()
    team = result["teams"][result["selected_team"]]
    team["preview_only"] = not source.is_relative_to(Path(result["incoming"]).resolve())
    result["preview_team_source"] = str(source)
    return result


def requested_distribution(count: int, requested: int | None, automatic: tuple[int, ...]) -> tuple[int, ...]:
    if requested is None:
        validate_rows(automatic, count)
        return automatic
    if type(requested) is not int or not 1 <= requested <= count:
        raise ValueError("Requested rows must be between 1 and the included athlete count.")
    minimum = (count + 15) // 16
    if requested < minimum:
        raise ValueError(f"{count} athletes need at least {minimum} rows (maximum 16 per row).")
    base, extra = divmod(count, requested)
    result = tuple(base + (index < extra) for index in range(requested))
    validate_rows(result, count)
    return result


def validate_job(job: dict, base_dir: Path):
    if (
        not isinstance(job, dict)
        or type(job.get("schema_version")) is not int
        or job.get("schema_version") != 1
        or not isinstance(job.get("teams"), list)
        or (not job["teams"] and not job.get("hotfolder_mode"))
    ):
        raise ValueError("Unsupported or empty TeamBuilder job.")
    for key in ("name", "incoming", "templates", "output", "row_csv"):
        if not isinstance(job.get(key), str):
            raise ValueError(f"Invalid job {key}.")
    if not isinstance(job.get("preview_team_source", ""), str):
        raise ValueError("Invalid preview team folder.")
    if not isinstance(job.get("template_presets"), dict):
        raise ValueError("Invalid template settings in saved job.")
    if Path(job["output"]).resolve().is_relative_to(Path(job["incoming"]).resolve()):
        raise ValueError("Completed images must be outside the incoming source folder.")
    for team in job["teams"]:
        if not isinstance(team, dict) or not isinstance(team.get("manifest"), dict):
            raise ValueError("Invalid team in saved job.")
        if type(team.get("preview_only", False)) is not bool:
            raise ValueError("Invalid preview-only team flag.")
        manifest = manifest_from_data(team["manifest"], base_dir, require_sources=not team.get("preview_only", False))
        if team.get("requested_rows") is not None:
            requested_distribution(sum(not e.excluded for e in manifest.entries), team["requested_rows"], ())
        if Path(job["output"]).resolve().is_relative_to(manifest.source_folder):
            raise ValueError(
                "Completed images must be outside every team's source folder."
            )
        if not isinstance(team.get("anchors"), dict) or not isinstance(
            team.get("exports"), list
        ):
            raise ValueError("Invalid saved team review state.")
    if type(job.get("selected_team")) is not int or (job["teams"] and not 0 <= job["selected_team"] < len(job["teams"])):
        raise ValueError("Invalid selected team in job.")
    from math import isfinite
    if type(job.get("relaxed_detection", True)) is not bool:
        raise ValueError("Detection mode must be true or false.")
    if type(job.get("retained_capture", False)) is not bool:
        raise ValueError("Fixed capture setting must be true or false.")
    if type(job.get("settle_seconds", 20)) not in (int, float) or not 0 <= job.get("settle_seconds", 20) <= 3600:
        raise ValueError("Copy settling time must be between 0 and 3600 seconds.")
    if type(job.get("require_ready", False)) is not bool:
        raise ValueError("Ready-marker setting must be true or false.")
    scale = job.get("base_scale")
    if scale is not None and (type(scale) not in (int, float) or not isfinite(scale) or scale <= 0):
        raise ValueError("Invalid common job scale.")
    CompositionSettings(**job.get("job_settings", {})).validate()
    if not isinstance(job.get("routes", []), list) or any(not isinstance(p, str) or not p for p in job.get("routes", [])):
        raise ValueError("Invalid delivery folders.")
    source_roots = [Path(job["incoming"]).resolve()] + [Path(t["manifest"]["source_folder"]).resolve() for t in job["teams"]]
    for target in [job["output"], *job.get("routes", [])]:
        if any(Path(target).resolve().is_relative_to(source) for source in source_roots):
            raise ValueError("Output/delivery folders must be outside incoming source folders.")
    fingerprint(job)  # Reject non-finite numbers before any write.


class JobStore:
    """Atomic saves with a version guard; never overwrite an external edit."""

    def __init__(self):
        self.path: Path | None = None
        self.version: str | None = None

    def load(self, path: Path) -> dict:
        data = path.read_bytes()
        job = json.loads(data)
        validate_job(job, path.parent)
        self.path, self.version = path, hashlib.sha256(data).hexdigest()
        return job

    def save(self, job: dict, path: Path | None = None):
        target = path or self.path
        if target is None:
            raise ValueError("Choose where to save this job first.")
        validate_job(job, target.parent)
        if target.exists():
            if (
                self.path != target
                or hashlib.sha256(target.read_bytes()).hexdigest() != self.version
            ):
                raise ValueError(
                    "This job was changed elsewhere. Reopen it or save a new copy."
                )
        payload = (json.dumps(job, indent=2, allow_nan=False) + "\n").encode()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".team-job-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if self.path == target:
                os.replace(temporary, target)
            else:
                os.link(temporary, target)
                os.unlink(temporary)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.path, self.version = target, hashlib.sha256(payload).hexdigest()


def upgrade_job(job):
    result = deepcopy(job)
    settings = result.setdefault("job_settings", {})
    if result.get("framing_version", 0) < 2:
        settings["foreground_framing"] = 1.0
        result["base_scale"] = None
        # Template presets must not reintroduce the removed lower-body crop.
        for preset in result.get("template_presets", {}).values():
            if isinstance(preset, dict):
                preset["crop_body_fraction"] = 1.0
                preset["foreground_framing"] = 1.0
    settings["crop_body_fraction"] = 1.0
    settings.setdefault("foreground_framing", 1.0)
    if result.get("framing_version", 0) < 3:
        settings["transparency_mode"] = 1.0
        if settings.get("v_step_in", 0.025) == 0.025:
            settings["v_step_in"] = 0.125
        settings["v_max_drop_in"] = max(settings.get("v_max_drop_in", 0.25), 0.5)
        result["base_scale"] = None
    settings.setdefault("transparency_mode", 1.0)
    if result.get("framing_version", 0) < 4 and settings.get("transparency_mode"):
        result["base_scale"] = None
    result["framing_version"] = 4
    result.setdefault("preview_team_source", "")
    result.setdefault("relaxed_detection", True)
    return result


class ReviewSession:
    def __init__(self, job: dict):
        self.job = upgrade_job(job)
        self.revision = 0
        self.dirty = False

    @property
    def team(self):
        if not self.job["teams"]:
            raise ValueError("Waiting for the first team folder.")
        return self.job["teams"][self.job["selected_team"]]

    @property
    def manifest(self):
        return self.team["manifest"]

    def touch(self):
        self.revision += 1
        self.dirty = True

    def select_team(self, index):
        if not 0 <= index < len(self.job["teams"]):
            raise ValueError("Unknown team.")
        self.job["selected_team"] = index
        self.touch()

    def entry(self, identity):
        return next(e for e in self.manifest["athletes"] if e["id"] == identity)

    def settings(self, values: dict):
        candidate = {**self.job.get("job_settings", {}), **values}
        CompositionSettings(**candidate).validate()
        self.job["job_settings"] = deepcopy(candidate)
        for team in self.job["teams"]:
            team["manifest"]["settings"] = deepcopy(candidate)
        template = (self.team.get("templates") if self.job["teams"] else None) or self.job["templates"]
        self.job["template_presets"][template] = deepcopy(candidate)
        self.touch()

    def confirm_scale(self):
        self.job["retained_capture"] = True
        for entry in self.manifest["athletes"]:
            entry["capture"].update(
                source_scale=1.0,
                geometry_id=self.manifest["team_id"] + ":fixed-capture",
                provenance="Operator confirmed original canvas/scale and common capture setup in review UI.",
            )
            self._height(entry)
        self.touch()

    def _height(self, entry):
        anchors = self.team["anchors"].get(entry["id"], {})
        top, feet = anchors.get("height_top"), anchors.get("floor_y")
        scale = entry["capture"].get("source_scale")
        if top is not None and feet is not None and scale:
            if feet <= top:
                raise ValueError(
                    "The feet anchor must be below the anatomical head top."
                )
            entry["capture"]["relative_height"] = (feet - top) / scale

    def correct_head(self, identity, box):
        from .model import Rect

        Rect(*box)
        self.entry(identity)["manual_head"] = list(box)
        self.touch()

    def anchor(self, identity, kind: str, y: float):
        if kind not in ("height_top", "floor_y") or y < 0:
            raise ValueError("Invalid height anchor.")
        previous = deepcopy(self.team["anchors"].get(identity, {}))
        self.team["anchors"].setdefault(identity, {})[kind] = y
        try:
            self._height(self.entry(identity))
        except ValueError:
            self.team["anchors"][identity] = previous
            raise
        self.touch()

    def request_rows(self, count: int | None):
        active = sum(not e.get("excluded", False) for e in self.manifest["athletes"])
        if count is not None:
            requested_distribution(active, count, ())
            self.team["requested_rows"] = count
        else:
            self.team.pop("requested_rows", None)
        self.manifest.setdefault("corrections", {}).pop("rows", None)
        self.touch()

    def manual_rows(self, counts: tuple[int, ...]):
        active = [e for e in self.manifest["athletes"] if not e.get("excluded", False)]
        validate_rows(counts, len(active))
        if any(e["capture"].get("relative_height") is None for e in active):
            raise ValueError(
                "Confirm athlete height measurements before changing row counts."
            )
        active.sort(key=lambda e: (-e["capture"]["relative_height"], e["id"]))
        rows, start = [], 0
        for count in counts:
            row = [None] * count
            for slot, entry in zip(
                center_out_slots(count), active[start : start + count]
            ):
                row[slot] = entry["id"]
            rows.append(row)
            start += count
        self.manifest.setdefault("corrections", {})["rows"] = rows
        self.team.pop("requested_rows", None)
        self.touch()

    def move(
        self,
        identity,
        rows: list[list[str]],
        target_row: int | None = None,
        direction=0,
    ):
        rows = deepcopy(rows)
        index = next(i for i, row in enumerate(rows) if identity in row)
        slot = rows[index].index(identity)
        if target_row is None:
            other = slot + direction
            if not 0 <= other < len(rows[index]):
                return
            rows[index][slot], rows[index][other] = (
                rows[index][other],
                rows[index][slot],
            )
        else:
            if not 0 <= target_row <= len(rows):
                raise ValueError("Choose an existing row or the next new row.")
            if target_row == index:
                return
            if target_row == len(rows):
                rows.append([])
            if len(rows[target_row]) >= 16:
                raise ValueError("A row cannot contain more than 16 athletes.")
            rows[index].remove(identity)
            rows[target_row].append(identity)
        self.manifest.setdefault("corrections", {})["rows"] = [
            row for row in rows if row
        ]
        self.team.pop("requested_rows", None)
        self.touch()

    def offset(self, identity, x: float, y: float):
        self.manifest.setdefault("corrections", {}).setdefault("offsets_in", {})[
            identity
        ] = [x, y]
        self.touch()

    def rank(self, identity, taller: bool):
        active = [e for e in self.manifest["athletes"] if not e.get("excluded", False)]
        if any(e["capture"].get("relative_height") is None for e in active):
            raise ValueError(
                "Confirm height measurements before adjusting height order."
            )
        active.sort(key=lambda e: (-e["capture"]["relative_height"], e["id"]))
        index = next(i for i, e in enumerate(active) if e["id"] == identity)
        other = index - 1 if taller else index + 1
        if not 0 <= other < len(active):
            return
        a, b = active[index]["capture"], active[other]["capture"]
        a["relative_height"], b["relative_height"] = (
            b["relative_height"],
            a["relative_height"],
        )
        for capture in (a, b):
            capture["provenance"] = (
                capture.get("provenance") or ""
            ) + " Operator overrode height ranking in review UI."
        self.manifest.setdefault("corrections", {}).pop("rows", None)
        self.touch()

    def exclude(self, identity):
        entry = self.entry(identity)
        excluded = not entry.get("excluded", False)
        entry.update(
            excluded=excluded,
            exclusion_reason="Operator excluded incorrect source image"
            if excluded
            else None,
        )
        self.team.pop("requested_rows", None)
        # Keep all source geometry, anchors and offsets for non-destructive restore.
        self.manifest.setdefault("corrections", {}).pop("rows", None)
        self.touch()

    def snapshot(self):
        return deepcopy(self.job)


def rows_from_layout(layout) -> list[list[str]]:
    return [
        [
            p.athlete_id
            for p in sorted(layout.placements, key=lambda p: p.slot)
            if p.row == row
        ]
        for row in range(len(layout.row_counts))
    ]
