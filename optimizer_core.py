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


def _quotes(text: str) -> list[str]:
    return _unique(a or b for a, b in re.findall(r'"([^"\n]{2,120})"|“([^”\n]{2,120})”', text))


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
    r"(?P<unit>페이지|문장|항목|단락|문단|단어|가지|쪽|줄|명|개|자)"
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
    pasted text. Form-style lines ("항목: 값") and [placeholders], as used by
    the templates, are conditions rather than material and are left alone.
    """
    lines = prompt.strip().splitlines()
    if not lines:
        return ""
    first, rest = lines[0], "\n".join(lines[1:]).strip()
    head, colon, tail = first.partition(":")
    if colon and tail.strip() and _REQUEST_END.search(head):
        return "\n".join(part for part in (tail.strip(), rest) if part)
    if (rest and (first.rstrip().endswith(":") or (_MATERIAL_CUE.search(first) and _REQUEST_END.search(first)))
            and not _FIELD_LINE.search(rest) and "[" not in rest):
        return rest
    return ""


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def keep_original_details(prompt: str, result: str, check_details: bool = True) -> tuple[str, list[str]]:
    """Tidy an AI-written prompt and re-attach original wording it lost.

    The AI is asked to keep numbers, quoted phrases and supplied material,
    but this is enforced here: when a literal check finds a missing number or
    quote, or a changed exact/upper/lower bound, the original request is
    appended verbatim; when only the supplied material is missing, that
    material is appended. Revisions may change values on purpose, so callers
    pass check_details=False for them.
    """
    text = _strip_wrapper(prompt, result) or result.strip()
    if not check_details:
        return text, []
    output_numbers = set(_numbers(text))
    lost = (any(number not in output_numbers for number in _numbers(prompt))
            or any(quote not in text for quote in _quotes(prompt))
            or bool(_changed_quantity_bounds(prompt, text)))
    if lost:
        return (f"{text}\n\n{KEEP_ORIGINAL_HEADING}\n{prompt.strip()}",
                ["AI 결과에서 빠지거나 바뀐 숫자·문구가 있어 원래 요청을 결과 끝에 원문 그대로 붙였어요."])
    material = _material(prompt)
    core = material.strip().strip('"“”')
    if core and _flat(core) not in _flat(text):
        return (f"{text}\n\n{KEEP_MATERIAL_HEADING}\n{material}",
                ["AI 결과에 자료 원문이 그대로 없어 결과 끝에 붙였어요."])
    return text, []
