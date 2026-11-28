"""Live filename changes reconcile state without decoding sources or reverting edits."""
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from pilot_teambuilder.inventory_watch import (
    InventoryScanner,
    apply_inventory,
    merge_processing_result,
)
from pilot_teambuilder.review import ReviewSession, new_job, validate_job
from pilot_teambuilder.synthetic import athletes, write_sources


class LiveInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.incoming = self.root / 'Incoming'
        self.folder = self.incoming / 'One team'
        self.paths = write_sources(self.folder, athletes(3))
        self.scanner = InventoryScanner()
        self.job = apply_inventory(new_job(self.incoming), self.scanner.scan(self.incoming))

    def test_discovery_never_decodes_sources_or_starts_output(self):
        write_sources(self.incoming / 'Nested/Another team', athletes(2))
        with patch('pilot_teambuilder.sources.load_png', side_effect=AssertionError('PNG decode during folder scan')):
            updated = apply_inventory(self.job, self.scanner.scan(self.incoming))
        self.assertEqual(len(updated['teams']), 2)
        self.assertFalse(Path(updated['output']).exists())

    def test_deleting_an_athlete_removes_all_stale_corrections(self):
        session = ReviewSession(self.job)
        entry = session.manifest['athletes'][1]
        identity = entry['id']
        session.offset(identity, 0.125, 0.25)
        session.team['anchors'][identity] = {'height_top': 200}
        session.team['review_hashes'] = {identity: 'old-hash'}
        session.manifest['corrections']['rows'] = [[e['id'] for e in session.manifest['athletes']]]
        (self.folder / entry['file']).unlink()
        updated = apply_inventory(session.job, self.scanner.scan(self.incoming))
        team = updated['teams'][0]
        self.assertEqual(len(team['manifest']['athletes']), 2)
        self.assertNotIn(identity, team['manifest']['corrections']['offsets_in'])
        self.assertNotIn(identity, team['anchors'])
        self.assertNotIn(identity, team['review_hashes'])
        self.assertNotIn('rows', team['manifest']['corrections'])
        validate_job(updated, self.root)

    def test_renaming_preserves_athlete_identity_exclusion_and_manual_rows(self):
        session = ReviewSession(self.job)
        entry = session.manifest['athletes'][1]
        identity = entry['id']
        session.offset(identity, 0.125, 0)
        session.manifest['corrections']['rows'] = [[e['id'] for e in session.manifest['athletes']]]
        source = self.folder / entry['file']
        source.rename(source.with_name('Renamed_Orange.png'))
        updated = apply_inventory(session.job, self.scanner.scan(self.incoming))
        renamed = next(e for e in updated['teams'][0]['manifest']['athletes'] if e['file'] == 'Renamed_Orange.png')
        self.assertEqual(renamed['id'], identity)
        self.assertEqual(updated['teams'][0]['manifest']['corrections'], session.manifest['corrections'])

    def test_processing_result_cannot_restore_a_deleted_file(self):
        submitted = deepcopy(self.job)
        removed = submitted['teams'][0]['manifest']['athletes'][1]
        (self.folder / removed['file']).unlink()
        current = apply_inventory(submitted, self.scanner.scan(self.incoming))
        result = {'job': submitted, 'statuses': {str(self.folder): {'state': 'Completed'}}}
        merged = merge_processing_result(current, result, submitted)
        self.assertEqual(len(merged['teams'][0]['manifest']['athletes']), 2)
        self.assertEqual(merged['teams'][0]['production']['state'], 'Queued')

    def test_processing_result_cannot_erase_an_exclusion_or_new_job_defaults(self):
        submitted = deepcopy(self.job)
        session = ReviewSession(submitted)
        identity = session.manifest['athletes'][0]['id']
        session.exclude(identity)
        session.settings({'overlap_fraction': 0.25})
        result = {'job': submitted, 'statuses': {str(self.folder): {'state': 'Needs attention', 'message': 'old error'}}}
        merged = merge_processing_result(session.job, result, submitted)
        self.assertTrue(next(e for e in merged['teams'][0]['manifest']['athletes'] if e['id'] == identity)['excluded'])
        self.assertEqual(merged['job_settings']['overlap_fraction'], 0.25)
        self.assertNotIn('old error', merged['teams'][0]['production'].get('message', ''))

    def test_a_new_processing_inventory_is_adopted_when_the_ui_has_no_edits(self):
        submitted = deepcopy(self.job)
        write_sources(self.incoming / 'Another team', athletes(1))
        worker_job = apply_inventory(submitted, self.scanner.scan(self.incoming))
        result = {'job': worker_job, 'statuses': {str(self.folder): {'state': 'Completed'}}}
        merged = merge_processing_result(submitted, result, submitted)
        self.assertEqual(len(merged['teams']), 2)
        original = next(t for t in merged['teams'] if t['manifest']['source_folder'] == str(self.folder))
        self.assertEqual(original['production']['state'], 'Completed')

    def test_empty_or_removed_team_disappears_without_missing_file_errors(self):
        for path in self.paths.values():
            path.unlink()
        updated = apply_inventory(self.job, self.scanner.scan(self.incoming))
        self.assertEqual(updated['teams'], [])
        validate_job(updated, self.root)

    def test_an_offline_hotfolder_keeps_the_previous_inventory(self):
        missing = self.root / 'Missing'
        with self.assertRaises(OSError):
            self.scanner.scan(missing)
        self.assertEqual(len(self.job['teams']), 1)

    def test_a_scan_from_an_old_job_is_rejected(self):
        other = self.root / 'Other'
        other.mkdir()
        with self.assertRaisesRegex(ValueError, 'another hotfolder'):
            apply_inventory(self.job, self.scanner.scan(other))
