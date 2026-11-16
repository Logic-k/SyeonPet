"""External AI handoff links and local preference persistence."""
from __future__ import annotations

import json
from pathlib import Path
import uuid

from PySide6.QtCore import QStandardPaths, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from optimizer_core import utf16_length
from ..paths import atomic_write_bytes
from .rules import _MAX_ANSWER, _facade_attr

_MAX_PREF_ENTRIES = 100
_PREF_MIN_USES = 2
_MAX_PROFILE_CARDS = 10

EXTERNAL_AI = [
    ("chatgpt", "ChatGPT", "https://chatgpt.com/"),
    ("wrtn", "뤼튼 (Wrtn)", "https://wrtn.ai/"),
    ("claude", "Claude", "https://claude.ai/"),
    ("gemini", "Gemini", "https://gemini.google.com/"),
    ("grok", "Grok", "https://grok.com/"),
    ("perplexity", "Perplexity", "https://www.perplexity.ai/"),
]


def copy_to_clipboard(text: str) -> bool:
    try:
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        return clipboard.text().replace("\r\n", "\n") == text.replace("\r\n", "\n")
    except (RuntimeError, AttributeError):
        return False


def open_external(key: str, text: str) -> bool:
    """Always copy first; opening the site is the best-effort second step."""
    url = {service[0]: service[2] for service in EXTERNAL_AI}[key]
    copied = copy_to_clipboard(text)
    _facade_attr("QDesktopServices", QDesktopServices).openUrl(QUrl(url))
    return copied


# ---------------------------------------------------------------------------
# Local preference memory (repeated answers prefilled; local storage only)
# ---------------------------------------------------------------------------

def _pref_path() -> Path:
    root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
    if not root:
        raise OSError("사용자 저장 폴더를 찾을 수 없습니다.")
    return Path(root) / "SyeonPet" / "prompt-assistant" / "flow-preferences.json"


def _atomic_write(path: Path, payload: bytes) -> None:
    atomic_write_bytes(path, payload, prefix=".flow-pref-")


class PreferenceStore:
    """Locally remembered answers and reusable user context cards."""

    def __init__(self, path: Path | None = None):
        self.path = path or _facade_attr("_pref_path", _pref_path)()

    def _read_raw(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return {}
        if not isinstance(data, dict) or data.get("version") != 1:
            return {}
        return data

    def load(self) -> dict:
        data = self._read_raw()
        if not isinstance(data.get("answers"), dict):
            return {}
        result = {}
        try:
            for type_id, entries in data["answers"].items():
                if not isinstance(entries, dict):
                    continue
                result[type_id] = {str(k): {"text": str(v.get("text", "")), "uses": int(v.get("uses", 0))}
                                   for k, v in entries.items()
                                   if isinstance(v, dict) and isinstance(v.get("text"), str)}
        except (TypeError, ValueError):
            return {}
        return result

    def load_profile_cards(self) -> list[dict]:
        """Load saved reusable persona/org cards (local only)."""
        raw = self._read_raw().get("profile_cards")
        if not isinstance(raw, list):
            return []
        cards: list[dict] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            if not name or utf16_length(name) > 80:
                continue
            cards.append({
                "id": str(entry.get("id") or uuid.uuid4()),
                "name": name,
                "org": str(entry.get("org", "")).strip()[:200],
                "audience": str(entry.get("audience", "")).strip()[:120],
                "sender": str(entry.get("sender", "")).strip()[:120],
                "tone": str(entry.get("tone", "")).strip()[:120],
            })
            if len(cards) >= _MAX_PROFILE_CARDS:
                break
        return cards

    def _write_document(self, answers: dict, profile_cards: list[dict] | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict = {"version": 1, "answers": answers}
        if profile_cards:
            payload["profile_cards"] = profile_cards
        _atomic_write(self.path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))

    def _save(self, data: dict) -> None:
        self._write_document(data, self.load_profile_cards())

    def save_profile_card(self, card: dict) -> dict:
        """Insert or update a reusable profile card safely."""
        name = str(card.get("name", "")).strip()
        if not name or utf16_length(name) > 80:
            raise ValueError("카드 이름은 1~80자 이내로 입력해 주세요.")
        normalized = {
            "id": str(card.get("id") or uuid.uuid4()),
            "name": name,
            "org": str(card.get("org", "")).strip()[:200],
            "audience": str(card.get("audience", "")).strip()[:120],
            "sender": str(card.get("sender", "")).strip()[:120],
            "tone": str(card.get("tone", "")).strip()[:120],
        }
        cards = [c for c in self.load_profile_cards() if c["id"] != normalized["id"]]
        if len(cards) >= _MAX_PROFILE_CARDS:
            raise ValueError(f"내 정보 카드는 최대 {_MAX_PROFILE_CARDS}장까지 저장할 수 있어요.")
        cards.insert(0, normalized)
        self._write_document(self.load(), cards)
        return normalized

    def delete_profile_card(self, card_id: str) -> bool:
        cards = self.load_profile_cards()
        remaining = [c for c in cards if c["id"] != card_id]
        if len(remaining) == len(cards):
            return False
        self._write_document(self.load(), remaining)
        return True

    def record(self, type_id: str, question_id: str, text: str) -> None:
        text = text.strip()
        # UTF-16 units, matching the answer-length limit used elsewhere.
        if not text or utf16_length(text) > _MAX_ANSWER:
            return
        data = self.load()
        entries = data.setdefault(type_id, {})
        entry = entries.get(question_id)
        if entry and entry["text"] == text:
            entry["uses"] += 1
        else:
            entries[question_id] = {"text": text, "uses": 1}
        total = sum(len(v) for v in data.values())
        if total > _MAX_PREF_ENTRIES:
            # Drop the least-used entries until within the bound.
            flat = sorted(((t, k, v["uses"]) for t, d in data.items() for k, v in d.items()),
                          key=lambda item: item[2])
            for t, k, _u in flat[: max(0, total - _MAX_PREF_ENTRIES)]:
                data[t].pop(k, None)
        self._save(data)

    def prefilled(self, type_id: str) -> dict:
        """Answers repeated at least _PREF_MIN_USES times, for this type."""
        return {qid: entry["text"]
                for qid, entry in self.load().get(type_id, {}).items()
                if entry["uses"] >= _facade_attr("_PREF_MIN_USES", _PREF_MIN_USES)}

    def clear(self, type_id: str | None = None) -> None:
        if type_id is None:
            self._save({})
            return
        data = self.load()
        data.pop(type_id, None)
        self._save(data)

