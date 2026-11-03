"""SyeonPet PII Privacy Guard.

Detects sensitive Korean personal information (RRN, cards, accounts, phone numbers,
emails, driver's licenses, passports, addresses) and provides one-click masking
with restoration maps and human-friendly Korean summaries.
"""
from __future__ import annotations

import re
from typing import Any

# Sorted in precedence order for non-overlapping span matching
PATTERNS = [
    ("rrn", "주민등록번호", re.compile(r"(?<!\d)\d{6}-[1-4]\d{6}(?!\d)")),
    ("foreigner_rrn", "외국인등록번호", re.compile(r"(?<!\d)\d{6}-[5-8]\d{6}(?!\d)")),
    ("card", "카드번호", re.compile(r"(?<!\d)\d{4}-\d{4}-\d{4}-\d{4}(?!\d)")),
    ("driver_license", "운전면허번호", re.compile(r"(?<!\d)\d{2}-\d{2}-\d{6}-\d{2}(?!\d)")),
    ("phone", "연락처", re.compile(
        r"(?<!\d)(?:01[016789]-\d{3,4}-\d{4}|01[016789]\d{7,8}|0(?:2|[3-6][1-5]|70|50\d?)-\d{3,4}-\d{4})(?!\d)"
    )),
    ("email", "이메일", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("passport", "여권번호", re.compile(r"(?<![A-Za-z0-9])[MSRGD][0-9]{8}(?![0-9])")),
    ("address", "주소", re.compile(
        r"(?:[가-힣]{2,}(?:특별시|광역시|특별자치시|특별자치도|도)\s+)?[가-힣]{1,6}(?:시|군|구)\s+[가-힣0-9]{1,10}(?:대로|로|길)\s+\d+(?:-\d+)?"
    )),
    ("account", "계좌번호", re.compile(r"(?<!\d)(?<!제)\d{3,6}-\d{2,6}-\d{3,7}(?!호)(?!\d)")),
]

REDACTION_MARKERS = (
    "[연락처", "[전화번호", "[주민", "[외국인", "[계좌",
    "[이메일", "[주소", "[카드", "[운전면허", "[여권", "가림]",
)


def detect_pii(text: str) -> list[dict[str, Any]]:
    """Detect personal identifiable information spans in Korean office text."""
    if not isinstance(text, str) or not text:
        return []
    spans: list[dict[str, Any]] = []
    occupied = [False] * len(text)
    for kind, label, pat in PATTERNS:
        for match in pat.finditer(text):
            start, end = match.start(), match.end()
            if any(occupied[start:end]):
                continue
            for i in range(start, end):
                occupied[i] = True
            spans.append({
                "kind": kind,
                "label": label,
                "value": match.group(),
                "start": start,
                "end": end,
            })
    spans.sort(key=lambda s: s["start"])
    return spans


def mask_pii(text: str, restore_map: dict[str, str] | None = None) -> str:
    """Mask detected PII spans with Korean placeholder tokens.

    If restore_map is provided, populates token -> original_value mapping
    so the user can restore original values locally when desired.
    """
    if not isinstance(text, str) or not text:
        return text
    detected = detect_pii(text)
    if not detected:
        return text

    # Count occurrences per label to decide whether indexing is required
    counts: dict[str, int] = {}
    for item in detected:
        counts[item["label"]] = counts.get(item["label"], 0) + 1

    current_idx: dict[str, int] = {}
    pieces = []
    last_idx = 0

    for item in detected:
        start = item["start"]
        end = item["end"]
        label = item["label"]
        val = item["value"]

        current_idx[label] = current_idx.get(label, 0) + 1
        if counts[label] > 1:
            token = f"[{label} 가림 {current_idx[label]}]"
        else:
            token = f"[{label} 가림]"

        pieces.append(text[last_idx:start])
        pieces.append(token)
        last_idx = end

        if restore_map is not None:
            restore_map[token] = val

    pieces.append(text[last_idx:])
    return "".join(pieces)


def summarize_pii(text: str) -> str:
    """Return a novice-friendly Korean summary of detected PII, or empty string."""
    detected = detect_pii(text)
    if not detected:
        return ""
    label_counts: dict[str, int] = {}
    for item in detected:
        label_counts[item["label"]] = label_counts.get(item["label"], 0) + 1
    parts = [f"{lbl} {cnt}건" for lbl, cnt in label_counts.items()]
    return f"개인정보 후보 {len(detected)}건 감지됨 ({', '.join(parts)})"


def contains_redaction_placeholder(text: str) -> bool:
    """Check if the text contains any PII masking tokens."""
    if not isinstance(text, str):
        return False
    return any(marker in text for marker in REDACTION_MARKERS)


def _patch_optimizer_core_leak_guard() -> None:
    """Seamlessly prevent optimizer_core from leaking masked PII back into output."""
    try:
        import sys
        import optimizer_core
        orig_fn = getattr(optimizer_core, "keep_original_details", None)
        if orig_fn is None or getattr(orig_fn, "_pii_guarded", False):
            return

        def guarded_keep_original_details(prompt: str, result: str, check_details: bool = True):
            if contains_redaction_placeholder(result):
                masked_prompt = mask_pii(prompt)
                return orig_fn(masked_prompt, result, check_details=check_details)
            return orig_fn(prompt, result, check_details=check_details)

        guarded_keep_original_details._pii_guarded = True
        optimizer_core.keep_original_details = guarded_keep_original_details
    except Exception:
        pass


_patch_optimizer_core_leak_guard()
