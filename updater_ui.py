"""Background update preparation and restart snapshots for the Qt application."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, Signal

import updater_core
import updater_switch
from update_trust import PUBLIC_KEY_BYTES
from version import APP_VERSION

_MAX_DRAFT_BYTES = 20 * 1024 * 1024


class UpdatePreparer(QObject):
    """Prepare signed updates without blocking the single Qt event loop."""

    completed = Signal(object)

    def __init__(self, install_root, parent=None):
        super().__init__(parent)
        self.install_root = Path(install_root)
        self._lock = threading.Lock()
        self._running = False
        self._manual = False
        self._shutting_down = False

    def begin_shutdown(self):
        # Serialize with queue_update: a download completing during quit must
        # not activate a version after the draft preservation decision.
        with self._lock:
            self._shutting_down = True

    def cancel_shutdown(self):
        with self._lock:
            self._shutting_down = False

    def check(self, manual=False):
        with self._lock:
            self._manual |= bool(manual)
            if self._running:
                return
            self._running = True

        def work():
            try:
                result = updater_core.prepare_update(
                    APP_VERSION, updater_switch.state_directory(self.install_root),
                    PUBLIC_KEY_BYTES)
                if result['status'] in ('ready', 'already_ready'):
                    with self._lock:
                        if not self._shutting_down:
                            updater_switch.queue_update(self.install_root, result['version'],
                                                        current_version=APP_VERSION)
                        result['version'] = updater_switch.pending_version(self.install_root) or result['version']
                    result['status'] = 'ready'
                    result['message'] = (f"새 버전 {result['version']}을 준비했어요. "
                                         "션을 종료하고 다시 실행하면 적용됩니다.")
                else:
                    result['message'] = '현재 버전이 최신입니다.'
            except updater_core.UpdateError as exc:
                # Core messages are fixed local strings, never server bodies.
                result = {'status': 'unavailable', 'message':
                          str(exc) + ' 기존 션은 계속 사용할 수 있어요.'}
            except Exception:
                # Do not expose downloaded content, local paths or tokens in UI.
                result = {'status': 'unavailable', 'message':
                          '업데이트를 준비하지 못했어요. 인터넷 연결을 확인한 후 다시 시도해 주세요. '
                          '기존 션은 계속 사용할 수 있어요.'}
            with self._lock:
                result['manual'] = self._manual
                self._manual = False
                self._running = False
            try:
                self.completed.emit(result)
            except RuntimeError:
                pass

        try:
            threading.Thread(target=work, daemon=True, name='syeon-update-prepare').start()
        except Exception:
            with self._lock:
                self._running = False
                manual = self._manual
                self._manual = False
            self.completed.emit({'status': 'unavailable', 'manual': manual,
                                 'message': '업데이트 확인을 시작하지 못했어요.'})


class _DraftLock:
    """OS lock releases automatically on crash; no other process may claim it."""

    def __init__(self, path):
        self._file = open(path, 'a+b')
        if self._file.seek(0, os.SEEK_END) == 0:
            self._file.write(b'\0')
            self._file.flush()
        self._file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._file.close()
            raise

    def close(self):
        if self._file.closed:
            return
        self._file.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()


class RestartDrafts:
    """Keep independent snapshots for each instance until a clean exit.

    The original is retained even after restoration, so a failed candidate can
    be retried by the previous version without deleting the user's words.
    """

    def __init__(self, install_root):
        self.directory = updater_switch.state_directory(install_root) / 'drafts'
        self._claimed = None
        self._lock = None

    def save(self, panel):
        data = json.dumps(panel.export_update_state(), ensure_ascii=False,
                          separators=(',', ':')).encode('utf-8')
        if len(data) > _MAX_DRAFT_BYTES:
            raise ValueError('대화가 너무 커서 업데이트용으로 저장하지 못했어요.')
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise ValueError('대화 저장 위치를 확인하지 못했어요.')
        target = self.directory / (uuid.uuid4().hex + '.json')
        fd, tmp = tempfile.mkstemp(prefix='.draft-', suffix='.tmp', dir=self.directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # A unique path avoids overwriting any earlier user draft.
            if target.exists():
                raise FileExistsError('대화 저장 파일이 이미 있습니다.')
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return target

    def restore(self, panel):
        if not self.directory.exists():
            return False
        if self.directory.is_symlink():
            raise ValueError('대화 저장 위치를 확인하지 못했어요.')
        for path in sorted(self.directory.glob('*.json'), key=lambda p: p.name):
            if (path.is_symlink() or len(path.stem) != 32
                    or any(c not in '0123456789abcdef' for c in path.stem)):
                continue
            lock_path = path.with_suffix('.lock')
            if lock_path.is_symlink():
                raise ValueError('대화 잠금 위치를 확인하지 못했어요.')
            try:
                lock = _DraftLock(lock_path)
            except OSError:
                continue
            try:
                if not path.exists():
                    lock.close()
                    continue
                if path.stat().st_size > _MAX_DRAFT_BYTES:
                    raise ValueError('복원할 대화가 너무 커요.')
                data = json.loads(path.read_text(encoding='utf-8'))
                visible = panel.restore_update_state(data)
                self._claimed, self._lock = path, lock
                return visible
            except Exception:
                lock.close()
                raise
        return False

    def finish_cleanly(self):
        if self._claimed is None:
            return
        try:
            restored = self._claimed.with_suffix('.restored')
            if not restored.exists():
                self._claimed.rename(restored)
        finally:
            self._lock.close()
            self._claimed = self._lock = None

    def remaining_count(self):
        if not self.directory.exists():
            return 0
        return sum(1 for path in self.directory.glob('*.json')
                   if path != self._claimed and not path.is_symlink())


def prepare_quit(preparer, drafts, panel, install_root):
    """Freeze queueing, then preserve drafts if an update became pending."""
    preparer.begin_shutdown()
    try:
        if updater_switch.pending_version(install_root):
            drafts.save(panel)
    except Exception:
        preparer.cancel_shutdown()
        raise
