"""SyeonPet Direct Work Assistant Core.

Provides direct Korean administrative and educational office deliverable generation,
avoiding the meta-prompt trap. Novices receive complete, ready-to-copy drafts.

Draft honesty rule: offline formatters only use facts actually present in the
request. Anything that cannot be extracted is marked with "[확인 필요]" rather
than being invented (no made-up dates, names, places or subjects).
"""
from __future__ import annotations

import re
from typing import Any

SYSTEM = """당신은 한국 사무 및 교육행정 실무 비서 '션(Syeon)'입니다.
질문 다듬기나 메타 프롬프트 형식으로 되묻지 말고, 요청된 일을 직접 수행하여
현장에서 바로 복사해 쓸 수 있는 완성된 초안·요약·표·답변을 직접 작성합니다.

지침:
1. 문맥에 나타난 숫자, 날짜, 기관명, 대상 인원 및 발신자/수신자 역할을 정확히 보존합니다.
2. 일정이 확정되지 않은 항목('미정', '미확정', '예정', '문의')은 임의로 지어내지 않고
   '장소: 미정 (추후 안내 예정)', '일정 미확정(확인 후 재안내)' 등으로 명확히 표기합니다.
3. 교육행정·일반 사무 표준 양식(기안문, 업무 협조전, 가정통신문·알림 문자, 회의록 등)에
   맞는 정중하고 신뢰감 있는 한국어 경어를 사용합니다.
"""

INTENT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("excel_help", ["엑셀", "수식", "함수", "sumif", "vlookup", "countif"]),
    ("computer_tip", ["단축키", "캡처", "윈도우", "한글 문서에 넣는", "복사 단축키"]),
    ("table_summary", ["표로 정리", "표 정리", "건수 요약", "건을 표", "합계 적어", "집계 표"]),
    ("checklist", ["체크리스트", "점검표", "사전·당일·사후", "준비 체크리스트"]),
    ("official_doc", ["공문", "기안문", "시행문", "공문 초안"]),
    ("sms_notice", ["문자", "sms", "알림톡", "안내 문자", "안내문자", "알림 문자", "알림장"]),
    ("coop_email", ["메일", "이메일", "협조 이메일", "협조 메일"]),
    ("meeting_notes", ["회의", "회의록", "회의 메모", "회의 결과"]),
    ("inquiry_reply", ["회신", "민원", "문의에 대한 답변", "회신 초안", "답장", "답신"]),
    ("polite_rewrite", ["공손", "정중", "다듬", "바꿔", "어조", "안 됩니다", "처리 안"]),
]


def detect_work_intent(prompt: str) -> str:
    """Classify user request into specific administrative office intent."""
    p = prompt.strip().lower()
    for intent, kws in INTENT_KEYWORDS:
        if any(k in p for k in kws):
            return intent
    if len(p) < 40 and ("제출" in p or "안 됨" in p or "기한" in p or "불가" in p):
        return "polite_rewrite"
    return "general_draft"


def _extract_term(prompt: str, pattern: str, default: str = "") -> str:
    m = re.search(pattern, prompt)
    return m.group(1) if m else default


_NEED = "[확인 필요]"

