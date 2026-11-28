"""Bounded disposable raw assets: decode PNG once, verify originals at export."""

import hashlib
import mmap
import tempfile
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from PIL import Image

from .sources import MAX_BYTES, SourceImage, load_png, render_source_box


class RenderSourceCache:
    """One worker, at most 2 GiB on local temporary storage; no full-image RAM cache.

    Transparent margins are omitted from this disposable representation only.
    Original canvas coordinates, alpha, pixel scale and source hashes are retained.
    Evicted assets fall back to verified original PNG loading.
    """

    def __init__(self, budget=2 * 1024**3):
        self.budget = budget
        self.bytes = 0
        self.entries = OrderedDict()
        self.temp = tempfile.TemporaryDirectory(prefix="pilot-teambuilder-render-")
        self.hits = 0

    def prime(self, path, source):
        from .measurements import MeasurementCache
        key = MeasurementCache.signature(path)
        if key in self.entries:
            return
        # Preserve transparent filter support around the silhouette as well.
        box = render_source_box(source.body, source.image.size)
        size = (box[2] - box[0], box[3] - box[1])
        required = size[0] * size[1] * 4
        if required > self.budget:
            return
        while self.entries and self.bytes + required > self.budget:
            _, old = self.entries.popitem(last=False)
            self.bytes -= old[1]
            old[0].unlink(missing_ok=True)
        target = Path(self.temp.name) / (uuid4().hex + ".rgba")
        try:
            with source.image.crop(box) as crop, crop.convert("RGBA") as rgba:
                target.write_bytes(rgba.tobytes())
        except OSError:
            # A full/unavailable temp drive must not prevent original-PNG export.
            target.unlink(missing_ok=True)
            return
        self.entries[key] = (target, required, size, box[:2], source.image.size, source.body, source.sha256)
        self.bytes += required

    @contextmanager
    def load(self, path, expected_hash=None):
        from .measurements import MeasurementCache
        key = MeasurementCache.signature(path)
        entry = self.entries.get(key)
        if entry is None or not entry[0].is_file() or entry[0].stat().st_size != entry[1]:
            with load_png(path) as source:
                yield source
            return
        # Hash the CURRENT PNG bytes before using its decoded representation.
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024**2):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("PNG exceeds the 128-MiB input budget.")
                digest.update(chunk)
        if digest.hexdigest() != entry[6] or (expected_hash and digest.hexdigest() != expected_hash):
            raise ValueError("Source content changed since placement; rebuild this team.")
        if key != MeasurementCache.signature(path):
            raise ValueError("Source changed before rendering; waiting for copying to finish.")
        self.entries.move_to_end(key)
        with entry[0].open("rb") as handle:
            with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
                image = Image.frombuffer("RGBA", entry[2], data, "raw", "RGBA", 0, 1)
                try:
                    self.hits += 1
                    yield SourceImage(image, entry[6], entry[5], entry[4], entry[3])
                finally:
                    image.close()

    def close(self):
        self.entries.clear()
        self.bytes = 0
        self.temp.cleanup()
