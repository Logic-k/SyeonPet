"""Completed-prompt section assembly, tuner axes, and HTML rendering."""
from __future__ import annotations

import html
import re

from .rules import (
    CONFIRM_NEEDED,
    _FORBIDDEN_LATER,
    _GENERIC,
    _TYPE_BY_ID,
    _covers,
    _seed_text,
    extract_conditions,
)

# Section-name basis each type maps question answers into ([섹션별 근거]).
_BASIS_ORDER = {
    "weekly_report": ["보고 대상", "이번 주 성과", "진행 중·이슈", "다음 주 계획", "도움 필요사항"],
    "general_report": ["보고 대상", "핵심 안건", "배경", "배경·현황", "현황", "리스크", "요청 사항"],
    "minutes": ["기본 정보", "안건", "강조 포인트"],
    "official_reply": ["원 공문 요구", "현황·답변 방향"],
    "notice_letter": ["발신·대상", "안내 내용"],
    "event_notice": ["행사 주제·목적", "일시·참여 방법"],
    "project_plan": ["사업 목적·대상", "추진 일정·활동", "예산·인력", "성과 지표"],
    "student_record": ["학년·영역", "관찰 사실 근거", "기재 유의점"],
    "audit_response": ["지적·점검 사안", "근거·사실 경위", "개선·재발방지"],
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
    "project_plan": ["추진 배경·목적", "사업 대상·운영 기간", "세부 추진 일정·내용", "예산·인력 운영 계획", "성과 점검·확인 사항"],
    "student_record": ["핵심 역량·활동 요약", "구체적 관찰 사례·성장", "지도·평가 유의사항 확인"],
    "audit_response": ["대상 사안 요지", "사실 경위·관련 규정 근거", "시정·보완 조치 완료 사항", "재발 방지 계획·증빙"],
    "lesson_activity": ["수업 개요 (학년·차시·목표)", "도입·동기 유발", "전개 (핵심 학습 활동·발문)", "정리·형성평가 및 준비물"],
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
    "project_plan": ["사업 목적·대상", "추진 기간", "톤·문체", "핵심 예산·인력", "성과 확인 기준"],
    "student_record": ["학년·교과 영역", "작성 목적", "관찰 사실 근거", "톤·문체", "기재 유의점"],
    "audit_response": ["수신·점검 기관", "지적·점검 사안", "근거·사실 경위", "개선·재발방지", "톤·문체"],
    "lesson_activity": ["학년·교과·주제", "수업 목표", "학습 활동 흐름", "톤·문체", "차시·준비물"],
    "generic": ["사용 맥락", "목적", "요청 내용", "톤·문체", "전달할 핵심 내용"],
}

# 업무·기록 문서 계열: ②-1 '나중에 보고' 금지 조항이 들어가는 유형.
_WORK_DOC_TYPES = {
    "weekly_report", "general_report", "minutes",
    "official_reply", "project_plan", "audit_response",
}
_DEFAULT_TONE = "격식 있는 공문체"

# Alternative output framings offered on the result screen for quick re-assembly.
_ALT_TYPE_SUGGESTIONS = {
    "weekly_report": ["general_report", "notice_letter"],
    "general_report": ["weekly_report", "project_plan"],
    "minutes": ["general_report", "notice_letter"],
    "official_reply": ["complaint_reply", "audit_response", "general_report"],
    "notice_letter": ["event_notice", "counsel_reply"],
    "event_notice": ["notice_letter", "project_plan"],
    "complaint_reply": ["official_reply", "counsel_reply"],
    "counsel_reply": ["notice_letter", "complaint_reply"],
    "project_plan": ["general_report", "event_notice"],
    "student_record": ["lesson_activity", "notice_letter"],
    "audit_response": ["official_reply", "general_report"],
    "lesson_activity": ["student_record", "notice_letter"],
    "generic": ["general_report", "notice_letter", "project_plan"],
}

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
    if spec["id"] == "student_record":
        rules.append(
            f"{_next_num()} 실명·개인 식별정보를 넣지 않고 비식별 표현만 사용하며, "
            "제공되지 않은 성장·수상·태도를 과장해 쓰지 않는다.")
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
    elif type_id == "project_plan":
        axes += [
            {"kind": "slider", "id": "length_scale", "title": "계획서 분량", "steps": _LENGTH_STEPS},
            {"kind": "chips", "id": "chips", "title": "강조 방향",
             "options": ["실행 일정 중심", "예산·근거 중심", "성과·기대효과 중심"]},
        ]
    elif type_id == "student_record":
        axes += [
            {"kind": "slider", "id": "length_scale", "title": "서술 분량", "steps": _LENGTH_STEPS},
            {"kind": "chips", "id": "chips", "title": "서술 관점",
             "options": ["핵심 역량 중심", "성장 과정 중심", "객관적 관찰 사실 위주"]},
        ]
    elif type_id == "audit_response":
        axes += [
            {"kind": "chips", "id": "chips", "title": "소명·대응 초점",
             "options": ["사실·근거 소명 중심", "재발방지·개선안 중심", "간결 요지 정리"]},
            {"kind": "slider", "id": "length_scale", "title": "답변 분량", "steps": _LENGTH_STEPS},
        ]
    elif type_id == "lesson_activity":
        axes += [
            {"kind": "slider", "id": "length_scale", "title": "활동안 상세 정도",
             "steps": ["핵심 흐름만", "간단 수업안", "표준 지도안", "상세 발문 포함", "활동지 수준"]},
            {"kind": "chips", "id": "chips", "title": "수업 구성 형태",
             "options": ["도입·활동·정리 단계형", "모둠·토론 활동형", "기초 학생 배려 힌트"]},
        ]
    else:
        axes.append({"kind": "chips", "id": "chips", "title": "작업 종류",
                     "options": ["글쓰기", "정리", "분석", "계획", "검토"]})
    return axes