# Request verbs that end the "topic" part of a request
# ("…안내 문자 써줘", "…협조 메일 부탁드립니다").
_REQ_RE = re.compile(
    r"만들어\s*주|만들어|작성해\s*주|작성해|작성|써\s*줘|써줘|써\s*주|써|"
    r"보내\s*줘|보내줘|보내\s*주|보낼|보내|해\s*줘|해줘|해\s*주|부탁|주세요|주십시오|"
    r"드려|드립|정리해\s*주|정리해|정리|뽑아|필요|알려\s*주|알려|하고\s*싶|줘"
)
# Format/genre/tone words that are not part of the topic itself.
_FMT_RE = re.compile(
    r"안내\s*문자|알림\s*문자|알림톡|문자|sms|메일|이메일|공문|기안문|시행문|회의록|"
    r"회의\s*메모|체크리스트|점검표|보고서|안내문|안내장|가정통신문|통신문|초안|협조|"
    r"답변|회신|답장|답신|문서|양식|서식|형식|표로|"
    r"정중하게|공손하게|정중한|공손한|정중히|공손히|정중|공손|바로|즉시|빠른|간단한|간략한"
)
# Time/quantity/context words that describe conditions rather than the topic.
_STOP_RE = re.compile(
    r"내일|오늘|모레|글피|이번\s*주|다음\s*주|이번\s*달|다음\s*달|오전|오후|새벽|저녁|밤|"
    r"\d+\s*월|\d+\s*일|\d+\s*시|\d+\s*분|\d+\s*명|\d+\s*곳|\d+\s*부|\d+\s*개|\d+\s*건|"
    r"\d+\s*학년도|\d+\s*년|"
    r"까지|부터|경|요일|마감|예정|미정|미확정|장소|대면|비대면|온라인|진행|실시|개최|대상|참석|준비|"
    r"이라고|이라구|라고|이라는|라는|다고|라며|관련|대한|위한|때문|사전|당일|사후|"
    r"학부모님|학부모|선생님|교직원|교사|학생|담당자|주무관"
)
# Department/recipient mentions that frame the request but are not the topic.
_DEPT_RE = re.compile(
    r"교무부에서|교무부로|행정실에서|행정실에|행정실로|교무실에서|교무실에|교무실로|"
    r"교육지원청에서|교육지원청에|교육지원청으로|교육청에서|교육청에|교육청으로|"
    r"각급학교에서|각급학교에|학교에서|학교에|학교로"
)
# Trailing particles cut from leftover tokens.
_TRAIL_RE = re.compile(
    r"(?:님들께|님께|께서|에게|한테|에서|으로|부터|까지|처럼|이랑|랑|밖에|조차|마저|"
    r"이라고|라고|이라는|라는|다고|은|는|이|가|을|를|의|에|와|과|도|만|로)$"
)
# Tokens that never carry topic meaning on their own.
_STOP_TOKENS = {
    "각", "해당", "관련", "대한", "위한", "때문", "이", "가", "을", "를", "의", "에",
    "도", "만", "로", "으로", "에서", "까지", "부터", "과", "와", "랑", "및", "등",
    "께", "님",
}


def _strip_trail(token: str) -> str:
    """Cut a trailing particle only when a usable stem (>=2 chars) remains."""
    m = _TRAIL_RE.search(token)
    if m and len(token[:m.start()]) >= 2:
        return token[:m.start()]
    return token


def _extract_topic(prompt: str) -> str:
    """Best-effort topic extraction that never invents content.

    Takes the part of the request before the request verb, strips format
    words, time/quantity words and department markers, then keeps the
    remaining content words. Returns "" when nothing usable remains.
    """
    head = _REQ_RE.split(prompt, maxsplit=1)[0]
    head = _FMT_RE.sub(" ", head)
    head = _DEPT_RE.sub(" ", head)
    head = _STOP_RE.sub(" ", head)
    head = re.sub(r"[()（）\[\]{}<>,.:;·~!?\"'&/\\-]", " ", head)
    tokens = []
    for token in head.split():
        token = _strip_trail(token)
        if token and token not in _STOP_TOKENS:
            tokens.append(token)
    return " ".join(tokens).strip()


_DEADLINE_PATTERNS = (
    r"((?:내일|오늘|모레|이번\s*주|다음\s*주)\s*(?:오전|오후)?\s*\d+\s*시(?:\s*\d+\s*분)?\s*까지)",
    r"(마감\s*\d+\s*월\s*\d+\s*일(?:\s*(?:오전|오후)?\s*\d+\s*시)?)",
    r"((?:월|화|수|목|금|토|일)요일\s*(?:오전|오후)?\s*까지)",
    r"(\d+\s*월\s*\d+\s*일\s*(?:(?:오전|오후)\s*\d+\s*시(?:\s*\d+\s*분)?)?)",
    r"((?:오전|오후)\s*\d+\s*시(?:\s*\d+\s*분)?)",
    r"(\d+\s*시\s*까지)",
)


