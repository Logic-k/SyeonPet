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
DOCUMENT_EXTENSIONS = {
    ".hwp", ".hwpx", ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".csv",
    ".ppt", ".pptx", ".pdf", ".txt", ".md", ".rtf", ".html", ".htm",
    ".tsv", ".log", ".json", ".ini", ".cfg",
    ".zip", ".7z", ".rar",
    ".exe", ".bat", ".cmd", ".msi", ".ps1"
}
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff"
}
INDEXED_EXTENSIONS = DOCUMENT_EXTENSIONS | IMAGE_EXTENSIONS
EXCLUDED_DIRS = {
    "appdata", "windows", "program files", "program files (x86)",
    "$recycle.bin", "node_modules", ".git", "__pycache__", ".venv", "venv",
    ".idea", ".vscode", "dist", "build", "target", "bin", "obj",
    "cache", ".cache", ".gradle", ".cargo", "site-packages", "gems",
    "vendor", "lib", "libs", "temp", "tmp", ".pytest_cache"
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


def _known_folder_paths() -> dict[str, Path]:
    """Resolve Desktop/Documents/Downloads via User Shell Folders.

    The registry values already point at OneDrive-redirected or Korean-named
    folders when the user enabled known-folder backup, unlike Path.home() guesses.
    """
    if sys.platform != "win32":
        return {}
    import winreg
    mapping = {
        "Desktop": "Desktop",
        "Documents": "Personal",
        "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
        "Pictures": "My Pictures",
    }
    out: dict[str, Path] = {}
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            for name, value_name in mapping.items():
                try:
                    raw, _ = winreg.QueryValueEx(key, value_name)
                    candidate = Path(os.path.expandvars(str(raw)))
                    if candidate.is_dir():
                        out[name] = candidate
                except OSError:
                    continue
    except OSError:
        pass
    return out


def get_default_scan_roots() -> list[Path]:
    """Default scan roots: shell known folders + OneDrive + KakaoTalk dirs.

    Resolves Desktop/Downloads/Documents/Pictures through User Shell Folders so
    OneDrive-redirected and Korean-named folders are covered, then adds common
    OneDrive fallbacks and the real '카카오톡 받은 파일' locations.
    """
    u = Path.home()
    kf = _known_folder_paths()
    desktop = kf.get("Desktop", u / "Desktop")
    downloads = kf.get("Downloads", u / "Downloads")
    documents = kf.get("Documents", u / "Documents")
    pictures = kf.get("Pictures", u / "Pictures")

    roots: list[Path] = [desktop, downloads, documents, pictures]

    # KakaoTalk received-files folders (both real Korean and legacy EN names).
    doc_bases = {documents, u / "Documents", u / "OneDrive" / "문서", u / "OneDrive" / "Documents"}
    dl_bases = {downloads, u / "Downloads", u / "OneDrive" / "다운로드", u / "OneDrive" / "Downloads"}
    for base in doc_bases | dl_bases:
        for name in ("카카오톡 받은 파일", "KakaoTalk Downloads"):
            roots.append(base / name)

    # OneDrive folders that exist but are not the active shell target.
    for od_base in {u / "OneDrive", *u.glob("OneDrive -*")}:
        if od_base.is_dir():
            for name in ("바탕 화면", "Desktop", "문서", "Documents", "사진", "Pictures"):
                roots.append(od_base / name)

    seen: set[str] = set()
    resolved: list[Path] = []
    for root in roots:
        try:
            key = str(root.resolve()).lower()
        except OSError:
            key = str(root).lower()
        if key not in seen and root.exists():
            seen.add(key)
            resolved.append(root)
    return resolved


def get_cache_file_path() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    base = (Path(local_appdata) if local_appdata else Path.home() / ".syeonpet") / "SyeonPet" / "file-finder"
    base.mkdir(parents=True, exist_ok=True)
    return base / "index_v1.json.zlib"


class FileIndex:
    def __init__(self, roots: list[Path], cache_path: Path | None = None, max_depth: int = 4, max_files: int = 5000):
        self.roots = roots
        self.cache_path = cache_path or get_cache_file_path()
        self.max_depth = max_depth
        self.max_files = max_files
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
                # Per-root quota so one huge folder cannot starve later roots.
                root_recs: list[FileRecord] = []
                self._scan_dir(root, root_recs, visited, cur_depth=0)
                raw_recs.extend(root_recs)

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

    def _scan_dir(self, folder: Path, out: list[FileRecord], visited: set[str], cur_depth: int = 0) -> None:
        if cur_depth > self.max_depth or len(out) >= self.max_files:
            return
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    nm = entry.name
                    if nm.startswith("~$") or nm.endswith(".tmp") or nm.endswith(".crdownload"):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if nm.lower() not in EXCLUDED_DIRS and not nm.startswith("."):
                            self._scan_dir(Path(entry.path), out, visited, cur_depth + 1)
                    elif entry.is_file(follow_symlinks=False):
                        ext = Path(entry.path).suffix.lower()
                        if ext not in INDEXED_EXTENSIONS:
                            continue
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
                now = time.time()
                is_text = ext in (".txt", ".md", ".csv", ".tsv", ".log", ".json", ".ini", ".cfg")
                is_recent = (now - st.st_mtime) <= 14 * 86400
                if ext in IMAGE_EXTENSIONS:
                    badge = "이미지 · 파일명 검색"
                elif not is_danger and ext not in (".zip", ".7z", ".rar"):
                    if (is_text and st.st_size <= 512 * 1024) or (is_recent and st.st_size <= 2 * 1024 * 1024):
                        raw_text, text_badge = extract_document_text(p, max_chars=450)
                        badge = text_badge
                        snippet = " ".join(RRN_PATTERN.sub(r"\1-\2******", raw_text).split())[:450]
                    elif st.st_size > 2 * 1024 * 1024:
                        badge = "대용량 문서 · 파일명 검색"
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


def build_index(roots_or_base_dir: Path | str | list[Path | str] | None = None, cache_path: Path | None = None, max_depth: int = 4, max_files: int = 5000) -> FileIndex:
    """Build or refresh local file index."""
    if roots_or_base_dir is None:
        roots = get_default_scan_roots()
    elif isinstance(roots_or_base_dir, (str, Path)):
        roots = [Path(roots_or_base_dir)]
    else:
        roots = [Path(r) for r in roots_or_base_dir]
    return FileIndex(roots=roots, cache_path=cache_path, max_depth=max_depth, max_files=max_files)


def search(query: str, index: FileIndex | None = None, filters: dict | None = None, limit: int = 30) -> list[dict]:
    """Search indexed files using natural language, Hangul Choseong, and recency ranking."""
    if index is None:
        index = build_index()
    q_str = query.strip()
    now = time.time()
    parsed = parse_natural_query(q_str, now=now)
    if filters:
        if "exts" in filters and filters["exts"]:
            parsed.ext_filters.update(e.lower() for e in filters["exts"])
        elif "ext" in filters and filters["ext"]:
            parsed.ext_filters.add(filters["ext"].lower())
        if "min_mtime" in filters:
            parsed.min_mtime = filters["min_mtime"]
        if "max_mtime" in filters:
            parsed.max_mtime = filters["max_mtime"]

    def _passes(rec: FileRecord) -> bool:
        if parsed.ext_filters and rec.ext not in parsed.ext_filters:
            return False
        if parsed.min_mtime and rec.mtime < parsed.min_mtime:
            return False
        if parsed.max_mtime and rec.mtime > parsed.max_mtime:
            return False
        return True

    if not q_str:
        # Recent document listing: filters apply, executables are not documents.
        recs = sorted((r for r in index.records if not r.is_dangerous and _passes(r)),
                      key=lambda x: x.mtime, reverse=True)
        return [_record_to_dict(r, 100.0) for r in recs[:limit]]

    scored: list[tuple[float, FileRecord]] = []
    q_lower, q_bigrams = q_str.lower(), make_bigrams(q_str)

    for rec in index.records:
        if not _passes(rec):
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
