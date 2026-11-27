"""GUI smoke tests run on Linux CI under Xvfb; no private photography is used."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(
    os.environ.get("TEAMBUILDER_GUI_TEST") == "1",
    "Run desktop smoke under a graphical display",
)
class DesktopSmokeTests(unittest.TestCase):
    def tearDown(self):
        # Multiple Tk roots in the harness must be collected on this main thread.
        import gc
        gc.collect()

    def test_choose_png_folder_uses_directory_picker_and_preserves_corrections(self):
        import tkinter as tk

        from pilot_teambuilder.app import TeamBuilderApp
        from pilot_teambuilder.synthetic import athletes, write_sources

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "Incoming/001_Real Team"
            write_sources(source, athletes(3))
            root = tk.Tk()
            app = TeamBuilderApp(root, folder / "state")
            try:
                with patch("pilot_teambuilder.app.filedialog.askdirectory",
                           return_value=str(source)) as picker, patch(
                    "pilot_teambuilder.app.filedialog.askopenfilename",
                    side_effect=AssertionError("PNG folder selection must not ask for JSON"),
                ):
                    app.choose_team_folder()
                    picker.assert_called_once()
                    self.assertEqual(len(app.teams.get_children()), 1)
                    self.assertEqual(len(app.athletes.get_children()), 3)
                    self.assertEqual(app.title.get(), "001_Real Team")
                    identity = app.selected_id
                    app.session.offset(identity, 0.125, -0.25)
                    saved = app.session.snapshot()
                    app.choose_team_folder()
                    self.assertEqual(len(app.session.job["teams"]), 1)
                    self.assertEqual(app.session.team, saved["teams"][0])
                    self.assertTrue(app.session.dirty)
            finally:
                if app.session:
                    app.session.dirty = False
                app.close()

    def test_z_automatic_hotfolder_main_screen(self):
        import tkinter as tk

        from PIL import Image, ImageGrab
        from test_production import SyntheticDetector

        from pilot_teambuilder import __version__
        from pilot_teambuilder.app import TeamBuilderApp
        from pilot_teambuilder.desktop_engine import DesktopEngine
        from pilot_teambuilder.production import ProductionEngine
        from pilot_teambuilder.review import new_job
        from pilot_teambuilder.synthetic import athletes, write_sources

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            incoming = folder / "Incoming"
            incoming.mkdir()
            job = new_job(incoming, allow_empty=True)
            job["settle_seconds"] = 0
            templates = Path(job["templates"])
            templates.mkdir(parents=True)
            with Image.new("RGB", (975, 300), "navy") as image:
                image.save(templates / "Team Background_default.jpg")
            with Image.new("RGBA", (975, 300)) as image:
                image.save(templates / "Team Foreground_default.png")
            job["routes"] = [str(folder / "Upload"), str(folder / "Samples")]
            root = tk.Tk()
            app = TeamBuilderApp(root, folder / "state", job)
            failures = []
            root.report_callback_exception = lambda *error: failures.append(error)
            def wait_for(predicate):
                deadline = time.monotonic() + 30
                while not predicate():
                    root.update()
                    if time.monotonic() > deadline:
                        self.fail("Automatic desktop timed out: " + app.status.get())
                    time.sleep(0.01)
                root.update_idletasks()
            try:
                self.assertIn(__version__, root.title())
                self.assertEqual(len(app.main.panes()), 2)  # Correction pane hidden.
                with patch("pilot_teambuilder.app.ProductionEngine",
                           side_effect=lambda state: ProductionEngine(state, SyntheticDetector())), patch(
                    "pilot_teambuilder.app.DesktopEngine",
                    side_effect=lambda state: DesktopEngine(state, SyntheticDetector()),
                ):
                    app.toggle_monitor()
                    write_sources(incoming / "Automatic Team", athletes(9))
                    wait_for(lambda: app.session.job["teams"] and app.session.team.get("production", {}).get("state") == "Completed")
                    app.stop_monitor()
                    wait_for(lambda: not app.production_busy)
                    wait_for(lambda: app.view is not None and app.view.analysis.layout is not None)
                self.assertEqual(app.recent.get(), app.session.job["name"])
                self.assertFalse(app.view.draft)
                self.assertEqual(len(app.view.analysis.layout.placements), 9)
                self.assertEqual(len(list((folder / "Upload").glob("*.jpg"))), 1)
                self.assertEqual(len(list((folder / "Samples").glob("*.jpg"))), 1)
                self.assertFalse(failures, failures)
                capture = os.environ.get("TEAMBUILDER_UI_CAPTURE")
                if capture:
                    with ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(),
                         root.winfo_rootx() + root.winfo_width(), root.winfo_rooty() + root.winfo_height())) as image:
                        image.save(capture)
            finally:
                app.stop_monitor()
                if app.production_busy:
                    wait_for(lambda: not app.production_busy)
                app.session.dirty = False
                app.close()
                if app.production_worker:
                    app.production_worker.thread.join(3)
                app.worker.thread.join(3)

    def test_saved_job_preview_correction_and_export_flow(self):
        import tkinter as tk

        from PIL import Image, ImageGrab

        from pilot_teambuilder.app import TeamBuilderApp

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            root = tk.Tk()
            app = TeamBuilderApp(root, folder / "state")
            app.demo()
            try:

                def wait_for(predicate):
                    deadline = time.monotonic() + 30
                    while not predicate():
                        root.update()
                        if time.monotonic() > deadline:
                            self.fail("Desktop timed out: " + app.status.get())
                        time.sleep(0.01)
                    root.update_idletasks()

                wait_for(lambda: app.view is not None)
                self.assertEqual(len(app.athletes.get_children()), 17)
                self.assertIsNotNone(app.view.panorama)
                self.assertGreater(app.canvas.winfo_width(), 600)
                self.assertIsNotNone(app.portrait_map)
                # Live sliders invalidate approval immediately and debounce a rebuild.
                old = app.view
                app._live_change("group_scale", "90", 1)
                self.assertIn("disabled", app.export_button.state())
                self.assertIsNotNone(app.live_timer)
                # Clicking export while a slider is pending updates the preview first.
                app.export()
                self.assertFalse(app.exporting)
                wait_for(lambda: app.view is not old)
                self.assertEqual(app.session.manifest["settings"]["group_scale"], 0.9)
                # Requested rows update the actual composition, and Auto restores CSV.
                old = app.view
                app.requested_rows.set("3")
                app.rows_picker.event_generate("<<ComboboxSelected>>")
                wait_for(lambda: app.view is not old)
                self.assertEqual(app.view.analysis.layout.row_counts, (6, 6, 5))
                self.assertEqual(app.distribution.get(), "6 / 6 / 5")
                old = app.view
                app.requested_rows.set("Auto")
                app.apply_requested_rows()
                wait_for(lambda: app.view is not old)
                self.assertNotIn("requested_rows", app.session.team)
                # Edit through the same form callback operators use.
                app.values["head_spacing_in"].set("0.875")
                old = app.view
                app.apply_settings()
                wait_for(lambda: app.view is not old)
                self.assertEqual(
                    app.session.manifest["settings"]["head_spacing_in"], 0.875
                )
                # Exercise actual canvas gestures with source-coordinate mapping.
                app.show_review()
                app.tabs.select(1)
                root.update()

                def point(x, y):
                    left, top, sx, sy = app.portrait_map
                    return round(left + x * sx), round(top + y * sy)

                old = app.view
                app.mode.set("Head box")
                start, end = point(160, 385), point(240, 465)
                app.portrait.event_generate("<ButtonPress-1>", x=start[0], y=start[1])
                app.portrait.event_generate("<B1-Motion>", x=end[0], y=end[1])
                app.portrait.event_generate("<ButtonRelease-1>", x=end[0], y=end[1])
                wait_for(lambda: app.view is not old)
                for mode, y in (("Height top", 390), ("Feet", 960)):
                    old = app.view
                    app.mode.set(mode)
                    x, y_canvas = point(200, y)
                    app.portrait.event_generate("<ButtonPress-1>", x=x, y=y_canvas)
                    app.portrait.event_generate("<ButtonRelease-1>", x=x, y=y_canvas)
                    wait_for(lambda: app.view is not old)
                height = app.session.entry(app.selected_id)["capture"][
                    "relative_height"
                ]
                self.assertAlmostEqual(height, 570, delta=10)
                old = app.view
                app.exclude()
                wait_for(lambda: app.view is not old)
                self.assertEqual(len(app.view.analysis.layout.placements), 16)
                old = app.view
                app.exclude()
                wait_for(lambda: app.view is not old)
                self.assertEqual(len(app.view.analysis.layout.placements), 17)
                root.geometry("1240x780")
                root.update()
                self.assertLessEqual(
                    app.analyze_button.winfo_rootx() + app.analyze_button.winfo_width(),
                    root.winfo_rootx() + root.winfo_width(),
                )
                self.assertGreater(app.canvas.winfo_width(), 500)
                root.geometry("1520x960")
                root.update()
                job_path = folder / "job.json"
                app.store.save(app.session.job, job_path)
                app.session.dirty = False
                with patch(
                    "pilot_teambuilder.app.messagebox.askyesno", return_value=True
                ):
                    app.export()
                wait_for(lambda: not app.exporting)
                self.assertTrue(app.export_destination.is_file(), app.status.get())
                with Image.open(app.export_destination) as image:
                    self.assertEqual(image.size, (7800, 2400))
                # Optional synthetic UI screenshot is retained as a CI artifact.
                capture = os.environ.get("TEAMBUILDER_UI_CAPTURE")
                if capture:
                    root.update()
                    with ImageGrab.grab(
                        bbox=(
                            root.winfo_rootx(),
                            root.winfo_rooty(),
                            root.winfo_rootx() + root.winfo_width(),
                            root.winfo_rooty() + root.winfo_height(),
                        )
                    ) as shot:
                        shot.save(capture)
            finally:
                app.session.dirty = False
                app.close()


if __name__ == "__main__":
    unittest.main()
