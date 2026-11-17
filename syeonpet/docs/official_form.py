"""공문서 양식 — 기안문·보고서 본문을 받아 행정 양식 모양의 텍스트/파일로 만든다.

양식은 행정업무의 운영 및 혁신에 관한 규정 별표 서식을 단순화한 것이다.
기관마다 세부 양식이 다르므로, 사용자가 기관 양식에 맞게 고칠 수 있게
텍스트 구조로 먼저 보여 주고 파일은 그 결과를 그대로 저장한다.
"""
from __future__ import annotations

from pathlib import Path

_NEED = "[확인 필요]"


def _v(fields: dict, key: str) -> str:
    val = str(fields.get(key, "") or "").strip()
    return val if val else _NEED


def build_gian_lines(fields: dict) -> list[str]:
    """기안문 본문을 행정 기안문 형태의 줄 목록으로 조립한다.

    ``fields``: doc_no, date, dept, drafter, recipient, cc, title, body, attach.
    비어 있는 값은 ``[확인 필요]``로 표시한다 — 지어내지 않는다.
    """
    lines = [
        f"|문서번호|{_v(fields, 'doc_no')}|",
        f"|시행일자|{_v(fields, 'date')}|",
        f"|기안부서|{_v(fields, 'dept')}|",
        f"|기안자|{_v(fields, 'drafter')}|",
        "",
        f"수신: {_v(fields, 'recipient')}",
    ]
    cc = str(fields.get("cc", "") or "").strip()
    if cc:
        lines.append(f"참조: {cc}")
    lines += [
        "",
        f"제목: {_v(fields, 'title')}",
        "",
        str(fields.get("body", "") or "").strip() or _NEED,
        "",
        "끝.",
    ]
    attach = str(fields.get("attach", "") or "").strip()
    if attach:
        lines += ["", f"붙임  {attach}"]
    return lines


def build_report_lines(fields: dict) -> list[str]:
    """보고서·계획서 형태 (기안 상단부 없이 제목 중심)."""
    title = _v(fields, "title")
    dept = _v(fields, "dept")
    date = _v(fields, "date")
    body = str(fields.get("body", "") or "").strip() or _NEED
    lines = [
        title,
        "",
        f"작성부서: {dept}    작성일자: {date}",
        "",
        body,
        "",
        "끝.",
    ]
    attach = str(fields.get("attach", "") or "").strip()
    if attach:
        lines += ["", f"붙임  {attach}"]
    return lines


_BUILDERS = {
    "gian": build_gian_lines,
    "report": build_report_lines,
}

FORM_LABELS = {
    "gian": "기안문",
    "report": "보고서·계획서",
}


def build_official_text(kind: str, fields: dict) -> str:
    """Assemble the document text for the given form kind."""
    builder = _BUILDERS.get(kind, build_gian_lines)
    return "\n".join(builder(fields))


def export_official_doc(kind: str, fields: dict, filepath: str | Path) -> Path:
    """Render the form and save it (.hwpx via doc_export, or .txt/.md)."""
    from syeonpet.work.doc_export import export_deliverable
    text = build_official_text(kind, fields)
    title = str(fields.get("title", "") or FORM_LABELS.get(kind, "공문서")).strip()
    return export_deliverable(text, filepath, title=title)
