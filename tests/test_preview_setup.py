"""Preview-folder setup is non-producing; fitting maximizes each later team."""
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

from PIL import Image
from test_fast_placement import NoDetector

from pilot_teambuilder.composition import compose
from pilot_teambuilder.desktop_engine import DesktopEngine
from pilot_teambuilder.model import Athlete, CompositionSettings, Rect
from pilot_teambuilder.production import ProductionEngine
from pilot_teambuilder.review import (
    JobStore,
    ReviewSession,
    new_job,
    select_preview_folder,
    upgrade_job,
)
from pilot_teambuilder.rows import RowTable
from pilot_teambuilder.synthetic import athletes, write_sources


class PreviewSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.incoming = self.root / 'Incoming'
        self.incoming.mkdir()
        self.preview = self.root / 'Preview/Setup team'
        write_sources(self.preview, athletes(5))
        self.job = new_job(self.incoming, allow_empty=True)
        self.job['settle_seconds'] = 2
        templates = Path(self.job['templates'])
        templates.mkdir(parents=True)
        with Image.new('RGB', (900, 300), 'navy') as image:
            image.save(templates / 'Team Background_default.jpg')
        with Image.new('RGBA', (900, 300)) as image:
            image.save(templates / 'Team Foreground_default.png')

    def test_external_preview_keeps_hotfolder_and_destinations_and_does_not_export(self):
        job = select_preview_folder(self.job, self.preview)
        self.assertEqual(job['incoming'], str(self.incoming))
        self.assertEqual(job['output'], self.job['output'])
        self.assertEqual(job['preview_team_source'], str(self.preview))
        self.assertTrue(job['teams'][0]['preview_only'])
        engine = DesktopEngine(self.root / 'cache.sqlite3', NoDetector())
        self.addCleanup(engine.close)
        view = engine.view(job, None)
        self.addCleanup(view.close)
        self.assertIsNotNone(view.analysis.layout)
        self.assertFalse(Path(job['output']).exists())
        saved = self.root / 'job.json'
        store = JobStore()
        store.save(job, saved)
        restored = JobStore().load(saved)
        self.assertEqual(restored['preview_team_source'], str(self.preview))
        self.assertTrue(restored['teams'][0]['preview_only'])

    def test_preview_reselection_preserves_manual_corrections_without_duplicate_team(self):
        session = ReviewSession(select_preview_folder(self.job, self.preview))
        identity = session.manifest['athletes'][0]['id']
        session.offset(identity, 0.125, -0.125)
        session.settings({'overlap_fraction': 0.2, 'row_separation_heads': 1.75, 'v_step_in': 0.15})
        again = select_preview_folder(session.job, self.preview)
        self.assertEqual(len(again['teams']), 1)
        self.assertEqual(again['teams'][0]['manifest']['corrections'], session.manifest['corrections'])
        self.assertEqual(again['job_settings'], session.job['job_settings'])

    def test_preview_parent_with_multiple_teams_has_a_specific_error(self):
        write_sources(self.preview.parent / 'Other team', athletes(1))
        with self.assertRaisesRegex(ValueError, 'Choose one team folder'):
            select_preview_folder(self.job, self.preview.parent)

    def test_a_removed_external_preview_does_not_block_saved_defaults_or_production(self):
        import shutil
        job = select_preview_folder(self.job, self.preview)
        shutil.rmtree(self.preview)
        path = self.root / 'saved.json'
        JobStore().save(job, path)
        restored = JobStore().load(path)
        self.assertEqual(restored['job_settings'], job['job_settings'])
        write_sources(self.incoming / 'Actual team', athletes(1))
        production = ProductionEngine(self.root / 'state', NoDetector())
        self.addCleanup(production.close)
        waiting = production.tick(restored, now=0)
        done = production.tick(waiting['job'], now=3)
        self.assertEqual(done['statuses'][str((self.incoming / 'Actual team').resolve())]['state'], 'Completed')

    def test_preview_outside_hotfolder_never_enters_automatic_output(self):
        session = ReviewSession(select_preview_folder(self.job, self.preview))
        defaults = {'overlap_fraction': 0.2, 'row_separation_heads': 1.75, 'v_step_in': 0.15}
        session.settings(defaults)
        production = ProductionEngine(self.root / 'state', NoDetector())
        self.addCleanup(production.close)
        empty = production.tick(session.job, now=0)
        self.assertFalse(list(Path(self.job['output']).rglob('*.jpg')))
        write_sources(self.incoming / 'Actual team', athletes(3))
        waiting = production.tick(empty['job'], now=1)
        done = production.tick(waiting['job'], now=4)
        self.assertEqual(done['statuses'][str((self.incoming / 'Actual team').resolve())]['state'], 'Completed')
        self.assertNotIn(str(self.preview), done['statuses'])
        outputs = list(Path(self.job['output']).rglob('*.jpg'))
        self.assertEqual(len(outputs), 1)
        self.assertIn('Actual team', outputs[0].name)
        actual = next(t for t in done['job']['teams'] if t['manifest']['team_name'] == 'Actual team')
        for key, value in defaults.items():
            self.assertEqual(actual['manifest']['settings'][key], value)
        self.assertIsNone(done['job']['base_scale'])

    def test_a_preview_inside_incoming_is_an_ordinary_team_when_processing_starts(self):
        folder = self.incoming / 'Actual team'
        write_sources(folder, athletes(3))
        job = select_preview_folder(new_job(self.incoming), folder)
        self.assertFalse(job['teams'][0]['preview_only'])
        self.assertFalse(Path(job['output']).exists())

    def test_upgrade_discards_old_fast_calibration_once_but_keeps_defaults(self):
        old = deepcopy(self.job)
        old['framing_version'] = 3
        old['base_scale'] = 0.1
        old['job_settings']['overlap_fraction'] = 0.25
        upgraded = upgrade_job(old)
        self.assertIsNone(upgraded['base_scale'])
        self.assertEqual(upgraded['job_settings']['overlap_fraction'], 0.25)
        self.assertEqual(upgrade_job(upgraded), upgraded)


