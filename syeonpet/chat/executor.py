"""Execution routines for model generation, comparisons, and profile resolution."""
from __future__ import annotations

import sys
import threading
import time

from optimizer_ai import fixed_model, optimize_ai
from optimizer_core import create_offline_prompt, utf16_length, validate_input
from syeonpet.chat.error_sanitizer import sanitize_error as _safe_generation_error
from syeonpet.chat.state_codec import _append_conditions


def reload_profiles(widget) -> None:
    from ai_profiles import load_profiles
    sel = widget.provider.currentData()
    for idx in range(widget.provider.count() - 1, -1, -1):
        if str(widget.provider.itemData(idx)).startswith("profile:"):
            widget.provider.removeItem(idx)
    try:
        profiles = load_profiles()
    except (OSError, ValueError):
        # A damaged ai_profiles.json must not crash the chat window; keep the
        # built-in destinations and let _provider_changed show the notice.
        widget._profiles_unreadable = True
        profiles = []
    else:
        widget._profiles_unreadable = False
    for prof in profiles:
        widget.provider.addItem(f"{prof.provider} / {prof.model}", "profile:" + prof.id)
    widget.provider.setCurrentIndex(max(0, widget.provider.findData(sel)))


def resolve_profile(destination: str):
    if not str(destination).startswith("profile:"):
        return None
    from ai_profiles import load_profiles, validate_profile
    prof = next((p for p in load_profiles() if "profile:" + p.id == destination), None)
    if prof is None:
        raise ValueError("선택한 AI 프로필을 찾을 수 없습니다.")
    validate_profile(prof)
    return prof


def destination_label(destination: str, widget=None) -> str:
    prof = resolve_profile(destination)
    if prof:
        loc = "로컬" if prof.provider == "ollama" else "외부"
        return f"{prof.provider} / {prof.model} ({loc})"
    if destination in _FIXED_NAMES:
        return f"{_FIXED_NAMES[destination]} / {fixed_model(destination)}"
    return {"offline": "기본 정리 / local (로컬)"}.get(destination, destination)


_FIXED_NAMES = {"vireonix": "Vireonix", "pollinations-classic": "Pollinations Classic",
                "jarvisclaw": "JarvisClaw", "kilo": "Kilo", "ovhcloud": "OVHcloud"}


def _metadata_model(destination: str) -> str:
    return "local" if destination == "offline" else fixed_model(destination)


def snapshot_profiles() -> tuple:
    from ai_profiles import load_profiles
    from ai_secrets import get_secret
    import chatgpt_auth
    profs = tuple(load_profiles())
    revs = []
    for prof in profs:
        cred = None
        if prof.account_id:
            acc = chatgpt_auth.account_status(prof.account_id)
            cred = (acc.connected, acc.credential_revision)
        elif prof.provider not in ("ollama", "chatgpt-subscription"):
            cred = hash(repr(get_secret("provider:" + prof.provider)))
        revs.append((prof, cred))
    return tuple(revs)


def build_generation_input(widget, text: str) -> tuple:
    orig = widget.original or text
    rev = text if widget.original else ""
    prev = widget.result.toPlainText() if widget.original else ""
    old_ctx = "\n\n".join(filter(None, [widget._clarifications, *widget.revisions]))
    next_ctx = "\n\n".join(filter(None, [old_ctx, rev]))
    cnt = (widget._result_revision_counts[widget._shown_result]
           if 0 <= widget._shown_result < len(widget._result_revision_counts) else 0)
    pend = [*widget.revisions[cnt:], rev] if rev else []
    return (text, orig, rev, old_ctx, next_ctx, prev, widget.task.currentData(), widget.detail.currentData(), pend)


def check_over_limit(original: str, revision: str, context: str, previous: str, provider: str) -> str | None:
    if utf16_length(original) > 30000:
        return "원래 요청이 30,000자를 넘어 정리할 수 없어요. 요청을 나누거나 일부를 지운 뒤 새 요청으로 시작해 주세요."
    if utf16_length(context) > 2000:
        return "추가 조건이 2,000자를 넘어 정리할 수 없어요. 조건 확인에서 일부를 지우거나 새 요청으로 나눠 주세요."
    if provider == "offline":
        return None
    if utf16_length(revision) > 2000:
        return "이번 수정 요청이 2,000자를 넘어 정리할 수 없어요. 요청을 나누거나 일부를 지운 뒤 다시 보내 주세요."
    if utf16_length(previous) > 30000:
        return "이전 결과가 30,000자를 넘어 정리할 수 없어요. 결과를 줄이거나 새 요청으로 시작해 주세요."
    return None


def run_generate(frozen: tuple, destination: str, profile=None) -> dict:
    text, original, revision, old_ctx, next_ctx, previous, task, detail, pending = frozen
    started = time.monotonic()
    mod = sys.modules.get("optimizer_chat")
    fn_off = getattr(mod, "create_offline_prompt", create_offline_prompt)
    fn_ai = getattr(mod, "optimize_ai", optimize_ai)
    if destination == "offline":
        if revision:
            validate_input(original, next_ctx, task, detail)
            if not previous.strip():
                raise ValueError("이전 정리 결과가 비어 있습니다.")
            reply = {"result": _append_conditions(previous, pending), "notes": [], "mode": "offline"}
        else:
            reply = fn_off(original, context=next_ctx, task=task, detail=detail)
    else:
        kw = dict(context=old_ctx, provider=profile.provider if profile else destination,
                  revision=revision, previous_result=previous, task=task, detail=detail)
        if profile is not None:
            kw["profile"] = profile
        reply = fn_ai(original, **kw)
    reply = dict(reply)
    reply["_metadata"] = {
        "provider": profile.provider if profile else destination,
        "model": profile.model if profile else _metadata_model(destination),
        "elapsed": max(0.0, time.monotonic() - started),
    }
    return reply