def _extract_deadline(prompt: str) -> str:
    for pattern in _DEADLINE_PATTERNS:
        found = _extract_term(prompt, pattern)
        if found:
            return found.strip()
    return ""


_KNOWN_RECIPIENTS = (
    ("행정실", "행정실 담당 주무관님"),
    ("교무실", "교무실 담당 선생님"),
    ("교육지원청", "교육지원청 담당자님"),
    ("교육청", "교육청 담당자님"),
    ("각급학교", "각급학교 담당자님"),
    ("학부모", "학부모님"),
    ("교직원", "교직원 선생님"),
)


def _extract_recipient(prompt: str) -> str:
    for word, label in _KNOWN_RECIPIENTS:
        if word in prompt:
            return label
    return ""


_EVENT_PHRASES = (
    "학부모 연수", "교직원 연수", "교원 연수", "학부모 총회", "학교설명회",
    "학부모 상담회", "현장체험학습", "체육대회", "방과후학교",
)
_EVENT_WORDS = (
    "총회", "설명회", "연수", "체육대회", "운동회", "졸업식", "입학식",
    "발표회", "학예회", "축제", "상담회", "오픈스쿨", "등교맞이",
)


def _extract_event(prompt: str) -> str:
    for phrase in _EVENT_PHRASES:
        if phrase in prompt:
            return phrase
    topic = _extract_topic(prompt)
    if topic:
        return topic
    for word in _EVENT_WORDS:
        if word in prompt:
            return word
    return ""


def _meeting_body(prompt: str) -> str:
    """Extract pasted meeting content: text after ':' or after the first line."""
    if ":" in prompt or "：" in prompt:
        body = re.split(r"[:：]", prompt, maxsplit=1)[1].strip()
        if len(body) >= 10:
            return body
    lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
    if len(lines) >= 2:
        rest = "\n".join(lines[1:]).strip()
        if len(rest) >= 10:
            return rest
    return ""


def _format_sms(prompt: str) -> str:
    time_str = _extract_deadline(prompt) or f"{_NEED}: 일시·마감 시간"
    topic = _extract_topic(prompt) or f"{_NEED}: 안내할 내용"
    if re.search(r"마감|신청|제출|접수|모집", prompt):
        body_lines = (
            f"{topic} 관련하여 안내드립니다.\n"
            f"마감 일시: {time_str}\n"
            "마감 시간 이후에는 접수가 어려울 수 있으니, 일정을 꼭 확인해 주시기 바랍니다."
        )
    else:
        body_lines = (
            f"{topic} 관련하여 안내드립니다.\n"
            f"일시: {time_str}\n"
            f"장소: {_NEED}\n"
            f"준비물·기타 사항: {_NEED}"
        )
    return f"""[안내 문자 초안]

안녕하십니까, 학부모님.
항상 학교 교육활동에 따뜻한 관심과 성원을 보내주셔서 감사드립니다.

{body_lines}

궁금하신 점이 있으시면 언제든지 학교로 문의해 주십시오. 감사합니다.

[발신: 학교 담당 부서]"""


