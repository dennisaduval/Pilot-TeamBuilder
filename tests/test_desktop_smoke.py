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
                # Edit through the same form callback operators use.
                app.values["head_spacing_in"].set("0.875")
                old = app.view
                app.apply_settings()
                wait_for(lambda: app.view is not old)
                self.assertEqual(
                    app.session.manifest["settings"]["head_spacing_in"], 0.875
                )
                # Exercise actual canvas gestures with source-coordinate mapping.
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
