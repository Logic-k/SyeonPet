"""Quality view and literal diff dialog for prompt comparisons."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QTextEdit, QVBoxLayout


def compute_quality_text(widget) -> str:
    """Compute diff and condition findings for display."""
    from prompt_quality import condition_report, literal_diff
    original = widget.original or widget.input.toPlainText()
    result = widget.result.toPlainText()
    context = "\n\n".join(filter(None, [widget._clarifications, *widget.revisions]))
    changes = literal_diff(original, result)
    findings = condition_report(original, context, result)
    change_labels = {"insert": "추가", "delete": "삭제", "replace": "변경"}
    condition_labels = {"number": "수량·숫자", "date": "날짜", "time": "시간", "weekday": "요일",
                        "quote": "인용 문구", "prohibition": "금지 조건"}
    status_labels = {"present": "문자 일치", "missing": "문자 일치 없음"}
    source_labels = {"original": "원문", "context": "추가 조건"}
    spans = []
    for change in changes:
        if change["tag"] == "equal":
            continue
        before = original[change["before_start"]:change["before_end"]]
        after = result[change["after_start"]:change["after_end"]]
        spans.append(f'{change_labels.get(change["tag"], "변경")} · 원문 {change["before_start"]}:{change["before_end"]}'
                     f' → 결과 {change["after_start"]}:{change["after_end"]}\n'
                     f'- {before}\n+ {after}')
    return ("문자 단위 비교이며 의미 보존을 보장하지 않습니다.\n\n"
            + "\n\n".join(spans) + "\n\n조건 확인:\n"
            + ("\n".join(f'{condition_labels.get(item["kind"], "기타")}: {item["literal"]} · '
                          f'{status_labels.get(item["status"], "확인 필요")} '
                          f'({source_labels.get(item["source"], "출처 확인 필요")})'
                          for item in findings) or "문자로 확인할 조건이 없습니다."))


def open_diff_dialog(widget) -> QDialog:
    """Open or re-raise the literal diff modal dialog."""
    dialog = QDialog(widget)
    dialog.setWindowTitle("원문과 결과 문자 차이")
    dialog.resize(600, 450)
    layout = QVBoxLayout(dialog)
    view = QTextEdit()
    view.setReadOnly(True)
    view.setAccessibleName("문자 차이와 조건 확인")
    layout.addWidget(view)
    widget._diff_dialog, widget._diff_view = dialog, view
    widget._refresh_quality()
    dialog.open()
    return dialog
