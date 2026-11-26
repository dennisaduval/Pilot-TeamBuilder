import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

from pilot_teambuilder.analysis import analyze_team
from pilot_teambuilder.detection import DetectionUnavailable, Face
from pilot_teambuilder.detection_cache import DetectionCache
from pilot_teambuilder.manifests import inventory, load_manifest
from pilot_teambuilder.model import Rect
from pilot_teambuilder.rendering import render
from pilot_teambuilder.rows import RowTable
from pilot_teambuilder.synthetic import athletes, write_sources


class FakeDetector:
    identity="synthetic-face-model-v1"

    def __init__(self, faces=None):
        self.calls=0
        self.faces=(Face(Rect(175,400,225,450),0.99),) if faces is None else faces

    def detect(self,image):
        self.calls+=1
        return self.faces


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.folder=self.root/"team"
        self.people=athletes(1)
        write_sources(self.folder,self.people)
        self.data=inventory(self.folder)
        self.data["athletes"][0]["capture"].update(
            source_scale=1,relative_height=620,geometry_id="station-10-fixed",provenance="Reviewed original capture geometry")
        self.path=self.root/"team-manifest.json"
        self.cache=DetectionCache(self.root/"state/cache.sqlite3")
        self.addCleanup(self.cache.db.close)
        self.detector=FakeDetector()
        self.table=RowTable({1:(1,),2:(2,)})

    def analyze(self, refresh=False):
        self.path.write_text(json.dumps(self.data))
        return analyze_team(load_manifest(self.path),self.table,self.detector,self.cache,refresh)

    def test_single_head_detects_composes_and_reuses_cache(self):
        first=self.analyze()
        self.assertIsNotNone(first.layout)
        self.assertEqual(first.report["status"],"ready")
        self.assertEqual(first.report["cache_hits"],0)
        second=self.analyze()
        self.assertEqual(second.report["cache_hits"],1)
        self.assertEqual(self.detector.calls,1)
        self.assertEqual(first.layout,second.layout)

    def test_cache_refresh_and_source_change(self):
        self.analyze()
        self.analyze(refresh=True)
        self.assertEqual(self.detector.calls,2)
        path=next(self.folder.glob("*.png"))
        with Image.open(path) as image:
            copy=image.copy()
        copy.putpixel((200,600),(255,0,0,255))
        copy.save(path)
        copy.close()
        result=self.analyze()
        self.assertEqual(self.detector.calls,3)
        self.assertEqual(result.report["cache_hits"],0)

    def test_missing_multiple_and_low_confidence_faces_hold_entire_team(self):
        for faces in ((),(Face(Rect(175,400,225,450),0.1),),
                      (Face(Rect(175,400,225,450),0.9),Face(Rect(180,401,230,451),0.9))):
            self.detector=FakeDetector(faces)
            result=self.analyze(refresh=True)
            self.assertIsNone(result.layout)
            self.assertEqual(len(result.report["athletes"]),1)
            self.assertEqual(result.report["status"],"review")
            self.assertEqual(len(result.report["athletes"][0]["faces"]),len(faces))

    def test_unknown_capture_scale_is_never_guessed(self):
        self.data["athletes"][0]["capture"]["source_scale"]=None
        result=self.analyze()
        self.assertIsNone(result.layout)
        self.assertIn("confirmation",result.report["athletes"][0]["issues"][0])

    def test_manual_head_changes_rebuild_without_detector(self):
        self.data["athletes"][0]["manual_head"]=[155,380,245,470]
        result=self.analyze()
        self.assertIsNotNone(result.layout)
        self.assertEqual(self.detector.calls,0)
        self.data["athletes"][0]["manual_head"]=[156,381,244,470]
        self.assertNotEqual(result.layout,self.analyze().layout)
        self.assertEqual(self.detector.calls,0)

    def test_duplicate_content_and_unlisted_pngs_require_review(self):
        shutil.copyfile(next(self.folder.glob("*.png")),self.folder/"duplicate.png")
        result=self.analyze()
        self.assertTrue(any("missing from manifest" in i for i in result.report["issues"]))
        duplicate=dict(self.data["athletes"][0],id="duplicate",file="duplicate.png")
        self.data["athletes"].append(duplicate)
        result=self.analyze()
        self.assertTrue(any("Duplicate image" in i for i in result.report["athletes"][1]["issues"]))

    def test_invalid_and_missing_images_are_recorded(self):
        next(self.folder.glob("*.png")).write_bytes(b"broken png")
        self.assertIsNone(self.analyze().layout)
        next(self.folder.glob("*.png")).unlink()
        self.assertTrue(self.analyze().report["athletes"][0]["issues"])

    def test_unavailable_detector_is_not_cached(self):
        class Unavailable(FakeDetector):
            def detect(self,image):
                self.calls+=1
                raise DetectionUnavailable("Test detector unavailable")
        self.detector=Unavailable()
        self.assertIsNone(self.analyze().layout)
        self.assertIsNone(self.analyze().layout)
        self.assertEqual(self.detector.calls,2)

    def test_explicit_exclusion_still_accounts_for_source(self):
        extra=self.folder/"excluded.png"
        extra.write_bytes(b"not an athlete")
        self.data["athletes"].append({"id":"excluded","file":"excluded.png",
                                     "excluded":True,"exclusion_reason":"Wrong source image"})
        result=self.analyze()
        self.assertIsNotNone(result.layout)
        self.assertEqual(result.report["athletes"][1]["status"],"excluded")

    def test_source_change_between_analysis_and_render_is_rejected(self):
        self.data["athletes"][0]["manual_head"]=[155,380,245,470]
        result=self.analyze()
        bg,fg=self.root/"background.jpg",self.root/"foreground.png"
        with Image.new("RGB",(900,300),"navy") as image:
            image.save(bg)
        with Image.new("RGBA",(900,300)) as image:
            image.save(fg)
        path=next(iter(result.sources.values()))
        with Image.open(path) as image:
            changed=image.copy()
        changed.putpixel((200,600),(255,0,0,255))
        changed.save(path)
        changed.close()
        output=self.root/"team.jpg"
        with self.assertRaisesRegex(ValueError,"content changed"):
            render(result.layout,result.sources,bg,fg,"Test",output,expected_hashes=result.hashes)
        self.assertFalse(output.exists())

    def test_cli_inventory_and_manual_manifest_analysis(self):
        out=self.root/"inventory"
        subprocess.run([sys.executable,"-m","pilot_teambuilder.cli","--inspect",str(self.folder),
                        "--output",str(out)],check=True,capture_output=True)
        self.assertTrue((out/"team-manifest.json").is_file())
        self.data["athletes"][0]["manual_head"]=[155,380,245,470]
        self.path.write_text(json.dumps(self.data))
        subprocess.run([sys.executable,"-m","pilot_teambuilder.cli","--manifest",str(self.path),
                        "--output",str(self.root/"analysis")],check=True,capture_output=True)
        report=json.loads((self.root/"analysis/analysis.json").read_text())
        self.assertEqual(report["status"],"ready")

    def test_cli_manifest_renders_one_full_panorama(self):
        self.data["athletes"][0]["manual_head"]=[155,380,245,470]
        self.path.write_text(json.dumps(self.data))
        templates=self.root/"Templates/Team"
        templates.mkdir(parents=True)
        with Image.new("RGB",(900,300),"navy") as image:
            image.save(templates/"Team Background_Default.jpg")
        with Image.new("RGBA",(900,300)) as image:
            image.save(templates/"Team Foreground_default.png")
        output=self.root/"rendered"
        subprocess.run([sys.executable,"-m","pilot_teambuilder.cli","--manifest",str(self.path),
                        "--templates",str(templates),"--output",str(output)],check=True,capture_output=True)
        self.assertEqual([p.name for p in output.glob("*.jpg")],["team.jpg"])
        with Image.open(output/"team.jpg") as image:
            self.assertEqual(image.size,(7800,2400))
        self.assertIn("render",json.loads((output/"analysis.json").read_text()))

    def test_cli_uncalibrated_manifest_writes_review_report(self):
        self.data["athletes"][0]["capture"]["source_scale"]=None
        self.data["athletes"][0]["manual_head"]=[155,380,245,470]
        self.path.write_text(json.dumps(self.data))
        output=self.root/"review"
        run=subprocess.run([sys.executable,"-m","pilot_teambuilder.cli","--manifest",str(self.path),
                            "--output",str(output)],capture_output=True)
        self.assertEqual(run.returncode,2)
        self.assertEqual(json.loads((output/"analysis.json").read_text())["status"],"review")
        self.assertFalse((output/"team.jpg").exists())


if __name__ == "__main__":
    unittest.main()
