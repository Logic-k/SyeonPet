"""Restart draft preservation and update scheduling without user data."""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

import updater_switch
from updater_ui import (_RESTORED_RETENTION_SECONDS, RestartDrafts, UpdatePreparer,
                        prepare_quit)


class FakePanel:
    def __init__(self, state=None):
        self.state = state or {'schema': 1, 'visible': True, 'conversation': {'text': '원문'}}

    def export_update_state(self):
        return self.state

    def restore_update_state(self, value):
        self.state = value
        return value['visible']


class RestartDraftTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'LOCALAPPDATA': self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.root = Path(self.temp.name) / 'portable'
        self.root.mkdir()

    def test_success_retains_snapshot_and_marks_clean_exit(self):
        drafts = RestartDrafts(self.root)
        path = drafts.save(FakePanel())
        restored = FakePanel({'ignored': True})
        self.assertTrue(drafts.restore(restored))
        self.assertEqual(restored.state['conversation']['text'], '원문')
        self.assertTrue(path.exists())
        drafts.finish_cleanly()
        self.assertFalse(path.exists())
        self.assertEqual(json.loads(path.with_suffix('.restored').read_text(encoding='utf-8'))
                         ['conversation']['text'], '원문')

    def test_two_instances_claim_different_snapshots(self):
        writer = RestartDrafts(self.root)
        writer.save(FakePanel({'visible': True, 'text': 'first'}))
        writer.save(FakePanel({'visible': True, 'text': 'second'}))
        first, second = RestartDrafts(self.root), RestartDrafts(self.root)
        a, b = FakePanel(), FakePanel()
        self.assertTrue(first.restore(a))
        self.assertTrue(second.restore(b))
        self.assertEqual({a.state['text'], b.state['text']}, {'first', 'second'})
        first.finish_cleanly()
        second.finish_cleanly()

    def test_failed_restore_keeps_snapshot_for_previous_version(self):
        writer = RestartDrafts(self.root)
        path = writer.save(FakePanel())
        class Broken(FakePanel):
            def restore_update_state(self, value):
                raise ValueError('unsupported schema')
        with self.assertRaises(ValueError):
            RestartDrafts(self.root).restore(Broken())
        previous = FakePanel()
        retry = RestartDrafts(self.root)
        self.assertTrue(retry.restore(previous))
        self.assertTrue(path.exists())
        retry.finish_cleanly()

    def test_failed_export_never_writes_or_overwrites(self):
        writer = RestartDrafts(self.root)
        original = writer.save(FakePanel())
        class Busy(FakePanel):
            def export_update_state(self):
                raise RuntimeError('busy')
        with self.assertRaises(RuntimeError):
            writer.save(Busy())
        self.assertEqual(list(writer.directory.glob('*.json')), [original])

    def test_update_queued_during_confirmation_keeps_busy_draft_and_blocks_quit(self):
        writer = RestartDrafts(self.root)
        class Busy(FakePanel):
            def export_update_state(self):
                raise RuntimeError('still running')
        preparer = UpdatePreparer(self.root)
        # Simulates a pending update arriving after the quit dialog opened.
        with patch('updater_ui.updater_switch.pending_version', return_value='9.0.0'):
            with self.assertRaises(RuntimeError):
                prepare_quit(preparer, writer, Busy(), self.root)
        self.assertFalse(preparer._shutting_down)
        self.assertFalse(writer.directory.exists())

    def test_update_queued_during_confirmation_preserves_finished_draft(self):
        writer = RestartDrafts(self.root)
        preparer = UpdatePreparer(self.root)
        with patch('updater_ui.updater_switch.pending_version', return_value='9.0.0'):
            prepare_quit(preparer, writer, FakePanel(), self.root)
        self.assertTrue(preparer._shutting_down)
        self.assertEqual(len(list(writer.directory.glob('*.json'))), 1)

    # Every retention test below calls RestartDrafts.prune_restored directly, so
    # deleting the method fails all of them with AttributeError, and each
    # assertion also pins the exact number of files the pruning pass must report.
    def _restored(self, stem):
        drafts = RestartDrafts(self.root)
        drafts.directory.mkdir(parents=True, exist_ok=True)
        path = drafts.directory / (stem + '.restored')
        path.write_text('{"conversation": {"text": "원문"}}', encoding='utf-8')
        return drafts, path

    def test_restored_snapshot_past_retention_is_deleted(self):
        drafts, path = self._restored('a' * 32)
        now = os.path.getmtime(path) + _RESTORED_RETENTION_SECONDS + 1
        self.assertEqual(drafts.prune_restored(now=now), 1)
        self.assertFalse(path.exists())

    def test_restored_snapshot_within_retention_is_kept(self):
        drafts, path = self._restored('a' * 32)
        now = os.path.getmtime(path) + _RESTORED_RETENTION_SECONDS - 1
        self.assertEqual(drafts.prune_restored(now=now), 0)
        self.assertTrue(path.exists())

    def test_injected_now_decides_retention_without_sleeping(self):
        drafts, path = self._restored('d' * 32)
        mtime = os.path.getmtime(path)
        self.assertEqual(drafts.prune_restored(now=mtime + 60), 0)
        self.assertTrue(path.exists())
        self.assertEqual(drafts.prune_restored(now=mtime + _RESTORED_RETENTION_SECONDS + 1), 1)
        self.assertFalse(path.exists())

    def test_drafts_journals_and_symlinks_are_never_pruned(self):
        drafts = RestartDrafts(self.root)
        drafts.directory.mkdir(parents=True, exist_ok=True)
        retained = drafts.directory / ('b' * 32 + '.restored')
        draft = drafts.directory / ('b' * 32 + '.json')
        lock = drafts.directory / ('b' * 32 + '.lock')
        retained.write_text('{}', encoding='utf-8')
        draft.write_text('{}', encoding='utf-8')
        lock.write_text('', encoding='utf-8')
        now = os.path.getmtime(retained) + _RESTORED_RETENTION_SECONDS + 1
        self.assertEqual(drafts.prune_restored(now=now), 1)
        self.assertFalse(retained.exists())
        self.assertTrue(draft.exists())
        self.assertTrue(lock.exists())
        link = drafts.directory / ('c' * 32 + '.restored')
        try:
            link.symlink_to(draft)
        except OSError:
            self.skipTest('현재 Windows 계정에서 심볼릭 링크를 만들 수 없습니다')
        self.assertEqual(drafts.prune_restored(now=now), 0)
        self.assertTrue(link.is_symlink())
        self.assertTrue(draft.exists())

    def test_names_that_are_not_snapshot_hashes_are_never_pruned(self):
        drafts = RestartDrafts(self.root)
        drafts.directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for name in ('draft.restored', 'a' * 31 + '.restored', 'a' * 33 + '.restored',
                     'z' * 32 + '.restored', 'A' * 32 + '.restored',
                     'a' * 32 + '.json'):
            path = drafts.directory / name
            path.write_text('{}', encoding='utf-8')
            paths.append(path)
        now = max(os.path.getmtime(path) for path in paths) + _RESTORED_RETENTION_SECONDS + 1
        self.assertEqual(drafts.prune_restored(now=now), 0)
        for path in paths:
            self.assertTrue(path.exists())

    def test_successful_restore_prunes_an_expired_retained_backup(self):
        first = RestartDrafts(self.root)
        first.save(FakePanel())
        self.assertTrue(first.restore(FakePanel()))
        first.finish_cleanly()
        retained = next(first.directory.glob('*.restored'))
        expired = time.time() - _RESTORED_RETENTION_SECONDS - 60
        os.utime(retained, (expired, expired))
        current = RestartDrafts(self.root).save(FakePanel())
        self.assertTrue(RestartDrafts(self.root).restore(FakePanel()))
        self.assertFalse(retained.exists())
        self.assertTrue(current.exists())


class UpdateSchedulingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _service(self, done, results):
        service = UpdatePreparer(Path('.'))

        def record(result):
            results.append(result)
            done.set()

        service.completed.connect(record)
        return service

    def _run_check(self, done, results):
        end = time.monotonic() + 3
        while not done.is_set() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(done.is_set())
        return results[0]

    def test_update_finishing_during_shutdown_is_not_queued(self):
        started, unblock, done = threading.Event(), threading.Event(), threading.Event()
        results = []
        def prepare(*_args):
            started.set()
            unblock.wait(3)
            return {'status': 'ready', 'version': '9.0.0'}
        with patch('updater_ui.updater_core.prepare_update', side_effect=prepare), \
             patch('updater_ui.updater_switch.state_directory', return_value=Path('.')), \
             patch('updater_ui.updater_switch.pending_version') as pending, \
             patch('updater_ui.updater_switch.queue_update') as queue:
            service = self._service(done, results)
            service.check()
            self.assertTrue(started.wait(2))
            service.begin_shutdown()
            unblock.set()
            result = self._run_check(done, results)
            queue.assert_not_called()
            pending.assert_not_called()
        self.assertEqual(result['status'], 'shutting_down')
        self.assertNotIn('준비했어요', result['message'])

    def test_queued_update_reports_new_preparation(self):
        done, results = threading.Event(), []
        with patch('updater_ui.updater_core.prepare_update',
                   return_value={'status': 'ready', 'version': '9.0.0'}), \
             patch('updater_ui.updater_switch.state_directory', return_value=Path('.')), \
             patch('updater_ui.updater_switch.queue_update', return_value=True) as queue, \
             patch('updater_ui.updater_switch.pending_version'):
            service = self._service(done, results)
            service.check()
            result = self._run_check(done, results)
            queue.assert_called_once()
        self.assertEqual(result['status'], 'ready')
        self.assertIn('9.0.0', result['message'])
        self.assertIn('준비했어요', result['message'])

    def test_rejected_queue_with_existing_pending_reports_already_prepared(self):
        done, results = threading.Event(), []
        with patch('updater_ui.updater_core.prepare_update',
                   return_value={'status': 'already_ready', 'version': '9.0.0'}), \
             patch('updater_ui.updater_switch.state_directory', return_value=Path('.')), \
             patch('updater_ui.updater_switch.queue_update', return_value=False), \
             patch('updater_ui.updater_switch.pending_version', return_value='9.1.0'):
            service = self._service(done, results)
            service.check()
            result = self._run_check(done, results)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['version'], '9.1.0')
        self.assertIn('이미 준비', result['message'])

    def test_rejected_queue_without_pending_reports_nothing_prepared(self):
        done, results = threading.Event(), []
        with patch('updater_ui.updater_core.prepare_update',
                   return_value={'status': 'ready', 'version': '9.0.0'}), \
             patch('updater_ui.updater_switch.state_directory', return_value=Path('.')), \
             patch('updater_ui.updater_switch.queue_update', return_value=False), \
             patch('updater_ui.updater_switch.pending_version', return_value=None):
            service = self._service(done, results)
            service.check()
            result = self._run_check(done, results)
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('준비했어요', result['message'])

    def test_queue_error_does_not_crash_the_tray(self):
        done, results = threading.Event(), []
        with patch('updater_ui.updater_core.prepare_update',
                   return_value={'status': 'ready', 'version': '9.0.0'}), \
             patch('updater_ui.updater_switch.state_directory', return_value=Path('.')), \
             patch('updater_ui.updater_switch.queue_update',
                   side_effect=updater_switch.SwitchError('broken')), \
             patch('updater_ui.updater_switch.pending_version') as pending:
            service = self._service(done, results)
            service.check()
            result = self._run_check(done, results)
            pending.assert_not_called()
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('준비했어요', result['message'])


if __name__ == '__main__':
    unittest.main()
