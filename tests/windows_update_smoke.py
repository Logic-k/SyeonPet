"""Isolated signed-update smoke test using the actual Windows release launcher.

The archive is copied to a temporary installation.  Only the temporary copies
receive an instrumentation wrapper and a freshly generated test signing key.
GitHub responses are simulated; no external AI service or user data is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


WRAPPER = r'''import sys
sys.dont_write_bytecode = True
import json, os, traceback
from pathlib import Path
import updater_core, updater_switch
from update_trust import PUBLIC_KEY_BYTES

root = Path(__file__).resolve().parent
qa = Path(os.environ['SYEON_QA_DIR'])
mode = os.environ.get('SYEON_QA_MODE', 'prepare')
child = bool(os.environ.get('SYEONPET_UPDATE_CHILD'))
def mark(message):
    with (qa / ('trace-' + mode + '-' + ('child' if child else 'root') + '.txt')).open('a', encoding='utf-8') as f:
        f.write(message + '\n')
mark('wrapper started')
if child and mode == 'candidate_fail':
    raise SystemExit(7)

result = updater_switch.bootstrap(root, PUBLIC_KEY_BYTES, updater_core.verify_staged)
mark('bootstrap returned')
if result is not None:
    raise SystemExit(result)

def fake_download(url, limit):
    mark('synthetic download requested')
    if mode == 'offline':
        raise OSError('synthetic network failure')
    assets = qa / 'assets'
    if url == updater_core.API_URL:
        data = (assets / 'release.json').read_bytes()
    else:
        name = url.rsplit('/', 1)[-1]
        if name not in {'SyeonPet-0.1.7-windows.zip', 'SyeonPet-0.1.7-update.json', 'SyeonPet-0.1.7-update.sig'}:
            raise ValueError('unexpected synthetic URL')
        data = (assets / name).read_bytes()
    if len(data) > limit:
        raise ValueError('synthetic response over limit')
    return data

updater_core._download_body = fake_download
original_prepare = updater_core.prepare_update
def diagnostic_prepare(*args, **kwargs):
    try:
        return original_prepare(*args, **kwargs)
    except Exception:
        mark(traceback.format_exc())
        raise
updater_core.prepare_update = diagnostic_prepare

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox
import _production_main as entry
import wardrobe
from chat import ChatPanel
from optimizer_library import LibraryStore
from version import APP_VERSION

library_init = LibraryStore.__init__
def isolated_library_init(self, path=None):
    library_init(self, path if path is not None else qa / 'data' / 'library.json')
LibraryStore.__init__ = isolated_library_init

def library_item(name):
    return {'id': name, 'title': name, 'prompt': '가상 요청 ' + name,
            'result': '가상 결과 ' + name, 'task': 'general', 'detail': 'balanced',
            'createdAt': '2026-01-01T00:00:00Z'}

def settings(name):
    s = QSettings(str(qa / (name + '.ini')), QSettings.Format.IniFormat)
    s.setFallbacksEnabled(False)
    return s

wardrobe._settings = lambda settings_arg=None: settings_arg if settings_arg is not None else settings('wardrobe')
entry.autostart_enabled = lambda: False
original_present = entry.present_update_result
def capture_update(result, tray, dialogs):
    mark('update result: ' + str(result.get('status')))
    if mode == 'offline':
        (qa / 'offline-result.json').write_text(json.dumps({'status': result.get('status')}), encoding='utf-8')
    return original_present(result, tray, dialogs)
entry.present_update_result = capture_update

class IsolatedChatPanel(ChatPanel):
    def __init__(self, *args, **kwargs):
        kwargs['settings'] = settings('conversation')
        super().__init__(*args, **kwargs)

def report_failure():
    (qa / ('error-' + mode + '-' + ('child' if child else 'root') + '.txt')).write_text(traceback.format_exc(), encoding='utf-8')
    QApplication.instance().exit(1)

class TestApplication(QApplication):
    def __init__(self, args):
        super().__init__(args)
        QTimer.singleShot(500, self.start_test)
        QTimer.singleShot(90000, lambda: self.exit(9))

    def start_test(self):
        mark('start_test')
        try:
            panels = [x for x in self.topLevelWidgets() if isinstance(x, ChatPanel)]
            assert len(panels) == 1
            self.panel = panels[0]
            p = self.panel.prompt_conversation
            if child:
                assert APP_VERSION == '0.1.7'
                if mode == 'repeat':
                    (qa / 'candidate-repeat.json').write_text(json.dumps({'version': APP_VERSION}), encoding='utf-8')
                    QTimer.singleShot(500, self.quit)
                    return
                assert p.input.toPlainText() == '추가 조건: 표로 정리'
                assert p.result.toPlainText() == '검토한 결과 문장'
                assert p.original == '회의 결과를 세 항목으로 요약해 주세요.'
                assert self.panel.isVisible()
                assert self.panel.opacity_slider.value() == 91
                picker = p._template_dialog
                assert picker is not None and picker.isVisible()
                assert picker.preview.toPlainText() == '두 번째 가상 템플릿 초안'
                assert '첫 가상 템플릿 초안' in picker._drafts.values()
                library = LibraryStore()
                assert library.path.resolve().is_relative_to(qa)
                previous = json.loads((qa / 'before-library.json').read_text(encoding='utf-8'))
                assert library.load() == previous
                merged, added = library.merge([library_item('after-update')])
                assert added == 1 and len(merged) == len(previous) + 1
                assert library.load()[:len(previous)] == previous
                (qa / ('candidate-' + mode + '.json')).write_text(json.dumps({'version': APP_VERSION, 'restored': True}), encoding='utf-8')
                QTimer.singleShot(1500, self.quit)
                return
            assert APP_VERSION == '0.1.6'
            if mode in ('fallback', 'candidate_fail', 'offline'):
                (qa / ('root-' + mode + '.json')).write_text(json.dumps({'version': APP_VERSION}), encoding='utf-8')
                QTimer.singleShot(4200 if mode == 'offline' else 800, self.quit)
                return
            if mode == 'second_instance':
                assert updater_switch.pending_version(entry.INSTALL_ROOT) == '0.1.7'
                (qa / 'second-instance.json').write_text(json.dumps({'version': APP_VERSION}), encoding='utf-8')
                QTimer.singleShot(500, self.quit)
                return
            self.panel.show_prompt_near_pet()
            self.panel.opacity_slider.setValue(91)
            library = LibraryStore()
            assert library.path.resolve().is_relative_to(qa)
            _, added = library.merge([library_item('before-update-1')])
            assert added == 1
            saved, added = library.merge([library_item('before-update-2')])
            assert added == 1 and library.load() == saved
            (qa / 'before-library.json').write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
            p.provider.setCurrentIndex(p.provider.findData('offline'))
            p.input.setPlainText('회의 결과를 세 항목으로 요약해 주세요.')
            p.send.click()
            self.after_offline()
        except Exception:
            report_failure()

    def after_offline(self):
        try:
            p = self.panel.prompt_conversation
            if p.is_busy:
                QTimer.singleShot(100, self.after_offline)
                return
            assert p.original == '회의 결과를 세 항목으로 요약해 주세요.'
            mark('offline result complete')
            p.result.setPlainText('검토한 결과 문장')
            p.input.setPlainText('추가 조건: 표로 정리')
            p._insert_template()
            picker = p._template_dialog
            picker.office_button.setChecked(True)
            picker.list.setCurrentRow(0)
            picker.preview.setPlainText('첫 가상 템플릿 초안')
            picker.list.setCurrentRow(1)
            picker.preview.setPlainText('두 번째 가상 템플릿 초안')
            self.wait_ready()
        except Exception:
            report_failure()

    def wait_ready(self):
        try:
            if updater_switch.pending_version(entry.INSTALL_ROOT) != '0.1.7':
                if mode == 'hold' and (qa / 'release-hold').exists():
                    self.quit()
                    return
                QTimer.singleShot(150, self.wait_ready)
                return
            mark('pending update detected')
            (qa / 'pending.json').write_text(json.dumps({'version': APP_VERSION}), encoding='utf-8')
            if mode == 'hold' and not (qa / 'release-hold').exists():
                QTimer.singleShot(150, self.wait_ready)
                return
            menus = [x for x in self.topLevelWidgets() if isinstance(x, QMenu)]
            actions = [a for menu in menus for a in menu.actions() if a.text() == '종료']
            assert len(actions) == 1
            actions[0].trigger()
            dialog = self.panel._quit_dialog
            assert dialog is not None and dialog.isVisible()
            dialog.done(int(QMessageBox.StandardButton.Yes))
        except Exception:
            report_failure()

entry.QApplication = TestApplication
entry.ChatPanel = IsolatedChatPanel
raise SystemExit(entry.main())
'''


def _run(args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=subprocess.CREATE_NO_WINDOW, **kwargs)


def _safe_extract(archive: Path, root: Path):
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            parts = PurePosixPath(name).parts
            if not parts or name.startswith('/') or '..' in parts or '\\' in name or ':' in name:
                raise ValueError('unsafe archive path')
        z.extractall(root)


def _instrument(root: Path, key: bytes):
    (root / 'main.py').rename(root / '_production_main.py')
    (root / 'main.py').write_text(WRAPPER, encoding='utf-8')
    (root / 'update_trust.py').write_text('PUBLIC_KEY_BYTES = bytes.fromhex(' + repr(key.hex()) + ')\n', encoding='utf-8')


def _candidate_zip(root: Path, dest: Path):
    manifest = json.loads((root / 'package-manifest.json').read_text(encoding='utf-8'))
    files = {}
    with zipfile.ZipFile(dest, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in root.rglob('*'):
            if not path.is_file() or path.name == 'package-manifest.json':
                continue
            name = path.relative_to(root).as_posix()
            data = path.read_bytes()
            if name == 'version.py':
                data = data.replace(b'0.1.6', b'0.1.7')
            files[name] = hashlib.sha256(data).hexdigest()
            archive.writestr(name, data)
        manifest['version'] = '0.1.7'
        manifest['files'] = files
        archive.writestr('package-manifest.json', json.dumps(manifest, ensure_ascii=False).encode('utf-8'))


def _release(root: Path, openssl: str):
    assets = root / 'assets'
    assets.mkdir()
    key = root / 'test-private.pem'
    public = root / 'test-public.der'
    _run([openssl, 'genpkey', '-algorithm', 'Ed25519', '-out', str(key)])
    _run([openssl, 'pkey', '-in', str(key), '-pubout', '-outform', 'DER', '-out', str(public)])
    der = public.read_bytes()
    if len(der) != 44 or der[:12] != bytes.fromhex('302a300506032b6570032100'):
        raise ValueError('unexpected test key format')
    return assets, key, der[-32:]


def _sign_release(assets: Path, key: Path, openssl: str):
    archive = assets / 'SyeonPet-0.1.7-windows.zip'
    manifest = assets / 'SyeonPet-0.1.7-update.json'
    signature = assets / 'SyeonPet-0.1.7-update.sig'
    blob = archive.read_bytes()
    manifest.write_text(json.dumps({'schema': 1, 'platform': 'windows-x64', 'version': '0.1.7',
                                    'asset': archive.name, 'size': len(blob),
                                    'sha256': hashlib.sha256(blob).hexdigest()}, separators=(',', ':')), encoding='utf-8')
    _run([openssl, 'pkeyutl', '-sign', '-inkey', str(key), '-rawin', '-in', str(manifest), '-out', str(signature)])
    tag = 'v0.1.7'
    names = [archive.name, manifest.name, signature.name]
    release = {'tag_name': tag, 'draft': False, 'prerelease': False,
               'html_url': 'https://github.com/Logic-k/SyeonPet/releases/tag/' + tag,
               'assets': [{'name': n, 'state': 'uploaded', 'browser_download_url':
                           'https://github.com/Logic-k/SyeonPet/releases/download/' + tag + '/' + n} for n in names]}
    (assets / 'release.json').write_text(json.dumps(release), encoding='utf-8')


def _launch(install: Path, qa: Path, mode: str, wait=True, local_name='localappdata'):
    env = os.environ.copy()
    env.pop('QT_QPA_PLATFORM', None)
    env['APPDATA'] = str(qa / 'appdata')
    env['LOCALAPPDATA'] = str(qa / local_name)
    env['SYEON_QA_DIR'] = str(qa)
    env['SYEON_QA_MODE'] = mode
    for name in ('SYEONPET_UPDATE_CHILD', 'SYEONPET_INSTALL_ROOT', 'SYEONPET_UPDATE_READY_TOKEN'):
        env.pop(name, None)
    process = subprocess.Popen([str(install / 'SyeonPet.exe')], cwd=install, env=env,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    if not wait:
        return process
    code = process.wait(timeout=100)
    errors = list(qa.glob('error-*.txt'))
    if code or errors:
        raise RuntimeError(f'{mode}: exit={code}; ' + '\n'.join(x.read_text(encoding='utf-8') for x in errors))
    return process


def run(archive: Path, openssl: str, short_path=False):
    if os.name != 'nt':
        raise RuntimeError('Windows is required')
    import time
    with tempfile.TemporaryDirectory(prefix='sq-' if short_path else 'syeon-update-qa-') as tmp:
        qa = Path(tmp).resolve()
        install = qa / 'install'
        install.mkdir()
        _safe_extract(archive, install)
        assets, key, public = _release(qa, openssl)
        _instrument(install, public)
        _candidate_zip(install, assets / 'SyeonPet-0.1.7-windows.zip')
        _sign_release(assets, key, openssl)
        config = install / 'config.json'
        before_config = config.read_bytes()
        local_name = 'l' if short_path else 'localappdata'
        hold = _launch(install, qa, 'hold', wait=False, local_name=local_name)
        try:
            deadline = time.monotonic() + 40
            while not (qa / 'pending.json').exists() and time.monotonic() < deadline:
                if hold.poll() is not None:
                    traces = '\n'.join(x.read_text(encoding='utf-8') for x in qa.glob('trace-*.txt'))
                    errors = '\n'.join(x.read_text(encoding='utf-8') for x in qa.glob('error-*.txt'))
                    raise RuntimeError('first instance exited before preparation; ' + traces + errors)
                time.sleep(.2)
            if not (qa / 'pending.json').exists():
                traces = '\n'.join(x.read_text(encoding='utf-8') for x in qa.glob('trace-*.txt'))
                errors = '\n'.join(x.read_text(encoding='utf-8') for x in qa.glob('error-*.txt'))
                raise RuntimeError('update preparation timed out; ' + traces + errors)
            _launch(install, qa, 'second_instance', local_name=local_name)
            assert json.loads((qa / 'second-instance.json').read_text())['version'] == '0.1.6'
            (qa / 'release-hold').write_text('done')
            if hold.wait(timeout=30):
                raise RuntimeError('first instance did not close normally')
            _launch(install, qa, 'prepare', local_name=local_name)
            assert json.loads((qa / 'candidate-prepare.json').read_text())['restored']
            state_paths = list((qa / local_name / 'SyeonPet' / 'updates').glob('*/state.json'))
            assert len(state_paths) == 1
            state = json.loads(state_paths[0].read_text(encoding='utf-8'))
            assert state['active'] == '0.1.7' and state['pending'] is None
            assert config.read_bytes() == before_config
            # A fresh process must validate the unchanged staged app again.
            # The prior snapshot has been marked restored on normal exit, so
            # this run checks activation separately instead of draft restore.
            _launch(install, qa, 'repeat', local_name=local_name)
            assert (qa / 'candidate-repeat.json').is_file()
            staged = state_paths[0].parent / 'versions' / '0.1.7'
            signature = staged / 'manifest.sig'
            before_signature = signature.read_bytes()
            signature.write_bytes(bytes([before_signature[0] ^ 1]) + before_signature[1:])
            _launch(install, qa, 'fallback', local_name=local_name)
            assert (qa / 'root-fallback.json').is_file()
            state = json.loads(state_paths[0].read_text(encoding='utf-8'))
            assert state['active'] is None and state['pending'] is None
            signature.write_bytes(before_signature)
            state['pending'] = '0.1.7'
            state_paths[0].write_text(json.dumps(state), encoding='utf-8')
            _launch(install, qa, 'candidate_fail', local_name=local_name)
            assert (qa / 'root-candidate_fail.json').is_file()
            state = json.loads(state_paths[0].read_text(encoding='utf-8'))
            assert state['active'] is None and state['pending'] is None
            _launch(install, qa, 'offline', local_name=local_name)
            assert json.loads((qa / 'offline-result.json').read_text(encoding='utf-8'))['status'] == 'unavailable'
            assert config.read_bytes() == before_config
            print('PASS: signed synthetic download, multi-instance deferral, restart and conversation/template draft restore, repeated launch, tamper/start failure rollback, offline, settings and library preservation')
            print('LIMIT: GitHub transport and data are simulated; no external AI; GUI uses synthetic Qt events')
        finally:
            if hold.poll() is None:
                (qa / 'release-hold').write_text('done')
                hold.wait(timeout=30)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--openssl', default='openssl')
    parser.add_argument('--short-path', action='store_true', help='diagnose behavior with a shorter isolated LOCALAPPDATA path')
    args = parser.parse_args()
    run(args.archive.resolve(strict=True), args.openssl, args.short_path)
