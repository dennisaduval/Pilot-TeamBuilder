"""Desktop review model and durable jobs, independent of Tk and image rendering."""

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path

from .composition import center_out_slots
from .discovery import is_system_entry, png_paths
from .manifests import inventory, manifest_from_data
from .model import CompositionSettings
from .rows import validate_rows


def fingerprint(data) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def new_job(incoming: Path, name: str | None = None) -> dict:
    incoming = incoming.resolve(strict=True)
    folders = (
        [incoming]
        if any(
            p.is_file()
            and p.suffix.lower() == ".png"
            and not is_system_entry(Path(p.name))
            for p in incoming.iterdir()
        )
        else [
            p
            for p in sorted(incoming.iterdir())
            if p.is_dir()
            and not p.is_symlink()
            and not is_system_entry(Path(p.name))
            and p.name not in {"Templates", "Completed", "Upload", "Samples"}
            and png_paths(p)
        ]
    )
    if not folders:
        raise ValueError(
            "Choose a team folder or the incoming folder containing your team folders."
        )
    teams = []
    for folder in folders:
        manifest = inventory(folder)
        manifest["team_name"] = re.sub(r"^\d+[_ -]+", "", folder.name)
        teams.append(
            {"manifest": manifest, "anchors": {}, "templates": "", "exports": []}
        )
    return {
        "schema_version": 1,
        "name": name or incoming.name,
        "incoming": str(incoming),
        "templates": str(incoming.parent / "Templates/Team"),
        "output": str(incoming.parent / "Completed Teams"),
        "row_csv": "",
        "template_presets": {},
        "teams": teams,
        "selected_team": 0,
    }


def validate_job(job: dict, base_dir: Path):
    if (
        not isinstance(job, dict)
        or type(job.get("schema_version")) is not int
        or job.get("schema_version") != 1
        or not isinstance(job.get("teams"), list)
        or not job["teams"]
    ):
        raise ValueError("Unsupported or empty TeamBuilder job.")
    for key in ("name", "incoming", "templates", "output", "row_csv"):
        if not isinstance(job.get(key), str):
            raise ValueError(f"Invalid job {key}.")
    if not isinstance(job.get("template_presets"), dict):
        raise ValueError("Invalid template settings in saved job.")
    if Path(job["output"]).resolve().is_relative_to(Path(job["incoming"]).resolve()):
        raise ValueError("Completed images must be outside the incoming source folder.")
    for team in job["teams"]:
        if not isinstance(team, dict) or not isinstance(team.get("manifest"), dict):
            raise ValueError("Invalid team in saved job.")
        manifest = manifest_from_data(team["manifest"], base_dir)
        if Path(job["output"]).resolve().is_relative_to(manifest.source_folder):
            raise ValueError(
                "Completed images must be outside every team's source folder."
            )
        if not isinstance(team.get("anchors"), dict) or not isinstance(
            team.get("exports"), list
        ):
            raise ValueError("Invalid saved team review state.")
    if type(job.get("selected_team")) is not int or not 0 <= job["selected_team"] < len(
        job["teams"]
    ):
        raise ValueError("Invalid selected team in job.")
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


class ReviewSession:
    def __init__(self, job: dict):
        self.job = deepcopy(job)
        self.revision = 0
        self.dirty = False

    @property
    def team(self):
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
        candidate = {**self.manifest.get("settings", {}), **values}
        CompositionSettings(**candidate).validate()
        self.manifest["settings"] = candidate
        template = self.team.get("templates") or self.job["templates"]
        self.job["template_presets"][template] = deepcopy(candidate)
        self.touch()

    def confirm_scale(self):
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