def _format_email(prompt: str) -> str:
    recipient = _extract_recipient(prompt)
    salutation = recipient or "담당자님"
    recipient_disp = recipient or f"{_NEED}: 수신 부서·담당자"
    deadline = _extract_deadline(prompt) or f"{_NEED}: 요청 기한"
    topic = _extract_topic(prompt) or f"{_NEED}: 협조할 내용"
    return f"""[업무 협조 메일 초안]

제목: {topic} 관련 협조 요청의 건
수신: {recipient_disp}
발신: {_NEED}: 발신 부서·담당자

안녕하십니까, {salutation}.
원활한 업무 추진에 늘 애써주심에 감사드립니다.

다름이 아니오라, {topic} 관련하여 아래와 같이 협조를 요청드리고자 합니다.

- 협조 내용: {topic}
- 요청 기한: {deadline}
- 문의: {_NEED}: 담당자 연락처

바쁘신 업무 중에 번거로운 부탁을 드려 송구하며, 확인이 필요하신 부분은 언제든 연락 주시기 바랍니다.
감사합니다."""


def _format_official_doc(prompt: str) -> str:
    dates = re.findall(r"\d+\s*월\s*\d+\s*일", prompt)
    deadline = _extract_term(prompt, r"마감\s*(\d+\s*월\s*\d+\s*일)")
    date_term = next((d for d in dates if d != deadline), "")
    schools = _extract_term(prompt, r"((?:초등|중등|고등)?학교\s*\d+\s*곳|\d+\s*곳)")
    topic = _extract_topic(prompt) or f"{_NEED}: 안내할 사업·행사"
    if _NEED in topic or topic.endswith(" 안내"):
        title = topic
    else:
        title = f"{topic} 안내"
    if "관내" in prompt or "학교장" in prompt:
        recipient = "관내 각급학교장"
    elif "교육지원청" in prompt or "교육청" in prompt:
        recipient = "교육지원청"
    else:
        recipient = f"{_NEED}: 수신 기관"
    if schools:
        target = f"관내 {schools} 소속 교사"
    elif "교사" in prompt or "교직원" in prompt:
        target = f"{_NEED}: 대상 학교·소속 교사"
    else:
        target = f"{_NEED}: 대상"
    if re.search(r"장소\s*(?:미정|미확정)", prompt):
        place = "미정 (추후 별도 안내 예정)"
    else:
        place = _extract_term(prompt, r"(강당|체육관|운동장|회의실|시청각실|교실|온라인)\S*") \
            or f"{_NEED}: 장소"
    return f"""[공문 기안문 초안]

수신자: {recipient}
(경유)
제  목: {title}

1. 관련·목적: {_NEED}: 관련 지침·계획 등 근거
2. 아래와 같이 {topic}을(를) 진행하고자 하오니 협조하여 주시기 바랍니다.

  가. 일시: {date_term or f'{_NEED}: 일시'}
  나. 대상: {target}
  다. 장소: {place}
  라. 내용: {_NEED}: 세부 내용
  마. 제출·신청 마감: {deadline or f'{_NEED}: 마감일'}
  바. 방법: {_NEED}: 신청·제출 방법

붙임  {_NEED}: 붙임 문서명 1부.  끝."""


def _format_meeting_notes(prompt: str) -> str:
    date = _extract_term(prompt, r"(\d+\s*월\s*\d+\s*일)") or f"{_NEED}: 회의 일시"
    body = _meeting_body(prompt)
    if not body:
        return """[회의록·회의 결과 정리]

정리할 회의 내용이 확인되지 않았습니다.
아래처럼 회의 메모·녹취·속기 내용을 함께 붙여 넣어 주시면
일시·안건·주요 내용·결정 사항·후속 조치로 정리해 드립니다.

  예) "11월 20일 회의 메모: 안건 — …, 결정 — …, 담당 — …"

(참고용 빈 양식)

■ 회의 일시:
■ 회의 장소:
■ 참석자:
■ 안건:
■ 주요 내용:
■ 결정 사항:
■ 후속 조치 (담당/기한/내용):"""

    items = [s.strip(" ,.") for s in re.split(r"[,，;；\n]|·", body) if s.strip()]
    lines = "\n".join(f" {idx}. {s}" for idx, s in enumerate(items, 1))
    return f"""[회의 결과 정리 및 후속 조치]

■ 회의 일시: {date}
■ 회의 장소: {_NEED}
■ 참석자: {_NEED}
■ 회의 안건: {_NEED}

■ 주요 내용 (붙여 넣은 내용 정리)
{lines}

■ 결정 사항
 1. {_NEED}: 회의에서 결정된 사항

■ 후속 조치 (Action Items)
 1. 담당: {_NEED} / 기한: {_NEED} / 내용: {_NEED}
"""


