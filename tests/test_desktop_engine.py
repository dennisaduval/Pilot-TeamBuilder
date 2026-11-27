import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from threading import Event
from unittest.mock import patch

from PIL import Image, ImageDraw
from test_review import review_job

from pilot_teambuilder.desktop_engine import AssetCache, DesktopEngine, LatestWorker
from pilot_teambuilder.review import ReviewSession


class DesktopEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = review_job(self.root)
        templates = Path(self.job["templates"])
        templates.mkdir(parents=True)
        self.bg = templates / "Team Background_default.jpg"
        self.fg = templates / "Team Foreground_default.png"
        with Image.new("RGB", (900, 300), "navy") as image:
            image.save(self.bg)
        with Image.new("RGBA", (900, 300)) as image:
            image.save(self.fg)
        self.engine = DesktopEngine(self.root / "cache.sqlite3")
        self.addCleanup(self.engine.close)

    def test_layout_adjustments_reuse_measurements_and_preview_assets(self):
        first = self.engine.view(self.job, None)
        self.addCleanup(first.close)
        self.assertEqual(first.panorama.size, (1560, 480))
        decodes = self.engine.assets.decodes
        changed = deepcopy(self.job)
        changed["teams"][0]["manifest"]["settings"] = {"head_spacing_in": 0.875}
        with patch(
            "pilot_teambuilder.measurements.load_png",
            side_effect=AssertionError("Unexpected full decode"),
        ):
            second = self.engine.view(changed, None)
        self.addCleanup(second.close)
        self.assertIsNotNone(second.analysis.layout)
        self.assertEqual(self.engine.assets.decodes, decodes)
        self.assertNotEqual(first.review_signature, second.review_signature)

    def test_requested_rows_rebuild_layout_without_redecoding_and_preserve_sources(self):
        original = self.engine.view(self.job, None)
        self.addCleanup(original.close)
        decodes = self.engine.assets.decodes
        session = ReviewSession(self.job)
        session.request_rows(2)
        with patch("pilot_teambuilder.measurements.load_png",
                   side_effect=AssertionError("Unexpected full decode")):
            changed = self.engine.view(session.job, None)
        self.addCleanup(changed.close)
        self.assertEqual(changed.analysis.layout.row_counts, (2, 1))
        self.assertEqual({p.athlete_id for p in changed.analysis.layout.placements},
                         {p.athlete_id for p in original.analysis.layout.placements})
        self.assertEqual(decodes, self.engine.assets.decodes)
        self.assertNotEqual(changed.review_signature, original.review_signature)
        session.request_rows(None)
        restored = self.engine.view(session.job, None)
        self.addCleanup(restored.close)
        self.assertEqual(restored.analysis.layout.row_counts, original.analysis.layout.row_counts)

    def test_print_canvas_and_mixed_template_pairs_work_in_preview_and_export(self):
        for bg_width, fg_width in ((975, 900), (900, 975), (975, 975)):
            with self.subTest(background=bg_width, foreground=fg_width):
                with Image.new("RGB", (bg_width, 300), "#0B1F3A") as image:
                    if bg_width > 900:
                        ImageDraw.Draw(image).rectangle((900, 0, 974, 299), fill="magenta")
                    image.save(self.bg, quality=100, subsampling=0)
                with Image.new("RGBA", (fg_width, 300)) as image:
                    if fg_width > 900:
                        ImageDraw.Draw(image).rectangle((900, 0, 974, 299), fill="red")
                    image.save(self.fg)
                view = self.engine.view(self.job, None)
                try:
                    self.assertTrue(view.templates_ready, view.template_issue)
                    self.assertEqual(view.panorama.size, (1560, 480))
                    self.assertLess(view.panorama.getpixel((1410, 200))[0], 20)
                    output = self.root / f"mixed-{bg_width}-{fg_width}.jpg"
                    self.engine.export(self.job, view.review_signature, output)
                    with Image.open(output) as image:
                        self.assertEqual(image.size, (7800, 2400))
                        self.assertEqual(image.info["dpi"], (300, 300))
                        self.assertLess(image.getpixel((7050, 1000))[0], 30)
                        self.assertEqual(image.crop((7200, 0, 7800, 2400)).getextrema()[0], (0, 255))
                finally:
                    view.close()

    def test_export_rejects_stale_template_review(self):
        view = self.engine.view(self.job, None)
        self.addCleanup(view.close)
        with Image.new("RGB", (900, 300), "white") as image:
            image.save(self.bg)
        output = self.root / "stale.jpg"
        with self.assertRaisesRegex(ValueError, "changed after preview"):
            self.engine.export(self.job, view.review_signature, output)
        self.assertFalse(output.exists())

    def test_export_rechecks_sources_and_writes_full_dimensions(self):
        view = self.engine.view(self.job, None)
        self.addCleanup(view.close)
        output = self.root / "team.jpg"
        self.engine.export(self.job, view.review_signature, output)
        with Image.open(output) as image:
            self.assertEqual(image.size, (7800, 2400))
            self.assertEqual(image.info["dpi"], (300, 300))

    def test_outside_placement_area_cannot_be_approved_away(self):
        session = ReviewSession(self.job)
        identity = session.manifest["athletes"][0]["id"]
        session.offset(identity, 100, 0)
        view = self.engine.view(session.job, None)
        self.addCleanup(view.close)
        with self.assertRaisesRegex(ValueError, "inside the placement area"):
            self.engine.export(
                session.job, view.review_signature, self.root / "outside.jpg"
            )

    def test_preview_cache_respects_budget(self):
        cache = AssetCache(budget=16384)
        self.addCleanup(cache.close)
        for e in self.job["teams"][0]["manifest"]["athletes"]:
            cache.get(
                Path(self.job["teams"][0]["manifest"]["source_folder"]) / e["file"]
            )
        self.assertLessEqual(cache.bytes, cache.budget)

    def test_excluded_offsets_are_retained_without_blocking_other_athletes(self):
        session = ReviewSession(self.job)
        identity = session.manifest["athletes"][0]["id"]
        session.offset(identity, 0.125, 0)
        session.exclude(identity)
        view = self.engine.view(session.job, None)
        self.addCleanup(view.close)
        self.assertIsNotNone(view.analysis.layout)
        self.assertEqual(len(view.analysis.layout.placements), 2)


