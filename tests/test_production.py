import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from PIL import Image

from pilot_teambuilder.detection import Face
from pilot_teambuilder.model import Rect
from pilot_teambuilder.production import (
    ProductionEngine,
    StableFolders,
    file_hash,
)
from pilot_teambuilder.review import ReviewSession, new_job, validate_job
from pilot_teambuilder.synthetic import athletes, write_sources
from pilot_teambuilder.templates import select_templates


class SyntheticDetector:
    identity = "synthetic-production-test-v1"

    def detect(self, image):
        top = image.getbbox()[1]
        return (Face(Rect(175, top + 25, 225, top + 65), 0.99),)


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.incoming = self.root / "Incoming"
        self.incoming.mkdir()
        self.folder = self.incoming / "Team name_unchanged"
        write_sources(self.folder, athletes(3))
        self.job = new_job(self.incoming)
        self.job["settle_seconds"] = 2
        self.templates = Path(self.job["templates"])
        self.templates.mkdir(parents=True)
        for variant in ("default", "Orange", "Aqua", "Green"):
            with Image.new("RGB", (900, 300), "navy") as image:
                image.save(self.templates / f"Team Background_{variant}.jpg")
            with Image.new("RGBA", (900, 300)) as image:
                image.save(self.templates / f"Team Foreground_{variant}.png")
        self.engine = ProductionEngine(self.root / "state", SyntheticDetector())
        self.addCleanup(self.engine.close)

    def run_team(self):
        first = self.engine.tick(self.job, now=0)
        self.assertFalse(list(Path(self.job["output"]).rglob("*.jpg")))
        return self.engine.tick(first["job"], now=3)

    def test_automatic_fixed_capture_needs_no_individual_approval(self):
        before = {p.name: file_hash(p) for p in self.folder.glob("*.png")}
        result = self.run_team()
        status = result["statuses"][str(self.folder)]
        self.assertEqual(status["state"], "Completed", status)
        self.assertIn("Team name_unchanged", Path(status["output"]).name)
        with Image.open(status["output"]) as image:
            self.assertEqual(image.size, (7800, 2400))
            self.assertEqual(image.info["dpi"], (300, 300))
        self.assertIsNotNone(result["job"]["base_scale"])
        self.assertEqual(result["job"]["capture_canvas"], (400, 1000))
        self.assertEqual(before, {p.name: file_hash(p) for p in self.folder.glob("*.png")})
        records = [json.loads(line) for line in (self.root / "state/production.jsonl").read_text().splitlines()]
        self.assertIn("completed", [r["event"] for r in records])

    def test_stability_resets_after_copy_changes_and_ready_marker_is_optional(self):
        scanner = StableFolders()
        self.assertFalse(scanner.scan(self.incoming, 2, now=0)[0][2])
        (self.folder / "extra.txt").write_text("copying")
        self.assertFalse(scanner.scan(self.incoming, 2, now=3)[0][2])
        self.assertTrue(scanner.scan(self.incoming, 2, now=6)[0][2])
        self.assertFalse(scanner.scan(self.incoming, 2, require_ready=True, now=7)[0][2])
        (self.folder / "team.ready").write_text("")
        self.assertFalse(scanner.scan(self.incoming, 2, require_ready=True, now=8)[0][2])
        self.assertTrue(scanner.scan(self.incoming, 2, require_ready=True, now=11)[0][2])
        (self.folder / "athlete.png.part").write_bytes(b"partial")
        self.assertFalse(scanner.scan(self.incoming, 2, now=15)[0][2])

    def test_empty_hotfolder_can_start_and_new_teams_are_discovered(self):
        empty = self.root / "Empty"
        empty.mkdir()
        job = new_job(empty, allow_empty=True)
        validate_job(job, self.root)
        self.assertEqual(self.engine.tick(job, now=0)["job"]["teams"], [])
        write_sources(empty / "New Team", athletes(1))
        self.engine.tick(job, now=1)
        result = self.engine.tick(job, now=22)
        self.assertEqual(len(result["job"]["teams"]), 1)
        self.assertEqual(result["job"]["teams"][0]["manifest"]["team_name"], "New Team")

    def test_no_duplicate_render_or_delivery_even_if_printer_consumes_file(self):
        self.job["routes"] = [str(self.root / "Upload"), str(self.root / "Print")]
        result = self.run_team()
        master = Path(result["statuses"][str(self.folder)]["output"])
        self.assertTrue((self.root / "Upload" / master.name).is_file())
        (self.root / "Print" / master.name).unlink()
        with patch.object(self.engine.engine, "export", side_effect=AssertionError("Duplicate render")):
            repeated = self.engine.tick(result["job"], now=4)
        self.assertEqual(repeated["statuses"][str(self.folder)]["state"], "Completed")
        self.assertFalse((self.root / "Print" / master.name).exists())
        self.assertEqual(len(list(Path(self.job["output"]).rglob("*.jpg"))), 1)

    def test_missing_master_does_not_trigger_automatic_printing_again(self):
        result = self.run_team()
        Path(result["statuses"][str(self.folder)]["output"]).unlink()
        with patch.object(self.engine.engine, "export", side_effect=AssertionError("Duplicate render")):
            repeated = self.engine.tick(result["job"], now=4)
        self.assertEqual(repeated["statuses"][str(self.folder)]["state"], "Needs attention")

    def test_recovery_verifies_existing_render_without_overwrite(self):
        result = self.run_team()
        old = result["statuses"][str(self.folder)]["output"]
        digest = file_hash(old)
        self.engine.db.execute("UPDATE teams SET state='interrupted',receipt=NULL")
        self.engine.db.commit()
        recovered = self.engine.tick(result["job"], now=4)
        self.assertEqual(recovered["statuses"][str(self.folder)]["state"], "Completed")
        self.assertEqual(file_hash(old), digest)
        self.assertEqual(len(list(Path(self.job["output"]).rglob("*.jpg"))), 1)

    def test_ambiguous_print_delivery_is_held_instead_of_resent(self):
        result = self.run_team()
        master = Path(result["statuses"][str(self.folder)]["output"])
        key = self.engine.db.execute("SELECT key FROM teams").fetchone()[0]
        target = self.root / "Printer" / master.name
        self.engine.db.execute("INSERT INTO routes VALUES (?,?,?)", (key, str(target), "sending"))
        self.engine.db.commit()
        with self.assertRaisesRegex(ValueError, "may already have printed"):
            self.engine._deliver(key, master, target.parent, file_hash(master))
        self.assertFalse(target.exists())

    def test_mid_render_folder_change_blocks_publication_and_routing(self):
        self.engine.tick(self.job, now=0)
        original = self.engine.engine.export
        def changed(job, signature, destination, before_publish=None):
            (self.folder / "arriving.txt").write_text("copy not done")
            return original(job, signature, destination, before_publish=before_publish)
        with patch.object(self.engine.engine, "export", side_effect=changed):
            result = self.engine.tick(self.job, now=3)
        self.assertEqual(result["statuses"][str(self.folder)]["state"], "Waiting for copying to finish")
        self.assertFalse(list(Path(self.job["output"]).rglob("*.jpg")))

    def test_color_from_players_selects_matching_pair_case_insensitively(self):
        team = self.job["teams"][0]
        for e in team["manifest"]["athletes"]:
            e["file"] = e["file"].replace(".png", "_oRaNgE_team.png")
        bg, fg, color = select_templates(self.job, team)
        self.assertEqual(color, "orange")
        self.assertTrue(bg.name.endswith("Orange.jpg"))
        self.assertTrue(fg.name.endswith("Orange.png"))
        # The color in the TEAM folder has no authority over the player files.
        team["manifest"]["team_name"] = "Aqua Team"
        self.assertEqual(select_templates(self.job, team)[2], "orange")
        team["manifest"]["athletes"][0]["file"] = "other_Aqua.png"
        with self.assertRaisesRegex(ValueError, "mixed"):
            select_templates(self.job, team)

    def test_missing_color_never_falls_back_to_default(self):
        for e in self.job["teams"][0]["manifest"]["athletes"]:
            e["file"] = "one_Purple.png"
        with self.assertRaisesRegex(ValueError, "Missing purple"):
            select_templates(self.job, self.job["teams"][0])

    def test_explicit_default_files_need_no_special_names(self):
        bg = self.templates / "Team Background_default.jpg"
        fg = self.templates / "Team Foreground_default.png"
        self.job["background"] = str(bg.rename(self.templates / "My design.jpg"))
        self.job["foreground"] = str(fg.rename(self.templates / "My design.png"))
        pair = select_templates(self.job, self.job["teams"][0])
        self.assertEqual(pair[:2], (Path(self.job["background"]), Path(self.job["foreground"])))

    def test_fixed_job_scale_is_identical_across_teams_and_allows_over_100_percent(self):
        result = self.run_team()
        job = result["job"]
        write_sources(self.incoming / "Small", athletes(1))
        from pilot_teambuilder.production import merge_inventory
        merge_inventory(job, self.incoming / "Small")
        job["job_settings"] = {"group_scale": 1.1}
        views = []
        try:
            for index in range(2):
                job["selected_team"] = index
                view = self.engine.engine.view(job, None)
                views.append(view)
                self.assertIsNotNone(view.analysis.layout)
            self.assertEqual(views[0].analysis.layout.global_scale, views[1].analysis.layout.global_scale)
            for view in views:
                self.assertAlmostEqual(view.analysis.layout.global_scale, job["base_scale"] * 1.1)
        finally:
            for view in views:
                view.close()

    def test_uncertain_detection_shows_all_sources_but_never_auto_exports(self):
        self.engine.engine.detector.detect = lambda image: ()
        result = self.run_team()
        self.assertEqual(result["statuses"][str(self.folder)]["state"], "Needs attention")
        self.assertFalse(list(Path(self.job["output"]).rglob("*.jpg")))
        view = self.engine.engine.view(self.job, None)
        try:
            self.assertTrue(view.draft)
            self.assertEqual(view.panorama.size, (1560, 480))
            self.assertEqual(len(view.analysis.report["athletes"]), 3)
        finally:
            view.close()

    def test_disabled_fixed_capture_does_not_invent_height(self):
        self.job["retained_capture"] = False
        result = self.run_team()
        self.assertEqual(result["statuses"][str(self.folder)]["state"], "Needs attention")
        self.assertFalse(list(Path(self.job["output"]).rglob("*.jpg")))

    def test_changed_canvas_requires_attention(self):
        self.job["capture_canvas"] = (4000, 6000)
        result = self.run_team()
        self.assertEqual(result["statuses"][str(self.folder)]["state"], "Needs attention")
        self.assertIn("Canvas differs", result["statuses"][str(self.folder)]["message"])

    def test_job_settings_are_shared_and_routes_cannot_point_into_incoming(self):
        session = ReviewSession(self.job)
        session.settings({"group_scale": 1.25, "head_spacing_in": 0.75})
        self.assertEqual(session.job["job_settings"]["group_scale"], 1.25)
        self.assertTrue(all(t["manifest"]["settings"]["group_scale"] == 1.25 for t in session.job["teams"]))
        self.job["routes"] = [str(self.incoming / "Printer")]
        with self.assertRaises(ValueError):
            validate_job(self.job, self.root)

    def test_single_head_correction_reuses_automatic_ranks(self):
        from pilot_teambuilder.app import TeamBuilderApp
        app = TeamBuilderApp.__new__(TeamBuilderApp)
        app.session = ReviewSession(self.job)
        app.view = self.engine.engine.view(self.job, None)
        try:
            identity = app.session.manifest["athletes"][0]["id"]
            box = app.view.analysis.report["athletes"][0]["head"]
            app._correct_head(identity, [box[k] for k in ("left", "top", "right", "bottom")])
            view = self.engine.engine.view(app.session.snapshot(), None)
            try:
                self.assertIsNotNone(view.analysis.layout)
                self.assertTrue(all(e["capture"]["relative_height"] for e in app.session.manifest["athletes"]))
            finally:
                view.close()
        finally:
            app.view.close()

    def test_changed_corrected_source_is_held_for_attention(self):
        self.job["teams"][0]["review_hashes"] = {"athlete-0001": "stale-reviewed-hash"}
        view = self.engine.engine.view(self.job, None)
        try:
            self.assertIsNone(view.analysis.layout)
            self.assertTrue(view.draft)
            self.assertIn("corrected source changed", " ".join(view.analysis.report["issues"]))
        finally:
            view.close()

    def test_pause_before_work_has_no_side_effects(self):
        self.engine.tick(self.job, now=0)
        stop = Event()
        stop.set()
        self.engine.tick(self.job, cancel=stop, now=3)
        self.assertFalse(list(Path(self.job["output"]).rglob("*.jpg")))


if __name__ == "__main__":
    unittest.main()