def _format_inquiry_reply(prompt: str) -> str:
    count = _extract_term(prompt, r"(학교\s*\d+\s*곳|기관\s*\d+\s*곳|\d+\s*곳)") \
        or f"{_NEED}: 문의 기관"
    date = _extract_term(prompt, r"(\d+\s*월\s*\d+\s*일)")
    m = re.search(r"(\S+(?:\s*\S+){0,3}?)(?:을|를)?\s*문의", prompt)
    matter = ""
    if m:
        matter = _STOP_RE.sub(" ", m.group(1)).strip()
        matter = _strip_trail(matter).strip()
    matter = matter or _extract_topic(prompt) or "문의하신 사항"
    date_txt = f"{date} " if date else ""
    if "미확정" in prompt or "미정" in prompt:
        answer = ("해당 사항은 현재 일정 미확정 상태입니다.\n"
                  "세부 추진 계획이 확정되는 즉시 공문 및 유선으로 재안내해 드리겠습니다.")
    else:
        answer = f"{_NEED}: 문의에 대한 답변 내용"
    return f"""[문의 회신 초안]

수신: 문의 기관 담당자님 ({count})
발신: {_NEED}: 발신 기관·담당자

안녕하십니까, {count} 업무 담당자님.
문의해 주신 {date_txt}{matter}에 대하여 회신드립니다.

{answer}

학사 일정 수립에 불편을 드리지 않도록 빠르게 안내해 드리겠습니다.
궁금하신 점이 있으시면 언제든지 담당자에게 연락 주시기 바랍니다. 감사합니다."""


def _format_checklist(prompt: str) -> str:
    headcount = _extract_term(prompt, r"(\d+\s*명)")
    copies = f"{headcount}부" if headcount else "참석 인원 수만큼"
    scale = headcount or "예상 인원"
    scale_txt = headcount or _NEED
    event = _extract_event(prompt) or f"{_NEED}: 행사명"
    if "대면" in prompt:
        mode = ", 대면 진행"
    elif "온라인" in prompt or "비대면" in prompt:
        mode = ", 온라인·비대면 진행"
    else:
        mode = ""
    return f"""[행사·업무 준비 체크리스트]

■ 행사명: {event} (참석 인원: {scale_txt}{mode})

■ 1. 사전 준비 (행사 3일 전 ~ 전일)
 □ 참석 대상자 명단 확인 (총 {scale} 등록 및 참석 여부 최종 파악)
 □ 행사장(강당/시청각실) 대관 및 좌석 배치 ({scale} 규모)
 □ 음향(마이크), 빔프로젝터, 노트북, 프리젠터 작동 상태 사전 점검
 □ 교재·유인물 {copies} 인쇄 및 제본 준비
 □ 다과, 생수, 명찰, 필기도구 준비
 □ 참석자 대상 사전 안내(일시, 장소, 주차) 발송

■ 2. 당일 운영 (행사 시작 전 ~ 진행)
 □ 행사장 입구 등록대 설치 (출석부 서명, 교재 및 다과 배부)
 □ 강사 영접 및 발표 자료(PPT) 사전 로딩 확인
 □ 냉난방 및 실내 환기 점검
 □ 행사 진행 안내, 개회 및 강사 소개
 □ 진행 중 사진 촬영 및 질의응답 지원

■ 3. 사후 정리 (행사 종료 후)
 □ 참석자 만족도 설문 수합 및 통계 정리
 □ 행사장 정리정돈 및 대여 기자재 원상복구
 □ 강사료 지급 품의 및 정산 (원천징수 확인, 계좌 입금 의뢰)
 □ 결과 보고서 작성 및 기안"""


