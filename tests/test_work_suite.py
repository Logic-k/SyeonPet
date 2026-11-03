"""Comprehensive Unit Test Suite for SyeonPet Work Assistant, Privacy Guard,
Bag Tasks/Memo, Web AI Bridge, and Document Exporter.
"""
from __future__ import annotations

import datetime
from pathlib import Path
import tempfile
import unittest

from PySide6.QtWidgets import QApplication

from syeonpet.privacy.detector import (
    contains_redaction_placeholder,
    detect_pii,
    mask_pii,
    summarize_pii,
)
from syeonpet.work.assistant_core import (
    SYSTEM,
    create_direct_draft,
    detect_work_intent,
    generate_offline_deliverable,
)
from syeonpet.work.bag_ui import BagWorkspaceWidget
from syeonpet.work.doc_export import (
    export_deliverable,
    export_to_csv,
    export_to_html,
    export_to_markdown,
    export_to_txt,
)
from syeonpet.work.tasks_memo import BagTaskManager, TaskItem
from syeonpet.work.web_ai_bridge import (
    WEB_AI_TARGETS,
    copy_and_open_web_ai,
    get_target_url,
    get_web_ai_targets,
)


class TestPIIDetector(unittest.TestCase):
    PII_TEST_INPUTS = [
        ("주민등록번호 900101-1234567 입니다", "rrn"),
        ("휴대전화 010-2345-6789 로 연락주세요", "phone"),
        ("연락처는 01012345678 입니다", "phone"),
        ("유선전화 051-234-5678 로 문의 바랍니다", "phone"),
        ("이메일 mino.kim@korea.kr 로 제출하세요", "email"),
        ("계좌번호 국민 123456-04-123456 로 입금 바랍니다", "account"),
        ("외국인등록번호 900101-5234567 확인", "foreigner_rrn"),
        ("운전면허번호 11-12-345678-90 입니다", "driver_license"),
        ("여권번호 M12345678 소지자", "passport"),
        ("카드번호 1234-5678-9012-3456 결제", "card"),
        ("주소: 부산광역시 연제구 중앙대로 1001", "address"),
        ("김철수(920304-2198765) 선생님", "rrn"),
    ]

    BENIGN_OFFICE_NUMBERS = [
        "2026년 11월 18일 연수 안내",
        "참석 인원은 40명입니다",
        "예산 총액 1,250,000원 집행 계획",
        "붙임 2 서식 참조",
        "제2026-1234호 공문",
        "오후 4시까지 제출",
        "초등학교 4곳 교사 대상",
        "3층 제1회의실에서 개최",
    ]

    def test_pii_detection_recall(self):
        for text, kind in self.PII_TEST_INPUTS:
            res = detect_pii(text)
            self.assertTrue(len(res) > 0, f"Failed to detect PII in: {text}")
            self.assertEqual(res[0]["kind"], kind)

    def test_pii_zero_false_positives_on_benign(self):
        for text in self.BENIGN_OFFICE_NUMBERS:
            res = detect_pii(text)
            self.assertEqual(len(res), 0, f"False positive detected on: {text} -> {res}")

    def test_mask_and_restore_pii(self):
        prompt = "교사 김철수(920304-2198765) 010-2345-6789 mino@korea.kr 계좌 123456-04-123456"
        restore_map: dict[str, str] = {}
        masked = mask_pii(prompt, restore_map)

        self.assertNotIn("920304-2198765", masked)
        self.assertNotIn("010-2345-6789", masked)
        self.assertNotIn("mino@korea.kr", masked)
        self.assertNotIn("123456-04-123456", masked)
        self.assertTrue(contains_redaction_placeholder(masked))

        # Test local restoration
        restored = masked
        for token, original in restore_map.items():
            restored = restored.replace(token, original)
        self.assertEqual(restored, prompt)

    def test_summarize_pii(self):
        self.assertEqual(summarize_pii("안녕하세요"), "")
        summary = summarize_pii("연락처 010-1111-2222, 이메일 test@test.com")
        self.assertIn("개인정보 후보 2건 감지됨", summary)
        self.assertIn("연락처 1건", summary)
        self.assertIn("이메일 1건", summary)


