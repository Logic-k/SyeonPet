"""Offline hints and literal checks, not semantic analysis or text rewriting.

Hints are optional questions: a missing clue does not imply a requirement.
Reports compare exact condition text (excluding surrounding punctuation), so
paraphrases can be marked missing even when their meaning is unchanged.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Final, Literal, TypedDict

from optimizer_core import _DATE, _QUOTE, _QUANTITY, _TIME, _WEEKDAY, _normalize_schedule, _prohibitions


class MissingCondition(TypedDict):
    field: Literal["purpose", "audience", "format", "deadline", "inquiry_roles"]
    question: str


class LiteralEdit(TypedDict):
    tag: Literal["equal", "insert", "delete", "replace"]
    before_start: int
    before_end: int
    after_start: int
    after_end: int


class ConditionFinding(TypedDict):
    kind: Literal["number", "date", "time", "weekday", "quote", "prohibition"]
    literal: str
    source: Literal["original", "context"]
    status: Literal["present", "missing"]


_CLUES: Final[tuple[tuple[MissingCondition, str], ...]] = (
    ({"field": "purpose", "question": "필요하다면, 무엇을 하려는 요청인지 알려 주실까요?"},
     r"목적|용도|위해|하려|작성|써\s*줘|만들|설명|요약|번역|비교|분석|검토|수정|추천|정리|"
     r"\b(?:purpose|write|create|explain|summari[sz]e|translate|compare|analy[sz]e|review|fix|help|draft)\b"),
    ({"field": "audience", "question": "필요하다면, 누구를 위한 결과인지 알려 주실까요?"},
     r"독자|대상|청중|고객|사용자|초보자|입문자|전문가|학생|어린이|팀원|학부모|교직원|직원|교사|선생님|"
     r"\b(?:audience|readers?|beginners?|students?|customers?|children|experts?)\b"),
    ({"field": "format", "question": "원하는 결과 형식이 있다면 알려 주실까요?"},
     r"형식|표로|표\s*형태|목록|글머리|보고서|이메일|메일|문장|문단|편지|"
     r"\b(?:format|table|list|json|markdown|csv|email|report|bullet|paragraphs?|sentences?)\b"),
    ({"field": "deadline", "question": "기한이 필요한 작업이라면 언제까지인지 알려 주실까요?"},
     r"기한|마감|까지|오늘|내일|모레|이번\s*주|다음\s*주|"
     r"\b(?:deadline|due|by|today|tomorrow)\b"),
)
_ENGLISH_QUANTITY: Final = re.compile(
    r"(?<![\w.,])(?:(?:at most|at least|up to|under|over|about|maximum|minimum)[ \t]+)?"
    r"\d+(?:[.,]\d+)*[ \t]+(?:people|persons?|words?|sentences?|paragraphs?|pages?|items?|characters?)\b",
    re.IGNORECASE,
)
_NUMBER: Final = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*(?:%|퍼센트)?(?!\d|[.,]\d)")


def missing_conditions(prompt: str, context: str = "") -> list[MissingCondition]:
    """Suggest only fields without recognizable Korean/English task clues."""
    text = prompt + "\n" + context
    hints = [dict(hint) for hint, pattern in _CLUES
             if not re.search(pattern, text, re.IGNORECASE)
             and not (hint["field"] == "deadline" and _DATE.search(text))]
    # Offer a question, not an inferred assignment: the source may name an
    # unusual sender/recipient or leave one unknown. A contact label alone is
    # not an inquiry. An explicitly applied answer is not requested again.
    if (re.search(r"문의(?![ \t]*(?:처|전화|번호|메일|이메일))|질의|민원", text)
            and re.search(r"답변|회신", text)
            and not re.search(r"(?m)^inquiry_roles:\s*\S", context)):
        hints.append({"field": "inquiry_roles",
                      "question": "문의·답변 역할 (미정 가능)"})
    return hints


def literal_diff(before: str, after: str) -> list[LiteralEdit]:
    """Return contiguous difflib opcodes with Python code-point offsets.

    Equal spans are included; offsets are not Qt/JavaScript UTF-16 positions.
    The default difflib autojunk heuristic keeps large pasted input practical.
    """
    return [
        {"tag": tag, "before_start": i, "before_end": j,
         "after_start": a, "after_end": b}
        for tag, i, j, a, b in SequenceMatcher(None, before, after).get_opcodes()
    ]


def _literals(text: str) -> list[tuple[Literal["number", "date", "time", "weekday", "quote", "prohibition"], str]]:
    """Extract bounded recognizable literals, retaining whole quantities/dates."""
    found: list[tuple[int, Literal["number", "date", "time", "weekday", "quote", "prohibition"], str]] = []
    occupied: list[tuple[int, int]] = []
    for match in _DATE.finditer(text):
        found.append((match.start(), "date", match.group()))
        occupied.append(match.span())
    for kind, pattern in (("time", _TIME), ("weekday", _WEEKDAY)):
        for match in pattern.finditer(text):
            found.append((match.start(), kind, match.group()))
            occupied.append(match.span())
    for pattern in (_QUANTITY, _ENGLISH_QUANTITY, _NUMBER):
        for match in pattern.finditer(text):
            if not any(start < match.end() and match.start() < end for start, end in occupied):
                found.append((match.start(), "number", match.group().strip()))
                occupied.append(match.span())
    for match in _QUOTE.finditer(text):
        literal = next(group for group in match.groups() if group is not None)
        if any(character.isalnum() for character in literal):
            found.append((match.start(), "quote", literal))
    for start, clause in _prohibitions(text):
        found.append((start, "prohibition", clause))
    return list(dict.fromkeys((kind, literal) for _, kind, literal in sorted(found)))


def condition_report(original: str, context: str, result: str) -> list[ConditionFinding]:
    """Report exact literal presence, separately for each supplied source.

    Dates and quantities must match a whole extracted condition: ``30명`` is
    not present in ``130명``, and ``30명 이내`` differs from ``30명 이상``.
    Schedule literals ignore horizontal whitespace and case. Quotes compare
    their content, not quote style. Prohibitions compare a sentence/comma-
    delimited clause without terminal punctuation.
    """
    output = set(_literals(result))
    output_schedules = {(kind, _normalize_schedule(literal)) for kind, literal in output
                        if kind in ("date", "time", "weekday")}
    findings: list[ConditionFinding] = []
    sources: tuple[tuple[Literal["original", "context"], str], ...] = (
        ("original", original), ("context", context),
    )
    for source, text in sources:
        for kind, literal in _literals(text):
            present = (kind, literal) in output
            if kind in ("date", "time", "weekday"):
                present = (kind, _normalize_schedule(literal)) in output_schedules
            if kind == "quote":
                present = literal in result
            findings.append({"kind": kind, "literal": literal, "source": source,
                             "status": "present" if present else "missing"})
    return findings
