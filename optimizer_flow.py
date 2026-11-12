"""Guided 'diagnose → ask → completed prompt' flow for education-office requests.

Type classification, missing-condition derivation, question building and
prompt assembly all run locally with a rule table — nothing is transmitted.
Optional AI add-ons (supplementary questions, refine) run only after the
user's explicit per-request consent, matching the app's existing AI policy.

This module is an original implementation of a diagnose/ask-back/completion
workflow. Screen texts, question wording and example chips are written for
this app; only the functional contracts (section labels, honesty rules,
forbidden-expression list) are reproduced.
"""
from __future__ import annotations

import html
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QPlainTextEdit,
                               QScrollArea, QSlider, QStackedWidget,
                               QTextBrowser, QTextEdit, QVBoxLayout, QWidget)

from optimizer_core import utf16_length

CONFIRM_NEEDED = "[확인 필요]"

# Forbidden "we will report/confirm later" expressions, per the output contract
# for report-type documents. Functional rule content, kept verbatim.
_FORBIDDEN_LATER = ("승인 후 보고", "승인 이후 보고", "추후 보고", "후속 보고", "별도 보고",
                    "보고드리겠습니다", "보완 예정입니다", "보고 예정입니다",
                    "재보고가 필요합니다", "확인 후 보고", "담당 부서 확인 후")

_MAX_INPUT = 30000
_MAX_ANSWER = 2000
_MAX_QUESTIONS = 5
_MAX_PREF_ENTRIES = 100
_PREF_MIN_USES = 2
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"

# ---------------------------------------------------------------------------
# Input-condition extraction (never re-ask what the input already states)
# ---------------------------------------------------------------------------

_AUDIENCE = re.compile(
    r"([가-힣A-Za-z]{1,12}님)(?=\s*(?:께|에게|한테|께서|님께))|"
    r"(과장|팀장|부장|교장|교감|국장|차장|임원|장관|주무관|담당자|실장|소장|본부장)"
    r"(?=\s|께|에게|한테|,|\.|$)|"
    r"\b(?:principal|director|manager|chief|head|boss)\b", re.IGNORECASE)
_RECIPIENT = re.compile(r"(학부모|민원인|학생|교직원|직원|교사|선생님|보호자|신청인|청구인|주민)")
_TONE = re.compile(
    r"(격식체|격식|공식적?인?\s*(?:어조|톤|문체)|공문체|정중|공손|딱딱|부드럽|친근|따뜻|간결|"
    r"존댓말|반말|부드러운|차분|formal|polite|casual|friendly)", re.IGNORECASE)
_LENGTH = re.compile(
    r"(A4\s*[0-9한두세네반]*\s*장|"
    r"[한두세네다섯열]\s*장(?:\s*(?:이내|정도|내외|분량))?|"
    r"\d+\s*장(?:\s*(?:이내|정도|내외|분량))?|"
    r"\d+\s*(?:쪽|페이지|p)\b(?:\s*(?:이내|정도|분량))?|"
    r"\d+\s*(?:줄|문장|단락|문단|글자|자)\b(?:\s*(?:이내|정도|분량|수준))?|"
    r"\b\d+\s*(?:pages?|words?|sentences?|paragraphs?)\b)", re.IGNORECASE)
_WHEN = re.compile(
    r"(\d{4}\s*년\s*)?\d{1,2}\s*월\s*\d{1,2}\s*일|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|"
    r"[월화수목금토일]요일|"
    r"(?:이번|지난|다음)\s*(?:주|달|월|분기|학기)|주간|월간|격주|매주|매월|"
    r"오늘|내일|모레|어제|이번|지난|다음")
_MEDIUM = re.compile(r"(문자|메시지|이메일|e-?메일|메일|알림장|전화|카카오톡|카톡|SNS|sns)")
_SENDER = re.compile(r"([가-힣]{2,4})\s*(?:드림|올림|배상)")
_ORGANIZATION = re.compile(r"(교육청|교육지원청|교육부|교육과|지원청|행정실|학교)\b")
_SPECIFIC_DATE = re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
_SPECIFIC_TIME = re.compile(r"오[전후]\s*\d{1,2}\s*시|\d{1,2}\s*시\s*\d{0,2}\s*분?|\d{1,2}:[0-5]\d")
_PLACE = re.compile(
    r"체육관|강당|운동장|교무실|회의실|시청각실|급식실|도서관|컴퓨터실|온라인|각 교실|본관|별관|"
    r"다목적실|강의실|세미나실|과학실|음악실|미술실|방송실|보건실|상담실|돌봄교실|놀이터|정문|후문")

_MEANINGFUL = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}|\d")


def _specific_when_text(text: str) -> str:
    """Stated date/time fragments in document order (kept as real values)."""
    spans = []
    for pattern in (_SPECIFIC_DATE, _SPECIFIC_TIME):
        spans += [match.span() for match in pattern.finditer(text)]
    parts, end = [], -1
    for start, stop in sorted(spans):
        if start <= end:  # overlapping alternative match of the same span
            continue
        end = stop
        parts.append(text[start:stop].strip())
    return " ".join(parts)


def extract_conditions(text: str) -> dict:
    """Recognize stated conditions so they are never asked again (re-ask ban).

    specific_when/specific_place keep the actual stated text, not a flag —
    the values are seeded into the completed prompt instead of being lost.
    """
    audience = _AUDIENCE.search(text)
    tone = _TONE.search(text)
    length = _LENGTH.search(text)
    when = _WHEN.search(text)
    medium = _MEDIUM.search(text)
    sender = _SENDER.search(text)
    recipient = _RECIPIENT.search(text)
    place = _PLACE.search(text)
    return {
        "audience": audience.group(0).strip() if audience else (recipient.group(1) if recipient else None),
        "explicit_audience": audience.group(0).strip() if audience else None,
        "recipient": recipient.group(1) if recipient else None,
        "tone": tone.group(0).strip() if tone else None,
        "length": length.group(0).strip() if length else None,
        "when": when.group(0).strip() if when else None,
        "medium": medium.group(0).strip() if medium else None,
        "sender": sender.group(1) if sender else None,
        "organization": _ORGANIZATION.search(text).group(1) if _ORGANIZATION.search(text) else None,
        "specific_when": _specific_when_text(text),
        "specific_place": place.group(0) if place else None,
    }


# ---------------------------------------------------------------------------
# Task-type table (T1~T7 education tasks + T8 generic fallback)
# ---------------------------------------------------------------------------

def _topic(topic_id, title, reason, chips, basis, optional=False, covered=None, seed=None):
    # ``seed`` is a regex used to lift the stated text out of the input; the
    # resolved text itself only appears under "seed" on covered topics.
    return {"id": topic_id, "title": title, "reason": reason,
            "chips": list(chips), "basis": basis,
            "optional": optional, "covered": covered, "seed_pattern": seed}


def _audience_topic(kind="보고"):
    label = "보고를 받는 사람" if kind == "보고" else "받는 사람"
    reason = "받는 사람에 따라 깊이와 표현 수준이 달라져요."
    return _topic("audience", label, reason,
                  ["과장님", "팀장님", "교장·교감 선생님"], "보고 대상")


def _notice_topics(_text, _ex):
    """T1 school/parent notice letters."""
    return [
        _topic("sender_target", "알리는 주체와 받는 대상",
               "누가 누구에게 알리는지가 제목과 첫 문단을 정해요.",
               ["△△초등학교 1~3학년", "우리 학교 전 학년 학부모", "특정 학년 학생·학부모"],
               "발신·대상",
               covered=r"[가-힣]{2,}(?:초등학교|중학교|고등학교|유치원|교육청|어린이집)",
               seed=r"([가-힣△○·]{0,12}(?:초등학교|중학교|고등학교|유치원|교육청|어린이집)"
                    r"(?:\s*[가-힣0-9~\-]{0,15})?)"),
        _topic("content", "안내할 내용의 구체 구성",
               "무엇을 안내하는지가 통신문의 본문 전부예요.",
               ["프로그램 이름과 운영 요일·시간", "행사 일정과 장소", "신청 대상과 신청 방법"],
               "안내 내용",
               covered=r"요일|시간표|매주|주\s*\d+\s*회|구성|커리큘럼|시간|일정표",
               seed=r"(?:프로그램|시간표|일정|구성|커리큘럼)\s*[:：]\s*([^,.\n;]+)|"
                    r"([^,.\n;]{2,40}(?:요일|시간표|매주|주\s*\d+\s*회|일정표)[^,.\n;]{0,15})"),
    ]


def _counsel_topics(_text, _ex):
    """T2 parent-counsel replies."""
    return [
        _topic("topic", "어떤 상담인지",
               "상담 주제가 회신의 핵심 내용과 어조를 정해요.",
               ["학교 적응", "교우 관계", "학습·진로 고민"],
               "상담 주제",
               covered=r"적응|교우|학습|진로|성적|가정\s*사정|생활\s*지도|심리|진학",
               seed=r"(?<![0-9A-Za-z가-힣])([가-힣]+(?:\s+[가-힣]+){0,4}\s*"
                    r"(?:적응|교우|학습|진로|성적|심리|진학|고민|문제|상담)(?:\s*상담)?)"),
        _topic("schedule", "제안할 상담 일시",
               "받는 분이 고를 수 있는 시간 후보가 회신 문장 자체가 돼요.",
               ["이번 주 금요일 오후", "다음 주 화·목요일 오후", "두 시간대를 제시해 직접 적기"],
               "상담 일시",
               covered=r"일시|시간|요일|오전|오후|\d{1,2}\s*시|\d{1,2}\s*월\s*\d{1,2}\s*일",
               seed=r"((?:이번|다음|지난)\s*주\s*[월화수목금토일]요일"
                    r"(?:\s*(?:오전|오후|\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?|이후|경|쯤)){0,3}|"
                    r"[월화수목금토일]요일"
                    r"(?:\s*(?:오전|오후|\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?|이후|경|쯤)){0,3}|"
                    r"(?:오전|오후)\s*\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?|"
                    r"\d{1,2}\s*월\s*\d{1,2}\s*일"
                    r"(?:\s*(?:오전|오후|\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?)){0,2})"),
    ]


def _minutes_topics(_text, _ex):
    """T3 meeting minutes."""
    return [
        _topic("info", "회의 기본 정보 (일시·장소·참석자)",
               "회의록은 누가·언제·어디서 모였는지 기본 정보로 시작해요.",
               ["오늘 오후 3시, 교무실", "교장·교감·부장교사 참석", "날짜와 장소를 직접 적기"],
               "기본 정보",
               covered=r"참석|교장|교감|부장|전교직원|참가자|참석자|장소",
               seed=r"((?:[가-힣]{1,10}[·,、]\s*)+[가-힣]{1,10}\s*참석(?:자)?|"
                    r"[가-힣]{2,}(?:\s+[가-힣]{2,}){0,3}\s*참석(?:자)?)"),
        _topic("agenda", "회의에서 다룬 안건",
               "안건이 회의록 본문의 골격이에요.",
               ["학사일정·행사 계획", "학생 지도 관련 사안", "예산·사업 추진"],
               "안건",
               covered=r"안건|의제|논의\s*(?:했|한|내용)|다룬|다뤘|결정\s*사항",
               seed=r"(?:안건|의제|논의\s*내용|결정\s*사항)\s*[:：]\s*([^,.\n;]+)"),
        _topic("emphasis", "강조할 부분이나 기록 톤",
               "무엇을 특히 남길지가 결정사항 서술의 무게를 정해요.",
               ["결정된 사항 중심", "논의 과정도 간단히 기록", "차후 과제 강조"],
               "강조 포인트",
               covered=r"강조|중점|중심|포인트|강조점",
               seed=r"([^,.\n;]{2,40}(?:강조|중점|중심|포인트)[^,.\n;]{0,15})"),
    ]


def _official_topics(_text, _ex):
    """T4 official-letter replies."""
    return [
        _topic("demand", "온 공문이 요구하는 내용",
               "회신은 원 공문의 요구에 대한 답변이라 요구 사항이 먼저 필요해요.",
               ["사업 완료 결과 보고", "현황·실적 제출 요구", "개선 계획 제출 요구"],
               "원 공문 요구",
               covered=r"(제출|보고|현황|협조|요구|완료|개선|회신).{0,10}(요구|요청|하라|해\s*달|란|라는|인한|에\s*대한)",
               seed=r"([^,.\n;]{2,60}(?:제출|보고|현황|협조|요구|완료|개선|회신)[^,.\n;]{0,10}"
                    r"(?:요구|요청|하라|해\s*달|란|라는|인한|에\s*대한))"),
        _topic("stance", "우리 쪽 현황과 답변 방향",
               "처리 상태와 방향이 회신 문장의 실제 내용이에요.",
               ["기한 내 제출 예정", "일부 지연 — 사유 있음", "내부 검토 중"],
               "현황·답변 방향",
               covered=r"현황|입장|방향|처리\s*(?:중|예정|상태)|검토\s*중|제출\s*예정|지연",
               seed=r"(?:현황|입장|방향|처리\s*상태)\s*[:：]\s*([^,.\n;]+)"),
    ]


