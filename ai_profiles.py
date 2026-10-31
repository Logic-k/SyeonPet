"""Nonsecret AI configuration, stored outside the application directory."""
from __future__ import annotations

from dataclasses import dataclass, fields
import json
import os
from pathlib import Path
import re
import tempfile
import threading


PROVIDERS = frozenset({
    "vireonix", "pollinations-classic", "chatgpt-subscription",
    "groq", "openrouter", "gemini", "ollama",
})


@dataclass(frozen=True)
class AIProfile:
    id: str
    provider: str
    model: str
    account_id: str | None = None
    free_tier_acknowledged: bool = False
    ollama_no_cloud_acknowledged: bool = False
    revision: int = 0


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str


def validate_profile(profile: AIProfile) -> None:
    """Validate configuration only; acknowledgments do not prove billing status."""
    if not isinstance(profile, AIProfile):
        raise ValueError("Invalid AI profile.")
    for value in (profile.id, profile.model):
        if (not isinstance(value, str) or not value.strip()
                or len(value) > 256 or any(ord(c) < 32 for c in value)):
            raise ValueError("Invalid profile or model identifier.")
    if not isinstance(profile.provider, str) or profile.provider not in PROVIDERS:
        raise ValueError("Unknown AI provider.")
    if not re.fullmatch(r"[A-Za-z0-9_./:@+-]+", profile.model):
        raise ValueError("Invalid model identifier.")
    if (profile.account_id is not None and
            (not isinstance(profile.account_id, str) or not profile.account_id.strip()
             or len(profile.account_id) > 256
             or any(ord(c) < 32 for c in profile.account_id))):
        raise ValueError("Invalid account identifier.")
    if (type(profile.revision) is not int or profile.revision < 0
            or type(profile.free_tier_acknowledged) is not bool
            or type(profile.ollama_no_cloud_acknowledged) is not bool):
        raise ValueError("Invalid profile settings.")
    if profile.provider in ("groq", "gemini") and not profile.free_tier_acknowledged:
        raise ValueError("Explicit free-tier acknowledgment is required.")
    if profile.provider == "openrouter" and (
            not profile.model.endswith(":free") or "/" not in profile.model):
        raise ValueError("OpenRouter requires an explicit :free model.")
    if profile.provider == "ollama":
        if not profile.ollama_no_cloud_acknowledged:
            raise ValueError("Ollama no-cloud server acknowledgment is required.")
        if ":cloud" in profile.model.lower() or "-cloud" in profile.model.lower():
            raise ValueError("Remote Ollama models are not allowed.")
    if profile.provider == "gemini" and not re.fullmatch(r"[A-Za-z0-9_.-]+", profile.model):
        raise ValueError("Use a Gemini model slug without a path.")


def _path(path: str | os.PathLike | None) -> Path:
    if path is not None:
        return Path(path)
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise ValueError("LOCALAPPDATA is unavailable.")
    return Path(root) / "SyeonPet" / "ai_profiles.json"


def load_profiles(path: str | os.PathLike | None = None) -> list[AIProfile]:
    """Missing configuration starts on ChatGPT subscription, never a vendor API."""
    target = _path(path)
    try:
        with target.open("rb") as stream:
            raw = stream.read(1_000_001)
    except FileNotFoundError:
        return [AIProfile("default", "chatgpt-subscription", "auto")]
    except OSError:
        raise ValueError("Could not read AI profiles.") from None
    try:
        if len(raw) > 1_000_000:
            raise ValueError
        data = json.loads(raw)
        if (not isinstance(data, dict) or set(data) != {"version", "profiles"}
                or type(data["version"]) is not int or data["version"] != 1
                or not isinstance(data["profiles"], list)):
            raise ValueError
        profiles = []
        allowed = {field.name for field in fields(AIProfile)}
        for item in data["profiles"]:
            if not isinstance(item, dict) or set(item) - allowed:
                raise ValueError
            profile = AIProfile(**item)
            validate_profile(profile)
            profiles.append(profile)
        if len({p.id for p in profiles}) != len(profiles):
            raise ValueError
        return profiles
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("Invalid AI profile file.") from None


def save_profiles(profiles: list[AIProfile],
                  path: str | os.PathLike | None = None) -> None:
    """Atomically replace an allowlisted nonsecret JSON document."""
    names = [field.name for field in fields(AIProfile)]
    records = []
    for profile in profiles:
        validate_profile(profile)
        records.append({name: getattr(profile, name) for name in names})
    if len({p.id for p in profiles}) != len(profiles):
        raise ValueError("Duplicate AI profile identifiers.")
    body = json.dumps({"version": 1, "profiles": records}, ensure_ascii=False).encode("utf-8")
    if len(body) > 1_000_000:
        raise ValueError("AI profile file is too large.")
    target = _path(path)
    temporary = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".ai_profiles-",
                                         suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except OSError:
        raise ValueError("Could not save AI profiles.") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def list_models(profile: AIProfile,
                cancel: threading.Event | None = None) -> list[ModelInfo]:
    from ai_backends import list_models as discover
    return discover(profile, cancel=cancel)
