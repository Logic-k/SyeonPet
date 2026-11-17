"""문서 → PDF 변환 — Qt 내장 QPdfWriter만 사용 (외부 프로그램·의존성 없음).

- 텍스트 본문: A4에 쪽 번호 없이 문단 흐름대로 인쇄
- 이미지: 파일마다 한 쪽씩 맞춰 넣고 여러 장이면 하나의 PDF로 묶음
모두 새 파일을 만들 뿐 원본은 건드리지 않는다.
"""
from __future__ import annotations

from pathlib import Path

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}


def export_text_to_pdf(text: str, filepath: str | Path, title: str = "") -> Path:
    """Write plain text to an A4 PDF, paginating automatically."""
    from PySide6.QtCore import QSizeF
    from PySide6.QtGui import QFont, QPageSize, QPdfWriter, QTextDocument
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(96)
    writer.setCreator("SyeonPet")
    if title:
        writer.setTitle(title)

    doc = QTextDocument()
    doc.setDefaultFont(QFont("맑은 고딕", 11))
    doc.setPlainText(text)
    # 페이지 크기를 본문 여백만큼 줄여 여백을 흉내낸다.
    w, h = float(writer.width()), float(writer.height())
    margin = w * 0.06
    doc.setPageSize(QSizeF(w - 2 * margin, h - 2 * margin))
    doc.print_(writer)
    del doc, writer  # 파일 핸들을 즉시 닫는다(Windows 잠금 방지)
    return path


def export_images_to_pdf(image_paths: list, filepath: str | Path) -> Path:
    """One page per image, scaled to fit A4, all in a single PDF."""
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QImage, QPainter, QPageSize, QPdfWriter
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(150)
    writer.setCreator("SyeonPet")

    painter = QPainter(writer)
    first = True
    written = 0
    for src in image_paths:
        img = QImage(str(src))
        if img.isNull():
            continue
        if not first:
            writer.newPage()
        first = False
        target = QRect(0, 0, writer.width(), writer.height())
        scaled = img.scaled(target.size(), Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        x = target.x() + (target.width() - scaled.width()) // 2
        y = target.y() + (target.height() - scaled.height()) // 2
        painter.drawImage(x, y, scaled)
        written += 1
    painter.end()
    del painter, writer  # 파일 핸들을 즉시 닫는다(Windows 잠금 방지)
    if not written:
        path.unlink(missing_ok=True)
        raise ValueError("읽을 수 있는 이미지가 없어 PDF를 만들지 못했습니다.")
    return path


def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_EXTS