def _weekly_topics(_text, ex):
    """T5-B weekly report format."""
    topics = [
        _topic("week_results", "이번 주에 한 일과 성과",
               "보고의 중심이 되는 실제 활동이라, 없으면 본문을 채울 수 없어요.",
               ["방과후 프로그램 3개 운영", "민원 5건 처리 완료", "예산 집행 정리 마침"],
               "이번 주 성과",
               covered=r"(?:이번\s*주.{0,15})?(?:성과|실적)(?:은|는|이|가|:|을|를)|한\s*일\s*[:：]",
               seed=r"(?:한\s*일|성과|실적)\s*[:：]\s*([^,.\n;]+)"),
        _topic("week_issues", "진행 중인 업무와 어려움",
               "아직 끝나지 않은 일과 막힌 부분이 보고의 다음 축이에요.",
               ["협의가 더 필요한 사안", "일정이 늦어진 업무", "민원이 늘어난 분야"],
               "진행 중·이슈",
               covered=r"(진행\s*중|처리\s*중|계속).{0,15}(이슈|문제|어려움|상황)|"
                      r"(?:이슈|어려움|진행\s*중인?\s*업무)\s*[:：]",
               seed=r"(?:이슈|어려움|진행\s*중인?\s*업무)\s*[:：]\s*([^,.\n;]+)"),
        _topic("next_week", "다음 주에 할 일과 계획",
               "보고는 다음 계획을 밝히며 마무리되는 경우가 많아요.",
               ["행사 준비·운영", "점검·보고 마무리", "새 사업 시작"],
               "다음 주 계획",
               covered=r"다음\s*주.{0,15}(계획|예정|할\s*일|추진)|"
                      r"(?:다음\s*주|차주)\s*(?:할\s*일|계획|예정)\s*[:：]",
               seed=r"(?:다음\s*주|차주)?\s*(?:할\s*일|계획|예정|추진)\s*[:：]\s*([^,.\n;]+)"),
        _topic("help_needed", "지원이나 도움이 필요한 일",
               "받는 사람에게 무엇을 요청할지가 보고의 목적이에요.",
               ["인력·시간이 부족", "예산·물자가 필요", "상급 기관 협조가 필요"],
               "도움 필요사항",
               optional=True,
               covered=r"(요청|필요|지원|도움)\s*(사항|해|을|를|함|드림|해요)|"
                      r"(?:요청|지원|도움)\s*(?:사항)?\s*[:：]",
               seed=r"(?:요청|지원|도움)\s*(?:사항)?\s*[:：]\s*([^,.\n;]+)"),
    ]
    if not ex["explicit_audience"]:
        topics.insert(0, _audience_topic())
    return topics


# "○○을 분석해서 … 보고", "○○에 대한 보고" — the subject is already stated.
_REPORT_SUBJECT = (r"([가-힣A-Za-z0-9·~]+(?:\s+[가-힣A-Za-z0-9·~]+){0,4}?)\s*(?:을|를)\s*"
                   r"(?:분석|정리|검토|조사|점검)|"
                   r"([가-힣A-Za-z0-9·~]+(?:\s+[가-힣A-Za-z0-9·~]+){0,4}?)\s*"
                   r"(?:에\s*대한|에\s*관한|관련)\s*보고")


def _general_report_topics(_text, ex):
    """T5-A general report format."""
    topics = [
        _topic("agenda", "보고의 핵심 안건",
               "무엇에 대한 보고인지가 보고서 전체를 정해요.",
               ["예산 집행 현황", "민원 처리 결과", "사업 추진 상황"],
               "핵심 안건",
               covered=r"(안건|핵심\s*안건|주제|내용)\s*[:은는이]|" + _REPORT_SUBJECT,
               seed=r"(?:안건|핵심\s*안건|주제|내용)\s*(?:[:：은는이가])\s*([^,.\n;]+)|"
                    + _REPORT_SUBJECT),
        _topic("background", "안건의 배경",
               "결정을 내리려면 배경 설명이 필요해요.",
               ["이전 보고 이후 진행된 일", "관련 지침·요청이 있었던 일"],
               "배경",
               covered=r"(배경|경위|지금까지|그동안|사유)\s*[:은는이]?",
               seed=r"(?:배경|경위|사유)\s*(?:[:：은는이가])\s*([^,.\n;]+)"),
        _topic("status", "현재 진행 상태와 수치",
               "현황 수치가 판단의 직접 재료가 돼요.",
               ["목표 대비 진행률", "처리 건수·기간 현황"],
               "현황",
               covered=r"(현황|현재|진행률|진척|\d+\s*%|처리율|실적)",
               seed=r"(?:현황|현재|진행률|진척|처리율|실적)\s*[:：]\s*([^,.\n;]+)|"
                    r"(\d+\s*%[^,.\n;]{0,20})"),
        _topic("risk", "예상되는 어려움·리스크",
               "미리 짚은 어려움이 보고의 설득력을 높여요.",
               ["일정 지연 가능성", "예산·인력 부족", "민원 증가 우려"],
               "리스크",
               covered=r"리스크|우려|어려움|문제점|지연\s*(?:가능|예상)",
               seed=r"(?:리스크|우려|어려움|문제점)\s*[:：은는이가]\s*([^,.\n;]+)"),
        _topic("request", "보고로 요청하거나 기대하는 것",
               "보고의 끝은 요청·기대를 밝히는 자리예요.",
               ["예산·인력 지원 요청", "일정 조정 승인", "결정·승인 필요 사항"],
               "요청 사항",
               covered=r"(요청|요구|승인|지원|기대)\s*(사항|드림|함)?|"
                      r"(?:요청|요구|승인|지원)\s*(?:사항)?\s*[:：]",
               seed=r"(?:요청|요구|승인|지원|기대)\s*(?:사항)?\s*[:：]\s*([^,.\n;]+)"),
    ]
    if not ex["explicit_audience"]:
        # Keep every topic area: fold background+status into one card.
        merged = dict(topics[1])
        merged["id"] = "background_status"
        merged["title"] = "안건의 배경과 현재 상황"
        merged["basis"] = "배경·현황"
        merged["chips"] = topics[1]["chips"] + topics[2]["chips"]
        topics = ([_audience_topic(), topics[0], merged] + topics[3:])[:_MAX_QUESTIONS]
    return topics


def _complaint_topics(_text, _ex):
    """T6 complaint/civil-petition replies."""
    return [
        _topic("reason", "무엇이 왜 문제가 됐는지",
               "사실 관계 설명이 회신의 첫 내용이에요.",
               ["발급 지연 — 사유를 적어 주세요", "처리 절차상 착오", "인력·시스템 문제"],
               "지연·사유 사실",
               covered=r"(?:사유|원인|이유)\s*[:：]|때문|탓|지연.{0,6}(?:사유|원인)|.{0,15}연유",
               seed=r"(?:사유|원인|이유)\s*[:：]\s*([^,.\n;]+)|"
                    r"([^,.\n;]{2,40}(?:때문|탓|연유|오류|장애|누락))"),
        _topic("status", "현재 처리 상태와 예상 완료",
               "상대가 가장 궁금해하는 처리 시점이에요.",
               ["처리 중 — 이번 주 안", "일부 완료 — 나머지 진행 중", "일정 미정"],
               "처리 상태",
               covered=r"(처리|진행|완료|해결).{0,8}(중|예정|상태|시점|기한)",
               seed=r"([^,.\n;]{2,50}(?:처리|진행|완료|해결)[^,.\n;]{0,8}"
                    r"(?:중|예정|상태|시점|기한))"),
        _topic("offer", "제시할 수 있는 보상·편의",
               "불편에 대한 보완책이 회신의 신뢰를 만들어요.",
               ["우선 처리 약속", "수수료 감면·면제", "방문 없이 처리"],
               "보상·편의",
               covered=r"보상|편의|감면|면제|우선|신속|배상",
               seed=r"([^,.\n;]{2,40}(?:보상|편의|감면|면제|우선|배상|신속)[^,.\n;]{0,15})"),
    ]


def _event_topics(_text, _ex):
    """T7 event plan/notice."""
    return [
        _topic("topic", "행사 주제와 목적",
               "무엇을 위한 행사인지가 안내문의 제목이에요.",
               ["학생 안전 체험 행사", "학부모 연수·설명회", "지역 주민 참여 행사"],
               "행사 주제·목적",
               # '행사'·'안내문'은 유형 신호어이지 명시된 주제가 아니다 — 맨 '행사'
               # 단어만으로 주제가 정해졌다고 보면 T7 되묻기 ①이 영구 누락된다.
               covered=r"주제|목적|취지|기념|체험|연수|설명회|축제|발표회|체육대회|현장학습|운동회|캠프|안전교육",
               seed=r"(?<![0-9A-Za-z가-힣])([가-힣]+(?:\s+[가-힣]+){0,6}\s*"
                    r"(?:체험\s*행사|행사|체험|연수|설명회|축제|발표회|체육대회|"
                    r"현장학습|운동회|캠프|안전교육))"),
        _topic("schedule", "행사 일시·장소·참여 방법",
               "언제·어디서·어떻게 참여하는지가 안내문의 정보예요.",
               ["11월 20일 오후 2시, 체육관", "사전 신청 후 참여", "당일 현장 접수"],
               "일시·참여 방법",
               covered=r"__SPECIFIC_SCHEDULE__"),
    ]


def _generic_topics(_text, _ex):
    """T8 unclassified fallback."""
    return [
        _topic("context", "누가·어떤 상황에서 쓸 건지",
               "쓰는 상황이 결과의 방향과 깊이를 정해요.",
               ["나 혼자 참고용", "상사·동료에게 보고용", "여러 사람에게 공개용"],
               "사용 맥락"),
        _topic("request", "구체적으로 원하는 것",
               "원하는 산출이 정해져야 만들 수 있어요.",
               [],
               "요청 내용"),
    ]


_TYPES = [
    # (id, label, expert role, doc name, section set, signals, topics_fn)
    {
        "id": "counsel_reply", "label": "상담 회신",
        "role": "학부모 상담 회신 작성 전문가", "doc": "상담 회신",
        "sections": 4, "topics": _counsel_topics,
        "signals": r"상담.{0,12}(신청|회신|답변|문자|연락|안내)|상담\s*(?:일정|시간|신청)",
    },
    {
        "id": "complaint_reply", "label": "민원 회신",
        "role": "민원·항의 회신 문서 작성 전문가", "doc": "민원 회신",
        "sections": 4, "topics": _complaint_topics,
        "signals": r"민원인|민원.{0,10}(회신|답변|공문|대응)|항의|불만|클레임|진정|청구",
    },
    {
        "id": "official_reply", "label": "공문 답변",
        "role": "기관 간 공문 회신 작성 전문가", "doc": "공문 답변",
        "sections": 5, "topics": _official_topics,
        "signals": r"(공문|시행|교육청|교육지원청|기안|협조전).{0,15}(답변|회신|초안|회의|제출)|공문.{0,5}답변",
    },
    {
        "id": "weekly_report", "label": "주간 업무 보고",
        "role": "교육행정기관 업무 보고 작성 전문가", "doc": "주간 업무 보고",
        "sections": 5, "topics": _weekly_topics, "subtype": "weekly",
        # B형 신호는 '주간보고'·'주보' 계열 한국어만 — 영어 'weekly report'에는
        # 신호가 없으므로 A형 보고형으로 분류한다(결정 #2 r1, 원본 I12 관찰 A형).
        "signals": r"주간보고|주간.{0,4}보고|주보|주간\s*업무",
    },
    {
        "id": "general_report", "label": "업무 보고",
        "role": "교육행정기관 업무 보고서 작성 전문가", "doc": "업무 보고",
        "sections": 5, "topics": _general_report_topics,
        "signals": r"보고서|보고|실적|보고\s*(?:자료|문서)|report",
    },
    {
        "id": "minutes", "label": "회의록",
        "role": "교무·업무회의 기록 정리 전문가", "doc": "회의록",
        "sections": 5, "topics": _minutes_topics,
        "signals": r"회의록|교무회의|협의회|회의\s*(?:내용|기록|록)|minutes|meeting",
    },
    {
        "id": "event_notice", "label": "행사 안내",
        "role": "학교·지역 행사 안내문 작성 전문가", "doc": "행사 안내문",
        "sections": 5, "topics": _event_topics,
        "signals": r"행사|체육대회|현장체험|현장학습|졸업식|입학식|축제|발표회",
    },
    {
        "id": "notice_letter", "label": "학교 안내문",
        "role": "학교 안내·통신문 작성 전문가", "doc": "가정통신문·안내문",
        "sections": 5, "topics": _notice_topics,
        "signals": r"가정통신문|통신문|알림장|방과후|안내문|안내장|공지|통지|newsletter|notice",
    },
]

_GENERIC = {
    "id": "generic", "label": "일반 요청",
    "role": "사용자의 업무 요청을 정확히 수행하는 문서 작성 보조자", "doc": "요청된 작업",
    "sections": 4, "topics": _generic_topics, "signals": "",
}

_TYPE_BY_ID = {t["id"]: t for t in _TYPES} | {"generic": _GENERIC}

# Classification order matters: reply types before document types.
_SIGNAL_ORDER = ["counsel_reply", "complaint_reply", "official_reply", "weekly_report",
                 "general_report", "minutes", "event_notice", "notice_letter"]


