"""Native settings for non-secret AI profiles and separately stored credentials."""
from __future__ import annotations

from dataclasses import replace
import threading
import uuid
import webbrowser

from PySide6.QtCore import QObject, QSignalBlocker, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)


class _SettingsSignals(QObject):
    finished = Signal(object)


class AISettingsDialog(QDialog):
    """Credentials never enter profile JSON, draft state, or result history."""

    profiles_changed = Signal()
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        from ai_profiles import load_profiles
        self.setWindowTitle("AI 설정")
        self.setAccessibleName("AI 설정")
        self.resize(490, 580)
        self.setStyleSheet("""
            QDialog, QWidget#aiSettingsBody { background: #f3f6fa; color: #172b3a; }
            QScrollArea { background: #f3f6fa; border: none; }
            QLabel, QCheckBox { color: #172b3a; }
            QLineEdit, QComboBox { background: white; color: #172b3a;
                border: 1px solid #9aaebc; border-radius: 4px; padding: 4px; }
            QPushButton { background: white; color: #172b3a;
                border: 1px solid #8fa5b4; border-radius: 4px; padding: 5px; }
            QPushButton:disabled { color: #526a79; background: #e9eef2; }
        """)
        self._profiles = list(load_profiles())
        self._cancel = threading.Event()
        self._busy = False
        self._remove_key = False
        self._account_models = {}
        self._signals = _SettingsSignals(self)
        self._signals.finished.connect(self._worker_done)
        self._worker_callback = None
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("aiSettingsBody")
        form = QFormLayout(body)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.profile = QComboBox()
        self.profile.setAccessibleName("AI 프로필 선택")
        self.profile.addItem("새 프로필", None)
        for item in self._profiles:
            self.profile.addItem(f"{item.provider} / {item.model}", item.id)
        form.addRow("프로필", self.profile)
        self.provider = QComboBox()
        self.provider.setAccessibleName("AI 제공자")
        for provider in ("chatgpt-subscription", "groq", "gemini", "openrouter", "ollama"):
            self.provider.addItem(provider, provider)
        form.addRow("제공자", self.provider)
        self.provider_guide = QLabel()
        self.provider_guide.setAccessibleName("제공자 연결 방법")
        self.provider_guide.setWordWrap(True)
        form.addRow(self.provider_guide)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setAccessibleName("AI 모델")
        form.addRow("모델", self.model)
        self.refresh = QPushButton("모델 새로고침")
        self.refresh.setAccessibleName("계정 모델 새로고침")
        self.refresh.clicked.connect(self.refresh_models)
        form.addRow(self.refresh)
        self.account = QComboBox()
        self.account.setAccessibleName("ChatGPT 계정")
        form.addRow("ChatGPT 계정", self.account)
        account_row = QHBoxLayout()
        self.login = QPushButton("ChatGPT 로그인")
        self.login.setAccessibleName("ChatGPT 로그인")
        self.login.clicked.connect(self._login)
        self.logout = QPushButton("로그아웃")
        self.logout.setAccessibleName("ChatGPT 로그아웃")
        self.logout.clicked.connect(self._logout)
        self.cancel_work = QPushButton("진행 취소")
        self.cancel_work.setAccessibleName("AI 설정 작업 취소")
        self.cancel_work.clicked.connect(self._cancel.set)
        for button in (self.login, self.logout, self.cancel_work):
            account_row.addWidget(button)
        form.addRow(account_row)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setAccessibleName("API 키 교체")
        self.key.setPlaceholderText("교체할 때만 새 키를 입력하세요")
        form.addRow("API 키", self.key)
        self.save_key = QPushButton("키 저장")
        self.save_key.setAccessibleName("API 키 저장")
        self.save_key.clicked.connect(self._save_key)
        form.addRow(self.save_key)
        self.remove_key = QPushButton("저장된 키 삭제")
        self.remove_key.setAccessibleName("저장된 API 키 삭제")
        self.remove_key.clicked.connect(self._mark_remove_key)
        form.addRow(self.remove_key)
        self.free_ack = QCheckBox("Groq/Gemini 무료 한도와 초과 비용 정책을 확인했습니다.")
        self.free_ack.setAccessibleName("무료 한도 확인")
        form.addRow(self.free_ack)
        self.local_ack = QCheckBox("Ollama 서버에 OLLAMA_NO_CLOUD=1 또는\n"
                                   "disable_ollama_cloud=true를 설정하고 서버를 재시작했습니다.")
        self.local_ack.setAccessibleName("Ollama 로컬 서버 확인")
        form.addRow(self.local_ack)
        self.status = QLabel()
        self.status.setAccessibleName("AI 설정 상태")
        self.status.setWordWrap(True)
        form.addRow(self.status)
        scroll.setWidget(body)
        root.addWidget(scroll)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                        QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.profile.currentIndexChanged.connect(self._select_profile)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self._last_profile_index = self.profile.currentIndex()
        self._last_provider = self.provider.currentData()
        self._reload_accounts()
        self._provider_changed()

    @property
    def is_busy(self):
        return self._busy

    def _reload_accounts(self, selected=None):
        import chatgpt_auth
        self.account.clear()
        for account in chatgpt_auth.list_accounts():
            if account.connected:
                self.account.addItem(f"{account.display_name} · {account.workspace_label}", account.id)
        if selected is not None:
            self.account.setCurrentIndex(self.account.findData(selected))

    def _select_profile(self):
        if self.profile.currentIndex() != self._last_profile_index and self.key.text().strip():
            # Keep the original selection and the key until the user acts.
            with QSignalBlocker(self.profile):
                self.profile.setCurrentIndex(self._last_profile_index)
            self.status.setText("입력한 API 키를 먼저 저장하거나 지운 뒤 프로필을 변경하세요.")
            return
        self._last_profile_index = self.profile.currentIndex()
        selected = next((p for p in self._profiles if p.id == self.profile.currentData()), None)
        self.key.clear()
        self._remove_key = False
        self.model.clear()
        if selected is not None:
            self.provider.setCurrentIndex(self.provider.findData(selected.provider))
            self.model.setEditText(selected.model)
            self.account.setCurrentIndex(self.account.findData(selected.account_id))
            self.free_ack.setChecked(selected.free_tier_acknowledged)
            self.local_ack.setChecked(selected.ollama_no_cloud_acknowledged)
        else:
            self.free_ack.setChecked(False)
            self.local_ack.setChecked(False)
        self._provider_changed()

    def _provider_changed(self):
        provider = self.provider.currentData()
        if provider != self._last_provider:
            if self.key.text().strip():
                with QSignalBlocker(self.provider):
                    self.provider.setCurrentIndex(self.provider.findData(self._last_provider))
                self.status.setText("입력한 API 키를 먼저 저장하거나 지운 뒤 제공자를 변경하세요.")
                return
            # A model from a different provider must never become the new
            # provider's candidate by accident.
            self.model.clear()
            self._last_provider = provider
        guides = {
            "chatgpt-subscription": "1. ChatGPT 로그인 → 2. 모델 새로고침 → 3. 모델 선택 후 저장. 구독 및 계정 한도가 적용됩니다.",
            "groq": "1. API 키 입력 후 키 저장 → 2. 무료 한도 확인 → 3. 모델 새로고침 → 4. 모델 선택 후 저장.",
            "gemini": "1. API 키 입력 후 키 저장 → 2. 무료 한도 확인 → 3. 모델 새로고침 → 4. 모델 선택 후 저장.",
            "openrouter": "1. API 키 입력 후 키 저장 → 2. 모델 새로고침 → 3. :free 모델 선택 후 저장. 무료 모델에도 사용 제한이 있습니다.",
            "ollama": "별도 로컬 서버와 모델을 설치한 경우에만 사용합니다.",
        }
        self.provider_guide.setText(guides.get(provider, ""))
        chatgpt = provider == "chatgpt-subscription"
        for widget in (self.account, self.login, self.logout):
            widget.setEnabled(chatgpt and not self._busy)
        self.key.setEnabled(not chatgpt and provider != "ollama" and not self._busy)
        self.remove_key.setEnabled(self.key.isEnabled())
        self.save_key.setEnabled(self.key.isEnabled())
        self.free_ack.setVisible(provider in ("groq", "gemini"))
        self.local_ack.setVisible(provider == "ollama")
        self.status.setText("OpenRouter는 :free 모델만 저장할 수 있습니다." if provider == "openrouter"
                            else "키는 운영체제 비밀 저장소에만 저장됩니다.")

    def _mark_remove_key(self):
        from ai_profiles import save_profiles
        from ai_secrets import delete_secret
        provider = self.provider.currentData()
        try:
            delete_secret("provider:" + provider)
            self._profiles = [replace(p, revision=p.revision + 1) if p.provider == provider else p
                              for p in self._profiles]
            save_profiles(self._profiles)
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return
        self.key.clear()
        self.profiles_changed.emit()
        self.status.setText("저장된 키를 삭제했습니다.")

    def _save_key(self):
        from ai_profiles import save_profiles
        from ai_secrets import set_secret
        provider = self.provider.currentData()
        key = self.key.text().strip()
        try:
            if provider not in ("groq", "gemini", "openrouter"):
                raise ValueError("API 키를 사용하는 제공자를 선택하세요.")
            if not key or any(ord(character) < 33 or ord(character) > 126 for character in key):
                raise ValueError("올바른 API 키를 입력하세요.")
            set_secret("provider:" + provider, {"api_key": key})
            self._profiles = [replace(p, revision=p.revision + 1) if p.provider == provider else p
                              for p in self._profiles]
            save_profiles(self._profiles)
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return
        self.key.clear()
        self.profiles_changed.emit()
        self.status.setText("키를 저장했습니다. 모델 새로고침으로 사용 가능한 모델을 확인하세요.")

    def _candidate(self):
        from ai_profiles import AIProfile
        previous = next((p for p in self._profiles if p.id == self.profile.currentData()), None)
        return AIProfile(
            id=previous.id if previous else uuid.uuid4().hex,
            provider=self.provider.currentData(), model=self.model.currentText().strip(),
            account_id=self.account.currentData() if self.provider.currentData() == "chatgpt-subscription" else None,
            free_tier_acknowledged=self.free_ack.isChecked(),
            ollama_no_cloud_acknowledged=self.local_ack.isChecked(),
            revision=previous.revision + 1 if previous else 0)

    def _start_worker(self, work, callback):
        if self._busy:
            return
        self._cancel = threading.Event()
        self._busy = True
        self._worker_callback = callback
        for widget in (self.profile, self.provider, self.model, self.refresh, self.account,
                       self.login, self.logout, self.key, self.save_key, self.remove_key, self.buttons):
            widget.setEnabled(False)
        self.busy_changed.emit(True)
        self.status.setText("진행 중입니다. 취소할 수 있습니다.")

        def run():
            try:
                value = work(self._cancel)
                result = (value, None)
            except Exception as exc:
                # Never expose arbitrary errors from a worker: exceptions may
                # include credentials, request text or server response bodies.
                from ai_backends import BackendError
                from chatgpt_auth import (AuthenticationError, CancelledError,
                                          NetworkError, ProtocolError, UsageLimitError)
                if isinstance(exc, CancelledError):
                    message = "요청을 취소했습니다."
                elif isinstance(exc, AuthenticationError):
                    message = "ChatGPT 계정 연결을 확인하고 필요하면 다시 로그인하세요."
                elif isinstance(exc, UsageLimitError):
                    message = "ChatGPT 사용이 현재 제한되어 있습니다. 잠시 후 다시 확인하세요."
                elif isinstance(exc, NetworkError):
                    message = "ChatGPT 연결에 실패했습니다. 인터넷 연결을 확인하세요."
                elif isinstance(exc, ProtocolError):
                    message = "ChatGPT 응답을 확인할 수 없습니다. 잠시 후 다시 시도하세요."
                elif isinstance(exc, BackendError):
                    known = {
                        "Configure the provider API key first.": "API 키를 입력하고 ‘키 저장’을 누른 뒤 다시 시도하세요.",
                        "AI service rate limit reached (HTTP 429).": "서비스 사용 한도에 도달했습니다. 잠시 후 다시 시도하세요.",
                        "Invalid AI profile settings.": "제공자와 무료 한도 확인을 살펴보고 다시 시도하세요.",
                        "AI request cancelled.": "요청을 취소했습니다.",
                        "AI request deadline exceeded.": "응답 시간이 초과되었습니다. 잠시 후 다시 시도하세요.",
                        "ChatGPT 계정 연결을 확인하고 필요하면 다시 연결해 주세요.": "ChatGPT 계정 연결을 확인하고 필요하면 다시 연결해 주세요.",
                        "ChatGPT 사용이 현재 제한되어 있습니다. 잠시 후 다시 확인해 주세요.": "ChatGPT 사용이 현재 제한되어 있습니다. 잠시 후 다시 확인해 주세요.",
                        "ChatGPT 연결에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 시도해 주세요.": "ChatGPT 연결에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 시도해 주세요.",
                        "ChatGPT 응답을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.": "ChatGPT 응답을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                    }
                    message = known.get(str(exc), "연결 또는 모델 조회에 실패했습니다. 설정과 인터넷 연결을 확인하세요.")
                else:
                    message = "연결 또는 모델 조회에 실패했습니다. 설정과 인터넷 연결을 확인하세요."
                result = (None, message)
            try:
                self._signals.finished.emit(result)
            except RuntimeError:
                pass
        try:
            threading.Thread(target=run, daemon=True, name="ai-settings").start()
        except RuntimeError:
            self._worker_done((None, "작업을 시작하지 못했습니다. 다시 시도하세요."))

    def _worker_done(self, result):
        self._busy = False
        for widget in (self.profile, self.provider, self.model, self.refresh, self.buttons):
            widget.setEnabled(True)
        self._provider_changed()
        value, error = result
        if error:
            self.status.setText(error)
        elif not self._cancel.is_set():
            try:
                self._worker_callback(value)
            except (ValueError, OSError):
                self.status.setText("설정 결과를 적용하지 못했습니다. 다시 시도하세요.")
        else:
            self.status.setText("취소했습니다.")
        if not self._busy:
            self.busy_changed.emit(False)

    def _login(self):
        import chatgpt_auth
        self._start_worker(lambda cancel: chatgpt_auth.login(open_browser=webbrowser.open, cancel=cancel),
                           lambda account: self._login_done(account))

    def _login_done(self, account):
        self._reload_accounts(account.id)
        self.profiles_changed.emit()
        self.refresh_models()

    def _logout(self):
        import chatgpt_auth
        account_id = self.account.currentData()
        if account_id:
            self._start_worker(lambda cancel: chatgpt_auth.logout(account_id),
                               lambda value: self._logout_done())

    def _logout_done(self):
        self._reload_accounts()
        self._account_models.clear()
        self.model.clear()
        self.profiles_changed.emit()

    def refresh_models(self):
        from ai_profiles import list_models
        candidate = self._candidate()
        if candidate.provider in ("groq", "gemini", "openrouter") and self.key.text().strip():
            self.status.setText("입력한 API 키를 먼저 ‘키 저장’한 다음 모델을 새로고침하세요.")
            return
        if candidate.provider == "chatgpt-subscription":
            if not candidate.account_id:
                self.status.setText("먼저 ChatGPT 계정에 로그인하세요.")
                return
        def discover(cancel):
            return list_models(candidate, cancel=cancel)
        self._start_worker(discover,
                           lambda models: self._models_done(models, candidate))

    def _models_done(self, models, candidate):
        from ai_profiles import ModelInfo
        models = [ModelInfo(model["id"], model["label"]) if isinstance(model, dict) else model
                  for model in models]
        selected = self.model.currentText()
        self.model.clear()
        for model in models:
            self.model.addItem(model.label, model.id)
        # Editable text contains the exact model id, not its display label.
        self.model.setEditText(selected or (models[0].id if models else ""))
        if candidate.provider == "chatgpt-subscription":
            self._account_models[candidate.account_id] = {model.id for model in models}
        self.status.setText(f"모델 {len(models)}개를 불러왔습니다.")

    def _save(self):
        from ai_profiles import save_profiles, validate_profile
        from ai_secrets import get_secret, set_secret
        try:
            profile = self._candidate()
            # A selected catalogue label must resolve to its model id.
            if self.model.currentIndex() >= 0 and self.model.currentText() == self.model.itemText(self.model.currentIndex()):
                model_id = self.model.currentData()
                if model_id:
                    profile = replace(profile, model=model_id)
            validate_profile(profile)
            if profile.provider == "chatgpt-subscription":
                import chatgpt_auth
                if not profile.account_id:
                    raise ValueError("먼저 ChatGPT 계정에 로그인하세요.")
                account = chatgpt_auth.account_status(profile.account_id)
                if not account.connected or profile.model not in self._account_models.get(profile.account_id, set()):
                    raise ValueError("연결한 계정의 모델을 새로고침하고 선택하세요.")
            if profile.provider in ("groq", "gemini") and not profile.free_tier_acknowledged:
                raise ValueError("무료 한도 확인이 필요합니다.")
            if profile.provider == "ollama" and not profile.ollama_no_cloud_acknowledged:
                raise ValueError("Ollama 클라우드 비활성화 설정과 서버 재시작 확인이 필요합니다.")
            if profile.provider == "openrouter" and not profile.model.endswith(":free"):
                raise ValueError("OpenRouter 무료 모델을 선택하세요.")
            key = self.key.text().strip()
            if profile.provider in ("groq", "gemini", "openrouter") and not key:
                saved = get_secret("provider:" + profile.provider)
                if not saved or not saved.get("api_key"):
                    raise ValueError("API 키를 입력하세요.")
            if key:
                set_secret("provider:" + profile.provider, {"api_key": key})
            profiles = [p for p in self._profiles if p.id != profile.id] + [profile]
            save_profiles(profiles)
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return
        self.key.clear()
        self._profiles = profiles
        self.profiles_changed.emit()
        self.accept()

    def reject(self):
        self._cancel.set()
        self.key.clear()
        super().reject()
