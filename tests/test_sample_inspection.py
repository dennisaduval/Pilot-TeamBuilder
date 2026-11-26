import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image, ImageDraw

from scripts.inspect_sample_batch import inspect


class SampleInspectionTests(unittest.TestCase):
    def test_private_sample_inspection_is_non_destructive_and_omits_sensitive_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            team = root / "team"
            team.mkdir()
            image_path = team / "one.png"
            with Image.new("RGBA", (128, 256)) as image:
                ImageDraw.Draw(image).rectangle((60, 40, 90, 220), fill="red")
                image.putpixel((0, 0), (255, 0, 0, 1))
                exif = Image.Exif()
                exif[272] = "Synthetic Camera"
                exif[315] = "private-author-must-not-be-exported"
                image.save(image_path, exif=exif)
            before = hashlib.sha256(image_path.read_bytes()).hexdigest()
            shutil.copyfile(image_path, team / "duplicate.png")
            (team / "._one.png").write_bytes(b"AppleDouble")
            report = inspect(root)
            self.assertEqual(report["total_pngs"], 2)
            self.assertEqual(report["valid_pngs"], 2)
            self.assertNotIn("private-author", json.dumps(report))
            self.assertEqual(report["images"][0]["capture_hints"]["camera_model"], "Synthetic Camera")
            self.assertEqual(report["images"][0]["alpha_bounds"][0], 0)
            self.assertEqual(report["images"][0]["alpha_bounds_above_8"][0], 60)
            self.assertTrue(any("Faint alpha" in i for i in report["images"][0]["issues"]))
            self.assertTrue(any("Duplicate" in i for i in report["images"][1]["issues"]))
            self.assertEqual(hashlib.sha256(image_path.read_bytes()).hexdigest(), before)

    def test_invalid_sample_is_reported_and_not_silently_omitted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "invalid.png").write_bytes(b"bad PNG")
            report = inspect(root)
            self.assertEqual(report["total_pngs"], 1)
            self.assertEqual(report["valid_pngs"], 0)
            self.assertEqual(report["images"][0]["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
