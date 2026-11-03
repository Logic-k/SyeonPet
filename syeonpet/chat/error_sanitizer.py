"""Error Sanitizer for SyeonPet Chat & Direct Work.

Translates technical backend and provider exceptions into safe, helpful,
actionable Korean guidance without exposing private connection URLs or tokens.
"""
from __future__ import annotations

import re

_KNOWN_KOREAN = {
    "원래 요청을 입력해 주세요.",
    "원래 요청은 30,000자 이내로 입력해 주세요.",
    "원래 요청은 1~30,000자 이내로 입력해 주세요.",
    "맥락은 2,000자 이내의 텍스트로 입력해 주세요.",
    "맥락은 2,000자 이내로 입력해 주세요.",
    "수정 요청은 2,000자 이내로 입력해 주세요.",
    "이전 결과는 30,000자 이내여야 합니다.",
    "작업 유형 또는 상세 수준을 확인해 주세요.",
    "작업 유형을 확인해 주세요.",
    "상세 수준을 확인해 주세요.",
    "이전 정리 결과가 비어 있습니다.",
    "AI 프로필이나 인증 정보가 바뀌었습니다. 비교를 다시 확인하세요.",
    "설정한 AI 정리 방식을 선택해 주세요.",
    "모델 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
    "모델 응답이 너무 큽니다.",
    "AI 응답이 길이 제한으로 중간에 잘렸습니다. 요청을 줄여 다시 시도해 주세요.",
    "AI 서비스의 콘텐츠 필터로 응답이 제한되었습니다.",
    "모델 응답이 비어 있습니다.",
    "모델 응답을 읽을 수 없습니다. 잠시 후 다시 시도해 주세요.",
    "AI 서비스 주소 확인이 진행 중입니다. 잠시 후 다시 시도해 주세요.",
    "AI 서비스 주소 확인 시간이 초과되었습니다. 인터넷 연결과 DNS 설정을 확인하고 다시 시도해 주세요.",
    "AI 서비스 주소를 확인하지 못했습니다. 인터넷 연결과 DNS 설정을 확인하고 다시 시도해 주세요.",
    "사설망 또는 확인할 수 없는 AI 서비스 주소는 사용할 수 없습니다.",
    "AI 서비스 주소를 확인하지 못했습니다.",
    "AI 서비스 주소를 확인해 주세요.",
    "AI 서비스에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요.",
    "정리 요청을 취소했습니다.",
    "선택한 AI의 연결 설정을 다시 확인해 주세요.",
    "기존 정리 방식에는 별도 AI 연결 설정을 사용하지 않습니다.",
    "Pollinations Classic 요청이 진행 중입니다. 완료 후 다시 시도해 주세요.",
    "서비스 사용량 제한에 도달했습니다 (HTTP 429). Vireonix는 같은 인터넷 연결을 쓰는 사용자들이 시간당 한도를 함께 씁니다. 잠시 후 다시 시도해 주세요.",
    "서비스 사용량 제한에 도달했습니다 (HTTP 429). 잠시 후 다시 시도해 주세요.",
    "Pollinations Classic의 요청 간격이나 이용 한도 문제일 수 있습니다 (HTTP 402).",
    "JarvisClaw 무료 요청을 처리할 수 없습니다 (HTTP 402). 잠시 후 다시 시도해 주세요.",
    "ChatGPT 요청이 취소되었습니다.",
    "ChatGPT 계정 연결을 확인하고 필요하면 다시 연결해 주세요.",
    "ChatGPT 사용이 현재 제한되어 있습니다. 잠시 후 다시 확인해 주세요.",
    "ChatGPT 연결에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 시도해 주세요.",
    "ChatGPT 응답을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.",
}

_SAFE_DEFAULT = "연결 실패 또는 정리 중 오류가 발생했습니다. 설정과 인터넷 연결을 확인하고 다시 시도해 주세요."


def sanitize_error(exc: Exception | str) -> str:
    """Show only known fixed errors; provider exceptions can contain private data."""
    message = str(exc)
    name = type(exc).__name__ if isinstance(exc, Exception) else ""
    if name == "BackendError" or "BackendError" in str(type(exc)):
        low = message.lower()
        if "api key" in low or "authentication" in low or "unauthorized" in low or "401" in low:
            return "AI 설정에서 API 키 또는 계정 인증 정보를 확인해 주세요."
        if "chatgpt" in low:
            return "ChatGPT 계정 연결을 확인하고 필요하면 다시 연결해 주세요."
        if "429" in message or "rate limit" in low:
            return "서비스 사용량 제한에 도달했습니다 (HTTP 429). 잠시 후 다시 시도해 주세요."
        if "blocked" in low or "safety" in low or "filter" in low:
            return "AI 서비스의 콘텐츠 필터로 응답이 제한되었습니다."
        return _SAFE_DEFAULT
    if message in _KNOWN_KOREAN:
        return message
    if re.fullmatch(r"AI 서비스가 일시적으로 응답하지 않습니다 \(HTTP 50[234]\)\. 잠시 후 다시 시도해 주세요\.", message):
        return message
    return _SAFE_DEFAULT


_safe_generation_error = sanitize_error