def classify(text: str) -> str:
    """Return the education task type id, or 'generic' for the fallback path."""
    for type_id in _SIGNAL_ORDER:
        if re.search(_TYPE_BY_ID[type_id]["signals"], text, re.IGNORECASE):
            return type_id
    return "generic"


_SPLIT = re.compile(
    r"(?:쓰고|작성하고|만들고|정리하고|적어\s*두고|보내고|하고)[,，、\s]+"
    r"|,\s+(?=\S)|;\s*|그리고\s+|또\s+|및\s+|\band\b\s*|\+\s*")


def detect_segments(text: str) -> list[dict]:
    """Split a composite request; single-type input returns one segment.

    A composite is reported only when at least two parts classify to
    concrete task types. Otherwise the whole text is one segment.

    Returns every detected segment; the caller caps how many stages it runs
    and is responsible for telling the user about any dropped remainder.
    """
    parts = [part.strip() for part in _SPLIT.split(text) if part and part.strip()]
    if len(parts) < 2:
        return [{"text": text.strip(), "type": classify(text)}]
    typed = [(part, classify(part)) for part in parts]
    concrete = [pair for pair in typed if pair[1] != "generic"]
    if len(concrete) < 2:
        return [{"text": text.strip(), "type": classify(text)}]
    return [{"text": part, "type": type_id} for part, type_id in typed]


def is_meaningless(text: str) -> bool:
    """Inputs like 'ㅁㄴㅇㄹ' — no completed words — need the re-entry tuner chip."""
    return not bool(_MEANINGFUL.search(text))


