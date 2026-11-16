"""Guided 'diagnose → ask → completed prompt' flow for education-office requests.

Compatibility facade over ``syeonpet.flow`` submodules; keeps module-level
names and Qt patch hooks expected by existing callers and tests.
"""
from __future__ import annotations

from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from optimizer_core import utf16_length
from syeonpet.flow.assembler import (
    alternative_types,
    apply_slot_fills,
    build_prompt,
    extract_fillable_slots,
    inspect_prompt_quality,
    render_prompt_html,
    tuner_axes,
)
from syeonpet.flow.flow_ui import (
    _FLOW_STYLES,
    _KNOWN_AI_ERRORS,
    _QUESTION_HINTS,
    FlowDialog,
    _QuestionCard,
    _ReplySignals,
    show_flow_dialog,
)
from syeonpet.flow.preferences import (
    _MAX_PREF_ENTRIES,
    _PREF_MIN_USES,
    EXTERNAL_AI,
    PreferenceStore,
    _atomic_write,
    _pref_path,
    copy_to_clipboard,
    open_external,
)
from syeonpet.flow.rules import (
    _CIRCLED,
    _FORBIDDEN_LATER,
    _GENERIC,
    _MAX_ANSWER,
    _MAX_INPUT,
    _MAX_QUESTIONS,
    _TYPE_BY_ID,
    _TYPES,
    CONFIRM_NEEDED,
    _ai_confirm_title,
    _answered_count,
    _completion_text,
    _is_local,
    _service_label,
    _topic,
    build_questions,
    classify,
    detect_segments,
    diagnose,
    extract_conditions,
    is_meaningless,
    summarize_recognized_conditions,
)

__all__ = [
    "CONFIRM_NEEDED",
    "EXTERNAL_AI",
    "FlowDialog",
    "PreferenceStore",
    "QDesktopServices",
    "QMessageBox",
    "alternative_types",
    "apply_slot_fills",
    "build_prompt",
    "build_questions",
    "classify",
    "copy_to_clipboard",
    "detect_segments",
    "diagnose",
    "extract_conditions",
    "extract_fillable_slots",
    "inspect_prompt_quality",
    "is_meaningless",
    "open_external",
    "render_prompt_html",
    "show_flow_dialog",
    "summarize_recognized_conditions",
    "tuner_axes",
    "utf16_length",
]
