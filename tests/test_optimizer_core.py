import unittest

import json
import re
from pathlib import Path

from optimizer_core import (KEEP_MATERIAL_HEADING, KEEP_ORIGINAL_HEADING, _material,
                            create_offline_prompt, keep_original_details, review_result,
                            utf16_length)


class OfflinePromptTests(unittest.TestCase):
    def test_preserves_request_context_and_longest_code_fences(self):
        original = "  설명: ```python\nprint('안녕')\n```\n\n"
        context = "앞의 줄\n````\n마지막 줄  "
        result = create_offline_prompt(original, context, "coding", "detailed")
        self.assertEqual(result["mode"], "offline")
        self.assertIn("원래 요청:\n````\n" + original + "\n````", result["result"])
        self.assertIn("사용자가 제공한 참고 맥락:\n`````\n" + context + "\n`````", result["result"])
        self.assertIn("작업 유형: 프로그래밍과 개발", result["result"])
        self.assertIn("AI가 의미를 분석", result["notes"][0])

    def test_auto_and_blank_context_do_not_add_labels(self):
        result = create_offline_prompt("있는 그대로", " \n")
        self.assertNotIn("작업 유형:", result["result"])
        self.assertNotIn("사용자가 제공한 참고 맥락:", result["result"])

    def test_validates_utf16_limits_like_javascript(self):
        self.assertEqual(utf16_length("A😀"), 3)
        create_offline_prompt("😀" * 15000, "😀" * 1000)
        with self.assertRaisesRegex(ValueError, "30,000"):
            create_offline_prompt("😀" * 15001)
        with self.assertRaisesRegex(ValueError, "2,000"):
            create_offline_prompt("요청", "😀" * 1001)

    def test_rejects_invalid_inputs(self):
        for kwargs in ({"prompt": "  "}, {"prompt": None}, {"prompt": "x", "task": "other"},
                       {"prompt": "x", "detail": "other"}, {"prompt": "x", "context": None}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                create_offline_prompt(**kwargs)


class ReviewTests(unittest.TestCase):
    def test_inquiry_response_role_is_a_review_hint_not_role_assignment(self):
        prompt = ("가상의 학교 2곳이 시설 점검 일정에 대해 문의했어. "
                  "확인된 사실과 확인 필요 사항을 나눠 답변 초안을 요청해 줘.")
        flawed = "가상의 학교 2곳이 시설 점검 일정에 대해 답변 초안을 작성해 주세요."
        correct = "가상의 학교 2곳의 문의에 대한 답변 초안을 작성해 주세요."
        for output in (flawed, correct):
            with self.subTest(output=output):
                warnings = review_result(prompt, "", output)
                role_hints = [w for w in warnings if "답변 작성자·수신자" in w]
                self.assertEqual(len(role_hints), 1)
                self.assertIn("원문과 대조", role_hints[0])
                self.assertNotIn("학교 2곳", role_hints[0])

    def test_inquiry_role_hint_uses_context_and_ignores_unrelated_tasks(self):
        warnings = review_result("시설 점검 안내를 작성해 주세요.",
                                 "학교의 질의에 대해 담당자가 회신합니다.",
                                 "시설 점검 회신을 작성해 주세요.")
        self.assertTrue(any("답변 작성자·수신자" in w for w in warnings))
        for prompt in ("회의 내용을 세 항목으로 요약해 주세요.",
                       "안내문에 문의처와 답변 기간을 적어 주세요."):
            with self.subTest(prompt=prompt):
                self.assertFalse(any("답변 작성자·수신자" in w
                                     for w in review_result(prompt, "", "안내문을 작성해 주세요.")))

    def test_explicit_and_unspecified_inquiry_roles_are_not_invented(self):
        cases = (
            ("학교가 교육청에 문의했고 교육청이 학교에 회신해요.", "교육청이 학교에 회신해 주세요."),
            ("교육청이 학교에 문의했고 학교가 교육청에 답변해요.", "학교가 교육청에 답변해 주세요."),
            ("민원 회신 초안을 작성해 주세요. 문의자 이름은 김가상.", "답변 초안을 작성해 주세요."),
        )
        for prompt, output in cases:
            with self.subTest(prompt=prompt):
                hints = [w for w in review_result(prompt, "", output) if "답변 작성자·수신자" in w]
                self.assertEqual(len(hints), 1)
                self.assertNotIn("김가상", hints[0])
                self.assertNotIn("교육청", hints[0])
                self.assertNotIn("학교", hints[0])

    def test_uncertain_inquiry_and_confirmed_fact_heading_prompt_review(self):
        original = "학교 2곳이 12월 1일 시설 점검 일정에 대해 문의했습니다. 확인된 사실과 확인 필요를 구분하세요."
        result = "확인된 사실: 학교 2곳의 12월 1일 시설 점검 일정."
        warnings = review_result(original, "", result)
        alert = next(w for w in warnings if "확정된 사실처럼" in w)
        self.assertNotIn("학교 2곳", alert)
        self.assertEqual(result, "확인된 사실: 학교 2곳의 12월 1일 시설 점검 일정.")

    def test_uncertain_context_and_definite_only_source(self):
        self.assertTrue(any("확정된 사실처럼" in warning for warning in
                            review_result("시설 점검 안내", "일정 문의", "확인된 사실: 점검 일정")))
        self.assertFalse(any("확정된 사실처럼" in warning for warning in
                             review_result("시설 점검 날짜는 12월 1일로 확정되었습니다.", "",
                                           "확인된 사실: 시설 점검은 12월 1일로 확정되었습니다.")))
        self.assertFalse(any("확정된 사실처럼" in warning for warning in
                             review_result("시설 점검은 미확인", "", "시설 점검 일정은 확정되지 않았습니다.")))
    def test_korean_single_quoted_phrase_is_checked_with_particle(self):
        prompt = "제목을 '학교 안전'으로 작성해 주세요"
        self.assertTrue(any("학교 안전" in warning and "문구 확인" in warning
                            for warning in review_result(prompt, "", "제목을 안전으로 작성하세요")))
        self.assertFalse(any("문구 확인" in warning
                             for warning in review_result(prompt, "", '제목을 “학교 안전”으로 작성하세요')))
        self.assertFalse(any("문구 확인" in warning
                             for warning in review_result("Don't change the school's name", "", "Do not change the name")))

    def test_school_location_bound_changes_are_detected(self):
        warnings = review_result("학교 4곳 이상을 선정", "", "학교 4곳 이하를 선정")
        self.assertTrue(any("수량 범위 확인" in warning and "4곳" in warning for warning in warnings))
        self.assertFalse(any("수량 범위 확인" in warning
                             for warning in review_result("학교 4곳 이상", "", "학교 최소 4곳")))
    def test_changed_prohibition_gets_a_literal_warning_without_modifying_result(self):
        original = "회의록에서 결정 지어내지 말기"
        result = "회의록에서 결정 사항 제외"
        warnings = review_result(original, "", result)
        self.assertTrue(any("금지 조건 원문 확인 필요" in warning and original in warning
                            for warning in warnings))
        self.assertEqual(result, "회의록에서 결정 사항 제외")

    def test_prohibition_in_context_is_checked_but_unchanged_phrase_is_not_flagged(self):
        context = "참석자 이름을 추가하지 마세요"
        missing = review_result("회의록 작성", context, "회의 내용을 요약하세요")
        self.assertTrue(any("금지 조건 원문 확인 필요" in warning and context in warning
                            for warning in missing))
        kept = review_result("회의록 작성", context, "조건: 참석자 이름을 추가하지 마세요.")
        self.assertFalse(any("금지 조건 원문 확인 필요" in warning for warning in kept))

    def test_long_missing_prohibition_warning_is_bounded(self):
        prompt = "내용 " * 200 + "지어내지 말기"
        warnings = review_result(prompt, "", "간단하게 작성해 주세요.")
        warning = next(warning for warning in warnings if "금지 조건 원문 확인 필요" in warning)
        self.assertLess(len(warning), 200)

    def test_schedule_changes_warn_even_when_numbers_are_unchanged(self):
        for prompt, result, missing in (
            ("화요일에 연수 안내", "월요일에 연수 안내", "화요일"),
            ("11월 5일 회의", "5월 11일 회의", "11월 5일"),
            ("3시 30분에 시작", "30시 3분에 시작", "3시 30분"),
            ("회의는 09:30", "회의는 09:45", "09:30"),
            ("오전 9시 회의", "오후 9시 회의", "오전 9시"),
        ):
            with self.subTest(prompt=prompt):
                warnings = review_result(prompt, "", result)
                self.assertTrue(any("날짜·요일·시간 확인 필요" in warning and missing in warning
                                    for warning in warnings))

    def test_equivalent_schedule_spacing_does_not_warn(self):
        original = "2026년 11월 5일 화요일 오후 3시 30분 회의"
        for separator in ("", "\u00a0", "\u202f"):
            with self.subTest(separator=repr(separator)):
                result = f"2026년{separator}11월{separator}5일 화요일 오후{separator}3시{separator}30분 회의"
                warnings = review_result(original, "", result)
                self.assertFalse(any("날짜·요일·시간 확인 필요" in warning for warning in warnings))
                self.assertEqual(keep_original_details(original, result), (result, []))

    def test_schedule_comparison_does_not_join_lines(self):
        original = "11월 5일 회의"
        for separator in ("\n", "\r", "\u0085", "\u2028", "\u2029"):
            with self.subTest(separator=repr(separator)):
                warnings = review_result(original, "", f"11월{separator}5일 회의")
                self.assertTrue(any("날짜·요일·시간 확인 필요" in warning for warning in warnings))

    def test_checks_original_and_context_numbers_and_quoted_phrases(self):
        warnings = review_result('"사업명"을 3개 작성', '예산 30,000원, “중학생 대상”', '사업명을 30개 작성')
        self.assertTrue(any("3, 30,000" in item for item in warnings))
        self.assertTrue(any("중학생 대상" in item for item in warnings))
        self.assertTrue(any("문자 그대로만" in item for item in warnings))

    def test_same_fenced_text_and_possible_language_drift(self):
        original = "한국어로 현재 진행 상황을 정리하고 다음 할 일을 알려주세요."
        self.assertTrue(any("원문과 같습니다" in item for item in review_result(original, "", f"```text\n{original}\n```")))
        english = "Please summarize the current project progress and identify practical next steps for the team in detail."
        self.assertTrue(any("언어" in item for item in review_result(original, "", english)))
        self.assertFalse(any("언어" in item for item in review_result(original, "", f"```text\n{english}\n```")))

    def test_preserved_values_are_not_falsely_flagged(self):
        warnings = review_result('"사업명"을 3개 작성', '예산 30,000원', '사업명을 3개 작성하고 예산은 30,000원으로 지키세요.')
        self.assertFalse(any("숫자 확인" in item or "문구 확인" in item for item in warnings))

    def test_quantity_bounds_warn_on_exact_limit_changes(self):
        pairs = [
            ("세 문장으로 설명", "세 문장 이내로 설명"),
            ("3개 작성", "최대 3개 작성"),
            ("3개 이하 작성", "3개 작성"),
            ("3문장으로 작성", "세 문장 이내로 작성"),
            ("3쪽 이상 작성", "3쪽 이하 작성"),
            ("3개 이하", "3개 미만"),
            ("3개 이상", "3개 초과"),
            ("두 항목으로", "약 2항목으로"),
        ]
        for original, result in pairs:
            with self.subTest(original=original, result=result):
                self.assertTrue(any("수량 범위 확인" in w for w in review_result(original, "", result)))

    def test_quantity_bounds_dont_warn_when_equivalent_or_ambiguous(self):
        pairs = [
            ("최대 3개", "3개 이하"),
            ("3개 이내", "최대 세 개"),
            ("최소 2줄", "두 줄 이상"),
            ("세 문장", "3개 이내"),
            ("1.5개", "5개 이내"),
            ("3,001개", "1개 이내"),
            ("3개", "최대 3개이면서 정확히 3개"),
            ("3개\n이내 자료 참고", "3개"),
        ]
        for original, result in pairs:
            with self.subTest(original=original, result=result):
                self.assertFalse(any("수량 범위 확인" in w for w in review_result(original, "", result)))

    def test_quantity_bounds_check_context_without_changing_the_result(self):
        result = "안내문을 2문장 이내로 작성해 주세요."
        warnings = review_result("안내문을 작성", "두 문장으로", result)
        self.assertTrue(any("2문장" in w and "수량 범위 확인" in w for w in warnings))
        self.assertEqual(result, "안내문을 2문장 이내로 작성해 주세요.")

    def test_long_warning_ignores_structured_prompts_but_flags_finished_documents(self):
        structured = "가정통신문을 작성해 주세요.\n" + "- 형식: [항목]\n" * 25
        self.assertLess(len(structured), 400)
        self.assertFalse(any("많이 깁니다" in w for w in review_result("안내문 써줘", "", structured)))
        finished = "학부모님 안녕하십니까. " * 60
        self.assertTrue(any("많이 깁니다" in w for w in review_result("안내문 써줘", "", finished)))

    def test_extremely_long_number_does_not_break_review(self):
        long_count = "1" * 5000 + "개"
        self.assertTrue(review_result(long_count, "", long_count + " 이내"))


class KeepOriginalDetailsTests(unittest.TestCase):
    def test_single_quoted_phrase_is_restored_if_omitted(self):
        prompt = "제목을 '학교 안전'으로 작성해 주세요"
        text, notes = keep_original_details(prompt, "제목을 안전으로 작성해 주세요.")
        self.assertIn(f"{KEEP_ORIGINAL_HEADING}\n{prompt}", text)
        self.assertTrue(notes)

    def test_source_with_colon_fields_is_preserved(self):
        source = "학교 상황: 교실 누수\n조치 요청: 안전 점검"
        for head in ("아래 자료를 요약해 주세요:", "아래 자료를 요약해 주세요."):
            with self.subTest(head=head):
                prompt = head + "\n" + source
                self.assertEqual(_material(prompt), source)
                text, notes = keep_original_details(prompt, "시설 현황을 요약해 주세요.")
                self.assertTrue(text.endswith(f"{KEEP_MATERIAL_HEADING}\n{source}"))
                self.assertTrue(notes)
        self.assertEqual(_material("조건을 정리해 주세요\n형식: 표\n대상: 교사"), "")
        self.assertEqual(_material("아래 자료를 요약해 주세요:\n내용: [자료]"), "")

    def test_explicit_source_fills_only_generic_placeholder_verbatim(self):
        prompt = ("가상 회의록 정리 요청을 만들어 줘. 11월 3일 학교 3곳.\n"
                  "자료: 일정은 11월 18일로 확정. 장소는 미정. 예산은 논의 중.")
        result = "11월 3일 학교 3곳 회의록을 작성해 주세요.\n자료: [자료 붙여 넣기]"
        material = "일정은 11월 18일로 확정. 장소는 미정. 예산은 논의 중."
        text, notes = keep_original_details(prompt, result)
        self.assertEqual(text, result.replace("[자료 붙여 넣기]", material))
        self.assertEqual(len(notes), 1)
        self.assertNotIn(KEEP_ORIGINAL_HEADING, text)
        duplicated = result + "\n다른 자료: [자료 붙여 넣기]"
        text, _ = keep_original_details(prompt, duplicated)
        self.assertIn("[자료 붙여 넣기]", text)
        self.assertIn(material, text)
        self.assertEqual(keep_original_details(prompt, result, check_details=False), (result, []))

    def test_explicit_source_fills_a_single_source_specific_paste_slot(self):
        prompt = ("가상 회의록 요청. 11월 3일 학교 3곳.\n"
                  "자료: 일정은 11월 18일로 확정. 장소는 미정. 예산은 논의 중.")
        material = "일정은 11월 18일로 확정. 장소는 미정. 예산은 논의 중."
        result = "11월 3일 학교 3곳 회의록을 작성해 주세요.\n자료:\n[가상 회의록 메모 붙여 넣기]"
        text, notes = keep_original_details(prompt, result)
        self.assertEqual(text, result.replace("[가상 회의록 메모 붙여 넣기]", material))
        self.assertTrue(notes)

    def test_source_does_not_fill_ambiguous_or_non_source_placeholders(self):
        prompt = "회의 정리.\n자료: 논의 중인 안건입니다."
        material = "논의 중인 안건입니다."
        outputs = (
            "[가상 회의록 메모 붙여 넣기]\n[담당자 연락처]",
            "[자료 붙여 넣기]\n[원문 붙여 넣기]",
            "[담당자 기록 붙여 넣기]",
            "[연락처 붙여 넣기]",
            "[제출 기한]",
        )
        for output in outputs:
            with self.subTest(output=output):
                text, notes = keep_original_details(prompt, output)
                self.assertTrue(text.startswith(output))
                self.assertIn(f"{KEEP_MATERIAL_HEADING}\n{material}", text)
                self.assertTrue(notes)
        self.assertEqual(keep_original_details(prompt, "[가상 회의록 메모 붙여 넣기]", check_details=False),
                         ("[가상 회의록 메모 붙여 넣기]", []))
    def test_changed_schedule_appends_original_for_the_first_request_only(self):
        for prompt, result in (("화요일에 안내해 줘", "월요일에 안내해 주세요."),
                               ("11월 5일 회의 안내", "5월 11일 회의를 안내해 주세요."),
                               ("3시 30분 회의", "30시 3분 회의를 안내해 주세요.")):
            with self.subTest(prompt=prompt):
                text, notes = keep_original_details(prompt, result)
                self.assertEqual(text, f"{result}\n\n{KEEP_ORIGINAL_HEADING}\n{prompt}")
                self.assertEqual(len(notes), 1)
                self.assertEqual(keep_original_details(prompt, result, check_details=False), (result, []))

    def test_strips_label_fence_and_wrapping_quotes(self):
        for raw in ("```text\n안내문을 작성해 주세요.\n```",
                    "**Prompt to send to an AI:**\n\n\"안내문을 작성해 주세요.\"",
                    "“안내문을 작성해 주세요.”"):
            with self.subTest(raw=raw):
                self.assertEqual(keep_original_details("안내문 써줘", raw), ("안내문을 작성해 주세요.", []))
        kept = "```python\nprint(1)\n```"
        self.assertEqual(keep_original_details("```코드``` 설명해 줘", kept)[0], kept)
        inner_quotes = '"재량휴업일"과 "등교일"을 구분해 주세요.'
        self.assertEqual(keep_original_details("휴업일 안내", inner_quotes)[0], inner_quotes)

    def test_lost_or_changed_literals_append_the_original_request(self):
        cases = (("엑셀에서 학교 4개 합계", "엑셀에서 학교 3개 합계를 구해 주세요."),
                 ('"재량휴업일" 안내문 써줘', "휴업일 안내문을 작성해 주세요."),
                 ("세 문장으로 요약해 줘", "세 문장 이내로 요약해 주세요."))
        for prompt, result in cases:
            with self.subTest(prompt=prompt):
                text, notes = keep_original_details(prompt, result)
                self.assertEqual(text, f"{result}\n\n{KEEP_ORIGINAL_HEADING}\n{prompt}")
                self.assertEqual(len(notes), 1)

    def test_missing_material_is_appended_verbatim(self):
        cases = (("다음 안내문을 영어로 번역해 주세요: 다음 주 화요일은 재량휴업일입니다.",
                  "다음 주 화요일은 재량휴업일입니다."),
                 ("아래 글을 쉬운 말로 바꿔 줘\n가상 안내문입니다.\n학부모 동의가 필요합니다.",
                  "가상 안내문입니다.\n학부모 동의가 필요합니다."))
        for prompt, material in cases:
            with self.subTest(prompt=prompt[:20]):
                text, notes = keep_original_details(prompt, "안내문을 바꿔 주세요.\n- 말투: 쉬운 말")
                self.assertTrue(text.endswith(f"{KEEP_MATERIAL_HEADING}\n{material}"))
                self.assertEqual(len(notes), 1)
                kept = f"안내문을 바꿔 주세요.\n자료:\n{material}"
                self.assertEqual(keep_original_details(prompt, kept), (kept, []))

    def test_revisions_and_preserved_results_are_left_unchanged(self):
        result = "학교 3개 합계를 구해 주세요."
        self.assertEqual(keep_original_details("학교 4개 합계", result, check_details=False), (result, []))
        kept = "학교 4개의 합계를 구하는 방법을 알려 주세요.\n- 형식: 단계별"
        self.assertEqual(keep_original_details("학교 4개 합계 알려줘", kept), (kept, []))

    def test_template_fields_are_never_treated_as_material(self):
        root = Path(__file__).resolve().parents[1] / "assets"
        texts = []
        for name in ("prompt_templates.json", "seobu_office_templates.json"):
            with (root / name).open(encoding="utf-8") as file:
                texts.extend(item["text"] for item in json.load(file))
        self.assertGreater(len(texts), 50)
        for text in texts:
            filled = re.sub(r"\[[^\]]*\]", "가상 내용", text)
            with self.subTest(text=text[:30]):
                self.assertEqual(_material(text), "")
                self.assertEqual(_material(filled), "")


if __name__ == "__main__":
    unittest.main()
