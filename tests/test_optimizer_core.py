import unittest

from optimizer_core import create_offline_prompt, review_result, utf16_length


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

    def test_extremely_long_number_does_not_break_review(self):
        long_count = "1" * 5000 + "개"
        self.assertTrue(review_result(long_count, "", long_count + " 이내"))


if __name__ == "__main__":
    unittest.main()
