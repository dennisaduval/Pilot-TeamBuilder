"""Single-worker preview/export adapter. No Tk calls and no source modification."""

from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path
from queue import Empty, Queue, SimpleQueue
from threading import Thread

from PIL import Image

from .analysis import analyze_team
from .detection import VisionDetector
from .detection_cache import DetectionCache
from .identification import JobProfile, draw_right_footer
from .manifests import manifest_from_data
from .measurements import MeasurementCache
from .rendering import TemplateCache, discover_templates, render
from .review import fingerprint, requested_distribution
from .rows import RowTable
from .sources import load_png


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
        team = job["teams"][job["selected_team"]]
        manifest = manifest_from_data(team["manifest"], Path.cwd())
        table = RowTable.load(
            Path(job["row_csv"])
            if job["row_csv"]
            else Path(__file__).parent / "data/row_counts.csv"
        )
        count = sum(not e.excluded for e in manifest.entries)
        if team.get("requested_rows") is not None and count:
            table = RowTable({count: requested_distribution(count, team["requested_rows"], ())})
        result = analyze_team(
            manifest, table, self.detector, self.disk, refresh, self.measurements
        )
        template_issue = ""
        template_ready = False
        template_versions = []
        layers = None
        try:
            bg, fg = discover_templates(Path(team.get("templates") or job["templates"]))
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
                    b = asset.bounds
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
                        panorama.alpha_composite(
                            small,
                            (
                                round((p.x + b.left * p.scale) * 0.2),
                                round((p.y + b.top * p.scale) * 0.2),
                            ),
                        )
                if layers:
                    panorama.alpha_composite(layers[1])
                draw_right_footer(panorama, manifest.team_name, JobProfile())
            except Exception:
                panorama.close()
                raise
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
        )

    def export(self, job: dict, expected_signature: str, destination: Path):
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
            bg, fg = discover_templates(Path(team.get("templates") or job["templates"]))
            return render(
                view.analysis.layout,
                view.analysis.sources,
                bg,
                fg,
                team["manifest"]["team_name"],
                destination,
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
