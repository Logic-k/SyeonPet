"""Switch tests use temporary installations only and never run a user app."""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import updater_switch as switch


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "install"
        self.root.mkdir()
        self.environment = patch.dict(os.environ, {"LOCALAPPDATA": str(self.base / "local")})
        self.environment.start()
        self.directory = switch.state_directory(self.root)

    def tearDown(self):
        if switch._instance_lock:
            switch._instance_lock.release()
            switch._instance_lock = None
        self.environment.stop()
        self.temp.cleanup()

    def candidate(self, version):
        folder = self.directory / "versions" / version / "app"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SyeonPet.exe").write_bytes(b"synthetic")
        return folder

    def test_short_new_state_path_and_existing_long_state_are_preserved(self):
        new = switch.state_directory(self.root)
        self.assertEqual(len(new.name), 32)
        identity = hashlib.sha256(os.path.normcase(str(self.root.resolve())).encode("utf-8")).hexdigest()
        old = new.parent / identity
        old.mkdir(parents=True)
        (old / "state.json").write_text("preserve", encoding="utf-8")
        self.assertEqual(switch.state_directory(self.root), old)
        self.assertEqual((old / "state.json").read_text(encoding="utf-8"), "preserve")

    def verifier(self, directory, key):
        self.assertEqual(key, b"key")
        return {"version": Path(directory).name}

    def test_queue_is_atomic_and_rejects_downgrade(self):
        for version in ("1.0.0", "1.0.1", "0.9.9"):
            self.candidate(version)
        self.assertTrue(switch.queue_update(self.root, "1.0.0"))
        self.assertFalse(switch.queue_update(self.root, "1.0.1"))
        self.assertEqual(switch.pending_version(self.root), "1.0.0")
        with switch._state_lock(self.directory):
            switch._write_state(self.directory, {"schema": 1, "active": "1.0.0",
                                                  "previous": None, "pending": None})
        self.assertFalse(switch.queue_update(self.root, "0.9.9"))
        self.assertFalse(switch.queue_update(self.root, "1.0.0"))
        self.assertTrue(switch.queue_update(self.root, "1.0.1"))

    def test_signed_older_pending_is_not_launched(self):
        self.candidate("1.0.0")
        self.candidate("1.2.0")
        with switch._state_lock(self.directory):
            switch._write_state(self.directory, {"schema": 1, "active": "1.2.0",
                                                  "previous": None, "pending": "1.0.0"})
        with patch.object(switch, "_run_child", return_value=(0, True)) as launch:
            self.assertEqual(switch.bootstrap(self.root, b"key", self.verifier, base_version="0.9.0"), 0)
        self.assertEqual(launch.call_args.args[1], "1.2.0")
        self.assertIsNone(switch.pending_version(self.root))
        self.assertFalse(switch.queue_update(self.root, "1.0.0", current_version="1.2.0"))

    def test_bootstrap_rejects_failed_verification_without_launching(self):
        self.candidate("1.0.0")
        switch.queue_update(self.root, "1.0.0")
        with patch.object(switch, "_run_child") as launch:
            self.assertIsNone(switch.bootstrap(self.root, b"key", lambda *_: (_ for _ in ()).throw(ValueError())))
        launch.assert_not_called()
        self.assertIsNone(switch.pending_version(self.root))

    def test_failed_candidate_rolls_back_to_verified_previous(self):
        self.candidate("1.0.0")
        self.candidate("1.1.0")
        with switch._state_lock(self.directory):
            switch._write_state(self.directory, {"schema": 1, "active": "1.0.0",
                                                  "previous": None, "pending": "1.1.0"})
        with patch.object(switch, "_run_child", side_effect=[(2, False), (0, True)]) as launch:
            self.assertEqual(switch.bootstrap(self.root, b"key", self.verifier), 0)
        self.assertEqual([call.args[1] for call in launch.call_args_list], ["1.1.0", "1.0.0"])
        self.assertIsNone(switch.pending_version(self.root))
        self.assertEqual(switch._read_state(self.directory)["active"], "1.0.0")

    def test_same_version_can_launch_again_and_update_waits(self):
        self.candidate("1.0.0")
        switch.queue_update(self.root, "1.0.0")
        other = switch._FileLock(self.directory / ("instance-" + "a" * 32 + ".lock"))
        self.assertTrue(other.acquire())
        other.describe(None)
        try:
            # An already running original app makes another original launch
            # safe, while leaving the update pending for a later launch.
            self.assertIsNone(switch.bootstrap(self.root, b"key", self.verifier))
            self.assertEqual(switch.pending_version(self.root), "1.0.0")
        finally:
            other.release()

    def test_other_windows_process_defers_update_until_it_exits(self):
        self.candidate("1.0.0")
        switch.queue_update(self.root, "1.0.0")
        script = (
            "import sys,pathlib,tempfile,json,hashlib,secrets,threading,subprocess,time,contextlib,atexit;"
            "sys.path.insert(0,sys.argv[1]);import updater_switch as s;"
            "lock=s._FileLock(pathlib.Path(sys.argv[2])/('instance-'+('c'*32)+'.lock'));"
            "assert lock.acquire();lock.describe(None);print('ready',flush=True);"
            "sys.stdin.read(1);lock.release()"
        )
        proc = subprocess.Popen([sys.executable, "-I", "-c", script, str(Path(switch.__file__).parent),
                                 str(self.directory)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            with patch.object(switch, "_run_child") as launch:
                self.assertIsNone(switch.bootstrap(self.root, b"key", self.verifier))
                launch.assert_not_called()
            self.assertEqual(switch.pending_version(self.root), "1.0.0")
        finally:
            proc.communicate(input="x", timeout=10)
        self.assertEqual(proc.returncode, 0)
        switch._instance_lock.release()
        switch._instance_lock = None
        with patch.object(switch, "_run_child", return_value=(0, True)) as launch:
            self.assertEqual(switch.bootstrap(self.root, b"key", self.verifier), 0)
            self.assertEqual(launch.call_args.args[1], "1.0.0")

    def test_health_ack_activates_and_does_not_terminate_child(self):
        app = self.candidate("1.0.0")
        switch.queue_update(self.root, "1.0.0")
        switch._instance_lock = switch._FileLock(self.directory / ("instance-" + "b" * 32 + ".lock"))
        self.assertTrue(switch._instance_lock.acquire())
        switch._instance_lock.describe("1.0.0", True)

        class Process:
            returncode = 0
            calls = 0
            def poll(self):
                self.calls += 1
                return None if self.calls == 1 else 0
            def terminate(self):
                raise AssertionError("must not terminate a candidate")
            def kill(self):
                raise AssertionError("must not kill a candidate")

        def start(args, cwd, env, creationflags):
            self.assertEqual(Path(cwd), app)
            self.assertEqual(Path(env["SYEONPET_INSTALL_ROOT"]), self.root)
            marker = self.directory / "ready" / (env["SYEONPET_UPDATE_READY_TOKEN"] + ".json")
            marker.write_text(json.dumps({"version": "1.0.0", "token": env["SYEONPET_UPDATE_READY_TOKEN"]}))
            return Process()

        with patch.object(switch.subprocess, "Popen", side_effect=start), patch.object(switch.time, "sleep"):
            self.assertEqual(switch._run_child(self.root, "1.0.0", app), (0, True))
        self.assertEqual(switch._read_state(self.directory)["active"], "1.0.0")
        self.assertIsNone(switch.pending_version(self.root))

    def test_delayed_health_warns_without_forcing_a_live_process_to_close(self):
        app = self.candidate("1.0.0")
        switch.queue_update(self.root, "1.0.0")
        class Process:
            returncode = 1
            calls = 0
            def poll(self):
                self.calls += 1
                return None if self.calls <= 2 else 1
            def terminate(self):
                raise AssertionError("must not terminate a candidate")
            def kill(self):
                raise AssertionError("must not kill a candidate")
        with patch.object(switch.subprocess, "Popen", return_value=Process()), \
                patch.object(switch.time, "monotonic", side_effect=[0, 31, 32]), \
                patch.object(switch.time, "sleep"), patch.object(switch, "_health_delay_notice") as notice:
            self.assertEqual(switch._run_child(self.root, "1.0.0", app), (1, False))
            notice.assert_called_once()

    def test_monitor_error_waits_for_child_instead_of_launching_fallback(self):
        app = self.candidate("1.0.0")
        class Process:
            waits = 0
            def poll(self):
                raise OSError("synthetic polling failure")
            def wait(self):
                self.waits += 1
                if self.waits == 1:
                    raise OSError("synthetic wait failure")
                return 3
            def terminate(self):
                raise AssertionError("must not terminate a candidate")
            def kill(self):
                raise AssertionError("must not kill a candidate")
        process = Process()
        with patch.object(switch.subprocess, "Popen", return_value=process), \
                patch.object(switch.time, "sleep"), patch.object(switch, "_health_delay_notice") as notice:
            self.assertEqual(switch._run_child(self.root, "1.0.0", app), (3, False))
            notice.assert_called_once()
        self.assertEqual(process.waits, 2)

    def test_stale_trial_marker_blocks_unsafe_second_launch(self):
        self.candidate("1.0.0")
        switch.queue_update(self.root, "1.0.0")
        lock = switch._FileLock(self.directory / ("instance-" + "e" * 32 + ".lock"))
        self.assertTrue(lock.acquire())
        lock.describe("1.0.0", True)
        lock.release()  # supervisor crashed during candidate startup
        with patch.object(switch, "_run_child") as launch:
            with switch._state_lock(self.directory):
                version, allowed = switch._register_instance(self.directory, switch._read_state(self.directory))
            self.assertFalse(allowed)
            self.assertIsNone(version)
            launch.assert_not_called()

    def test_child_uses_original_install_root_and_rejects_wrong_path(self):
        app = self.candidate("1.0.0")
        env = {"SYEONPET_INSTALL_ROOT": str(self.root), "SYEONPET_UPDATE_CHILD": "1.0.0",
               "SYEONPET_UPDATE_READY_TOKEN": "a" * 32}
        with patch.dict(os.environ, env):
            self.assertEqual(switch.installation_root(app), self.root.resolve())
            with self.assertRaises(switch.SwitchError):
                switch.installation_root(self.root)

    def test_failed_version_transition_is_atomic_against_new_launch(self):
        self.candidate("1.0.0")
        self.candidate("1.1.0")
        with switch._state_lock(self.directory):
            switch._write_state(self.directory, {"schema": 1, "active": "1.0.0",
                                                  "previous": None, "pending": "1.1.0"})
        checked = threading.Event()
        attempted = threading.Event()
        seen = []
        original = switch._running_instances
        calls = 0

        def slow_running(directory):
            nonlocal calls
            calls += 1
            result = original(directory)
            if calls == 2:  # failure path, while state.lock is held
                checked.set()
                self.assertTrue(attempted.wait(3))
            return result

        def observer():
            self.assertTrue(checked.wait(3))
            attempted.set()
            with switch._state_lock(self.directory):
                seen.append((switch._read_state(self.directory), original(self.directory)))

        thread = threading.Thread(target=observer)
        thread.start()
        with patch.object(switch, "_running_instances", side_effect=slow_running), \
                patch.object(switch, "_run_child", side_effect=[(2, False), (0, True)]):
            self.assertEqual(switch.bootstrap(self.root, b"key", self.verifier), 0)
        thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(seen[0][0]["active"], "1.0.0")
        self.assertIsNone(seen[0][0]["pending"])
        self.assertEqual(seen[0][1], [{"version": "1.0.0", "trial": False}])


if __name__ == "__main__":
    unittest.main()
