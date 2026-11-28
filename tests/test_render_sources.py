"""Disposable raw assets preserve coordinates and never bypass source verification."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops

from pilot_teambuilder.render_sources import RenderSourceCache
from pilot_teambuilder.sources import load_png
from pilot_teambuilder.synthetic import athletes, write_sources


class RenderSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = list(write_sources(self.root, athletes(3)).values())
        self.cache = RenderSourceCache(budget=2 * 1024**2)
        self.addCleanup(self.cache.close)

    def test_cached_pixels_and_canvas_coordinates_are_identical(self):
        for path in self.paths:
            with load_png(path) as original:
                self.cache.prime(path, original)
                with patch('pilot_teambuilder.render_sources.load_png', side_effect=AssertionError('Second PNG decode')):
                    with self.cache.load(path, original.sha256) as cached:
                        self.assertEqual(cached.size, original.image.size)
                        self.assertEqual(cached.body, original.body)
                        x, y = cached.origin
                        with original.image.crop((x, y, x + cached.image.width, y + cached.image.height)) as crop:
                            with ImageChops.difference(crop, cached.image) as diff:
                                self.assertIsNone(diff.getbbox())
        self.assertEqual(self.cache.hits, 3)

    def test_cache_is_bounded_and_eviction_falls_back_to_original(self):
        small = RenderSourceCache(budget=800000)
        self.addCleanup(small.close)
        for path in self.paths:
            with load_png(path) as original:
                small.prime(path, original)
            self.assertLessEqual(small.bytes, small.budget)
        with small.load(self.paths[0]) as source:
            self.assertEqual(source.size, (400, 1000))

    def test_changed_original_is_rejected_even_when_a_decoded_asset_exists(self):
        path = self.paths[0]
        with load_png(path) as original:
            self.cache.prime(path, original)
            expected = original.sha256
        # Force the cached-version branch so that content validation is exercised.
        from pilot_teambuilder.measurements import MeasurementCache
        key = MeasurementCache.signature(path)
        with Image.open(path) as opened:
            changed = opened.copy()
        with changed:
            changed.putpixel((200, 900), (255, 0, 0, 255))
            changed.save(path)
        with patch.object(MeasurementCache, 'signature', return_value=key):
            with self.assertRaisesRegex(ValueError, 'Source content changed'):
                with self.cache.load(path, expected):
                    pass

    def test_close_removes_disposable_files(self):
        cache = RenderSourceCache()
        with load_png(self.paths[0]) as source:
            cache.prime(self.paths[0], source)
        location = Path(cache.temp.name)
        self.assertTrue(list(location.iterdir()))
        cache.close()
        self.assertFalse(location.exists())
