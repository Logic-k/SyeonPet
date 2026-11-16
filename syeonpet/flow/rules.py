"""Type classification, condition extraction, and ask-back rules for prompt flow."""
from __future__ import annotations

import re
import sys

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


def _project_plan_topics(_text, _ex):
    """T9 education business & budget planning."""
    return [
        _topic("goal", "사업 목적과 핵심 대상",
               "추진 배경과 누구를 지원하는 사업인지가 계획서의 중심이에요.",
               ["기초학력 부진 학생 맞춤 지원", "전교생 안전·인성 프로그램 운영", "교원 업무 경감·디지털 역량 강화"],
               "사업 목적·대상",
               covered=r"목적|취지|대상\s*[:：]|지원\s*대상",
               seed=r"(?:목적|취지|지원\s*대상|대상)\s*[:：]\s*([^,.\n;]+)"),
        _topic("schedule_plan", "추진 일정과 세부 활동",
               "월별·단계별 실행 계획과 주요 프로그램이 있어야 구체적인 계획서가 돼요.",
               ["1학기(3~7월) 운영 · 월 2회", "사전 준비 → 본 운영 → 결과 공유", "학년별 순회·집중 운영"],
               "추진 일정·활동",
               covered=r"추진\s*일정|세부\s*활동|단계별|로드맵|분기별|학기별",
               seed=r"(?:추진\s*일정|세부\s*활동|일정)\s*[:：]\s*([^,.\n;]+)"),
        _topic("budget", "예산·인력과 산출 근거",
               "예산이나 강사·담당 인력 범위가 있어야 실현 가능한 계획을 세울 수 있어요.",
               ["강사비·교재비 포함 총액 기재", "교내 기존 인력·예산 활용", "별도 예산 미확정 — 검토용"],
               "예산·인력",
               covered=r"(?:\d[\d,]*\s*(?:원|천원|만원|억)|예산\s*[:：]|산출\s*근거|인력\s*배치)",
               seed=r"(?:예산|소요\s*예산|인력|산출\s*근거)\s*[:：]\s*([^,.\n;]+)|"
                    r"(\d[\d,]*(?:\.\d+)?\s*(?:만원|천원|억원|원)[^,.\n;]{0,15})"),
        _topic("outcome", "성과 확인 지표·증빙",
               "사업이 잘 됐는지 무엇으로 확인할지 정하면 계획이 완성돼요.",
               ["참여율 및 설문 만족도", "활동 결과물·이수 건수", "해당 없음 — 담당자 판단"],
               "성과 지표",
               optional=True,
               covered=r"성과\s*지표|평가\s*기준|만족도|참여율|증빙",
               seed=r"(?:성과\s*지표|평가\s*기준|증빙)\s*[:：]\s*([^,.\n;]+)"),
    ]


def _student_record_topics(_text, _ex):
    """T10 student records & evaluation guidance (non-identifying only)."""
    return [
        _topic("subject_grade", "대상 학년과 교과·영역 (비식별)",
               "학교급·학년과 교과(또는 창의적 체험활동)에 맞춰 서술 기준이 달라져요.",
               ["초등학교 고학년 국어·사회", "중학교 수학 수행평가·세특", "창의적 체험활동 · 진로 영역"],
               "학년·영역",
               covered=r"(?:초등|중학|고등|\d\s*학년|국어|영어|수학|사회|과학|창체|동아리|자율|진로|행동특성)",
               seed=r"(?<![0-9A-Za-z가-힣])([가-힣0-9\s·]{1,20}"
                    r"(?:학년|국어|영어|수학|사회|과학|음악|미술|체육|실과|기술|정보|창체|동아리|자율|진로|행동특성))"),
        _topic("evidence", "관찰된 활동과 사실 근거 (실명 제외)",
               "실제 관찰한 행동·활동 사실만 들어가야 근거 없는 미사여구를 막을 수 있어요.",
               ["모둠 토의에서 자료 요약·발표 역할 수행", "실험 설계 과정을 단계별로 기록하고 공유", "규칙적인 독서·성찰 일지 작성 지속"],
               "관찰 사실 근거",
               covered=r"(?:활동\s*내용|관찰\s*내용|사례|특징|역량|참여\s*모습)\s*[:：]",
               seed=r"(?:활동\s*내용|관찰\s*내용|사례|특징)\s*[:：]\s*([^.\n;]+)"),
        _topic("constraints_note", "분량 제한·기재 유의점",
               "바이트·글자 수 제한이나 제외할 표현을 알면 바로 쓸 수 있게 다듬어요.",
               ["500자(또는 1500바이트) 이내 간결 서술", "추측·과장 표현 제외하고 사실 위주", "특정 단어 반복 피하기"],
               "기재 유의점",
               optional=True,
               covered=r"바이트|byte|유의사항|금지|제외",
               seed=r"(?:유의사항|주의사항|제외)\s*[:：]\s*([^,.\n;]+)"),
    ]


