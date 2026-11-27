"""Small per-worker source measurements; layout edits never retain full PNGs."""

from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path

from .detection import measure_head
from .model import Rect
from .sources import load_png


@dataclass(frozen=True)
class Measurement:
    size: tuple[int, int]
    body: Rect
    sha256: str
    head: Rect | None = None
    confidence: float = 1.0
    faces: tuple = ()
    issues: tuple[str, ...] = ()
    method: str = "reviewed-manual"
    cache_hit: bool = False


class MeasurementCache:
    """Bounded geometry only. File version and detector identity invalidate it."""

    def __init__(self, capacity=256):
        self.capacity = capacity
        self.entries = OrderedDict()

    @staticmethod
    def signature(path: Path):
        stat = path.stat()
        return (str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def measure(self, entry, detector, disk_cache, refresh=False):
        signature = self.signature(entry.path)
        # Resolve identity only for automatic measurements. Manual corrections
        # remain usable when the native helper is unavailable.
        identity = None
        identity_error = None
        if entry.manual_head is None:
            try:
                identity = detector.identity
            except RuntimeError as error:
                identity_error = str(error)
        cached = None if refresh else self.entries.get(signature)
        if cached:
            base, auto = cached
            self.entries.move_to_end(signature)
            if entry.manual_head is not None:
                return replace(base, head=entry.manual_head)
            if identity_error:
                return replace(base, issues=(identity_error,), method="unavailable")
            if auto is not None and auto.method == identity:
                return replace(auto, cache_hit=True)
        else:
            base, auto = None, None
        with load_png(entry.path) as source:
            base = Measurement(source.image.size, source.body, source.sha256)
            result = replace(base, head=entry.manual_head)
            if entry.manual_head is None:
                if identity_error:
                    result = replace(
                        base, issues=(identity_error,), method="unavailable"
                    )
                else:
                    key = disk_cache.key(source.sha256, identity)
                    faces = None if refresh else disk_cache.get(key, source.image.size)
                    hit = faces is not None
                    if faces is None:
                        faces = detector.detect(source.image)
                        disk_cache.put(key, source.image.size, faces)
                    errors = []
                    head, confidence = None, 1.0
                    if len(faces) != 1:
                        errors.append(
                            f"Detected {len(faces)} faces; expected one. Correct detection or explicitly exclude this image."
                        )
                    else:
                        confidence = faces[0].confidence
                        if confidence < 0.8:
                            errors.append("Face detection confidence is below 0.8.")
                        head, uncertain = measure_head(source.image, faces[0])
                        if uncertain:
                            errors.append(
                                "Head/hair silhouette reaches the analysis window; review head bounds."
                            )
                    auto = result = replace(
                        base,
                        head=head,
                        confidence=confidence,
                        faces=faces,
                        issues=tuple(errors),
                        method=identity,
                        cache_hit=hit,
                    )
            if self.signature(entry.path) != signature:
                raise ValueError(
                    "Source changed while measuring; analyze again when copying finishes."
                )
        self.entries[signature] = (base, auto)
        self.entries.move_to_end(signature)
        while len(self.entries) > self.capacity:
            self.entries.popitem(last=False)
        return result
