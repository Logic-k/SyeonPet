"""Comprehensive unit and integration tests for Syeon Smart File Finder."""
from __future__ import annotations

import os
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
import zlib

from PySide6.QtWidgets import QApplication

from syeonpet.finder import (
    FileFinderDialog,
    FileFinderWidget,
    FileIndex,
    build_index,
    extract_choseong,
    extract_document_text,
    is_safe_to_open,
    parse_natural_query,
    safe_open_or_reveal,
    search,
    search_everything_fallback,
)


def _make_cfb(streams: dict[str, bytes]) -> bytes:
    sec_size = 512
    s_list = list(streams.items())
    fat_sec_id = 1 + len(s_list)
    hdr = bytearray(512)
    hdr[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HH", hdr, 30, 9, 6)
    struct.pack_into("<I", hdr, 44, 1)
    struct.pack_into("<I", hdr, 48, 0)
    struct.pack_into("<I", hdr, 60, 0xFFFFFFFE)
    struct.pack_into("<I", hdr, 64, 0)
    struct.pack_into("<II", hdr, 68, 0xFFFFFFFE, 0)
    struct.pack_into("<I", hdr, 76, fat_sec_id)
    for i in range(1, 109):
        struct.pack_into("<I", hdr, 76 + i * 4, 0xFFFFFFFF)

    dir_sec = bytearray(sec_size)
    r_name = "Root Entry\x00".encode("utf-16le")
    dir_sec[:len(r_name)] = r_name
    struct.pack_into("<HB", dir_sec, 64, len(r_name), 5)
    struct.pack_into("<IQ", dir_sec, 116, 0xFFFFFFFE, 0)

    s_sectors = []
    for idx, (name, data) in enumerate(s_list):
        entry_off = (idx + 1) * 128
        n_utf = (name + "\x00").encode("utf-16le")
        dir_sec[entry_off:entry_off + len(n_utf)] = n_utf
        struct.pack_into("<HB", dir_sec, entry_off + 64, len(n_utf), 2)
        sec_id = 1 + idx
        pad_len = sec_size - (len(data) % sec_size if len(data) % sec_size else sec_size)
        s_sectors.append(data + b"\x00" * pad_len)
        struct.pack_into("<IQ", dir_sec, entry_off + 116, sec_id, len(data))

    fat = bytearray(sec_size)
    struct.pack_into("<I", fat, 0, 0xFFFFFFFE)
    for idx in range(len(s_list)):
        struct.pack_into("<I", fat, (1 + idx) * 4, 0xFFFFFFFE)
    struct.pack_into("<I", fat, fat_sec_id * 4, 0xFFFFFFFE)
    for i in range(fat_sec_id + 1, sec_size // 4):
        struct.pack_into("<I", fat, i * 4, 0xFFFFFFFF)

    return bytes(hdr) + bytes(dir_sec) + b"".join(s_sectors) + bytes(fat)


class FileFinderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="syeon-finder-test-")
        self.base = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_extract_hwpx_clean_and_bomb_guard(self):
        p = self.base / "sample.hwpx"
        xml = '<?xml version="1.0" encoding="UTF-8"?><hp:sec><hp:p><hp:t>2026 직무연수</hp:t></hp:p></hp:sec>'
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("Contents/section0.xml", xml.encode("utf-8"))
        txt, badge = extract_document_text(p)
        self.assertIn("2026 직무연수", txt)
        self.assertEqual(badge, "")

        # Corrupt magic bytes
        corrupt = self.base / "corrupt.hwpx"
        corrupt.write_bytes(b"NOT_A_ZIP_HEADER")
        c_txt, c_badge = extract_document_text(corrupt)
        self.assertEqual(c_txt, "")
        self.assertIn("보안(DRM)/특수 문서", c_badge)

    def test_extract_hwp_tag67_with_inline_control_skipping(self):
        raw_body = bytearray()
        for ch in "안녕하세요 ":
            raw_body.extend(struct.pack("<H", ord(ch)))
        # Control code 2 followed by 16 bytes of metadata
        raw_body.extend(struct.pack("<H", 2))
        raw_body.extend(b"\xFF\xFE\xDD\xCC\x11\x22\x33\x44\x55\x66\x77\x88\x99\xAA\xBB\xCC")
        for ch in "선생님":
            raw_body.extend(struct.pack("<H", ord(ch)))

        hdr_tag67 = 67 | (len(raw_body) << 20)
        section0_data = struct.pack("<I", hdr_tag67) + bytes(raw_body)

        file_hdr = bytearray(40)
        file_hdr[:32] = b"HWP Document File\x00" * 2
        struct.pack_into("<I", file_hdr, 36, 0)

        cfb_bytes = _make_cfb({
            "FileHeader": bytes(file_hdr),
            "Section0": section0_data,
        })
        p = self.base / "test_doc.hwp"
        p.write_bytes(cfb_bytes)

        txt, badge = extract_document_text(p, max_chars=1000)
        self.assertIn("안녕하세요", txt)
        self.assertIn("선생님", txt)
        self.assertNotIn("\ufffe", txt)
        self.assertEqual(badge, "")

    def test_extract_hwp_encrypted_and_distribution(self):
        enc_hdr = bytearray(40)
        struct.pack_into("<I", enc_hdr, 36, 0x02)
        p_enc = self.base / "enc.hwp"
        p_enc.write_bytes(_make_cfb({"FileHeader": bytes(enc_hdr)}))
        txt, badge = extract_document_text(p_enc)
        self.assertEqual(txt, "")
        self.assertIn("암호 문서", badge)

        dist_hdr = bytearray(40)
        struct.pack_into("<I", dist_hdr, 36, 0x04)
        p_dist = self.base / "dist.hwp"
        p_dist.write_bytes(_make_cfb({
            "FileHeader": bytes(dist_hdr),
            "PrvText": "배포용 미리보기 본문".encode("utf-16le"),
        }))
        txt, badge = extract_document_text(p_dist)
        self.assertEqual(txt, "배포용 미리보기 본문")
        self.assertIn("배포용 문서", badge)

    def test_extract_docx_and_xlsx_and_pptx_and_csv(self):
        # docx
        p_doc = self.base / "sample.docx"
        doc_xml = '<?xml version="1.0"?><w:document><w:body><w:p><w:r><w:t>워드 본문 내용</w:t></w:r></w:p></w:body></w:document>'
        with zipfile.ZipFile(p_doc, "w") as z:
            z.writestr("word/document.xml", doc_xml.encode("utf-8"))
        txt, _ = extract_document_text(p_doc)
        self.assertIn("워드 본문 내용", txt)

        # xlsx
        p_xls = self.base / "sample.xlsx"
        wb_xml = '<workbook><sheets><sheet name="명단"/></sheets></workbook>'
        ss_xml = '<sst><si><t>홍길동 엑셀자료</t></si></sst>'
        with zipfile.ZipFile(p_xls, "w") as z:
            z.writestr("xl/workbook.xml", wb_xml.encode("utf-8"))
            z.writestr("xl/sharedStrings.xml", ss_xml.encode("utf-8"))
        txt, _ = extract_document_text(p_xls)
        self.assertIn("명단", txt)
        self.assertIn("홍길동 엑셀자료", txt)

        # pptx
        p_ppt = self.base / "sample.pptx"
        ppt_xml = '<p:sld><p:sp><p:txBody><a:p><a:r><a:t>발표 슬라이드 제목</a:t></a:r></a:p></p:txBody></p:sp></p:sld>'
        with zipfile.ZipFile(p_ppt, "w") as z:
            z.writestr("ppt/slides/slide1.xml", ppt_xml.encode("utf-8"))
        txt, _ = extract_document_text(p_ppt)
        self.assertIn("발표 슬라이드 제목", txt)

        # cp949 csv
        p_csv = self.base / "sample.csv"
        p_csv.write_text("이름,부서\n김선생,교무기획", encoding="cp949")
        txt, _ = extract_document_text(p_csv)
        self.assertIn("김선생", txt)
        self.assertIn("교무기획", txt)

    def test_extract_pdf_and_html_and_null_byte_guard(self):
        # Scanned PDF without text streams
        p_pdf = self.base / "scan.pdf"
        p_pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF")
        txt, badge = extract_document_text(p_pdf)
        self.assertEqual(txt, "")
        self.assertIn("스캔/이미지 PDF", badge)

        # Encrypted PDF
        p_enc_pdf = self.base / "enc.pdf"
        p_enc_pdf.write_bytes(b"%PDF-1.4\n/Encrypt 1 0 R\n%%EOF")
        _, enc_badge = extract_document_text(p_enc_pdf)
        self.assertIn("암호 문서", enc_badge)

        # HTML with scripts and tags
        p_html = self.base / "page.html"
        p_html.write_text("<html><script>alert(1)</script><body><h1>공지사항</h1><p>내용입니다.</p></body></html>", encoding="utf-8")
        h_txt, _ = extract_document_text(p_html)
        self.assertNotIn("alert", h_txt)
        self.assertIn("공지사항", h_txt)
        self.assertIn("내용입니다", h_txt)

        # Disguised binary file
        p_bin = self.base / "fake.txt"
        p_bin.write_bytes(b"MZ\x00\x00\x00\x00\x00\x00\x00\x00\x00")
        b_txt, b_badge = extract_document_text(p_bin)
        self.assertEqual(b_txt, "")
        self.assertIn("바이너리 파일", b_badge)

    def test_onedrive_offline_attribute_skipping(self):
        p = self.base / "offline_cloud.hwpx"
        p.write_text("dummy", encoding="utf-8")
        orig_stat = os.stat(p)

        class MockStat:
            st_size = orig_stat.st_size
            st_mtime = orig_stat.st_mtime
            st_mtime_ns = orig_stat.st_mtime_ns
            st_file_attributes = 0x1000

        with patch("os.DirEntry.stat", return_value=MockStat()):
            idx = build_index(self.base)
            self.assertEqual(len(idx.records), 1)
            self.assertEqual(idx.records[0].badge, "OneDrive 온라인 전용 · 파일명 검색")
            self.assertEqual(idx.records[0].snippet, "")

    def test_choseong_and_query_parser(self):
        self.assertEqual(extract_choseong("연수안내_2026"), "ㅇㅅㅇㄴ2026")
        self.assertEqual(extract_choseong("행정실_협조요청_공문"), "ㅎㅈㅅㅎㅈㅇㅊㄱㅁ")

        q = parse_natural_query("어제 카톡으로 받은 예산 엑셀")
        self.assertIn("예산", q.keywords)
        self.assertIn("kakaotalk", q.folder_hints)
        self.assertIn(".xlsx", q.ext_boosts)
        self.assertEqual(q.time_boost, "yesterday")

        q_syn = parse_natural_query("한글 워드 시트 피피티 pdf 문서 찾아줘")
        self.assertIn(".hwpx", q_syn.ext_boosts)
        self.assertIn(".docx", q_syn.ext_boosts)
        self.assertIn(".xlsx", q_syn.ext_boosts)
        self.assertIn(".pptx", q_syn.ext_boosts)
        self.assertIn(".pdf", q_syn.ext_boosts)

    def test_duplicate_version_grouping(self):
        f1 = self.base / "계획서_초안.hwpx"
        f2 = self.base / "계획서_최종.hwpx"
        f3 = self.base / "계획서_진짜최종_수정2.hwpx"
        now = time.time()
        for p, off in [(f1, -300), (f2, -200), (f3, -100)]:
            p.write_text("dummy", encoding="utf-8")
            os.utime(p, (now + off, now + off))

        idx = build_index(self.base)
        self.assertEqual(len(idx.records), 1)
        primary = idx.records[0]
        self.assertEqual(Path(primary.path).name, "계획서_진짜최종_수정2.hwpx")
        self.assertEqual(len(primary.older_versions), 2)

    def test_safe_open_blocking_guard(self):
        self.assertTrue(is_safe_to_open("report.docx"))
        self.assertTrue(is_safe_to_open("budget.xlsx"))
        self.assertFalse(is_safe_to_open("malicious.exe"))
        self.assertFalse(is_safe_to_open("script.bat"))
        self.assertFalse(is_safe_to_open("hack.ps1"))

        exe_file = self.base / "trojan.exe"
        exe_file.write_bytes(b"MZ\x00\x00")
        self.assertFalse(safe_open_or_reveal(exe_file, action="open"))

    def test_rrn_masking_in_snippet(self):
        p = self.base / "personnel.txt"
        p.write_text("담당자 주민번호: 880101-1234567 기재금지", encoding="utf-8")
        idx = build_index(self.base)
        self.assertEqual(len(idx.records), 1)
        self.assertNotIn("1234567", idx.records[0].snippet)
        self.assertIn("880101-1******", idx.records[0].snippet)

    def test_empty_query_recent_sort(self):
        f_old = self.base / "old_doc.txt"
        f_new = self.base / "new_doc.txt"
        f_old.write_text("old", encoding="utf-8")
        f_new.write_text("new", encoding="utf-8")
        now = time.time()
        os.utime(f_old, (now - 500, now - 500))
        os.utime(f_new, (now - 10, now - 10))

        idx = build_index(self.base)
        results = search("", index=idx)
        self.assertGreaterEqual(len(results), 2)
        self.assertEqual(results[0]["name"], "new_doc.txt")
        self.assertEqual(results[1]["name"], "old_doc.txt")

    def test_finder_widget_and_dialog_ui_interaction(self):
        test_file = self.base / "2026_업무안내.docx"
        test_file.write_text("dummy content", encoding="utf-8")
        idx = build_index(self.base)

        widget = FileFinderWidget(index=idx)
        widget.do_search("")
        self.assertGreaterEqual(widget.card_lay.count(), 1)

        widget.search_input.setText("업무안내")
        widget._on_search()
        self.assertIn("1개 발견", widget.section_title.text())

        emitted = []
        widget.send_to_assistant.connect(lambda n, s, b: emitted.append((n, s, b)))
        widget._on_send_card({"name": "테스트.docx", "snippet": "요약본문", "badge": "워드"})
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0][0], "테스트.docx")
        widget.close()

        # Dialog instantiation test
        dlg = FileFinderDialog(index=idx)
        self.assertIsNotNone(dlg.finder_widget)
        dlg.close()


if __name__ == "__main__":
    unittest.main()
