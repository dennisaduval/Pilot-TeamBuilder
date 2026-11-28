"""Bounded template caching and serial full-resolution compositing."""

import os
import re
import tempfile
from collections import OrderedDict
from contextlib import closing
from pathlib import Path
from time import perf_counter

from PIL import Image, ImageOps

from .identification import JobProfile, draw_right_footer
from .measurements import MeasurementCache
from .model import ART_SIZE, FINAL_SIZE, Layout
from .sources import load_png, render_source_box


def discover_templates(root: Path) -> tuple[Path, Path]:
    layers = {}
    pattern = re.compile(r"^Team[ _-]+(Background|Foreground)_default$", re.I)
    for path in sorted(root.iterdir()):
        match = pattern.fullmatch(path.stem)
        if not match or not path.is_file():
            continue
        role = match[1].lower()
        if path.suffix.lower() not in ({".jpg", ".jpeg"} if role == "background" else {".png"}):
            continue
        if role in layers:
            raise ValueError(f"Duplicate default team {role}.")
        layers[role] = path
    if set(layers) != {"background", "foreground"}:
        raise ValueError("Select a Team Background_default.jpg and Team Foreground_default.png.")
    return layers["background"], layers["foreground"]


def template_artwork_box(size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Normalize the artwork region, preserving the right-hand print allowance.

    Templates may describe either the 24 × 8 artwork or the 26 × 8 print sheet.
    Small rounding differences from image exports are accepted; DPI metadata is
    irrelevant to the aspect ratio. The rightmost 2/26 of a full sheet is never
    stretched into the artwork. Identification is drawn separately by the app.
    """
    width, height = size
    canvas = min((ART_SIZE, FINAL_SIZE), key=lambda ref: abs(width - height * ref[0] / ref[1]))
    ratio = canvas[0] / canvas[1]
    # Allow one source pixel of rounding in either dimension.
    if abs(width - height * ratio) <= 1 + ratio:
        right = width if canvas == ART_SIZE else round(width * ART_SIZE[0] / FINAL_SIZE[0])
        return (0, 0, right, height)
    raise ValueError(
        f"Template is {width} × {height} px. Select landscape 24 × 8-inch artwork "
        "or a 26 × 8-inch print template (for example 7200 × 2400 or 7800 × 2400 px). "
        "The rightmost 2-inch strip is reserved for identification."
    )


class TemplateCache:
    """Two decoded artwork layers, owned by one rendering worker."""

    def __init__(self):
        self.layers = OrderedDict()

    def load(self, path: Path, foreground: bool, size=ART_SIZE) -> Image.Image:
        stat = path.stat()
        key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, foreground, size)
        if key in self.layers:
            self.layers.move_to_end(key)
            return self.layers[key]
        with Image.open(path) as opened, closing(ImageOps.exif_transpose(opened)) as oriented:
            artwork_box = template_artwork_box(oriented.size)
            with closing(oriented.crop(artwork_box)) as artwork:
                if foreground:
                    if opened.format != "PNG" or "A" not in artwork.getbands():
                        raise ValueError("Foreground must be an alpha-channel PNG.")
                    with closing(artwork.getchannel("A")) as alpha:
                        if alpha.getextrema()[0] == 255:
                            raise ValueError("Foreground artwork has no transparent pixels.")
                elif opened.format != "JPEG":
                    raise ValueError("Background must be JPEG.")
                converted = artwork.convert("RGBA")
                try:
                    layer = converted.resize(size, Image.Resampling.LANCZOS)
                finally:
                    converted.close()
        after = path.stat()
        if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            layer.close()
            raise ValueError("Template changed while decoding; select it again after copying finishes.")
        self.layers[key] = layer
        while len(self.layers) > 2:
            self.layers.popitem(last=False)[1].close()
        return layer

    def close(self):
        for image in self.layers.values():
            image.close()
        self.layers.clear()


def composite_subject(canvas, image, position, clip=None):
    """Shared preview/final clipping. Templates and identification are unaffected."""
    x, y = position
    left = max(0, x, round(clip.left) if clip else 0)
    top = max(0, y, round(clip.top) if clip else 0)
    right = min(canvas.width, x + image.width, round(clip.right) if clip else canvas.width)
    bottom = min(canvas.height, y + image.height, round(clip.bottom) if clip else canvas.height)
    if right > left and bottom > top:
        with closing(image.crop((left - x, top - y, right - x, bottom - y))) as part:
            canvas.alpha_composite(part, (left, top))


def composite_scaled_subject(canvas, image, bounds, scale, x, y, clip=None):
    """Resample only the part reaching the artwork; keep original PNGs intact."""
    width, height = max(1, round(bounds.width * scale)), max(1, round(bounds.height * scale))
    px, py = round(x + bounds.left * scale), round(y + bounds.top * scale)
    left = max(0, px, round(clip.left) if clip else 0)
    top = max(0, py, round(clip.top) if clip else 0)
    right = min(canvas.width, px + width, round(clip.right) if clip else canvas.width)
    bottom = min(canvas.height, py + height, round(clip.bottom) if clip else canvas.height)
    if right <= left or bottom <= top:
        return
    box = (bounds.left + (left - px) * bounds.width / width,
           bounds.top + (top - py) * bounds.height / height,
           bounds.left + (right - px) * bounds.width / width,
           bounds.top + (bottom - py) * bounds.height / height)
    with closing(image.resize((right - left, bottom - top), Image.Resampling.LANCZOS, box=box)) as region:
        with closing(region.convert("RGBA")) as rgba:
            canvas.alpha_composite(rgba, (left, top))


def render(layout: Layout, sources: dict[str, Path], background: Path, foreground: Path,
           team_name: str, destination: Path, cache: TemplateCache | None = None,
           font: str = "", expected_hashes: dict[str, str] | None = None,
           expected_templates: dict[str, tuple] | None = None, before_publish=None, source_cache=None) -> dict:
    """Render one draft/final artifact. Issues must block routing, not preview.

    Exclusive publication refuses overwrites. This milestone has no printer or
    hotfolder side effects. The caller owns any supplied cache.
    """
    if not team_name.strip() or len(team_name) > 240 or any(ord(c) < 32 for c in team_name):
        raise ValueError("Team name must contain 1–240 printable characters.")
    if set(sources) != {p.athlete_id for p in layout.placements}:
        raise ValueError("Source mapping must match the included athlete IDs exactly.")
    if expected_hashes is not None and set(expected_hashes) != set(sources):
        raise ValueError("Expected source hashes must match all included athlete IDs.")
    if destination.exists():
        raise FileExistsError(destination)
    own_cache = cache is None
    cache = cache or TemplateCache()
    start = perf_counter()
    timings = {}
    initial_cache_hits = source_cache.hits if source_cache else 0
    temporary = None
    def verify_templates():
        if expected_templates is not None:
            for path, expected in expected_templates.items():
                if MeasurementCache.signature(Path(path)) != expected:
                    raise ValueError("Template changed since review; rebuild the preview before exporting.")
    try:
        verify_templates()
        bg, fg = cache.load(background, False), cache.load(foreground, True)
        timings["templates_seconds"] = perf_counter() - start
        with closing(Image.new("RGBA", FINAL_SIZE, "white")) as canvas:
            canvas.paste(bg, (0, 0))
            last = perf_counter()
            for p in layout.placements:
                expected = expected_hashes.get(p.athlete_id) if expected_hashes else None
                loader = source_cache.load(sources[p.athlete_id], expected) if source_cache else load_png(sources[p.athlete_id])
                with loader as source:
                    image = source.image
                    if expected_hashes is not None and source.sha256 != expected_hashes[p.athlete_id]:
                        raise ValueError(f"{p.athlete_id}: source content changed since analysis; rebuild.")
                    if source.size != p.source_size:
                        raise ValueError(f"{p.athlete_id}: source dimensions changed; rebuild layout.")
                    if not p.source_body.contains(source.body, tolerance=1):
                        raise ValueError(f"{p.athlete_id}: alpha bounds differ from layout; rebuild geometry.")
                    bounds = p.source_visible or source.body
                    if not source.body.contains(bounds):
                        raise ValueError("Visible crop exceeds the original source bounds.")
                    cropped = None
                    ox, oy = source.origin
                    if source.original_size is None:
                        box = render_source_box(source.body, source.size)
                        cropped = image.crop(box)
                        image, (ox, oy) = cropped, box[:2]
                    try:
                        from .model import Rect
                        local_bounds = Rect(bounds.left - ox, bounds.top - oy, bounds.right - ox, bounds.bottom - oy)
                        composite_scaled_subject(canvas, image, local_bounds, p.scale, p.x + ox * p.scale, p.y + oy * p.scale, layout.clip)
                    finally:
                        if cropped is not None:
                            cropped.close()
            canvas.alpha_composite(fg, (0, 0))
            timings["composite_seconds"] = perf_counter() - last
            draw_right_footer(canvas, team_name, JobProfile(footer_font=font))
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".team-", suffix=".jpg", dir=destination.parent)
            with os.fdopen(fd, "wb") as handle, closing(canvas.convert("RGB")) as rgb:
                last = perf_counter()
                rgb.save(handle, "JPEG", quality=95, subsampling=0, dpi=(300, 300), optimize=True)
                handle.flush()
                os.fsync(handle.fileno())
                timings["jpeg_seconds"] = perf_counter() - last
        with Image.open(temporary) as check:
            check.load()
            if check.size != FINAL_SIZE or check.format != "JPEG":
                raise ValueError("Output verification failed.")
        # Hard-link publication is atomic and refuses replacing an existing
        # file. Unsupported network filesystems fail explicitly for later retry.
        verify_templates()
        if before_publish is not None:
            before_publish()
        os.link(temporary, destination)
        timings["total_seconds"] = perf_counter() - start
        return {"size": FINAL_SIZE, "ppi": 300, "timings": timings,
                "source_cache_hits": source_cache.hits - initial_cache_hits if source_cache else 0,
                "review_required": bool(layout.issues), "issues": layout.issues}
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
        if own_cache:
            cache.close()
