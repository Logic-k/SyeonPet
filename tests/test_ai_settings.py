"""Native settings tests: mocked credentials, account models, and transport."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QSettings, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from ai_profiles import AIProfile, ModelInfo
from ai_settings import AISettingsDialog
from chatgpt_auth import AccountInfo


class AISettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"LOCALAPPDATA": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.old_format = QSettings.defaultFormat()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, self.temp.name)
        self.addCleanup(QSettings.setDefaultFormat, self.old_format)
        self.account = AccountInfo("qa-account", "QA_ACCOUNT", "QA_WORKSPACE", True, 1)
        self.patches = [
            patch("ai_profiles.load_profiles", return_value=[]),
            patch("ai_profiles.save_profiles"),
            patch("ai_secrets.get_secret", return_value=None),
            patch("ai_secrets.set_secret"),
            patch("ai_secrets.delete_secret"),
            patch("chatgpt_auth.list_accounts", return_value=[self.account]),
            patch("chatgpt_auth.account_status", return_value=self.account),
        ]
        self.mocks = [item.start() for item in self.patches]
        for item in self.patches:
            self.addCleanup(item.stop)
        self.dialog = AISettingsDialog()
        self.dialog.show()
        self.loop = QEventLoop()
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.loop.quit)
        self.dialog.busy_changed.connect(lambda busy: None if busy else self.loop.quit())

    def tearDown(self):
        self.dialog.reject()
        self.app.processEvents()

    def wait_idle(self):
        if self.dialog.is_busy:
            self.timer.start(5000)
            self.loop.exec()
            self.timer.stop()
        self.app.processEvents()
        self.assertFalse(self.dialog.is_busy)

    def test_key_is_masked_and_only_secret_store_receives_it(self):
        self.dialog.provider.setCurrentIndex(self.dialog.provider.findData("groq"))
        self.dialog.model.setEditText("qa-model")
        self.dialog.key.setText("QA_SECRET")
        self.assertEqual(self.dialog.key.echoMode(), self.dialog.key.EchoMode.Password)
        self.dialog._save()
        self.mocks[1].assert_not_called()
        self.dialog.free_ack.setChecked(True)
        self.dialog._save()
        self.mocks[3].assert_called_once_with("provider:groq", {"api_key": "QA_SECRET"})
        profiles = self.mocks[1].call_args.args[0]
        self.assertEqual(profiles[0].model, "qa-model")
        self.assertNotIn("QA_SECRET", repr(profiles))
        self.assertEqual(self.dialog.key.text(), "")
        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)

    def test_invalid_models_acknowledgments_and_missing_keys_cannot_save(self):
        for provider, model in (("openrouter", "vendor/paid"),
                                ("ollama", "qa-cloud"),
                                ("gemini", "qa-model")):
            self.dialog.provider.setCurrentIndex(self.dialog.provider.findData(provider))
            self.dialog.model.setEditText(model)
            self.dialog._save()
        self.mocks[1].assert_not_called()
        self.mocks[3].assert_not_called()

    def test_remove_key_invalidates_profile_revision_without_exposing_secret(self):
        profile = AIProfile("qa", "groq", "qa-model", free_tier_acknowledged=True, revision=3)
        self.dialog._profiles = [profile]
        self.dialog.provider.setCurrentIndex(self.dialog.provider.findData("groq"))
        self.dialog.remove_key.click()
        self.mocks[4].assert_called_once_with("provider:groq")
        self.assertEqual(self.mocks[1].call_args.args[0][0].revision, 4)
        self.assertEqual(self.dialog.key.text(), "")

    def test_account_models_refresh_runs_off_gui_thread_and_save_uses_model_id(self):
        thread_ids = []
        gui_thread = threading.get_ident()

        def models(account_id, cancel=None):
            thread_ids.append(threading.get_ident())
            self.assertEqual(account_id, "qa-account")
            return [ModelInfo("qa-account-model", "QA model label")]
        self.dialog.model.setEditText("not-a-real-model")
        self.dialog._save()
        self.mocks[1].assert_not_called()
        with patch("chatgpt_auth.list_models", side_effect=models):
            self.dialog.refresh.click()
            self.wait_idle()
        self.assertNotEqual(thread_ids, [gui_thread])
        self.dialog.model.setCurrentIndex(0)
        self.dialog._save()
        profile = self.mocks[1].call_args.args[0][0]
        self.assertEqual(profile.model, "qa-account-model")
        self.assertEqual(profile.account_id, "qa-account")

    def test_login_cancel_does_not_refresh_models_or_save_profile(self):
        started = threading.Event()
        def login(open_browser, cancel):
            started.set()
            if not cancel.wait(3):
                raise TimeoutError("cancel not delivered")
            return self.account
        with patch("chatgpt_auth.login", side_effect=login), \
                patch("chatgpt_auth.list_models") as models, \
                patch("webbrowser.open") as browser:
            self.dialog.login.click()
            self.assertTrue(started.wait(2))
            self.dialog.cancel_work.click()
            self.wait_idle()
            models.assert_not_called()
            browser.assert_not_called()
            self.mocks[1].assert_not_called()

    def test_login_refresh_and_logout_are_worker_operations(self):
        thread_ids = []
        gui_thread = threading.get_ident()

        def login(*, open_browser, cancel):
            thread_ids.append(threading.get_ident())
            return self.account

        def models(account_id, *, cancel=None):
            thread_ids.append(threading.get_ident())
            return [ModelInfo("qa-model", "QA_MODEL")]

        def logout(account_id):
            thread_ids.append(threading.get_ident())
            self.mocks[5].return_value = []
        with patch("chatgpt_auth.login", side_effect=login), \
                patch("chatgpt_auth.list_models", side_effect=models), \
                patch("chatgpt_auth.logout", side_effect=logout) as disconnect:
            self.dialog.login.click()
            self.wait_idle()
            self.assertEqual(self.dialog.account.currentData(), "qa-account")
            self.assertEqual(self.dialog._account_models["qa-account"], {"qa-model"})
            self.dialog.logout.click()
            self.wait_idle()
            disconnect.assert_called_once_with("qa-account")
            self.assertEqual(self.dialog.account.count(), 0)
            self.assertEqual(self.dialog.model.count(), 0)
            self.assertEqual(self.dialog._account_models, {})
        self.assertEqual(len(thread_ids), 3)
        self.assertNotIn(gui_thread, thread_ids)

    def test_new_account_profile_discovers_dictionary_catalog_without_model_input(self):
        self.assertEqual(self.dialog.model.currentText(), "")
        with patch("chatgpt_auth.list_models",
                   return_value=[{"id": "qa-account-model", "label": "QA account model"}]) as models:
            self.dialog.refresh.click()
            self.wait_idle()
        self.assertEqual(models.call_args.args, ("qa-account",))
        self.assertEqual(self.dialog.model.currentText(), "qa-account-model")
        self.dialog._save()
        self.assertEqual(self.mocks[1].call_args.args[0][0].model, "qa-account-model")

    def test_new_key_profile_can_store_key_then_discover_without_model_input(self):
        self.dialog.provider.setCurrentIndex(self.dialog.provider.findData("groq"))
        self.dialog.free_ack.setChecked(True)
        self.dialog.key.setText("QA_SECRET")
        self.dialog.save_key.click()
        self.mocks[3].assert_called_once_with("provider:groq", {"api_key": "QA_SECRET"})
        self.assertEqual(self.dialog.key.text(), "")
        self.assertEqual(self.dialog.model.currentText(), "")
        self.mocks[2].return_value = {"api_key": "QA_SECRET"}
        with patch("ai_backends._request", return_value={
                "data": [{"id": "qa-discovered", "name": "QA discovered model"}]}) as transport:
            self.dialog.refresh.click()
            self.wait_idle()
        self.assertEqual(transport.call_args.args[:3], ("groq", "GET", "/openai/v1/models"))
        self.assertEqual(self.dialog.model.currentText(), "qa-discovered")
        self.dialog._save()
        profiles = self.mocks[1].call_args.args[0]
        self.assertEqual(profiles[0].model, "qa-discovered")
        self.assertNotIn("QA_SECRET", repr(profiles))

    def test_switch_provider_preserves_unsaved_key_until_user_saves_or_clears(self):
        self.dialog.provider.setCurrentIndex(self.dialog.provider.findData("groq"))
        self.dialog.model.addItem("Groq model", "qa-groq")
        self.dialog.model.setEditText("qa-groq")
        self.dialog.key.setText("QA_UNSAVED_SECRET")
        self.dialog.provider.setCurrentIndex(self.dialog.provider.findData("gemini"))
        self.assertEqual(self.dialog.provider.currentData(), "groq")
        self.assertEqual(self.dialog.model.currentText(), "qa-groq")
        self.assertEqual(self.dialog.key.text(), "QA_UNSAVED_SECRET")
        self.assertIn("저장하거나 지운", self.dialog.status.text())
        self.mocks[3].assert_not_called()
        self.dialog.key.clear()
        self.dialog.provider.setCurrentIndex(self.dialog.provider.findData("gemini"))
        self.assertEqual(self.dialog.provider.currentData(), "gemini")
        self.assertEqual(self.dialog.model.count(), 0)
        self.assertEqual(self.dialog.model.currentText(), "")
        self.assertEqual(self.dialog.key.text(), "")
        self.assertIn("키 저장", self.dialog.provider_guide.text())
        self.mocks[3].assert_not_called()

    def test_switch_profile_preserves_unsaved_key_and_current_profile(self):
        saved = AIProfile("qa-profile", "gemini", "qa-gemini", free_tier_acknowledged=True)
        self.dialog._profiles = [saved]
        self.dialog.profile.addItem("saved", saved.id)
        self.dialog.provider.setCurrentIndex(self.dialog.provider.findData("groq"))
        self.dialog.model.setEditText("qa-groq")
        self.dialog.key.setText("QA_UNSAVED_SECRET")
        self.dialog.profile.setCurrentIndex(self.dialog.profile.findData(saved.id))
        self.assertIsNone(self.dialog.profile.currentData())
        self.assertEqual(self.dialog.provider.currentData(), "groq")
        self.assertEqual(self.dialog.model.currentText(), "qa-groq")
        self.assertEqual(self.dialog.key.text(), "QA_UNSAVED_SECRET")
        self.assertIn("저장하거나 지운", self.dialog.status.text())
        self.dialog.key.clear()
        self.dialog.profile.setCurrentIndex(self.dialog.profile.findData(saved.id))
        self.assertEqual(self.dialog.profile.currentData(), saved.id)
        self.assertEqual(self.dialog.provider.currentData(), "gemini")
        self.assertEqual(self.dialog.model.currentText(), "qa-gemini")

    def test_selecting_saved_profile_restores_its_provider_and_model(self):
        saved = AIProfile("qa-profile", "gemini", "qa-gemini", free_tier_acknowledged=True)
        self.dialog._profiles = [saved]
        self.dialog.profile.addItem("saved", saved.id)
        self.dialog.model.setEditText("old-model")
        self.dialog.profile.setCurrentIndex(self.dialog.profile.findData(saved.id))
        self.assertEqual(self.dialog.provider.currentData(), "gemini")
        self.assertEqual(self.dialog.model.currentText(), "qa-gemini")
        self.assertTrue(self.dialog.free_ack.isChecked())

    def test_refresh_with_unsaved_key_explains_save_first_without_network(self):
        self.dialog.provider.setCurrentIndex(self.dialog.provider.findData("gemini"))
        self.dialog.key.setText("QA_SECRET")
        with patch("ai_profiles.list_models") as models:
            self.dialog.refresh.click()
            self.assertFalse(self.dialog.is_busy)
            models.assert_not_called()
        self.assertIn("키 저장", self.dialog.status.text())
        self.assertEqual(self.dialog.key.text(), "QA_SECRET")

    def test_worker_never_displays_unexpected_exception_contents(self):
        from ai_backends import BackendError
        for error in (RuntimeError("QA_SECRET QA_PRIVATE_INPUT"),
                      BackendError("QA_SECRET QA_PRIVATE_INPUT")):
            with self.subTest(error=type(error).__name__), \
                    patch("ai_profiles.list_models", side_effect=error):
                self.dialog.refresh.click()
                self.wait_idle()
                self.assertNotIn("QA_SECRET", self.dialog.status.text())
                self.assertNotIn("QA_PRIVATE_INPUT", self.dialog.status.text())
        with patch("ai_profiles.list_models", side_effect=BackendError("AI service rate limit reached (HTTP 429).")):
            self.dialog.refresh.click()
            self.wait_idle()
        self.assertIn("한도", self.dialog.status.text())

    def test_ollama_discovery_requires_explicit_server_configuration_acknowledgment(self):
        self.dialog.provider.setCurrentIndex(self.dialog.provider.findData("ollama"))
        with patch("ai_backends._request", return_value={"models": []}) as transport:
            self.dialog.refresh.click()
            self.wait_idle()
            transport.assert_not_called()
            self.dialog.local_ack.setChecked(True)
            self.dialog.refresh.click()
            self.wait_idle()
            self.assertEqual(transport.call_args.args[:3], ("ollama", "GET", "/api/tags"))
        self.dialog.model.setEditText("qa-local")
        self.dialog._save()
        self.assertTrue(self.mocks[1].call_args.args[0][0].ollama_no_cloud_acknowledged)

    def test_unexpected_worker_error_is_visible_and_does_not_leave_settings_busy(self):
        with patch("ai_profiles.list_models", side_effect=RuntimeError()):
            self.dialog.refresh.click()
            self.wait_idle()
        self.assertTrue(self.dialog.status.text())
        self.mocks[1].assert_not_called()


if __name__ == "__main__":
    unittest.main()