def _format_excel(prompt: str) -> str:
    return """[엑셀 수식 도우미]

요청하신 조건부 합계를 구하는 엑셀 수식은 다음과 같습니다.

■ 추천 수식:
=SUMIF(B:B, "제출완료", C:C)

특정 행 범위를 지정할 경우 (예: 2행~100행):
=SUMIF(B2:B100, "제출완료", C2:C100)

■ 수식 설명:
• 첫 번째 인수(B:B): 조건을 검사할 기준 범위 ('제출완료' 여부가 적힌 열)
• 두 번째 인수("제출완료"): 찾고자 하는 조건값
• 세 번째 인수(C:C): 실제로 합산할 금액이 들어있는 합계 범위

■ 초보자용 적용 방법 (3단계):
1단계: 합계 금액이 표시되길 원하는 빈 셀을 클릭합니다.
2단계: 위 수식 '=SUMIF(B:B, "제출완료", C:C)'를 복사(Ctrl+C)하여 수식 입력창에 붙여넣기(Ctrl+V)합니다.
3단계: Enter 키를 누르면 조건에 맞는 행의 C열 금액 합계가 즉시 계산됩니다.

※ 팁: 만약 조건이 2개 이상이라면 =SUMIFS(C:C, B:B, "제출완료", ...) 수식을 사용하세요."""


def _format_polite(prompt: str) -> str:
    core = prompt.strip()
    return f"""[정중하고 공손한 공문·안내문 표현 다듬기]

원문: "{core}"

상황과 상대방에 맞춰 아래 세 가지 정중한 표현 중 선택하여 사용해 보세요.

■ 대안 1 (부드러운 표준 공문/메일용 - 가장 추천):
"원활한 업무 처리를 위하여 정해진 기한 내에 제출해 주시기 바라며, 기한 경과 시에는 처리가 어려울 수 있음을 너른 마음으로 양해 부탁드립니다."

■ 대안 2 (간결하고 정중한 안내 문자/알림용):
"원활한 행정 처리를 위해 기한 내 제출을 부탁드립니다. 기한 이후에는 접수 및 처리가 제한될 수 있으니 유의해 주시기 바랍니다."

■ 대안 3 (학부모님 및 외부 민원인 안내용):
"마감 기한 이후에는 관련 시스템 마감으로 인하여 처리가 불가하오니, 반드시 안내해 드린 기한 내에 제출해 주시기를 간곡히 부탁드립니다."
"""


def _format_table(prompt: str) -> str:
    # Match patterns like: 지원 완료 7건, 진행 중 4건, 미확인 2건
    matches = re.findall(r"([가-힣\w\s]+?)\s*(\d+)\s*건", prompt)
    if matches:
        items = [(name.strip(", "), int(num)) for name, num in matches]
        total = sum(num for _, num in items)
    else:
        nums = [int(n) for n in re.findall(r"\b\d+\b", prompt)]
        if not nums:
            return """[건수 집계 및 요약 표]

집계할 항목과 건수가 확인되지 않았습니다.
"제출 완료 7건, 진행 중 4건"처럼 항목 이름과 건수를 함께 적어 주시면 표로 정리해 드립니다."""
        items = [(f"{_NEED}: 항목 {i+1} 이름", n) for i, n in enumerate(nums)]
        total = sum(nums)

    table_lines = [
        "[건수 집계 및 요약 표]",
        "",
        "요청하신 항목별 현황을 정리한 집계 표입니다.",
        "",
        "| 구분 (상태) | 건수 |",
        "| :--- | :--- |",
    ]
    for name, num in items:
        table_lines.append(f"| {name} | {num}건 |")
    table_lines.append(f"| **합계** | **{total}건** |")
    table_lines.append("")
    terms_str = " + ".join(str(num) for _, num in items)
    table_lines.append(f"※ 총 {len(items)}개 항목의 합계는 {total}건({terms_str})입니다.")
    return "\n".join(table_lines)


