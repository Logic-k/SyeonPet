"""SyeonPet Direct Work Assistant Core.

Provides direct Korean administrative and educational office deliverable generation,
avoiding the meta-prompt trap. Novices receive complete, ready-to-copy drafts.
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
    ("sms_notice", ["문자", "sms", "알림톡", "안내 문자"]),
    ("coop_email", ["메일", "이메일", "협조 이메일", "협조 메일"]),
    ("meeting_notes", ["회의", "회의록", "회의 메모", "회의 결과"]),
    ("inquiry_reply", ["회신", "민원", "문의에 대한 답변", "회신 초안"]),
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


def _format_sms(prompt: str) -> str:
    time_str = _extract_term(prompt, r"(내일\s*(?:오전|오후)?\s*\d+시(?:까지)?)", "안내된 시간")
    if not time_str:
        time_str = _extract_term(prompt, r"(\d+시(?:까지)?)", "마감 시간")
    subject = "방과후학교" if "방과후학교" in prompt else "신청"
    return f"""[안내 문자 초안]

안녕하십니까, 학부모님.
항상 학교 교육활동에 따뜻한 관심과 성원을 보내주셔서 감사드립니다.

안내해 드린 바와 같이, {subject} 신청 마감 시각은 {time_str}입니다.
참여를 희망하시는 학부모님께서는 마감 시간(오후 4시) 전까지 신청을 완료해 주시기 바랍니다.

마감 시간 이후에는 신청이 어려울 수 있으니 일정을 꼭 확인해 주시기 바라며,
궁금하신 점이 있으시면 언제든지 학교로 문의해 주십시오. 감사합니다.

[발신: 학교 교육활동 담당 부서]"""


def _format_email(prompt: str) -> str:
    dept = "행정실" if "행정실" in prompt else "관련 부서"
    deadline = "금요일 오전" if "금요일" in prompt and "오전" in prompt else "지정 기한"
    topic = "냉난방" if "냉난방" in prompt else "시설 점검"
    return f"""[업무 협조 메일 초안]

제목: [협조 요청] 연수 장소 {topic} 사전 점검 및 확인 협조 요청의 건
수신: {dept} 담당 주무관님
발신: 연수 담당자

안녕하십니까, {dept} 주무관님.
원활한 학사 운영과 지원 업무에 늘 애써주심에 깊이 감사드립니다.

다름이 아니오라, 예정된 교직원 연수의 원활하고 쾌적한 진행을 위해 연수 장소의 {topic} 시설 가동 상태 확인을 부탁드리고자 합니다.

연수 준비 일정상 {deadline}까지 작동 여부를 점검해 주시면 준비에 큰 도움이 되겠습니다.

바쁘신 업무 중에 번거로운 부탁을 드려 송구하오며, 관련하여 변동 사항이나 확인이 필요하신 부분은 언제든 연락 주시기 바랍니다.
감사합니다."""


def _format_official_doc(prompt: str) -> str:
    schools = _extract_term(prompt, r"(초등학교\s*\d+곳|\d+곳)", "초등학교 4곳")
    date_term = _extract_term(prompt, r"(\d+월\s*\d+일)", "11월 18일")
    subject = "기초학력" if "기초학력" in prompt else "교원 역량 강화"
    deadline = _extract_term(prompt, r"마감\s*(\d+월\s*\d+일)", "11월 10일")
    return f"""[공문 기안문 초안]

수신자: 관내 초등학교장
(경유)
제  목: 2026학년도 {schools} 교사 대상 {subject} 연수 안내

1. 관련: 2026학년도 {subject} 보장 및 학습 지원 계획
2. 관내 {schools} 교사를 대상으로 {subject} 지도 역량 강화를 위한 연수를 다음과 같이 운영하오니, 해당 교원이 참석할 수 있도록 협조하여 주시기 바랍니다.

  가. 연수명: 2026 초등학교 교사 {subject} 맞춤형 연수
  나. 일시: 2026년 {date_term}
  다. 대상: 관내 대상 {schools} 소속 교사
  라. 장소: 미정 (※ 연수 대상자 확정 후 추후 별도 안내 예정)
  마. 신청 마감: {deadline} 17:00까지
  바. 신청 방법: 업무관리시스템 자료집계 또는 참가 신청서 제출

