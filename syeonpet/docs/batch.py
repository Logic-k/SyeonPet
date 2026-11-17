"""폴더째 문서 일괄 처리 — AI용 텍스트 변환, 개인정보 가림본, hwp→hwpx.

모두 로컬 처리다. 원본은 절대 건드리지 않고 항상 새 파일을 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from syeonpet.finder.extractors import extract_document_text
from syeonpet.privacy.detector import mask_pii

SUPPORTED_EXTS = {".txt", ".md", ".hwp", ".hwpx", ".docx", ".xlsx", ".csv", ".pptx", ".pdf"}


@dataclass
class BatchResult:
    done: list[tuple[Path, Path]] = field(default_factory=list)   # (src, out)
    failed: list[tuple[Path, str]] = field(default_factory=list)  # (src, reason)

    @property
    def summary(self) -> str:
        return f"{len(self.done)}개 완료" + (
            f" · {len(self.failed)}개 실패" if self.failed else "")


def collect_documents(folder: str | Path, exts: set[str] | None = None) -> list[Path]:
    """Document files under ``folder`` (non-recursive by default)."""
    folder = Path(folder)
    wanted = exts or SUPPORTED_EXTS
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in wanted)


def _out_name(src: Path, out_dir: Path, suffix: str, ext: str) -> Path:
    name = out_dir / f"{src.stem}{suffix}{ext}"
    n = 1
    while name.exists():
        name = out_dir / f"{src.stem}{suffix}({n}){ext}"
        n += 1
    return name


def convert_for_ai(src_files: list[Path], out_dir: Path, ext: str = ".md",
                   mask: bool = False) -> BatchResult:
    """Extract each document's text into a new .md/.txt file.

    ``mask=True`` additionally redacts personal information in the output —
    the source file is never modified.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    result = BatchResult()
    for src in src_files:
        try:
            text, badge = extract_document_text(src)
        except Exception as exc:  # noqa: BLE001
            result.failed.append((src, str(exc)))
            continue
        if not text.strip():
            result.failed.append((src, badge or "본문을 읽지 못했습니다"))
            continue
        if mask:
            text = mask_pii(text)
        suffix = "_AI용_가림" if mask else "_AI용"
        out = _out_name(src, out_dir, suffix, ext)
        header = (f"# {src.name}\n\n"
                  + ("(개인정보 가림본 — 션이 연락처·주민등록번호 등을 [가림] 처리했습니다)\n\n" if mask else "")
                  + text)
        out.write_text(header, encoding="utf-8")
        result.done.append((src, out))
    return result


def convert_hwp_to_hwpx(src_files: list[Path], out_dir: Path) -> BatchResult:
    """Rewrite .hwp text content as real .hwpx packages (text-level conversion).

    Layout details from the binary format are not preserved — this is a
    content migration, so the note is written into the output's first line.
    """
    from syeonpet.work.doc_export import export_to_hwpx
    out_dir.mkdir(parents=True, exist_ok=True)
    result = BatchResult()
    for src in src_files:
        try:
            if src.suffix.lower() == ".hwpx":
                result.failed.append((src, "이미 hwpx 파일입니다"))
                continue
            text, badge = extract_document_text(src)
        except Exception as exc:  # noqa: BLE001
            result.failed.append((src, str(exc)))
            continue
        if not text.strip():
            result.failed.append((src, badge or "본문을 읽지 못했습니다"))
            continue
        out = _out_name(src, out_dir, "", ".hwpx")
        try:
            export_to_hwpx(text, out, title=src.stem)
            result.done.append((src, out))
        except Exception as exc:  # noqa: BLE001
            result.failed.append((src, str(exc)))
    return result