def _covers(topic: dict, text: str, ex: dict) -> bool:
    pattern = topic.get("covered")
    if not pattern:
        return False
    if pattern == "__SPECIFIC_SCHEDULE__":
        return bool(ex["specific_when"] or ex["specific_place"])
    return bool(re.search(pattern, text, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Seeding: covered topics keep their stated text so it reaches the output.
# ---------------------------------------------------------------------------

# Leading fragments like "에서/은/는/을/를" that end a token are postpositions —
# strip them so a seed reads as a value, not a half-sentence.
_TRAILING_PARTICLE = re.compile(
    r"(?:에서|으로|부터|께서|에게|한테|이라도|라도|이랑|랑|처럼|마저|밖에|조차|까지|"
    r"은|는|이|가|을|를|의|에|와|과|도|만|로)$")
_REQUEST_VERB = re.compile(
    r"(?:써|작성해|만들어|정리해|적어|보내|해\s*달라고?|주세요|줘|드려|드립니다|해)\s*"
    r"(?:주|줘|요|시오)?\s*[.!]?$")


def _clip_utf16(text: str, limit: int) -> str:
    """Shorten text to a UTF-16 unit limit, marking the cut with an ellipsis."""
    if utf16_length(text) <= limit:
        return text
    cut = ""
    for ch in text:
        if utf16_length(cut + ch) > limit - 1:
            break
        cut += ch
    return cut + "…"


def _clean_seed(text: str) -> str:
    """Trim a captured stated-conditions fragment into an answer-like value."""
    text = text.strip().strip(",.:;、·")
    tokens = text.split()
    while len(tokens) > 1 and _TRAILING_PARTICLE.search(tokens[0]):
        tokens.pop(0)
    text = " ".join(tokens)
    text = _REQUEST_VERB.sub("", text).strip(" ,.:;")
    return _clip_utf16(text, 80)


def _label_norm(text: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]+", "", text)


def _is_label_echo(topic: dict, seed: str) -> bool:
    """True when the seed merely repeats the topic's own label surface.

    '지연 사유' seeded for basis '지연·사유 사실' or '행사' for '행사 주제·목적'
    carries no stated value — it only bounces the question back at the user.
    'Label + trailing particle' ('이번 주 성과가') still counts as the label.
    """
    words = seed.split()
    candidates = {_label_norm(seed)}
    if words:
        stem = _TRAILING_PARTICLE.sub("", words[-1])
        if stem != words[-1]:
            candidates.add(_label_norm(" ".join(words[:-1] + [stem])))
    candidates.discard("")
    for label in (topic.get("basis", ""), topic.get("title", "")):
        key = _label_norm(label)
        if key and any(len(candidate) >= 2 and candidate in key
                       for candidate in candidates):
            return True
    return False


def _schedule_seed(ex: dict) -> str | None:
    """Combine the stated schedule pieces into one seed line."""
    parts = []
    for part in (ex.get("specific_when"), ex.get("specific_place"), ex.get("when")):
        if part and not any(part in existing for existing in parts):
            parts.append(part)
    return ", ".join(parts) or None


def _seed_text(topic: dict, text: str, ex: dict) -> str | None:
    """Actual stated text for a covered topic, or None when nothing usable.

    A covered topic whose only capture repeats its own label carries no
    stated value — it seeds CONFIRM_NEEDED so the output stays honest
    instead of echoing the question back as the answer.
    """
    pattern = topic.get("covered")
    if not pattern:
        return None
    if pattern == "__SPECIFIC_SCHEDULE__":
        return _schedule_seed(ex)
    seed_pattern = topic.get("seed_pattern")
    if seed_pattern:
        match = re.search(seed_pattern, text, re.IGNORECASE)
        if match:
            value = next((group for group in match.groups() if group), match.group(0))
            cleaned = _clean_seed(value)
            if cleaned and not _is_label_echo(topic, cleaned):
                return cleaned
    covered_match = re.search(pattern, text, re.IGNORECASE)
    if not covered_match:
        return None
    if topic["id"] == "schedule" and ex.get("when"):
        return ex["when"]
    cleaned = _clean_seed(covered_match.group(0))
    if cleaned and not _is_label_echo(topic, cleaned):
        return cleaned
    return CONFIRM_NEEDED


def _split_topics(type_id: str, text: str):
    """(displayed questions, covered topics carrying their stated text).

    Covered topics are never re-asked; their seed text is returned alongside
    so the completed prompt can still carry the original conditions.
    """
    ex = extract_conditions(text)
    spec = _TYPE_BY_ID.get(type_id, _GENERIC)
    questions, seeded = [], []
    for topic in spec["topics"](text, ex):
        if _covers(topic, text, ex):
            seed = _seed_text(topic, text, ex)
            if seed:
                seeded.append(dict(topic, seed=seed, seeded=True))
        else:
            questions.append(dict(topic))
    return questions[:_MAX_QUESTIONS], seeded


def build_questions(type_id: str, text: str) -> list[dict]:
    """Questions for the type, minus topics the input already covers."""
    return _split_topics(type_id, text)[0]


def _answered_count(ex: dict) -> int:
    return sum(1 for key in ("audience", "tone", "length", "when", "medium") if ex.get(key))


def diagnose(text: str, type_id: str | None = None) -> dict:
    """One-line diagnosis: verdict + completeness % + missing conditions.

    ``seeded_topics`` carries the stated text of topics that were covered in
    the input — they are not re-asked but still reach the completed prompt.
    """
    type_id = type_id or classify(text)
    questions, seeded_topics = _split_topics(type_id, text)
    ex = extract_conditions(text)
    answered = _answered_count(ex) + 1  # +1: the task itself was stated
    missing = len(questions)
    percent = max(5, min(95, round(100 * answered / (answered + missing + 1))))
    if percent < 40:
        verdict = "핵심 조건이 아직 부족해요 — 지금 그대로는 원하는 결과가 나오기 어려워요."
    elif percent < 70:
        verdict = "어느 정도 방향이 보여요 — 몇 가지만 더 알려 주시면 좋아져요."
    else:
        verdict = "거의 다 갖췄어요 — 남은 조건만 확인하면 돼요."
    return {
        "type": type_id, "label": _TYPE_BY_ID[type_id]["label"],
        "percent": percent, "verdict": verdict,
        "questions": questions,
        "seeded_topics": seeded_topics,
        "missing_titles": [q["title"] for q in questions],
        "extracted": ex,
    }


# ---------------------------------------------------------------------------
# Completed-prompt assembly (section-structure contract)
# ---------------------------------------------------------------------------

# Section-name basis each type maps question answers into ([섹션별 근거]).
_BASIS_ORDER = {
    "weekly_report": ["보고 대상", "이번 주 성과", "진행 중·이슈", "다음 주 계획", "도움 필요사항"],
    "general_report": ["보고 대상", "핵심 안건", "배경", "배경·현황", "현황", "리스크", "요청 사항"],
    "minutes": ["기본 정보", "안건", "강조 포인트"],
    "official_reply": ["원 공문 요구", "현황·답변 방향"],
    "notice_letter": ["발신·대상", "안내 내용"],
    "event_notice": ["행사 주제·목적", "일시·참여 방법"],
}

_DOC_SKELETON = {
    "weekly_report": ["이번 주 주요 성과", "진행 중인 업무·이슈", "다음 주 계획", "지원·요청 필요사항"],
    "general_report": ["핵심 안건", "배경", "현황·수치", "리스크·대응", "요청·기대"],
    "minutes": ["회의 개요 (일시·장소·참석자)", "안건별 논의 내용", "결정 사항", "후속 과제·담당"],
    "official_reply": ["답변 제목", "원 공문 요지 확인", "현황·답변 내용", "마무리·연락처"],
    "notice_letter": ["제목", "인사·안내 목적", "안내 내용 (대상·일정·방법)", "신청·참여 방법", "문의·연락처"],
    "event_notice": ["제목·인사", "행사 개요 (주제·목적)", "일시·장소·참여 방법", "안내·문의", "마무리"],
    "complaint_reply": ["인사·공감 표현", "사실 확인·설명", "조치·해결 방안", "재발 방지·마무리 인사"],
    "counsel_reply": ["인사·감사 표현", "상담 안내 (주제·일시)", "연락 방법·마무리"],
    "generic": ["요청 요약", "본문 작성", "마무리·확인 사항"],
}

_CONTEXT_FIELDS = {
    "weekly_report": ["대상(보고 받는 사람)", "목적", "톤·문체", "핵심 안건", "요청 사항"],
    "general_report": ["대상(보고 받는 사람)", "목적", "톤·문체", "핵심 안건", "요청 사항"],
    "minutes": ["기본 정보", "기록 목적", "안건", "강조 포인트", "톤·문체"],
    "official_reply": ["수신 기관", "원 공문 요구", "답변 목적", "현황·입장", "톤·문체"],
    "notice_letter": ["발신 주체·대상", "안내 목적", "안내 내용", "톤·문체", "신청·문의"],
    "event_notice": ["대상", "행사 주제·목적", "일시·장소·참여", "톤·문체", "전달할 내용"],
    "complaint_reply": ["수신자", "회신 목적", "핵심 용건", "톤·문체", "전달할 핵심 내용"],
    "counsel_reply": ["수신자", "회신 목적", "상담 주제", "톤·문체", "전달할 핵심 내용"],
    "generic": ["사용 맥락", "목적", "요청 내용", "톤·문체", "전달할 핵심 내용"],
}

# 업무·기록 문서 계열: ②-1 '나중에 보고' 금지 조항이 들어가는 유형.
_WORK_DOC_TYPES = {"weekly_report", "general_report", "minutes", "official_reply"}
_DEFAULT_TONE = "격식 있는 공문체"

# Context fields that should take a specific answer's basis when names differ.
_FIELD_BASIS_OVERRIDE = {
    "weekly_report": {"핵심 안건": "이번 주 성과"},
}


def _tokens(text):
    return [part for part in re.split(r"[^가-힣A-Za-z0-9]+", text) if part]


def _raw_answer(question, answers):
    """Answer for a topic: explicit answer first, then the input-seeded text.

    Returns None when neither exists — callers decide how to mark the gap.
    """
    value = answers.get(question["id"], "").strip()
    if value:
        return value
    seed = (question.get("seed") or "").strip()
    return seed or None


def _basis_value_for(field, type_id, questions, answers):
    """Resolve a [맥락] field from a question answer (basis match, then overlap)."""
    override = _FIELD_BASIS_OVERRIDE.get(type_id, {}).get(field)
    key = field.split("(")[0].strip()
    for question in questions:
        if question["basis"] in (field, key, override):
            return _raw_answer(question, answers)
    field_tokens = _tokens(field)
    for question in questions:
        basis_tokens = _tokens(question["basis"])
        if any(f == b or f in b or b in f for f in field_tokens for b in basis_tokens):
            return _raw_answer(question, answers)
    return None


def _line(label, value):
    return f"- {label}: {value if value else '(해당 없음)'}"


def _tone_clause(ex, tuner):
    if tuner.get("tone_chips"):
        return "·".join(tuner["tone_chips"]) + " 방향"
    if ex.get("tone"):
        return ex["tone"]
    return _DEFAULT_TONE


def _length_clause(ex, tuner):
    if ex.get("length"):
        return ex["length"] + " 정도"
    if tuner.get("length_scale"):
        return tuner["length_scale"] + " 수준"
    return "읽기 쉬운 표준 분량"


def _basis_lines(type_id, questions, answers):
    """Map answers into the [섹션별 근거] 'section: fact' list.

    Unanswered optional items become [확인 필요] — only an explicit '없음'
    answer may render as 없음 (no implicit assumption of absence).
    """
    order = _BASIS_ORDER.get(type_id, [])
    by_basis = {}
    for question in questions:
        value = _raw_answer(question, answers)
        if value is not None:
            by_basis[question["basis"]] = value
        else:
            by_basis.setdefault(question["basis"], CONFIRM_NEEDED)
    lines = []
    for basis in order:
        if basis in by_basis:
            lines.append(f"- {basis}: {by_basis.pop(basis)}")
    for basis, value in by_basis.items():
        lines.append(f"- {basis}: {value}")
    if not lines:
        lines.append(f"- 본문: {CONFIRM_NEEDED}")
    return lines


def _tuner_note(tuner):
    selected = [item for key in ("chips", "tone_chips") for item in tuner.get(key, [])]
    if tuner.get("free"):
        selected.append(tuner["free"])
    return selected


def build_prompt(type_id: str, original: str, questions: list[dict],
                 answers: dict, extracted: dict | None = None,
                 tuner: dict | None = None, seeded: list[dict] | None = None) -> str:
    """Assemble the completed prompt with the type's section structure.

    ``seeded`` holds topics covered by the input (not re-asked) together with
    their stated text; when omitted it is recomputed so stated conditions are
    never dropped from the output.
    """
    spec = _TYPE_BY_ID.get(type_id, _GENERIC)
    ex = extracted or extract_conditions(original)
    tuner = tuner or {}
    if seeded is None:
        asked = {question["id"] for question in questions}
        seeded = []
        for topic in spec["topics"](original, ex):
            if topic["id"] in asked or not _covers(topic, original, ex):
                continue
            seed = _seed_text(topic, original, ex)
            if seed:
                seeded.append(dict(topic, seed=seed, seeded=True))
    all_topics = list(questions) + list(seeded)
    sections = spec["sections"]
    role_line = (f"당신은 {spec['role']}입니다. 문서는 앞쪽에 결론·핵심을 두는 두괄식으로 쓰고, "
                 f"제공된 사실만 사용하며, 확인되지 않은 내용은 지어내지 않고 {CONFIRM_NEEDED}로 남깁니다.")
    # The user's own words go first: extraction only finds some conditions, so
    # subjects, counts and facts the rules miss must still reach the AI.
    out = ["[역할]", role_line, "", "[맥락]",
           "- 원래 요청: " + " ".join(original.split())]

    if sections == 5:
        for field in _CONTEXT_FIELDS[spec["id"]]:
            value = None
            if "톤" in field or "문체" in field:
                value = ex.get("tone") or ("·".join(tuner.get("tone_chips", [])) or None)
            elif "목적" in field and not _basis_value_for(field, spec["id"], all_topics, answers):
                value = spec["doc"] + " 작성"
            else:
                value = _basis_value_for(field, spec["id"], all_topics, answers)
                if value is None and any(mark in field for mark in ("대상", "수신", "발신")):
                    value = ex.get("audience") or ex.get("recipient") or ex.get("organization")
            out.append(_line(field, value))
        out += ["", "[섹션별 근거]",
                "아래 사실을 같은 이름의 섹션에 반드시 반영한다. "
                f"사실이 없는 섹션은 {CONFIRM_NEEDED}로만 남긴다."]
        out += _basis_lines(spec["id"], all_topics, answers)
    else:
        # 4-section reply/generic types: answers flow into the context fields.
        basis_answers = {}
        for question in all_topics:
            value = _raw_answer(question, answers)
            basis_answers[question["basis"]] = value if value is not None else CONFIRM_NEEDED
        delivered = [f"{basis}: {value}" for basis, value in basis_answers.items()]
        for field in _CONTEXT_FIELDS[spec["id"]]:
            value = None
            if "수신" in field:
                value = ex.get("audience") or ex.get("recipient") or ex.get("organization")
            elif "톤" in field or "문체" in field:
                value = ex.get("tone") or ("·".join(tuner.get("tone_chips", [])) or None)
            elif field in basis_answers:
                value = basis_answers[field]
            elif "목적" in field:
                value = spec["doc"] + " 작성"
            elif "용건" in field or "주제" in field:
                value = (_raw_answer(all_topics[0], answers) or CONFIRM_NEEDED) if all_topics else None
            elif "핵심 내용" in field:
                value = "; ".join(delivered) if delivered else None
            else:
                value = _basis_value_for(field, spec["id"], all_topics, answers)
            out.append(_line(field, value))

    if ex.get("medium"):
        task_line = (f"위 맥락으로 '{ex['medium']}' 형식의 '{spec['doc']}'을(를) 작성한다. "
                     "산출 문서는 아래 섹션을 순서와 이름 그대로 사용해 쓴다.")
    else:
        task_line = (f"위 맥락으로 '{spec['doc']}'을(를) 작성한다. "
                     "산출 문서는 아래 섹션을 순서와 이름 그대로 사용해 쓴다.")
    out += ["", "[과업]", task_line]
    out += [f"{i}. {name}" for i, name in enumerate(_DOC_SKELETON[spec["id"]], 1)]

    circled = "①②③④⑤⑥⑦⑧⑨⑩"
    next_num = 3  # rules already hold ①·②

    def _next_num():
        nonlocal next_num
        num, next_num = next_num, next_num + 1
        return circled[num - 1]

    rules = [f"① 앞쪽에 결론·핵심을 두고(두괄식), 제공된 사실·수치만 사용한다. "
             "원래 요청에 적힌 주제·대상·수치·날짜는 빠짐없이 반영한다.",
             f"② 없는 사실·수치는 지어내지 않고 {CONFIRM_NEEDED}로만 표기한다."]
    if spec["id"] in _WORK_DOC_TYPES:
        # Sub-rule of ② — keeps its own ②-1 label without consuming a number.
        rules.append(
            "②-1 '나중에 알아보거나 다시 보고하겠다'는 취지의 문장을 어떤 변형으로도 쓰지 않는다 — "
            + "·".join(_FORBIDDEN_LATER) +
            f" 류의 표현을 모두 금지한다. 빠진 항목은 {CONFIRM_NEEDED} 한 표기만 남기고 "
            "약속·예정·후속 조치를 서술하지 않는다.")
    elif spec["sections"] == 4 and spec["id"] in ("complaint_reply", "counsel_reply"):
        rules.append(f"{_next_num()} 주어진 내용만 사용하고, 확인되지 않은 표현·약속을 만들지 않는다.")
    rules.append(f"{_next_num()} 분량은 {_length_clause(ex, tuner)}에 맞춘다.")
    rules.append(f"{_next_num()} 톤·문체는 {_tone_clause(ex, tuner)}에 맞춘다.")
    if ex.get("medium"):
        rules.append(f"{_next_num()} 전달할 매체는 '{ex['medium']}'이다 — "
                     "그 매체에 맞는 길이와 형식으로 작성한다.")
    if spec["sections"] == 4 and spec["id"] in ("complaint_reply", "counsel_reply"):
        if ex.get("sender"):
            rules.append(f"{_next_num()} 발신인은 '{ex['sender']}'로 쓰고, "
                         "수신자 호칭은 맥락의 값만 쓰고 지어내지 않는다.")
        else:
            rules.append(f"{_next_num()} 발신인 이름은 제공되지 않았으므로 서명은 "
                         "'[보내는 사람 이름] 드림'으로 남긴다. 수신자 호칭은 맥락의 값만 쓰고 지어내지 않는다.")
    extra = _tuner_note(tuner)
    if extra:
        rules.append(f"{_next_num()} 사용자가 고른 표현 방향: {' · '.join(extra)}.")
    rules.append(f"{_next_num()} 원문에 없는 이름·날짜·수치·연락처를 만들지 않고, "
                 "자리표시자는 꼭 필요한 곳에만 둔다.")
    out += ["", "[제약] (반드시 지킨다)"] + rules
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Tuner axes (pre-generation optional controls, S2b)
# ---------------------------------------------------------------------------

_TONE_CHIPS = ["짧게 줄이기", "정중하게 다듬기", "어조를 단단하게", "따뜻한 느낌으로", "핵심만 남기기"]
_LENGTH_STEPS = ["간단 요약", "간결", "적당", "자세히", "전체 상세"]


def tuner_axes(type_id: str, meaningless: bool = False) -> list[dict]:
    """Per-request adjustment axes; empty list means the tuner is not needed."""
    axes = []
    if meaningless:
        axes.append({"kind": "chips", "id": "reenter", "title": "요청 다시 적기",
                     "options": ["위 입력란에 요청을 다시 적어 주세요"]})
    if type_id in ("weekly_report", "general_report"):
        axes += [
            {"kind": "slider", "id": "length_scale", "title": "보고 분량", "steps": _LENGTH_STEPS},
            {"kind": "chips", "id": "chips", "title": "무게를 둘 부분",
             "options": ["성과 중심", "문제·이슈 중심", "다음 계획 중심"]},
        ]
    elif type_id in ("complaint_reply", "counsel_reply"):
        axes += [
            {"kind": "chips", "id": "chips", "title": "시작부 방식",
             "options": ["사과·공감으로 시작", "핵심 답변부터", "정중한 인사 먼저"]},
            {"kind": "slider", "id": "status_detail", "title": "처리 상황 안내 정도",
             "steps": ["간단 안내", "짧은 설명", "중간", "상세 설명", "전 과정 안내"]},
            {"kind": "chips", "id": "closing", "title": "보상·편의 제시",
             "options": ["제시하지 않음", "가능 범위 안내", "적극 제안"]},
        ]
    elif type_id == "minutes":
        axes += [
            {"kind": "chips", "id": "chips", "title": "기록 형식",
             "options": ["표 형식", "항목 나열", "요약 중심"]},
            {"kind": "chips", "id": "focus", "title": "무게 중심",
             "options": ["결정 사항 중심", "논의 과정 포함", "후속 과제 강조"]},
        ]
    elif type_id in ("notice_letter", "event_notice"):
        axes += [
            {"kind": "slider", "id": "length_scale", "title": "안내문 분량", "steps": _LENGTH_STEPS},
            {"kind": "chips", "id": "chips", "title": "대상 맞춤",
             "options": ["학부모 눈높이", "학생 포함", "관계 기관용"]},
        ]
    elif type_id == "official_reply":
        axes += [
            {"kind": "chips", "id": "chips", "title": "답변 형식",
             "options": ["간결 회신", "표준 공문 형식", "상세 보고 첨부"]},
            {"kind": "slider", "id": "length_scale", "title": "답변 분량", "steps": _LENGTH_STEPS},
        ]
    else:
        axes.append({"kind": "chips", "id": "chips", "title": "작업 종류",
                     "options": ["글쓰기", "정리", "분석", "계획", "검토"]})
    return axes


# ---------------------------------------------------------------------------
# External AI handoff (clipboard-first, paste fallback)
# ---------------------------------------------------------------------------

EXTERNAL_AI = [
    ("chatgpt", "ChatGPT", "https://chatgpt.com/"),
    ("claude", "Claude", "https://claude.ai/"),
    ("gemini", "Gemini", "https://gemini.google.com/"),
    ("grok", "Grok", "https://grok.com/"),
    ("perplexity", "Perplexity", "https://www.perplexity.ai/"),
]


def copy_to_clipboard(text: str) -> bool:
    try:
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        return clipboard.text().replace("\r\n", "\n") == text.replace("\r\n", "\n")
    except (RuntimeError, AttributeError):
        return False


def open_external(key: str, text: str) -> bool:
    """Always copy first; opening the site is the best-effort second step."""
    url = {service[0]: service[2] for service in EXTERNAL_AI}[key]
    copied = copy_to_clipboard(text)
    QDesktopServices.openUrl(QUrl(url))
    return copied


# ---------------------------------------------------------------------------
# Local preference memory (repeated answers prefilled; local storage only)
# ---------------------------------------------------------------------------

def _pref_path() -> Path:
    root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
    if not root:
        raise OSError("사용자 저장 폴더를 찾을 수 없습니다.")
    return Path(root) / "SyeonPet" / "prompt-assistant" / "flow-preferences.json"


def _atomic_write(path: Path, payload: bytes) -> None:
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".flow-pref-", suffix=".tmp", delete=False) as f:
            name = f.name
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        name = None
    finally:
        if name:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass


class PreferenceStore:
    """Locally remembered answers: type_id -> {question_id: {text, uses}}."""

    def __init__(self, path: Path | None = None):
        self.path = path or _pref_path()

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return {}
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("answers"), dict):
            return {}
        result = {}
        try:
            for type_id, entries in data["answers"].items():
                if not isinstance(entries, dict):
                    continue
                result[type_id] = {str(k): {"text": str(v.get("text", "")), "uses": int(v.get("uses", 0))}
                                   for k, v in entries.items()
                                   if isinstance(v, dict) and isinstance(v.get("text"), str)}
        except (TypeError, ValueError):
            return {}
        return result

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.path, json.dumps({"version": 1, "answers": data},
                                            ensure_ascii=False, indent=2).encode("utf-8"))

    def record(self, type_id: str, question_id: str, text: str) -> None:
        text = text.strip()
        # UTF-16 units, matching the answer-length limit used elsewhere.
        if not text or utf16_length(text) > _MAX_ANSWER:
            return
        data = self.load()
        entries = data.setdefault(type_id, {})
        entry = entries.get(question_id)
        if entry and entry["text"] == text:
            entry["uses"] += 1
        else:
            entries[question_id] = {"text": text, "uses": 1}
        total = sum(len(v) for v in data.values())
        if total > _MAX_PREF_ENTRIES:
            # Drop the least-used entries until within the bound.
            flat = sorted(((t, k, v["uses"]) for t, d in data.items() for k, v in d.items()),
                          key=lambda item: item[2])
            for t, k, _u in flat[: max(0, total - _MAX_PREF_ENTRIES)]:
                data[t].pop(k, None)
        self._save(data)

    def prefilled(self, type_id: str) -> dict:
        """Answers repeated at least _PREF_MIN_USES times, for this type."""
        return {qid: entry["text"]
                for qid, entry in self.load().get(type_id, {}).items()
                if entry["uses"] >= _PREF_MIN_USES}

    def clear(self, type_id: str | None = None) -> None:
        if type_id is None:
            self._save({})
            return
        data = self.load()
        data.pop(type_id, None)
        self._save(data)


