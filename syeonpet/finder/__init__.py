"""Syeon Smart File & Folder Finder (폴더 지옥 탈출 · 션이 파일 탐정)."""
from __future__ import annotations

from .extractors import extract_document_text
from .finder_ui import FileFinderDialog, FileFinderWidget
from .index_engine import (
    FileIndex,
    build_index,
    is_safe_to_open,
    safe_open_or_reveal,
    search,
    search_everything_fallback,
)
from .query_parser import ParsedQuery, extract_choseong, parse_natural_query

__all__ = [
    "FileIndex",
    "build_index",
    "search",
    "safe_open_or_reveal",
    "is_safe_to_open",
    "extract_document_text",
    "parse_natural_query",
    "extract_choseong",
    "ParsedQuery",
    "FileFinderWidget",
    "FileFinderDialog",
    "search_everything_fallback",
]