class MaximalFitTests(unittest.TestCase):
    def setUp(self):
        self.settings = CompositionSettings(transparency_mode=1, foreground_framing=1,
                                            v_step_in=0.125, v_max_drop_in=0.5)

    def test_all_team_sizes_keep_front_anchor_and_reach_a_real_fit_limit(self):
        table = RowTable.load(files('pilot_teambuilder').joinpath('data/row_counts.csv'))
        for count in (*range(1, 46), 64, 100):
            with self.subTest(count=count):
                layout = compose(athletes(count), table, self.settings)
                front = [p for p in layout.placements if p.row == len(layout.row_counts) - 1]
                self.assertAlmostEqual(max(p.head.bottom for p in front), 1087.5)
                top = min(p.head.top for p in layout.placements)
                width = max(p.body.right for p in layout.placements) - min(p.body.left for p in layout.placements)
                framed = max(p.source_body.height for p in front) * 0.75 * layout.global_scale
                target = self.settings.area.height * (0.9 if len(layout.row_counts) > 1 else 1)
                self.assertTrue(abs(width - 6600) < 1e-5 or abs(top - 225) < 1e-5 or abs(framed - target) < 1e-5,
                                'The group can still grow without reaching a framing, width or top limit')
                for p in layout.placements:
                    self.assertTrue(self.settings.area.contains(p.head))
                self.assertEqual(layout.issues, ())
                self.assertEqual(len({p.scale for p in layout.placements}), 1)

    def test_later_smaller_teams_can_grow_beyond_the_first_team_scale(self):
        table = RowTable({1: (1,), 16: (16,)})
        large = compose(athletes(16), table, self.settings)
        small = compose(athletes(1), table, self.settings, fixed_scale=large.global_scale)
        independent = compose(athletes(1), table, self.settings)
        self.assertGreater(small.global_scale, large.global_scale)
        self.assertEqual(small.global_scale, independent.global_scale)

    def test_front_row_controls_framing_instead_of_the_tall_back_row(self):
        people = [Athlete(str(i), (500, 2500), Rect(100, 2500 - h, 400, 2450),
                          Rect(200, 2500 - h, 300, 2500 - h + 100), h, 1)
                  for i, h in enumerate((2100, 1900, 800, 700))]
        layout = compose(people, RowTable({4: (2, 2)}), self.settings)
        front = [p for p in layout.placements if p.row == 1]
        self.assertAlmostEqual(layout.reference_scale, 1800 / (max(p.source_body.height for p in front) * 0.75))
        self.assertEqual({p.scale for p in layout.placements}, {layout.global_scale})

    def test_row_spacing_reduces_scale_without_shifting_front_heads_down(self):
        table = RowTable({27: (9, 9, 9)})
        normal = compose(athletes(27), table, self.settings)
        separated = compose(athletes(27), table, replace(self.settings, row_separation_heads=2.5))
        self.assertLess(separated.global_scale, normal.global_scale)
        for layout in (normal, separated):
            self.assertAlmostEqual(max(p.head.bottom for p in layout.placements if p.row == 2), 1087.5)
            self.assertAlmostEqual(min(p.head.top for p in layout.placements), 225)

    def test_small_multi_row_team_gets_the_ten_percent_framing_reduction(self):
        person = Athlete('one', (400, 1000), Rect(90, 300, 310, 970), Rect(155, 300, 245, 390), 700, 1)
        people = [replace(person, id=str(i)) for i in range(8)]
        one = compose(people, RowTable({8: (8,)}), self.settings)
        two = compose(people, RowTable({8: (4, 4)}), self.settings)
        self.assertAlmostEqual(two.global_scale, one.global_scale * 0.9)
