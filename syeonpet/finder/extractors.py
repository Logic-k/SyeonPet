"""Safe, resilient document text extractors for Korean office environments.

Inspects magic bytes first so misnamed, encrypted, or DRM-wrapped files NEVER crash.
Uses 100% Python stdlib (no olefile, lxml, rapidfuzz).
"""
from __future__ import annotations

import html
import os
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib

MAX_XML_ENTRY_SIZE = 4 * 1024 * 1024  # 4MB zip bomb guard
MAX_PDF_SCAN_BYTES = 5 * 1024 * 1024  # 5MB PDF stream cap
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
HWP_CTRL_CODES = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def _read_cfb_streams(data: bytes) -> dict[str, bytes]:
    """Pure-Python Compound File Binary (MS-CFB / OLE2) parser (~90 lines)."""
    if len(data) < 512 or data[:8] != OLE2_MAGIC:
        return {}
    sec_shift, mini_shift = struct.unpack_from("<HH", data, 30)
    sec_size, mini_sec_size = 1 << sec_shift, 1 << mini_shift
    dir_sec = struct.unpack_from("<I", data, 48)[0]
    mini_fat_sec, num_mini_fat = struct.unpack_from("<II", data, 60)
    difat_sec, num_difat = struct.unpack_from("<II", data, 68)

    fat_secs = [s for s in struct.unpack_from("<109I", data, 76) if s < 0xFFFFFFFD]
    cur_difat = difat_sec
    while cur_difat < 0xFFFFFFFD and num_difat > 0:
        off = (cur_difat + 1) * sec_size
        if off + sec_size > len(data):
            break
        entries = struct.unpack(f"<{sec_size // 4}I", data[off:off + sec_size])
        fat_secs.extend(s for s in entries[:-1] if s < 0xFFFFFFFD)
        cur_difat = entries[-1]

    fat: list[int] = []
    for fs in fat_secs:
        off = (fs + 1) * sec_size
        if off + sec_size <= len(data):
            fat.extend(struct.unpack(f"<{sec_size // 4}I", data[off:off + sec_size]))

    def read_chain(start: int, max_len: int, is_mini: bool = False, mini_buf: bytes = b"") -> bytes:
        out, cur, visited = bytearray(), start, set()
        while cur < 0xFFFFFFFD and len(out) < max_len and cur not in visited:
            visited.add(cur)
            if is_mini:
                off = cur * mini_sec_size
                if off + mini_sec_size > len(mini_buf):
                    break
                out.extend(mini_buf[off:off + mini_sec_size])
                cur = minifat[cur] if cur < len(minifat) else 0xFFFFFFFE
            else:
                off = (cur + 1) * sec_size
                if off + sec_size > len(data):
                    break
                out.extend(data[off:off + sec_size])
                cur = fat[cur] if cur < len(fat) else 0xFFFFFFFE
        return bytes(out[:max_len])

    dir_data = read_chain(dir_sec, 2 * 1024 * 1024)
    entries: list[dict] = []
    for i in range(0, len(dir_data), 128):
        block = dir_data[i:i + 128]
        if len(block) < 128:
            break
        n_len, obj_type = struct.unpack_from("<HB", block, 64)
        start_sec, st_size = struct.unpack_from("<IQ", block, 116)
        name = block[:max(0, n_len - 2)].decode("utf-16le", errors="ignore").rstrip("\x00")
        entries.append({"name": name, "type": obj_type, "start": start_sec, "size": st_size})

    if not entries:
        return {}

    root = entries[0]
    mini_stream = read_chain(root["start"], root["size"], is_mini=False) if root["start"] < 0xFFFFFFFD else b""
    minifat_raw = read_chain(mini_fat_sec, num_mini_fat * sec_size, is_mini=False)
    minifat = list(struct.unpack(f"<{len(minifat_raw) // 4}I", minifat_raw)) if minifat_raw else []

    streams: dict[str, bytes] = {}
    for e in entries[1:]:
        if e["type"] == 2 and e["name"]:
            if e["size"] < 4096 and mini_stream and minifat:
                c = read_chain(e["start"], e["size"], is_mini=True, mini_buf=mini_stream)
            else:
                c = read_chain(e["start"], e["size"], is_mini=False)
            streams[e["name"].lower()] = streams[e["name"]] = c
    return streams


def _parse_xml_text(xml_bytes: bytes) -> list[str]:
    txt_data = xml_bytes.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(txt_data)
    except ET.ParseError:
        try:
            root = ET.fromstring(re.sub(r"<(/?)[\w-]+:", r"<\1", txt_data))
        except Exception:
            return [t.strip() for t in re.findall(r">([^<]+)<", txt_data) if t.strip()]
    parts = []
    for el in root.iter():
        if el.text:
            parts.append(el.text)
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag in ("p", "sec", "tr", "br") and parts and not parts[-1].endswith("\n"):
            parts.append("\n")
    return parts


