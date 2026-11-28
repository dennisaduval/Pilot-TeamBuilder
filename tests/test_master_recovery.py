"""Finished-image recovery cannot accidentally send a second printing copy."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from test_fast_placement import NoDetector

from pilot_teambuilder.inventory_watch import snapshot
from pilot_teambuilder.production import ProductionEngine, file_hash
from pilot_teambuilder.review import ReviewSession, fingerprint, new_job
from pilot_teambuilder.synthetic import athletes, write_sources


class MasterRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.folder = self.root / 'Incoming/Team'
        self.sources = write_sources(self.folder, athletes(3))
        self.job = new_job(self.folder.parent)
        self.job['settle_seconds'] = 0
        self.job['routes'] = [str(self.root / 'Print')]
        templates = Path(self.job['templates'])
        templates.mkdir(parents=True)
        with Image.new('RGB', (900, 300), 'navy') as image:
            image.save(templates / 'Team Background_default.jpg')
        with Image.new('RGBA', (900, 300)) as image:
            image.save(templates / 'Team Foreground_default.png')
        self.engine = ProductionEngine(self.root / 'state', NoDetector())
        self.addCleanup(self.engine.close)

    def run_team(self):
        result = self.engine.tick(self.job, now=0)
        self.assertEqual(result['statuses'][str(self.folder)]['state'], 'Completed')
        return result

    def legacy_receipt(self, delete_output=False):
        finished = self.run_team()
        status = finished['statuses'][str(self.folder)]
        original_hash = file_hash(status['master'])
        hashes = [(p[0], file_hash(self.folder / p[0])) for p in snapshot(self.folder) if p[0].endswith('.png')]
        legacy_key = fingerprint((str(self.folder.parent), self.folder.name, hashes, 0))
        legacy = Path(status['published_output']).with_name(f'Team_team_{legacy_key[:16]}.jpg')
        Path(status['published_output']).rename(legacy)
        Path(status['master']).unlink()
        for copied in (self.root / 'Print').glob('*.jpg'):
            copied.unlink()  # A printer has already consumed this copy.
        self.engine.db.execute('DELETE FROM teams')
        self.engine.db.execute('DELETE FROM identities')
        self.engine.db.execute('DELETE FROM routes')
        self.engine._state(legacy_key, 'completed', legacy, {'sha256': original_hash})
        self.engine.db.execute('INSERT INTO routes VALUES (?,?,?)', (legacy_key, str(self.root / 'Print' / legacy.name), 'done'))
        self.engine.db.commit()
        if delete_output:
            legacy.unlink()
        return legacy_key, legacy, original_hash

    def test_consumed_or_moved_exports_do_not_make_a_valid_master_need_attention(self):
        result = self.run_team()
        status = result['statuses'][str(self.folder)]
        Path(status['published_output']).unlink()
        (self.root / 'Print' / Path(status['master']).name).unlink()
        with patch.object(self.engine.engine, 'export', side_effect=AssertionError('Second render')), patch.object(self.engine, '_deliver', side_effect=AssertionError('Second delivery')):
            repeated = self.engine.tick(result['job'], now=1)
        self.assertEqual(repeated['statuses'][str(self.folder)]['state'], 'Completed')
        self.assertTrue(Path(repeated['statuses'][str(self.folder)]['master']).is_file())
        self.assertFalse(Path(status['published_output']).exists())

    def test_deleted_local_master_is_restored_from_a_verified_copy_without_render_or_resend(self):
        result = self.run_team()
        master = Path(result['statuses'][str(self.folder)]['master'])
        expected = file_hash(master)
        master.unlink()
        with patch.object(self.engine.engine, 'export', side_effect=AssertionError('Second render')), patch.object(self.engine, '_deliver', side_effect=AssertionError('Second delivery')):
            repeated = self.engine.tick(result['job'], now=1)
        self.assertEqual(repeated['statuses'][str(self.folder)]['state'], 'Completed')
        self.assertEqual(file_hash(master), expected)

    def test_legacy_completed_receipt_imports_the_existing_master_without_printing(self):
        key, public, expected = self.legacy_receipt()
        with patch.object(self.engine.engine, 'export', side_effect=AssertionError('Legacy render')), patch.object(self.engine, '_deliver', side_effect=AssertionError('Legacy resend')):
            recovered = self.engine.tick(self.job, now=1)
        status = recovered['statuses'][str(self.folder)]
        self.assertEqual(status['state'], 'Completed')
        self.assertEqual(status['key'], key)
        self.assertEqual(file_hash(status['master']), expected)
        self.assertEqual(file_hash(public), expected)
        self.assertFalse((self.root / 'Print' / public.name).exists())
        self.assertEqual(self.engine.db.execute('SELECT COUNT(*) FROM teams').fetchone()[0], 1)

    def test_legacy_missing_master_is_rebuilt_locally_only_and_the_error_clears(self):
        key, public, expected = self.legacy_receipt(delete_output=True)
        with patch.object(self.engine, '_deliver', side_effect=AssertionError('Recovery must not reprint')):
            recovered = self.engine.tick(self.job, now=1)
        status = recovered['statuses'][str(self.folder)]
        self.assertEqual(status['state'], 'Completed')
        self.assertEqual(status['message'], '')
        self.assertEqual(file_hash(status['master']), expected)
        self.assertFalse(public.exists())
        receipt = json.loads(self.engine.db.execute('SELECT receipt FROM teams WHERE key=?', (key,)).fetchone()[0])
        self.assertTrue(receipt['recovered_from_current_settings'])
        with patch.object(self.engine.engine, 'export', side_effect=AssertionError('Recovery repeated')):
            again = self.engine.tick(recovered['job'], now=2)
        self.assertEqual(again['statuses'][str(self.folder)]['state'], 'Completed')

    def test_corrupted_public_copy_is_retained_and_does_not_corrupt_the_local_master(self):
        result = self.run_team()
        status = result['statuses'][str(self.folder)]
        public = Path(status['published_output'])
        public.write_bytes(b'Changed by another program')
        with patch.object(self.engine, '_deliver', side_effect=AssertionError('Do not overwrite external files')):
            repeated = self.engine.tick(result['job'], now=1)
        self.assertEqual(repeated['statuses'][str(self.folder)]['state'], 'Completed')
        self.assertEqual(public.read_bytes(), b'Changed by another program')
        self.assertEqual(file_hash(status['master']), file_hash(repeated['statuses'][str(self.folder)]['master']))

    def test_renaming_an_athlete_does_not_render_or_print_the_same_content_again(self):
        result = self.run_team()
        source = next(self.folder.glob('*.png'))
        source.rename(source.with_name('Renamed_Orange.png'))
        with patch.object(self.engine.engine, 'export', side_effect=AssertionError('Renaming caused a duplicate render')):
            again = self.engine.tick(result['job'], now=1)
        self.assertEqual(again['statuses'][str(self.folder)]['state'], 'Completed')
        self.assertEqual(self.engine.db.execute('SELECT COUNT(*) FROM teams').fetchone()[0], 1)
        self.assertEqual(len(list((self.root / 'Print').glob('*.jpg'))), 1)

    def test_duplicate_copy_and_rename_still_hold_but_deleting_it_recovers_automatically(self):
        source = next(self.folder.glob('*.png'))
        copy = self.folder / 'Copied.png'
        shutil.copyfile(source, copy)
        held = self.engine.tick(self.job, now=0)
        self.assertEqual(held['statuses'][str(self.folder)]['state'], 'Needs attention')
        self.assertIn('Renaming does not change', held['statuses'][str(self.folder)]['message'])
        renamed = self.folder / 'DifferentName.png'
        copy.rename(renamed)
        still_held = self.engine.tick(held['job'], now=1)
        self.assertEqual(still_held['statuses'][str(self.folder)]['state'], 'Needs attention')
        renamed.unlink()
        fixed = self.engine.tick(still_held['job'], now=2)
        self.assertEqual(fixed['statuses'][str(self.folder)]['state'], 'Completed')
        self.assertEqual(len(fixed['job']['teams'][0]['manifest']['athletes']), 3)
        self.assertEqual(len(list((self.root / 'Print').glob('*.jpg'))), 1)

    def test_invalid_alpha_can_be_excluded_and_its_later_deletion_does_not_reprint(self):
        bad = self.folder / 'Bad.png'
        with Image.new('RGB', (400, 1000), 'white') as image:
            image.save(bad)
        held = self.engine.tick(self.job, now=0)
        self.assertEqual(held['statuses'][str(self.folder)]['state'], 'Needs attention')
        session = ReviewSession(held['job'])
        entry = next(e for e in session.manifest['athletes'] if e['file'] == 'Bad.png')
        session.exclude(entry['id'])
        finished = self.engine.tick(session.job, now=1)
        self.assertEqual(finished['statuses'][str(self.folder)]['state'], 'Completed')
        bad.unlink()
        with patch.object(self.engine.engine, 'export', side_effect=AssertionError('Deleting excluded source reprinted')):
            again = self.engine.tick(finished['job'], now=2)
        self.assertEqual(again['statuses'][str(self.folder)]['state'], 'Completed')
        self.assertEqual(len(list((self.root / 'Print').glob('*.jpg'))), 1)

    def test_a_real_new_athlete_builds_a_new_version(self):
        result = self.run_team()
        write_sources(self.folder, athletes(4))
        updated = self.engine.tick(result['job'], now=1)
        self.assertEqual(updated['statuses'][str(self.folder)]['state'], 'Completed')
        self.assertEqual(len(updated['job']['teams'][0]['manifest']['athletes']), 4)
        self.assertEqual(len(list((self.root / 'Print').glob('*.jpg'))), 2)

    def test_source_deleted_during_hash_check_waits_and_recovers(self):
        with patch.object(self.engine, '_source_hash', side_effect=FileNotFoundError('Removed during check')):
            waiting = self.engine.tick(self.job, now=0)
        self.assertEqual(waiting['statuses'][str(self.folder)]['state'], 'Waiting for copying to finish')
        ready = self.engine.tick(waiting['job'], now=1)
        self.assertEqual(ready['statuses'][str(self.folder)]['state'], 'Completed')

    def test_disconnected_incoming_keeps_receipts_and_retries_on_reconnection(self):
        finished = self.run_team()
        offline = self.folder.parent.with_name('Incoming-offline')
        self.folder.parent.rename(offline)
        waiting = self.engine.tick(finished['job'], now=1)
        self.assertEqual(waiting['statuses'][str(self.folder)]['state'], 'Retry needed')
        offline.rename(self.folder.parent)
        ready = self.engine.tick(waiting['job'], now=2)
        self.assertEqual(ready['statuses'][str(self.folder)]['state'], 'Completed')
        self.assertEqual(len(list((self.root / 'Print').glob('*.jpg'))), 1)

    def test_only_one_uncached_teams_pngs_are_hashed_before_returning_the_queue(self):
        write_sources(self.folder.parent / 'Other team', athletes(2))
        pngs = []
        original = self.engine._source_hash
        def count(path):
            if Path(path).suffix.lower() == '.png':
                pngs.append(Path(path))
            return original(path)
        with patch.object(self.engine, '_source_hash', side_effect=count):
            first = self.engine.tick(self.job, now=0)
        self.assertEqual(len(first['job']['teams']), 2)
        self.assertEqual({p.parent for p in pngs}, {self.folder})
        second = self.engine.tick(first['job'], now=1)
        self.assertEqual(sum(s['state'] == 'Completed' for s in second['statuses'].values()), 2)

    def test_completed_manual_exclusion_requires_an_intentional_rebuild(self):
        finished = self.run_team()
        session = ReviewSession(finished['job'])
        session.exclude(session.manifest['athletes'][0]['id'])
        session.team['edited_completed'] = True
        waiting = self.engine.tick(session.job, now=1)
        self.assertEqual(waiting['statuses'][str(self.folder)]['state'], 'Ready to rebuild')
        self.assertEqual(len(list((self.root / 'Print').glob('*.jpg'))), 1)
        session.team.pop('edited_completed')
        session.team['rebuild_nonce'] = 1
        rebuilt = self.engine.tick(session.job, now=2)
        self.assertEqual(rebuilt['statuses'][str(self.folder)]['state'], 'Completed')
        self.assertEqual(len(list((self.root / 'Print').glob('*.jpg'))), 2)
