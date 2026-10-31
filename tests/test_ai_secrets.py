"""Real current-user DPAPI tests under disposable isolated storage."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ai_secrets


@unittest.skipUnless(os.name == "nt", "Real Windows DPAPI is required.")
class SecretTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ai_secrets.SecretStore(self.temp.name)

    def test_real_dpapi_roundtrip_atomic_replacement_and_delete(self):
        key = "synthetic-account-key"
        secret = {"access_token": "SYNTHETIC-NOT-A-REAL-TOKEN", "nested": {"\ud55c": 3}}
        self.assertIsNone(self.store.get_secret(key))
        self.store.set_secret(key, secret)
        self.assertEqual(self.store.get_secret(key), secret)
        files = list(Path(self.temp.name).iterdir())
        self.assertEqual(len(files), 1)
        self.assertNotIn(key, files[0].name)
        self.assertNotIn(b"SYNTHETIC", files[0].read_bytes())
        self.store.set_secret(key, {"replacement": True})
        self.assertEqual(self.store.get_secret(key), {"replacement": True})
        self.assertEqual(len(list(Path(self.temp.name).iterdir())), 1)
        self.store.delete_secret(key)
        self.store.delete_secret(key)
        self.assertIsNone(self.store.get_secret(key))

    def test_real_dpapi_rejects_tamper_and_plaintext(self):
        self.store.set_secret("key", {"secret": "synthetic"})
        path = self.store._path("key")
        damaged = bytearray(path.read_bytes())
        damaged[-1] ^= 1
        path.write_bytes(damaged)
        with self.assertRaises(ai_secrets.SecretError):
            self.store.get_secret("key")
        path.write_text(json.dumps({"secret": "synthetic"}))
        with self.assertRaises(ai_secrets.SecretError):
            self.store.get_secret("key")

    def test_failed_atomic_replace_preserves_old_blob_and_cleans_temporary(self):
        self.store.set_secret("key", {"old": True})
        with patch("ai_secrets.os.replace", side_effect=OSError("private path")):
            with self.assertRaisesRegex(ai_secrets.SecretError, "^Could not save"):
                self.store.set_secret("key", {"new": True})
        self.assertEqual(self.store.get_secret("key"), {"old": True})
        self.assertEqual(len(list(Path(self.temp.name).iterdir())), 1)

    def test_injected_protector_and_module_contract(self):
        class Protector:
            def protect(self, data):
                return b"encrypted:" + data[::-1]

            def unprotect(self, data):
                return data.removeprefix(b"encrypted:")[::-1]

        store = ai_secrets.SecretStore(self.temp.name, Protector())
        with patch("ai_secrets.SecretStore", return_value=store):
            ai_secrets.set_secret("test", {"value": 1})
            self.assertEqual(ai_secrets.get_secret("test"), {"value": 1})
            ai_secrets.delete_secret("test")
            self.assertIsNone(ai_secrets.get_secret("test"))

    def test_no_plaintext_fallback_on_protection_failure(self):
        class Broken:
            def protect(self, data):
                raise ai_secrets.SecretError("Protection unavailable.")

        store = ai_secrets.SecretStore(self.temp.name, Broken())
        with self.assertRaises(ai_secrets.SecretError):
            store.set_secret("key", {"value": "synthetic"})
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