def _extract_hwpx(path: Path, max_chars: int) -> tuple[str, str]:
    with zipfile.ZipFile(path, "r") as zf:
        nl = zf.namelist()
        if max_chars <= 800 and "Preview/PrvText.txt" in nl:
            return zf.read("Preview/PrvText.txt").decode("utf-16le", errors="replace")[:max_chars].strip(), ""
        secs = sorted(n for n in nl if n.startswith("Contents/section") and n.endswith(".xml"))
        collected = []
        for sname in secs:
            if zf.getinfo(sname).file_size > MAX_XML_ENTRY_SIZE:
                continue
            collected.extend(_parse_xml_text(zf.read(sname)))
            if sum(len(c) for c in collected) >= max_chars:
                break
        res = "".join(collected).strip()
        if res:
            return res[:max_chars], ""
        if "Preview/PrvText.txt" in nl:
            return zf.read("Preview/PrvText.txt").decode("utf-16le", errors="replace")[:max_chars].strip(), ""
    return "", ""


def _extract_hwp(data: bytes, max_chars: int) -> tuple[str, str]:
    if data[:8] != OLE2_MAGIC:
        return ("", "손상/특수 HWPX 문서 · 파일명 검색") if data[:4] == b"PK\x03\x04" else ("", "보안(DRM)/특수 문서 · 파일명 검색")
    streams = _read_cfb_streams(data)
    hdr = streams.get("fileheader") or streams.get("FileHeader") or b""
    flags = struct.unpack_from("<I", hdr, 36)[0] if len(hdr) >= 40 else 0
    if flags & 0x02:
        return "", "암호 문서 · 파일명만 검색"
    if flags & 0x04:
        prv = streams.get("prvtext") or streams.get("PrvText") or b""
        return prv.decode("utf-16le", errors="replace").strip()[:max_chars], "배포용 문서 · 미리보기 검색"

    sec_key = next((k for k in streams if k.lower().endswith("section0")), None)
    if sec_key and max_chars > 600:
        raw = streams[sec_key]
        try:
            decomp = zlib.decompress(raw, -15) if (flags & 0x01) else raw
        except Exception:
            try:
                decomp = zlib.decompress(raw)
            except Exception:
                decomp = raw
        parts, off, dlen = [], 0, len(decomp)
        while off + 4 <= dlen:
            hdr_val = struct.unpack_from("<I", decomp, off)[0]
            tag, sz = hdr_val & 0x3FF, (hdr_val >> 20) & 0xFFF
            off += 4
            if sz == 0xFFF:
                if off + 4 > dlen:
                    break
                sz = struct.unpack_from("<I", decomp, off)[0]
                off += 4
            rec, off = decomp[off:off + sz], off + sz
            if tag == 67:  # HWPTAG_PARA_TEXT
                i, rlen = 0, len(rec)
                while i + 2 <= rlen:
                    code = struct.unpack_from("<H", rec, i)[0]
                    i += 2
                    if code in HWP_CTRL_CODES:
                        i += 16  # skip 8 wide-chars
                    elif code in (10, 13):
                        parts.append("\n")
                    elif code >= 32:
                        parts.append(chr(code))
                parts.append("\n")
        body = "".join(parts).strip()
        if body:
            return body[:max_chars], ""
    prv = streams.get("prvtext") or streams.get("PrvText") or b""
    return prv.decode("utf-16le", errors="replace").strip()[:max_chars], "" if prv else ("", "")


def _extract_docx(path: Path, max_chars: int) -> tuple[str, str]:
    with zipfile.ZipFile(path, "r") as zf:
        if "word/document.xml" not in zf.namelist():
            return "", ""
        return "".join(_parse_xml_text(zf.read("word/document.xml")))[:max_chars].strip(), ""


def _extract_xlsx(path: Path, max_chars: int) -> tuple[str, str]:
    with zipfile.ZipFile(path, "r") as zf:
        nl, parts = zf.namelist(), []
        if "xl/workbook.xml" in nl:
            for s in ET.fromstring(zf.read("xl/workbook.xml")).iter():
                name = s.attrib.get("name")
                if name:
                    parts.append(name)
        if "xl/sharedStrings.xml" in nl and zf.getinfo("xl/sharedStrings.xml").file_size <= MAX_XML_ENTRY_SIZE:
            for el in ET.fromstring(zf.read("xl/sharedStrings.xml")).iter():
                if el.tag.endswith("t") and el.text:
                    parts.append(el.text)
        for sp in [n for n in nl if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]:
            if sum(len(t) for t in parts) >= max_chars:
                break
            if zf.getinfo(sp).file_size <= MAX_XML_ENTRY_SIZE:
                for el in ET.fromstring(zf.read(sp)).iter():
                    if el.tag.endswith("t") and el.text and el.text not in parts:
                        parts.append(el.text)
        return " ".join(parts)[:max_chars].strip(), ""


