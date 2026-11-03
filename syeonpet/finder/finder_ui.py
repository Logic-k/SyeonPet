"""Warm, novice-friendly PySide6 UI for Syeon Smart File Finder.

No English jargon, high-contrast 맑은 고딕 typography, instant recent document listing,
and safe execution blocking guards.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import time

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .index_engine import FileIndex, build_index, get_cache_file_path, safe_open_or_reveal, search

STYLE_CARD = """
QFrame { background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 4px; }
QFrame:hover { border-color: #93c5fd; background-color: #f8fafc; }
"""
STYLE_BTN_BLUE = "QPushButton { background: #2563eb; color: #fff; font-weight: bold; border-radius: 4px; padding: 4px 10px; } QPushButton:hover { background: #1d4ed8; } QPushButton:disabled { background: #94a3b8; color: #e2e8f0; }"
STYLE_BTN_SEC = "QPushButton { background: #f8fafc; color: #334155; border: 1px solid #cbd5e1; border-radius: 4px; padding: 4px 10px; } QPushButton:hover { background: #e2e8f0; }"
STYLE_BTN_WARM = "QPushButton { background: #fef3c7; color: #92400e; font-weight: bold; border: 1px solid #fde68a; border-radius: 4px; padding: 4px 10px; } QPushButton:hover { background: #fde68a; }"


class FileCardWidget(QFrame):
    """Card widget representing a single search result file."""

    def __init__(self, item: dict, send_callback, parent=None):
        super().__init__(parent)
        self.item, self.send_callback = item, send_callback
        self.setStyleSheet(STYLE_CARD)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(5)

        # Header: badge + filename + time
        top_row = QHBoxLayout()
        ext = item.get("ext", "").lower()
        badge_txt, badge_col = self._badge_style(ext, item.get("is_dangerous", False))
        badge = QLabel(badge_txt)
        badge.setStyleSheet(f"background: {badge_col}; color: #fff; font-weight: bold; font-size: 11px; border-radius: 4px; padding: 2px 6px;")
        top_row.addWidget(badge)
        name_lbl = QLabel(item.get("name", ""))
        name_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #1e293b;")
        top_row.addWidget(name_lbl, stretch=1)
        mtime = item.get("mtime", 0)
        top_row.addWidget(QLabel(datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else ""))
        lay.addLayout(top_row)

        # Meta row: location + badges + duplicate bundle
        meta_row = QHBoxLayout()
        loc = item.get("folder", "")
        loc_low = loc.lower()
        loc_str = ("💬 카카오톡 받은 파일" if ("kakaotalk" in loc_low or "카카오톡" in loc)
                   else "🖥️ 바탕화면" if ("desktop" in loc_low or "바탕 화면" in loc)
                   else "📥 다운로드 폴더" if ("downloads" in loc_low or "다운로드" in loc)
                   else "📁 내 문서" if ("documents" in loc_low or "문서" in loc)
                   else f"📁 {loc}")
        meta_row.addWidget(QLabel(loc_str))
        if item.get("badge"):
            meta_row.addWidget(QLabel(f"[{item['badge']}]"))
        if item.get("older_versions"):
            meta_row.addWidget(QLabel(f"🔄 이전 버전 {len(item['older_versions'])}개 묶음"))
        meta_row.addStretch()
        lay.addLayout(meta_row)

        # Snippet preview
        if item.get("snippet"):
            snip = QLabel(item["snippet"][:180] + ("..." if len(item["snippet"]) > 180 else ""))
            snip.setStyleSheet("font-size: 12px; color: #334155; background: #f1f5f9; padding: 4px; border-radius: 4px;")
            snip.setWordWrap(True)
            lay.addWidget(snip)

        # Action Buttons
        btn_row = QHBoxLayout()
        is_danger = item.get("is_dangerous", False)
        open_btn = QPushButton("⚠️ 실행 파일 차단" if is_danger else "📄 바로 열기")
        open_btn.setEnabled(not is_danger)
        open_btn.setStyleSheet(STYLE_BTN_BLUE)
        open_btn.clicked.connect(lambda: safe_open_or_reveal(item["path"], "open"))
        btn_row.addWidget(open_btn)

        rev_btn = QPushButton("📁 폴더 위치 열기")
        rev_btn.setStyleSheet(STYLE_BTN_SEC)
        rev_btn.clicked.connect(lambda: safe_open_or_reveal(item["path"], "reveal"))
        btn_row.addWidget(rev_btn)

        ast_btn = QPushButton("✨ 션에게 요약·초안 맡기기")
        ast_btn.setStyleSheet(STYLE_BTN_WARM)
        ast_btn.clicked.connect(lambda: self.send_callback(item))
        btn_row.addWidget(ast_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

    def _badge_style(self, ext: str, is_danger: bool) -> tuple[str, str]:
        if is_danger:
            return "실행파일 차단", "#ef4444"
        if ext in (".hwpx", ".hwp"):
            return "한글", "#2563eb"
        if ext in (".docx", ".doc"):
            return "워드", "#1d4ed8"
        if ext in (".xlsx", ".xls", ".csv"):
            return "엑셀", "#16a34a"
        if ext in (".pptx", ".ppt"):
            return "발표자료", "#ea580c"
        if ext == ".pdf":
            return "PDF", "#dc2626"
        return "텍스트" if ext in (".txt", ".md") else "문서", "#475569"


class _IndexWorker(QThread):
    finished_index = Signal(object)

    def __init__(self, roots=None, cache_path=None, parent=None):
        super().__init__(parent)
        self.roots = roots
        self.cache_path = cache_path

    def run(self):
        try:
            idx = build_index(roots_or_base_dir=self.roots, cache_path=self.cache_path)
            self.finished_index.emit(idx)
        except Exception:
            self.finished_index.emit(None)


class FileFinderWidget(QWidget):
    """Main novice-friendly smart file finder widget."""

    send_to_assistant = Signal(str, str, str)  # (file_name, extracted_text, status_badge)

    def __init__(self, index: FileIndex | None = None, parent=None):
        super().__init__(parent)
        self.index = index
        self._worker: _IndexWorker | None = None
        self._index_requested = False
        self._filter_exts: set[str] | None = None
        self._filter_min_mtime = None
        self.setWindowTitle("폴더 지옥 탈출 · 션이 파일 탐정")
        self.setFont(QFont("맑은 고딕", 10))
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._detach_worker)
        self._init_ui()
        if self.index is not None:
            self.do_search("")
        else:
            if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
                self.index = FileIndex([])
                self.do_search("")
            else:
                self.ensure_indexed()

    def ensure_indexed(self) -> None:
        """Start the default-roots index once; retries are allowed after failure."""
        if self._index_requested:
            return
        self._index_requested = True
        self.start_async_indexing()

    def _extra_roots_path(self) -> Path:
        return get_cache_file_path().parent / "extra_roots.json"

    def _persisted_extra_roots(self) -> list[Path]:
        try:
            raw = json.loads(self._extra_roots_path().read_text(encoding="utf-8"))
        except Exception:
            return []
        out = []
        for item in raw if isinstance(raw, list) else []:
            try:
                p = Path(str(item))
            except Exception:
                continue
            if p.exists():
                out.append(p)
        return out

    def _remember_extra_root(self, folder: Path) -> None:
        roots = self._persisted_extra_roots()
        if str(folder).lower() in {str(r).lower() for r in roots}:
            return
        roots.append(folder)
        try:
            path = self._extra_roots_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([str(r) for r in roots], ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _merged_roots(self, roots=None):
        from .index_engine import get_default_scan_roots
        base = list(roots) if roots else get_default_scan_roots()
        seen = {str(r).lower() for r in base}
        for extra in self._persisted_extra_roots():
            if str(extra).lower() not in seen:
                seen.add(str(extra).lower())
                base.append(extra)
        return base

    def start_async_indexing(self, roots=None, cache_path=None) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText("📂 문서를 확인하고 있어요...")
            self.status_lbl.show()
        self._worker = _IndexWorker(roots=self._merged_roots(roots), cache_path=cache_path, parent=self)
        self._worker.finished_index.connect(self._on_indexing_finished)
        self._worker.start()

    def _on_indexing_finished(self, idx: FileIndex | None) -> None:
        if idx is not None:
            self.index = idx
            if hasattr(self, "status_lbl"):
                self.status_lbl.hide()
        else:
            # Allow the next section open or refresh to retry.
            self._index_requested = False
            if hasattr(self, "status_lbl"):
                self.status_lbl.setText("⚠️ 문서 확인에 실패했어요. 🔄 새로고침으로 다시 시도해 주세요.")
                self.status_lbl.show()
        self.do_search(self.search_input.text())

    def _detach_worker(self) -> None:
        """Release a running worker so it is never destroyed mid-scan."""
        worker = self._worker
        if worker is not None and worker.isRunning():
            try:
                worker.finished_index.disconnect(self._on_indexing_finished)
            except (TypeError, RuntimeError):
                pass
            worker.setParent(None)
            worker.finished.connect(worker.deleteLater)
        self._worker = None

    def closeEvent(self, event) -> None:
        self._detach_worker()
        super().closeEvent(event)

    def _init_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(9)

        # Header Title
        title_box = QVBoxLayout()
        title_box.addWidget(QLabel("📂 션이 파일 탐정 · 폴더 지옥 탈출"))
        sub = QLabel("파일 이름이 기억나지 않아도 괜찮아요! '어제 카톡 엑셀', 'ㅇㅅㅇㄴ', 문서 안의 단어로 찾아보세요.")
        sub.setStyleSheet("font-size: 12px; color: #64748b;")
        title_box.addWidget(sub)
        lay.addLayout(title_box)

        # Search Bar Row
        s_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("검색어를 입력하세요 (예: 기초학력 계획서, 어제 카톡 엑셀, ㅇㅅㅇㄴ)")
        self.search_input.setStyleSheet("font-size: 13px; padding: 7px 10px; border: 2px solid #cbd5e1; border-radius: 6px; background: #fff;")
        self.search_input.returnPressed.connect(self._on_search)
        self.search_input.textChanged.connect(lambda t: self.do_search("") if not t.strip() else None)
        s_row.addWidget(self.search_input, stretch=1)

        btn_find = QPushButton("🔍 찾기")
        btn_find.setStyleSheet("background: #2563eb; color: #fff; font-weight: bold; padding: 7px 14px; border-radius: 6px;")
        btn_find.clicked.connect(self._on_search)
        s_row.addWidget(btn_find)

        btn_add = QPushButton("📂+ 내 업무 폴더 추가")
        btn_add.setStyleSheet("background: #f1f5f9; color: #334155; padding: 7px 10px; border: 1px solid #cbd5e1; border-radius: 6px;")
        btn_add.clicked.connect(self._on_add_folder)
        s_row.addWidget(btn_add)

        btn_ref = QPushButton("🔄 새로고침")
        btn_ref.setStyleSheet("background: #f1f5f9; color: #334155; padding: 7px 10px; border: 1px solid #cbd5e1; border-radius: 6px;")
        btn_ref.clicked.connect(self._on_refresh)
        s_row.addWidget(btn_ref)
        lay.addLayout(s_row)

        # Filters Row
        f_row = QHBoxLayout()
        f_row.addWidget(QLabel("기간:"))
        self.time_grp = QButtonGroup(self)
        for i, (lbl, sec) in enumerate([("전체", None), ("오늘·어제", 2 * 86400), ("이번 주", 7 * 86400), ("이번 달", 30 * 86400)]):
            b = QPushButton(lbl)
            b.setCheckable(True)
            if i == 0:
                b.setChecked(True)
            b.setStyleSheet(self._chip_style())
            b.clicked.connect(lambda _, s=sec: self._on_time_chip(s))
            self.time_grp.addButton(b, i)
            f_row.addWidget(b)

        f_row.addSpacing(10)
        f_row.addWidget(QLabel("종류:"))
        self.type_grp = QButtonGroup(self)
        type_chips = [
            ("전체", None),
            ("한글(.hwp/.hwpx)", {".hwp", ".hwpx"}),
            ("워드(.doc/.docx)", {".doc", ".docx"}),
            ("엑셀·표", {".xls", ".xlsx", ".xlsm", ".csv"}),
            ("PDF", {".pdf"}),
            ("발표자료", {".ppt", ".pptx"}),
            ("사진", {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic"}),
        ]
        for i, (lbl, exts) in enumerate(type_chips):
            b = QPushButton(lbl)
            b.setCheckable(True)
            if i == 0:
                b.setChecked(True)
            b.setStyleSheet(self._chip_style())
            b.clicked.connect(lambda _, e=exts: self._on_type_chip(e))
            self.type_grp.addButton(b, i)
            f_row.addWidget(b)
        f_row.addStretch()
        lay.addLayout(f_row)

        # Results area
        hdr_row = QHBoxLayout()
        self.section_title = QLabel("🕒 최근 수정·다운로드한 문서")
        self.section_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #334155;")
        hdr_row.addWidget(self.section_title)
        self.status_lbl = QLabel("📂 문서를 확인하고 있어요...")
        self.status_lbl.setStyleSheet("font-size: 11px; color: #2563eb; background: #eff6ff; padding: 2px 6px; border-radius: 4px;")
        self.status_lbl.hide()
        hdr_row.addWidget(self.status_lbl)
        hdr_row.addStretch()
        lay.addLayout(hdr_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("border: 1px solid #e2e8f0; background: #f8fafc; border-radius: 6px;")
        self.card_box = QWidget()
        self.card_lay = QVBoxLayout(self.card_box)
        self.card_lay.setContentsMargins(6, 6, 6, 6)
        self.card_lay.setSpacing(6)
        self.card_lay.addStretch()
        self.scroll.setWidget(self.card_box)
        lay.addWidget(self.scroll, stretch=1)

        # Reassurance Footer
        foot = QLabel("🔒 안심하세요: 파일 찾기는 인터넷 없이 내 컴퓨터 안에서만 동작하며 외부로 전송되지 않습니다.")
        foot.setStyleSheet("font-size: 11px; color: #64748b; padding: 2px;")
        lay.addWidget(foot)

    def _chip_style(self) -> str:
        return "QPushButton { background: #f1f5f9; color: #475569; font-size: 11px; border: 1px solid #cbd5e1; border-radius: 10px; padding: 2px 8px; } QPushButton:checked { background: #2563eb; color: #fff; font-weight: bold; border-color: #2563eb; }"

    def _on_time_chip(self, sec: float | None) -> None:
        self._filter_min_mtime = (time.time() - sec) if sec else None
        self.do_search(self.search_input.text())

    def _on_type_chip(self, exts: set[str] | None) -> None:
        self._filter_exts = exts
        self.do_search(self.search_input.text())

    def _on_search(self) -> None:
        self.do_search(self.search_input.text())

    def _on_add_folder(self) -> None:
        f = QFileDialog.getExistingDirectory(self, "추가할 업무 폴더 선택")
        if f:
            folder = Path(f)
            self._remember_extra_root(folder)
            new_roots = (list(self.index.roots) if self.index and self.index.roots else []) + [folder]
            self.start_async_indexing(roots=new_roots)

    def _on_refresh(self) -> None:
        roots = self.index.roots if self.index and self.index.roots else None
        self._index_requested = True
        self.start_async_indexing(roots=roots)

    def _on_send_card(self, item: dict) -> None:
        full_text = ""
        path_str = item.get("path", "")
        if path_str:
            try:
                from .extractors import extract_document_text
                text, _doc_badge = extract_document_text(path_str)
                full_text = text or ""
            except Exception:
                full_text = ""
        if not full_text.strip():
            full_text = item.get("snippet", "")
        self.send_to_assistant.emit(item.get("name", ""), full_text, item.get("badge", ""))

    def do_search(self, query: str) -> None:
        q = query.strip()
        filts = {}
        if self._filter_exts:
            filts["exts"] = self._filter_exts
        if self._filter_min_mtime:
            filts["min_mtime"] = self._filter_min_mtime
        results = search(q, index=self.index, filters=filts, limit=30) if self.index else []

        while self.card_lay.count() > 1:
            item = self.card_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.section_title.setText(f"🕒 최근 수정·다운로드한 문서 ({len(results)}개)" if not q else f"🔍 검색 결과 ({len(results)}개 발견)")
        if not results:
            msg = QLabel("검색 결과가 없습니다. 다른 단어나 초성으로 찾아보세요.")
            msg.setStyleSheet("color: #64748b; font-size: 13px; padding: 20px;")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.card_lay.insertWidget(0, msg)
            return

        for i, item in enumerate(results):
            self.card_lay.insertWidget(i, FileCardWidget(item, send_callback=self._on_send_card, parent=self.card_box))


class FileFinderDialog(QDialog):
    """Standalone dialog window for Syeon Smart File Finder."""

    def __init__(self, index: FileIndex | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("폴더 지옥 탈출 · 션이 파일 탐정")
        self.resize(920, 680)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.finder_widget = FileFinderWidget(index=index, parent=self)
        lay.addWidget(self.finder_widget)
