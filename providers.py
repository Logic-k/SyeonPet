"""Chat backends for the pet. Stdlib-only (urllib) — no extra packages.

Providers:
  canned         — offline keyword responses (always available)
  gemini         — Google AI Studio free key → generativelanguage REST
  openai_compat  — Groq / OpenRouter / OpenAI / any /chat/completions endpoint
"""
from __future__ import annotations

import json
import random
import urllib.error
import urllib.request

DEFAULT_PERSONA = (
    "너는 부산광역시교육청 마스코트 '션'이야. 초록 잎사귀 머리를 한 귀여운 "
    "하얀 새/오리 친구지. 사용자의 바탕화면 위에서 살아. 한두 문장으로 짧고 "
    "다정하게, 존댓말로 답해. 가끔 이모티콘 하나 정도는 괜찮아. 너는 AI "
    "어시스턴트라기보다 귀여운 펫이야 — 긴 설명보다 따뜻한 리액션을 해줘."
)

_TIMEOUT = 20


def _post(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


class CannedBrain:
    """No-key fallback — keyword-matched mascot replies."""
    name = "canned"

    _RULES = [
        (("안녕", "하이", "헬로", "hello", "hi"),
         ["안녕하세요! 션이에요 🌱", "만나서 반가워요!"]),
        (("이름", "누구", "누구야", "뭐야"),
         ["저는 부산교육청 마스코트 '션'이에요. 바탕화면에서 함께할게요!"]),
        (("밥", "배고", "먹", "점심", "저녁"),
         ["맛있는 거 드셨어요? 저는 잎사귀 하나면 충전돼요!"]),
        (("날씨", "비", "눈", "춥", "덥"),
         ["창밖은 잘 못 보지만… 부산 하늘은 대체로 맑다고 들었어요!"]),
        (("공부", "학교", "시험", "숙제", "교육"),
         ["부산교육을 응원해요! 오늘도 파이팅 📚"]),
        (("사랑", "좋아", "귀여", "예쁘"),
         ["헤헤… 저도 주인님이 좋아요!"]),
        (("심심", "지루", "놀아"),
         ["그럼 저랑 놀아요! 더블클릭하면 춤도 춰요 💃"]),
        (("졸려", "잠", "자고", "피곤"),
         ["저도 살며시 졸리는데… 같이 쉬어요 💤"]),
        (("고마", "감사", "수고"),
         ["천만에요! 도움이 됐다면 기뻐요 ☺️"]),
        (("춤", "노래", "댄스"),
         ["트레이 메뉴 '춤추기' 눌러보세요 — 바로 보여드릴게요!"]),
        (("점프", "뛰"),
         ["점프는 제 자랑이에요! 트레이 '점프하기'로 언제든 볼 수 있어요"]),
    ]
    _FALLBACK = [
        "음… 잘 모르겠지만 옆에서 응원할게요!",
        "흥미로운 얘기네요! 조금 더 들려주세요",
        "오~ 그렇구나! 션은 콩닥콩닥해요",
        "좋은 하루 보내고 계신 거죠? 전 여기 있어요",
        "config.json에 AI 키를 넣으면 제가 더 똑똑하게 대답할 수 있어요!",
    ]

    def reply(self, history: list, text: str, persona: str) -> str:
        t = text.lower()
        for keys, answers in self._RULES:
            if any(k in t for k in keys):
                return random.choice(answers)
        return random.choice(self._FALLBACK)


class GeminiBrain:
    """Google AI Studio REST — generous free tier, key at aistudio.google.com."""
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model

    def reply(self, history: list, text: str, persona: str) -> str:
        contents = [{"role": ("model" if h["role"] == "assistant" else "user"),
                     "parts": [{"text": h["text"]}]} for h in history[-10:]]
        contents.append({"role": "user", "parts": [{"text": text}]})
        body = {"system_instruction": {"parts": [{"text": persona}]},
                "contents": contents}
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent?key={self.api_key}")
        data = _post(url, body, {})
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()


class OpenAICompatBrain:
    """Groq / OpenRouter / OpenAI — POST {base}/chat/completions."""
    name = "openai_compat"

    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://api.groq.com/openai/v1"):
        self.api_key, self.model, self.base_url = api_key, model, base_url

    def reply(self, history: list, text: str, persona: str) -> str:
        msgs = [{"role": "system", "content": persona}]
        msgs += [{"role": h["role"], "content": h["text"]} for h in history[-10:]]
        msgs.append({"role": "user", "content": text})
        data = _post(f"{self.base_url}/chat/completions",
                     {"model": self.model, "messages": msgs,
                      "max_tokens": 300, "temperature": 0.8},
                     {"Authorization": f"Bearer {self.api_key}"})
        return data["choices"][0]["message"]["content"].strip()


def make_brain(cfg: dict):
    """Pick a backend from config.chat; canned when no key is set."""
    c = cfg.get("chat", {}) or {}
    provider = c.get("provider", "canned")
    key = (c.get("api_key") or "").strip()
    if provider == "gemini" and key:
        return GeminiBrain(key, c.get("model", "gemini-2.5-flash"))
    if provider == "openai_compat" and key:
        return OpenAICompatBrain(
            key, c.get("model", "llama-3.3-70b-versatile"),
            c.get("base_url", "https://api.groq.com/openai/v1"))
    return CannedBrain()