class WorkerTests(unittest.TestCase):
    def test_worker_can_retry_after_engine_startup_failure(self):
        calls = []

        class Engine:
            def close(self):
                pass

        def factory():
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("Cache temporarily unavailable")
            return Engine()

        worker = LatestWorker(factory)
        try:
            worker.submit(1, lambda engine: "ready")
            self.assertEqual(
                worker.results.get(timeout=3),
                (1, None, "Cache temporarily unavailable"),
            )
            worker.submit(2, lambda engine: "ready")
            self.assertEqual(worker.results.get(timeout=3), (2, "ready", None))
        finally:
            worker.close()
            worker.thread.join(3)

    def test_pending_work_is_bounded_and_superseded_requests_are_dropped(self):
        entered, release = Event(), Event()

        class Engine:
            def close(self):
                pass

        worker = LatestWorker(Engine)
        try:

            def blocking(engine):
                entered.set()
                release.wait(5)
                return "first"

            worker.submit(1, blocking)
            self.assertTrue(entered.wait(2))
            worker.submit(2, lambda engine: "obsolete")
            worker.submit(3, lambda engine: "latest")
            self.assertLessEqual(worker.tasks.qsize(), 1)
            release.set()
            self.assertEqual(worker.results.get(timeout=3), (1, "first", None))
            self.assertEqual(worker.results.get(timeout=3), (3, "latest", None))
        finally:
            release.set()
            worker.close()
            worker.thread.join(3)


if __name__ == "__main__":
    unittest.main()
