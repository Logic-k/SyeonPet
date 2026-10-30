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
