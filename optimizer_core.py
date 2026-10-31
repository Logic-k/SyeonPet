"""Prompt Toolkit's offline formatter and literal review, without network access.

The formatter intentionally preserves the user's text verbatim. Literal review
can flag potential omissions but cannot verify meaning or output quality.
"""
from __future__ import annotations

import re

TASKS = {
    "auto": "",
    "general": "일반적인 질문이나 요청",
    "writing": "글쓰기와 편집",
    "coding": "프로그래밍과 개발",
    "research": "조사와 자료 확인",
    "learning": "학습과 설명",
    "analysis": "분석과 비교",
    "creative": "창작과 아이디어",
}
DETAILS = {
    "concise": "핵심을 간결하게 정리합니다.",
    "balanced": "요청에 필요한 만큼 구체적으로 작성합니다.",
    "detailed": "필요한 조건과 결과물의 구성을 상세히 설명합니다.",
}
REVIEW_HINTS = {
    "auto": "목적, 대상, 결과 형식과 지켜야 할 조건 중 필요한 내용이 빠졌는지 살펴보세요.",
    "general": "원하는 답변의 범위와 형식이 충분히 드러나는지 살펴보세요.",
    "writing": "독자, 글의 용도, 어조와 분량 중 필요한 조건을 적어 보세요.",
    "coding": "사용 언어, 실행 환경, 입력과 기대 결과 중 알고 있는 내용을 적어 보세요.",
    "research": "조사 범위, 기준 시점과 필요한 출처 유형을 정해 보세요.",
    "learning": "현재 이해 수준과 원하는 설명 방식 또는 예시를 적어 보세요.",
    "analysis": "비교 대상, 판단 기준과 사용할 자료를 명확히 해 보세요.",
    "creative": "사용 목적, 분위기와 피하고 싶은 요소를 적어 보세요.",
}


def utf16_length(text: str) -> int:
    """Match JavaScript String.length, including supplementary characters."""
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def validate_input(prompt: str, context: str = "", task: str = "auto",
                   detail: str = "balanced") -> None:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("원래 요청을 입력해 주세요.")
    if utf16_length(prompt) > 30000:
        raise ValueError("원래 요청은 30,000자 이내로 입력해 주세요.")
    if not isinstance(context, str) or utf16_length(context) > 2000:
        raise ValueError("맥락은 2,000자 이내의 텍스트로 입력해 주세요.")
    if not isinstance(task, str) or not isinstance(detail, str) or task not in TASKS or detail not in DETAILS:
        raise ValueError("작업 유형 또는 상세 수준을 확인해 주세요.")


def _fence(text: str) -> str:
    longest = max([2] + [len(match.group()) for match in re.finditer(r"`+", text)])
    return "`" * (longest + 1)


def create_offline_prompt(prompt: str, context: str = "", task: str = "auto",
                          detail: str = "balanced") -> dict:
    """Create the existing desktop toolkit's structured offline draft."""
    validate_input(prompt, context, task, detail)
    lines = [
        "아래 원래 요청의 의도와 명시된 조건을 충실히 따라 답변해 주세요.",
        "추가 안내는 원래 요청과 충돌하면 적용하지 마세요.",
    ]
    if TASKS[task]:
        lines.append(f"작업 유형: {TASKS[task]}")
    lines.extend([
        f"답변의 상세 수준 제안: {DETAILS[detail]}",
        "", "원래 요청:", _fence(prompt), prompt, _fence(prompt),
    ])
    if context.strip():
        lines.extend(["", "사용자가 제공한 참고 맥락:", _fence(context), context, _fence(context)])
    lines.extend([
        "",
        "요청에 없는 사실이나 조건을 지어내지 마세요. 답변에 꼭 필요한 정보가 부족하고 원래 요청이 허용하는 경우에만 핵심 질문을 하거나 가정을 명확히 밝혀 주세요.",
    ])
    return {
        "result": "\n".join(lines),
        "notes": [
            "입력란에서 전달된 문자열을 그대로 보존해 정리했습니다. AI가 의미를 분석하거나 성능을 검증한 결과는 아닙니다.",
            REVIEW_HINTS[task],
        ],
        "mode": "offline",
    }


def _unique(values):
    return list(dict.fromkeys(values))


