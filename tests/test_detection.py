import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pilot_teambuilder.detection import Face, VisionDetector, parse_faces
from pilot_teambuilder.detection_cache import DetectionCache
from pilot_teambuilder.model import Rect
from pilot_teambuilder.sources import load_png


class DetectionTests(unittest.TestCase):
    def test_packaged_helper_is_resolved_inside_bundle(self):
        with patch("pilot_teambuilder.detection.sys._MEIPASS", "/tmp/test-bundle", create=True):
            self.assertEqual(VisionDetector().helper,Path("/tmp/test-bundle/bin/TeamFaceDetector"))

    def test_parser_retains_all_faces_and_rejects_bad_coordinates(self):
        payload = {"schema_version":1,"width":100,"height":200,"faces":[
            {"x":1,"y":2,"width":10,"height":20,"confidence":0.9},
            {"x":70,"y":20,"width":10,"height":20,"confidence":0.2}]}
        self.assertEqual(len(parse_faces(payload,(100,200))),2)
        payload["faces"][1]["x"] = 99
        with self.assertRaises(ValueError):
            parse_faces(payload,(100,200))
        with self.assertRaises(ValueError):
            parse_faces(payload,(200,100))

    def test_reduced_resolution_mapping_and_cpu_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            helper=Path(folder)/"helper"
            helper.write_text("fake helper")
            detector=VisionDetector(helper)
            result=subprocess.CompletedProcess([],0,json.dumps({"schema_version":1,"width":800,"height":1600,
                "faces":[{"x":10,"y":20,"width":30,"height":40,"confidence":0.9},
                         {"x":400,"y":200,"width":30,"height":40,"confidence":0.1}]}),"")
            with patch("pilot_teambuilder.detection.platform.system",return_value="Darwin"), \
                 patch("pilot_teambuilder.detection.subprocess.run",side_effect=[
                     subprocess.CalledProcessError(1,"helper"),result]) as run, \
                 Image.new("RGBA",(1600,3200),"red") as image:
                faces=detector.detect(image)
            self.assertEqual(len(faces),2)
            self.assertEqual(faces[0].box,Rect(20,40,80,120))
            self.assertEqual(run.call_args_list[-1].args[0][-1],"--cpu")

    def test_cache_is_durable_and_invalidates_model_and_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"detections.sqlite3"
            with DetectionCache(path) as cache:
                key=cache.key("source-v1","model-v1")
                cache.put(key,(100,200),(Face(Rect(1,2,11,22),0.9),))
            with DetectionCache(path) as cache:
                self.assertEqual(len(cache.get(key,(100,200))),1)
                self.assertIsNone(cache.get(cache.key("source-v2","model-v1"),(100,200)))
                self.assertIsNone(cache.get(cache.key("source-v1","model-v2"),(100,200)))
                self.assertIsNone(cache.get(key,(200,100)))

    def test_corrupt_cache_payload_is_discarded(self):
        with tempfile.TemporaryDirectory() as folder, DetectionCache(Path(folder)/"cache.sqlite3") as cache:
            with cache.db:
                cache.db.execute("INSERT INTO detections VALUES ('broken','not json')")
            self.assertIsNone(cache.get("broken",(100,200)))

    def test_zero_faces_can_be_cached_without_reinventing_a_head(self):
        with tempfile.TemporaryDirectory() as folder, DetectionCache(Path(folder)/"cache.sqlite3") as cache:
            cache.put("empty",(100,200),())
            self.assertEqual(cache.get("empty",(100,200)),())

    def test_loader_rejects_empty_png_and_enforces_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"source.png"
            with Image.new("RGBA",(100,200)) as image:
                image.save(path)
            with self.assertRaisesRegex(ValueError,"no visible"):
                load_png(path)
            with patch("pilot_teambuilder.sources.MAX_BYTES",10), self.assertRaisesRegex(ValueError,"128-MiB"):
                load_png(path)
            with patch("PIL.Image.MAX_IMAGE_PIXELS",100), self.assertRaisesRegex(ValueError,"safe decoded"):
                load_png(path)

    @unittest.skipUnless(sys.platform == "darwin", "Native Vision runs on macOS")
    def test_native_helper_runs_offline_in_default_and_cpu_modes(self):
        helper=Path(__file__).parents[1]/"bin/TeamFaceDetector"
        self.assertTrue(helper.is_file(),"CI must build the native helper before tests")
        with Image.new("RGBA",(400,600),"white") as image:
            self.assertEqual(VisionDetector(helper).detect(image),())
            self.assertEqual(VisionDetector(helper,cpu_only=True).detect(image),())


if __name__ == "__main__":
    unittest.main()
