import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from pilot_teambuilder.composition import compose
from pilot_teambuilder.model import Athlete, Rect
from pilot_teambuilder.rendering import (
    TemplateCache,
    discover_templates,
    render,
    template_artwork_box,
)
from pilot_teambuilder.rows import RowTable
from pilot_teambuilder.synthetic import athletes, write_sources


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.bg=self.root/"Team Background_Default.JPG"
        self.fg=self.root/"Team Foreground_default.png"
        with Image.new("RGB",(900,300),"#0B1F3A") as image:
            image.save(self.bg)
        with Image.new("RGBA",(900,300),(255,0,0,0)) as image:
            image.putpixel((0,0),(0,255,0,255))
            image.save(self.fg)

    def test_defaults_case_and_duplicates(self):
        self.assertEqual(discover_templates(self.root),(self.bg,self.fg))
        (self.root/"Team Background_default.jpeg").write_bytes(self.bg.read_bytes())
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            discover_templates(self.root)

    def test_invalid_template_ratio_and_alpha(self):
        cache=TemplateCache()
        self.addCleanup(cache.close)
        with Image.new("RGB",(300,300)) as image:
            image.save(self.bg)
        with self.assertRaisesRegex(ValueError,"24 × 8-inch.*26 × 8-inch"):
            cache.load(self.bg,False)
        with Image.new("RGBA",(900,300),"white") as image:
            image.save(self.fg)
        with self.assertRaisesRegex(ValueError,"transparent"):
            cache.load(self.fg,True)

    def test_artwork_box_accepts_print_canvas_and_pixel_rounding(self):
        for size, right in (((7200, 2400), 7200), ((7800, 2400), 7200),
                            ((3900, 1200), 3600), ((900, 301), 900),
                            ((7801, 2400), round(7801 * 24 / 26)), ((13, 4), 12)):
            with self.subTest(size=size):
                self.assertEqual(template_artwork_box(size), (0, 0, right, size[1]))
        with self.assertRaisesRegex(ValueError, "300 × 300 px"):
            template_artwork_box((300, 300))

    def test_full_print_layers_exclude_strip_instead_of_squeezing_artwork(self):
        with Image.new("RGB", (975, 300), "#0B1F3A") as image:
            ImageDraw.Draw(image).rectangle((900, 0, 974, 299), fill="magenta")
            image.save(self.bg, quality=100, subsampling=0)
        with Image.new("RGBA", (975, 300)) as image:
            ImageDraw.Draw(image).rectangle((900, 0, 974, 299), fill="red")
            ImageDraw.Draw(image).rectangle((0, 0, 30, 30), fill="green")
            image.save(self.fg)
        original = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.bg, self.fg)}
        cache = TemplateCache()
        self.addCleanup(cache.close)
        bg = cache.load(self.bg, False, (900, 300))
        fg = cache.load(self.fg, True, (900, 300))
        self.assertLess(bg.getpixel((875, 150))[0], 20)
        self.assertEqual(fg.getpixel((875, 150))[3], 0)
        self.assertEqual(fg.getpixel((15, 15)), (0, 128, 0, 255))
        self.assertEqual(original, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in original})

    def test_foreground_transparency_must_be_in_artwork_not_just_discard_strip(self):
        with Image.new("RGBA", (975, 300), "red") as image:
            ImageDraw.Draw(image).rectangle((900, 0, 974, 299), fill=(0, 0, 0, 0))
            image.save(self.fg)
        cache = TemplateCache()
        self.addCleanup(cache.close)
        with self.assertRaisesRegex(ValueError, "artwork has no transparent"):
            cache.load(self.fg, True)

    def test_cache_reuse_and_bound(self):
        cache=TemplateCache()
        self.addCleanup(cache.close)
        first=cache.load(self.bg,False)
        self.assertIs(first,cache.load(self.bg,False))
        cache.load(self.fg,True)
        alternative=self.root/"alternative.jpg"
        alternative.write_bytes(self.bg.read_bytes())
        cache.load(alternative,False)
        self.assertEqual(len(cache.layers),2)

    def test_full_jpeg_dimensions_dpi_footer_foreground_and_no_overwrite(self):
        people=athletes(3)
        sources=write_sources(self.root/"sources",people)
        hashes={key:hashlib.sha256(path.read_bytes()).hexdigest() for key,path in sources.items()}
        layout=compose(people,RowTable({3:(3,)}))
        output=self.root/"final.jpg"
        result=render(layout,sources,self.bg,self.fg,"Test Team",output)
        self.assertGreater(result["timings"]["total_seconds"],0)
        with Image.open(output) as image:
            self.assertEqual(image.size,(7800,2400))
            self.assertEqual(image.info["dpi"],(300,300))
            self.assertEqual(image.mode,"RGB")
            self.assertGreater(image.getpixel((0,0))[1],100)
            self.assertEqual(image.crop((7200,0,7800,2400)).getextrema()[0],(0,255))
        with self.assertRaises(FileExistsError):
            render(layout,sources,self.bg,self.fg,"Test Team",output)
        self.assertEqual(hashes,{k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in sources.items()})
        self.assertFalse(list(self.root.glob(".team-*")))

    def test_source_changes_fail_without_publishing(self):
        people=athletes(1)
        sources=write_sources(self.root/"sources",people)
        layout=compose(people,RowTable({1:(1,)}))
        with Image.new("RGBA",(300,300), "red") as image:
            image.save(next(iter(sources.values())))
        output=self.root/"final.jpg"
        with self.assertRaisesRegex(ValueError,"dimensions changed"):
            render(layout,sources,self.bg,self.fg,"Test",output)
        self.assertFalse(output.exists())

    def test_tiny_cutout_on_large_transparent_canvas_crops_before_scaling(self):
        source=self.root/"tiny.png"
        with Image.new("RGBA",(1000,1000)) as image:
            for x in range(500,510):
                for y in range(500,510):
                    image.putpixel((x,y),(255,0,0,255))
            image.save(source)
        person=Athlete("tiny",(1000,1000),Rect(500,500,510,510),
                       Rect(503,500,507,504),10.0,1.0)
        layout=compose([person],RowTable({1:(1,)}))
        output=self.root/"tiny-team.jpg"
        render(layout,{"tiny":source},self.bg,self.fg,"Tiny cutout",output)
        with Image.open(output) as image:
            self.assertGreater(image.getpixel((3600,1000))[0],200)


if __name__ == "__main__":
    unittest.main()