def _audit_response_topics(_text, _ex):
    """T11 audit / inspection response."""
    return [
        _topic("finding", "지적·점검된 핵심 사안",
               "무엇이 지적되거나 확인 요청을 받았는지 명확해야 소명·개선 방향이 맞아요.",
               ["집행 서류·증빙 일부 누락", "절차상 사전 협의 기록 미비", "시설·안전 점검 보완 권고"],
               "지적·점검 사안",
               covered=r"(?:지적\s*사항|점검\s*내용|사안)\s*[:：]",
               seed=r"(?:지적\s*사항|점검\s*내용|사안)\s*[:：]\s*([^,.\n;]+)"),
        _topic("basis_facts", "관련 규정·사실 경위",
               "확인된 규정 근거와 실제 경위를 분리해야 객관적인 소명이 돼요.",
               ["당시 적용 지침 및 결재 내역 확인됨", "담당 인계 과정의 착오 — 사후 보완 완료", "규정 적용 여부 재확인 중"],
               "근거·사실 경위",
               covered=r"(?:경위|규정|근거|법령|지침)\s*[:：]",
               seed=r"(?:경위|규정|근거|지침)\s*[:：]\s*([^,.\n;]+)"),
        _topic("remedy", "시정·재발 방지 조치 계획",
               "이미 끝낸 조치와 앞으로 바꿀 절차를 나눠 적어야 신뢰를 얻어요.",
               ["누락 서류 보완 첨부 및 점검표 도입", "담당자 교육·교차 확인 절차 신설", "보완 기한 내 완료 보고 예정"],
               "개선·재발방지",
               covered=r"(?:개선|시정|재발\s*방지|보완\s*조치)\s*[:：]",
               seed=r"(?:개선|시정|재발\s*방지|보완\s*조치)\s*[:：]\s*([^,.\n;]+)"),
    ]


def _lesson_activity_topics(_text, _ex):
    """T12 curriculum / class lesson activity design."""
    return [
        _topic("target_topic", "대상 학년·교과와 수업 주제",
               "학생 수준과 교과 특성에 맞춰 활동 난이도와 발문을 정해요.",
               ["초등 4학년 · 지역 문제 탐색", "중등 영어 · 실생활 의사소통 표현", "전 학년 · 디지털 시민성·안전 교육"],
               "학년·교과·주제",
               covered=r"(?:\d\s*학년|국어|영어|수학|사회|과학|도덕|실과|미술|음악|체육|정보|주제\s*[:：])",
               seed=r"(?:주제|단원)\s*[:：]\s*([^,.\n;]+)"),
        _topic("objective_flow", "학습 목표와 희망 활동 흐름",
               "원하는 활동 형태(토론·실습·게임·모둠 등)를 알면 수업안을 바로 짤 수 있어요.",
               ["개념 이해 → 짝 활동 → 한 줄 성찰", "영상·질문 도입 → 모둠 토의 → 발표", "기초 연습 문제 중심 + 오답 확인"],
               "목표·활동 흐름",
               covered=r"(?:학습\s*목표|성취기준|활동\s*흐름|모둠|토론|실습)\s*[:：]?",
               seed=r"(?:학습\s*목표|성취기준|활동)\s*[:：]\s*([^,.\n;]+)"),
        _topic("materials", "차시·시간과 준비물·수준 고려",
               "시간 배분(예: 40분/45분)이나 기초·심화 학생 배려 조건을 넣을 수 있어요.",
               ["1차시 40분 기준 · 활동지 포함", "학습 속도가 느린 학생용 힌트 추가", "디지털 기기 없이 종이·필기도구만 사용"],
               "차시·준비물",
               optional=True,
               covered=r"(?:\d+\s*차시|\d+\s*분|준비물|수준)",
               seed=r"(\d+\s*차시(?:\s*\d+\s*분)?[^,.\n;]{0,15}|준비물\s*[:：]\s*[^,.\n;]+)"),
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
        "signals": r"상담.{0,12}(신청|회신|답변|문자|연락|안내|답장|답신)|상담\s*(?:일정|시간|신청|답장|답신)",
    },
    {
        "id": "complaint_reply", "label": "민원 회신",
        "role": "민원·항의 회신 문서 작성 전문가", "doc": "민원 회신",
        "sections": 4, "topics": _complaint_topics,
        "signals": r"민원인|민원.{0,10}(회신|답변|공문|대응|답장|답신)|항의|불만|클레임|진정|청구",
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
        "signals": r"행사|체육대회|현장체험|현장학습|졸업식|입학식|축제|발표회|총회|설명회|학예회",
    },
    {
        "id": "project_plan", "label": "사업·예산 계획",
        "role": "교육행정 사업·예산 계획서 작성 전문가", "doc": "사업·예산 추진 계획서",
        "sections": 5, "topics": _project_plan_topics,
        "signals": r"(?:사업|추진|운영|예산|추경|정산)\s*(?:계획서?|기획안?|편성|집행\s*계획|검토)|예산\s*운용|사업\s*기획",
    },
    {
        "id": "student_record", "label": "학생 기록·평가 안내",
        "role": "학생 활동 관찰 기록 및 평가 안내 작성 도우미", "doc": "학생 기록·평가 문구",
        "sections": 5, "topics": _student_record_topics,
        "signals": r"(?:생활기록부|생기부|세특|행동특성|종합의견|성적\s*안내|평가\s*안내|수행평가)(?!.*(?:가정통신문|통신문|안내문))",
    },
    {
        "id": "audit_response", "label": "감사·점검 대응",
        "role": "교육행정 감사 소명·점검 개선안 작성 전문가", "doc": "감사·점검 대응 자료",
        "sections": 5, "topics": _audit_response_topics,
        "signals": r"감사\s*(?:대응|답변|자료|소명|처분|지적)|현장\s*점검|모니터링\s*대응|지적\s*사항",
    },
    {
        "id": "lesson_activity", "label": "수업·활동 지도안",
        "role": "교육과정·교수학습 활동 설계 전문가", "doc": "수업·학습 활동 지도안",
        "sections": 4, "topics": _lesson_activity_topics,
        "signals": r"수업\s*(?:지도안|설계|계획|안)|교수학습|교수·학습|학습\s*활동|교육과정\s*(?:운영|계획|재구성)|단원\s*계획|활동지",
    },
    {
        "id": "notice_letter", "label": "학교 안내문",
        "role": "학교 안내·통신문 작성 전문가", "doc": "가정통신문·안내문",
        "sections": 5, "topics": _notice_topics,
        "signals": r"가정통신문|통신문|알림장|방과후|안내문|안내장|안내\s*문자|공지|통지|newsletter|notice",
    },
]

