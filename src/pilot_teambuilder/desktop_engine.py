"""Single-worker preview/export adapter. No Tk calls and no source modification."""

from collections import OrderedDict
from dataclasses import asdict, dataclass, replace
from math import ceil, sqrt
from pathlib import Path
from queue import Empty, Queue, SimpleQueue
from threading import Thread

from PIL import Image, ImageChops, ImageDraw, ImageOps

from .analysis import analyze_team
from .composition import transform
from .detection import VisionDetector
from .detection_cache import DetectionCache
from .identification import JobProfile, draw_right_footer
from .manifests import manifest_from_data
from .measurements import MeasurementCache
from .rendering import TemplateCache, composite_subject, render
from .review import fingerprint, requested_distribution, upgrade_job
from .rows import RowTable
from .sources import load_png
from .templates import select_templates


@dataclass
class PreviewAsset:
    image: Image.Image
    source_size: tuple[int, int]
    bounds: object
    sha256: str


class AssetCache:
    def __init__(self, budget=64 * 1024 * 1024):
        if budget < 4:
            raise ValueError("Preview cache budget must hold at least one RGBA pixel.")
        self.budget = budget
        self.items = OrderedDict()
        self.bytes = 0
        self.decodes = 0

    def get(self, path, expected_hash=None):
        key = MeasurementCache.signature(path)
        if key not in self.items:
            with load_png(path) as source:
                small = source.image.convert("RGBA")
                edge = min(1000, int((self.budget / 4) ** 0.5))
                small.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                asset = PreviewAsset(
                    small, source.image.size, source.body, source.sha256
                )
            self.decodes += 1
            size = small.width * small.height * 4
            while self.items and self.bytes + size > self.budget:
                _, old = self.items.popitem(last=False)
                self.bytes -= old.image.width * old.image.height * 4
                old.image.close()
            self.items[key] = asset
            self.bytes += size
        self.items.move_to_end(key)
        asset = self.items[key]
        if expected_hash is not None and asset.sha256 != expected_hash:
            raise ValueError("Source changed since analysis. Analyze again.")
        return asset

    def close(self):
        for asset in self.items.values():
            asset.image.close()
        self.items.clear()
        self.bytes = 0


@dataclass
class ViewResult:
    analysis: object
    panorama: Image.Image | None
    portrait: Image.Image | None
    portrait_size: tuple[int, int] | None
    templates_ready: bool
    template_issue: str
    review_signature: str
    template_names: str = ""
    draft: bool = False

    def close(self):
        for image in (self.panorama, self.portrait):
            if image is not None:
                image.close()


