"""High-performance local file indexing and search engine.

Features:
- Pure stdlib + local offline guarantee.
- OneDrive Files-On-Demand awareness (0x1000, 0x400000).
- Incremental zlib cache with RRN PII masking.
- Korean Choseong & bigram search + BM25-style scoring.
- Version duplicate grouping promoting latest mtime.
- Dangerous executable blocking guard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zlib

from .extractors import extract_document_text
from .query_parser import ParsedQuery, extract_choseong, parse_natural_query

DANGEROUS_EXTENSIONS = {
    ".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jse",
    ".wsf", ".scr", ".com", ".pif", ".lnk", ".reg", ".hta"
}
EXCLUDED_DIRS = {
    "appdata", "windows", "program files", "program files (x86)",
    "$recycle.bin", "node_modules", ".git", "__pycache__", ".venv", "venv"
}
VERSION_CLEAN_RE = re.compile(
    r"([-_ ]*(?:초안|최종본|최종|진짜최종|수정\d*|검토본|검토의견|복사본|사본|작업본|\(복사본\)|\(\d+\)|_v\d+|_ver\d+))+$",
    re.IGNORECASE
)
RRN_PATTERN = re.compile(r"\b(\d{6})[- ]?([1-8])\d{6}\b")


def is_safe_to_open(path: Path | str) -> bool:
    """Return False if path suffix is in dangerous executable extensions list."""
    return Path(path).suffix.lower() not in DANGEROUS_EXTENSIONS


def safe_open_or_reveal(path: Path | str, action: str = "open") -> bool:
    """Safely open safe documents or reveal them in Windows Explorer."""
    p = Path(path)
    if action == "open":
        if not is_safe_to_open(p):
            return False
        try:
            if sys.platform == "win32":
                os.startfile(str(p.resolve()))
                return True
        except Exception:
            pass
        return False
    if action == "reveal":
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer.exe", f"/select,{str(p.resolve())}"])
                return True
        except Exception:
            pass
        return False
    return False


@dataclass
class FileRecord:
    path: str
    name: str
    stem: str
    ext: str
    parent: str
    size: int
    mtime: float
    mtime_ns: int
    badge: str
    snippet: str
    choseong_stem: str
    normalized_stem: str
    is_dangerous: bool
    older_versions: list[FileRecord] = field(default_factory=list)


def normalize_stem(stem: str) -> str:
    cleaned = VERSION_CLEAN_RE.sub("", stem).strip("-_ ")
    return cleaned or stem


def make_bigrams(text: str) -> set[str]:
    c = re.sub(r"[^\w가-힣]", "", text.lower())
    return {c[i:i + 2] for i in range(len(c) - 1)} if len(c) >= 2 else set()


def get_default_scan_roots() -> list[Path]:
    u = Path.home()
    roots = [u / "Desktop", u / "Downloads", u / "Documents", u / "Downloads" / "KakaoTalk Downloads", u / "Documents" / "KakaoTalk Downloads"]
    return [r for r in roots if r.exists()]


def get_cache_file_path() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    base = (Path(local_appdata) if local_appdata else Path.home() / ".syeonpet") / "SyeonPet" / "file-finder"
    base.mkdir(parents=True, exist_ok=True)
    return base / "index_v1.json.zlib"


class FileIndex:
    def __init__(self, roots: list[Path], cache_path: Path | None = None):
        self.roots = roots
        self.cache_path = cache_path or get_cache_file_path()
        self.records: list[FileRecord] = []
        self._cache: dict[str, dict] = {}
        self._load_cache()
        self.rebuild()

    def _load_cache(self) -> None:
        if self.cache_path and self.cache_path.is_file():
            try:
                self._cache = json.loads(zlib.decompress(self.cache_path.read_bytes()).decode("utf-8"))
            except Exception:
                self._cache = {}

    def _save_cache(self) -> None:
        if self.cache_path:
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_bytes(zlib.compress(json.dumps(self._cache, ensure_ascii=False).encode("utf-8")))
            except Exception:
                pass

    def rebuild(self) -> None:
        raw_recs, visited = [], set()
        for root in self.roots:
            if root.exists():
                self._scan_dir(root, raw_recs, visited)

        groups: dict[tuple[str, str, str], list[FileRecord]] = {}
        for r in raw_recs:
            norm_p = str(Path(r.parent).resolve()).lower()
            groups.setdefault((norm_p, r.normalized_stem.lower(), r.ext.lower()), []).append(r)

        final_recs = []
        for _, members in groups.items():
            members.sort(key=lambda x: x.mtime, reverse=True)
            primary = members[0]
            if len(members) > 1:
                primary.older_versions = members[1:]
            final_recs.append(primary)

        self.records = final_recs
        self._save_cache()

    def _scan_dir(self, folder: Path, out: list[FileRecord], visited: set[str]) -> None:
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    nm = entry.name
                    if nm.startswith("~$") or nm.endswith(".tmp") or nm.endswith(".crdownload"):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if nm.lower() not in EXCLUDED_DIRS and not nm.startswith("."):
                            self._scan_dir(Path(entry.path), out, visited)
                    elif entry.is_file(follow_symlinks=False):
                        p_norm = str(Path(entry.path).resolve())
                        if p_norm not in visited:
                            visited.add(p_norm)
                            rec = self._index_entry(entry, p_norm)
                            if rec:
                                out.append(rec)
        except (PermissionError, OSError):
            pass

    def _index_entry(self, entry: os.DirEntry, p_norm: str) -> FileRecord | None:
        try:
            st = entry.stat()
            p = Path(entry.path)
            ext, attrs = p.suffix.lower(), getattr(st, "st_file_attributes", 0)
            is_offline, is_danger = bool(attrs & (0x1000 | 0x400000)), ext in DANGEROUS_EXTENSIONS
            cached = self._cache.get(p_norm)
            badge, snippet = "", ""

            if is_offline:
                badge = "OneDrive 온라인 전용 · 파일명 검색"
            elif cached and cached.get("mtime_ns") == st.st_mtime_ns and cached.get("size") == st.st_size:
                badge, snippet = cached.get("badge", ""), cached.get("snippet", "")
            else:
                if not is_danger and st.st_size <= 25 * 1024 * 1024:
                    raw_text, text_badge = extract_document_text(p, max_chars=450)
                    badge = text_badge
                    snippet = " ".join(RRN_PATTERN.sub(r"\1-\2******", raw_text).split())[:450]
                self._cache[p_norm] = {"mtime_ns": st.st_mtime_ns, "size": st.st_size, "badge": badge, "snippet": snippet}

            stem = p.stem
            return FileRecord(
                path=p_norm, name=entry.name, stem=stem, ext=ext, parent=str(p.parent),
                size=st.st_size, mtime=st.st_mtime, mtime_ns=st.st_mtime_ns,
                badge=badge, snippet=snippet, choseong_stem=extract_choseong(stem),
                normalized_stem=normalize_stem(stem), is_dangerous=is_danger
            )
        except Exception:
            return None


def build_index(roots_or_base_dir: Path | str | list[Path | str] | None = None, cache_path: Path | None = None) -> FileIndex:
    """Build or refresh local file index."""
    if roots_or_base_dir is None:
        roots = get_default_scan_roots()
    elif isinstance(roots_or_base_dir, (str, Path)):
        roots = [Path(roots_or_base_dir)]
    else:
        roots = [Path(r) for r in roots_or_base_dir]
    return FileIndex(roots=roots, cache_path=cache_path)


def search(query: str, index: FileIndex | None = None, filters: dict | None = None, limit: int = 30) -> list[dict]:
    """Search indexed files using natural language, Hangul Choseong, and recency ranking."""
    if index is None:
        index = build_index()
    q_str = query.strip()
    now = time.time()
    if not q_str:
        return [_record_to_dict(r, 100.0) for r in sorted(index.records, key=lambda x: x.mtime, reverse=True)[:limit]]

    parsed = parse_natural_query(q_str, now=now)
    if filters:
        if "ext" in filters:
            parsed.ext_filters.add(filters["ext"].lower())
        if "min_mtime" in filters:
            parsed.min_mtime = filters["min_mtime"]
        if "max_mtime" in filters:
            parsed.max_mtime = filters["max_mtime"]

    scored: list[tuple[float, FileRecord]] = []
    q_lower, q_bigrams = q_str.lower(), make_bigrams(q_str)

    for rec in index.records:
        if parsed.ext_filters and rec.ext not in parsed.ext_filters:
            continue
        if parsed.min_mtime and rec.mtime < parsed.min_mtime:
            continue
        if parsed.max_mtime and rec.mtime > parsed.max_mtime:
            continue

        score = 0.0
        n_low, s_low, p_low, b_low = rec.name.lower(), rec.stem.lower(), rec.path.lower(), rec.snippet.lower()
        cho_stem = rec.choseong_stem

        # Exact and substring filename matches
        if q_lower == n_low or q_lower == s_low:
            score += 300.0
        elif q_lower in s_low:
            score += 200.0
        elif q_lower in n_low:
            score += 180.0

        # Choseong matches (e.g. ㅇㅅㅇㄴ, ㅎㅈㅅ)
        for ct in parsed.choseong_terms:
            if ct == cho_stem:
                score += 280.0
            elif cho_stem.startswith(ct):
                score += 220.0
            elif ct in cho_stem:
                score += 180.0

        # Keywords across stem, name, body, folder path
        for kw in parsed.keywords:
            kl = kw.lower()
            if kl == s_low:
                score += 150.0
            elif kl in s_low:
                score += 90.0
            elif kl in n_low:
                score += 70.0
            elif kl in b_low:
                score += 45.0
            elif kl in p_low:
                score += 30.0
            kw_cho = extract_choseong(kl)
            if len(kw_cho) >= 2 and kw_cho in cho_stem:
                score += 50.0

        # Bigram match
        common_bi = q_bigrams & make_bigrams(f"{rec.name} {rec.snippet}")
        if common_bi:
            score += len(common_bi) * 15.0

        # Folder hints (e.g. kakaotalk)
        for fh in parsed.folder_hints:
            if fh in p_low:
                score += 70.0

        # Extension boosts
        if parsed.ext_boosts and rec.ext in parsed.ext_boosts:
            score += 65.0

        # Time boosts
        age_h = (now - rec.mtime) / 3600.0
        if parsed.time_boost in ("today", "yesterday", "recent") and age_h <= 72.0:
            score += 20.0
        score += min(5.0, max(0.0, 5.0 - (age_h / 168.0)))

        if score > 5.0:
            scored.append((score, rec))

    scored.sort(key=lambda it: (it[0], it[1].mtime), reverse=True)
    return [_record_to_dict(rec, s) for s, rec in scored[:limit]]


def _record_to_dict(rec: FileRecord, score: float) -> dict:
    return {
        "path": rec.path, "name": rec.name, "stem": rec.stem, "ext": rec.ext,
        "folder": rec.parent, "size": rec.size, "mtime": rec.mtime,
        "badge": rec.badge, "snippet": rec.snippet, "is_dangerous": rec.is_dangerous,
        "score": round(score, 2),
        "older_versions": [
            {"path": v.path, "name": v.name, "mtime": v.mtime, "size": v.size, "badge": v.badge}
            for v in rec.older_versions
        ],
    }


def search_everything_fallback(query: str, limit: int = 30) -> list[dict]:
    """Optional bridge to Voidtools Everything (es.exe) when installed."""
    es_path = shutil.which("es.exe")
    if not es_path or not query.strip():
        return []
    try:
        proc = subprocess.run([es_path, "-n", str(limit), query.strip()], capture_output=True, text=True, timeout=3.0, check=False)
        results = []
        for line in [l.strip() for l in proc.stdout.splitlines() if l.strip()]:
            p = Path(line)
            if p.is_file():
                results.append({
                    "path": str(p.resolve()), "name": p.name, "stem": p.stem, "ext": p.suffix.lower(),
                    "folder": str(p.parent), "size": p.stat().st_size, "mtime": p.stat().st_mtime,
                    "badge": "Everything 전체 검색", "snippet": "",
                    "is_dangerous": p.suffix.lower() in DANGEROUS_EXTENSIONS, "score": 50.0, "older_versions": [],
                })
        return results
    except Exception:
        return []