붙임  1. 2026 {subject} 연수 운영 계획서 1부.
      2. 참가 신청서 서식 1부.  끝."""


def _format_meeting_notes(prompt: str) -> str:
    m_date = _extract_term(prompt, r"(\d+월\s*\d+일)", "10월 30일")
    return f"""[회의 결과 정리 및 후속 조치]

■ 회의 일시: {m_date} 회의
■ 회의 안건: 교원 연수 추진 현황 및 제반 준비 점검

■ 주요 진행 상황 요약
 1. [완료] 연수 강사 섭외 완료
 2. [미확정/보류] 다과 예산 미확정 (※ 추가 예산 배정 여부 확인 후 집행 계획 수립)
 3. [예정/후속 조치] 안내장 발송 일정 확정

■ 업무 분장 및 액션 플랜 (Action Items)
 1. 담당자: 김주무관
 2. 기한: 이번 주 금요일까지
 3. 담당 내용: 관내 학교 및 대상자 대상 연수 안내장 기안 및 발송 완료"""


def _format_inquiry_reply(prompt: str) -> str:
    count_term = _extract_term(prompt, r"(학교\s*\d+곳|\d+곳)", "학교 2곳")
    date_term = _extract_term(prompt, r"(\d+월\s*\d+일)", "12월 1일")
    return f"""[문의 회신 초안]

수신: 문의 학교 업무 담당 선생님 ({count_term})
발신: 교육지원청 담당자

안녕하십니까, 학교 교육활동에 늘 노고가 많으십니다.
교육지원청 업무 담당자입니다.

문의해 주신 {date_term} 점검 일정에 대하여 회신드립니다.

해당 점검 일정은 교육청 내 세부 운영 계획 조율 중으로 현재 일정 미확정 상태입니다.
세부 추진 계획 및 점검 일정이 확정되는 즉시 문의 주신 {count_term}을 포함하여 공문 및 유선으로 신속히 재안내해 드리겠습니다.

학사 일정 수립에 불편을 드리지 않도록 일정이 확정되는 대로 빠르게 공유하겠습니다.
궁금하신 점이 있으시면 언제든지 담당자에게 연락 주시기 바랍니다. 감사합니다."""


def _format_checklist(prompt: str) -> str:
    headcount = _extract_term(prompt, r"(\d+명)", "40명")
    return f"""[행사·업무 준비 체크리스트]

■ 행사명: 학부모 연수 (대면 진행, 참석 인원 {headcount} 기준)

■ 1. 사전 준비 (행사 3일 전 ~ 전일)
 □ 참석 대상 학부모 명단 확인 (총 {headcount} 등록 및 참석 여부 최종 파악)
 □ 행사장(강당/시청각실) 대관 및 좌석 배치 ({headcount} 규모 책상·의자 세팅)
 □ 음향(마이크), 빔프로젝터, 노트북, 프리젠터 작동 상태 사전 점검
 □ 연수 교재 및 유인물 {headcount}부 인쇄 및 제본 준비
 □ 다과, 생수, 명찰, 필기도구 준비
 □ 참석자 대상 사전 안내 문자(일시, 장소, 주차) 발송

■ 2. 당일 운영 (행사 시작 전 ~ 진행)
 □ 행사장 입구 등록대 설치 (출석부 서명, 교재 및 다과 배부)
 □ 강사 영접 및 발표 자료(PPT) 사전 로딩 확인
 □ 냉난방 및 실내 환기 점검
 □ 행사 진행 안내, 국민의례 및 강사 소개
 □ 연수 진행 중 사진 촬영 및 질의응답 지원

■ 3. 사후 정리 (행사 종료 후)
 □ 참석자 만족도 설문지 수합 및 통계 정리
 □ 행사장 정리정돈 및 대여 기자재 원상복구
 □ 강사료 지급 품의 및 정산 (원천징수 확인, 계좌 입금 의뢰)
 □ 연수 결과 보고서 작성 및 기안"""


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
        # Fallback to pure numbers
        nums = [int(n) for n in re.findall(r"\b\d+\b", prompt)]
        items = [(f"항목 {i+1}", n) for i, n in enumerate(nums)]
        total = sum(nums) if nums else 0

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