class TestAssistantCore(unittest.TestCase):
    SCENARIOS_D1 = [
        {
            "id": "scenario_01_sms",
            "prompt": "내일 오후 4시까지 방과후학교 신청 마감이라고 학부모님께 정중한 안내 문자 써줘",
            "expected_terms": ["4시", "방과후학교"],
            "unconfirmed_terms": [],
        },
        {
            "id": "scenario_02_email",
            "prompt": "행정실에 금요일 오전까지 연수 장소 냉난방 확인 부탁드린다고 공손한 메일 써줘",
            "expected_terms": ["행정실", "금요일", "오전", "냉난방"],
            "unconfirmed_terms": [],
        },
        {
            "id": "scenario_03_official_doc",
            "prompt": "초등학교 4곳 교사 대상 11월 18일 기초학력 연수 안내 공문 초안 작성 (마감 11월 10일, 장소 미정)",
            "expected_terms": ["4곳", "11월 18일", "기초학력", "11월 10일"],
            "unconfirmed_terms": ["미정"],
        },
        {
            "id": "scenario_04_meeting_notes",
            "prompt": "10월 30일 회의 메모: 연수 강사 섭외 완료, 다과 예산 미확정, 김주무관이 금요일까지 안내장 발송 예정",
            "expected_terms": ["10월 30일", "강사", "김주무관", "금요일", "안내장"],
            "unconfirmed_terms": ["미확정"],
        },
        {
            "id": "scenario_05_inquiry_reply",
            "prompt": "학교 2곳이 12월 1일 점검 일정을 문의함. 일정 미확정. 교육지원청 담당자가 학교에 보낼 회신 초안",
            "expected_terms": ["2곳", "12월 1일", "회신"],
            "unconfirmed_terms": ["미확정"],
        },
        {
            "id": "scenario_06_checklist",
            "prompt": "학부모 연수 40명 대면 진행 사전·당일·사후 체크리스트 만들어줘",
            "expected_terms": ["40명", "사전", "당일", "사후"],
            "unconfirmed_terms": [],
        },
        {
            "id": "scenario_07_excel",
            "prompt": "엑셀에서 B열이 '제출완료'인 행만 C열 금액 합계 구하는 수식 알려줘",
            "expected_terms": ["SUMIF"],
            "unconfirmed_terms": [],
        },
        {
            "id": "scenario_08_polite_rewrite",
            "prompt": "기한 내에 안 내면 처리 안 됩니다.",
            "expected_terms": ["기한"],
            "unconfirmed_terms": [],
        },
        {
            "id": "scenario_09_table_summary",
            "prompt": "지원 완료 7건, 진행 중 4건, 미확인 2건을 표로 정리하고 합계 적어줘",
            "expected_terms": ["7", "4", "2", "13"],
            "unconfirmed_terms": [],
        },
        {
            "id": "scenario_10_beginner_computer",
            "prompt": "윈도우에서 화면 일부만 캡처해서 한글 문서에 넣는 단축키 알려줘",
            "expected_terms": ["Shift", "V"],
            "unconfirmed_terms": [],
        },
    ]

    def test_system_prompt_compliance(self):
        self.assertIn("요청된 일을 직접", SYSTEM)
        self.assertTrue("초안을 작성" in SYSTEM or "직접 작성" in SYSTEM)

    def test_all_10_deliverables(self):
        for sc in self.SCENARIOS_D1:
            out = generate_offline_deliverable(sc["prompt"])
            self.assertGreater(len(out.strip()), 20)
            self.assertNotIn("원래 요청:", out)
            for term in sc["expected_terms"]:
                self.assertIn(term.lower(), out.lower(), f"Term '{term}' missing in {sc['id']}")
            for u in sc["unconfirmed_terms"]:
                self.assertIn(u, out, f"Unconfirmed term '{u}' missing in {sc['id']}")

    def test_create_direct_draft(self):
        d = create_direct_draft("기한 내에 안 내면 처리 안 됩니다.")
        self.assertIsInstance(d, dict)
        self.assertIn("result", d)
        self.assertEqual(d["mode"], "direct_work")
        self.assertEqual(d["intent"], "polite_rewrite")


