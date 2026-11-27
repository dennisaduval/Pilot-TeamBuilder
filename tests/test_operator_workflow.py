"""Regressions from real operator setup, detection and waist-up framing feedback."""

import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from pilot_teambuilder.composition import compose
from pilot_teambuilder.desktop_engine import DesktopEngine
from pilot_teambuilder.detection import Face
from pilot_teambuilder.discovery import team_folders
from pilot_teambuilder.model import CompositionSettings, Rect
from pilot_teambuilder.production import ProductionEngine, StableFolders
from pilot_teambuilder.review import new_job, upgrade_job
from pilot_teambuilder.rows import RowTable
from pilot_teambuilder.synthetic import athletes, write_sources
from pilot_teambuilder.templates import select_templates


class ModerateDetector:
    identity = "moderate-tilted-face-test-v1"

    def __init__(self, confidence=0.6):
        self.confidence = confidence

    def detect(self, image):
        top = image.getbbox()[1]
        return (Face(Rect(175, top + 25, 225, top + 65), self.confidence),)


class OperatorWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.incoming = self.root / "Incoming"
        self.folder = self.incoming / "Event/Division/Real Team"
        write_sources(self.folder, athletes(3))
        self.job = new_job(self.incoming)
        self.templates = Path(self.job["templates"])
        self.templates.mkdir(parents=True)
        self.bg = self.templates / "Team Background_default.jpg"
        self.fg = self.templates / "Team Foreground_default.png"
        with Image.new("RGB", (900, 300), "#0B1F3A") as image:
            image.save(self.bg)
        with Image.new("RGBA", (900, 300)) as image:
            image.save(self.fg)
        self.engine = DesktopEngine(self.root / "detection.sqlite3", ModerateDetector())
        self.addCleanup(self.engine.close)

    def test_nested_folders_are_real_teams_not_aggregate_containers(self):
        other = self.incoming / "Event/Other Division/Another Team"
        write_sources(other, athletes(2))
        self.assertEqual(set(team_folders(self.incoming)), {self.folder, other})
        job = new_job(self.incoming)
        self.assertEqual({t["manifest"]["team_name"] for t in job["teams"]}, {"Real Team", "Another Team"})
        self.assertEqual(sorted(len(t["manifest"]["athletes"]) for t in job["teams"]), [2, 3])

    def test_extracted_wrapper_keeps_real_team_name(self):
        source = self.incoming / "Second Event/Second Team/Edited PNG"
        write_sources(source, athletes(1))
        self.assertIn(source.parent, team_folders(self.incoming))
        job = new_job(self.incoming)
        team = next(t for t in job["teams"] if t["manifest"]["team_name"] == "Second Team")
        self.assertTrue(team["manifest"]["athletes"][0]["file"].startswith("Edited PNG/"))

    def test_watcher_uses_same_nested_discovery_as_picker(self):
        scanner = StableFolders()
        first = scanner.scan(self.incoming, quiet=2, now=0)
        self.assertEqual(first[0][0], self.folder)
        self.assertFalse(first[0][2])
        self.assertTrue(scanner.scan(self.incoming, quiet=2, now=3)[0][2])

    def test_moderate_single_face_builds_without_height_approval(self):
        view = self.engine.view(self.job, None)
        try:
            self.assertIsNotNone(view.analysis.layout, view.analysis.report)
            self.assertEqual(len(view.analysis.layout.placements), 3)
            self.assertTrue(all(r["capture"]["relative_height"] for r in view.analysis.report["athletes"]))
        finally:
            view.close()

    def test_strict_mode_reassesses_cached_faces(self):
        automatic = self.engine.view(self.job, None)
        automatic.close()
        strict = deepcopy(self.job)
        strict["relaxed_detection"] = False
        view = self.engine.view(strict, None)
        try:
            self.assertIsNone(view.analysis.layout)
            self.assertIn("0.8", str(view.analysis.report["athletes"][0]["issues"]))
        finally:
            view.close()

    def test_very_low_confidence_still_preserves_all_sources_for_recovery(self):
        self.engine.detector = ModerateDetector(0.2)
        view = self.engine.view(self.job, None, refresh=True)
        try:
            self.assertIsNone(view.analysis.layout)
            self.assertEqual(len(view.analysis.report["athletes"]), 3)
            self.assertIsNotNone(view.panorama)
        finally:
            view.close()

    def test_uncertain_hair_window_does_not_hold_automatic_mode(self):
        with patch("pilot_teambuilder.measurements.measure_head", return_value=(Rect(155, 380, 245, 465), True)):
            view = self.engine.view(self.job, None)
        try:
            self.assertIsNotNone(view.analysis.layout)
        finally:
            view.close()

    def test_selected_defaults_work_for_missing_color_and_mixed_identifiers(self):
        job = deepcopy(self.job)
        job["background"], job["foreground"] = str(self.bg), str(self.fg)
        for entry in job["teams"][0]["manifest"]["athletes"]:
            entry["file"] = entry["file"].replace(".png", "_Aqua.png")
        pair = select_templates(job, job["teams"][0])
        self.assertEqual(pair[:2], (self.bg, self.fg))
        self.assertIn("selected default", pair[2])
        job["teams"][0]["manifest"]["athletes"][0]["file"] = "one_Orange.png"
        self.assertEqual(select_templates(job, job["teams"][0])[:2], (self.bg, self.fg))

    def test_waist_up_scaling_keeps_head_line_steady(self):
        people = athletes(3)
        table = RowTable({3: (3,)})
        settings = CompositionSettings(crop_body_fraction=0.55)
        small = compose(people, table, replace(settings, group_scale=0.7), fixed_scale=1.8)
        large = compose(people, table, replace(settings, group_scale=1.2), fixed_scale=1.8)
        self.assertAlmostEqual(min(p.head.top for p in small.placements), min(p.head.top for p in large.placements))
        self.assertTrue(all(p.source_visible.height < p.source_body.height for p in large.placements))
        self.assertEqual(large.clip, settings.area)

    def test_position_moves_in_inches_without_resizing_team(self):
        people = athletes(3)
        table = RowTable({3: (3,)})
        settings = CompositionSettings(crop_body_fraction=0.55)
        first = compose(people, table, settings, fixed_scale=1.8)
        moved = compose(people, table, replace(settings, y_offset_in=0.5, x_offset_in=-0.125), fixed_scale=1.8)
        self.assertEqual(first.global_scale, moved.global_scale)
        for before, after in zip(first.placements, moved.placements):
            self.assertAlmostEqual(after.head.top - before.head.top, 150)
            self.assertAlmostEqual(after.head.left - before.head.left, -37.5)

    def test_crop_preview_and_final_do_not_render_lower_body(self):
        # A bright canary occupies the original feet region, below the selected crop.
        source = self.folder / self.job["teams"][0]["manifest"]["athletes"][0]["file"]
        with Image.open(source) as opened:
            image = opened.copy()
        with image:
            ImageDraw.Draw(image).rectangle((120, 850, 280, 965), fill=(255, 0, 255, 255))
            image.save(source)
        self.job["base_scale"] = 1.8
        self.job["job_settings"]["group_scale"] = 0.4
        view = self.engine.view(self.job, None)
        try:
            self.assertIsNotNone(view.analysis.layout)
            self.assertTrue(all(p.source_visible for p in view.analysis.layout.placements))
            output = self.root / "waist-up.jpg"
            self.engine.export(self.job, view.review_signature, output)
            with Image.open(output) as image:
                self.assertEqual(image.size, (7800, 2400))
                self.assertEqual(image.info["dpi"], (300, 300))
                # A sample below the placement area is background, not cropped feet.
                self.assertLess(image.getpixel((3600, 2180))[0], 40)
                with image.crop((0, 0, 7200, 2400)).resize((720, 240)) as small:
                    self.assertFalse(any(r > 200 and b > 200 and g < 55 for r, g, b in small.getdata()))
        finally:
            view.close()

    def test_corrected_head_alone_supplies_automatic_rank_in_relaxed_mode(self):
        for entry, person in zip(self.job["teams"][0]["manifest"]["athletes"], athletes(3)):
            entry["manual_head"] = [person.head.left, person.head.top, person.head.right, person.head.bottom]
        view = self.engine.view(self.job, None)
        try:
            self.assertIsNotNone(view.analysis.layout)
            self.assertTrue(all(r["capture"]["relative_height"] for r in view.analysis.report["athletes"]))
        finally:
            view.close()

    def test_legacy_job_migrates_once_and_keeps_original_metadata(self):
        old = deepcopy(self.job)
        old.pop("framing_version")
        old["job_settings"] = {"group_scale": 1.2}
        old["base_scale"] = 2.0
        new = upgrade_job(old)
        self.assertEqual(new["job_settings"]["crop_body_fraction"], 0.55)
        self.assertIsNone(new["base_scale"])
        new["base_scale"] = 3.0
        self.assertEqual(upgrade_job(new)["base_scale"], 3.0)
        self.assertEqual(old["base_scale"], 2.0)

    def test_nested_team_with_moderate_faces_processes_automatically(self):
        engine = ProductionEngine(self.root / "production", ModerateDetector())
        try:
            engine.tick(self.job, now=0)
            result = engine.tick(self.job, now=25)
            self.assertEqual(result["statuses"][str(self.folder)]["state"], "Completed")
            self.assertEqual(result["job"]["teams"][0]["manifest"]["team_name"], "Real Team")
        finally:
            engine.close()


if __name__ == "__main__":
    unittest.main()