_GENERIC = {
    "id": "generic", "label": "일반 요청",
    "role": "사용자의 업무 요청을 정확히 수행하는 문서 작성 보조자", "doc": "요청된 작업",
    "sections": 4, "topics": _generic_topics, "signals": "",
}

_TYPE_BY_ID = {t["id"]: t for t in _TYPES} | {"generic": _GENERIC}

# Classification order matters: reply types before document/specialized types.
_SIGNAL_ORDER = [
    "counsel_reply", "complaint_reply", "official_reply", "weekly_report",
    "minutes", "student_record", "audit_response", "lesson_activity",
    "project_plan", "general_report", "event_notice", "notice_letter",
]


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


def _completion_text(answered: int, total: int) -> str:
    """Result-page completeness label: honest about skipped questions."""
    if total <= 0 or answered >= total:
        return "완성도 100%"
    return f"답변 {answered}/{total} · 일부 미답변"


def _is_local(profile) -> bool:
    return profile is not None and profile.provider == "ollama"


def _ai_confirm_title(profile) -> str:
    return "로컬 AI 처리 확인" if _is_local(profile) else "외부 AI 전송 확인"


def _service_label(provider, profile) -> str:
    """User-facing destination name — never the raw provider id."""
    if profile is not None:
        loc = "로컬" if _is_local(profile) else "외부"
        return f"{profile.provider} / {profile.model} ({loc})"
    from syeonpet.chat.executor import destination_label
    return destination_label(provider)


def summarize_recognized_conditions(extracted: dict, seeded_topics: list[dict] | None = None) -> list[str]:
    """Return human-readable chips for conditions already detected in the user's input."""
    chips: list[str] = []
    pairs = (
        ("대상", extracted.get("explicit_audience") or extracted.get("recipient")),
        ("기관", extracted.get("organization")),
        ("문체", extracted.get("tone")),
        ("분량", extracted.get("length")),
        ("매체", extracted.get("medium")),
        ("발신", extracted.get("sender")),
        ("일시", extracted.get("specific_when") or extracted.get("when")),
        ("장소", extracted.get("specific_place")),
    )
    for label, value in pairs:
        if value:
            entry = f"{label}: {value}"
            if entry not in chips:
                chips.append(entry)
    for topic in seeded_topics or ():
        seed = (topic.get("seed") or "").strip()
        basis = (topic.get("basis") or "").strip()
        if seed and seed != CONFIRM_NEEDED and basis:
            entry = f"{basis}: {seed}"
            if entry not in chips:
                chips.append(entry)
    return chips


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
        "recognized": summarize_recognized_conditions(ex, seeded_topics),
        "extracted": ex,
    }



def _facade_attr(name: str, default):
    """Return an attribute from the ``optimizer_flow`` facade when patched by tests."""
    facade = sys.modules.get("optimizer_flow")
    if facade is not None and hasattr(facade, name):
        return getattr(facade, name)
    return default
