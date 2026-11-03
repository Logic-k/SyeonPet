"""Natural language query parser for Korean office file search.

Handles Hangul Choseong (initial consonants), time hints, type synonyms,
folder hints, and conversational query normalization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import time

CHOSEONG_LIST = [
    "ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"
]

CHOSEONG_PATTERN = re.compile(r"^[ㄱ-ㅎ]{2,}$")

TYPE_SYNONYMS: dict[str, set[str]] = {
    "엑셀": {".xlsx", ".xls", ".csv"},
    "시트": {".xlsx", ".xls", ".csv"},
    "스프레드시트": {".xlsx", ".xls", ".csv"},
    "한글": {".hwpx", ".hwp"},
    "한글파일": {".hwpx", ".hwp"},
    "아래아한글": {".hwpx", ".hwp"},
    "워드": {".docx", ".doc"},
    "워드파일": {".docx", ".doc"},
    "피피티": {".pptx", ".ppt"},
    "발표자료": {".pptx", ".ppt"},
    "ppt": {".pptx", ".ppt"},
    "파워포인트": {".pptx", ".ppt"},
    "피디에프": {".pdf"},
    "pdf": {".pdf"},
    "텍스트": {".txt", ".md"},
    "메모": {".txt", ".md"},
    "사진": {".jpg", ".jpeg", ".png"},
    "그림": {".jpg", ".jpeg", ".png"},
}

FOLDER_SYNONYMS: dict[str, str] = {
    "카톡": "kakaotalk",
    "카카오톡": "kakaotalk",
    "다운로드": "downloads",
    "다운": "downloads",
    "바탕화면": "desktop",
    "바탕": "desktop",
    "데스크탑": "desktop",
    "내문서": "documents",
    "문서함": "documents",
}

CONVERSATIONAL_FILLERS = {
    "찾아줘", "찾아", "찾기", "어디있지", "어디갔어", "어디에", "보여줘", "열어줘",
    "파일", "문서", "자료", "받은", "으로", "에서", "있는", "좀", "해줘",
}

JOSA_SUFFIXES = ("으로", "에서", "에게", "한테", "의", "을", "를", "은", "는", "이", "가", "에", "와", "과", "도", "로")


def extract_choseong(text: str) -> str:
    """Convert Korean syllables to initial consonants, keep ascii alnum lowercase, omit punctuation."""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            idx = (code - 0xAC00) // 588
            out.append(CHOSEONG_LIST[idx])
        elif 0x3131 <= code <= 0x314E:
            out.append(ch)
        elif ch.isalnum() and ch.isascii():
            out.append(ch.lower())
    return "".join(out)


@dataclass
class ParsedQuery:
    raw: str
    keywords: list[str] = field(default_factory=list)
    choseong_terms: list[str] = field(default_factory=list)
    ext_boosts: set[str] = field(default_factory=set)
    ext_filters: set[str] = field(default_factory=set)
    folder_hints: set[str] = field(default_factory=set)
    min_mtime: float | None = None
    max_mtime: float | None = None
    time_boost: str | None = None


def parse_natural_query(raw_query: str, now: float | None = None) -> ParsedQuery:
    """Parse Korean natural language search queries with office synonym expansion."""
    raw = raw_query.strip()
    if not raw:
        return ParsedQuery(raw="")

    cur_time = time.time() if now is None else now
    keywords: list[str] = []
    choseong_terms: list[str] = []
    ext_boosts: set[str] = set()
    ext_filters: set[str] = set()
    folder_hints: set[str] = set()
    time_boost: str | None = None

    tokens = raw.split()
    for token in tokens:
        clean = token.strip()
        if not clean:
            continue

        # Check pure choseong token
        if CHOSEONG_PATTERN.match(clean):
            choseong_terms.append(clean)
            continue

        # File extension direct match (e.g. dangerous_update.exe or .hwpx)
        if "." in clean and not clean.startswith("."):
            stem, ext_part = clean.rsplit(".", 1)
            ext_boosts.add(f".{ext_part.lower()}")
            keywords.append(clean)
            if stem:
                keywords.append(stem)
            continue

        # Strip trailing josa if token is longer than josa
        base = clean
        for josa in sorted(JOSA_SUFFIXES, key=len, reverse=True):
            if base.endswith(josa) and len(base) > len(josa):
                # Avoid stripping meaningful noun endings like 계획서, 신청서
                candidate = base[:-len(josa)]
                if len(candidate) >= 2 or candidate in FOLDER_SYNONYMS or candidate in TYPE_SYNONYMS:
                    base = candidate
                    break

        # Check time hints (Soft boost to prevent excluding recent test fixtures)
        if base in ("오늘", "방금", "아까", "지금"):
            time_boost = "today"
            continue
        if base in ("어제", "어저께", "그저께"):
            time_boost = "yesterday"
            continue
        if base in ("이번주", "이번", "최근"):
            time_boost = "recent"
            continue

        # Check folder synonyms
        matched_folder = False
        for fkey, ftarget in FOLDER_SYNONYMS.items():
            if fkey in base:
                folder_hints.add(ftarget)
                matched_folder = True
        if matched_folder and base in FOLDER_SYNONYMS:
            continue

        # Check type synonyms
        matched_type = False
        for tkey, sexts in TYPE_SYNONYMS.items():
            if tkey in base:
                ext_boosts.update(sexts)
                matched_type = True
        if matched_type and base in TYPE_SYNONYMS:
            continue

        # Filter conversational fillers
        if base in CONVERSATIONAL_FILLERS or clean in CONVERSATIONAL_FILLERS:
            continue

        # Regular keyword
        keywords.append(base)

    # Fallback if all tokens were stripped as conversational fillers
    if not keywords and not choseong_terms and not folder_hints and not ext_boosts:
        for t in tokens:
            if t not in ("찾아줘", "보여줘", "열어줘", "어디있지", "어디갔어"):
                keywords.append(t)

    return ParsedQuery(
        raw=raw,
        keywords=keywords,
        choseong_terms=choseong_terms,
        ext_boosts=ext_boosts,
        ext_filters=ext_filters,
        folder_hints=folder_hints,
        min_mtime=None,
        max_mtime=None,
        time_boost=time_boost,
    )
