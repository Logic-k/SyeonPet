"""양식 채우기 — {{이름}} 빈칸이 있는 문서 양식에 행 데이터를 넣어 문서를 만든다.

양식 한 장 + CSV·엑셀 표(첫 행이 칸 이름)로 여러 문서를 한 번에 만든다.
결과는 항상 새 파일이며 원본 양식은 건드리지 않는다.
"""
from __future__ import annotations

import csv
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def template_keys(template: str) -> list[str]:
    """Unique {{칸}} names in the template, in order of appearance."""
    seen: list[str] = []
    for m in _PLACEHOLDER.finditer(template):
        key = m.group(1).strip()
        if key not in seen:
            seen.append(key)
    return seen


def fill_template(template: str, values: dict[str, str]) -> str:
    """Replace {{칸}} with values; unknown keys become [확인 필요]."""
    def repl(m: re.Match) -> str:
        key = m.group(1).strip()
        val = str(values.get(key, "") or "").strip()
        return val if val else "[확인 필요]"
    return _PLACEHOLDER.sub(repl, template)


def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    """CSV의 첫 행을 칸 이름으로 쓰는 행 dict 목록. utf-8-sig/CP949 모두 허용."""
    for enc in ("utf-8-sig", "cp949"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                rows = list(csv.DictReader(fh))
            return [dict(r) for r in rows]
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError("CSV 파일을 읽지 못했습니다 (utf-8 또는 CP949 인코딩만 지원합니다).")


def _col_index(cell_ref: str) -> int:
    col = 0
    for ch in cell_ref:
        if ch.isalpha():
            col = col * 26 + (ord(ch.upper()) - ord("A") + 1)
        else:
            break
    return col - 1


def read_xlsx_rows(path: str | Path) -> list[dict[str, str]]:
    """첫 시트의 첫 행을 칸 이름으로 쓰는 행 dict 목록 (서식 없이 값만 읽음)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter() if t.tag.endswith("}t")))
        sheets = sorted(n for n in names
                        if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        if not sheets:
            return []
        root = ET.fromstring(zf.read(sheets[0]))
        grid: list[list[str]] = []
        for row in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
            cells: dict[int, str] = {}
            for c in row:
                if not c.tag.endswith("}c"):
                    continue
                ref = c.attrib.get("r", "A1")
                t = c.attrib.get("t")
                v = c.find("m:v", ns)
                is_el = c.find("m:is", ns)
                text = ""
                if t == "s" and v is not None and v.text is not None:
                    idx = int(v.text)
                    text = shared[idx] if 0 <= idx < len(shared) else ""
                elif t == "inlineStr" and is_el is not None:
                    text = "".join(x.text or "" for x in is_el.iter() if x.tag.endswith("}t"))
                elif v is not None and v.text is not None:
                    text = v.text
                cells[_col_index(ref)] = text
            if cells:
                width = max(cells) + 1
                grid.append([cells.get(i, "") for i in range(width)])
    if not grid:
        return []
    header = grid[0]
    rows: list[dict[str, str]] = []
    for line in grid[1:]:
        if not any(str(v).strip() for v in line):
            continue
        rows.append({header[i] or f"열{i + 1}": (line[i] if i < len(line) else "")
                     for i in range(len(header))})
    return rows


def read_rows(path: str | Path) -> list[dict[str, str]]:
    ext = Path(path).suffix.lower()
    if ext == ".csv":
        return read_csv_rows(path)
    if ext in (".xlsx", ".xlsm"):
        return read_xlsx_rows(path)
    raise ValueError(f"행 데이터는 .csv/.xlsx만 읽을 수 있습니다 ({ext})")


@dataclass
class FillResult:
    done: list[Path] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return f"{len(self.done)}개 완료" + (
            f" · {len(self.failed)}개 실패" if self.failed else "")


def fill_documents(template: str, rows: list[dict[str, str]], out_dir: Path,
                   name_key: str | None = None, kind: str | None = None) -> FillResult:
    """One document per row.

    ``kind`` selects the 공문서 wrapper (``"gian"``/``"report"``); ``None``
    writes the raw filled text. Output names come from ``name_key`` when set,
    else the row number.
    """
    from syeonpet.docs.official_form import build_official_text
    from syeonpet.work.doc_export import export_deliverable
    out_dir.mkdir(parents=True, exist_ok=True)
    result = FillResult()
    for i, row in enumerate(rows, 1):
        try:
            text = fill_template(template, row)
            if kind:
                text = build_official_text(kind, {
                    "title": row.get("제목", "문서"), "body": text,
                    "dept": row.get("부서", ""), "date": row.get("날짜", ""),
                    "recipient": row.get("수신", ""),
                })
            stem = str(row.get(name_key, "") or f"{i:03d}").strip() or f"{i:03d}"
            stem = re.sub(r'[\\/:*?"<>|]', "_", stem)[:60]
            out = out_dir / f"{stem}.hwpx"
            export_deliverable(text, out, title=stem)
            result.done.append(out)
        except Exception as exc:  # noqa: BLE001
            result.failed.append((i, str(exc)))
    return result