# ---------------------------------------------------------------------------
# Rendered document view of the finished prompt (B13)
# ---------------------------------------------------------------------------

_SECTION_HEAD = re.compile(r"^\[(역할|맥락|섹션별 근거|과업|제약)\]\s*(.*)$")


def render_prompt_html(text: str) -> str:
    """Render the prompt text as a styled document for the result screen."""
    body = []
    for raw in text.splitlines():
        line = html.escape(raw)
        head = _SECTION_HEAD.match(raw)
        if head:
            body.append(f'<h3 style="color:#195b83;margin:10px 0 2px 0;">'
                        f'[{head.group(1)}] {html.escape(head.group(2))}</h3>')
            continue
        if not line.strip():
            body.append("<br/>")
            continue
        line = line.replace(CONFIRM_NEEDED,
                            f'<b style="background:#fff3cd;color:#6b4e00;">{CONFIRM_NEEDED}</b>')
        stripped = raw.lstrip()
        if stripped.startswith("- "):
            body.append(f'<div style="margin-left:14px;">• {line.lstrip()[2:]}</div>')
        else:
            body.append(f'<div>{line}</div>')
    return ('<div style="font-family:\'Malgun Gothic\',sans-serif;font-size:10pt;'
            'color:#172b3a;">' + "".join(body) + "</div>")


# ---------------------------------------------------------------------------
# Guided dialog — diagnose → ask-back → tuner → generate → result
# ---------------------------------------------------------------------------

