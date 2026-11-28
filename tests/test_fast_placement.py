"""Fast production does not depend on face detection or per-athlete approval."""
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pilot_teambuilder.composition import compose
from pilot_teambuilder.desktop_engine import DesktopEngine
from pilot_teambuilder.model import CompositionSettings, Corrections
from pilot_teambuilder.production import ProductionEngine
from pilot_teambuilder.review import new_job, upgrade_job
from pilot_teambuilder.rows import RowTable
from pilot_teambuilder.sources import load_png
from pilot_teambuilder.synthetic import athletes, write_sources


class NoDetector:
    @property
    def identity(self):
        raise AssertionError("Transparency mode must not resolve a face model")

    def detect(self, _image):
        raise AssertionError("Transparency mode must not detect faces")


class FastPlacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "Incoming/Team"
        write_sources(self.folder, athletes(9))
        self.job = new_job(self.folder.parent)
        self.job["settle_seconds"] = 2
        templates = Path(self.job["templates"])
        templates.mkdir(parents=True)
        with Image.new("RGB", (900, 300), "navy") as bg:
            bg.save(templates / "Team Background_default.jpg")
        with Image.new("RGBA", (900, 300)) as fg:
            fg.save(templates / "Team Foreground_default.png")
        self.engine = DesktopEngine(self.root / "geometry.sqlite3", NoDetector())
        self.addCleanup(self.engine.close)
        self.settings = CompositionSettings(transparency_mode=1, foreground_framing=1,
                                            v_step_in=0.125, v_max_drop_in=0.5)

    def test_one_png_decode_per_athlete_and_zero_for_warm_layout_edits(self):
        with patch("pilot_teambuilder.measurements.load_png", wraps=load_png) as decode:
            with patch("pilot_teambuilder.desktop_engine.load_png", side_effect=AssertionError("Duplicate PNG decode")):
                view = self.engine.view(self.job, None)
                self.addCleanup(view.close)
                self.assertIsNotNone(view.analysis.layout, view.analysis.report)
                self.assertEqual(decode.call_count, 9)
                self.assertEqual(view.analysis.report["placement_method"], "transparency")
                changed = deepcopy(self.job)
                changed["job_settings"]["v_step_in"] = 0.15
                updated = self.engine.view(changed, None)
                self.addCleanup(updated.close)
                self.assertEqual(decode.call_count, 9)
                self.assertEqual(updated.analysis.report["cache_hits"], 9)
                self.assertEqual(updated.analysis.report["issues"], [])

    def test_hero_alpha_top_is_highest_and_each_outward_slot_is_lower(self):
        for count in (7, 8, 15, 16):
            with self.subTest(count=count):
                layout = compose(athletes(count), RowTable({count: (count,)}), self.settings)
                by_slot = sorted(layout.placements, key=lambda p: p.slot)
                center = (count - 1) / 2
                for inner, outer in zip(by_slot[:count // 2][::-1], by_slot[:count // 2 - 1][::-1]):
                    self.assertLess(inner.body.top, outer.body.top)
                for inner, outer in zip(by_slot[(count - 1) // 2:], by_slot[(count - 1) // 2 + 1:]):
                    if abs(inner.slot - center) == abs(outer.slot - center):
                        continue
                    self.assertLess(inner.body.top, outer.body.top)
                self.assertLessEqual(abs(layout.placements[-1].slot - center), 0.5)
                for a, b in zip(by_slot, by_slot[1:]):
                    self.assertGreater(a.body.right, b.body.left)

    def test_all_csv_sizes_and_large_teams_fit_without_false_review_holds(self):
        table = RowTable.load(files("pilot_teambuilder").joinpath("data/row_counts.csv"))
        for count in (*range(1, 46), 64, 100):
            with self.subTest(count=count):
                layout = compose(athletes(count), table, self.settings)
                self.assertEqual(layout.issues, ())
                self.assertEqual(len(layout.placements), count)
                self.assertEqual(len({p.scale for p in layout.placements}), 1)
                self.assertLessEqual(max(layout.row_counts), 16)
                span = (min(p.body.left for p in layout.placements) + max(p.body.right for p in layout.placements)) / 2
                self.assertAlmostEqual(span, self.settings.area.center_x)
                for p in layout.placements:
                    self.assertTrue(self.settings.area.contains(p.head))
                    self.assertIsNone(p.source_visible)

    def test_ordinary_offsets_and_size_changes_are_contained_automatically(self):
        layout = compose(athletes(16), RowTable({16: (16,)}),
                         replace(self.settings, group_scale=3, x_offset_in=4, y_offset_in=-4),
                         Corrections(offsets_in={"athlete-000": (1, 0.5)}))
        self.assertEqual(layout.issues, ())
        for p in layout.placements:
            self.assertTrue(self.settings.area.contains(p.head))
            self.assertGreaterEqual(p.body.left, self.settings.area.left - 1)
            self.assertLessEqual(p.body.right, self.settings.area.right + 1)

    def test_incoming_pixel_sizes_are_retained_even_if_legacy_metadata_has_a_scale(self):
        people = athletes(3)
        people[0] = replace(people[0], source_scale=2)
        layout = compose(people, RowTable({3: (3,)}), self.settings)
        self.assertEqual(len({p.scale for p in layout.placements}), 1)
        for p in layout.placements:
            a = next(a for a in people if a.id == p.athlete_id)
            self.assertAlmostEqual(p.body.height / a.body.height, layout.global_scale)

    def test_prepared_export_reuses_layout_and_rejects_a_changed_template(self):
        view = self.engine.view(self.job, None)
        self.addCleanup(view.close)
        with patch.object(self.engine, "view", side_effect=AssertionError("Second preview")):
            self.engine.export(self.job, view.review_signature, self.root / "ready.jpg", prepared_view=view)
        with Image.open(self.root / "ready.jpg") as image:
            self.assertEqual(image.size, (7800, 2400))
        bg = Path(self.job["templates"]) / "Team Background_default.jpg"
        with Image.new("RGB", (900, 300), "red") as image:
            image.save(bg)
        with self.assertRaisesRegex(ValueError, "Template changed"):
            self.engine.export(self.job, view.review_signature, self.root / "stale.jpg", prepared_view=view)
        self.assertFalse((self.root / "stale.jpg").exists())

    def test_cached_and_direct_jpegs_are_identical_for_crash_recovery(self):
        view = self.engine.view(self.job, None, preview=False)
        self.addCleanup(view.close)
        cached = self.root / "cached.jpg"
        direct = self.root / "direct.jpg"
        self.engine.export(self.job, view.review_signature, cached, prepared_view=view)
        from pilot_teambuilder.rendering import render
        templates = Path(self.job["templates"])
        render(view.analysis.layout, view.analysis.sources,
               templates / "Team Background_default.jpg", templates / "Team Foreground_default.png",
               "Team", direct, expected_hashes=view.analysis.hashes)
        self.assertEqual(cached.read_bytes(), direct.read_bytes())

    def test_prepared_export_rejects_changed_settings(self):
        view = self.engine.view(self.job, None)
        self.addCleanup(view.close)
        changed = deepcopy(self.job)
        changed["job_settings"]["group_scale"] = 0.5
        with self.assertRaisesRegex(ValueError, "settings changed"):
            self.engine.export(changed, view.review_signature, self.root / "changed.jpg", prepared_view=view)

    def test_automatic_export_uses_one_view_and_never_duplicates_delivery(self):
        production = ProductionEngine(self.root / "production", NoDetector())
        self.addCleanup(production.close)
        first = production.tick(self.job, now=0)
        with patch.object(production.engine, "view", wraps=production.engine.view) as view, patch("pilot_teambuilder.render_sources.load_png", side_effect=AssertionError("Second decode during production")):
            result = production.tick(first["job"], now=3)
            self.assertEqual(result["statuses"][str(self.folder.resolve())]["state"], "Completed")
            self.assertEqual(view.call_count, 1)
            self.assertEqual(production.engine.render_sources.hits, 9)
        completed = list(Path(self.job["output"]).rglob("*.jpg"))
        again = production.tick(result["job"], now=6)
        self.assertEqual(again["statuses"][str(self.folder.resolve())]["state"], "Completed")
        self.assertEqual(list(Path(self.job["output"]).rglob("*.jpg")), completed)

    def test_migration_enables_alpha_and_hero_step_without_erasing_edits(self):
        old = deepcopy(self.job)
        old["framing_version"] = 2
        old["job_settings"] = {"v_step_in": 0.025, "group_scale": 1.2}
        upgraded = upgrade_job(old)
        self.assertEqual(upgraded["job_settings"]["transparency_mode"], 1)
        self.assertEqual(upgraded["job_settings"]["v_step_in"], 0.125)
        self.assertEqual(upgraded["job_settings"]["group_scale"], 1.2)
        self.assertEqual(upgrade_job(upgraded), upgraded)
