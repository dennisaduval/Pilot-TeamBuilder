from pathlib import Path
import tempfile
import unittest

from pilot_teambuilder.discovery import png_paths, source_paths
from pilot_teambuilder.manifests import inventory


class DiscoveryTests(unittest.TestCase):
    def test_macos_archive_metadata_does_not_become_athletes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = ["team/one.png", "team/two.PNG", "team/._one.png",
                     "__MACOSX/team/._two.PNG", "team/.DS_Store"]
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"inventory only")
            self.assertEqual([p.name for p in png_paths(root)], ["one.png", "two.PNG"])
            self.assertEqual(len(inventory(root / "team")["athletes"]), 2)

    def test_ordinary_hidden_png_is_not_silently_omitted(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".athlete.png"
            path.write_bytes(b"inventory only")
            self.assertEqual(png_paths(Path(temp)), [path])


if __name__ == "__main__":
    unittest.main()
