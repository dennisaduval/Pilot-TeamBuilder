import json
from pathlib import Path
import tempfile
import unittest

from pilot_teambuilder.manifests import inventory, load_manifest
from pilot_teambuilder.synthetic import athletes, write_sources


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / "team"
        write_sources(self.folder, athletes(2))
        self.data = inventory(self.folder)
        self.path = self.root / "team-manifest.json"

    def load(self):
        self.path.write_text(json.dumps(self.data))
        return load_manifest(self.path)

    def test_inventory_preserves_all_pngs_unknown_scale_and_stable_ids(self):
        self.assertEqual(self.data, inventory(self.folder))
        manifest = self.load()
        self.assertEqual(len(manifest.entries), 2)
        self.assertTrue(all(e.source_scale is None for e in manifest.entries))
        self.assertTrue(all(e.manual_head is None for e in manifest.entries))

    def test_relative_source_folder_and_manual_corrections_round_trip(self):
        self.data["source_folder"] = "team"
        self.data["athletes"][0]["manual_head"] = [155, 380, 245, 470]
        ids = [e["id"] for e in self.data["athletes"]]
        self.data["corrections"] = {"rows": [ids[::-1]], "offsets_in": {ids[0]: [0.125, -0.125]}}
        manifest = self.load()
        self.assertEqual(manifest.entries[0].manual_head.left, 155)
        self.assertEqual(manifest.corrections.rows, (tuple(ids[::-1]),))
        self.assertEqual(manifest.corrections.offsets_in[ids[0]], (0.125, -0.125))

    def test_paths_cannot_escape_source_root(self):
        for invalid in ("../outside.png", str(self.root / "outside.png"), "..\\outside.png"):
            with self.subTest(invalid=invalid):
                self.data["athletes"][0]["file"] = invalid
                with self.assertRaises(ValueError):
                    self.load()

    def test_schema_and_numeric_errors_are_explicit(self):
        original = json.dumps(self.data)
        mutations = [lambda d:d.update(schema_version=2),
                     lambda d:d.update(schema_version=True),
                     lambda d:d.update(unknown="field"),
                     lambda d:d.update(settings={"width_in":"22"}),
                     lambda d:d["athletes"][0]["capture"].update(source_scale=-1),
                     lambda d:d["athletes"][0]["capture"].update(source_scale=True),
                     lambda d:d["athletes"][0].update(excluded=True),
                     lambda d:d["athletes"][0].update(manual_head=[0,0,0,3])]
        for mutate in mutations:
            self.data = json.loads(original)
            mutate(self.data)
            with self.subTest(data=self.data), self.assertRaises(ValueError):
                self.load()

    def test_duplicate_ids_and_paths_are_rejected(self):
        self.data["athletes"][1]["id"] = self.data["athletes"][0]["id"]
        with self.assertRaisesRegex(ValueError, "unique"):
            self.load()

    def test_exclusion_requires_and_preserves_reason(self):
        self.data["athletes"][0].update(excluded=True, exclusion_reason="Coach image intentionally excluded")
        self.assertEqual(self.load().corrections.excluded, ("athlete-0001",))


if __name__ == "__main__":
    unittest.main()
