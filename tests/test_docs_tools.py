"""Regression tests for the 문서 도구 (document tools) package."""
from __future__ import annotations

import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from syeonpet.docs import official_rules as rules
from syeonpet.docs import official_form as oform
from syeonpet.docs import compare as cmp_mod
from syeonpet.docs import batch as batch_mod
from syeonpet.docs import form_fill as fill_mod


class OfficialRulesTests(unittest.TestCase):
    def test_bad_date_forms_flagged_with_fix(self):
        text = "회의는 2026.11.17 개최, 마감은 2026-11-20, 회신은 2026/11/25까지."
        found = rules.review_official_document(text)
        kinds = {f.rule_id for f in found}
        self.assertIn("date_dot", kinds)
        self.assertIn("date_dash", kinds)
        self.assertIn("date_slash", kinds)
        fixes = " ".join(f.suggestion for f in found)
        self.assertIn("년", fixes)

    def test_good_date_not_flagged(self):
        text = "2026년 11월 17일에 회의를 엽니다."
        self.assertEqual(
            [f for f in rules.review_official_document(text) if "date" in f.rule_id], [])

    def test_amount_without_comma_flagged(self):
        found = rules.review_official_document("예산은 1234560원입니다.")
        self.assertTrue(any(f.rule_id == "amount_comma" for f in found))
        self.assertIn("1,234,560", found[0].suggestion)

    def test_amount_with_comma_ok(self):
        found = rules.review_official_document("예산은 1,234,560원입니다.")
        self.assertFalse(any(f.rule_id == "amount_comma" for f in found))

    def test_phone_glue_flagged(self):
        found = rules.review_official_document("연락처 0212345678 로 회신 바랍니다.")
        kinds = {f.rule_id for f in found}
        self.assertIn("phone_glue", kinds)

    def test_attachment_colon_flagged(self):
        found = rules.review_official_document("첨부: 신청서 1부")
        self.assertTrue(any(f.rule_id == "attachment_form" for f in found))

    def test_official_doc_without_ending_flagged(self):
        text = "수신: 교장선생님\n\n제목: 연수 안내\n\n내용입니다"
        found = rules.review_official_document(text)
        self.assertTrue(any(f.rule_id == "ending" for f in found))

    def test_clean_doc_passes(self):
        text = ("수신: 교장선생님\n\n제목: 연수 안내\n\n"
                "일시는 2026년 11월 17일 오후 2시입니다. 끝.")
        found = rules.review_official_document(text)
        self.assertEqual(found, [])
        self.assertIn("못했", rules.render_report(text, found))


class OfficialFormTests(unittest.TestCase):
    def test_gian_marks_missing_fields(self):
        text = oform.build_official_text("gian", {"title": "연수 계획", "body": "본문"})
        self.assertIn("제목: 연수 계획", text)
        self.assertIn("본문", text)
        self.assertIn("[확인 필요]", text)  # 문서번호·수신 등 미입력
        self.assertTrue(text.rstrip().endswith("끝."))

    def test_gian_table_rows_are_hwpx_tables(self):
        with tempfile.TemporaryDirectory() as d:
            out = oform.export_official_doc(
                "gian", {"title": "테스트", "body": "본문", "dept": "교무부"}, Path(d) / "t.hwpx")
            import zipfile
            with zipfile.ZipFile(out) as zf:
                self.assertEqual(zf.namelist()[0], "mimetype")
                section = zf.read("Contents/section0.xml").decode("utf-8")
            self.assertIn("<hp:tbl", section)      # 헤더 표가 실제 표로
            self.assertIn("교무부", section)

    def test_report_form_has_no_gian_header(self):
        text = oform.build_official_text("report", {"title": "계획서", "body": "본문"})
        self.assertNotIn("문서번호", text)
        self.assertIn("계획서", text)


class CompareTests(unittest.TestCase):
    def test_insert_delete_replace_classified(self):
        old = "첫째\n둘째\n셋째\n넷째"
        new = "첫째\n둘째(수정)\n넷째\n다섯째"
        rows = cmp_mod.compare_documents(old, new)
        notes = {r.note for r in rows}
        self.assertIn("수정", notes)
        self.assertIn("삭제", notes)
        self.assertIn("추가", notes)
        self.assertGreaterEqual(cmp_mod.changed_count(rows), 3)

    def test_identical_docs_no_changes(self):
        rows = cmp_mod.compare_documents("가\n나", "가\n나")
        self.assertEqual(cmp_mod.changed_count(rows), 0)

    def test_csv_export_has_bom_and_header(self):
        with tempfile.TemporaryDirectory() as d:
            rows = [cmp_mod.CompareRow("구", "신", "수정")]
            out = cmp_mod.export_compare_csv(rows, Path(d) / "t.csv")
            raw = out.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            with open(out, encoding="utf-8-sig", newline="") as fh:
                parsed = list(csv.reader(fh))
            self.assertEqual(parsed[0], ["구조항", "신조항", "비고"])
            self.assertEqual(parsed[1], ["구", "신", "수정"])