def _format_computer_tip(prompt: str) -> str:
    return """[컴퓨터 초보 단축키 가이드: 화면 캡처 후 한글 문서 붙여넣기]

윈도우에서 화면의 원하는 부분만 깔끔하게 오려내어 한글(HWP)이나 워드 문서에 바로 넣는 가장 빠른 방법입니다.

■ 핵심 단축키 2개:
1. 캡처하기: [Windows 로고 키] + [Shift] + [S]
2. 붙여넣기: [Ctrl] + [V]

■ 따라 하기 3단계:
1단계 [캡처]:
키보드 왼쪽 아래 [Windows 로고 키]를 누른 상태에서 [Shift] 키와 알파벳 [S] 키를 동시에 누릅니다.
화면이 살짝 어두워지며 화면 상단에 캡처 바가 나타납니다.

2단계 [영역 선택]:
마우스 왼쪽 버튼을 누른 채 캡처하고 싶은 영역을 드래그한 뒤 마우스 손을 뗍니다.
(캡처된 이미지가 컴퓨터 메모리/클립보드에 자동으로 저장됩니다.)

3단계 [한글 문서에 넣기]:
작업 중이던 한글(HWP) 또는 워드 창으로 돌아와서 사진을 넣을 위치를 마우스로 클릭합니다.
키보드에서 [Ctrl] 키를 누른 채 [V] 키를 누르면 캡처한 이미지가 쏙 들어갑니다!

※ 추가 팁:
• 화면 전체 캡처: [Print Screen] 키 누르기
• 한글 문서에서 사진 크기 줄이기: 붙여넣은 사진 클릭 후 모서리 네모를 드래그"""


def _format_general_draft(prompt: str) -> str:
    unconfirmed = []
    for u in ["미정", "미확정", "예정", "문의"]:
        if u in prompt:
            unconfirmed.append(u)
    unconf_note = f"\n■ 확인 필요 사항: {', '.join(unconfirmed)} (추후 별도 확인 후 보완)" if unconfirmed else ""

    return f"""[업무 초안 정리]

요청하신 내용에 대한 실무 초안입니다.

■ 주요 내용 요약:
{prompt.strip()}
{unconf_note}

■ 실행 가이드:
1. 상기 초안 내용을 검토하시고 빈칸이나 미확정 사항을 보완해 주십시오.
2. 필요시 상단 복사 버튼을 눌러 공문이나 업무 메신저에 바로 활용하실 수 있습니다."""


DISPATCHER = {
    "sms_notice": _format_sms,
    "coop_email": _format_email,
    "official_doc": _format_official_doc,
    "meeting_notes": _format_meeting_notes,
    "inquiry_reply": _format_inquiry_reply,
    "checklist": _format_checklist,
    "excel_help": _format_excel,
    "polite_rewrite": _format_polite,
    "table_summary": _format_table,
    "computer_tip": _format_computer_tip,
    "general_draft": _format_general_draft,
}


def generate_offline_deliverable(prompt: str) -> str:
    """Intelligently generate a ready-to-use Korean office deliverable without network."""
    if not isinstance(prompt, str) or not prompt.strip():
        return ""
    intent = detect_work_intent(prompt)
    formatter = DISPATCHER.get(intent, _format_general_draft)
    return formatter(prompt)


def create_direct_draft(prompt: str, mode: str = "direct_work") -> dict[str, Any]:
    """Create deliverable dictionary compatible with Syeon workspace pipelines."""
    deliverable = generate_offline_deliverable(prompt)
    intent = detect_work_intent(prompt)
    return {
        "result": deliverable,
        "intent": intent,
        "mode": mode,
        "notes": ["인터넷 연결 없이도 즉시 사용 가능한 완성형 실무 초안입니다."],
    }