def _extract_pptx(path: Path, max_chars: int) -> tuple[str, str]:
    with zipfile.ZipFile(path, "r") as zf:
        slides = sorted(n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        parts = []
        for s in slides:
            parts.extend(_parse_xml_text(zf.read(s)))
            if sum(len(c) for c in parts) >= max_chars:
                break
        return " ".join("".join(parts).split())[:max_chars].strip(), ""


def _extract_pdf(raw_data: bytes, max_chars: int) -> tuple[str, str]:
    if b"/Encrypt" in raw_data[:8192]:
        return "", "암호 문서 · 파일명 검색"
    scan_limit = min(len(raw_data), MAX_PDF_SCAN_BYTES)
    data = raw_data[:scan_limit]
    parts: list[str] = []
    idx = 0
    while idx < scan_limit and sum(len(c) for c in parts) < max_chars:
        s_start = data.find(b"stream", idx)
        if s_start == -1:
            break
        after_s = s_start + 6
        if after_s < scan_limit and data[after_s:after_s + 1] == b"\r":
            after_s += 1
        if after_s < scan_limit and data[after_s:after_s + 1] == b"\n":
            after_s += 1
        s_end = data.find(b"endstream", after_s)
        if s_end == -1:
            break
        idx = s_end + 9
        stream_bytes = data[after_s:s_end]
        if len(stream_bytes) > 256 * 1024:
            continue
        try:
            decomp = zlib.decompress(stream_bytes)
        except Exception:
            try:
                decomp = zlib.decompress(stream_bytes, -15)
            except Exception:
                decomp = None
        if decomp:
            for tm in re.findall(rb"\(([^()]{1,250})\)\s*Tj", decomp):
                try:
                    t = tm.decode("utf-8", errors="ignore")
                    if t:
                        parts.append(t)
                except Exception:
                    pass
    res = " ".join(parts).strip()
    return (res, "스캔/이미지 PDF · 파일명 검색") if len(res) < 10 else (res[:max_chars], "")


def _extract_text(raw_data: bytes, ext: str, max_chars: int) -> tuple[str, str]:
    if raw_data.count(b"\x00") / max(1, len(raw_data)) > 0.01:
        return "", "바이너리 파일 · 파일명 검색"
    try:
        txt = raw_data.decode("utf-8-sig")
    except UnicodeDecodeError:
        txt = raw_data.decode("cp949", errors="replace")
    if ext in (".html", ".htm"):
        txt = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", txt)))
    return " ".join(txt.split())[:max_chars].strip(), ""


def extract_document_text(path: Path | str, max_chars: int = 12000) -> tuple[str, str]:
    """Inspect magic bytes and extract plain text and safety status badge."""
    p = Path(path)
    if not p.is_file():
        return "", ""
    ext = p.suffix.lower()
    try:
        with open(p, "rb") as f:
            head = f.read(min(8192, p.stat().st_size))
            if not head:
                return "", ""
        if head.startswith(OLE2_MAGIC):
            full = p.read_bytes()
            if b"EncryptedPackage" in head or b"EncryptedPackage" in full[:4096]:
                return "", "암호 문서 · 파일명 검색"
            return _extract_hwp(full, max_chars)
        if head.startswith(b"PK\x03\x04"):
            if ext == ".hwpx":
                return _extract_hwpx(p, max_chars)
            if ext in (".docx", ".doc"):
                return _extract_docx(p, max_chars)
            if ext in (".xlsx", ".xlsm", ".xls"):
                return _extract_xlsx(p, max_chars)
            if ext in (".pptx", ".ppt"):
                return _extract_pptx(p, max_chars)
            return _extract_hwpx(p, max_chars)
        if head.startswith(b"%PDF-"):
            with open(p, "rb") as pf:
                return _extract_pdf(pf.read(MAX_PDF_SCAN_BYTES), max_chars)
        if ext in (".txt", ".md", ".csv", ".tsv", ".html", ".htm", ".json", ".log", ".ini", ".cfg"):
            return _extract_text(p.read_bytes(), ext, max_chars)
        if ext in (".hwpx", ".hwp"):
            return "", "보안(DRM)/특수 문서 · 파일명 검색"
        return "", ""
    except Exception:
        return "", ""