class TestTasksAndMemo(unittest.TestCase):
    def test_task_item_badges(self):
        today = datetime.date.today().isoformat()
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

        t_done = TaskItem("완료된 작업", done=True)
        self.assertEqual(t_done.status_badge, "✅ 완료")

        t_overdue = TaskItem("지연된 작업", due_date=yesterday)
        self.assertEqual(t_overdue.status_badge, "🚨 기한 지남")

        t_today = TaskItem("오늘 마감 작업", due_date=today)
        self.assertEqual(t_today.status_badge, "🔥 오늘 마감")

        t_future = TaskItem("예정 작업", due_date=tomorrow)
        self.assertEqual(t_future.status_badge, "📅 예정")

    def test_bag_task_manager_crud(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "tasks.json"
            mgr = BagTaskManager(file_path)

            # Add tasks
            t1 = mgr.add_task("공문 기안하기", "2026-11-20")
            t2 = mgr.add_task("연수 다과 주문")
            self.assertEqual(len(mgr.get_tasks()), 2)

            # Toggle task
            state = mgr.toggle_task(t1.id)
            self.assertTrue(state)

            # Update task
            mgr.update_task(t2.id, title="연수 다과 및 음료 주문")
            tasks = mgr.get_tasks()
            self.assertEqual(tasks[1].title, "연수 다과 및 음료 주문")

            # Memo
            mgr.set_memo("행정실 내선: 104")
            self.assertEqual(mgr.get_memo(), "행정실 내선: 104")

            # Reload from disk
            mgr2 = BagTaskManager(file_path)
            self.assertEqual(len(mgr2.get_tasks()), 2)
            self.assertTrue(mgr2.get_tasks()[0].done)
            self.assertEqual(mgr2.get_memo(), "행정실 내선: 104")

            # Delete
            mgr2.delete_task(t1.id)
            self.assertEqual(len(mgr2.get_tasks()), 1)


class TestBagUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_bag_widget_operations_and_signals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "ui_tasks.json"
            widget = BagWorkspaceWidget(file_path)

            # Test adding task through UI
            widget.txt_task.setText("학부모 알림 발송")
            widget._on_add_task_clicked()
            self.assertEqual(len(widget.manager.get_tasks()), 1)

            # Test task completion signal
            received_signals: list[str] = []
            widget.task_completed.connect(received_signals.append)

            task_id = widget.manager.get_tasks()[0].id
            widget._on_task_toggled(task_id, "학부모 알림 발송", True)
            self.assertEqual(received_signals, ["학부모 알림 발송"])

            # Test timer control
            widget.start_focus_timer(25, "집중")
            self.assertEqual(widget.timer_seconds_left, 1500)
            widget._on_timer_tick()
            self.assertEqual(widget.timer_seconds_left, 1499)
            widget.stop_focus_timer()
            self.assertEqual(widget.timer_seconds_left, 0)


class TestWebAIBridge(unittest.TestCase):
    def test_web_ai_bridge(self):
        targets = get_web_ai_targets()
        self.assertEqual(len(targets), 4)
        ids = [t[0] for t in targets]
        self.assertIn("chatgpt", ids)
        self.assertIn("wrtn", ids)
        self.assertIn("gemini", ids)
        self.assertIn("claude", ids)

        url = get_target_url("wrtn")
        self.assertEqual(url, "https://wrtn.ai/")

        guide = copy_and_open_web_ai("테스트 문구", "chatgpt", open_browser=False)
        self.assertIn("클립보드에 복사했어요!", guide)


class TestDocExport(unittest.TestCase):
    def test_export_all_formats(self):
        sample = """[회의 결과 정리]
| 구분 | 내용 |
| 강사 | 섭외 완료 |
| 예산 | 미확정 |
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            p_txt = export_deliverable(sample, base / "doc.txt")
            self.assertTrue(p_txt.exists())
            self.assertIn("회의 결과 정리", p_txt.read_text(encoding="utf-8"))

            p_md = export_deliverable(sample, base / "doc.md")
            self.assertTrue(p_md.exists())

            p_html = export_deliverable(sample, base / "doc.html", title="테스트 산출물")
            self.assertTrue(p_html.exists())
            html_text = p_html.read_text(encoding="utf-8")
            self.assertIn("<table", html_text)
            self.assertIn("테스트 산출물", html_text)

            p_csv = export_deliverable(sample, base / "doc.csv")
            self.assertTrue(p_csv.exists())
            csv_bytes = p_csv.read_bytes()
            # Must start with UTF-8 BOM
            self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))

    def test_export_hwpx_is_valid_and_roundtrips(self):
        """HWPX export: mimetype first/stored, valid XML, finder can re-read it."""
        import zipfile
        import xml.etree.ElementTree as ET
        from syeonpet.finder import extract_document_text

        sample = "제목 줄입니다.\n\n| 항목 | 담당 |\n|:---|:---|\n| 안내장 | 김민오 |\n\n끝줄 & 특수<문자>"
        with tempfile.TemporaryDirectory() as tmpdir:
            out = export_deliverable(sample, Path(tmpdir) / "out.hwpx", title="테스트 문서")
            self.assertTrue(out.exists())
            with zipfile.ZipFile(out) as zf:
                names = zf.namelist()
                self.assertEqual(names[0], "mimetype")
                self.assertEqual(zf.read("mimetype"), b"application/hwp+zip")
                self.assertEqual(zf.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
                for n in names:
                    if n.endswith((".xml", ".hpf", ".rdf")):
                        ET.fromstring(zf.read(n))
                self.assertIn("Contents/section0.xml", names)
                self.assertIn("Contents/header.xml", names)
                self.assertIn("Preview/PrvText.txt", names)
            text, _ = extract_document_text(out)
            self.assertIn("제목 줄입니다", text)
            self.assertIn("안내장", text)
            self.assertIn("김민오", text)
            self.assertIn("끝줄", text)


if __name__ == "__main__":
    unittest.main()
