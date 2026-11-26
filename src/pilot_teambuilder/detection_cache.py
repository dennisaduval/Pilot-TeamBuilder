"""Durable content/model-version keyed detection cache; no image binaries stored."""

import hashlib
import json
from pathlib import Path
import sqlite3

from .detection import Face, parse_faces


class DetectionCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError("Unsupported detection-cache schema version.")
        self.db.execute("CREATE TABLE IF NOT EXISTS detections (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self.db.execute("PRAGMA user_version=1")
        self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    @staticmethod
    def key(source_hash: str, detector_identity: str) -> str:
        # Bump preprocessing revision when alpha matte or analysis size changes.
        return hashlib.sha256(json.dumps([source_hash, detector_identity,
                                          "resizeRGBA-matte220-edge1600-orient-v2"]).encode()).hexdigest()

    def get(self, key: str, size: tuple[int, int]) -> tuple[Face, ...] | None:
        row = self.db.execute("SELECT payload FROM detections WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return parse_faces(json.loads(row[0]), size)
        except (ValueError, TypeError, KeyError, AttributeError):
            with self.db:
                self.db.execute("DELETE FROM detections WHERE key=?", (key,))
            return None

    def put(self, key: str, size: tuple[int, int], faces: tuple[Face, ...]):
        payload = {"schema_version": 1, "width": size[0], "height": size[1], "faces": [
            {"x": f.box.left, "y": f.box.top, "width": f.box.width,
             "height": f.box.height, "confidence": f.confidence} for f in faces]}
        parse_faces(payload, size)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO detections VALUES (?, ?)", (key, json.dumps(payload)))
