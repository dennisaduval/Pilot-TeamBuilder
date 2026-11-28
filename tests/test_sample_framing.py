"""Sample-informed framing uses whole sources and one proportional team scale."""
import unittest
from dataclasses import replace

from pilot_teambuilder.composition import compose
from pilot_teambuilder.model import CompositionSettings, Rect
from pilot_teambuilder.rows import RowTable
from pilot_teambuilder.synthetic import athletes


class SampleFramingTests(unittest.TestCase):
    def setUp(self):
        self.settings = CompositionSettings(foreground_framing=1)

    def test_front_head_bottom_above_center_and_back_heads_inside_for_all_sizes(self):
        from importlib.resources import files
        table = RowTable.load(files("pilot_teambuilder").joinpath("data/row_counts.csv"))
        for count in (*range(1, 46), 64, 100):
            with self.subTest(count=count):
                layout = compose(athletes(count), table, self.settings)
                self.assertEqual(len(layout.placements), count)
                self.assertLessEqual(max(layout.row_counts), 16)
                self.assertEqual(layout.clip, Rect(0, 0, 7200, 2400))
                for p in layout.placements:
                    self.assertIsNone(p.source_visible)
                    self.assertTrue(self.settings.area.contains(p.head))
                    self.assertGreaterEqual(p.body.left, self.settings.area.left - 1e-7)
                    self.assertLessEqual(p.body.right, self.settings.area.right + 1e-7)
                front = [p for p in layout.placements if p.row == len(layout.row_counts) - 1]
                self.assertAlmostEqual(max(p.head.bottom for p in front), 1087.5)
                self.assertEqual(len({p.scale for p in layout.placements}), 1)

    def test_two_rows_are_slightly_smaller_than_single_row(self):
        people = athletes(8)
        one = compose(people, RowTable({8: (8,)}), self.settings)
        two = compose(people, RowTable({8: (4, 4)}), self.settings)
        self.assertLess(two.global_scale, one.global_scale)
        self.assertTrue(all(self.settings.area.contains(p.head) for p in two.placements))

    def test_wide_team_fills_width_and_small_team_is_not_stretched(self):
        wide = compose(athletes(16), RowTable({16: (16,)}), self.settings)
        span = max(p.body.right for p in wide.placements) - min(p.body.left for p in wide.placements)
        self.assertAlmostEqual(span, self.settings.area.width)
        small = compose(athletes(3), RowTable({3: (3,)}), self.settings)
        self.assertLess(max(p.body.right for p in small.placements) - min(p.body.left for p in small.placements), self.settings.area.width)

    def test_source_resize_is_undone_without_equalizing_head_sizes(self):
        people = athletes(3)
        original = compose(people, RowTable({3: (3,)}), self.settings)
        a = people[0]
        from pilot_teambuilder.composition import transform
        people[0] = replace(a, image_size=(800, 2000), body=transform(a.body, 2, 0, 0), head=transform(a.head, 2, 0, 0), source_scale=2)
        resized = compose(people, RowTable({3: (3,)}), self.settings)
        self.assertEqual(original.global_scale, resized.global_scale)
        for before, after in zip(original.placements, resized.placements):
            self.assertEqual(before.head, after.head)
            self.assertEqual(before.body, after.body)

    def test_deprecated_crop_field_never_truncates_source(self):
        layout = compose(athletes(3), RowTable({3: (3,)}), replace(self.settings, crop_body_fraction=0.55))
        self.assertTrue(all(p.source_visible is None for p in layout.placements))
        self.assertTrue(any(p.body.bottom > self.settings.area.bottom for p in layout.placements))
