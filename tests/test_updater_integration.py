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

from updater_ui import RestartDrafts, UpdatePreparer, prepare_quit


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


class UpdateSchedulingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_update_finishing_during_shutdown_is_not_queued(self):
        started, unblock, done = threading.Event(), threading.Event(), threading.Event()
        def prepare(*_args):
            started.set()
            unblock.wait(3)
            return {'status': 'ready', 'version': '9.0.0'}
        with patch('updater_ui.updater_core.prepare_update', side_effect=prepare), \
             patch('updater_ui.updater_switch.state_directory', return_value=Path('.')), \
             patch('updater_ui.updater_switch.pending_version', return_value=None), \
             patch('updater_ui.updater_switch.queue_update') as queue:
            service = UpdatePreparer(Path('.'))
            service.completed.connect(lambda _result: done.set())
            service.check()
            self.assertTrue(started.wait(2))
            service.begin_shutdown()
            unblock.set()
            end = time.monotonic() + 3
            while not done.is_set() and time.monotonic() < end:
                self.app.processEvents()
                time.sleep(.01)
            self.assertTrue(done.is_set())
            queue.assert_not_called()


if __name__ == '__main__':
    unittest.main()
