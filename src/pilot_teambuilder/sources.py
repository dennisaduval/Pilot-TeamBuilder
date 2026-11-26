"""Bounded, immutable PNG snapshots shared by analysis and final rendering."""

from dataclasses import dataclass
import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from .model import Rect

MAX_BYTES = 128 * 1024 * 1024
MAX_PIXELS = 64_000_000


@dataclass
class SourceImage:
    image: Image.Image
    sha256: str
    body: Rect

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.image.close()


def load_png(path: Path) -> SourceImage:
    """Hash and decode the same bytes, so changing source files cannot mix versions."""
    if path.is_symlink():
        raise ValueError("Symbolic-link images are not supported.")
    with path.open("rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("PNG exceeds the 128-MiB input budget.")
    try:
        with Image.open(BytesIO(data)) as opened:
            if opened.format != "PNG" or "A" not in opened.getbands():
                raise ValueError("Expected an extracted PNG with an alpha channel.")
            if opened.width * opened.height > MAX_PIXELS:
                raise ValueError("PNG exceeds the 64-megapixel decoded image budget.")
            if getattr(opened, "n_frames", 1) != 1:
                raise ValueError("Animated PNGs are not supported.")
            image = ImageOps.exif_transpose(opened)
    except Image.DecompressionBombError as error:
        raise ValueError("PNG exceeds the safe decoded image budget.") from error
    try:
        with image.getchannel("A") as alpha:
            bounds = alpha.getbbox()
        if bounds is None:
            raise ValueError("PNG contains no visible athlete pixels.")
        return SourceImage(image, hashlib.sha256(data).hexdigest(), Rect(*bounds))
    except Exception:
        image.close()
        raise
