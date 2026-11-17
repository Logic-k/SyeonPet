"""공문서 표기 검수 — 행정 문서에서 흔히 틀리는 표기를 규칙으로 찾아 낸다.

각 규칙은 "찾은 곳 + 왜 문제인지 + 어떻게 고치는지"를 돌려준다.
날짜·시간·금액·전화번호·붙임 표기처럼 기계적으로 확인 가능한 것만 다루고,
문법·맞춤법 같은 판단이 필요한 것은 건드리지 않는다(정직성 원칙).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Finding:
    """One rule violation, tied to a source line."""

    rule_id: str
    line_no: int            # 1-based line number
    found: str              # the offending text as written
    message: str            # what is wrong
    suggestion: str         # how to write it instead


# --- 날짜: 공문서는 "2026년 11월 17일" 형태로 쓴다 ---------------------------------

_DATE_DOT = re.compile(r"(?<!\d)\d{4}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}(?!\d)")
_DATE_DASH = re.compile(r"(?<!\d)\d{4}-\d{1,2}-\d{1,2}(?!\d)")
_DATE_SLASH = re.compile(r"(?<!\d)\d{4}/\d{1,2}/\d{1,2}(?!\d)")
_DATE_SHORT_YEAR = re.compile(r"(?<!\d)\d{2}년\s*\d{1,2}월\s*\d{1,2}일?(?!\d)")
_DATE_OK = re.compile(r"\d{4}년\s*\d{1,2}월\s*\d{1,2}일")


def _check_dates(text: str) -> list[Finding]:
    out: list[Finding] = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in _DATE_DOT.finditer(line):
            out.append(Finding("date_dot", i, m.group(),
                               "날짜는 점(.)이 아니라 '년 월 일'로 씁니다.",
                               m.group().replace(".", "년 ", 1) + " (예: 2026년 11월 17일)"))
        for m in _DATE_DASH.finditer(line):
            y, mo, d = m.group().split("-")
            out.append(Finding("date_dash", i, m.group(),
                               "날짜는 하이픈(-)이 아니라 '년 월 일'로 씁니다.",
                               f"{y}년 {int(mo)}월 {int(d)}일"))
        for m in _DATE_SLASH.finditer(line):
            y, mo, d = m.group().split("/")
            out.append(Finding("date_slash", i, m.group(),
                               "날짜는 슬래시(/)가 아니라 '년 월 일'로 씁니다.",
                               f"{y}년 {int(mo)}월 {int(d)}일"))
        for m in _DATE_SHORT_YEAR.finditer(line):
            out.append(Finding("date_short_year", i, m.group(),
                               "연도는 두 자리가 아니라 네 자리로 씁니다.",
                               "(예: 2026년 " + m.group()[3:]))
    return out


# --- 시간: '오후 2시' 또는 '14시'. 혼용은 안 된다 ---------------------------------

_TIME_BAD = re.compile(r"오[전후]\s*\d{2}시|오[전후]\s*\d{1,2}시\s*\d{2}분"
                       r"|[AP]M\s*\d{1,2}시|\d{1,2}\s*[AP]M")


def _check_times(text: str) -> list[Finding]:
    out: list[Finding] = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in _TIME_BAD.finditer(line):
            found = m.group()
            if re.match(r"오[전후]\s*\d{2}시|오[전후]\s*\d{1,2}시\s*\d{2}분", found):
                out.append(Finding("time_mixed", i, found,
                                   "'오전/오후'에는 12시간제 숫자만 옵니다 (오후 2시 O, 오후 14시 X).",
                                   "오후 2시 또는 14시"))
            else:
                out.append(Finding("time_english", i, found,
                                   "공문서 시간은 AM/PM 대신 '오전/오후' 또는 24시간제를 씁니다.",
                                   "오후 2시 또는 14시"))
    return out


# --- 금액: 네 자리 이상은 천 단위 쉼표 ---------------------------------------------

_AMOUNT_NOCOMMA = re.compile(r"(?<![\d,])(\d{5,})\s*(원|만원|천원|백만원|억원)")


def _check_amounts(text: str) -> list[Finding]:
    out: list[Finding] = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in _AMOUNT_NOCOMMA.finditer(line):
            num, unit = m.group(1), m.group(2)
            out.append(Finding("amount_comma", i, m.group(),
                               "다섯 자리 이상 금액은 천 단위 쉼표를 넣어 읽기 쉽게 씁니다.",
                               f"{int(num):,}{unit}"))
    return out


# --- 붙임: "붙임 1. 제목 1부." 형태 ------------------------------------------------

_ATTACH_BAD = re.compile(r"(?:첨부\s*[:\uff1a]|\[\s*붙임\s*\]|붙임\s*[:\uff1a])(?!\s*\d)")


def _check_attachments(text: str) -> list[Finding]:
    out: list[Finding] = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in _ATTACH_BAD.finditer(line):
            out.append(Finding("attachment_form", i, m.group(),
                               "붙임 표기는 '붙임 1. 제목 1부.' 형태로 씁니다.",
                               "붙임 1. (문서 제목) 1부."))
    return out


# --- 전화번호: 지역번호-국번-번호 ---------------------------------------------------

_PHONE_GLUE = re.compile(r"(?<!\d)(0\d{8,10})(?!\d)")


def _check_phones(text: str) -> list[Finding]:
    out: list[Finding] = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in _PHONE_GLUE.finditer(line):
            digits = m.group(1)
            if digits.startswith("02") and len(digits) in (9, 10):
                fixed = f"02-{digits[2:-4]}-{digits[-4:]}"
            elif len(digits) in (10, 11):
                fixed = f"{digits[:3]}-{digits[3:-4]}-{digits[-4:]}"
            else:
                continue
            out.append(Finding("phone_glue", i, digits,
                               "전화번호는 하이픈으로 구분해 씁니다.",
                               fixed))
    return out


# --- 종결 표기: 본문 끝은 "끝." ------------------------------------------------------


def _check_ending(text: str) -> list[Finding]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    tail = "\n".join(lines[-3:])
    if re.search(r"끝\s*\.?\s*$", tail) or re.search(r"이상\s*\.?\s*$", tail):
        return []
    # Only flag texts that look like official documents (have 수신/제목/본문 markers)
    head = "\n".join(lines[:12])
    if not re.search(r"수신|참조|제목|기안|보고서|계획서", head):
        return []
    return [Finding("ending", len(lines), lines[-1].strip()[:40],
                    "공문서 본문 끝에는 '끝.' 또는 '이상.'을 붙입니다.",
                    "끝.")]


_RULE_CHECKS = (_check_dates, _check_times, _check_amounts,
                _check_attachments, _check_phones, _check_ending)

RULE_NAMES = {
    "date_dot": "날짜 표기(점)",
    "date_dash": "날짜 표기(하이픈)",
    "date_slash": "날짜 표기(슬래시)",
    "date_short_year": "연도 두 자리",
    "time_mixed": "시간 표기 혼용",
    "time_english": "AM/PM 표기",
    "amount_comma": "금액 쉼표",
    "attachment_form": "붙임 표기",
    "phone_glue": "전화번호 붙여쓰기",
    "ending": "종결 표기 없음",
}


def review_official_document(text: str) -> list[Finding]:
    """Run every notation rule over the document body.

    Returns findings in document order; an empty list means the text passed
    every mechanical notation check (it says nothing about wording quality).
    """
    if not text or not text.strip():
        return []
    findings: list[Finding] = []
    for check in _RULE_CHECKS:
        findings.extend(check(text))
    findings.sort(key=lambda f: (f.line_no, f.rule_id))
    return findings


def summarize_findings(findings: list[Finding]) -> str:
    """Human summary line for status display."""
    if not findings:
        return "기계적으로 확인 가능한 표기 규칙 위반이 없습니다."
    from collections import Counter
    counts = Counter(f.rule_id for f in findings)
    parts = [f"{RULE_NAMES.get(k, k)} {v}건" for k, v in counts.most_common()]
    return f"{len(findings)}건 발견 — " + ", ".join(parts)


def render_report(text: str, findings: list[Finding]) -> str:
    """Full readable report with per-line fixes."""
    if not findings:
        return ("[공문서 표기 검수 결과]\n\n"
                "확인한 표기 규칙(날짜·시간·금액·전화번호·붙임·종결 표기)에서 "
                "문제를 찾지 못했습니다.\n\n"
                "※ 맞춤법·문장 다듬기는 검사하지 않습니다.")
    out = [f"[공문서 표기 검수 결과] — {len(findings)}건", ""]
    for f in findings:
        out.append(f"{f.line_no}행 · {RULE_NAMES.get(f.rule_id, f.rule_id)}")
        out.append(f"  쓰인 것 : {f.found}")
        out.append(f"  이유     : {f.message}")
        out.append(f"  고침 예시: {f.suggestion}")
        out.append("")
    return "\n".join(out)
