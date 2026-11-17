"""두 문서 비교 — 신구대조표(구조항/신조항/비고)를 만든다.

문단(비어 있지 않은 줄) 단위로 맞춰서 보기 좋은 대조표를 만들고,
CSV(엑셀에서 바로 열림)·HWPX 표로 내보낸다.
"""
from __future__ import annotations

import csv
import difflib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CompareRow:
    old: str
    new: str
    note: str  # 추가 / 삭제 / 수정 / (빈칸 = 같음)


def _units(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def compare_documents(old_text: str, new_text: str) -> list[CompareRow]:
    """Align two documents paragraph-by-paragraph into old/new rows."""
    old, new = _units(old_text), _units(new_text)
    rows: list[CompareRow] = []
    sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, a0, a1, b0, b1 in sm.get_opcodes():
        if tag == "equal":
            rows.extend(CompareRow(o, n, "") for o, n in zip(old[a0:a1], new[b0:b1]))
        elif tag == "replace":
            olds, news = old[a0:a1], new[b0:b1]
            paired = min(len(olds), len(news))
            for k in range(paired):
                rows.append(CompareRow(olds[k], news[k], "수정"))
            rows.extend(CompareRow(o, "", "삭제") for o in olds[paired:])
            rows.extend(CompareRow("", n, "추가") for n in news[paired:])
        elif tag == "delete":
            rows.extend(CompareRow(o, "", "삭제") for o in old[a0:a1])
        elif tag == "insert":
            rows.extend(CompareRow("", n, "추가") for n in new[b0:b1])
    return rows


def changed_count(rows: list[CompareRow]) -> int:
    return sum(1 for r in rows if r.note)


def render_table_text(rows: list[CompareRow]) -> str:
    """신구대조표를 미리보기용 텍스트로 만든다."""
    out = ["[신구대조표]", "", "| 구 조 항 | 신 조 항 | 비 고 |", "|---|---|---|"]
    for r in rows:
        old = r.old if r.old else "(없음)"
        new = r.new if r.new else "(없음)"
        out.append(f"| {old} | {new} | {r.note} |")
    out += ["", f"변경 {changed_count(rows)}곳 / 전체 {len(rows)}행"]
    return "\n".join(out)


def export_compare_csv(rows: list[CompareRow], filepath: str | Path) -> Path:
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel opens Korean correctly.
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["구조항", "신조항", "비고"])
        for r in rows:
            writer.writerow([r.old, r.new, r.note])
    return path


def export_compare_hwpx(rows: list[CompareRow], filepath: str | Path) -> Path:
    """Save the table as a real .hwpx with a bordered three-column table."""
    from syeonpet.work.doc_export import export_to_hwpx
    lines = ["[신구대조표]", "", "| 구 조 항 | 신 조 항 | 비 고 |"]
    for r in rows:
        old = r.old.replace("|", "⁄") if r.old else "(없음)"
        new = r.new.replace("|", "⁄") if r.new else "(없음)"
        lines.append(f"| {old} | {new} | {r.note} |")
    lines += ["", f"변경 {changed_count(rows)}곳 / 전체 {len(rows)}행"]
    return export_to_hwpx("\n".join(lines), filepath, title="신구대조표")
