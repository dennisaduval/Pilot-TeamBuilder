"""All-face native Vision adapter. No silent largest-face selection or downloads."""

from dataclasses import dataclass
import hashlib
import json
from math import isfinite
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

from PIL import Image

from .model import Rect

ANALYSIS_EDGE = 1600


@dataclass(frozen=True)
class Face:
    box: Rect
    confidence: float


class DetectionUnavailable(RuntimeError):
    pass


def parse_faces(payload: dict, size: tuple[int, int]) -> tuple[Face, ...]:
    if (payload.get("schema_version") != 1 or payload.get("width") != size[0]
            or payload.get("height") != size[1] or not isinstance(payload.get("faces"), list)):
        raise ValueError("Face helper returned an incompatible coordinate contract.")
    bounds = Rect(0, 0, *size)
    result = []
    for raw in payload["faces"]:
        values = [raw[k] for k in ("x", "y", "width", "height", "confidence")]
        if any(type(v) not in (float, int) or not isfinite(v) for v in values):
            raise ValueError("Invalid face geometry/confidence from helper.")
        x, y, width, height, confidence = values
        box = Rect(x, y, x + width, y + height)
        if not bounds.contains(box, tolerance=0.001) or not 0 <= confidence <= 1:
            raise ValueError("Face helper returned out-of-bounds geometry/confidence.")
        result.append(Face(box, confidence))
    return tuple(result)


class VisionDetector:
    def __init__(self, helper: Path | None = None, cpu_only: bool = False):
        bundle = getattr(sys, "_MEIPASS", None)
        root = Path(bundle) if bundle else Path(__file__).resolve().parents[2]
        self.helper = helper or root / "bin/TeamFaceDetector"
        self.cpu_only = cpu_only

    @property
    def identity(self) -> str:
        if platform.system() != "Darwin" or not self.helper.is_file():
            raise DetectionUnavailable("Mac face detector is unavailable; build the native helper or provide reviewed manual_head rectangles.")
        digest = hashlib.sha256(self.helper.read_bytes()).hexdigest()
        return f"vision-v1:{platform.mac_ver()[0]}:{digest}:cpu={self.cpu_only}"

    def detect(self, image: Image.Image) -> tuple[Face, ...]:
        self.identity
        ratio = min(1.0, ANALYSIS_EDGE / max(image.size))
        size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
        with tempfile.TemporaryDirectory(prefix="pilot-team-detect-") as folder:
            path = Path(folder) / "analysis.png"
            # Neutral matte prevents discarded transparency turning into black hair.
            with image.convert("RGBA") as rgba, rgba.resize(size, Image.Resampling.LANCZOS) as small:
                with Image.new("RGBA", size, (220, 220, 220, 255)) as matte:
                    matte.alpha_composite(small)
                    with matte.convert("RGB") as rgb:
                        rgb.save(path)
            attempts = (True,) if self.cpu_only else (False, True)
            errors = []
            for cpu in attempts:
                try:
                    result = subprocess.run([str(self.helper), str(path)] + (["--cpu"] if cpu else []),
                                            capture_output=True, text=True, check=True, timeout=30)
                    faces = parse_faces(json.loads(result.stdout), size)
                    return tuple(Face(Rect(f.box.left * image.width / size[0],
                                           f.box.top * image.height / size[1],
                                           f.box.right * image.width / size[0],
                                           f.box.bottom * image.height / size[1]), f.confidence)
                                 for f in faces)
                except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError) as error:
                    errors.append(type(error).__name__)
            raise DetectionUnavailable("Native face detection failed, including CPU fallback: " + ", ".join(errors))


def measure_head(image: Image.Image, face: Face) -> tuple[Rect, bool]:
    """Estimate hair top from alpha near a face; flag truncated analysis windows."""
    box = face.box
    region = (max(0, int(box.left - box.width * 0.7)),
              max(0, int(box.top - box.height * 1.5)),
              min(image.width, int(box.right + box.width * 0.7) + 1),
              min(image.height, int(box.bottom) + 1))
    with image.getchannel("A") as alpha, alpha.crop(region) as crop:
        # Tiny alpha residues should not establish hair position.
        with crop.point(lambda v: 255 if v > 8 else 0) as mask:
            visible = mask.getbbox()
    if visible is None:
        raise ValueError("Detected face has no visible alpha silhouette.")
    left, top, right, _ = visible
    head = Rect(min(box.left, region[0] + left), min(box.top, region[1] + top),
                max(box.right, region[0] + right), box.bottom)
    uncertain = top == 0 or head.height > box.height * 2.5
    return head, uncertain