class DesktopEngine:
    def __init__(self, cache_path: Path, detector=None):
        self.disk = DetectionCache(cache_path)
        self.detector = detector or VisionDetector()
        self.measurements = MeasurementCache()
        self.assets = AssetCache()
        self.templates = TemplateCache()

    def close(self):
        self.assets.close()
        self.templates.close()
        self.disk.db.close()

    def view(self, job: dict, selected_id: str | None, refresh=False):
        job = upgrade_job(job)
        team = job["teams"][job["selected_team"]]
        data = dict(team["manifest"])
        if job.get("job_settings"):
            data["settings"] = dict(job["job_settings"])
        manifest = manifest_from_data(data, Path.cwd())
        table = RowTable.load(
            Path(job["row_csv"])
            if job["row_csv"]
            else Path(__file__).parent / "data/row_counts.csv"
        )
        count = sum(not e.excluded for e in manifest.entries)
        if team.get("requested_rows") is not None and count:
            table = RowTable({count: requested_distribution(count, team["requested_rows"], ())})
        result = analyze_team(
            manifest, table, self.detector, self.disk, refresh, self.measurements,
            retained_capture=job.get("retained_capture", True),
            capture_canvas=job.get("capture_canvas"), fixed_scale=job.get("base_scale"),
            min_confidence=0.5 if job.get("relaxed_detection", True) else 0.8,
            relaxed_heads=job.get("relaxed_detection", True),
        )
        for record in result.report["athletes"]:
            expected = team.get("review_hashes", {}).get(record["id"])
            if expected and record["status"] != "excluded" and record.get("sha256") != expected:
                record["issues"].append("Source changed after manual correction; review its head/height again.")
                record["status"] = "review"
                result.layout = None
                result.report["layout"] = None
                result.report["status"] = "review"
                result.report["issues"].append("A corrected source changed; automatic processing is held.")
        if result.layout is not None:
            self._visibility(result)
        template_names = ""
        template_issue = ""
        template_ready = False
        template_versions = []
        layers = None
        try:
            bg, fg, variant = select_templates(job, team)
            template_names = f"{variant.title()} · {bg.name} + {fg.name}"
            template_versions = [
                MeasurementCache.signature(bg),
                MeasurementCache.signature(fg),
            ]
            layers = (
                self.templates.load(bg, False, (1440, 480)),
                self.templates.load(fg, True, (1440, 480)),
            )
            template_ready = True
        except (OSError, ValueError) as error:
            template_issue = str(error)
        panorama = None
        if result.layout is not None:
            panorama = Image.new("RGBA", (1560, 480), "#0B1F3A")
            try:
                if layers:
                    panorama.paste(layers[0], (0, 0))
                for p in result.layout.placements:
                    asset = self.assets.get(
                        result.sources[p.athlete_id], result.hashes[p.athlete_id]
                    )
                    b = p.source_visible or asset.bounds
                    sx, sy = (
                        asset.image.width / asset.source_size[0],
                        asset.image.height / asset.source_size[1],
                    )
                    box = (
                        int(b.left * sx),
                        int(b.top * sy),
                        min(asset.image.width, int(b.right * sx) + 1),
                        min(asset.image.height, int(b.bottom * sy) + 1),
                    )
                    size = (
                        max(1, round(b.width * p.scale * 0.2)),
                        max(1, round(b.height * p.scale * 0.2)),
                    )
                    with (
                        asset.image.crop(box) as crop,
                        crop.resize(size, Image.Resampling.LANCZOS) as small,
                    ):
                        clip = result.layout.clip
                        if clip:
                            clip = transform(clip, 0.2, 0, 0)
                        composite_subject(panorama, small,
                            (round((p.x + b.left * p.scale) * 0.2), round((p.y + b.top * p.scale) * 0.2)), clip)
                if layers:
                    panorama.alpha_composite(layers[1])
                draw_right_footer(panorama, manifest.team_name, JobProfile())
            except Exception:
                panorama.close()
                raise
        draft = result.layout is None
        if draft:
            panorama = self._source_sheet(manifest, layers)
        portrait, portrait_size = None, None
        if selected_id:
            entry = next(e for e in manifest.entries if e.id == selected_id)
            try:
                asset = self.assets.get(entry.path)
                portrait, portrait_size = asset.image.copy(), asset.source_size
            except (OSError, ValueError):
                pass  # The per-athlete report contains the failed input.
        signature = fingerprint(
            {
                "manifest": team["manifest"],
                "hashes": result.hashes,
                "layout": asdict(result.layout) if result.layout else None,
                "templates": template_versions,
            }
        )
        return ViewResult(
            result,
            panorama,
            portrait,
            portrait_size,
            template_ready,
            template_issue,
            signature,
            template_names,
            draft,
        )

    def _source_sheet(self, manifest, layers):
        """Show every source during exceptional preflight instead of an empty screen."""
        canvas = Image.new("RGBA", (1560, 480), "#0B1F3A")
        if layers:
            canvas.paste(layers[0], (0, 0))
        draw = ImageDraw.Draw(canvas)
        entries = [e for e in manifest.entries if not e.excluded]
        columns = max(1, ceil(sqrt(len(entries) * 3)))
        rows = max(1, ceil(len(entries) / columns))
        cw, ch = 1440 / columns, 450 / rows
        for i, entry in enumerate(entries):
            x, y = int(i % columns * cw), int(i // columns * ch + 25)
            try:
                asset = self.assets.get(entry.path)
                with ImageOps.contain(asset.image, (max(1, int(cw) - 8), max(1, int(ch) - 18))) as small:
                    canvas.alpha_composite(small, (x + (int(cw) - small.width) // 2, y))
            except (OSError, ValueError):
                draw.text((x + 4, y + 10), "Cannot read image", fill="white")
            draw.text((x + 4, int(y + ch - 16)), entry.relative_path[:22], fill="white")
        if layers:
            canvas.alpha_composite(layers[1])
        draw.rectangle((0, 0, 1440, 23), fill="#0B1F3A")
        draw.text((12, 5), "SOURCE OVERVIEW — detection needs attention; not a finished team layout", fill="white")
        draw_right_footer(canvas, manifest.team_name, JobProfile())
        return canvas

    def _visibility(self, result):
        """Check anatomical face coverage against actual foreground alpha, not body boxes."""
        placements = result.layout.placements
        records = {r["id"]: r for r in result.report["athletes"]}
        issues = [i for i in result.layout.issues if "possible face obstruction by" not in i]
        for index, back in enumerate(placements):
            observed = records[back.athlete_id].get("faces", [])
            if len(observed) == 1:
                from .model import Rect
                b = observed[0]["box"]
                face = transform(Rect(**b), back.scale, back.x, back.y)
            else:
                face = back.head
            mask = Image.new("L", (64, 64))
            try:
                for front in placements[index + 1:]:
                    if not face.overlaps(front.body):
                        continue
                    asset = self.assets.get(result.sources[front.athlete_id])
                    sx = asset.image.width / asset.source_size[0] / front.scale
                    sy = asset.image.height / asset.source_size[1] / front.scale
                    with asset.image.getchannel("A") as alpha:
                        with alpha.transform((64, 64), Image.Transform.AFFINE,
                            (face.width / 64 * sx, 0, (face.left - front.x) * sx,
                             0, face.height / 64 * sy, (face.top - front.y) * sy),
                            resample=Image.Resampling.BILINEAR) as covered:
                            combined = ImageChops.lighter(mask, covered)
                    mask.close()
                    mask = combined
                histogram = mask.histogram()
                if sum(histogram[32:]) / 4096 > 0.12:
                    issues.append(f"{back.athlete_id}: face is obscured; increase row separation or adjust placement.")
            finally:
                mask.close()
        result.layout = replace(result.layout, issues=tuple(issues))
        result.report["issues"] = issues
        result.report["layout"] = asdict(result.layout)
        result.report["status"] = "review" if issues else "ready"

    def export(self, job: dict, expected_signature: str, destination: Path, before_publish=None):
        # Fresh source preflight and final source hash verification happen on this
        # worker. UI edits are locked until this deliberate export finishes.
        view = self.view(job, None)
        try:
            if view.analysis.layout is None or not view.templates_ready:
                raise ValueError(
                    "Resolve source review issues and choose valid templates before exporting."
                )
            if any(
                "outside athlete placement area" in issue
                for issue in view.analysis.layout.issues
            ):
                raise ValueError(
                    "Move athletes inside the placement area before exporting."
                )
            if view.review_signature != expected_signature:
                raise ValueError(
                    "Sources, templates or layout changed after preview. Analyze and review them again."
                )
            team = job["teams"][job["selected_team"]]
            bg, fg, variant = select_templates(job, team)
            return render(
                view.analysis.layout,
                view.analysis.sources,
                bg,
                fg,
                team["manifest"]["team_name"],
                destination,
                before_publish=before_publish,
                expected_hashes=view.analysis.hashes,
                expected_templates={
                    str(p): MeasurementCache.signature(p) for p in (bg, fg)
                },
            )
        finally:
            view.close()


class LatestWorker:
    """One running task plus one latest pending task. Never touches Tk objects."""

    def __init__(self, factory):
        self.factory = factory
        self.tasks = Queue(maxsize=1)
        self.results = SimpleQueue()
        self.stopped = False
        self.thread = Thread(target=self._run, name="teambuilder-preview", daemon=True)
        self.thread.start()

    def submit(self, token, operation):
        try:
            self.tasks.get_nowait()
        except Empty:
            pass
        self.tasks.put_nowait((token, operation))

    def _run(self):
        engine = None
        try:
            while True:
                item = self.tasks.get()
                if item is None:
                    break
                token, operation = item
                try:
                    if engine is None:
                        engine = self.factory()
                    value = operation(engine)
                    if self.stopped and hasattr(value, "close"):
                        value.close()
                    else:
                        self.results.put((token, value, None))
                except Exception as error:
                    self.results.put((token, None, str(error)))
        finally:
            if engine is not None:
                engine.close()

    def close(self):
        self.stopped = True
        try:
            self.tasks.get_nowait()
        except Empty:
            pass
        self.tasks.put_nowait(None)
        while not self.results.empty():
            _, value, _ = self.results.get()
            if hasattr(value, "close"):
                value.close()
