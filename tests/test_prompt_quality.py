import unittest

from prompt_quality import condition_report, literal_diff, missing_conditions


class MissingConditionTests(unittest.TestCase):
    def test_all_fields_are_optional_questions_when_empty(self):
        # Given / When
        hints = missing_conditions("")
        # Then
        self.assertEqual({hint["field"] for hint in hints},
                         {"purpose", "audience", "format", "deadline"})
        self.assertTrue(all(isinstance(hint["question"], str) and hint["question"] for hint in hints))

    def test_korean_task_and_context_supply_clues(self):
        # Given / When
        hints = missing_conditions("초보자를 위해 설명을 세 문장으로 작성해 줘", "11월 5일까지")
        # Then
        self.assertEqual(hints, [])

    def test_english_clues_supply_fields(self):
        # Given / When
        hints = missing_conditions("Write an email for customers by tomorrow.")
        # Then
        self.assertEqual(hints, [])

    def test_no_deadline_is_invented_for_simple_request(self):
        # Given / When
        hints = missing_conditions("요약해 줘")
        # Then
        self.assertEqual({hint["field"] for hint in hints}, {"audience", "format", "deadline"})

    def test_school_staff_and_parent_audiences_do_not_prompt_again(self):
        for audience in ("학부모", "교직원", "직원", "교사", "선생님"):
            with self.subTest(audience=audience):
                hints = missing_conditions(f"{audience} 연수 안내를 세 문장으로 작성", "11월 5일")
                self.assertNotIn("audience", {hint["field"] for hint in hints})

    def test_applied_purpose_answer_is_not_asked_again(self):
        hints = missing_conditions("", "purpose: 연수 홍보")
        self.assertNotIn("purpose", {hint["field"] for hint in hints})

    def test_inquiry_roles_are_optional_and_only_for_inquiry_response(self):
        for prompt in ("학교가 교육청에 문의했고 답변 초안을 요청해 줘.",
                       "학부모 질의에 대한 회신을 준비해 줘.",
                       "민원에 대한 답변을 준비해 줘."):
            with self.subTest(prompt=prompt):
                hints = missing_conditions(prompt)
                role = next(h for h in hints if h["field"] == "inquiry_roles")
                self.assertIn("미정", role["question"])
        for prompt in ("안내문에 문의처와 답변 기간을 적어 주세요.",
                       "문의 전화와 답변 시간을 알려 주세요.",
                       "문의번호와 답변 기한을 적어 주세요.",
                       "학교의 문의를 분류해 주세요."):
            with self.subTest(prompt=prompt):
                self.assertNotIn("inquiry_roles", {h["field"] for h in missing_conditions(prompt)})

    def test_applied_inquiry_roles_are_not_asked_again(self):
        prompt = "학교 문의에 대한 회신 초안을 작성해 줘."
        for value in ("문의자: 학교 / 답변자: 교육청 / 수신자: 학교", "미정"):
            with self.subTest(value=value):
                self.assertNotIn("inquiry_roles", {h["field"] for h in
                    missing_conditions(prompt, "inquiry_roles: " + value)})
        self.assertIn("inquiry_roles", {h["field"] for h in
                      missing_conditions(prompt, "inquiry_roles: ")})


class LiteralDiffTests(unittest.TestCase):
    def test_unicode_replacement_uses_python_offsets(self):
        # Given / When
        edits = literal_diff("😀한글", "😀한국")
        # Then
        self.assertEqual(edits, [
            {"tag": "equal", "before_start": 0, "before_end": 2, "after_start": 0, "after_end": 2},
            {"tag": "replace", "before_start": 2, "before_end": 3, "after_start": 2, "after_end": 3},
        ])

    def test_insert_delete_and_empty_inputs(self):
        for before, after, tag in (("", "새😀", "insert"), ("새😀", "", "delete"), ("", "", None)):
            with self.subTest(before=before, after=after):
                # Given / When
                edits = literal_diff(before, after)
                # Then
                self.assertEqual([edit["tag"] for edit in edits], [tag] if tag else [])
                if edits:
                    self.assertEqual(edits[0]["before_end"], len(before))
                    self.assertEqual(edits[0]["after_end"], len(after))

    def test_long_identical_input_is_one_equal_span(self):
        # Given
        text = "가😀" * 20000
        # When
        edits = literal_diff(text, text)
        # Then
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["tag"], "equal")
        self.assertEqual(edits[0]["after_end"], len(text))


