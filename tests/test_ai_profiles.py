"""Nonsecret configuration and atomic persistence contracts."""
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ai_profiles as profiles


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "nested" / "profiles.json"
        self.profile = profiles.AIProfile("default", "chatgpt-subscription", "auto")

    def test_missing_file_defaults_only_to_subscription(self):
        self.assertEqual(profiles.load_profiles(self.path), [self.profile])
        self.assertFalse(self.path.exists())
        with self.assertRaises(FrozenInstanceError):
            self.profile.model = "different"

    def test_default_storage_is_localappdata_not_install_directory(self):
        with patch.dict("os.environ", {"LOCALAPPDATA": self.directory.name}):
            profiles.save_profiles([self.profile])
        expected = Path(self.directory.name) / "SyeonPet" / "ai_profiles.json"
        self.assertEqual(profiles.load_profiles(expected), [self.profile])

    def test_round_trip_and_allowlisted_serialization(self):
        class Extended(profiles.AIProfile):
            token = "not-serialized-token"
            api_key = "not-serialized-key"

        profile = Extended("work", "chatgpt-subscription", "auto", account_id="account-1",
                           revision=3)
        profiles.save_profiles([profile], self.path)
        self.assertEqual(profiles.load_profiles(self.path),
                         [profiles.AIProfile("work", "chatgpt-subscription", "auto",
                                              account_id="account-1", revision=3)])
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(document["profiles"][0]), {
            "id", "provider", "model", "account_id", "free_tier_acknowledged",
            "ollama_no_cloud_acknowledged", "revision",
        })
        self.assertNotIn("not-serialized", self.path.read_text(encoding="utf-8"))

    def test_failed_replace_keeps_old_file_and_removes_temporary(self):
        profiles.save_profiles([self.profile], self.path)
        before = self.path.read_bytes()
        with patch.object(profiles.os, "replace", side_effect=OSError("secret-path")):
            with self.assertRaisesRegex(ValueError, "^Could not save AI profiles"):
                profiles.save_profiles([replace(self.profile, revision=1)], self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_corrupt_unknown_secret_fields_and_duplicate_ids_are_rejected(self):
        self.path.parent.mkdir()
        values = [
            b"not JSON",
            b'{"version":1,"profiles":[{"id":"x","provider":"chatgpt-subscription",'
            b'"model":"auto","api_key":"DO-NOT-ECHO"}]}',
            b'{"version":true,"profiles":[]}',
            b'{"version":1,"profiles":{}}',
            b'{"version":1,"profiles":[null]}',
            b" " * 1_000_001,
        ]
        for raw in values:
            with self.subTest(raw=raw[:30]):
                self.path.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, "^Invalid AI profile file"):
                    profiles.load_profiles(self.path)
        with self.assertRaises(ValueError):
            profiles.save_profiles([self.profile, self.profile], self.path)

    def test_validation_types_and_no_arbitrary_endpoints(self):
        invalid = [
            {"provider": "https://evil.invalid"}, {"provider": []},
            {"id": ""}, {"model": "hello\ninjected"}, {"model": "a?key=secret"},
            {"revision": True}, {"revision": -1}, {"account_id": ""},
            {"account_id": "a\r\nb"}, {"free_tier_acknowledged": 1},
            {"ollama_no_cloud_acknowledged": "yes"},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                profiles.validate_profile(replace(self.profile, **changes))

    def test_billing_and_local_only_acknowledgments_are_explicit(self):
        for provider in ("groq", "gemini"):
            profile = profiles.AIProfile(provider, provider, "model")
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                profiles.validate_profile(profile)
            profiles.validate_profile(replace(profile, free_tier_acknowledged=True))
        for model in ("vendor/model", "vendor/model:free/paid", "free"):
            with self.subTest(model=model), self.assertRaises(ValueError):
                profiles.validate_profile(profiles.AIProfile("r", "openrouter", model))
        profiles.validate_profile(profiles.AIProfile("r", "openrouter", "vendor/model:free"))
        local = profiles.AIProfile("o", "ollama", "local:latest")
        with self.assertRaises(ValueError):
            profiles.validate_profile(local)
        profiles.validate_profile(replace(local, ollama_no_cloud_acknowledged=True))
        for model in ("remote:cloud", "remote-cloud:latest"):
            with self.subTest(model=model), self.assertRaises(ValueError):
                profiles.validate_profile(replace(local, model=model,
                                                 ollama_no_cloud_acknowledged=True))

    def test_list_models_delegates_lazily(self):
        expected = [profiles.ModelInfo("auto", "Auto")]
        with patch("ai_backends.list_models", return_value=expected) as discover:
            self.assertEqual(profiles.list_models(self.profile), expected)
        discover.assert_called_once_with(self.profile, cancel=None)


if __name__ == "__main__":
    unittest.main()