_FLOW_STYLES = """
    QDialog#flowDialog { background: #f3f6fa; color: #172b3a; }
    QDialog#flowDialog QLabel { color: #172b3a; }
    QLabel#flowTitle { font-size: 12pt; font-weight: 600; }
    QLabel#flowSection { font-weight: 600; }
    QLabel#flowMuted { color: #4a6375; }
    QLabel#flowNotice { background: #eef4d9; color: #46520f; border: 1px solid #b9c88a;
        border-radius: 4px; padding: 5px 7px; }
    QLabel#flowInlineError { background: #fff1ed; color: #722c1d;
        border: 1px solid #c38170; border-radius: 4px; padding: 4px 6px; }
    QFrame#questionCard { background: #ffffff; border: 1px solid #c4d3dd; border-radius: 6px; }
    QFrame#tunerPanel { background: #eaf1f6; border: 1px solid #c4d3dd; border-radius: 6px; }
    QPlainTextEdit, QTextEdit, QLineEdit, QTextBrowser {
        background: #ffffff; color: #172b3a; border: 1px solid #9aaebc; border-radius: 4px;
        padding: 4px; selection-background-color: #195b83; selection-color: #ffffff; }
    QPushButton { background: #ffffff; color: #172b3a; border: 1px solid #8fa5b4;
        border-radius: 4px; padding: 5px 9px; }
    QPushButton:hover { background: #e4edf3; }
    QPushButton:focus { border: 2px solid #195b83; }
    QPushButton:checked { background: #d9eaf4; border-color: #195b83; }
    QPushButton#flowPrimary { background: #195b83; color: #ffffff; border-color: #195b83;
        font-weight: 600; }
    QPushButton#flowPrimary:hover { background: #124969; }
    QPushButton#flowPrimary:disabled { background: #d5e0e6; color: #425a6a; }
    QPushButton:disabled { background: #e9eef2; color: #526a79; border-color: #afbec8; }
    QScrollBar:vertical { background: #dce5eb; width: 11px; margin: 0; }
    QScrollBar::handle:vertical { background: #6a8394; min-height: 20px; border-radius: 5px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


class _QuestionCard(QFrame):
    """Numbered card: title + reason + example chips + free-text answer."""

    def __init__(self, index: int, question: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("questionCard")
        self.question = question
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)
        title = f"{index}. {question['title']}" + ("  (없으면 비워두세요)" if question.get("optional") else "")
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-weight:600;color:#172b3a;")
        layout.addWidget(self.title_label)
        self.reason_label = QLabel("묻는 이유: " + question["reason"])
        self.reason_label.setStyleSheet("color:#4a6375;")
        self.reason_label.setWordWrap(True)
        layout.addWidget(self.reason_label)
        self.answer = QPlainTextEdit()
        self.answer.setAccessibleName(f"{index}번 질문 답변")
        self.answer.setPlaceholderText("아래 칩을 누르거나 직접 적어 주세요.")
        self.answer.setTabChangesFocus(True)
        self.answer.setMaximumHeight(52)
        layout.addWidget(self.answer)
        if question["chips"]:
            row = QHBoxLayout()
            row.setSpacing(4)
            hint = QLabel("예시 칩 — 누르면 답에 들어가요")
            hint.setStyleSheet("color:#4a6375;font-size:9pt;")
            row.addWidget(hint)
            row.addStretch()
            layout.addLayout(row)
            chips_row = QHBoxLayout()
            chips_row.setSpacing(4)
            self.chip_buttons = []
            for chip in question["chips"]:
                button = QPushButton(chip)
                button.setAccessibleName(f"예시: {chip}")
                button.clicked.connect(lambda _checked=False, text=chip: self._fill(text))
                self.chip_buttons.append(button)
                chips_row.addWidget(button)
            chips_row.addStretch()
            layout.addLayout(chips_row)

    def _fill(self, text):
        current = self.answer.toPlainText().strip()
        if not current:
            self.answer.setPlainText(text)
        elif text not in current:
            self.answer.setPlainText(current + ", " + text)
        self.answer.setFocus()

    def value(self) -> str:
        return self.answer.toPlainText().strip()

    def set_value(self, text: str):
        self.answer.setPlainText(text)


class _ReplySignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class FlowDialog(QDialog):
    """The guided workflow as a modeless dialog hosted by PromptConversation.

    Parameters
    ----------
    parent : QWidget
    initial_text : seed request text (usually the panel's current input).
    provider_getter : callable returning (provider_id, profile|None); used for
        optional AI paths. Returning ('offline', None) disables them.
    on_finished : callable(text, type_label, original) invoked once per accepted
        completed prompt (also fired on multiple composite segments).
    """

    PAGE_INPUT, PAGE_WORK, PAGE_GENERATING, PAGE_RESULT = range(4)

    def __init__(self, parent=None, initial_text="", provider_getter=None, on_finished=None):
        super().__init__(parent)
        self.setObjectName("flowDialog")
        self.setWindowTitle("단계별 완성형 도우미")
        self.setAccessibleName("진단·되묻기·완성형 단계 도우미")
        self.setStyleSheet(_FLOW_STYLES)
        self.resize(560, 640)
        self._provider_getter = provider_getter or (lambda: ("offline", None))
        self._on_finished = on_finished
        self._segments = []
        self._segment_index = 0
        self._dropped_segments = 0
        self._busy_ai = False
        self._ai_kind = None
        # (segment index, flow generation) the in-flight AI call belongs to.
        # The generation bumps on every context change (new segment, restart,
        # close) so a reply landing later under another context is stale.
        self._ai_context = None
        self._flow_generation = 0
        self._signals = _ReplySignals(self)
        self._signals.done.connect(self._ai_done)
        self._signals.failed.connect(self._ai_failed)
        self._last_prompt = ""
        self._last_original = ""
        self._diag = {}
        self._cards = []
        self._answers = {}
        self._tuner_widgets = []
        self.tuner_free = None
        # Completed per-step results, keyed by segment index (memory only —
        # the record dies with the dialog and is never auto-saved to the
        # library). ``saved_text`` mirrors the text at the last explicit save.
        self._step_results = {}
        self._result_view_index = None
        self._back_target = None
        self._hide_notice_shown = False

        self.pages = QStackedWidget(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.addWidget(self.pages)
        self._build_input_page()
        self._build_work_page()
        self._build_generating_page()
        self._build_result_page()
        self.input_edit.setPlainText(initial_text)
        self.input_edit.textChanged.connect(self._refresh_start)
        self._refresh_start()
        self.pages.setCurrentIndex(self.PAGE_INPUT)

    # ----- page builders ---------------------------------------------------

    def _build_input_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        title = QLabel("원하는 일을 한 줄로 적어 주세요")
        title.setObjectName("flowTitle")
        layout.addWidget(title)
        hint = QLabel("예: 이번 주에 한 일을 정리해서 과장님께 낼 부서 주간보고 써줘\n"
                      "입력만으로 빠진 조건을 진단하고, 답을 받아 완성형 프롬프트를 만들어요. "
                      "외부로 보내지 않고 이 기기에서 처리해요.")
        hint.setObjectName("flowMuted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.input_edit = QPlainTextEdit()
        self.input_edit.setAccessibleName("한 줄 요청 입력")
        self.input_edit.setPlaceholderText("할 일을 한 줄로 적어 주세요.")
        self.input_edit.setTabChangesFocus(True)
        layout.addWidget(self.input_edit, 1)
        row = QHBoxLayout()
        row.addStretch()
        self.start_button = QPushButton("진단 시작")
        self.start_button.setObjectName("flowPrimary")
        self.start_button.setAccessibleName("한 줄 요청 진단 시작")
        self.start_button.clicked.connect(self._start)
        row.addWidget(self.start_button)
        layout.addLayout(row)
        self.pages.addWidget(page)

    def _build_work_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.work_content = QWidget()
        self.work_layout = QVBoxLayout(self.work_content)
        self.work_layout.setContentsMargins(4, 4, 4, 4)
        self.work_layout.setSpacing(6)
        scroll.setWidget(self.work_content)
        outer.addWidget(scroll, 1)

        # Composite notice (hidden unless a multi-task request is detected).
        self.composite_notice = QLabel()
        self.composite_notice.setObjectName("flowNotice")
        self.composite_notice.setWordWrap(True)
        self.composite_notice.hide()
        self.work_layout.addWidget(self.composite_notice)

        diag_row = QHBoxLayout()
        self.verdict_label = QLabel()
        self.verdict_label.setObjectName("flowSection")
        self.verdict_label.setWordWrap(True)
        diag_row.addWidget(self.verdict_label, 1)
        self.percent_label = QLabel()
        self.percent_label.setObjectName("flowTitle")
        diag_row.addWidget(self.percent_label)
        self.work_layout.addLayout(diag_row)
        top_row = QHBoxLayout()
        self.step_label = QLabel()
        self.step_label.setObjectName("flowMuted")
        top_row.addWidget(self.step_label, 1)
        self.restart_button = QPushButton("다른 요청 입력하기")
        self.restart_button.setAccessibleName("새 요청으로 돌아가기")
        self.restart_button.clicked.connect(self._restart)
        top_row.addWidget(self.restart_button)
        self.work_layout.addLayout(top_row)

        # Unsaved-finished-results notice + finished-step access (composite
        # recall) — both stay hidden unless a finished result exists.
        self.unsaved_notice_work = QLabel()
        self.unsaved_notice_work.setObjectName("flowNotice")
        self.unsaved_notice_work.setWordWrap(True)
        self.unsaved_notice_work.hide()
        self.work_layout.addWidget(self.unsaved_notice_work)

        self.done_row_work = QWidget()
        self.done_row_work_layout = QHBoxLayout(self.done_row_work)
        self.done_row_work_layout.setContentsMargins(0, 0, 0, 0)
        self.done_row_work_layout.setSpacing(4)
        done_label = QLabel("완료된 작업:")
        done_label.setObjectName("flowMuted")
        self.done_row_work_layout.addWidget(done_label)
        self.done_row_work_layout.addStretch()
        self.done_row_work.hide()
        self.work_layout.addWidget(self.done_row_work)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.work_layout.addWidget(self.summary_label)
        self.counter_label = QLabel()
        self.counter_label.setObjectName("flowMuted")
        self.work_layout.addWidget(self.counter_label)

        self.questions_holder = QWidget()
        self.questions_layout = QVBoxLayout(self.questions_holder)
        self.questions_layout.setContentsMargins(0, 0, 0, 0)
        self.questions_layout.setSpacing(6)
        self.work_layout.addWidget(self.questions_holder)

        ai_row = QHBoxLayout()
        self.ai_question_button = QPushButton("AI에게 보완 질문 추천받기 (선택)")
        self.ai_question_button.setAccessibleName("AI 보완 질문 추천")
        self.ai_question_button.setToolTip("선택한 외부 AI가 이 요청에서 더 물어볼 조건을 제안합니다. 전송을 확인한 뒤에만 요청이 나갑니다.")
        self.ai_question_button.clicked.connect(self._ask_ai_questions)
        ai_row.addWidget(self.ai_question_button)
        ai_row.addStretch()
        self.work_layout.addLayout(ai_row)
        self.ai_question_status = QLabel()
        self.ai_question_status.setObjectName("flowMuted")
        self.ai_question_status.setWordWrap(True)
        self.ai_question_status.hide()
        self.work_layout.addWidget(self.ai_question_status)

        # Pre-generation tuner (S2b): loading text first, axes appear after.
        self.tuner_panel = QFrame()
        self.tuner_panel.setObjectName("tunerPanel")
        tuner_layout = QVBoxLayout(self.tuner_panel)
        tuner_layout.setContentsMargins(8, 6, 8, 6)
        tuner_layout.setSpacing(4)
        self.tuner_loading = QLabel("표현·형식 옵션을 준비하는 중…")
        self.tuner_loading.setObjectName("flowMuted")
        tuner_layout.addWidget(self.tuner_loading)
        self.tuner_content = QWidget()
        self.tuner_axes_layout = QVBoxLayout(self.tuner_content)
        self.tuner_axes_layout.setContentsMargins(0, 0, 0, 0)
        self.tuner_axes_layout.setSpacing(6)
        self.tuner_content.hide()
        tuner_layout.addWidget(self.tuner_content)
        self.work_layout.addWidget(self.tuner_panel)

        bottom = QHBoxLayout()
        self.pref_clear = QPushButton("기억한 답변 지우기")
        self.pref_clear.setAccessibleName("이 유형에서 기억한 답변 지우기")
        self.pref_clear.setToolTip("이 기기에만 저장된, 이 유형의 반복 답변을 지웁니다.")
        self.pref_clear.clicked.connect(self._clear_preferences)
        self.pref_clear.hide()
        bottom.addWidget(self.pref_clear)
        bottom.addStretch()
        self.generate_button = QPushButton("완성형 만들기")
        self.generate_button.setObjectName("flowPrimary")
        self.generate_button.clicked.connect(self._generate)
        bottom.addWidget(self.generate_button)
        self.work_layout.addLayout(bottom)
        self.work_note = QLabel("각 항목에 답해 주시면 다음으로 넘어가요. 답이 없는 항목은 [확인 필요] 표시만 남기고 완성형에 반영돼요.")
        self.work_note.setObjectName("flowMuted")
        self.work_note.setWordWrap(True)
        self.work_layout.addWidget(self.work_note)
        self.work_layout.addStretch()
        self.pages.addWidget(page)

    def _build_generating_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 24, 16, 16)
        layout.setSpacing(8)
        title = QLabel("완성형 프롬프트를 만드는 중…")
        title.setObjectName("flowTitle")
        layout.addWidget(title)
        self.stage_labels = []
        for stage in ("① 입력과 답변 모으는 중", "② 완성형 프롬프트 구성하는 중", "③ 마무리 점검 중"):
            label = QLabel(stage)
            label.setObjectName("flowMuted")
            layout.addWidget(label)
            self.stage_labels.append(label)
        layout.addStretch()
        self.pages.addWidget(page)

    def _build_result_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        head = QHBoxLayout()
        self.result_title = QLabel()
        self.result_title.setObjectName("flowTitle")
        head.addWidget(self.result_title, 1)
        self.back_button = QPushButton()
        self.back_button.setAccessibleName("이전 화면으로 돌아가기")
        self.back_button.clicked.connect(self._back)
        self.back_button.hide()
        head.addWidget(self.back_button)
        self.complete_label = QLabel("완성도 100%")
        self.complete_label.setStyleSheet("font-weight:600;color:#2f6b3a;")
        head.addWidget(self.complete_label)
        layout.addLayout(head)
        self.result_type_label = QLabel()
        self.result_type_label.setObjectName("flowMuted")
        layout.addWidget(self.result_type_label)

        self.unsaved_notice_result = QLabel()
        self.unsaved_notice_result.setObjectName("flowNotice")
        self.unsaved_notice_result.setWordWrap(True)
        self.unsaved_notice_result.hide()
        layout.addWidget(self.unsaved_notice_result)

        self.done_row_result = QWidget()
        self.done_row_result_layout = QHBoxLayout(self.done_row_result)
        self.done_row_result_layout.setContentsMargins(0, 0, 0, 0)
        self.done_row_result_layout.setSpacing(4)
        done_label_result = QLabel("완료된 작업:")
        done_label_result.setObjectName("flowMuted")
        self.done_row_result_layout.addWidget(done_label_result)
        self.done_row_result_layout.addStretch()
        self.done_row_result.hide()
        layout.addWidget(self.done_row_result)

        self.result_edit = QTextEdit()
        self.result_edit.setAccessibleName("완성형 프롬프트 편집")
        self.result_edit.setMinimumHeight(150)
        self.result_edit.setTabChangesFocus(True)
        layout.addWidget(self.result_edit, 2)
        self.render_view = QTextBrowser()
        self.render_view.setAccessibleName("완성형 프롬프트 문서 형태 표시")
        self.render_view.setOpenExternalLinks(False)
        self.render_view.setMinimumHeight(150)
        self.render_view.hide()
        layout.addWidget(self.render_view, 2)
        view_row = QHBoxLayout()
        self.view_toggle = QPushButton("문서 형태로 보기")
        self.view_toggle.setCheckable(True)
        self.view_toggle.toggled.connect(self._toggle_render)
        view_row.addWidget(self.view_toggle)
        view_row.addStretch()
        self.copy_button = QPushButton("프롬프트 복사")
        self.copy_button.clicked.connect(self._copy)
        self.save_button = QPushButton("보관함에 저장")
        self.save_button.clicked.connect(self._save)
        self.new_button = QPushButton("새 요청으로 돌아가기")
        self.new_button.clicked.connect(self._restart)
        view_row.addWidget(self.new_button)
        view_row.addWidget(self.save_button)
        view_row.addWidget(self.copy_button)
        layout.addLayout(view_row)

        refine_title = QLabel("다듬기")
        refine_title.setObjectName("flowSection")
        layout.addWidget(refine_title)
        refine_row = QHBoxLayout()
        self.refine_input = QLineEdit()
        self.refine_input.setAccessibleName("다듬기 한 줄 지시")
        self.refine_input.setPlaceholderText("바꿀 방향을 한 줄로 남겨 주세요 (예: 더 짧게)")
        self.refine_input.setMaxLength(2000)
        self.refine_button = QPushButton("다시 만들기")
        self.refine_button.setToolTip("AI 정리로 다듬습니다. AI 방식이 선택돼 있고 전송을 확인해야만 요청이 나갑니다.")
        self.refine_button.clicked.connect(self._refine)
        self.refine_cancel = QPushButton("취소")
        self.refine_cancel.clicked.connect(lambda: self.refine_input.clear())
        refine_row.addWidget(self.refine_input, 1)
        refine_row.addWidget(self.refine_button)
        refine_row.addWidget(self.refine_cancel)
        layout.addLayout(refine_row)

        handoff = QLabel("외부 AI로 옮기기 — 누르면 프롬프트를 복사한 뒤 사이트를 열어요")
        handoff.setObjectName("flowSection")
        handoff.setWordWrap(True)
        layout.addWidget(handoff)
        self.external_buttons = {}
        ext_row = QHBoxLayout()
        for key, name, _url in EXTERNAL_AI:
            button = QPushButton(name)
            button.setToolTip("프롬프트를 복사하고 " + name + "을(를) 엽니다. 자동으로 넣어 주지는 않으니 열린 화면에 붙여 넣어 주세요.")
            button.setAccessibleName(name + " 열기 (복사한 프롬프트를 붙여 넣기)")
            button.clicked.connect(lambda _c=False, k=key: self._open_external(k))
            self.external_buttons[key] = button
            ext_row.addWidget(button)
        ext_row.addStretch()
        layout.addLayout(ext_row)

        self.next_step_button = QPushButton()
        self.next_step_button.setObjectName("flowPrimary")
        self.next_step_button.clicked.connect(self._next_segment)
        self.next_step_button.hide()
        layout.addWidget(self.next_step_button)
        self.result_status = QLabel()
        self.result_status.setObjectName("flowMuted")
        self.result_status.setWordWrap(True)
        layout.addWidget(self.result_status)
        layout.addStretch()
        self.pages.addWidget(page)

    # ----- flow control ----------------------------------------------------

    def _defer(self, ms, fn):
        """QTimer.singleShot that survives the dialog being closed early."""
        def run():
            try:
                fn()
            except RuntimeError:
                pass
        QTimer.singleShot(ms, run)

    def _refresh_start(self):
        self.start_button.setEnabled(bool(self.input_edit.toPlainText().strip())
                                     and utf16_length(self.input_edit.toPlainText()) <= _MAX_INPUT)

    def _start(self):
        text = self.input_edit.toPlainText().strip()
        if not text or utf16_length(text) > _MAX_INPUT:
            return
        segments = detect_segments(text)
        self._segments = segments[:4]
        self._dropped_segments = len(segments) - len(self._segments)
        self._segment_index = 0
        # A new request owns a fresh result set — finished results of the
        # previous request were memory-only and are dropped here.
        self._step_results = {}
        self._result_view_index = None
        self._back_target = None
        self._hide_notice_shown = False
        self._begin_segment()

    def _begin_segment(self):
        # A new segment is a new context — any AI reply still in flight was
        # issued for the previous one and must be discarded when it lands.
        self._flow_generation += 1
        segment = self._segments[self._segment_index]
        text = segment["text"]
        type_id = segment["type"]
        self._last_original = text
        self._diag = diagnose(text, type_id)
        self._answers = {}
        self._result_view_index = self._segment_index
        self._back_target = None
        # New stage = new task: drop stale tuner controls and free text so a
        # previous segment's choices can never leak into this prompt.
        self._tuner_widgets = []
        self.tuner_free = None
        self._populate_work_page()
        self.pages.setCurrentIndex(self.PAGE_WORK)
        # Tuner loads asynchronously after the questions, like the original flow.
        self.tuner_loading.show()
        self.tuner_content.hide()
        self._defer(600, self._load_tuner)

    def _populate_work_page(self):
        diag = self._diag
        self.verdict_label.setText(diag["verdict"])
        self.percent_label.setText(f"완성도 {diag['percent']}%")
        total = len(self._segments)
        self.step_label.setText(f"{self._segment_index + 1}/{total} 단계 · '{diag['label']}' 유형"
                                if total > 1 else f"'{diag['label']}' 유형으로 진단")
        if total > 1:
            steps = " / ".join(f"{i + 1}. {_TYPE_BY_ID[s['type']]['label']}"
                               for i, s in enumerate(self._segments))
            note = ("여러 작업이 섞인 요청으로 보여요 — 하나씩 순서대로 진행해요.\n"
                    "한꺼번에 시키면 중간에 확인할 수 없어 결과가 어긋날 수 있어요.\n단계: " + steps)
            if self._dropped_segments:
                note += (f"\n나머지 작업 {self._dropped_segments}개는 이번 흐름에서 다루지 않아요 — "
                         "따로 다시 요청해 주세요.")
            self.composite_notice.setText(note)
            self.composite_notice.show()
        else:
            self.composite_notice.hide()
        titles = " · ".join(diag["missing_titles"]) or "없음"
        self.summary_label.setText(f"빠진 조건: {titles}\n"
                                   "아래 항목에 한 번에 답해 주시면 완성형을 만들 수 있어요.")

        while self.questions_layout.count():
            item = self.questions_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._cards = []
        prefs = {}
        try:
            prefs = PreferenceStore().prefilled(self._diag["type"])
        except OSError:
            pass
        self.pref_clear.setVisible(bool(prefs))
        for index, question in enumerate(diag["questions"], 1):
            card = _QuestionCard(index, question)
            if question["id"] in prefs:
                card.set_value(prefs[question["id"]])
                self._answers[question["id"]] = prefs[question["id"]]
            card.answer.textChanged.connect(self._sync_counter)
            self.questions_layout.addWidget(card)
            self._cards.append(card)
        self._sync_counter()
        # AI supplement is optional and consent-gated; hidden without an AI path.
        provider, _profile = self._provider_getter()
        self.ai_question_button.setVisible(provider != "offline"
                                           and len(diag["questions"]) < _MAX_QUESTIONS)
        self.ai_question_status.hide()
        # Reached via "다음 작업 이어가기": finished results stay reachable and
        # any of them still unsaved is announced without blocking progress.
        self._update_done_row(None)
        self._update_unsaved_notice(None)

    def _sync_counter(self):
        for card in self._cards:
            self._answers[card.question["id"]] = card.value()
        answered = sum(1 for card in self._cards if card.value())
        self.counter_label.setText(f"답변 완성도 {answered}/{len(self._cards)}")

    def _load_tuner(self):
        if self.pages.currentIndex() != self.PAGE_WORK:
            return
        while self.tuner_axes_layout.count():
            item = self.tuner_axes_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._tuner_widgets = []
        intro = QLabel("바꾸고 싶은 표현이나 형식이 있다면 아래에서 선택해 보세요 — 아무것도 고르지 않아도 만들 수 있어요\n"
                       "방금 요청에 맞춰 준비한 옵션이에요. 켜 둔 항목은 모두 완성형에 반영돼요.")
        intro.setWordWrap(True)
        intro.setObjectName("flowMuted")
        self.tuner_axes_layout.addWidget(intro)
        meaningless = is_meaningless(self._last_original) and self._diag["type"] == "generic"
        for axis in tuner_axes(self._diag["type"], meaningless):
            holder = QWidget()
            layout = QVBoxLayout(holder)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(2)
            title = QLabel(axis["title"])
            title.setStyleSheet("font-weight:600;")
            layout.addWidget(title)
            if axis["kind"] == "slider":
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(0, len(axis["steps"]) - 1)
                slider.setSingleStep(1)
                slider.setTickPosition(QSlider.TickPosition.TicksBelow)
                slider.setTickInterval(1)
                slider.setAccessibleName(axis["title"])
                value_label = QLabel("기본(미선택)")
                value_label.setObjectName("flowMuted")
                slider.valueChanged.connect(
                    lambda v, steps=axis["steps"], lab=value_label:
                        lab.setText(steps[v] if v else "기본(미선택)"))
                row = QHBoxLayout()
                row.addWidget(slider, 1)
                row.addWidget(value_label)
                layout.addLayout(row)
                self._tuner_widgets.append(("slider", axis["id"], slider, axis["steps"]))
            else:
                row = QHBoxLayout()
                buttons = []
                for option in axis["options"]:
                    button = QPushButton(option)
                    button.setCheckable(True)
                    button.setAccessibleName(axis["title"] + ": " + option)
                    buttons.append(button)
                    row.addWidget(button)
                row.addStretch()
                layout.addLayout(row)
                self._tuner_widgets.append(("chips", axis["id"], buttons, axis["options"]))
            self.tuner_axes_layout.addWidget(holder)
        tone = QWidget()
        tone_layout = QVBoxLayout(tone)
        tone_layout.setContentsMargins(0, 0, 0, 0)
        tone_layout.setSpacing(2)
        tone_title = QLabel("말투 조절")
        tone_title.setStyleSheet("font-weight:600;")
        tone_layout.addWidget(tone_title)
        tone_row = QHBoxLayout()
        tone_buttons = []
        for label in _TONE_CHIPS:
            button = QPushButton(label)
            button.setCheckable(True)
            tone_buttons.append(button)
            tone_row.addWidget(button)
        tone_row.addStretch()
        tone_layout.addLayout(tone_row)
        self._tuner_widgets.append(("tone_chips", "tone_chips", tone_buttons, _TONE_CHIPS))
        self.tuner_axes_layout.addWidget(tone)
        free_label = QLabel("직접 적는 표현·형식")
        free_label.setStyleSheet("font-weight:600;")
        self.tuner_free = QLineEdit()
        self.tuner_free.setAccessibleName("직접 적는 표현·형식")
        self.tuner_free.setPlaceholderText("원하는 표현 방식을 자유롭게 적어 주세요.")
        self.tuner_free.setMaxLength(500)
        free_holder = QWidget()
        free_layout = QVBoxLayout(free_holder)
        free_layout.setContentsMargins(0, 0, 0, 0)
        free_layout.addWidget(free_label)
        free_layout.addWidget(self.tuner_free)
        self.tuner_axes_layout.addWidget(free_holder)
        self.tuner_loading.hide()
        self.tuner_content.show()

    def _collect_tuner(self) -> dict:
        tuner = {"chips": [], "tone_chips": [], "free": "", "length_scale": None, "sliders": {}}
        for kind, axis_id, widget, options in self._tuner_widgets:
            if kind == "slider":
                value = options[widget.value()]
                if widget.value() > 0:  # first step means "unset" by default
                    tuner["sliders"][axis_id] = value
                    if axis_id == "length_scale":
                        tuner["length_scale"] = value
                    elif axis_id == "status_detail":
                        tuner["chips"].append("처리 상황 " + value)
            elif kind == "tone_chips":
                tuner["tone_chips"] = [options[i] for i, b in enumerate(widget) if b.isChecked()]
            else:
                tuner["chips"].extend(options[i] for i, b in enumerate(widget) if b.isChecked())
        free = getattr(self, "tuner_free", None)
        if free is not None and free.text().strip():
            tuner["free"] = free.text().strip()
        return tuner

    def _generate(self):
        if self._busy_ai:  # an AI call is in flight — never interleave prompts
            self.work_note.setText("AI 응답을 기다리는 중이에요. 끝나면 다시 눌러 주세요.")
            return
        self._sync_counter()
        self.pages.setCurrentIndex(self.PAGE_GENERATING)
        for label in self.stage_labels:
            label.setStyleSheet("color:#4a6375;")
        self._gen_stage = 0
        self._advance_stage()

    def _advance_stage(self):
        if self.pages.currentIndex() != self.PAGE_GENERATING:
            return
        if self._gen_stage < len(self.stage_labels):
            self.stage_labels[self._gen_stage].setStyleSheet("color:#172b3a;font-weight:600;")
            self._gen_stage += 1
            self._defer(320, self._advance_stage)
        else:
            self._finish_generation()

    def _finish_generation(self):
        prompt = build_prompt(self._diag["type"], self._last_original,
                              self._diag["questions"], self._answers,
                              self._diag["extracted"], self._collect_tuner(),
                              seeded=self._diag.get("seeded_topics"))
        self._show_result(prompt)
        self._record_preferences()

    def _record_preferences(self):
        try:
            store = PreferenceStore()
            for card in self._cards:
                store.record(self._diag["type"], card.question["id"], card.value())
        except OSError:
            pass

    def _record_current_result(self, prompt: str):
        """Keep this segment's finished text under its index (memory only)."""
        rec = self._step_results.setdefault(self._segment_index, {
            "label": self._diag["label"],
            "original": self._last_original,
            "revisions": [f"{card.question['title']}: {card.value()}"
                          for card in self._cards if card.value()],
            "text": "",
            "saved_text": None,
        })
        rec["text"] = prompt

    def _sync_result_record(self):
        """Carry the on-screen edits back into the record being viewed."""
        if self.pages.currentIndex() != self.PAGE_RESULT:
            return
        rec = self._step_results.get(self._result_view_index)
        if rec is not None:
            rec["text"] = self.result_edit.toPlainText()

    def _render_record(self, index: int):
        """Show one recorded result; the current segment stays fully editable."""
        rec = self._step_results[index]
        current = index == self._segment_index
        self._result_view_index = index
        self.result_edit.setPlainText(rec["text"])
        self.render_view.setHtml(render_prompt_html(rec["text"]))
        if current:
            self.result_title.setText("완성형 프롬프트")
            self.result_type_label.setText(f"'{self._diag['label']}' 형식으로 만들었어요")
        else:
            self.result_title.setText(f"{index + 1}단계 완료 결과")
            self.result_type_label.setText(
                f"'{rec['label']}' 형식으로 만든 결과예요 — "
                "복사하거나 보관함에 저장할 수 있어요")
        provider, _profile = self._provider_getter()
        # AI 다듬기는 진행 중인 단계의 맥락만 쓰므로 완료 결과 열람 중에는 잠근다.
        self.refine_input.setEnabled(current)
        self.refine_button.setEnabled(current and provider != "offline" and not self._busy_ai)
        if current and provider == "offline":
            self.refine_button.setToolTip("다듬기 재생성은 AI 정리 방식이 필요해요. 대화창의 정리 방식에서 AI를 선택해 주세요.")
        remaining = len(self._segments) - self._segment_index - 1
        if current and remaining > 0:
            nxt = _TYPE_BY_ID[self._segments[self._segment_index + 1]["type"]]["label"]
            self.next_step_button.setText(f"다음 작업 이어가기 ({self._segment_index + 2}/{len(self._segments)}): {nxt}")
            self.next_step_button.show()
        else:
            self.next_step_button.hide()
        self.back_button.setVisible(not current)

    def _show_result(self, prompt: str, refined: bool = False):
        self._last_prompt = prompt
        self._record_current_result(prompt)
        self._render_record(self._segment_index)
        if refined:
            self.result_type_label.setText(
                f"'{self._diag['label']}' 형식으로 만들었어요 · 다듬은 결과")
        self.result_status.setText("이대로 바로 사용해도 돼요 — 직접 고치거나 내용을 더 넣으면 정확해져요.")
        self.pages.setCurrentIndex(self.PAGE_RESULT)
        self._update_done_row(self._segment_index)
        self._update_unsaved_notice(self._segment_index)

    # ----- finished-step recall (memory only; never auto-saved) -------------

    def _view_step(self, index: int):
        """Open a finished step's result for reading, copying and saving."""
        if index not in self._step_results:
            return
        from_work = self.pages.currentIndex() == self.PAGE_WORK
        self._sync_result_record()
        if index != self._segment_index:
            self._back_target = self.PAGE_WORK if from_work else None
            self.back_button.setText("질문 화면으로 돌아가기" if from_work
                                     else "이번 단계 결과로 돌아가기")
        self._render_record(index)
        self.result_status.setText(
            "완료된 작업 결과를 보고 있어요 — 복사·저장 후 돌아가기로 진행을 이어갈 수 있어요."
            if index != self._segment_index else
            "이대로 바로 사용해도 돼요 — 직접 고치거나 내용을 더 넣으면 정확해져요.")
        self.pages.setCurrentIndex(self.PAGE_RESULT)
        self._update_done_row(index)
        self._update_unsaved_notice(index)

    def _back(self):
        self._sync_result_record()
        if self._back_target == self.PAGE_WORK:
            self._result_view_index = self._segment_index
            self._back_target = None
            self.pages.setCurrentIndex(self.PAGE_WORK)
            self._update_done_row(None)
            self._update_unsaved_notice(None)
        else:
            self._back_target = None
            self._view_step(self._segment_index)

    def _fill_done_row(self, layout, viewing):
        """(Re)build the '완료된 작업' chips inside one row layout."""
        while layout.count() > 1:
            item = layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                # Unparent first so a pending-deletion chip can never be
                # found as a child of the row again.
                widget.setParent(None)
                widget.deleteLater()
        for index in sorted(self._step_results):
            rec = self._step_results[index]
            text = f"{_CIRCLED[index]} {rec['label']}"
            if rec.get("saved_text") == rec["text"]:
                text += " · 저장됨"
            button = QPushButton(text)
            button.setCheckable(True)
            button.setChecked(index == viewing)
            button.setAccessibleName(f"{index + 1}단계 결과 다시 열기")
            button.clicked.connect(lambda _checked=False, i=index: self._view_step(i))
            layout.addWidget(button)
        layout.addStretch()

    def _update_done_row(self, viewing):
        """Refresh both chip rows; ``viewing`` marks the chip being looked at."""
        self._fill_done_row(self.done_row_work_layout, viewing)
        self._fill_done_row(self.done_row_result_layout, viewing)
        self.done_row_work.setVisible(bool(self._step_results))
        self.done_row_result.setVisible(len(self._step_results) > 1)

    def _update_unsaved_notice(self, viewing):
        """Announce finished results that were never saved to the library.

        The work page counts every record (all are finished previous steps);
        the result page leaves out the record currently on screen.
        """
        def count(exclude):
            return sum(1 for i, rec in self._step_results.items()
                       if i != exclude and rec.get("saved_text") != rec["text"])
        work_unsaved = count(None)
        result_unsaved = count(viewing)

        def text(number):
            return (f"보관함에 저장하지 않은 완료 결과가 {number}건 있어요 — "
                    "'완료된 작업'에서 다시 열람·복사·저장할 수 있어요." if number else "")
        self.unsaved_notice_work.setText(text(work_unsaved))
        self.unsaved_notice_work.setVisible(bool(work_unsaved))
        self.unsaved_notice_result.setText(text(result_unsaved))
        self.unsaved_notice_result.setVisible(bool(result_unsaved))

    def _notice_before_hide(self):
        """Non-blocking heads-up when the window goes away with unsaved results."""
        self._sync_result_record()
        unsaved = sum(1 for rec in self._step_results.values()
                      if rec.get("saved_text") != rec["text"])
        if not unsaved or self._hide_notice_shown:
            return
        self._hide_notice_shown = True
        text = (f"보관함에 저장하지 않은 완료 결과가 {unsaved}건 있습니다. "
                "닫힌 창의 결과는 다시 열 수 없습니다.")
        # Prefer the host window's inline notice so the warning lands where
        # the user still is; a standalone dialog gets a non-modal box so the
        # close itself is never held back either way.
        notify = getattr(self.parent(), "_show_notice", None)
        if callable(notify):
            try:
                notify(text)
                return
            except Exception:  # noqa: BLE001 - the notice must not break closing
                pass
        box = QMessageBox(QMessageBox.Icon.Information, "저장하지 않은 결과 안내",
                          text, QMessageBox.StandardButton.Ok)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.show()
        self._notice_box = box

    def closeEvent(self, event):
        self._flow_generation += 1  # the issuing context is gone with the window
        self._notice_before_hide()
        super().closeEvent(event)

    def done(self, result):
        self._flow_generation += 1
        # ESC/reject path hides the dialog without a closeEvent — same notice.
        self._notice_before_hide()
        super().done(result)

    def _toggle_render(self, rendered):
        self.render_view.setVisible(rendered)
        self.result_edit.setVisible(not rendered)
        self.view_toggle.setText("편집으로 돌아가기" if rendered else "문서 형태로 보기")

    def _next_segment(self):
        if self._segment_index + 1 < len(self._segments):
            self._sync_result_record()
            self._segment_index += 1
            self._begin_segment()

    def _restart(self):
        # Abandoning the request abandons its context too — a late AI reply
        # must not land on whatever comes next.
        self._flow_generation += 1
        # Same tuner isolation as a new segment — old chips/free text die here.
        self._sync_result_record()
        self._tuner_widgets = []
        self.tuner_free = None
        self.input_edit.clear()
        self.pages.setCurrentIndex(self.PAGE_INPUT)
        self.input_edit.setFocus()

    def _clear_preferences(self):
        try:
            PreferenceStore().clear(self._diag["type"])
        except OSError:
            pass
        self.pref_clear.hide()
        self.counter_label.setText("기억한 답변을 지웠어요. " + self.counter_label.text())

    # ----- output actions --------------------------------------------------

    def _copy(self):
        if copy_to_clipboard(self.result_edit.toPlainText()):
            self.copy_button.setText("복사됨")
            self.result_status.setText("프롬프트를 복사했어요. 열린 화면에 붙여 넣어 쓰면 돼요.")
            self._defer(1500, lambda: self.copy_button.setText("프롬프트 복사"))
        else:
            self.result_status.setText("복사하지 못했어요. 다시 시도해 주세요.")

    def _save(self):
        from optimizer_library import LibraryStore
        index = self._result_view_index
        rec = self._step_results.get(index)
        viewing_current = index == self._segment_index
        prompt = rec["original"] if rec else self._last_original
        label = rec["label"] if rec else self._diag["label"]
        result = self.result_edit.toPlainText()
        if not prompt.strip() or not result.strip():
            self.result_status.setText("저장할 내용이 없어요.")
            return
        title = ""
        for ch in prompt.strip():
            if utf16_length(title + ch) > 120:
                break
            title += ch
        # The library accepts revision entries up to 2,000 UTF-16 units —
        # clip longer answers instead of letting the save fail outright.
        if rec is not None:
            raw_revisions = rec["revisions"]
        else:
            raw_revisions = [f"{card.question['title']}: {card.value()}"
                             for card in self._cards if card.value()]
        revisions, clipped = [], False
        for entry in raw_revisions:
            if utf16_length(entry) > 2000:
                entry = _clip_utf16(entry, 2000)
                clipped = True
            revisions.append(entry)
            if len(revisions) >= 30:
                break
        item = {"id": str(uuid.uuid4()), "title": title, "prompt": prompt,
                "result": result, "task": "writing", "detail": "detailed",
                "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "mode": label, "archived": False, "stale": False,
                "syeonRevisions": revisions}
        try:
            LibraryStore().merge([item])
        except (OSError, ValueError) as exc:
            self.result_status.setText(f"저장하지 못했어요: {exc}")
            return
        if rec is not None:
            rec["text"] = result
            rec["saved_text"] = result
        self._update_done_row(index)
        self._update_unsaved_notice(index)
        self.save_button.setText("저장됨")
        status = ("보관함에 저장했어요. 보관함에서 다시 열 수 있어요." if viewing_current
                  else f"{index + 1}단계 결과를 보관함에 저장했어요. 보관함에서 다시 열 수 있어요.")
        self.result_status.setText(status + (" 긴 답변은 잘려 저장됐어요." if clipped else ""))
        self._defer(1500, lambda: self.save_button.setText("보관함에 저장"))
        self._accept_if_done(result, label, prompt)

    def _accept_if_done(self, text, label, original):
        if self._on_finished is not None:
            try:
                # Register the text as the user sees it (post-edit), matching
                # what was just saved to the library.
                self._on_finished(text, label, original)
            except Exception:
                pass

    def _open_external(self, key):
        copied = open_external(key, self.result_edit.toPlainText())
        name = dict((k, n) for k, n, _u in EXTERNAL_AI)[key]
        self.result_status.setText(
            f"{name}을(를) 열었어요. 내용이 자동으로 들어가지 않으면 Ctrl+V로 직접 붙여 주세요."
            if copied else f"{name}을(를) 열었어요. 복사가 안 됐으면 '프롬프트 복사'로 직접 복사해 주세요.")

    # ----- optional AI paths (consent-gated) --------------------------------

    def _ai_send(self, kind, work):
        if self._busy_ai:
            return
        self._busy_ai = True
        self._ai_kind = kind
        # Tag the request with the context it was issued under so a late
        # reply can be dropped instead of bleeding into another segment.
        self._ai_context = (self._segment_index, self._flow_generation)
        self.ai_question_button.setEnabled(False)
        self.refine_button.setEnabled(False)
        self.generate_button.setEnabled(False)  # block generation while AI is in flight

        def run():
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - sanitized below
                try:
                    self._signals.failed.emit(str(exc))
                except RuntimeError:
                    pass
            else:
                try:
                    self._signals.done.emit(result)
                except RuntimeError:
                    pass

        threading.Thread(target=run, daemon=True, name="flow-ai").start()

    def _ask_ai_questions(self):
        provider, profile = self._provider_getter()
        if provider == "offline" or self._busy_ai:
            return
        existing = [q["title"] for q in self._diag["questions"]]
        if profile is not None and profile.provider == "ollama":
            title = "로컬 AI 처리 확인"
            detail = (f"원래 요청, 현재 진단 정보와 이미 묻는 질문 목록을 이 기기의 "
                      f"{profile.provider} / {profile.model} 로컬 서버에서 처리합니다.\n\n진행하시겠어요?")
        else:
            title = "외부 AI 전송 확인"
            detail = (f"원래 요청, 현재 진단 정보와 이미 묻는 질문 목록을 {provider}에 전송합니다. "
                      "제공자는 내용을 저장하거나 서비스 개선에 사용할 수 있습니다.\n\n전송하시겠어요?")
        box = QMessageBox(QMessageBox.Icon.Question, title, detail,
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.finished.connect(lambda value: self._ai_questions_confirmed(value, provider, profile, existing))
        box.open()

    def _ai_questions_confirmed(self, value, provider, profile, existing):
        if value != QMessageBox.StandardButton.Yes:
            return
        self.ai_question_status.setText("AI가 보완 질문을 만드는 중…")
        self.ai_question_status.show()

        def work():
            from optimizer_ai import ask_ai
            payload = {"original_request": self._last_original,
                       "task_type": self._diag["label"],
                       "already_asked": existing}
            text = ask_ai(provider, _QUESTION_HINTS, payload, profile=profile)
            lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
                     for line in text.splitlines()]
            return [line for line in lines if 8 <= len(line) <= 80 and line not in existing][:2]

        self._ai_send("questions", work)

    def _refine(self):
        # Refine only ever targets the live segment's result — a finished
        # step's record stays read-only for this path.
        if self._result_view_index != self._segment_index:
            return
        provider, profile = self._provider_getter()
        if provider == "offline" or self._busy_ai:
            self.result_status.setText("다듬기 재생성은 AI 정리 방식이 필요해요. 대화창의 정리 방식에서 AI를 선택해 주세요.")
            return
        instruction = self.refine_input.text().strip()
        if not instruction:
            self.result_status.setText("다듬기 방법을 한 줄로 적어 주세요.")
            return
        if profile is not None and profile.provider == "ollama":
            title = "로컬 AI 처리 확인"
            detail = (f"완성형 프롬프트와 다듬기 지시를 이 기기의 "
                      f"{profile.provider} / {profile.model} 로컬 서버에서 처리합니다.\n\n진행하시겠어요?")
        else:
            title = "외부 AI 전송 확인"
            detail = (f"완성형 프롬프트와 다듬기 지시를 {provider}에 전송합니다. "
                      "제공자는 내용을 저장하거나 서비스 개선에 사용할 수 있습니다.\n\n전송하시겠어요?")
        box = QMessageBox(QMessageBox.Icon.Question, title, detail,
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.finished.connect(lambda value: self._refine_confirmed(value, provider, profile, instruction))
        box.open()

    def _refine_confirmed(self, value, provider, profile, instruction):
        if value != QMessageBox.StandardButton.Yes:
            return
        previous = self.result_edit.toPlainText()
        context = "\n".join(f"{card.question['title']}: {card.value()}"
                            for card in self._cards if card.value())
        self.result_status.setText("AI로 다듬는 중… 응답에 1분 이상 걸릴 수 있어요.")

        def work():
            from optimizer_ai import optimize_ai
            reply = optimize_ai(self._last_original, context=context, provider=provider,
                                revision=instruction, previous_result=previous,
                                task="writing", detail="detailed",
                                profile=profile)
            return reply["result"]

        self._ai_send("refine", work)

    def _restore_ai_controls(self):
        """Re-enable the controls an in-flight AI call had frozen.

        Refine stays bound to the live segment's result: while a finished
        step is being read, both its input and button remain locked — the
        same gate ``_render_record`` applies.
        """
        self.ai_question_button.setEnabled(True)
        self.generate_button.setEnabled(True)
        provider, _profile = self._provider_getter()
        viewing_current = self._result_view_index == self._segment_index
        self.refine_button.setEnabled(viewing_current and provider != "offline")

    def _ai_context_is_stale(self):
        """True when the in-flight request was issued under another context."""
        return (self._segment_index, self._flow_generation) != self._ai_context

    def _discard_stale_ai(self):
        """A late reply must not move the user or write into a new record."""
        self.ai_question_status.setText(
            "이전 단계에 요청한 AI 응답이 늦게 도착해 반영하지 않았어요.")
        self.ai_question_status.show()

    def _ai_done(self, result):
        self._busy_ai = False
        self._restore_ai_controls()
        if self._ai_context_is_stale():
            self._discard_stale_ai()
            return
        if self._ai_kind == "questions":
            if not result:
                self.ai_question_status.setText("추가로 물을 만한 질문을 찾지 못했어요.")
                return
            base = len(self._cards)
            for offset, question_text in enumerate(result, 1):
                topic = _topic(f"ai_extra_{offset}", question_text,
                               "AI가 이 요청에서 더 필요하다고 본 정보예요.",
                               [], "추가 확인", optional=True)
                card = _QuestionCard(base + offset, topic)
                card.answer.textChanged.connect(self._sync_counter)
                self.questions_layout.addWidget(card)
                self._cards.append(card)
            self.ai_question_status.setText(f"AI 보완 질문 {len(result)}개를 아래에 붙였어요.")
            self._sync_counter()
        else:
            self._show_result(result, refined=True)
            self.result_status.setText("다듬은 결과를 확인해 주세요. AI 결과이므로 사용 전 검토가 필요해요.")

    def _ai_failed(self, message):
        self._busy_ai = False
        self._restore_ai_controls()
        if self._ai_context_is_stale():
            self._discard_stale_ai()
            return
        safe = "연결 실패 또는 처리 중 오류가 발생했습니다. 인터넷 연결과 설정을 확인하고 다시 시도해 주세요."
        if message in _KNOWN_AI_ERRORS:
            safe = message
        if self._ai_kind == "questions":
            self.ai_question_status.setText("보완 질문을 만들지 못했어요: " + safe)
        else:
            self.result_status.setText("다듬지 못했어요: " + safe + "\n이전 결과는 그대로 남아 있습니다.")


_QUESTION_HINTS = (
    "당신은 교육기관 직원의 업무 요청을 보강하는 도우미입니다.\n"
    "입력 JSON의 original_request(원래 요청)와 task_type(업무 유형), already_asked(이미 묻는 질문)을 보고, "
    "완성형 프롬프트를 만들기 위해 더 꼭 필요한 확인 질문을 최대 2개만 제안하세요.\n"
    "- 이미 묻는 질문과 겹치지 않는 것만 고릅니다.\n"
    "- 질문만 한 줄에 하나씩 출력합니다. 번호·머리말·설명은 쓰지 않습니다.\n"
    "- 한국어로 씁니다. 질문은 80자 이내로 짧게 씁니다.\n"
    "- 요청에 없는 사실을 지어내지 않고, 묻는 형태로만 씁니다."
)

_KNOWN_AI_ERRORS = {
    "모델 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
    "AI 서비스에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요.",
    "AI 요청에 실패했습니다.", "정리 요청을 취소했습니다.",
    "서비스 사용량 제한에 도달했습니다 (HTTP 429). 잠시 후 다시 시도해 주세요.",
}


def show_flow_dialog(parent=None, initial_text="", provider_getter=None, on_finished=None) -> FlowDialog:
    """Open without a nested event loop; callers keep the returned reference."""
    dialog = FlowDialog(parent, initial_text, provider_getter, on_finished)
    dialog.show()
    return dialog
