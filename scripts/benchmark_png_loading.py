"""Read-only source benchmark; timings are local and no athlete names are emitted.

Run after installing the package:
python scripts/benchmark_png_loading.py --team '/path/to/team' --limit 4
The baseline is the former two-decode geometry/thumbnail flow WITHOUT face detection.
It is a lower-bound comparison, not a benchmark of Apple Vision or another product.
"""
import argparse
import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

from PIL import Image

from pilot_teambuilder.desktop_engine import AssetCache
from pilot_teambuilder.discovery import source_paths
from pilot_teambuilder.measurements import MeasurementCache
from pilot_teambuilder.sources import load_png


def benchmark(folder, limit):
    paths = [p for p in source_paths(folder) if p.suffix.lower() == '.png'][:limit]
    if not paths:
        raise ValueError('No PNG sources found.')
    started = perf_counter()
    pixels = 0
    for path in paths:
        with load_png(path) as source:
            pixels += source.image.width * source.image.height
        with load_png(path) as source:
            with source.image.convert('RGBA') as small:
                small.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
    baseline = perf_counter() - started
    assets = AssetCache()
    measurements = MeasurementCache(asset_sink=assets.prime)
    entries = [SimpleNamespace(path=p) for p in paths]
    try:
        started = perf_counter()
        for entry in entries:
            measurements.measure(entry, None, None, transparency=True)
        cold = perf_counter() - started
        started = perf_counter()
        for entry in entries:
            measurements.measure(entry, None, None, transparency=True)
        warm = perf_counter() - started
        return {'athletes': len(paths), 'megapixels': pixels / 1e6,
                'baseline_two_decode_seconds_without_face_detection': baseline,
                'fast_single_decode_seconds': cold, 'cached_geometry_seconds': warm,
                'loading_speedup': baseline / cold, 'preview_cache_mib': assets.bytes / 1024**2,
                'notes': 'Single local run, warm OS disk cache possible. Preview changes from edge 1000 to 640; final rendering unchanged. Excludes copying-settle wait and final JPEG.'}
    finally:
        assets.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--team', required=True, type=Path)
    parser.add_argument('--limit', default=4, type=int)
    args = parser.parse_args()
    if not 1 <= args.limit <= 256:
        parser.error('limit must be 1–256')
    print(json.dumps(benchmark(args.team, args.limit), indent=2))