class ConditionReportTests(unittest.TestCase):
    def test_quote_with_korean_particle_and_english_contractions(self):
        report = condition_report("제목을 '학교 안전'으로", "", "제목을 학교 건강으로")
        self.assertTrue(any(item["kind"] == "quote" and item["literal"] == "학교 안전"
                            and item["status"] == "missing" for item in report))
        self.assertFalse(any(item["kind"] == "quote" for item in
                             condition_report("Don't change the school's name", "", "Do not change it")))

    def test_school_places_quantity_uses_whole_bound(self):
        report = condition_report("학교 4곳 이상", "", "학교 4곳 이하")
        self.assertTrue(any(item["kind"] == "number" and item["literal"] == "4곳 이상"
                            and item["status"] == "missing" for item in report))
    def test_schedule_unicode_horizontal_spaces_are_equivalent_but_newlines_are_not(self):
        original = "11월 5일 오후 3시 30분"
        report = condition_report(original, "", "11월\u202f5일 오후\u00a03시\u202f30분")
        self.assertTrue(all(item["status"] == "present" for item in report))
        report = condition_report(original, "", "11월\u20285일 오후 3시 30분")
        self.assertTrue(any(item["kind"] == "date" and item["status"] == "missing" for item in report))

    def test_weekday_and_time_are_compared_as_complete_literals(self):
        report = condition_report("화요일 오후 3시 30분", "회의 09:30", "월요일 오후 3시 09:30")
        self.assertEqual({(item["kind"], item["literal"], item["status"], item["source"]) for item in report},
                         {("weekday", "화요일", "missing", "original"),
                          ("time", "오후 3시 30분", "missing", "original"),
                          ("time", "09:30", "present", "context")})

    def test_whole_date_quantity_bound_and_spelled_count_are_reported(self):
        # Given
        original = "11월 5일, 30명 이내, 세 문장"
        # When
        report = condition_report(original, "", original)
        # Then
        self.assertEqual({(item["kind"], item["literal"], item["status"]) for item in report},
                         {("date", "11월 5일", "present"), ("number", "30명 이내", "present"),
                          ("number", "세 문장", "present")})

    def test_changed_conditions_are_missing(self):
        cases = (
            ("결정 지어내지 말기", "결정 사항 제외", "prohibition"),
            ("11월 5일", "11월 6일", "date"),
            ("2026-11-05", "2026-11-06", "date"),
            ("30명 이내", "30명 이상", "number"),
            ("30명", "130명", "number"),
            ("세 문장", "네 문장", "number"),
            ("at most 30 words", "at least 30 words", "number"),
            ('"안녕 세상"', '"안녕 세계"', "quote"),
            ("개인정보를 포함하지 마세요.", "개인정보를 포함하세요.", "prohibition"),
            ("Do not use names.", "Use names.", "prohibition"),
        )
        for original, result, kind in cases:
            with self.subTest(original=original):
                # Given / When
                report = condition_report(original, "", result)
                # Then
                self.assertTrue(any(item["kind"] == kind and item["status"] == "missing"
                                    for item in report))

    def test_punctuation_and_quote_style_are_not_missing_conditions(self):
        # Given / When
        report = condition_report('“안녕 세상”. 개인정보를 포함하지 마세요!',
                                  "", '"안녕 세상"! 개인정보를 포함하지 마세요.')
        # Then
        self.assertTrue(report)
        self.assertTrue(all(item["status"] == "present" for item in report))

    def test_context_and_original_keep_separate_provenance(self):
        # Given / When
        report = condition_report("30명 이내", "30명 이내, 11월 5일", "30명 이내")
        # Then
        self.assertEqual({(item["source"], item["literal"], item["status"]) for item in report},
                         {("original", "30명 이내", "present"), ("context", "30명 이내", "present"),
                          ("context", "11월 5일", "missing")})

    def test_plain_numbers_are_not_matched_inside_other_numbers(self):
        # Given / When
        report = condition_report("42, 3.14, 50%", "", "142, 3.15, 50%")
        # Then
        self.assertEqual({item["literal"]: item["status"] for item in report},
                         {"42": "missing", "3.14": "missing", "50%": "present"})

    def test_empty_and_punctuation_only_input_have_no_conditions(self):
        # Given / When
        report = condition_report("", '"" “...” !!!', "")
        # Then
        self.assertEqual(report, [])

    def test_long_input_deduplicates_conditions_without_losing_sources(self):
        # Given
        text = ("일반 자료입니다. " * 5000) + "11월 5일, 30명 이내, 30명 이내"
        # When
        report = condition_report(text, "30명 이내", text)
        # Then
        self.assertEqual(len(report), 3)
        self.assertTrue(all(item["status"] == "present" for item in report))


if __name__ == "__main__":
    unittest.main()
