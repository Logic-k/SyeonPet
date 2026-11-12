"""Guided 'diagnose -> ask-back -> completed prompt' flow tests.

Covers the acceptance cases AC01-AC05 and the A/B/C invariants of the
aitong workflow contract: diagnosis, missing-condition summary, re-ask ban,
one-screen batch questions, tuner controls, composite sequencing, fallback
handling, section structure, honesty rules, save/copy/handoff and restart.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

import optimizer_flow as flow
from optimizer_flow import (FlowDialog, PreferenceStore, build_prompt, build_questions,
                            classify, detect_segments, diagnose, extract_conditions,
                            is_meaningless, render_prompt_html, tuner_axes)


WEEKLY = "이번 주에 한 일을 정리해서 과장님께 낼 부서 주간보고 써줘"
WEEKLY_FULL = "팀장님께 보낼 격식체 주간보고 써줘. 한 일: 예산 집행 정리·민원 3건 처리. 분량: A4 반 장"
COMPLAINT = "민원인이 주민등록 등본 발급 지연에 항의했는데 회신 공문 초안 써줘"
COMPOSITE = "이번 주 주간보고 쓰고, 다음 달 행사 안내문도 만들어줘"


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class _AutoAnswerBox:
    """Stands in for QMessageBox so consent prompts can be answered in tests."""
    answer = QMessageBox.StandardButton.No
    Icon = QMessageBox.Icon
    StandardButton = QMessageBox.StandardButton

    def __init__(self, *args, **kwargs):
        self.finished = _FakeSignal()

    def setDefaultButton(self, _button):
        pass

    def open(self):
        for callback in self.finished.callbacks:
            callback(self.answer)


class _Clipboard:
    def __init__(self):
        self._text = ""

    def setText(self, text):
        self._text = text

    def text(self):
        return self._text


def _clipboard_patch():
    clipboard = _Clipboard()
    return clipboard, patch.object(QApplication, "clipboard", return_value=clipboard)


class ClassificationTests(unittest.TestCase):
    """Type table and subtype rule (AC05 invariants)."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_education_types_classify(self):
        cases = {
            "주간보고 형식으로 이번 주 업무 정리해줘": "weekly_report",
            "보고서 써줘": "general_report",
            "우리 학교 방과후학교 프로그램 안내 가정통신문 만들어줘": "notice_letter",
            "학부모가 상담을 신청했는데 답변 문자 써줘": "counsel_reply",
            "오늘 교무회의 내용을 회의록으로 정리해줘": "minutes",
            "교육청에서 온 공문에 우리 학교 답변 초안 써줘": "official_reply",
            COMPLAINT: "complaint_reply",
            "다음 달 주민 참여 행사 안내문 만들어줘": "event_notice",
            "그거 좀 해줘": "generic",
            "ㅁㄴㅇㄹㅁㄴㅇㄹ": "generic",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(classify(text), expected)

    def test_weekly_signal_is_not_hijacked_by_minwon_word(self):
        self.assertEqual(classify(WEEKLY_FULL), "weekly_report")
        self.assertEqual(classify(WEEKLY), "weekly_report")

    def test_report_ab_subtype_rule(self):
        self.assertEqual(classify("주간보고 써줘"), "weekly_report")
        self.assertEqual(classify("주보 올려줘"), "weekly_report")
        self.assertEqual(classify("월간보고서 작성해줘"), "general_report")
        self.assertEqual(classify("보고서 써줘"), "general_report")

    def test_english_weekly_report_uses_general_type(self):
        # Decision #2 r1: only '주간보고'·'주보' signals pick subtype B —
        # English 'weekly report' carries none (original I12 observed A형).
        self.assertEqual(classify("write a weekly report for my manager"), "general_report")
        self.assertEqual(classify("write a weekly report"), "general_report")


class DiagnosisTests(unittest.TestCase):
    """S0.5/S1 diagnosis: verdict, percent, summary (F01, A10, A12)."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_diagnosis_shape(self):
        result = diagnose(WEEKLY)
        self.assertEqual(result["type"], "weekly_report")
        self.assertTrue(0 < result["percent"] <= 95)
        self.assertTrue(result["verdict"].strip())
        self.assertEqual(result["missing_titles"], [q["title"] for q in result["questions"]])

    def test_percent_grows_with_stated_conditions(self):
        low = diagnose("주간보고 써줘")["percent"]
        high = diagnose(WEEKLY_FULL)["percent"]
        self.assertLess(low, high)

    def test_reask_ban_excludes_stated_conditions(self):
        # Audience stated -> no "who receives the report" question (AC01).
        titles = [q["title"] for q in diagnose(WEEKLY)["questions"]]
        self.assertFalse(any("받는 사람" in title for title in titles))
        # Without the audience the audience question appears.
        titles = [q["title"] for q in diagnose("주간보고 써줘")["questions"]]
        self.assertTrue(any("받는 사람" in title for title in titles))

    def test_extraction_recognizes_signals(self):
        found = extract_conditions(WEEKLY_FULL)
        self.assertEqual(found["explicit_audience"], "팀장님")
        self.assertEqual(found["tone"], "격식체")
        self.assertEqual(found["length"], "A4 반 장")

    def test_stated_topics_are_not_reasked(self):
        # Meeting input that already names attendees skips the basic-info card.
        questions = build_questions("minutes", "교장·교감·부장교사 참석 회의록 정리해줘")
        self.assertFalse(any(q["id"] == "info" for q in questions))
        questions = build_questions("minutes", "오늘 교무회의 내용을 회의록으로 정리해줘")
        self.assertTrue(any(q["id"] == "info" for q in questions))

    def test_questions_depend_on_input(self):
        vague = diagnose("그거 좀 해줘")
        self.assertEqual(vague["type"], "generic")
        self.assertEqual(len(vague["questions"]), 2)
        specific = diagnose(WEEKLY)
        self.assertNotEqual([q["title"] for q in vague["questions"]],
                            [q["title"] for q in specific["questions"]])

    def test_question_count_bounds(self):
        for text in (WEEKLY, "주간보고 써줘", "보고서 써줘", COMPLAINT,
                     "그거 좀 해줘", "ㅁㄴㅇㄹ", COMPOSITE.split(",")[0]):
            with self.subTest(text=text):
                count = len(diagnose(text)["questions"])
                self.assertTrue(1 <= count <= 5, f"{count} questions for {text!r}")


class CompositeTests(unittest.TestCase):
    """Composite request detection and sequencing (F08, AC04)."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_two_tasks_detected_and_typed(self):
        segments = detect_segments(COMPOSITE)
        self.assertEqual(len(segments), 2)
        self.assertEqual([s["type"] for s in segments], ["weekly_report", "event_notice"])

    def test_single_task_not_composite(self):
        self.assertEqual(len(detect_segments(WEEKLY)), 1)
        self.assertEqual(len(detect_segments("보고서 잘 써줘")), 1)

    def test_connectives_keep_unrelated_words_together(self):
        segments = detect_segments("검토하고 수정해줘")
        self.assertEqual(len(segments), 1)


class PromptBuildTests(unittest.TestCase):
    """Completed-prompt section contracts (B1-B12)."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _weekly_prompt(self, answers=None, tuner=None):
        d = diagnose(WEEKLY)
        answers = answers if answers is not None else {
            "week_results": "방과후 프로그램 3개 운영",
            "next_week": "안전 점검 주간 운영"}
        return build_prompt("weekly_report", WEEKLY, d["questions"], answers,
                            d["extracted"], tuner or {})

    def test_report_five_sections_in_order(self):
        prompt = self._weekly_prompt()
        positions = [prompt.index(tag) for tag in
                     ("[역할]", "[맥락]", "[섹션별 근거]", "[과업]", "[제약]")]
        self.assertEqual(positions, sorted(positions))

    def test_reply_four_sections_no_basis_section(self):
        d = diagnose(COMPLAINT)
        prompt = build_prompt("complaint_reply", COMPLAINT, d["questions"],
                              {"reason": "시스템 점검으로 발급이 늦어짐"}, d["extracted"])
        positions = [prompt.index(tag) for tag in ("[역할]", "[맥락]", "[과업]", "[제약]")]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("[섹션별 근거]", prompt)

    def test_original_request_facts_reach_prompt(self):
        text = "기초학력 진단평가 결과를 분석해서 교육장님께 보고할 보고서 써줘. 대상 초등 3~6학년 1200명"
        d = diagnose(text)
        prompt = build_prompt(d["type"], text, d["questions"], {}, d["extracted"])
        context = prompt[prompt.index("[맥락]"):prompt.index("[과업]")]
        for fact in ("기초학력 진단평가", "3~6학년", "1200명"):
            self.assertIn(fact, context)

    def test_stated_report_subject_is_not_reasked(self):
        d = diagnose("기초학력 진단평가 결과를 분석해서 교육장님께 보고할 보고서 써줘")
        self.assertNotIn("agenda", [q["id"] for q in d["questions"]])
        seeds = {t["id"]: t["seed"] for t in d["seeded_topics"]}
        self.assertEqual(seeds.get("agenda"), "기초학력 진단평가 결과")

    def test_answers_flow_into_basis_mapping(self):
        prompt = self._weekly_prompt()
        self.assertIn("- 이번 주 성과: 방과후 프로그램 3개 운영", prompt)
        self.assertIn("- 다음 주 계획: 안전 점검 주간 운영", prompt)

    def test_unanswered_required_slots_get_placeholder(self):
        prompt = self._weekly_prompt(answers={})
        self.assertIn("진행 중·이슈: [확인 필요]", prompt)
        self.assertIn("[확인 필요]", prompt)
        # Optional question skipped renders as [확인 필요], never an assumed
        # "없음" — only an explicit "없음" answer may say so (P8).
        self.assertIn("도움 필요사항: [확인 필요]", prompt)
        self.assertNotIn("도움 필요사항: 없음", prompt)

    def test_explicit_none_answer_renders_as_none(self):
        # The only path allowed to write "없음" is the user's own answer.
        prompt = self._weekly_prompt(answers={"help_needed": "없음"})
        self.assertIn("도움 필요사항: 없음", prompt)

    def test_later_report_ban_only_for_work_documents(self):
        weekly = self._weekly_prompt()
        for term in ("승인 후 보고", "추후 보고", "후속 보고", "별도 보고", "보고드리겠습니다",
                     "보고 예정입니다", "확인 후 보고", "어떤 변형으로도"):
            self.assertIn(term, weekly)
        d = diagnose(COMPLAINT)
        reply = build_prompt("complaint_reply", COMPLAINT, d["questions"], {}, d["extracted"])
        for term in ("승인 후 보고", "추후 보고", "후속 보고"):
            self.assertNotIn(term, reply)

    def test_stated_tone_and_length_reflected_in_constraints(self):
        d = diagnose(WEEKLY_FULL)
        prompt = build_prompt("weekly_report", WEEKLY_FULL, d["questions"], {}, d["extracted"])
        constraint = prompt.split("[제약]", 1)[1]
        self.assertIn("A4 반 장", constraint)
        self.assertIn("격식체", constraint)

    def test_reply_signature_placeholder_when_sender_unknown(self):
        d = diagnose(COMPLAINT)
        prompt = build_prompt("complaint_reply", COMPLAINT, d["questions"], {}, d["extracted"])
        self.assertIn("[보내는 사람 이름] 드림", prompt)

    def test_reply_answers_reach_context(self):
        d = diagnose(COMPLAINT)
        prompt = build_prompt("complaint_reply", COMPLAINT, d["questions"],
                              {"reason": "시스템 점검으로 발급이 늦어짐",
                               "status": "처리 중 — 이번 주 내 완료 예정"}, d["extracted"])
        self.assertIn("시스템 점검으로 발급이 늦어짐", prompt)
        self.assertIn("처리 중 — 이번 주 내 완료 예정", prompt)
        self.assertIn("민원인", prompt)

    def test_role_line_starts_with_dangsin(self):
        self.assertIn("당신은 ", self._weekly_prompt().splitlines()[1])

    def test_tuner_selections_reflected(self):
        prompt = self._weekly_prompt(tuner={"chips": ["성과 중심"], "tone_chips": ["짧게 줄이기"],
                                            "free": "표 없이", "length_scale": "간결"})
        self.assertIn("성과 중심", prompt)
        self.assertIn("짧게 줄이기", prompt)
        self.assertIn("표 없이", prompt)
        self.assertIn("간결 수준", prompt)

    def test_render_prompt_html(self):
        prompt = self._weekly_prompt(answers={})
        rendered = render_prompt_html(prompt)
        self.assertIn("[역할]", rendered)
        self.assertIn("[섹션별 근거]", rendered)
        self.assertIn("[확인 필요]", rendered)
        self.assertIn("<", rendered)  # document-style rendering, not raw text


AC01_IN2 = "팀장님께 보낼 격식체 주간보고 써줘, A4 반 장, 한 일: 예산 집행 정리·민원 3건 처리"
EVENT_STATED = "11월 20일 오후 2시 체육관에서 여는 학생 안전 체험 행사 안내문 만들어줘"


class StatedConditionSeedTests(unittest.TestCase):
    """B-1/B8: stated conditions are seeded into the output, never re-asked."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_ac01_in2_stated_work_reaches_output_without_reask(self):
        d = diagnose(AC01_IN2)
        self.assertEqual(d["type"], "weekly_report")
        self.assertFalse(any(q["id"] == "week_results" for q in d["questions"]))
        prompt = build_prompt("weekly_report", AC01_IN2, d["questions"], {},
                              d["extracted"])
        self.assertIn("- 이번 주 성과: 예산 집행 정리·민원 3건 처리", prompt)
        self.assertIn("- 핵심 안건: 예산 집행 정리·민원 3건 처리", prompt)

    def test_explicit_schedule_and_place_preserved(self):
        d = diagnose(EVENT_STATED)
        self.assertEqual(d["type"], "event_notice")
        self.assertEqual(d["questions"], [])
        prompt = build_prompt("event_notice", EVENT_STATED, d["questions"], {},
                              d["extracted"])
        self.assertIn("11월 20일", prompt)
        self.assertIn("오후 2시", prompt)
        self.assertIn("체육관", prompt)
        self.assertIn("학생 안전 체험 행사", prompt)
        self.assertNotIn("일시·장소·참여: (해당 없음)", prompt)

    def test_stated_attendees_seed_minutes(self):
        text = "교장·교감·부장교사 참석 회의록 정리해줘"
        d = diagnose(text)
        self.assertFalse(any(q["id"] == "info" for q in d["questions"]))
        prompt = build_prompt("minutes", text, d["questions"], {}, d["extracted"])
        self.assertIn("교장·교감·부장교사 참석", prompt)

    def test_stated_reason_reaches_reply_context(self):
        text = "민원 회신 써줘, 지연 사유: 시스템 점검으로 발급이 늦어짐"
        d = diagnose(text)
        prompt = build_prompt(d["type"], text, d["questions"], {}, d["extracted"])
        self.assertIn("시스템 점검으로 발급이 늦어짐", prompt)

    def test_medium_reaches_task_and_constraints(self):
        text = "학부모가 상담을 신청했는데 답변 문자 써줘"
        d = diagnose(text)
        prompt = build_prompt(d["type"], text, d["questions"], {}, d["extracted"])
        self.assertIn("'문자' 형식", prompt.split("[과업]", 1)[1])
        self.assertIn("전달할 매체는 '문자'", prompt)

    def test_label_echo_seed_becomes_confirm_needed(self):
        # MAIN decision #11: the matched keyword itself is not a value.
        text = "민원 회신 써줘, 지연 사유를 물어보더라"
        d = diagnose(text)
        self.assertEqual(d["type"], "complaint_reply")
        # The topic is still not re-asked — only the seed is honest.
        self.assertFalse(any(q["id"] == "reason" for q in d["questions"]))
        prompt = build_prompt(d["type"], text, d["questions"], {}, d["extracted"])
        self.assertIn("지연·사유 사실: [확인 필요]", prompt)
        self.assertNotIn("사실: 지연 사유", prompt)

    def test_keyword_only_capture_is_not_seeded_as_value(self):
        text = "행사 안내문 만들어줘, 체육관에서 한다고 들었어"
        d = diagnose(text)
        self.assertEqual(d["type"], "event_notice")
        prompt = build_prompt(d["type"], text, d["questions"], {}, d["extracted"])
        self.assertIn("행사 주제·목적: [확인 필요]", prompt)
        # The bare keyword never becomes a seeded value (line ends after '행사').
        self.assertNotIn("주제·목적: 행사\n", prompt)
        # A real stated value in the same input still seeds normally.
        self.assertIn("체육관", prompt)

    def test_event_type_signal_does_not_cover_topic_question(self):
        # AC04.aux regression (A6/T7): '행사'·'안내문' are type signals, not a
        # stated topic — '주민 참여 행사' still asks 주제·목적 (I04: 2 questions).
        for text in ("다음 달 주민 참여 행사 안내문 만들어줘",
                     "행사 안내문도 만들어줘"):
            with self.subTest(text=text):
                questions = build_questions("event_notice", text)
                titles = [q["title"] for q in questions]
                self.assertGreaterEqual(len(questions), 2)
                self.assertTrue(any("주제" in title for title in titles))
                self.assertTrue(any("일시" in title for title in titles))

    def test_event_concrete_topic_still_counts_as_stated(self):
        # A concrete topic word still suppresses the question (체험 행사).
        questions = build_questions("event_notice", "학생 안전 체험 행사 안내문 만들어줘")
        self.assertFalse(any(q["id"] == "topic" for q in questions))
        self.assertTrue(any(q["id"] == "schedule" for q in questions))

    def test_covered_but_echoing_event_topic_seeds_confirm_needed(self):
        # '주제' 키워드만 잡힌 covered 주제는 실제 값이 없으므로 시드가
        # [확인 필요] 자리표시자로 주입된다 — 질문은 하지 않되 지어내지 않는다.
        text = "행사 주제는 추후 공지, 체육관에서 진행해줘"
        d = diagnose(text)
        self.assertFalse(any(q["id"] == "topic" for q in d["questions"]))
        prompt = build_prompt(d["type"], text, d["questions"], {}, d["extracted"])
        self.assertIn("행사 주제·목적: [확인 필요]", prompt)
        self.assertNotIn("주제·목적: 주제", prompt)


class TunerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_axes_exist_and_are_type_specific(self):
        weekly = tuner_axes("weekly_report")
        self.assertTrue(any(axis["kind"] == "slider" for axis in weekly))
        reply = tuner_axes("complaint_reply")
        self.assertTrue(any(axis["kind"] == "chips" for axis in reply))
        self.assertNotEqual(weekly, reply)

    def test_meaningless_input_gets_reentry_axis(self):
        axes = tuner_axes("generic", meaningless=True)
        self.assertTrue(any(axis["id"] == "reenter" for axis in axes))
        self.assertTrue(is_meaningless("ㅁㄴㅇㄹㅁㄴㅇㄹ"))
        self.assertFalse(is_meaningless("보고서 써줘"))


class PreferenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PreferenceStore(Path(self.tmp.name) / "prefs.json")

    def test_repeat_answer_prefills_after_threshold(self):
        for _ in range(flow._PREF_MIN_USES):
            self.store.record("weekly_report", "week_results", "민원 3건 처리")
        self.assertEqual(self.store.prefilled("weekly_report"), {"week_results": "민원 3건 처리"})
        self.store.record("weekly_report", "week_results", "다른 답변")
        self.assertNotIn("week_results", self.store.prefilled("weekly_report"))

    def test_clear_and_corrupt_file(self):
        self.store.record("weekly_report", "week_results", "민원 3건 처리")
        self.store.clear("weekly_report")
        self.assertEqual(self.store.prefilled("weekly_report"), {})
        self.store.path.write_bytes(b"{broken")
        self.assertEqual(self.store.load(), {})


class FlowDialogTests(unittest.TestCase):
    """UI-level flow: cards, counter, tuner, result actions (A1-A13, C1-C5)."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._dialogs = []
        self._pref_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._pref_tmp.cleanup)
        pref_patch = patch("optimizer_flow._pref_path",
                           return_value=Path(self._pref_tmp.name) / "prefs.json")
        pref_patch.start()
        self.addCleanup(pref_patch.stop)

    def tearDown(self):
        for dialog in self._dialogs:
            dialog.close()
        self.app.processEvents()

    def _dialog(self, getter=None, on_finished=None, seed=""):
        dialog = FlowDialog(None, seed,
                            provider_getter=getter or (lambda: ("offline", None)),
                            on_finished=on_finished)
        self._dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    def _start(self, dialog, text):
        dialog.input_edit.setPlainText(text)
        dialog._start()
        self.app.processEvents()

    def test_ac01_weekly_flow_end_to_end(self):
        dialog = self._dialog()
        self._start(dialog, WEEKLY)
        self.assertEqual(dialog.pages.currentIndex(), FlowDialog.PAGE_WORK)
        # Diagnosis screen: verdict + percent + type label + restart (A1, A13).
        self.assertIn("완성도", dialog.percent_label.text())
        self.assertTrue(dialog.verdict_label.text().strip())
        self.assertIn("주간 업무 보고", dialog.step_label.text())
        self.assertTrue(dialog.restart_button.isEnabled())
        # Missing-condition summary lists question titles 1:1 (A12).
        for question in dialog._diag["questions"]:
            self.assertIn(question["title"], dialog.summary_label.text())
        # One-screen batch: all cards on one scroll page, numbered (A2, A3).
        self.assertEqual(len(dialog._cards), 4)
        for index, card in enumerate(dialog._cards, 1):
            self.assertTrue(card.title_label.text().startswith(f"{index}."))
            self.assertTrue(card.reason_label.text().startswith("묻는 이유:"))
            self.assertTrue(card.chip_buttons)  # example chips exist
        # Optional marker on the help-needed card (A6).
        self.assertIn("없으면 비워두세요", dialog._cards[-1].title_label.text())
        # n/N counter tracks answers (A5).
        self.assertIn("0/4", dialog.counter_label.text())
        dialog._cards[0]._fill(dialog._cards[0].question["chips"][0])
        dialog._cards[1].answer.setPlainText("예산 집행 지연")
        self.assertIn("2/4", dialog.counter_label.text())
        # Tuner loads asynchronously; axes are present and skippable (A7, A8).
        dialog._load_tuner()
        self.assertTrue(dialog._tuner_widgets)
        # Generate -> staged progress -> result screen (A9).
        dialog._generate()
        self.assertEqual(dialog.pages.currentIndex(), FlowDialog.PAGE_GENERATING)
        self.assertEqual(len(dialog.stage_labels), 3)
        dialog._finish_generation()
        self.assertEqual(dialog.pages.currentIndex(), FlowDialog.PAGE_RESULT)
        prompt = dialog.result_edit.toPlainText()
        self.assertIn("[섹션별 근거]", prompt)
        self.assertIn("과장님", prompt)
        self.assertEqual(dialog.complete_label.text(), "완성도 100%")
        # Document-style rendering of the prompt itself (B13).
        dialog.view_toggle.setChecked(True)
        self.assertIn("[역할]", dialog.render_view.toHtml())
        dialog.view_toggle.setChecked(False)

    def test_ac02_reply_prompt_structure(self):
        dialog = self._dialog()
        self._start(dialog, COMPLAINT)
        dialog._cards[0].answer.setPlainText("시스템 점검으로 발급이 늦어짐")
        dialog._load_tuner()
        dialog._finish_generation()
        prompt = dialog.result_edit.toPlainText()
        self.assertNotIn("[섹션별 근거]", prompt)
        self.assertIn("[보내는 사람 이름] 드림", prompt)
        self.assertIn("시스템 점검으로 발급이 늦어짐", prompt)

    def test_ac03_tuner_skipped_still_generates(self):
        dialog = self._dialog()
        self._start(dialog, WEEKLY)
        # Do not touch the tuner at all; generation must still succeed.
        dialog._finish_generation()
        self.assertIn("[제약]", dialog.result_edit.toPlainText())

    def test_ac03_slider_and_chip_choices_reach_prompt(self):
        dialog = self._dialog()
        self._start(dialog, "주간보고 써줘")
        dialog._load_tuner()
        slider = next(w for kind, _i, w, _o in dialog._tuner_widgets
                      if kind == "slider" and _i == "length_scale")
        slider.setValue(4)  # '전체 상세'
        chips = next(w for kind, _i, w, _o in dialog._tuner_widgets
                     if kind == "chips" and _i == "chips")
        chips[0].setChecked(True)
        dialog._finish_generation()
        prompt = dialog.result_edit.toPlainText()
        self.assertIn("전체 상세 수준", prompt)
        self.assertIn("성과 중심", prompt)

    def test_ac04_composite_notice_and_sequencing(self):
        dialog = self._dialog()
        self._start(dialog, COMPOSITE)
        self.assertTrue(dialog.composite_notice.isVisibleTo(dialog.pages))
        notice = dialog.composite_notice.text()
        self.assertIn("순서대로", notice)
        self.assertIn("1.", notice)
        self.assertIn("2.", notice)
        self.assertIn("주간 업무 보고", dialog.step_label.text())
        dialog._finish_generation()
        self.assertTrue(dialog.next_step_button.isVisibleTo(dialog.pages))
        self.assertIn("2/2", dialog.next_step_button.text())
        dialog.next_step_button.click()
        self.app.processEvents()
        self.assertIn("행사 안내", dialog.step_label.text())
        self.assertFalse(dialog.composite_notice.isHidden())

    def test_ac05_meaningless_and_vague_fallback(self):
        dialog = self._dialog()
        self._start(dialog, "ㅁㄴㅇㄹㅁㄴㅇㄹ")
        self.assertEqual(dialog._diag["type"], "generic")
        self.assertEqual(len(dialog._cards), 2)
        dialog._load_tuner()
        axes = [axis_id for _kind, axis_id, _w, _o in dialog._tuner_widgets]
        self.assertIn("reenter", axes)

    def test_save_is_explicit_and_labeled(self):
        dialog = self._dialog()
        self._start(dialog, WEEKLY)
        dialog._finish_generation()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store_file = Path(tmp.name) / "library.json"
        from optimizer_library import LibraryStore
        store = LibraryStore(store_file)
        # Nothing saved until the user presses save (C1).
        self.assertEqual(store.load(), [])
        with patch("optimizer_library.LibraryStore", return_value=store):
            dialog.save_button.click()
        items = store.load()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["mode"], "주간 업무 보고")
        self.assertEqual(items[0]["prompt"], WEEKLY)
        self.assertIn("[섹션별 근거]", items[0]["result"])
        self.assertIn("저장됨", dialog.save_button.text())

    def test_copy_puts_full_prompt_on_clipboard(self):
        clipboard, patcher = _clipboard_patch()
        with patcher:
            dialog = self._dialog()
            self._start(dialog, WEEKLY)
            dialog._finish_generation()
            dialog.copy_button.click()
            self.assertEqual(clipboard.text(), dialog.result_edit.toPlainText())
            self.assertIn("[섹션별 근거]", clipboard.text())

    def test_external_handoff_copies_then_opens(self):
        clipboard, patcher = _clipboard_patch()
        with patcher, patch("optimizer_flow.QDesktopServices.openUrl") as open_url:
            dialog = self._dialog()
            self._start(dialog, WEEKLY)
            dialog._finish_generation()
            dialog.external_buttons["chatgpt"].click()
            self.assertEqual(clipboard.text(), dialog.result_edit.toPlainText())
            self.assertIn("chatgpt.com", open_url.call_args.args[0].toString())
            self.assertIn("붙여", dialog.result_status.text())

    def test_restart_returns_to_input(self):
        dialog = self._dialog()
        self._start(dialog, WEEKLY)
        dialog.restart_button.click()
        self.assertEqual(dialog.pages.currentIndex(), FlowDialog.PAGE_INPUT)
        dialog._finish_generation()
        dialog.new_button.click()
        self.assertEqual(dialog.pages.currentIndex(), FlowDialog.PAGE_INPUT)

    def test_ai_button_hidden_without_ai_provider(self):
        dialog = self._dialog()
        self._start(dialog, WEEKLY)
        self.assertTrue(dialog.ai_question_button.isHidden())
        ai_dialog = self._dialog(getter=lambda: ("vireonix", None))
        self._start(ai_dialog, WEEKLY)  # 4 questions -> room for AI supplements
        self.assertFalse(ai_dialog.ai_question_button.isHidden())

    def test_ai_questions_require_consent_and_append_cards(self):
        _AutoAnswerBox.answer = QMessageBox.StandardButton.No
        dialog = self._dialog(getter=lambda: ("vireonix", None))
        self._start(dialog, WEEKLY)
        base = len(dialog._cards)
        with patch("optimizer_flow.QMessageBox", _AutoAnswerBox), \
                patch("optimizer_ai.ask_ai") as ask:
            dialog.ai_question_button.click()
            self.app.processEvents()
        self.assertFalse(ask.called)
        self.assertFalse(dialog._busy_ai)

        _AutoAnswerBox.answer = QMessageBox.StandardButton.Yes
        with patch("optimizer_flow.QMessageBox", _AutoAnswerBox), \
                patch("optimizer_ai.ask_ai",
                      return_value="보고 제출 기한이 언제인가요?\n참고할 지난 보고가 있나요?") as ask:
            dialog.ai_question_button.click()
            # Worker thread + Qt signal: pump until the call returns.
            loop = QEventLoop()
            timer = QTimer()
            timer.timeout.connect(lambda: loop.quit() if not dialog._busy_ai else None)
            timer.start(25)
            guard = QTimer.singleShot(5000, loop.quit)
            loop.exec()
            timer.stop()
            self.app.processEvents()
        self.assertTrue(ask.called)
        self.assertEqual(len(dialog._cards), base + 2)
        self.assertTrue(all(card.question.get("optional") for card in dialog._cards[base:]))

    def test_refine_requires_ai_provider(self):
        dialog = self._dialog()
        self._start(dialog, WEEKLY)
        dialog._finish_generation()
        dialog.refine_input.setText("더 짧게")
        dialog._refine()
        self.assertIn("AI 정리 방식", dialog.result_status.text())

    def test_preference_prefill_after_repeated_answers(self):
        store = PreferenceStore(Path(self._pref_tmp.name) / "prefs.json")
        for _ in range(flow._PREF_MIN_USES):
            store.record("weekly_report", "week_results", "민원 3건 처리")
        dialog = self._dialog()
        self._start(dialog, "주간보고 써줘")
        week_card = next(card for card in dialog._cards if card.question["id"] == "week_results")
        self.assertEqual(week_card.value(), "민원 3건 처리")
        self.assertFalse(dialog.pref_clear.isHidden())

    def test_composite_tuner_state_does_not_leak_between_segments(self):
        dialog = self._dialog()
        self._start(dialog, COMPOSITE)
        dialog._load_tuner()
        chips = next(w for kind, _i, w, _o in dialog._tuner_widgets
                     if kind == "chips" and _i == "chips")
        chips[0].setChecked(True)  # '성과 중심'
        dialog.tuner_free.setText("표 없이")
        dialog._finish_generation()
        dialog.next_step_button.click()
        self.app.processEvents()
        # Stage 2 must start clean — a generate that fires before the deferred
        # tuner reload can never read stage-1 controls or text.
        self.assertEqual(dialog._tuner_widgets, [])
        self.assertIsNone(dialog.tuner_free)
        dialog._finish_generation()
        prompt = dialog.result_edit.toPlainText()
        self.assertNotIn("표 없이", prompt)
        self.assertNotIn("성과 중심", prompt)

    def test_restart_clears_tuner_state(self):
        dialog = self._dialog()
        self._start(dialog, WEEKLY)
        dialog._load_tuner()
        dialog.tuner_free.setText("표 없이")
        dialog.restart_button.click()
        self.assertEqual(dialog._tuner_widgets, [])
        self.assertIsNone(dialog.tuner_free)

    def test_generate_blocked_while_ai_busy(self):
        dialog = self._dialog()
        self._start(dialog, WEEKLY)
        dialog._busy_ai = True
        dialog._generate()
        self.assertEqual(dialog.pages.currentIndex(), FlowDialog.PAGE_WORK)

    def test_long_answer_is_clipped_for_save(self):
        dialog = self._dialog()
        self._start(dialog, WEEKLY)
        dialog._cards[0].answer.setPlainText("가" * 2100)
        dialog._finish_generation()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store_file = Path(tmp.name) / "library.json"
        from optimizer_library import LibraryStore
        store = LibraryStore(store_file)
        with patch("optimizer_library.LibraryStore", return_value=store):
            dialog.save_button.click()
        items = store.load()
        self.assertEqual(len(items), 1)
        for entry in items[0]["syeonRevisions"]:
            self.assertLessEqual(flow.utf16_length(entry), 2000)
        self.assertIn("잘려", dialog.result_status.text())


class _NoticeBox:
    """Records the unsaved-results heads-up instead of showing it."""
    instances = []
    Icon = QMessageBox.Icon
    StandardButton = QMessageBox.StandardButton

    def __init__(self, *args, **kwargs):
        self.args = args
        _NoticeBox.instances.append(self)

    def setAttribute(self, *args):
        pass

    def setDefaultButton(self, _button):
        pass

    def setWindowModality(self, *args):
        pass

    def open(self):
        pass

    def show(self):
        pass


class CompositeResultRecallTests(unittest.TestCase):
    """Finished-step result recall + unsaved notices (MAIN decision #8, N-4).

    A composite request's finished results stay reachable inside the flow
    window (memory only — the dialog closing drops them). The library only
    ever receives explicit saves (C1), and leaving unsaved results behind is
    announced without blocking progress.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._dialogs = []
        self._pref_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._pref_tmp.cleanup)
        pref_patch = patch("optimizer_flow._pref_path",
                           return_value=Path(self._pref_tmp.name) / "prefs.json")
        pref_patch.start()
        self.addCleanup(pref_patch.stop)

    def tearDown(self):
        for dialog in self._dialogs:
            dialog.close()
        self.app.processEvents()

    def _dialog(self, getter=None, on_finished=None, seed=""):
        dialog = FlowDialog(None, seed,
                            provider_getter=getter or (lambda: ("offline", None)),
                            on_finished=on_finished)
        self._dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    def _start(self, dialog, text):
        dialog.input_edit.setPlainText(text)
        dialog._start()
        self.app.processEvents()

    def _done_buttons(self, row):
        return row.findChildren(QPushButton)

    def test_finished_step_result_is_reachable_from_work_page(self):
        dialog = self._dialog()
        self._start(dialog, COMPOSITE)
        dialog._finish_generation()
        first = dialog.result_edit.toPlainText()
        self.assertIn("[섹션별 근거]", first)
        dialog.next_step_button.click()
        self.app.processEvents()
        self.assertEqual(dialog.pages.currentIndex(), FlowDialog.PAGE_WORK)
        # The finished step is listed as a chip on the next step's page.
        self.assertFalse(dialog.done_row_work.isHidden())
        chips = self._done_buttons(dialog.done_row_work)
        self.assertEqual(len(chips), 1)
        self.assertIn("①", chips[0].text())
        self.assertIn("주간 업무 보고", chips[0].text())
        # Clicking it reopens the full text; copy still goes to the clipboard.
        chips[0].click()
        self.app.processEvents()
        self.assertEqual(dialog.pages.currentIndex(), FlowDialog.PAGE_RESULT)
        self.assertEqual(dialog.result_edit.toPlainText(), first)
        self.assertFalse(dialog.back_button.isHidden())
        clipboard, patcher = _clipboard_patch()
        with patcher:
            dialog.copy_button.click()
            self.assertEqual(clipboard.text(), first)
        # Back returns to the in-progress step's question page.
        dialog.back_button.click()
        self.app.processEvents()
        self.assertEqual(dialog.pages.currentIndex(), FlowDialog.PAGE_WORK)
        self.assertIn("행사 안내", dialog.step_label.text())

    def test_result_page_switches_between_finished_steps(self):
        dialog = self._dialog()
        self._start(dialog, COMPOSITE)
        dialog._finish_generation()
        first = dialog.result_edit.toPlainText()
        dialog.next_step_button.click()
        self.app.processEvents()
        dialog._finish_generation()
        second = dialog.result_edit.toPlainText()
        self.assertNotEqual(first, second)
        self.assertFalse(dialog.done_row_result.isHidden())
        chips = self._done_buttons(dialog.done_row_result)
        self.assertEqual(len(chips), 2)
        self.assertTrue(chips[1].isChecked())  # the step being viewed
        chips[0].click()
        self.app.processEvents()
        self.assertEqual(dialog.result_edit.toPlainText(), first)
        self.assertFalse(dialog.back_button.isHidden())
        # Refine is bound to the live segment only — locked while re-reading.
        self.assertFalse(dialog.refine_input.isEnabled())
        dialog.back_button.click()
        self.app.processEvents()
        self.assertEqual(dialog.result_edit.toPlainText(), second)
        self.assertTrue(dialog.back_button.isHidden())

    def test_step_edits_kept_when_switching_views(self):
        dialog = self._dialog()
        self._start(dialog, COMPOSITE)
        dialog._finish_generation()
        dialog.result_edit.setPlainText("1단계 사용자 수정본")
        dialog.next_step_button.click()
        self.app.processEvents()
        dialog._finish_generation()
        second = dialog.result_edit.toPlainText()
        self._done_buttons(dialog.done_row_result)[0].click()
        self.app.processEvents()
        # Edits made before moving on survive the round trip (memory only).
        self.assertEqual(dialog.result_edit.toPlainText(), "1단계 사용자 수정본")
        dialog.result_edit.setPlainText("1단계 재수정본")
        dialog.back_button.click()
        self.app.processEvents()
        self.assertEqual(dialog.result_edit.toPlainText(), second)
        self._done_buttons(dialog.done_row_result)[0].click()
        self.app.processEvents()
        self.assertEqual(dialog.result_edit.toPlainText(), "1단계 재수정본")

    def test_unsaved_notice_on_next_step_and_close(self):
        _NoticeBox.instances = []
        with patch("optimizer_flow.QMessageBox", _NoticeBox):
            dialog = self._dialog()
            self._start(dialog, COMPOSITE)
            dialog._finish_generation()
            dialog.next_step_button.click()
            self.app.processEvents()
            # Advancing past an unsaved result leaves a non-blocking notice.
            self.assertFalse(dialog.unsaved_notice_work.isHidden())
            self.assertIn("저장하지 않은", dialog.unsaved_notice_work.text())
            dialog.close()
            self.app.processEvents()
            # The close itself still goes through — nothing is vetoed.
            self.assertFalse(dialog.isVisible())
        self.assertEqual(len(_NoticeBox.instances), 1)
        self.assertIn("저장하지 않은", _NoticeBox.instances[0].args[2])

    def test_no_close_notice_when_everything_saved(self):
        _NoticeBox.instances = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store_file = Path(tmp.name) / "library.json"
        from optimizer_library import LibraryStore
        store = LibraryStore(store_file)
        with patch("optimizer_flow.QMessageBox", _NoticeBox):
            dialog = self._dialog()
            self._start(dialog, WEEKLY)
            dialog._finish_generation()
            with patch("optimizer_library.LibraryStore", return_value=store):
                dialog.save_button.click()
            dialog.close()
            self.app.processEvents()
        self.assertEqual(_NoticeBox.instances, [])

    def test_completed_results_stay_out_of_library_until_explicit_save(self):
        dialog = self._dialog()
        self._start(dialog, COMPOSITE)
        dialog._finish_generation()
        first = dialog.result_edit.toPlainText()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store_file = Path(tmp.name) / "library.json"
        from optimizer_library import LibraryStore
        store = LibraryStore(store_file)
        dialog.next_step_button.click()
        self.app.processEvents()
        # C1: keeping the finished result in memory never auto-saves it.
        self.assertEqual(store.load(), [])
        self._done_buttons(dialog.done_row_work)[0].click()
        self.app.processEvents()
        with patch("optimizer_library.LibraryStore", return_value=store):
            dialog.save_button.click()
        items = store.load()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["mode"], "주간 업무 보고")
        self.assertEqual(items[0]["prompt"], dialog._segments[0]["text"])
        self.assertEqual(items[0]["result"], first)
        # The chip gains a saved marker and the unsaved notice clears.
        self.assertIn("저장됨", self._done_buttons(dialog.done_row_work)[0].text())
        self.assertTrue(dialog.unsaved_notice_work.isHidden())