def alternative_types(type_id: str) -> list[tuple[str, str]]:
    """Return suggested alternative ``(type_id, label)`` pairs for the result screen."""
    alt_ids = _ALT_TYPE_SUGGESTIONS.get(type_id, _ALT_TYPE_SUGGESTIONS["generic"])
    return [(tid, _TYPE_BY_ID[tid]["label"]) for tid in alt_ids if tid in _TYPE_BY_ID]


_SECTION_HEAD = re.compile(r"^\[(역할|맥락|섹션별 근거|과업|제약)\]\s*(.*)$")
_BRACKET_TOKEN = re.compile(r"\[([^\[\]\n]{1,30})\]")
_SECTION_NAMES = frozenset({"역할", "맥락", "섹션별 근거", "과업", "제약"})


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
        line = _BRACKET_TOKEN.sub(
            lambda m: (
                f'<b style="background:#fff3cd;color:#6b4e00;">{m.group(0)}</b>'
                if m.group(0) == CONFIRM_NEEDED
                else f'<b style="background:#e4eff8;color:#154c79;">{m.group(0)}</b>'
            ),
            line,
        )
        stripped = raw.lstrip()
        if stripped.startswith("- "):
            body.append(f'<div style="margin-left:14px;">• {line.lstrip()[2:]}</div>')
        else:
            body.append(f'<div>{line}</div>')
    return ('<div style="font-family:\'Malgun Gothic\',sans-serif;font-size:10pt;'
            'color:#172b3a;">' + "".join(body) + "</div>")


def extract_fillable_slots(text: str) -> list[dict]:
    """Identify unfilled ``[확인 필요]`` field lines and other ``[대괄호]`` tokens."""
    slots: list[dict] = []
    seen_tokens: set[str] = set()
    section = ""
    for idx, raw in enumerate(text.splitlines()):
        head = _SECTION_HEAD.match(raw)
        if head:
            section = head.group(1)
            continue
        stripped = raw.strip()
        # Only field lines ("- 라벨: ... [확인 필요]") are data slots; meta-instructions
        # inside [역할], [제약], or intro lines intentionally describe CONFIRM_NEEDED.
        if CONFIRM_NEEDED in raw and stripped.startswith("- ") and section in ("맥락", "섹션별 근거", ""):
            label = stripped[2:].split(":", 1)[0].strip() if ":" in stripped else "확인 필요 항목"
            slots.append({"kind": "line_need", "line_index": idx, "label": label or "확인 필요 항목", "token": CONFIRM_NEEDED})
        for match in _BRACKET_TOKEN.finditer(raw):
            inner = match.group(1).strip()
            token = match.group(0)
            if not inner or token == CONFIRM_NEEDED or inner in _SECTION_NAMES or token in seen_tokens:
                continue
            seen_tokens.add(token)
            slots.append({"kind": "token", "line_index": idx, "label": inner, "token": token})
    return slots


def apply_slot_fills(text: str, fills: list[tuple[dict, str]]) -> str:
    """Apply user-entered slot values onto line-scoped ``[확인 필요]`` or bracket tokens."""
    lines = text.splitlines()
    for slot, raw_value in fills:
        value = raw_value.strip()
        if not value:
            continue
        if slot.get("kind") == "line_need":
            idx = slot.get("line_index", -1)
            if 0 <= idx < len(lines) and CONFIRM_NEEDED in lines[idx]:
                lines[idx] = lines[idx].replace(CONFIRM_NEEDED, value, 1)
    updated = "\n".join(lines)
    if text.endswith("\n") and not updated.endswith("\n"):
        updated += "\n"
    for slot, raw_value in fills:
        value = raw_value.strip()
        if value and slot.get("kind") == "token":
            updated = updated.replace(slot["token"], value)
    return updated


def inspect_prompt_quality(original: str, context: str, prompt_text: str) -> dict:
    """Verify literal condition preservation in the assembled prompt without network calls."""
    if not original.strip() or not prompt_text.strip():
        return {"total": 0, "present": 0, "missing": []}
    try:
        from prompt_quality import condition_report
        findings = condition_report(original, context, prompt_text)
    except Exception:
        return {"total": 0, "present": 0, "missing": []}
    missing = [item for item in findings if item.get("status") == "missing"]
    return {
        "total": len(findings),
        "present": len(findings) - len(missing),
        "missing": missing,
    }

