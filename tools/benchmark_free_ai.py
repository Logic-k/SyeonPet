"""Opt-in live benchmark with only fixed, fictional education-office requests.

This calls the same optimizer as the UI; output is synthetic but may be sent to
the selected third party. Literal checks are triage, not semantic grading. A
human must inspect every returned prompt, including extra and altered details.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import optimizer_ai
from optimizer_ai import OptimizationError, optimize_ai
from optimizer_core import KEEP_MATERIAL_HEADING, KEEP_ORIGINAL_HEADING


# All names, dates, numbers and materials below are invented test fixtures.
CASES = (
    ("연수 안내", "가상 연수 안내문 작성을 요청해 줘. 연수일은 11월 18일, 신청 마감은 11월 10일이야. 초등학교 4곳의 교사 각 1명 대상이고 학부모는 제외해. 장소와 준비물은 미정으로 표시해.", ("11월 18일", "11월 10일", "4곳", "1명")),
    ("회의록 정리", "가상 회의록 정리 요청을 만들어 줘. 11월 3일 학교 3곳 회의 메모를 결정 사항과 미결 사항으로 나눠 줘. 결정을 지어내지 말고 담당자 이름은 넣지 마.\n자료: 일정은 11월 18일로 확정. 장소는 미정. 예산은 논의 중.", ("11월 3일", "3곳", "11월 18일")),
    ("문의 회신", "가상의 학교 2곳이 12월 1일 시설 점검 일정에 대해 문의했어. 접수일은 11월 3일이고 담당 부서와 처리 기한은 미확인이야. 확인된 사실과 확인 필요 사항을 나눠 답변 초안을 요청해 줘. 법령과 처리 기한은 지어내지 마.", ("2곳", "12월 1일", "11월 3일")),
    ("시설 학교지원", "가상 학교 4곳의 교실 냉방기 점검 요청을 정리해 줘. 접수일은 11월 3일, 희망 회신은 11월 10일이야. 예산과 담당 부서는 미확인이고 수리 완료라고 쓰지 마.", ("4곳", "11월 3일", "11월 10일")),
    ("기초학력 수업", "초등 3학년을 위한 가상 읽기 이해 활동 20분짜리 구성 요청을 만들어 줘. 모둠은 3개이고 활동지는 제공하지 않았어. 학생을 진단하거나 수준을 판정하지 말고 교사용 초안으로 만들어 줘.", ("3학년", "20분", "3개")),
    ("공개자료 요약", "아래 가상 자료만 이용해 학교 지원 현황을 표로 정리하는 요청을 만들어 줘. 지원 완료 7건, 진행 중 4건, 미확인 2건이야. 원인을 추측하지 말고 자료에 없는 연도와 기관명을 쓰지 마.\n자료: 지원 완료 7건 / 진행 중 4건 / 미확인 2건.", ("7건", "4건", "2건")),
)

# Direction and known/unknown status require human review; these checks are
# deliberately not a machine-graded accuracy score.
ROLE_CASES = (
    ("학교가 문의", "가상 학교 2곳이 교육지원청에 12월 1일 시설 점검 일정에 대해 문의했어. 교육지원청 담당자가 학교에 보낼 회신 초안을 위한 요청을 정리해 줘. 점검일과 처리 기한은 확인되지 않았어.", ("2곳", "12월 1일")),
    ("교육지원청이 문의", "가상 교육지원청 담당자가 학교 3곳에 연수 참석 가능 인원을 11월 7일까지 알려 달라고 문의할 예정이야. 교육지원청에서 학교에 보낼 문의문 작성 요청을 정리해 줘. 참석 인원은 아직 몰라.", ("3곳", "11월 7일")),
    ("학부모가 문의", "가상 학부모가 교육지원청에 통학 지원 신청 방법을 문의했어. 교육지원청 담당자가 그 학부모에게 보낼 답변 초안 요청을 만들어 줘. 자격과 신청 기한은 확인 전이므로 지어내지 마.", ()),
    ("발신자 미정", "가상의 학교에서 교육지원청에 시설 점검에 관해 문의했어. 답변 문안을 작성해 줘. 답변을 누가 보내는지는 알려지지 않았고 날짜도 미정이야.", ()),
    ("문의자 직접 작성", "가상 학교 담당자가 교육지원청에 시설 점검 일정을 문의하는 글을 작성하려 해. 학교 담당자가 문의자이며 그 문의문 작성자야. 교육지원청으로 보내는 문의문 초안을 요청해 줘. 날짜는 미확인이야.", ()),
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def run(provider: str, cases: tuple, output) -> list[dict]:
    records = []
    last_poll_start = None
    for title, prompt, literals in cases:
        if provider == "pollinations-classic" and last_poll_start is not None:
            wait = max(0.0, 30.1 - (time.monotonic() - last_poll_start))
            if wait:
                time.sleep(wait)
        started = time.monotonic()
        if provider == "pollinations-classic":
            last_poll_start = started
        # Observe only the completion-state metadata, without changing the
        # product parser or recording headers and raw response bodies.
        metadata = {"finish_reason": "not_received"}
        original_read = optimizer_ai._read_result

        def inspect_response(response, deadline=None):
            captured = bytearray()

            class Reader:
                def read1(self, amount):
                    data = response.read1(amount)
                    captured.extend(data)
                    return data

            try:
                return original_read(Reader(), deadline)
            finally:
                try:
                    choice = json.loads(captured.decode("utf-8"))["choices"][0]
                    reason = choice.get("finish_reason", "missing")
                    if reason is None:
                        reason = "null"
                    metadata["finish_reason"] = reason if reason in (
                        "stop", "length", "content_filter", "tool_calls", "missing", "null") else "other"
                except (ValueError, UnicodeError, KeyError, IndexError, TypeError, AttributeError):
                    metadata["finish_reason"] = "unreadable"
        try:
            with patch.object(optimizer_ai, "_read_result", inspect_response):
                reply = optimize_ai(prompt, provider=provider)
            text = reply["result"]
            # The app can append the original on condition loss. Keep both
            # outputs for manual review and don't call that a model pass.
            generated = text
            for heading in (KEEP_ORIGINAL_HEADING, KEEP_MATERIAL_HEADING):
                generated = generated.split("\n\n" + heading + "\n", 1)[0]
            missing = [item for item in literals if _normalize(item) not in _normalize(generated)]
            record = {"case": title, "provider": provider, "status": "success",
                      "seconds": round(time.monotonic() - started, 2),
                      "finish_reason": metadata["finish_reason"],
                      "literal_checks_missing_before_restoration": missing,
                      "app_reattached_source": generated != text,
                      "prompt": prompt, "result": text, "notes": reply["notes"]}
        except OptimizationError as exc:
            record = {"case": title, "provider": provider, "status": "error",
                      "seconds": round(time.monotonic() - started, 2),
                      "finish_reason": metadata["finish_reason"],
                      "error": str(exc), "prompt": prompt}
        records.append(record)
        output.write(json.dumps(record, ensure_ascii=False) + "\n")
        output.flush()
        print(json.dumps({key: record[key] for key in
                          ("case", "provider", "status", "seconds")}, ensure_ascii=False), flush=True)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send fictional requests to the external provider")
    parser.add_argument("--provider", choices=("jarvisclaw", "pollinations-classic"), required=True)
    parser.add_argument("--limit", type=int, default=len(CASES), choices=range(1, len(CASES) + 1))
    parser.add_argument("--case", action="append", choices=[case[0] for case in CASES],
                        help="Run selected fictional cases (repeatable); by default runs all")
    parser.add_argument("--role-cases", action="store_true",
                        help="Run fictional inquiry/response direction cases; manually review all outputs")
    parser.add_argument("--role-case", action="append", choices=[case[0] for case in ROLE_CASES],
                        help="Select fictional role cases (repeatable; requires --role-cases)")
    parser.add_argument("--output", type=Path, required=True, help="New JSONL file; existing files are never replaced")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required because requests are sent to an external service")
    if args.role_cases and args.case:
        parser.error("--role-cases and --case cannot be combined")
    if args.role_case and not args.role_cases:
        parser.error("--role-case requires --role-cases")
    # Claim the output file before any request, without overwriting user data.
    try:
        with args.output.open("x", encoding="utf-8") as output:
            cases = (tuple(case for case in ROLE_CASES if not args.role_case or case[0] in args.role_case)
                     if args.role_cases else tuple(case for case in CASES if not args.case or case[0] in args.case))
            run(args.provider, cases[:args.limit], output)
    except FileExistsError:
        parser.error("output already exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
