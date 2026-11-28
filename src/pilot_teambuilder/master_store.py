"""Protected local finished masters, separate from consumer-managed export folders."""

import hashlib
import os
import tempfile
from pathlib import Path

from PIL import Image

from .model import FINAL_SIZE


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024**2), b''):
            result.update(block)
    return result.hexdigest()


def verify_jpeg(path, expected=None):
    path = Path(path)
    try:
        if not path.is_file() or (expected and digest(path) != expected):
            return False
        with Image.open(path) as image:
            if image.format != 'JPEG' or image.size != FINAL_SIZE:
                return False
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def install_copy(source, destination, expected):
    """Copy only a verified master; never overwrite any existing file."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if verify_jpeg(destination, expected):
            return
        raise ValueError('A different local master already exists; recovery will retain it separately.')
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.master-', suffix='.jpg', dir=destination.parent)
        with os.fdopen(fd, 'wb') as writer, Path(source).open('rb') as reader:
            for block in iter(lambda: reader.read(1024**2), b''):
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
        if not verify_jpeg(temporary, expected):
            raise ValueError('Finished master copy did not verify.')
        os.link(temporary, destination)
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