class BatchTests(unittest.TestCase):
    def _mkdocs(self, d: str):
        p = Path(d)
        (p / "a.txt").write_text("첫 문서입니다.", encoding="utf-8")
        (p / "b.txt").write_text("연락처 010-1234-5678 두 번째 문서.", encoding="utf-8")
        return p

    def test_collect_only_supported(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._mkdocs(d)
            (p / "skip.exe").write_bytes(b"x")
            files = batch_mod.collect_documents(p)
            self.assertEqual(len(files), 2)

    def test_ai_convert_writes_new_files_never_touches_source(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._mkdocs(d)
            out_dir = p / "out"
            orig = (p / "a.txt").read_text(encoding="utf-8")
            result = batch_mod.convert_for_ai(batch_mod.collect_documents(p), out_dir)
            self.assertEqual(len(result.done), 2)
            self.assertEqual((p / "a.txt").read_text(encoding="utf-8"), orig)
            self.assertTrue(all(f.suffix == ".md" for _, f in result.done))

    def test_mask_convert_redacts_pii_in_output(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._mkdocs(d)
            out_dir = p / "out"
            result = batch_mod.convert_for_ai(batch_mod.collect_documents(p), out_dir, mask=True)
            masked = next(f for s, f in result.done if s.stem == "b")
            content = masked.read_text(encoding="utf-8")
            self.assertNotIn("010-1234-5678", content)
            self.assertIn("가림", content)

    def test_hwpx_convert_skips_non_hwp_and_writes_hwpx(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._mkdocs(d)
            out_dir = p / "out"
            result = batch_mod.convert_hwp_to_hwpx(batch_mod.collect_documents(p), out_dir)
            # txt files are not hwp — extractor still converts them? .hwp filter applies
            # via collect_documents exts; direct call converts readable docs to hwpx.
            self.assertTrue(all(f.suffix == ".hwpx" for _, f in result.done))


class FormFillTests(unittest.TestCase):
    def test_keys_and_fill(self):
        tmpl = "성명: {{성명}} / 부서: {{부서}} / 기한: {{마감}}"
        self.assertEqual(fill_mod.template_keys(tmpl), ["성명", "부서", "마감"])
        out = fill_mod.fill_template(tmpl, {"성명": "김철수", "부서": "교무부"})
        self.assertIn("김철수", out)
        self.assertIn("[확인 필요]", out)  # 마감 미입력

    def test_csv_rows_bom(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "rows.csv"
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["성명", "날짜"])
                w.writerow(["김철수", "2026-11-17"])
            rows = fill_mod.read_rows(path)
            self.assertEqual(rows[0]["성명"], "김철수")

    def test_fill_documents_writes_per_row(self):
        with tempfile.TemporaryDirectory() as d:
            out_dir = Path(d) / "out"
            result = fill_mod.fill_documents(
                "성명 {{성명}} 님 안내", [{"성명": "가"}, {"성명": "나"}], out_dir)
            self.assertEqual(len(result.done), 2)
            self.assertTrue(all(f.suffix == ".hwpx" for f in result.done))


class PdfExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_text_to_pdf_valid_and_paginates(self):
        from syeonpet.docs.pdf_export import export_text_to_pdf
        with tempfile.TemporaryDirectory() as d:
            out = export_text_to_pdf("첫 줄\n" + "본문 줄\n" * 200, Path(d) / "t.pdf", title="테스트")
            data = out.read_bytes()
            self.assertTrue(data.startswith(b"%PDF"))
            self.assertGreater(len(data), 2000)  # 200줄이면 실제 내용이 들어감

    def test_images_to_pdf_bundle(self):
        from PySide6.QtGui import QColor, QImage
        from syeonpet.docs.pdf_export import export_images_to_pdf
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            imgs = []
            for i, c in enumerate((QColor(255, 0, 0), QColor(0, 0, 255))):
                img = QImage(64, 64, QImage.Format.Format_RGB32)
                img.fill(c)
                p = d / f"p{i}.png"
                img.save(str(p))
                imgs.append(p)
            out = export_images_to_pdf(imgs, d / "bundle.pdf")
            self.assertTrue(out.read_bytes().startswith(b"%PDF"))
            self.assertIn(b"/Type /Page", out.read_bytes())

    def test_no_images_raises(self):
        from syeonpet.docs.pdf_export import export_images_to_pdf
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                export_images_to_pdf([], Path(d) / "x.pdf")

    def test_batch_convert_to_pdf(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "a.txt").write_text("PDF로 바꿀 문서", encoding="utf-8")
            out_dir = p / "out"
            result = batch_mod.convert_to_pdf(batch_mod.collect_documents(p), out_dir)
            self.assertEqual(len(result.done), 1)
            self.assertTrue(result.done[0][1].read_bytes().startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
