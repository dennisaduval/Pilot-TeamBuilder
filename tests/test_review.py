import json
import tempfile
import unittest
from pathlib import Path

from pilot_teambuilder.review import (
    JobStore,
    ReviewSession,
    import_manifest,
    add_image_folder,
    requested_distribution,
    new_job,
    validate_job,
)
from pilot_teambuilder.synthetic import athletes, write_sources


def review_job(folder, count=3):
    people = athletes(count)
    write_sources(folder / "Incoming/001_Team", people)
    job = new_job(folder / "Incoming")
    for e, a in zip(job["teams"][0]["manifest"]["athletes"], people):
        e["manual_head"] = list(vars(a.head).values())
        e["capture"].update(
            source_scale=1,
            relative_height=a.relative_height,
            geometry_id="synthetic",
            provenance="Synthetic fixture",
        )
    return job


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.job = review_job(self.folder)

    def test_saved_job_preserves_non_destructive_corrections(self):
        session = ReviewSession(self.job)
        e = session.manifest["athletes"][0]
        session.correct_head(e["id"], [155, 380, 245, 470])
        session.anchor(e["id"], "height_top", 390)
        session.anchor(e["id"], "floor_y", 960)
        session.offset(e["id"], 0.125, -0.25)
        session.exclude(e["id"])
        path = self.folder / "review.json"
        store = JobStore()
        store.save(session.job, path)
        restored = ReviewSession(JobStore().load(path))
        restored.exclude(e["id"])
        entry = restored.entry(e["id"])
        self.assertFalse(entry["excluded"])
        self.assertEqual(entry["manual_head"], [155, 380, 245, 470])
        self.assertEqual(entry["capture"]["relative_height"], 570)
        self.assertEqual(
            restored.manifest["corrections"]["offsets_in"][e["id"]], [0.125, -0.25]
        )
        self.assertNotIn("excluded", self.job["teams"][0]["manifest"]["athletes"][0])

    def test_imported_manifest_preserves_verified_geometry_and_rejects_duplicate_team(
        self,
    ):
        path = self.folder / "team-manifest.json"
        manifest = self.job["teams"][0]["manifest"]
        path.write_text(json.dumps(manifest))
        imported = import_manifest(None, path)
        self.assertEqual(imported["teams"][0]["manifest"], manifest)
        with self.assertRaisesRegex(ValueError, "already in the job"):
            import_manifest(imported, path)

    def test_direct_png_folder_selection_retains_existing_team_corrections(self):
        session = ReviewSession(self.job)
        identity = session.manifest["athletes"][0]["id"]
        session.offset(identity, 0.125, -0.25)
        source = Path(session.manifest["source_folder"])
        selected = add_image_folder(session.job, source)
        self.assertEqual(len(selected["teams"]), 1)
        self.assertEqual(selected["teams"][0], session.team)
        self.assertEqual(selected["selected_team"], 0)
        self.assertEqual(add_image_folder(None, source)["teams"][0]["manifest"]["team_name"], "Team")

    def test_folder_collection_adds_and_selects_teams_without_replacing_settings(self):
        write_sources(self.folder / "Other/002_New Team", athletes(2))
        selected = add_image_folder(self.job, self.folder / "Other")
        self.assertEqual(len(selected["teams"]), 2)
        self.assertEqual(selected["selected_team"], 1)
        self.assertEqual(selected["teams"][0], self.job["teams"][0])
        self.assertEqual(selected["templates"], self.job["templates"])
        self.assertEqual(len(self.job["teams"]), 1)

    def test_same_named_sources_keep_distinct_team_ids(self):
        write_sources(self.folder / "Other/001_Team", athletes(3))
        selected = add_image_folder(self.job, self.folder / "Other/001_Team")
        ids = [t["manifest"]["team_id"] for t in selected["teams"]]
        self.assertEqual(len(set(ids)), 2)
        validate_job(selected, self.folder)

    def test_requested_rows_are_explicit_persisted_and_reset_to_csv(self):
        session = ReviewSession(self.job)
        session.request_rows(2)
        path = self.folder / "job.json"
        JobStore().save(session.job, path)
        loaded = JobStore().load(path)
        self.assertEqual(loaded["teams"][0]["requested_rows"], 2)
        session.request_rows(None)
        self.assertNotIn("requested_rows", session.team)
        session.request_rows(2)
        session.manual_rows((1, 2))
        self.assertNotIn("requested_rows", session.team)
        session.request_rows(2)
        self.assertNotIn("rows", session.manifest["corrections"])
        session.exclude(session.manifest["athletes"][0]["id"])
        self.assertNotIn("requested_rows", session.team)

    def test_requested_row_distributions_preserve_every_athlete_and_capacity(self):
        for count in (*range(1, 70), 128, 257):
            minimum = (count + 15) // 16
            for requested in range(minimum, count + 1):
                result = requested_distribution(count, requested, ())
                self.assertEqual(sum(result), count)
                self.assertEqual(len(result), requested)
                self.assertLessEqual(max(result), 16)
                self.assertGreaterEqual(min(result), 1)
                self.assertLessEqual(max(result) - min(result), 1)
        automatic = (9, 9, 9)
        self.assertEqual(requested_distribution(27, None, automatic), automatic)
        for count, requested in ((17, 1), (3, 0), (3, 4), (3, 1.5), (3, True)):
            with self.assertRaises(ValueError):
                requested_distribution(count, requested, ())

    def test_invalid_requested_rows_in_saved_job_are_rejected(self):
        self.job["teams"][0]["requested_rows"] = 4
        with self.assertRaises(ValueError):
            validate_job(self.job, self.folder)

    def test_external_job_changes_are_not_overwritten(self):
        path = self.folder / "job.json"
        store = JobStore()
        store.save(self.job, path)
        changed = json.loads(path.read_text())
        changed["name"] = "Changed elsewhere"
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "changed elsewhere"):
            store.save(self.job)
        self.assertEqual(json.loads(path.read_text())["name"], "Changed elsewhere")

    def test_output_cannot_be_inside_sources(self):
        self.job["output"] = self.job["incoming"] + "/outputs"
        with self.assertRaises(ValueError):
            validate_job(self.job, self.folder)

    def test_head_floor_measurement_is_source_normalized(self):
        session = ReviewSession(self.job)
        e = session.manifest["athletes"][0]
        e["capture"]["source_scale"] = 0.5
        session.anchor(e["id"], "height_top", 100)
        session.anchor(e["id"], "floor_y", 500)
        self.assertEqual(e["capture"]["relative_height"], 800)
        with self.assertRaises(ValueError):
            session.anchor(e["id"], "floor_y", 50)
        self.assertEqual(session.team["anchors"][e["id"]]["floor_y"], 500)

    def test_manual_rows_rearrange_and_restore_without_losing_athletes(self):
        session = ReviewSession(self.job)
        ids = {e["id"] for e in session.manifest["athletes"]}
        session.manual_rows((2, 1))
        rows = session.manifest["corrections"]["rows"]
        selected = rows[0][0]
        session.move(selected, rows, target_row=1)
        rows = session.manifest["corrections"]["rows"]
        self.assertEqual([len(r) for r in rows], [1, 2])
        self.assertEqual({i for row in rows for i in row}, ids)
        with self.assertRaises(ValueError):
            session.manual_rows((3, 1))

    def test_template_presets_and_height_order_are_saved(self):
        session = ReviewSession(self.job)
        session.settings({"x_offset_in": 0.125, "head_spacing_in": 0.875})
        self.assertEqual(
            session.job["template_presets"][session.job["templates"]]["x_offset_in"],
            0.125,
        )
        ordered = sorted(
            session.manifest["athletes"], key=lambda e: -e["capture"]["relative_height"]
        )
        first, second = ordered[:2]
        previous = first["capture"]["relative_height"]
        session.rank(second["id"], True)
        self.assertEqual(second["capture"]["relative_height"], previous)


if __name__ == "__main__":
    unittest.main()
