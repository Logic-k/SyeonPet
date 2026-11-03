"""Deterministic quantitative backtesting suite for SyeonPet.

Evaluates any version of SyeonPet across all 7 dimensions:
1. D1_direct_work_deliverable (10 realistic Korean office scenarios)
2. D2_ai_prompt_and_backend_resilience (Direct work system prompt, ChatGPT hiding, error translations, library deletion)
3. D3_folder_hell_file_finder (Synthetic folder hell: 40 files, hwpx/docx/xlsx/csv, 15 queries, recall@3, mrr, security)
4. D4_pii_privacy_protection (12 Korean PII inputs, 8 benign office numbers, masking, keep_original_details leak test)
5. D5_novice_usability_and_launch (Double-click action, quick action bar, right-click menu clutter, click-through safety, stationary onboarding, font scaling, shortcut helper, zero-admin setup)
6. D6_companion_cuteness_and_timer (Petting & heart particles, time-of-day care lines, timer reliability during sleep, visible timer badge)
7. D7_architecture_and_code_health (Dead root modules, max file lines, modular package organization)

Safe execution:
- Offscreen Qt (QT_QPA_PLATFORM=offscreen)
- Temporary user state (LOCALAPPDATA / APPDATA)
- Loopback-only network mocking (no external DNS/TCP)
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "Lib/site-packages")]

# Mock networking (loopback only)
REAL_DNS = socket.getaddrinfo
REAL_CONNECT = socket.socket.connect


def local_dns(host, *args, **kwargs):
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise socket.gaierror(socket.EAI_NONAME, "Backtest blocks external DNS")
    return REAL_DNS(host, *args, **kwargs)


def local_connect(connection, address):
    try:
        allowed = ipaddress.ip_address(address[0]).is_loopback
    except (ValueError, IndexError):
        allowed = False
    if not allowed:
        raise OSError("Backtest blocks external TCP")
    return REAL_CONNECT(connection, address)


# ---------------------------------------------------------------------------
# Dimension 1: Direct Work Deliverables (10 realistic Korean office scenarios)
# ---------------------------------------------------------------------------
SCENARIOS_D1 = [
    {
        "id": "scenario_01_sms",
        "title": "학부모 상담 안내 문자",
        "prompt": "내일 오후 4시까지 방과후학교 신청 마감이라고 학부모님께 정중한 안내 문자 써줘",
        "expected_terms": ["4시", "방과후학교"],
        "unconfirmed_terms": [],
    },
    {
        "id": "scenario_02_email",
        "title": "타 부서 협조 이메일",
        "prompt": "행정실에 금요일 오전까지 연수 장소 냉난방 확인 부탁드린다고 공손한 메일 써줘",
        "expected_terms": ["행정실", "금요일", "오전", "냉난방"],
        "unconfirmed_terms": [],
    },
    {
        "id": "scenario_03_official_doc",
        "title": "교육지원청 공문 초안",
        "prompt": "초등학교 4곳 교사 대상 11월 18일 기초학력 연수 안내 공문 초안 작성 (마감 11월 10일, 장소 미정)",
        "expected_terms": ["4곳", "11월 18일", "기초학력", "11월 10일"],
        "unconfirmed_terms": ["미정"],
    },
    {
        "id": "scenario_04_meeting_notes",
        "title": "회의록 요약 및 할 일 정리",
        "prompt": "10월 30일 회의 메모: 연수 강사 섭외 완료, 다과 예산 미확정, 김주무관이 금요일까지 안내장 발송 예정",
        "expected_terms": ["10월 30일", "강사", "김주무관", "금요일", "안내장"],
        "unconfirmed_terms": ["미확정"],
    },
    {
        "id": "scenario_05_inquiry_reply",
        "title": "민원·학교 문의 회신",
        "prompt": "학교 2곳이 12월 1일 점검 일정을 문의함. 일정 미확정. 교육지원청 담당자가 학교에 보낼 회신 초안",
        "expected_terms": ["2곳", "12월 1일", "회신"],
        "unconfirmed_terms": ["미확정"],
    },
    {
        "id": "scenario_06_checklist",
        "title": "행사 준비 체크리스트",
        "prompt": "학부모 연수 40명 대면 진행 사전·당일·사후 체크리스트 만들어줘",
        "expected_terms": ["40명", "사전", "당일", "사후"],
        "unconfirmed_terms": [],
    },
    {
        "id": "scenario_07_excel",
        "title": "엑셀 수식 질문",
        "prompt": "엑셀에서 B열이 '제출완료'인 행만 C열 금액 합계 구하는 수식 알려줘",
        "expected_terms": ["SUMIF"],
        "unconfirmed_terms": [],
    },
    {
        "id": "scenario_08_polite_rewrite",
        "title": "어색한 문장 공손하게 다듬기",
        "prompt": "기한 내에 안 내면 처리 안 됩니다.",
        "expected_terms": ["기한"],
        "unconfirmed_terms": [],
    },
    {
        "id": "scenario_09_table_summary",
        "title": "건수 요약 표 정리",
        "prompt": "지원 완료 7건, 진행 중 4건, 미확인 2건을 표로 정리하고 합계 적어줘",
        "expected_terms": ["7", "4", "2", "13"],
        "unconfirmed_terms": [],
    },
    {
        "id": "scenario_10_beginner_computer",
        "title": "컴퓨터 초보 질문",
        "prompt": "윈도우에서 화면 일부만 캡처해서 한글 문서에 넣는 단축키 알려줘",
        "expected_terms": ["Shift", "V"],
        "unconfirmed_terms": [],
    },
]


def evaluate_d1(verbose: bool = False) -> dict:
    results = []
    scenarios_passed = 0

    for sc in SCENARIOS_D1:
        prompt = sc["prompt"]
        output = None
        engine = "unknown"

        # Check for upgraded assistant core
        try:
            from syeonpet.work import assistant_core
            if hasattr(assistant_core, "generate_offline_deliverable"):
                output = assistant_core.generate_offline_deliverable(prompt)
                engine = "syeonpet.work.assistant_core.generate_offline_deliverable"
            elif hasattr(assistant_core, "create_direct_draft"):
                output = assistant_core.create_direct_draft(prompt)
                engine = "syeonpet.work.assistant_core.create_direct_draft"
        except (ImportError, AttributeError):
            pass

        # Fallback to existing v0.1.14 optimizer_core
        if output is None:
            try:
                import optimizer_core
                draft_dict = optimizer_core.create_offline_prompt(prompt)
                output = draft_dict.get("result", "")
                engine = "optimizer_core.create_offline_prompt"
            except Exception as e:
                output = f"ERROR: {e}"
                engine = "optimizer_core.error"

        # Evaluation criteria:
        # 1. Is it a meta-prompt refusing to write the actual draft?
        is_meta_prompt = (
            "원래 요청:" in output and
            "아래 원래 요청의 의도와 명시된 조건을 충실히 따라 답변해 주세요" in output
        )
        is_usable_draft = (not is_meta_prompt) and (len(output.strip()) > 20)

        # 2. Check terms preservation
        expected_matches = [term for term in sc["expected_terms"] if term.lower() in output.lower()]
        terms_preserved = (len(expected_matches) == len(sc["expected_terms"])) if is_usable_draft else False

        # 3. Check unconfirmed-state preservation
        unconfirmed_preserved = True
        if sc["unconfirmed_terms"]:
            for u in sc["unconfirmed_terms"]:
                if u not in output:
                    unconfirmed_preserved = False
                    break
        if not is_usable_draft:
            unconfirmed_preserved = False

        passed = is_usable_draft and terms_preserved and unconfirmed_preserved
        if passed:
            scenarios_passed += 1

        results.append({
            "id": sc["id"],
            "title": sc["title"],
            "engine": engine,
            "is_meta_prompt": is_meta_prompt,
            "is_usable_draft": is_usable_draft,
            "terms_preserved": terms_preserved,
            "unconfirmed_preserved": unconfirmed_preserved,
            "score": 1 if passed else 0,
            "snippet": output[:100].replace("\n", " "),
        })

    total = len(SCENARIOS_D1)
    pass_rate = round((scenarios_passed / total) * 100.0, 2)
    return {
        "scenarios_tested": total,
        "scenarios_passed": scenarios_passed,
        "pass_rate_percent": pass_rate,
        "details": results,
    }


# ---------------------------------------------------------------------------
# Dimension 2: AI Prompt & Backend Resilience
# ---------------------------------------------------------------------------
def evaluate_d2(verbose: bool = False) -> dict:
    # 1. Direct work execution mode in AI system prompt
    direct_work_mode_system_prompt = False
    try:
        from syeonpet.work import assistant_core
        if hasattr(assistant_core, "SYSTEM") and "요청된 일을 직접" in assistant_core.SYSTEM:
            # Check if it directs direct draft writing
            if "직접 작성" in assistant_core.SYSTEM or "초안을 작성" in assistant_core.SYSTEM:
                direct_work_mode_system_prompt = True
    except (ImportError, AttributeError):
        pass

    if not direct_work_mode_system_prompt:
        try:
            import optimizer_ai
            system_prompt = getattr(optimizer_ai, "SYSTEM", "")
            # v0.1.14 explicitly forbids direct work
            if "요청된 일을 직접 하지 마세요" in system_prompt or "결과물 예시를 직접 쓰지 마세요" in system_prompt:
                direct_work_mode_system_prompt = False
            elif "직접 초안" in system_prompt or "작성합니다" in system_prompt:
                direct_work_mode_system_prompt = True
        except Exception:
            direct_work_mode_system_prompt = False

    # 2. Fresh install hides unconfigured ChatGPT
    fresh_install_hides_unconfigured_chatgpt = False
    try:
        import ai_profiles
        with tempfile.TemporaryDirectory() as tmp_dir:
            dummy_path = Path(tmp_dir) / "nonexistent.json"
            profiles = ai_profiles.load_profiles(dummy_path)
            # In v0.1.14, it returns [AIProfile('default', 'chatgpt-subscription', 'auto')]
            if len(profiles) == 1 and profiles[0].provider == "chatgpt-subscription":
                fresh_install_hides_unconfigured_chatgpt = False
            else:
                fresh_install_hides_unconfigured_chatgpt = True
    except Exception:
        fresh_install_hides_unconfigured_chatgpt = False

    # 3. BackendError translation: API key & 429
    backend_error_api_key_guidance = False
    backend_error_rate_limit_guidance = False
    try:
        from ai_backends import BackendError
        sanitizer = None
        try:
            from syeonpet.chat import sanitize_error
            sanitizer = sanitize_error
        except (ImportError, AttributeError):
            import optimizer_chat
            sanitizer = getattr(optimizer_chat, "_safe_generation_error", None)

        if sanitizer is not None:
            msg_key = sanitizer(BackendError("Configure the provider API key first."))
            msg_429 = sanitizer(BackendError("AI service rate limit reached (HTTP 429)."))

            # Specific Korean guidance check (not generic unknown error)
            generic_err = "연결 실패 또는 정리 중 오류가 발생했습니다"
            if generic_err not in msg_key and ("API 키" in msg_key or "키 저장" in msg_key or "키를 입력" in msg_key):
                backend_error_api_key_guidance = True

            if generic_err not in msg_429 and ("사용량 제한" in msg_429 or "한도" in msg_429 or "429" in msg_429):
                backend_error_rate_limit_guidance = True
    except Exception:
        pass

    # 4. LibraryDialog supports item deletion
    library_dialog_supports_deletion = False
    try:
        from syeonpet.work import library_dialog
        if hasattr(library_dialog, "LibraryDialog") and (hasattr(library_dialog.LibraryDialog, "delete_item") or hasattr(library_dialog.LibraryDialog, "_delete")):
            library_dialog_supports_deletion = True
    except (ImportError, AttributeError):
        pass

    if not library_dialog_supports_deletion:
        try:
            import optimizer_library
            # Inspect LibraryDialog methods
            dlg_cls = getattr(optimizer_library, "LibraryDialog", None)
            if dlg_cls is not None:
                has_delete_method = any(m in dlg_cls.__dict__ for m in ("delete_item", "_delete", "_remove", "remove_item"))
                library_dialog_supports_deletion = has_delete_method
        except Exception:
            library_dialog_supports_deletion = False

    checks = [
        direct_work_mode_system_prompt,
        fresh_install_hides_unconfigured_chatgpt,
        backend_error_api_key_guidance,
        backend_error_rate_limit_guidance,
        library_dialog_supports_deletion,
    ]
    score_percent = round((sum(1 for c in checks if c) / len(checks)) * 100.0, 2)

    return {
        "direct_work_mode_system_prompt": direct_work_mode_system_prompt,
        "fresh_install_hides_unconfigured_chatgpt": fresh_install_hides_unconfigured_chatgpt,
        "backend_error_api_key_guidance": backend_error_api_key_guidance,
        "backend_error_rate_limit_guidance": backend_error_rate_limit_guidance,
        "library_dialog_supports_deletion": library_dialog_supports_deletion,
        "score_percent": score_percent,
    }


# ---------------------------------------------------------------------------
# Dimension 3: Folder Hell File Finder (40 files, hwpx/docx/xlsx/csv, 15 queries)
# ---------------------------------------------------------------------------
def _create_synthetic_folder_hell(base_dir: Path) -> dict:
    """Creates a realistic Folder Hell test tree with 40 files."""
    desktop = base_dir / "Desktop"
    downloads = base_dir / "Downloads"
    kakao = downloads / "KakaoTalk Downloads"
    documents = base_dir / "Documents"
    nested_office = base_dir / "2026_상반기" / "기초학력" / "붙임파일"

    for d in (desktop, downloads, kakao, documents, nested_office):
        d.mkdir(parents=True, exist_ok=True)

    def write_hwpx(p: Path, text: str, mtime_offset: float = 0):
        p.parent.mkdir(parents=True, exist_ok=True)
        xml = f'<?xml version="1.0" encoding="UTF-8"?><hp:sec><hp:p><hp:t>{text}</hp:t></hp:p></hp:sec>'
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("Contents/section0.xml", xml.encode("utf-8"))
        mtime = time.time() + mtime_offset
        os.utime(p, (mtime, mtime))

    def write_docx(p: Path, text: str, mtime_offset: float = 0):
        p.parent.mkdir(parents=True, exist_ok=True)
        xml = (f'<?xml version="1.0" encoding="UTF-8"?>'
               f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", xml.encode("utf-8"))
        mtime = time.time() + mtime_offset
        os.utime(p, (mtime, mtime))

    def write_xlsx(p: Path, text: str, mtime_offset: float = 0):
        p.parent.mkdir(parents=True, exist_ok=True)
        wb = '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets><sheet name="Sheet1" sheetId="1"/></sheets></workbook>'
        ss = f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1"><si><t>{text}</t></si></sst>'
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("xl/workbook.xml", wb.encode("utf-8"))
            z.writestr("xl/sharedStrings.xml", ss.encode("utf-8"))
        mtime = time.time() + mtime_offset
        os.utime(p, (mtime, mtime))

    def write_csv(p: Path, text: str, mtime_offset: float = 0):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="cp949", errors="ignore")
        mtime = time.time() + mtime_offset
        os.utime(p, (mtime, mtime))

    def write_txt(p: Path, text: str, mtime_offset: float = 0):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        mtime = time.time() + mtime_offset
        os.utime(p, (mtime, mtime))

    def write_bin(p: Path, header: bytes = b"MZ\x90\x00"):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(header + b"\x00" * 64)

    # Version duplicate set (ascending mtime)
    write_hwpx(desktop / "기초학력_운영계획_초안.hwpx", "2026년 기초학력 향상 운영계획 초안", mtime_offset=-300)
    write_hwpx(desktop / "기초학력_운영계획_최종.hwpx", "2026년 기초학력 향상 운영계획 최종본", mtime_offset=-200)
    target_v_latest = desktop / "기초학력_운영계획_진짜최종_수정2.hwpx"
    write_hwpx(target_v_latest, "2026년 기초학력 향상 운영계획 진짜최종 수정2", mtime_offset=-100)

    # Core target files
    target_choseong = desktop / "연수안내_2026.docx"
    write_docx(target_choseong, "2026 교원 직무 연수안내 및 참석 명단", mtime_offset=-50)

    target_kakao_excel = kakao / "2026_예산안_최종.xlsx"
    write_xlsx(target_kakao_excel, "어제 카톡으로 받은 예산 엑셀 파일입니다.", mtime_offset=-30)

    target_body_hwpx = nested_office / "강사_수당_지급_기준.hwpx"
    write_hwpx(target_body_hwpx, "강사 수당 지급 기준은 시간당 50,000원으로 책정한다.", mtime_offset=-40)

    target_body_docx = nested_office / "연수장소_냉난방_점검.docx"
    write_docx(target_body_docx, "연수 장소 냉난방 점검 결과 정상 작동 확인되었습니다.", mtime_offset=-45)

    target_csv = nested_office / "제출완료_명단.csv"
    write_csv(target_csv, "연번,학교명,성명,상태\n1,부산초,김철수,제출완료\n2,동백초,이영희,제출완료", mtime_offset=-25)

    target_afterschool = downloads / "방과후학교_신청서_양식.hwpx"
    write_hwpx(target_afterschool, "방과후학교 수강 신청서 양식 및 개인정보동의서", mtime_offset=-60)

    target_trip = documents / "출장신청서_서식.docx"
    write_docx(target_trip, "관내 관외 출장신청서 양식", mtime_offset=-70)

    target_plan = documents / "2026_교육계획서_요약.txt"
    write_txt(target_plan, "2026 계획서 핵심 과제 요약", mtime_offset=-80)

    target_admin_coop = documents / "행정실_협조요청_공문.hwpx"
    write_hwpx(target_admin_coop, "행정실 협조 요청 공문 서식", mtime_offset=-90)

    target_report = documents / "결과보고서_초안.docx"
    write_docx(target_report, "연수 운영 결과보고서 및 정산 내역", mtime_offset=-110)

    target_survey = documents / "설문조사_집계결과.xlsx"
    write_xlsx(target_survey, "학부모 만족도 설문조사 집계 표", mtime_offset=-120)

    target_terminal = kakao / "단말기_유지보수_일정.docx"
    write_docx(target_terminal, "학교 단말기 유지보수 및 점검 일정표", mtime_offset=-130)

    # Dangerous executables
    exe_file = downloads / "dangerous_update.exe"
    bat_file = downloads / "run_script.bat"
    write_bin(exe_file)
    write_txt(bat_file, "@echo off\nexit")

    # Extra office files to reach 40 files
    extra_files = [
        (desktop / "업무메모.txt", "행정실 제출 서류 목록"),
        (desktop / "바로가기_도움말.txt", "바탕화면 사용 안내"),
        (desktop / "2026_학사일정.docx", "2026학년도 전체 학사일정표"),
        (downloads / "다운로드_목록.txt", "임시 파일 다운로드 목록"),
        (downloads / "개인정보_서약서.hwpx", "교직원 보안 서약서"),
        (downloads / "체험학습_안내문.docx", "현장체험학습 학부모 동의서"),
        (downloads / "식자재_검수일지.xlsx", "급식 식자재 검수 일일 기록표"),
        (kakao / "사진_압축_안내.txt", "카카오톡 사진 다운로드 폴더"),
        (kakao / "회의_사진.jpg", "dummy image"),
        (kakao / "급식비_정산서.xlsx", "1분기 급식비 정산 내역서"),
        (documents / "공문_발송대장.xlsx", "2026 공문 발송 관리 대장"),
        (documents / "직원연락망.docx", "교육지원청 직원 비상 연락망"),
        (documents / "보안점검표.hwpx", "월간 사이버 보안 진단의 날 점검표"),
        (documents / "소방안전_계획.docx", "합동 소방 훈련 실시 계획서"),
        (documents / "인사발령_공지.txt", "상반기 정기 인사발령 사항 알림"),
        (documents / "물품관리대장.xlsx", "컴퓨터 및 전산 기기 물품 관리대장"),
        (nested_office / "기초학력_진단평가_매뉴얼.docx", "진단평가 문항 관리 및 실시 지침"),
        (nested_office / "학습지원대상학생_명단.xlsx", "대상 학생 지원 이력 관리"),
        (nested_office / "튜터_위촉_서약서.hwpx", "기초학력 전담 튜터 위촉 계약서"),
        (nested_office / "수당_영수증_서식.docx", "강사료 지급 영수증 서식"),
        (nested_office / "운영비_지출증빙.xlsx", "사업비 카드 지출 전표 모음"),
        (nested_office / "방과후_강사료_산출내역.xlsx", "방과후학교 강사료 시간당 산출 내역서"),
        (nested_office / "최종검토의견.txt", "장학사 검토의견서"),
    ]

    for p, content in extra_files:
        if p.suffix == ".hwpx":
            write_hwpx(p, content, mtime_offset=-200)
        elif p.suffix == ".docx":
            write_docx(p, content, mtime_offset=-200)
        elif p.suffix == ".xlsx":
            write_xlsx(p, content, mtime_offset=-200)
        elif p.suffix == ".jpg":
            write_bin(p, b"\xff\xd8\xff\xe0")
        else:
            write_txt(p, content, mtime_offset=-200)

    all_files = list(base_dir.rglob("*"))
    files_only = [f for f in all_files if f.is_file()]

    return {
        "base_dir": base_dir,
        "file_count": len(files_only),
        "target_v_latest": target_v_latest,
        "target_choseong": target_choseong,
        "target_kakao_excel": target_kakao_excel,
        "target_body_hwpx": target_body_hwpx,
        "target_body_docx": target_body_docx,
        "target_csv": target_csv,
        "target_afterschool": target_afterschool,
        "target_trip": target_trip,
        "target_plan": target_plan,
        "target_admin_coop": target_admin_coop,
        "target_report": target_report,
        "target_survey": target_survey,
        "target_terminal": target_terminal,
        "exe_file": exe_file,
    }


def evaluate_d3(base_dir: Path, verbose: bool = False) -> dict:
    folder_info = _create_synthetic_folder_hell(base_dir)

    finder_module = None
    try:
        from syeonpet import finder
        finder_module = finder
    except (ImportError, AttributeError):
        pass

    queries = [
        {"q": "기초학력 운영계획", "expected": folder_info["target_v_latest"]},
        {"q": "ㅇㅅㅇㄴ", "expected": folder_info["target_choseong"]},
        {"q": "어제 카톡으로 받은 예산 엑셀", "expected": folder_info["target_kakao_excel"]},
        {"q": "강사 수당 지급 기준", "expected": folder_info["target_body_hwpx"]},
        {"q": "냉난방 점검", "expected": folder_info["target_body_docx"]},
        {"q": "제출완료 명단", "expected": folder_info["target_csv"]},
        {"q": "", "expected": None},  # Recent files query
        {"q": "방과후학교 신청서", "expected": folder_info["target_afterschool"]},
        {"q": "출장신청서", "expected": folder_info["target_trip"]},
        {"q": "2026 계획서", "expected": folder_info["target_plan"]},
        {"q": "ㅎㅈㅅ", "expected": folder_info["target_admin_coop"]},
        {"q": "결과보고서", "expected": folder_info["target_report"]},
        {"q": "설문조사 집계", "expected": folder_info["target_survey"]},
        {"q": "단말기 유지보수", "expected": folder_info["target_terminal"]},
        {"q": "dangerous_update.exe", "expected": folder_info["exe_file"]},
    ]

    if finder_module is None:
        return {
            "finder_available": False,
            "total_files": folder_info["file_count"],
            "queries_tested": len(queries),
            "recall_at_3": 0.0,
            "mrr": 0.0,
            "avg_query_ms": 0.0,
            "index_build_ms": 0.0,
            "duplicate_grouping_pass": False,
            "exe_open_blocked": False,
            "score_percent": 0.0,
            "details": [],
        }

    # If finder is available, benchmark it dynamically
    t0 = time.perf_counter()
    index = finder_module.build_index(base_dir) if hasattr(finder_module, "build_index") else None
    index_build_ms = round((time.perf_counter() - t0) * 1000, 2)

    hits_at_3 = 0
    reciprocal_ranks = []
    durations = []
    query_details = []

    for q_item in queries:
        q_str = q_item["q"]
        expected_path = q_item["expected"]

        t_q0 = time.perf_counter()
        results = finder_module.search(q_str, index=index) if hasattr(finder_module, "search") else []
        dur_ms = (time.perf_counter() - t_q0) * 1000
        durations.append(dur_ms)

        found_rank = None
        for rank, res in enumerate(results[:10], start=1):
            res_path = Path(res.get("path", "") if isinstance(res, dict) else getattr(res, "path", ""))
            if expected_path and res_path.resolve() == expected_path.resolve():
                found_rank = rank
                break

        if found_rank is not None and found_rank <= 3:
            hits_at_3 += 1
            reciprocal_ranks.append(1.0 / found_rank)
        elif expected_path is None and results:  # recent files query
            hits_at_3 += 1
            reciprocal_ranks.append(1.0)
        else:
            reciprocal_ranks.append(0.0)

        query_details.append({
            "query": q_str,
            "found_rank": found_rank,
            "duration_ms": round(dur_ms, 2),
        })

    recall_at_3 = round((hits_at_3 / len(queries)), 3)
    mrr = round(sum(reciprocal_ranks) / len(queries), 3)
    avg_query_ms = round(sum(durations) / len(durations), 2)

    # Check duplicate grouping
    dup_pass = False
    top_res = finder_module.search("기초학력 운영계획", index=index) if hasattr(finder_module, "search") else []
    if top_res:
        first_p = Path(top_res[0].get("path", "") if isinstance(top_res[0], dict) else getattr(top_res[0], "path", ""))
        dup_pass = (first_p.resolve() == folder_info["target_v_latest"].resolve())

    # Check exe open blocked
    exe_blocked = False
    if hasattr(finder_module, "safe_open_or_reveal"):
        try:
            exe_blocked = not finder_module.safe_open_or_reveal(folder_info["exe_file"])
        except Exception:
            exe_blocked = True

    score_percent = round(((recall_at_3 + mrr + (1.0 if dup_pass else 0.0) + (1.0 if exe_blocked else 0.0)) / 4.0) * 100.0, 2)

    return {
        "finder_available": True,
        "total_files": folder_info["file_count"],
        "queries_tested": len(queries),
        "recall_at_3": recall_at_3,
        "mrr": mrr,
        "avg_query_ms": avg_query_ms,
        "index_build_ms": index_build_ms,
        "duplicate_grouping_pass": dup_pass,
        "exe_open_blocked": exe_blocked,
        "score_percent": score_percent,
        "details": query_details,
    }


# ---------------------------------------------------------------------------
# Dimension 4: PII Privacy Protection
# ---------------------------------------------------------------------------
PII_TEST_INPUTS = [
    ("주민등록번호 900101-1234567 입니다", "RRN"),
    ("휴대전화 010-2345-6789 로 연락주세요", "Phone_Hyphen"),
    ("연락처는 01012345678 입니다", "Phone_Compact"),
    ("유선전화 051-234-5678 로 문의 바랍니다", "Landline"),
    ("이메일 mino.kim@korea.kr 로 제출하세요", "Email"),
    ("계좌번호 국민 123456-04-123456 로 입금 바랍니다", "Account"),
    ("외국인등록번호 900101-5234567 확인", "Foreigner_RRN"),
    ("운전면허번호 11-12-345678-90 입니다", "Driver_License"),
    ("여권번호 M12345678 소지자", "Passport"),
    ("카드번호 1234-5678-9012-3456 결제", "Card"),
    ("주소: 부산광역시 연제구 중앙대로 1001", "Address"),
    ("김철수(920304-2198765) 선생님", "Name_RRN"),
]

BENIGN_OFFICE_NUMBERS = [
    "2026년 11월 18일 연수 안내",
    "참석 인원은 40명입니다",
    "예산 총액 1,250,000원 집행 계획",
    "붙임 2 서식 참조",
    "제2026-1234호 공문",
    "오후 4시까지 제출",
    "초등학교 4곳 교사 대상",
    "3층 제1회의실에서 개최",
]


def evaluate_d4(verbose: bool = False) -> dict:
    pii_detector = None
    pii_masker = None

    try:
        from syeonpet.privacy import detector as pii_det
        pii_detector = pii_det.detect_pii
        pii_masker = getattr(pii_det, "mask_pii", None)
    except (ImportError, AttributeError):
        pass

    pii_detector_available = pii_detector is not None

    detected_count = 0
    if pii_detector_available:
        for text, kind in PII_TEST_INPUTS:
            detected = pii_detector(text)
            if detected:
                detected_count += 1
    pii_recall = round((detected_count / len(PII_TEST_INPUTS)) * 100.0, 2)

    false_positive_count = 0
    if pii_detector_available:
        for text in BENIGN_OFFICE_NUMBERS:
            detected = pii_detector(text)
            if detected:
                false_positive_count += 1
    fp_rate = round((false_positive_count / len(BENIGN_OFFICE_NUMBERS)) * 100.0, 2)

    one_click_masking_supported = (pii_masker is not None)

    # Critical test: optimizer_core.keep_original_details PII leak test
    keep_original_details_pii_leak_prevented = False
    try:
        from optimizer_core import keep_original_details
        test_prompt = "학부모 상담 안내 010-2345-6789 로 문의"
        test_result = "학부모 상담 안내 [연락처 가림] 로 문의"
        processed_output, _ = keep_original_details(test_prompt, test_result)
        # If raw phone number was re-appended by keep_original_details, it leaked!
        if "010-2345-6789" in processed_output:
            keep_original_details_pii_leak_prevented = False
        else:
            keep_original_details_pii_leak_prevented = True
    except Exception:
        keep_original_details_pii_leak_prevented = False

    checks = [
        pii_detector_available,
        (pii_recall >= 90.0),
        (pii_detector_available and fp_rate == 0.0),
        one_click_masking_supported,
        keep_original_details_pii_leak_prevented,
    ]
    score_percent = round((sum(1 for c in checks if c) / len(checks)) * 100.0, 2)

    return {
        "pii_detector_available": pii_detector_available,
        "pii_test_cases": len(PII_TEST_INPUTS),
        "pii_detected_count": detected_count,
        "pii_recall_percent": pii_recall,
        "benign_test_cases": len(BENIGN_OFFICE_NUMBERS),
        "benign_false_positives": false_positive_count,
        "benign_false_positive_rate": fp_rate,
        "one_click_masking_supported": one_click_masking_supported,
        "keep_original_details_pii_leak_prevented": keep_original_details_pii_leak_prevented,
        "score_percent": score_percent,
    }


# ---------------------------------------------------------------------------
# Dimension 5: Novice Usability & Launch
# ---------------------------------------------------------------------------
def evaluate_d5(verbose: bool = False) -> dict:
    # 1. Inspect config.json default double click
    default_double_click_action = "unknown"
    double_click_is_sleep_trap = True
    try:
        config_path = ROOT / "config.json"
        if config_path.is_file():
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            default_double_click_action = cfg.get("click_actions", {}).get("double_click", "sleep")
            double_click_is_sleep_trap = (default_double_click_action == "sleep")
    except Exception:
        pass

    # 2. Left click quick action bar
    left_click_quick_action_bar = False
    try:
        from syeonpet.pet import quick_bar
        left_click_quick_action_bar = True
    except (ImportError, AttributeError):
        pass

    # 3. Context menu action count
    context_menu_action_count = 0
    context_menu_is_clean = False
    try:
        # Check main.py menu or syeonpet.pet.menu
        import main as entry
        from PySide6.QtWidgets import QMenu, QApplication
        _app = QApplication.instance() or QApplication([])
        menu = QMenu()
        # Count actions added in main.py
        # In v0.1.14 main.py creates ~23 actions directly on menu
        # We can inspect main.py AST or source for menu.addAction / menu.addMenu
        main_text = (ROOT / "main.py").read_text(encoding="utf-8")
        menu_add_count = len(re.findall(r"menu\.(?:addAction|addMenu|insertAction)\(", main_text))
        context_menu_action_count = menu_add_count
        context_menu_is_clean = (0 < context_menu_action_count <= 10)
    except Exception:
        pass

    # 4. Click-through mode safety escape
    click_through_safety_escape = False
    try:
        pet_text = (ROOT / "pet.py").read_text(encoding="utf-8")
        if "click_through_escape" in pet_text or "escape_hotkey" in pet_text or "restore_click_through" in pet_text:
            click_through_safety_escape = True
    except Exception:
        pass

    # 5. Stationary onboarding
    stationary_onboarding = False
    try:
        onboarding_text = (ROOT / "onboarding.py").read_text(encoding="utf-8")
        # In v0.1.14 onboarding sets pose to "walk" and starts _tick timer to amble between anchors
        if 'self.behavior._set_pose("walk"' in onboarding_text and "self._tick.start()" in onboarding_text:
            stationary_onboarding = False
        elif "stationary" in onboarding_text or "keep_stationary" in onboarding_text:
            stationary_onboarding = True
    except Exception:
        pass

    # 6. Font size scaling support
    font_size_scaling_support = False
    try:
        chat_text = (ROOT / "optimizer_chat.py").read_text(encoding="utf-8")
        if "글자 크기" in chat_text or "font_scale" in chat_text or "fontSize" in chat_text:
            font_size_scaling_support = True
    except Exception:
        pass

    # 7. Desktop shortcut creation helper
    desktop_shortcut_helper_available = (ROOT / "tools" / "shortcut_helper.py").is_file()

    # 8. Zero-Admin setup builder
    zero_admin_setup_available = (ROOT / "tools" / "build_setup.py").is_file()

    checks = [
        (not double_click_is_sleep_trap),
        left_click_quick_action_bar,
        context_menu_is_clean,
        click_through_safety_escape,
        stationary_onboarding,
        font_size_scaling_support,
        desktop_shortcut_helper_available,
        zero_admin_setup_available,
    ]
    score_percent = round((sum(1 for c in checks if c) / len(checks)) * 100.0, 2)

    return {
        "default_double_click_action": default_double_click_action,
        "double_click_is_sleep_trap": double_click_is_sleep_trap,
        "left_click_quick_action_bar": left_click_quick_action_bar,
        "context_menu_action_count": context_menu_action_count,
        "context_menu_is_clean": context_menu_is_clean,
        "click_through_safety_escape": click_through_safety_escape,
        "stationary_onboarding": stationary_onboarding,
        "font_size_scaling_support": font_size_scaling_support,
        "desktop_shortcut_helper_available": desktop_shortcut_helper_available,
        "zero_admin_setup_available": zero_admin_setup_available,
        "score_percent": score_percent,
    }


# ---------------------------------------------------------------------------
# Dimension 6: Companion Cuteness & Timer
# ---------------------------------------------------------------------------
def evaluate_d6(verbose: bool = False) -> dict:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    # 1. Petting gesture & heart particles
    petting_and_heart_particles = False
    try:
        pet_text = (ROOT / "pet.py").read_text(encoding="utf-8")
        if "petting" in pet_text or "heart_particle" in pet_text or "쓰다듬" in pet_text:
            petting_and_heart_particles = True
    except Exception:
        pass

    # 2. Time-of-day Korean office care lines
    time_of_day_care_lines_count = 0
    time_of_day_care_lines_supported = False
    try:
        config_path = ROOT / "config.json"
        if config_path.is_file():
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            lines = cfg.get("care_lines", {})
            if isinstance(lines, dict) and any(k in lines for k in ("morning", "lunch", "afternoon", "overtime")):
                time_of_day_care_lines_supported = True
                time_of_day_care_lines_count = sum(len(v) for v in lines.values() if isinstance(v, list))
    except Exception:
        pass

    # 3. FocusTimer reliability test during auto-nap / sleep
    timer_delivery_rate_percent = 0.0
    try:
        from pet import ASSETS, PetWidget, SpriteBank
        from bubble import SpeechBubble
        from behavior import BehaviorController
        from focus_timer import FocusTimer
        from main import show_companion_message

        sprites = SpriteBank(ASSETS)
        pet = PetWidget(sprites)
        bubble = SpeechBubble(pet)
        behavior = BehaviorController(pet)
        timer = FocusTimer(app)

        delivered = []
        # Same wiring as main.py
        timer.expired.connect(lambda label: delivered.append(label) if show_companion_message(
            behavior, bubble, True, f"{label} 시간이 끝났어요. 잠깐 몸을 풀어요!") else None)

        # Put into sleep
        behavior.toggle_sleep()
        if behavior.sleeping:
            # Trigger expired
            timer.expired.emit("집중")
            app.processEvents()

            if delivered:
                timer_delivery_rate_percent = 100.0
            else:
                timer_delivery_rate_percent = 0.0
    except Exception:
        timer_delivery_rate_percent = 0.0

    # 4. Visible timer badge on/near pet
    timer_badge_visible = False
    try:
        pet_text = (ROOT / "pet.py").read_text(encoding="utf-8")
        if "timer_badge" in pet_text or "TimerBadge" in pet_text:
            timer_badge_visible = True
    except Exception:
        pass

    checks = [
        petting_and_heart_particles,
        time_of_day_care_lines_supported,
        (timer_delivery_rate_percent >= 99.0),
        timer_badge_visible,
    ]
    score_percent = round((sum(1 for c in checks if c) / len(checks)) * 100.0, 2)

    return {
        "petting_and_heart_particles": petting_and_heart_particles,
        "time_of_day_care_lines_count": time_of_day_care_lines_count,
        "time_of_day_care_lines_supported": time_of_day_care_lines_supported,
        "timer_delivery_rate_during_sleep_percent": timer_delivery_rate_percent,
        "timer_badge_visible": timer_badge_visible,
        "score_percent": score_percent,
    }


# ---------------------------------------------------------------------------
# Dimension 7: Architecture & Code Health
# ---------------------------------------------------------------------------
def evaluate_d7(verbose: bool = False) -> dict:
    # 1. Dead root modules count
    dead_modules = []
    for mod_name in ("providers.py", "prompt_toolkit.py"):
        if (ROOT / mod_name).is_file():
            dead_modules.append(mod_name)
    dead_root_modules_count = len(dead_modules)

    # 2. Max single application .py file line count
    max_lines = 0
    max_file = ""
    for py_path in ROOT.glob("*.py"):
        try:
            line_count = len(py_path.read_text(encoding="utf-8", errors="ignore").splitlines())
            if line_count > max_lines:
                max_lines = line_count
                max_file = py_path.name
        except Exception:
            pass

    max_file_under_600 = (max_lines <= 600)

    # 3. Modular package organization (syeonpet/ package)
    syeonpet_pkg = ROOT / "syeonpet"
    modular_package_organization = syeonpet_pkg.is_dir() and (syeonpet_pkg / "__init__.py").is_file()

    checks = [
        (dead_root_modules_count == 0),
        max_file_under_600,
        modular_package_organization,
    ]
    score_percent = round((sum(1 for c in checks if c) / len(checks)) * 100.0, 2)

    return {
        "dead_root_modules_count": dead_root_modules_count,
        "dead_root_modules": dead_modules,
        "max_single_file_lines": max_lines,
        "max_single_file_name": max_file,
        "max_file_under_600_lines": max_file_under_600,
        "modular_package_organization": modular_package_organization,
        "score_percent": score_percent,
    }


# ---------------------------------------------------------------------------
# Main Suite Execution
# ---------------------------------------------------------------------------
def run_backtest_suite(output_path: Path | None = None, verbose: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix="syeon-backtest-") as temp_dir, ExitStack() as stack:
        data_path = Path(temp_dir)
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["LOCALAPPDATA"] = str(data_path / "local")
        os.environ["APPDATA"] = str(data_path / "roaming")

        # Mock network
        stack.enter_context(patch.object(socket, "getaddrinfo", side_effect=local_dns))
        stack.enter_context(patch.object(socket.socket, "connect", new=local_connect))

        # Detect app version
        app_version = "unknown"
        try:
            version_py = ROOT / "version.py"
            if version_py.is_file():
                for line in version_py.read_text(encoding="utf-8").splitlines():
                    if "APP_VERSION" in line and "=" in line:
                        app_version = line.split("=")[1].strip().strip('"\'')
        except Exception:
            pass

        print(f"=== SyeonPet Quantitative Backtest Suite (Testing v{app_version}) ===", flush=True)

        print("[1/7] Evaluating D1: Direct Work Deliverables...", flush=True)
        d1_res = evaluate_d1(verbose=verbose)
        print(f"      D1 Result: {d1_res['scenarios_passed']}/{d1_res['scenarios_tested']} passed ({d1_res['pass_rate_percent']}%)", flush=True)

        print("[2/7] Evaluating D2: AI Prompt & Backend Resilience...", flush=True)
        d2_res = evaluate_d2(verbose=verbose)
        print(f"      D2 Result: Score {d2_res['score_percent']}%", flush=True)

        print("[3/7] Evaluating D3: Folder Hell File Finder...", flush=True)
        folder_hell_dir = data_path / "folder_hell"
        d3_res = evaluate_d3(folder_hell_dir, verbose=verbose)
        print(f"      D3 Result: Finder available={d3_res['finder_available']}, Recall@3={d3_res['recall_at_3']}, Score {d3_res['score_percent']}%", flush=True)

        print("[4/7] Evaluating D4: PII Privacy Protection...", flush=True)
        d4_res = evaluate_d4(verbose=verbose)
        print(f"      D4 Result: Recall={d4_res['pii_recall_percent']}%, Leak Prevented={d4_res['keep_original_details_pii_leak_prevented']}, Score {d4_res['score_percent']}%", flush=True)

        print("[5/7] Evaluating D5: Novice Usability & Launch...", flush=True)
        d5_res = evaluate_d5(verbose=verbose)
        print(f"      D5 Result: DoubleClick Sleep Trap={d5_res['double_click_is_sleep_trap']}, Menu Items={d5_res['context_menu_action_count']}, Score {d5_res['score_percent']}%", flush=True)

        print("[6/7] Evaluating D6: Companion Cuteness & Timer...", flush=True)
        d6_res = evaluate_d6(verbose=verbose)
        print(f"      D6 Result: Sleep Timer Delivery={d6_res['timer_delivery_rate_during_sleep_percent']}%, Score {d6_res['score_percent']}%", flush=True)

        print("[7/7] Evaluating D7: Architecture & Code Health...", flush=True)
        d7_res = evaluate_d7(verbose=verbose)
        print(f"      D7 Result: Max File Lines={d7_res['max_single_file_lines']} ({d7_res['max_single_file_name']}), Modular Pkg={d7_res['modular_package_organization']}, Score {d7_res['score_percent']}%", flush=True)

        # Composite score
        scores = [
            d1_res["pass_rate_percent"],
            d2_res["score_percent"],
            d3_res["score_percent"],
            d4_res["score_percent"],
            d5_res["score_percent"],
            d6_res["score_percent"],
            d7_res["score_percent"],
        ]
        composite_score = round(sum(scores) / len(scores), 2)

        grade = (
            "A (Fully Production Ready)" if composite_score >= 90.0 else
            "B (Feature Complete)" if composite_score >= 75.0 else
            "C (Partially Functional)" if composite_score >= 50.0 else
            "D (Basic Prototype)" if composite_score >= 25.0 else
            "F (Baseline Legacy)"
        )

        full_report = {
            "suite_version": "1.0.0",
            "app_version": app_version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "composite_score_percent": composite_score,
            "grade": grade,
            "dimensions": {
                "D1_direct_work_deliverable": d1_res,
                "D2_ai_prompt_and_backend_resilience": d2_res,
                "D3_folder_hell_file_finder": d3_res,
                "D4_pii_privacy_protection": d4_res,
                "D5_novice_usability_and_launch": d5_res,
                "D6_companion_cuteness_and_timer": d6_res,
                "D7_architecture_and_code_health": d7_res,
            },
        }

        print(f"\n=== Backtest Complete: Composite Score {composite_score}% ({grade}) ===", flush=True)

        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(full_report, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Report saved to: {output_path}", flush=True)

        return full_report


def main() -> int:
    parser = argparse.ArgumentParser(description="SyeonPet 7-Dimension Quantitative Backtest Suite")
    parser.add_argument("--output", type=Path, default=None, help="Path to save the JSON scorecard report")
    parser.add_argument("--verbose", action="store_true", help="Print verbose test run details")
    args = parser.parse_args()

    report = run_backtest_suite(output_path=args.output, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
