"""Physical framing checks independent of the fitter's reported limiting scales."""

import unittest
from copy import deepcopy
from dataclasses import replace
from importlib.resources import files

from pilot_teambuilder.composition import compose
from pilot_teambuilder.model import Athlete, CompositionSettings, Corrections, Rect
from pilot_teambuilder.review import upgrade_job
from pilot_teambuilder.rows import RowTable
from pilot_teambuilder.synthetic import athletes


def uniform(count):
    person = Athlete('0', (600, 1400), Rect(100, 200, 400, 1200),
                     Rect(180, 200, 320, 340), 1000, 1)
    return [replace(person, id=str(i)) for i in range(count)]


class AdaptiveFramingTests(unittest.TestCase):
    def setUp(self):
        self.settings = CompositionSettings(transparency_mode=1, foreground_framing=1,
                                            v_step_in=0.125, v_max_drop_in=0.5)

    def fractions(self, layout, settings=None):
        settings = settings or self.settings
        line = settings.foreground_line_in * 300 or settings.area.bottom
        return [(line - p.body.top) / p.body.height for p in layout.placements
                if p.row == len(layout.row_counts) - 1]

    def test_medium_team_grows_beyond_old_cap_and_fills_width_within_range(self):
        people = uniform(10)
        layout = compose(people, RowTable({10: (10,)}), self.settings)
        width = max(p.body.right for p in layout.placements) - min(p.body.left for p in layout.placements)
        self.assertAlmostEqual(width, 6600)
        self.assertGreater(layout.global_scale, 1800 / (1000 * 0.75))
        self.assertTrue(all(0.5 - 1e-7 <= f <= 0.75 + 1e-7 for f in self.fractions(layout)))
        self.assertFalse(layout.fit_notes)

    def test_small_teams_stop_at_waist_and_remain_centered(self):
        for count in (1, 2, 5, 9):
            with self.subTest(count=count):
                layout = compose(uniform(count), RowTable({count: (count,)}), self.settings)
                left = min(p.body.left for p in layout.placements)
                right = max(p.body.right for p in layout.placements)
                self.assertLess(right - left, 6600)
                self.assertAlmostEqual((right + left) / 2, 3600)
                self.assertAlmostEqual(min(self.fractions(layout)), 0.5)
                # The group cannot grow further without hiding above the waist.
                self.assertAlmostEqual(layout.global_scale, 937.5 / (500 - 140))

    def test_job_size_preferences_stay_inside_framing_range(self):
        for percent in (0.25, 0.75, 0.92, 1, 1.5, 3):
            with self.subTest(percent=percent):
                layout = compose(uniform(3), RowTable({3: (3,)}), replace(self.settings, group_scale=percent))
                self.assertTrue(all(0.5 - 1e-7 <= f <= 0.75 + 1e-7 for f in self.fractions(layout)))

    def test_vertical_offsets_and_manual_moves_cannot_tighten_past_waist(self):
        for offset in (-4, -0.625, 0, 0.625, 4):
            with self.subTest(offset=offset):
                settings = replace(self.settings, y_offset_in=offset)
                layout = compose(uniform(8), RowTable({8: (8,)}), settings,
                                 Corrections(offsets_in={'0': (0.125, 0.25), '1': (0, -0.125)}))
                self.assertTrue(all(f >= 0.5 - 1e-7 for f in self.fractions(layout, settings)))
                self.assertTrue(all(settings.area.contains(p.head) for p in layout.placements))
                self.assertEqual(layout.issues, ())

    def test_template_cover_reference_changes_fit_without_trimming_sources(self):
        people = uniform(3)
        high = compose(people, RowTable({3: (3,)}), replace(self.settings, foreground_line_in=5.75))
        low = compose(people, RowTable({3: (3,)}), replace(self.settings, foreground_line_in=7.25))
        self.assertGreater(low.global_scale, high.global_scale)
        for layout, line in ((high, 5.75), (low, 7.25)):
            self.assertAlmostEqual(layout.framing['cover_line_in'], line)
            for p in layout.placements:
                self.assertIsNone(p.source_visible)
                self.assertEqual(p.source_body, people[int(p.athlete_id)].body)
                self.assertAlmostEqual(p.body.height / p.source_body.height, layout.global_scale)

    def test_multi_row_style_and_top_limits_keep_all_heads_inside(self):
        people = uniform(8)
        one = compose(people, RowTable({8: (8,)}), self.settings)
        two = compose(people, RowTable({8: (4, 4)}), self.settings)
        self.assertLessEqual(two.global_scale, one.global_scale * 0.9)
        self.assertTrue(all(0.5 - 1e-7 <= f <= 0.75 + 1e-7 for f in self.fractions(two)))
        large = compose(athletes(42), RowTable({42: (15, 14, 13)}), self.settings)
        self.assertEqual(len(large.placements), 42)
        self.assertTrue(all(self.settings.area.contains(p.head) for p in large.placements))
        self.assertEqual(large.issues, ())
        self.assertTrue(large.fit_notes)  # Exact rows/top bounds take precedence.

    def test_every_csv_size_and_larger_team_is_proportional_and_deterministic(self):
        table = RowTable.load(files('pilot_teambuilder').joinpath('data/row_counts.csv'))
        for count in (*range(1, 46), 64, 100):
            with self.subTest(count=count):
                people = athletes(count)
                layout = compose(people, table, self.settings)
                self.assertEqual(layout, compose(list(reversed(people)), table, self.settings))
                self.assertEqual(len({p.scale for p in layout.placements}), 1)
                self.assertEqual(len(layout.placements), count)
                self.assertGreaterEqual(min(self.fractions(layout)), 0.5 - 1e-7)
                if max(self.fractions(layout)) > 0.75 + 1e-7:
                    self.assertTrue(layout.fit_notes)

    def test_mixed_heights_and_wide_poses_keep_incoming_proportions(self):
        people = uniform(9)
        people[0] = replace(people[0], body=Rect(10, 150, 590, 1300),
                            head=Rect(180, 150, 320, 311), relative_height=1150)
        people[1] = replace(people[1], image_size=(800, 1700),
                            body=Rect(80, 400, 420, 1450), head=Rect(180, 400, 320, 547), relative_height=1050)
        layout = compose(people, RowTable({9: (9,)}), self.settings)
        for p in layout.placements:
            self.assertAlmostEqual(p.body.width / p.source_body.width, p.body.height / p.source_body.height)
            self.assertTrue(self.settings.area.contains(p.head))
        self.assertGreaterEqual(min(self.fractions(layout)), 0.5 - 1e-7)

    def test_cover_line_validation_explains_invalid_job_setting(self):
        for line in (-1, 3, 9):
            with self.subTest(line=line), self.assertRaisesRegex(ValueError, 'Foreground cover line'):
                replace(self.settings, foreground_line_in=line).validate()
        replace(self.settings, foreground_line_in=0).validate()

    def test_upgrade_preserves_completed_receipts_and_non_destructive_edits(self):
        old = {'framing_version': 4, 'base_scale': 0.2,
               'job_settings': {'transparency_mode': 1, 'group_scale': 0.92, 'y_offset_in': -0.625},
               'template_presets': {'custom': {'overlap_fraction': 0.5}},
               'teams': [{'anchors': {'a': [10, 20]}, 'production': {'state': 'Completed', 'key': 'receipt'},
                          'manifest': {'corrections': {'offsets_in': {'a': [0.125, 0]}}}}]}
        before = deepcopy(old)
        upgraded = upgrade_job(old)
        self.assertEqual(upgraded['framing_version'], 5)
        self.assertIsNone(upgraded['base_scale'])
        self.assertEqual(upgraded['teams'], before['teams'])
        self.assertEqual(upgraded['job_settings']['group_scale'], 0.92)
        self.assertEqual(upgraded['template_presets'], before['template_presets'])
        self.assertEqual(upgrade_job(upgraded), upgraded)
        self.assertEqual(old, before)