def start_comparison(widget, expected: tuple, frozen: tuple, destinations: list, profiles: list) -> None:
    if widget._busy or widget._sleep_paused or expected != widget._request_snapshot():
        widget._show_notice("입력이나 설정이 바뀌었습니다. 비교 요청을 다시 확인하세요.")
        return
    widget._comparison_active = True
    widget._comparison_frozen = frozen
    widget._comparison_destinations = destinations
    widget._pending = (frozen[0], frozen[1], frozen[2], destinations[0])
    widget._pending_review_input = (frozen[1], frozen[4])
    widget._set_busy(True)
    widget.status.setText("두 모델을 순서대로 요청하고 있어요. 응답에 1분 이상 걸릴 수 있습니다. 입력과 이전 결과는 보존됩니다.")

    def work():
        for index, (destination, profile) in enumerate(zip(destinations, profiles)):
            try:
                if widget._profile_snapshot() != expected[-1]:
                    raise ValueError("AI 프로필이나 인증 정보가 바뀌었습니다. 비교를 다시 확인하세요.")
                reply = widget._generate(frozen, destination, profile)
            except Exception as exc:
                widget._comparison_signals.completed.emit(("error", index, _safe_generation_error(exc)))
                return
            widget._comparison_signals.completed.emit(("result", index, reply))
        widget._comparison_signals.completed.emit(("done", 1, None))

    try:
        import optimizer_chat
        optimizer_chat.threading.Thread(target=work, daemon=True, name="prompt-comparison").start()
    except RuntimeError:
        widget._comparison_active = False
        widget._fail("정리 작업을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.")


def handle_comparison_event(widget, event: tuple) -> None:
    kind, index, value = event
    if kind == "result":
        frozen = widget._comparison_frozen
        widget._comparison_index = index
        widget._pending = (frozen[0], frozen[1], frozen[2] if index == 0 else "",
                           widget._comparison_destinations[index])
        widget._finish(value)
        if index == 0:
            first_status = widget.status.text()
            widget.status.setText("첫 결과를 받았어요. 두 번째 모델의 응답을 기다리는 중입니다. "
                                "응답에 1분 이상 걸릴 수 있습니다.\n" + first_status)
    else:
        widget._comparison_active = False
        if kind == "error":
            widget._pending = (*widget._pending[:3], widget._comparison_destinations[index])
            widget._fail(value)
            review = widget._selected_review()
            if review:
                widget.status.setText(widget.status.text() + "\n현재 선택한 결과 확인:\n" + "\n".join(review))
        else:
            widget._set_busy(False)


def submit_confirmed(widget, text, original, revision, provider, old_ctx, next_ctx, previous, task, detail, pending, profile=None) -> None:
    if widget._sleep_paused:
        widget.status.setText("션이 완전히 자고 있어요. 깨운 후 다시 보내 주세요. 입력은 그대로 남아 있습니다.")
        widget._show_notice("션을 깨운 후 다시 보내 주세요. 입력은 그대로 남아 있습니다.")
        return
    if widget._busy:
        widget.status.setText("이미 정리 중이에요. 입력은 그대로 남아 있습니다.")
        widget._show_notice("이미 정리 중이에요. 입력은 그대로 남아 있습니다.")
        return
    widget._pending = (text, original, revision, provider)
    widget._pending_review_input = (original, next_ctx)
    widget.inline_notice.hide()
    widget._hide_other_ai()
    widget._set_busy(True)
    if provider == "offline":
        widget.status.setText("기본 정리 중…")
    else:
        widget.status.setText(f"{widget.provider.currentText()} 응답을 기다리는 중입니다. 서비스에 따라 1분 이상 걸릴 수 있어요. 시간이 초과되면 입력과 이전 결과를 보존합니다.")

    def work():
        try:
            started = time.monotonic()
            mod = sys.modules.get("optimizer_chat")
            fn_off = getattr(mod, "create_offline_prompt", create_offline_prompt)
            fn_ai = getattr(mod, "optimize_ai", optimize_ai)
            if provider == "offline":
                if revision:
                    validate_input(original, next_ctx, task, detail)
                    if not previous.strip():
                        raise ValueError("이전 정리 결과가 비어 있어 추가 조건을 붙일 수 없습니다. 이전 결과를 선택하거나 새 요청을 시작해 주세요.")
                    reply = {"result": _append_conditions(previous, pending),
                             "notes": ["수정한 이전 결과를 유지하고 추가 조건을 그대로 붙였습니다. AI 재작성은 하지 않았습니다."],
                             "mode": "offline"}
                else:
                    reply = fn_off(original, context=next_ctx, task=task, detail=detail)
            else:
                kw = dict(context=old_ctx, provider=profile.provider if profile else provider,
                          revision=revision, previous_result=previous, task=task, detail=detail)
                if profile is not None:
                    kw["profile"] = profile
                reply = fn_ai(original, **kw)
            reply = dict(reply)
            reply["_metadata"] = {
                "provider": profile.provider if profile else provider,
                        "model": profile.model if profile else _metadata_model(provider),
                "elapsed": max(0.0, time.monotonic() - started),
            }
            try:
                widget._signals.completed.emit(reply)
            except RuntimeError:
                pass
        except Exception as exc:
            try:
                widget._signals.failed.emit(_safe_generation_error(exc))
            except RuntimeError:
                pass

    try:
        import optimizer_chat
        optimizer_chat.threading.Thread(target=work, daemon=True, name="prompt-optimizer").start()
    except RuntimeError:
        widget._fail("정리 작업을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.")