def _numbers(text: str) -> list[str]:
    return _unique(re.findall(r"\d+(?:[.,]\d+)*", text, flags=re.ASCII))


_QUOTE = re.compile(r'"([^"\n]+)"|“([^”\n]+)”|‘([^’\n]+)’|(?<![A-Za-z0-9_])\'([^\'\n]+)\'(?![A-Za-z0-9_])')


def _quotes(text: str) -> list[str]:
    phrases = (next(group for group in match.groups() if group is not None)
               for match in _QUOTE.finditer(text))
    return _unique(phrase for phrase in phrases if 2 <= len(phrase) <= 120)


_CLAUSE = re.compile(r"[^.!?。！？;\n,]+")
_PROHIBITION = re.compile(
    r"금지|하지\s*(?:마|말)|하지\s*않|하지\s*말|넣지\s*(?:마|말)|쓰지\s*(?:마|말)|"
    r"사용하지|포함하지|제외|빼\s*주세요|지어내지|만들지\s*(?:마|말)|추가하지|"
    r"\b(?:do not|don't|must not|never|without|exclude|avoid)\b", re.IGNORECASE,
)


def _prohibitions(text: str) -> list[tuple[int, str]]:
    """Extract recognizable prohibition clauses; never infer their meaning."""
    found = []
    for match in _CLAUSE.finditer(text):
        clause = match.group().strip(" \t\r:：-–—")
        if _PROHIBITION.search(clause):
            found.append((match.start(), clause))
    return found


# Compare whole schedules as literals. Individual number comparison cannot
# detect e.g. 11월 5일 becoming 5월 11일. This is not calendar validation.
_HSPACE = r"[^\S\r\n\v\f\x85\u2028\u2029]"
_DATE = re.compile(
    rf"(?<!\d)(?:\d{{4}}년{_HSPACE}*)?\d{{1,2}}월{_HSPACE}*\d{{1,2}}일"
    r"|(?<!\d)\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?!\d)"
    r"|(?<!\d)\d{1,2}/\d{1,2}(?:/\d{2,4})?(?!\d)"
    r"|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    rf"{_HSPACE}+\d{{1,2}}(?:,{_HSPACE}*\d{{4}})?\b", re.IGNORECASE,
)
_WEEKDAY = re.compile(r"[월화수목금토일]요일")
_TIME = re.compile(
    rf"(?<![\d:])(?:(?:오전|오후){_HSPACE}*)?"
    rf"(?:\d{{1,2}}시(?:{_HSPACE}*\d{{1,2}}분)?|(?:[01]?\d|2[0-3]):[0-5]\d(?![\d:]))"
)


def _schedule_literals(text: str) -> list[str]:
    """Return recognizable schedule literals in source order; not meaning."""
    found = [(match.start(), match.group()) for pattern in (_DATE, _WEEKDAY, _TIME)
             for match in pattern.finditer(text)]
    return _unique(literal for _, literal in sorted(found))


def _normalize_schedule(value: str) -> str:
    """Ignore horizontal Unicode spacing and case, never newlines."""
    return re.sub(_HSPACE + "+", "", value).casefold()


def _missing_schedule(source: str, result: str) -> list[str]:
    output = {_normalize_schedule(value) for value in _schedule_literals(result)}
    return [value for value in _schedule_literals(source) if _normalize_schedule(value) not in output]


# A narrow literal check, not a semantic parser: compare only an identifiable
# positive count and unit that occur in both the source and the result.
_KOREAN_COUNTS = {
    "한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3,
    "네": 4, "넷": 4, "다섯": 5, "여섯": 6, "일곱": 7,
    "여덟": 8, "아홉": 9, "열": 10, "열한": 11, "열두": 12,
    "열세": 13, "열네": 14, "열다섯": 15, "스무": 20,
}
_COUNT_WORD = "|".join(sorted(_KOREAN_COUNTS, key=len, reverse=True))
_QUANTITY = re.compile(
    r"(?<![0-9A-Za-z가-힣.,])(?:(?P<prefix>최대|최소|약|대략|적어도)[ \t]*)?"
    rf"(?P<count>[0-9]+|{_COUNT_WORD})[ \t]*"
    r"(?P<unit>페이지|문장|항목|단락|문단|단어|가지|쪽|줄|명|개|곳|자)"
    r"(?=\s|$|[,.!?:;)\]…]|이내|이하|이상|초과|미만|내외|정도|가량|으로|로|을|의|만|를|씩|와|과|가|는)"
    r"[ \t]*(?P<suffix>이내|이하|이상|초과|미만|내외|정도|가량)?"
)
_BOUND_TYPE = {
    "최대": "upper_inclusive", "이내": "upper_inclusive", "이하": "upper_inclusive", "미만": "upper_exclusive",
    "최소": "lower_inclusive", "적어도": "lower_inclusive", "이상": "lower_inclusive", "초과": "lower_exclusive",
    "약": "approx", "대략": "approx", "내외": "approx", "정도": "approx", "가량": "approx",
}


