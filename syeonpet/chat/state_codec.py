"""State encoding, validation, restore, and update routines."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

from optimizer_templates import filter_templates, load_templates

_PLACEHOLDER = re.compile(r"\[([^\[\]\n]{2,40})\]")
_MAX_FILL_FIELDS = 8
_ROLE_HEADING = "확정된 문의·답변 역할 (사용자 확인):"
_OTHER_AI = {
    "vireonix": ("pollinations-classic", "Pollinations Classic으로 보내기"),
    "pollinations-classic": ("vireonix", "Vireonix로 보내기"),
    "jarvisclaw": ("pollinations-classic", "Pollinations Classic으로 보내기"),
    "kilo": ("jarvisclaw", "JarvisClaw로 보내기"),
    "ovhcloud": ("jarvisclaw", "JarvisClaw로 보내기"),
}


def _placeholders(text: str) -> list[str]:
    names = []
    for match in _PLACEHOLDER.finditer(text):
        name = match.group(1).strip()
        if len(name) >= 2 and name not in names:
            names.append(name)
    return names


def _is_paste_slot(name: str) -> bool:
    return "붙여" in name


def _append_conditions(previous: str, additions: list[str]) -> str:
    sections = []
    for item in additions:
        longest = max([2] + [len(match.group()) for match in re.finditer(r"`+", item)])
        fence = "`" * (longest + 1)
        sections.append("사용자가 추가한 조건:\n" + fence + "\n" + item + "\n" + fence)
    glue = "\n\n" if sections else ""
    return previous + glue + "\n\n".join(sections)


def draft_snapshot(widget) -> tuple:
    return (widget.original, tuple(widget.revisions), tuple(widget.results), widget._shown_result,
            widget.input.toPlainText(), widget.result.toPlainText(), widget._clarifications)


def get_open_template_snapshot(widget):
    picker = getattr(widget, "_template_dialog", None)
    if picker is None:
        return None
    try:
        if not picker.isVisible():
            return None
        return (picker._current_id, picker.preview.toPlainText(),
                tuple(sorted(picker._drafts.items())), picker.search.text())
    except RuntimeError:
        return None


def export_update_state(widget) -> dict:
    if widget._busy or QApplication.activeModalWidget() is not None:
        raise RuntimeError("진행 중인 요청이나 확인창을 먼저 마쳐 주세요.")
    for name in ("_confirmation", "_library_dialog", "_settings_dialog", "_comparison_dialog",
                 "_flow_dialog"):
        dialog = getattr(widget, name, None)
        try:
            if name == "_settings_dialog" and dialog is not None and dialog.is_busy:
                raise RuntimeError("AI 설정 작업을 먼저 마쳐 주세요.")
            if dialog is not None and dialog.isVisible():
                raise RuntimeError("열려 있는 확인창이나 보관함을 먼저 닫아 주세요.")
        except RuntimeError as exc:
            if "already deleted" not in str(exc):
                raise
    template = None
    picker = getattr(widget, "_template_dialog", None)
    try:
        if picker is not None and picker.isVisible():
            template = {
                "current_id": picker._current_id, "preview": picker.preview.toPlainText(),
                "drafts": dict(picker._drafts), "search": picker.search.text(),
                "category": picker.category.currentData() or "",
                "office_only": picker.office_button.isChecked(),
            }
    except RuntimeError as exc:
        if "already deleted" not in str(exc):
            raise
    return {
        "schema": 1, "clarifications": widget._clarifications,
        "result_metadata": [widget.result_choice.itemData(i) for i in range(widget.result_choice.count())],
        "original": widget.original, "revisions": list(widget.revisions),
        "results": list(widget.results), "result_revision_counts": list(widget._result_revision_counts),
        "shown_result": widget._shown_result,
        "result_labels": [widget.result_choice.itemText(i) for i in range(widget.result_choice.count())],
        "input": widget.input.toPlainText(), "result": widget.result.toPlainText(),
        "conversation": widget.conversation.toPlainText(), "provider": widget.provider.currentData(),
        "task": widget.task.currentData(), "detail": widget.detail.currentData(),
        "history_open": widget.history_toggle.isChecked(), "status": widget.status.text(),
        "notice": widget.inline_notice.text() if not widget.inline_notice.isHidden() else None,
        "template": template,
    }


def restore_update_state(widget, state: dict) -> None:
    if widget._busy or widget.has_pending_work():
        raise RuntimeError("작성 중인 대화가 있어 복원하지 않았습니다.")
    if not isinstance(state, dict) or type(state.get("schema")) is not int or state["schema"] != 1:
        raise ValueError("복원할 대화의 형식을 확인할 수 없습니다.")
    if not isinstance(state.get("clarifications", ""), str):
        raise ValueError("복원할 추가 조건이 올바르지 않습니다.")
    metadata = state.get("result_metadata")
    if metadata is not None and (
            not isinstance(metadata, list) or len(metadata) != len(state.get("results", []))
            or any(item is not None and (
                not isinstance(item, dict) or set(item) != {"provider", "model", "elapsed"}
                or not isinstance(item["provider"], str) or not isinstance(item["model"], str)
                or type(item["elapsed"]) not in (int, float) or not 0 <= item["elapsed"] < float("inf"))
                for item in metadata)):
        raise ValueError("복원할 모델 정보가 올바르지 않습니다.")
    text_fields = ("original", "input", "result", "conversation", "provider", "task", "detail", "status")
    if not all(isinstance(state.get(key), str) for key in text_fields):
        raise ValueError("복원할 대화에 잘못된 텍스트가 있습니다.")
    for key in ("revisions", "results", "result_labels"):
        if not isinstance(state.get(key), list) or not all(isinstance(v, str) for v in state[key]):
            raise ValueError(f"복원할 대화의 {key} 데이터가 올바르지 않습니다.")
    counts = state.get("result_revision_counts")
    if not isinstance(counts, list) or not all(type(n) is int and 0 <= n <= len(state["revisions"]) for n in counts):
        raise ValueError("복원할 결과의 조건 기록이 올바르지 않습니다.")
    shown = state.get("shown_result")
    if (len(counts) != len(state["results"]) or len(state["result_labels"]) != len(state["results"])
            or type(shown) is not int or (shown != -1 and not 0 <= shown < len(counts))
            or (counts and shown == -1) or type(state.get("history_open")) is not bool
            or (state.get("notice") is not None and not isinstance(state["notice"], str))):
        raise ValueError("복원할 결과 선택 상태가 올바르지 않습니다.")
    for control, key in ((widget.provider, "provider"), (widget.task, "task"), (widget.detail, "detail")):
        if control.findData(state[key]) < 0:
            raise ValueError("복원할 정리 설정을 현재 버전에서 찾을 수 없습니다.")

    template = state.get("template")
    if template is not None:
        if (not isinstance(template, dict) or not isinstance(template.get("preview"), str)
                or not isinstance(template.get("search"), str) or not isinstance(template.get("category"), str)
                or type(template.get("office_only")) is not bool
                or (template.get("current_id") is not None and not isinstance(template["current_id"], str))
                or not isinstance(template.get("drafts"), dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in template["drafts"].items())):
            raise ValueError("복원할 템플릿 초안의 형식이 올바르지 않습니다.")
        catalog = load_templates()
        ids = {item["id"] for item in catalog}
        categories = {item["category"] for item in catalog}
        if (not set(template["drafts"]) <= ids or (template["category"] and template["category"] not in categories)
                or (template["current_id"] is not None and template["current_id"] not in {
                    item["id"] for item in filter_templates(catalog, template["search"], template["category"], template["office_only"])})):
            raise ValueError("작성하던 템플릿을 현재 버전에서 찾을 수 없습니다.")

    widget._reset_copy()
    widget.original = state["original"]
    widget._clarifications = re.sub(r"(?m)^inquiry_roles:[ 	]*", _ROLE_HEADING + "\n", state.get("clarifications", ""))
    widget.revisions = list(state["revisions"])
    widget.results = list(state["results"])
    widget._result_revision_counts = list(counts)
    widget._result_review_inputs = [None] * len(widget.results)
    widget.result_choice.blockSignals(True)
    try:
        widget.result_choice.clear()
        widget.result_choice.addItems(state["result_labels"])
        for index, item in enumerate(metadata or []):
            widget.result_choice.setItemData(index, item)
        widget.result_choice.setCurrentIndex(shown)
    finally:
        widget.result_choice.blockSignals(False)
    widget._shown_result = shown
    widget._refresh_metadata()
    widget.result.setPlainText(state["result"])
    widget.input.setPlainText(state["input"])
    widget.conversation.setPlainText(state["conversation"])
    widget.provider.setCurrentIndex(widget.provider.findData(state["provider"]))
    widget.task.setCurrentIndex(widget.task.findData(state["task"]))
    widget.detail.setCurrentIndex(widget.detail.findData(state["detail"]))
    widget.history_toggle.setChecked(state["history_open"])
    widget.status.setText(state["status"])
    if state["notice"] is not None:
        widget.inline_notice.setText(state["notice"])
        widget.inline_notice.show()
    else:
        widget.inline_notice.hide()
    widget._update_input_example()
    if template is not None:
        widget._insert_template()
        picker = widget._template_dialog
        picker.office_button.setChecked(template["office_only"])
        picker.category.setCurrentIndex(max(0, picker.category.findData(template["category"])))
        picker.search.setText(template["search"])
        picker._drafts = dict(template["drafts"])
        if template["current_id"] is not None:
            for row in range(picker.list.count()):
                item = picker.list.item(row)
                if item.data(Qt.ItemDataRole.UserRole)["id"] == template["current_id"]:
                    picker.list.setCurrentRow(row)
                    break
        picker.preview.setPlainText(template["preview"])
        picker._drafts = dict(template["drafts"])


def refresh_fill_panel(widget) -> None:
    names = tuple(_placeholders(widget.result.toPlainText()))
    if names == widget._fill_names:
        return
    widget._fill_names = names
    values = {name: field.text() for name, field in widget._fill_fields.items()}
    while widget.fill_form.rowCount():
        widget.fill_form.removeRow(0)
    widget._fill_fields = {}
    fillable = [name for name in names if not _is_paste_slot(name)]
    for name in fillable[:_MAX_FILL_FIELDS]:
        field = QLineEdit(values.get(name, ""))
        field.setAccessibleName(f"{name} 칸에 넣을 내용")
        field.setPlaceholderText("넣을 내용")
        field.returnPressed.connect(widget._apply_fill)
        label = QLabel(name)
        label.setWordWrap(True)
        label.setBuddy(field)
        widget.fill_form.addRow(label, field)
        widget._fill_fields[name] = field
    hints = []
    if len(fillable) > _MAX_FILL_FIELDS:
        hints.append(f"나머지 {len(fillable) - _MAX_FILL_FIELDS}개 칸은 결과에서 직접 고쳐 주세요.")
    if any(_is_paste_slot(name) for name in names):
        hints.append("자료를 붙여 넣는 칸은 결과 편집란의 해당 자리에 직접 붙여 넣어 주세요.")
    widget.fill_hint.setText(" ".join(hints))
    widget.fill_hint.setVisible(bool(hints))
    widget.fill_apply.setVisible(bool(widget._fill_fields))
    widget.fill_toggle.setVisible(bool(names))
    if not names:
        widget.fill_toggle.setChecked(False)
    widget._toggle_fill(widget.fill_toggle.isChecked())


def toggle_fill_panel(widget, visible: bool) -> None:
    count = len(widget._fill_names)
    widget.fill_panel.setVisible(bool(visible and count))
    widget.fill_toggle.setText(f"빈칸 채우기 접기 ({count})" if visible else f"빈칸 채우기 ({count})")


def apply_fill_to_result(widget) -> None:
    if widget._busy:
        return
    text = widget.result.toPlainText()
    filled = 0
    for name, field in widget._fill_fields.items():
        value = field.text().strip()
        pattern = re.compile(r"\[\s*" + re.escape(name) + r"\s*\]")
        if value and pattern.search(text):
            text = pattern.sub(lambda _match: value, text)
            filled += 1
    if not filled:
        widget.status.setText("넣을 내용을 적은 칸이 없어요. 칸 이름 옆에 내용을 적은 뒤 다시 눌러 주세요.")
        return
    cursor = widget.result.textCursor()
    cursor.beginEditBlock()
    cursor.select(QTextCursor.SelectionType.Document)
    cursor.insertText(text)
    cursor.endEditBlock()
    widget._save_result()
    remaining = len(_placeholders(text))
    widget.status.setText(f"빈칸 {filled}개를 채웠어요. "
                          + (f"남은 빈칸은 {remaining}개예요. " if remaining else "결과를 확인한 뒤 복사해 주세요. ")
                          + "되돌리려면 결과 편집란에서 Ctrl+Z를 누르세요.")


def finish_generation(widget, reply: dict) -> None:
    text, original, revision, provider = widget._pending
    comparison_second = widget._comparison_active and widget._comparison_index > 0
    widget._save_result()
    unslotted_draft = widget._shown_result < 0 and widget.result.toPlainText() != ""
    if unslotted_draft:
        widget.results.append(widget.result.toPlainText())
        widget._result_revision_counts.append(0)
        widget._result_review_inputs.append(None)
        widget.result_choice.addItem("직접 작성한 결과")
    widget.original = original
    widget._update_input_example()
    if revision:
        widget.revisions.append(revision)
    if not comparison_second:
        cursor = widget.conversation.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText("\n\n나: " + text + "\n션: " + ("AI로 정리했어요. 아래에서 살펴보고 수정해 주세요." if provider != "offline" else "입력과 조건을 기본 구조로 정리했어요."))
        widget.conversation.setTextCursor(cursor)
    input_changed = not comparison_second and widget.input.toPlainText() != text
    if not input_changed and not comparison_second:
        widget.input.clear()
    widget.results.append(reply["result"])
    widget._result_revision_counts.append(len(widget.revisions))
    widget._result_review_inputs.append(widget._pending_review_input if provider != "offline" else None)
    widget.result_choice.addItem(f"{len(widget.results)}번째 결과")
    widget.result_choice.setItemData(len(widget.results) - 1, reply.get("_metadata"))
    widget.result_choice.setCurrentIndex(len(widget.results) - 1)
    if widget._shown_result != len(widget.results) - 1:
        widget._select_result(len(widget.results) - 1)
    widget._refresh_metadata()
    notes = list(reply.get("notes", []))
    if provider != "offline":
        notes.extend(widget._selected_review())
    if input_changed:
        notes.append("정리 중 바뀐 입력은 그대로 남겨 두었어요. 다음 요청에 사용할 내용을 확인해 주세요.")
    if unslotted_draft:
        notes.append("미리 작성한 결과는 ‘직접 작성한 결과’에서 확인할 수 있어요.")
    widget.status.setText("\n".join(notes) if notes else "원래 의도와 조건을 확인해 주세요.")
    if not widget._comparison_active:
        widget._set_busy(False)
    widget.input.setFocus()
    widget.result_ready.emit()
    widget.task_feedback.emit("success")


def fail_generation(widget, message: str) -> None:
    widget._set_busy(False)
    status = "정리하지 못했어요: " + message + "\n입력과 이전 결과는 그대로 남아 있습니다."
    provider = widget._pending[3]
    if provider in _OTHER_AI:
        target, label = _OTHER_AI[provider]
        status += ("\n" + ("션을 깨운 뒤 " if widget._sleep_paused else "") +
                   "다시 시도하려면 ‘션에게 보내기’를 누르세요. "
                   f"‘{label}’ 버튼으로 다른 AI 서비스에 보내거나 기본 정리를 선택할 수도 있어요. "
                   "AI로 보낼 때마다 전송 여부를 확인하며, 기본 정리는 AI 재작성이 아닙니다.")
        widget._other_ai_target = target
        widget.retry_other.setText(label)
        widget.retry_other.setEnabled(not widget._sleep_paused)
        widget.retry_other.show()
    elif provider != "offline":
        status += ("\n다시 시도하려면 ‘션에게 보내기’를 누르세요. "
                   "AI로 보낼 때마다 전송 여부를 확인하며, 기본 정리는 AI 재작성이 아닙니다.")
    widget.status.setText(status)
    short_message = message if len(message) <= 180 else message[:177] + "…"
    widget._show_notice("정리하지 못했어요: " + short_message)
    widget.task_feedback.emit("failure")