class StaleAiResultTests(unittest.TestCase):
    """Async AI edge cases (review round 2: R2-1 refine lock, R2-2 stale replies).

    An AI reply is bound to the (segment, generation) context it was sent
    under; when the user has moved on — next step, restart — the late reply
    is discarded: no page pull, no record written under another step's
    label. Refine controls stay locked while a finished step is being read.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._dialogs = []
        self._pref_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._pref_tmp.cleanup)
        pref_patch = patch("optimizer_flow._pref_path",
                           return_value=Path(self._pref_tmp.name) / "prefs.json")
        pref_patch.start()
        self.addCleanup(pref_patch.stop)

    def tearDown(self):
        for dialog in self._dialogs:
            dialog.close()
        self.app.processEvents()

    def _dialog(self, getter=None):
        dialog = FlowDialog(None, "",
                            provider_getter=getter or (lambda: ("vireonix", None)))
        self._dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    def _start(self, dialog, text):
        dialog.input_edit.setPlainText(text)
        dialog._start()
        self.app.processEvents()

    def _done_buttons(self, row):
        return row.findChildren(QPushButton)

    def _mark_inflight(self, dialog, kind):
        """Simulate an AI request sent under the current context."""
        dialog._busy_ai = True
        dialog._ai_kind = kind
        dialog._ai_context = (dialog._segment_index, dialog._flow_generation)

    def test_ai_done_keeps_refine_locked_while_browsing_finished_step(self):
        dialog = self._dialog()
        self._start(dialog, COMPOSITE)
        dialog._finish_generation()
        dialog.next_step_button.click()
        self.app.processEvents()
        self._done_buttons(dialog.done_row_work)[0].click()  # read step 1
        self.app.processEvents()
        self.assertFalse(dialog.refine_input.isEnabled())
        self.assertFalse(dialog.refine_button.isEnabled())
        # An in-flight questions reply lands while the user is reading the
        # finished step — the restore path must not resurrect the button.
        self._mark_inflight(dialog, "questions")
        dialog._ai_done(["추가로 확인할 질문 하나입니다"])
        self.app.processEvents()
        self.assertFalse(dialog.refine_button.isEnabled())
        self.assertFalse(dialog.refine_input.isEnabled())
        # And a refine can never fire cross-segment even if invoked.
        dialog.refine_input.setText("더 짧게")
        dialog._refine()
        self.assertFalse(dialog._busy_ai)

    def test_refine_button_restored_on_current_segment_result(self):
        dialog = self._dialog()
        self._start(dialog, WEEKLY)
        dialog._finish_generation()
        self._mark_inflight(dialog, "refine")
        dialog._ai_done("AI가 다듬은 결과 텍스트")
        self.app.processEvents()
        self.assertEqual(dialog.result_edit.toPlainText(), "AI가 다듬은 결과 텍스트")
        self.assertTrue(dialog.refine_button.isEnabled())

    def test_late_refine_result_discarded_after_next_step(self):
        dialog = self._dialog()
        self._start(dialog, COMPOSITE)
        dialog._finish_generation()
        first = dialog.result_edit.toPlainText()
        # Refine in flight for step 1; the user moves on before it lands.
        self._mark_inflight(dialog, "refine")
        dialog.next_step_button.click()
        self.app.processEvents()
        self.assertEqual(dialog.pages.currentIndex(), FlowDialog.PAGE_WORK)
        dialog._ai_done("늦게 도착한 다듬기 결과")
        self.app.processEvents()
        # Dropped: no page pull, no record under step 2's label, controls free.
        self.assertEqual(dialog.pages.currentIndex(), FlowDialog.PAGE_WORK)
        self.assertNotIn(1, dialog._step_results)
        self.assertEqual(dialog._step_results[0]["text"], first)
        self.assertFalse(dialog._busy_ai)
        self.assertIn("반영하지 않", dialog.ai_question_status.text())

    def test_late_refine_result_discarded_after_restart(self):
        dialog = self._dialog()
        self._start(dialog, WEEKLY)
        dialog._finish_generation()
        first = dialog.result_edit.toPlainText()
        self._mark_inflight(dialog, "refine")
        dialog.new_button.click()  # back to the input page
        self.app.processEvents()
        self.assertEqual(dialog.pages.currentIndex(), FlowDialog.PAGE_INPUT)
        dialog._ai_done("늦게 도착한 다듬기 결과")
        self.app.processEvents()
        self.assertEqual(dialog.pages.currentIndex(), FlowDialog.PAGE_INPUT)
        self.assertEqual(dialog._step_results[0]["text"], first)
        self.assertFalse(dialog._busy_ai)


class FlowPanelIntegrationTests(unittest.TestCase):
    """PromptConversation wiring: entry button, provider getter, result intake."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._profile_patch = patch("ai_profiles.load_profiles", return_value=[])
        self._profile_patch.start()
        self.addCleanup(self._profile_patch.stop)
        from optimizer_chat import PromptConversation
        self.panel = PromptConversation()
        self.panel.show()
        self.app.processEvents()
        self.addCleanup(self.panel.close)

    def test_entry_button_opens_flow_with_current_input(self):
        self.panel.input.setPlainText(WEEKLY)
        self.panel._open_flow()
        self.addCleanup(self.panel._flow_dialog.close)
        self.assertIsNotNone(self.panel._flow_dialog)
        self.assertTrue(self.panel._flow_dialog.isVisible())
        self.assertEqual(self.panel._flow_dialog.input_edit.toPlainText(), WEEKLY)

    def test_flow_provider_follows_selected_destination(self):
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("offline"))
        self.assertEqual(self.panel._flow_provider(), ("offline", None))
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        provider, profile = self.panel._flow_provider()
        self.assertEqual(provider, "vireonix")
        self.assertIsNone(profile)

    def test_accept_flow_result_inserts_into_results(self):
        prompt = build_prompt("weekly_report", WEEKLY,
                              diagnose(WEEKLY)["questions"], {}, diagnose(WEEKLY)["extracted"])
        self.panel._accept_flow_result(prompt, "주간 업무 보고", WEEKLY)
        self.assertEqual(self.panel.original, WEEKLY)
        self.assertEqual(self.panel.results, [prompt])
        self.assertEqual(self.panel.result.toPlainText(), prompt)
        self.assertIn("완성형", self.panel.result_choice.itemText(0))

    def test_open_flow_dialog_blocks_state_export(self):
        self.panel._open_flow()
        self.addCleanup(self.panel._flow_dialog.close)
        with self.assertRaises(RuntimeError):
            self.panel.export_update_state()

    def test_corrupt_profiles_surface_settings_error(self):
        from optimizer_chat import _SETTINGS_ERROR
        with patch("ai_profiles.load_profiles", side_effect=ValueError("corrupt")):
            self.panel.reload_profiles()
        self.assertTrue(self.panel._profiles_unreadable)
        self.panel._provider_changed()
        self.assertEqual(self.panel.provider_note.text(), _SETTINGS_ERROR)

    def test_constructor_survives_corrupt_profiles(self):
        from optimizer_chat import _SETTINGS_ERROR, PromptConversation
        with patch("ai_profiles.load_profiles", side_effect=ValueError("corrupt")):
            panel = PromptConversation()
            self.addCleanup(panel.close)
        self.assertTrue(panel._profiles_unreadable)
        self.assertEqual(panel.provider_note.text(), _SETTINGS_ERROR)

    def test_submit_if_unchanged_survives_snapshot_error(self):
        from optimizer_chat import _SETTINGS_ERROR
        with patch.object(self.panel, "_request_snapshot",
                          side_effect=OSError("QA_PRIVATE")):
            self.panel._submit_if_unchanged(
                "expected", "t", "o", "r", "offline", "oc", "nc", "prev",
                "task", "detail", [])
        self.assertIn("읽지 못해", self.panel.inline_notice.text())
        self.assertNotIn("QA_PRIVATE", self.panel.inline_notice.text())
        self.assertFalse(self.panel._busy)

    def test_busy_panel_reports_flow_result_instead_of_dropping(self):
        self.panel._busy = True
        self.panel._accept_flow_result("[역할] QA", "주간 업무 보고", WEEKLY)
        self.assertIn("보관함", self.panel.inline_notice.text())
        self.assertEqual(self.panel.results, [])


if __name__ == "__main__":
    unittest.main()