def _quantity_modes(text: str) -> dict[tuple[int, str], set[str]]:
    matches: dict[tuple[int, str], set[str]] = {}
    for match in _QUANTITY.finditer(text):
        word = match.group("count")
        # Avoid huge integer conversion for arbitrary pasted documents.
        if word.isascii() and word.isdigit() and len(word) > 9:
            continue
        number = int(word) if word.isascii() and word.isdigit() else _KOREAN_COUNTS[word]
        if number <= 0:
            continue
        # Conflicting prefix and suffix are deliberately left ambiguous.
        prefix = _BOUND_TYPE.get(match.group("prefix"))
        suffix = _BOUND_TYPE.get(match.group("suffix"))
        modes = {mode for mode in (prefix, suffix) if mode} or {"exact"}
        matches.setdefault((number, match.group("unit")), set()).update(modes)
    return matches


def _changed_quantity_bounds(source: str, result: str) -> list[str]:
    original = _quantity_modes(source)
    rewritten = _quantity_modes(result)
    # Multiple distinct descriptions of the same quantity are ambiguous;
    # avoid claiming a change when at least one source condition still exists.
    return [f"{number}{unit}" for (number, unit), modes in original.items()
            if (number, unit) in rewritten and modes.isdisjoint(rewritten[(number, unit)])]


def review_result(prompt: str, context: str, result: str) -> list[str]:
    """Return possible literal omissions for an AI result; never alter the text."""
    if not isinstance(prompt, str) or not isinstance(context, str) or not isinstance(result, str):
        raise ValueError("원문과 결과를 텍스트로 입력해 주세요.")
    if not prompt.strip() or not result.strip():
        return ["원문과 결과를 모두 입력한 뒤 확인해 주세요."]
    warnings = []
    comparable = lambda text: text.strip().replace("\r\n", "\n")
    answer = comparable(result)
    fenced = re.fullmatch(r"```[^\n`]*\n([\s\S]*?)\n```", answer)
    if answer == comparable(prompt) or (fenced and comparable(fenced.group(1)) == comparable(prompt)):
        warnings.append("결과가 원문과 같습니다. 정리가 이루어졌는지 확인해 주세요.")
    output_numbers = set(_numbers(result))
    missing_numbers = _unique(number for text in (prompt, context) for number in _numbers(text)
                              if number not in output_numbers)
    missing_quotes = _unique(quote for text in (prompt, context) for quote in _quotes(text)
                             if quote not in result)
    if missing_numbers:
        warnings.append("숫자 확인 필요: " + ", ".join(missing_numbers[:10]))
    if missing_quotes:
        warnings.append("따옴표 속 문구 확인 필요: " + ", ".join(missing_quotes[:10]))
    missing_schedule = _missing_schedule(prompt + "\n" + context, result)
    if missing_schedule:
        warnings.append("날짜·요일·시간 확인 필요: " + ", ".join(missing_schedule[:10]))
    missing_prohibitions = _unique(clause for source in (prompt, context)
                                   for _, clause in _prohibitions(source)
                                   if clause not in result)
    if missing_prohibitions:
        snippets = [value if len(value) <= 120 else value[:120] + "…"
                    for value in missing_prohibitions[:5]]
        warnings.append("금지 조건 원문 확인 필요: " + " / ".join(snippets)
                        + " — 문자 비교이므로 표현이 달라졌을 수도 있습니다.")
    if (re.search(r"문의|희망|예정|미확인", prompt + "\n" + context)
            and re.search(r"확인된\s*사실|(?<!미)확정(?!되지|하지|\s*전)", result)):
        warnings.append("문의·희망·예정·미확인 내용이 확정된 사실처럼 표현됐는지 원문과 대조해 주세요.")
    # Identifying who is asking and who is responding requires interpretation.
    # Offer a general check without copying names or guessing either role.
    source = prompt + "\n" + context
    if re.search(r"문의(?!처|전화|번호|메일)|질의|민원", source) and re.search(r"답변|회신", source):
        warnings.append("문의한 사람이나 기관과 답변 작성자·수신자가 바뀌지 않았는지 원문과 대조해 주세요.")
    changed_bounds = _changed_quantity_bounds(prompt + "\n" + context, result)
    if changed_bounds:
        warnings.append("수량 범위 확인 필요: " + ", ".join(changed_bounds[:10])
                        + "의 정확한 개수와 최대·최소·대략 표현이 바뀌었을 수 있습니다.")
    korean_in = len(re.findall(r"[가-힣]", prompt + context))
    korean_out = len(re.findall(r"[가-힣]", result))
    latin_out = len(re.findall(r"[A-Za-z]", result))
    explicit_english = re.search(
        r"(?:영어|영문|english)\s*(?:로|으로|로만|로\s*만)?\s*(?:번역|작성|표현|답변|대답|써|적어|만들|바꿔|바꾸|변환)",
        prompt, re.IGNORECASE,
    )
    if (korean_in >= 10 and korean_out + latin_out >= 80
            and korean_out / (korean_out + latin_out) < 0.05
            and not explicit_english and not fenced and not re.match(r"^[\[{]", answer)):
        warnings.append("결과의 언어가 원문과 다를 수 있습니다. 직접 확인해 주세요.")
    # A structured prompt for a one-line request is often a few hundred
    # characters; only a much longer answer suggests a finished document.
    if utf16_length(result) > utf16_length(prompt) * 3 and utf16_length(result) > utf16_length(prompt) + 600:
        warnings.append("결과가 원문보다 많이 깁니다. 프롬프트 대신 완성된 글이 나오지 않았는지 확인해 주세요.")
    warnings.append("문자 그대로만 비교합니다. 표현이 바뀌면 누락으로 표시될 수 있으며 의도와 조건은 직접 확인해야 합니다.")
    return warnings


_LABEL_LINE = re.compile(r"\A\s*\**\s*(?:prompt|프롬프트)[^\n:]{0,40}:\s*\**[ \t]*\n+", re.IGNORECASE)
_WRAPPING_FENCE = re.compile(r"\A```[^\n`]*\n([\s\S]*?)\n```\Z")
_REQUEST_END = re.compile(r"(?:주세요|줘|주십시오|하세요|해라|하라|바랍니다)\s*[.!]?\s*\Z")
_MATERIAL_CUE = re.compile(r"아래|다음|밑의")
_FIELD_LINE = re.compile(r"^\s*[^:\n\[\]]{1,25}:\s", re.MULTILINE)
_MATERIAL_SUBJECT = re.compile(r"자료|원문|본문|메모|기록|보고서")
_MATERIAL_LINE = re.compile(r"^\s*(?:자료|원문|본문):[ \t]*(\S.*)$")
_BRACKET_PLACEHOLDER = re.compile(r"\[([^\[\]\n]*)\]")
_PASTE_ACTION = re.compile(r"붙여[ \t]*넣기")
_OTHER_PLACEHOLDER = re.compile(r"연락처|전화|이메일|담당자|성명|실명|이름|주소|계좌|주민")
KEEP_ORIGINAL_HEADING = "원래 요청 (위 내용과 다르면 이 원문을 따르세요):"
KEEP_MATERIAL_HEADING = "자료 (원문 그대로):"


def _strip_wrapper(prompt: str, result: str) -> str:
    """Remove a label line, an outer code fence or quotes around the whole answer."""
    text = _LABEL_LINE.sub("", result.strip(), count=1).strip()
    if "```" not in prompt:
        fenced = _WRAPPING_FENCE.match(text)
        if fenced:
            text = fenced.group(1).strip()
    for left, right in (('"', '"'), ("“", "”")):
        inner = text[1:-1]
        if (len(text) > 2 and text.startswith(left) and text.endswith(right)
                and left not in inner and right not in inner):
            text = inner.strip()
    return text


def _material(prompt: str) -> str:
    """Return text the user supplied to be worked on, in two plain layouts only.

    "번역해 주세요: <text>" on the first line, or a request line followed by
    pasted text. Form-style lines are treated as material only after an
    explicit request ending with a colon or a source-specific request followed
    entirely by at least two field lines. Template placeholders remain excluded.
    """
    lines = prompt.strip().splitlines()
    if not lines:
        return ""
    first, rest = lines[0], "\n".join(lines[1:]).strip()
    head, colon, tail = first.partition(":")
    if colon and tail.strip() and _REQUEST_END.search(head):
        return "\n".join(part for part in (tail.strip(), rest) if part)
    # An explicit source on the last line is distinct from a form containing
    # subsequent instructions. Do not treat placeholder templates as material.
    if len(lines) > 1:
        source_line = _MATERIAL_LINE.match(lines[-1])
        if source_line and "[" not in source_line.group(1):
            return source_line.group(1)
    if rest and "[" not in rest:
        explicit_boundary = first.rstrip().endswith(":") and _REQUEST_END.search(head)
        source_request = _MATERIAL_CUE.search(first) and _REQUEST_END.search(first)
        rest_lines = [line for line in rest.splitlines() if line.strip()]
        field_only = len(rest_lines) >= 2 and all(_FIELD_LINE.match(line) for line in rest_lines)
        if (explicit_boundary or (source_request and not _FIELD_LINE.search(rest))
                or (source_request and _MATERIAL_SUBJECT.search(first) and field_only)):
            return rest
    return ""


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _single_material_placeholder(text: str) -> str:
    """Recognize one unambiguous source slot, never contact or other fields."""
    matches = list(_BRACKET_PLACEHOLDER.finditer(text))
    if len(matches) != 1:
        return ""
    value = matches[0].group(1)
    if (2 <= len(value) <= 40 and _MATERIAL_SUBJECT.search(value)
            and _PASTE_ACTION.search(value) and not _OTHER_PLACEHOLDER.search(value)):
        return matches[0].group(0)
    return ""


def keep_original_details(prompt: str, result: str, check_details: bool = True) -> tuple[str, list[str]]:
    """Tidy an AI-written prompt and re-attach original wording it lost.

    The AI is asked to keep numbers, quoted phrases and supplied material,
    but this is enforced here: when a literal check finds a missing number or
    quote, a changed exact/upper/lower bound, or a missing schedule literal,
    the original request is
    appended verbatim; when only the supplied material is missing, that
    material is appended. Revisions may change values on purpose, so callers
    pass check_details=False for them.
    """
    text = _strip_wrapper(prompt, result) or result.strip()
    if not check_details:
        return text, []
    material = _material(prompt)
    inserted = False
    placeholder = _single_material_placeholder(text)
    if material and placeholder and _flat(material) not in _flat(text):
        # Only one explicitly source-related paste slot is unambiguous.
        text = text.replace(placeholder, material, 1)
        inserted = True
    output_numbers = set(_numbers(text))
    # A missing pasted passage is already reattached verbatim below. Avoid
    # replacing its existing, more precise material handling with a duplicate
    # full-request warning merely because the passage contains a schedule.
    schedule_source = prompt.replace(material, "", 1) if material else prompt
    lost = (any(number not in output_numbers for number in _numbers(prompt))
            or any(quote not in text for quote in _quotes(prompt))
            or bool(_changed_quantity_bounds(prompt, text))
            or bool(_missing_schedule(schedule_source, text)))
    if lost:
        return (f"{text}\n\n{KEEP_ORIGINAL_HEADING}\n{prompt.strip()}",
                ["AI 결과에서 빠지거나 바뀐 숫자·일정·문구가 있어 원래 요청을 결과 끝에 원문 그대로 붙였어요."])
    core = material.strip().strip('"“”')
    if core and _flat(core) not in _flat(text):
        return (f"{text}\n\n{KEEP_MATERIAL_HEADING}\n{material}",
                ["AI 결과에 자료 원문이 그대로 없어 결과 끝에 붙였어요."])
    return text, (["요청에 있던 자료 원문을 자료 빈칸에 그대로 넣었어요."] if inserted else [])
