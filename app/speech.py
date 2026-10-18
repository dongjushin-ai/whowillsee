"""음성 인식(받아쓰기) 연결. 분석 AI 와 마찬가지로 사용하는 사람이 직접 연결한다.

OpenAI 호환 `POST {base_url}/audio/transcriptions` 를 쓰는 모든 서버를 지원한다
(OpenAI Whisper, Groq, 로컬 Whisper 서버 등).
"""
from __future__ import annotations

import io
import os
import time
import wave

import httpx
from pydantic import BaseModel, Field

from . import providers, storage

PRESETS = [
    {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1", "needs_key": True,
     "model_hint": "예: whisper-1", "key_url": "https://platform.openai.com/api-keys"},
    {"id": "groq", "label": "Groq", "base_url": "https://api.groq.com/openai/v1", "needs_key": True,
     "model_hint": "예: whisper-large-v3", "key_url": "https://console.groq.com/keys"},
    {"id": "local", "label": "내 컴퓨터 Whisper 서버", "base_url": "http://localhost:9000/v1", "needs_key": False,
     "model_hint": "서버가 제공하는 모델 이름", "key_url": ""},
    {"id": "custom", "label": "직접 입력", "base_url": "", "needs_key": False, "model_hint": "모델 이름", "key_url": ""},
]
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".webm", ".ogg", ".mp4", ".mpeg", ".mpga", ".flac", ".aac"}
MAX_AUDIO_BYTES = 25 * 1024 * 1024  # OpenAI 계열 서버의 일반적인 상한


class SpeechError(Exception):
    pass


class SpeechSettings(BaseModel):
    enabled: bool = False
    preset: str = "openai"
    base_url: str = ""
    model: str = ""
    language: str = "ko"
    api_keys: dict[str, str] = Field(default_factory=dict)  # 프리셋별로 따로 저장


class SpeechUpdate(BaseModel):
    enabled: bool
    preset: str = Field(default="openai", pattern="^(openai|groq|local|custom)$")
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    language: str = Field(default="ko", max_length=10)
    api_key: str | None = Field(default=None, max_length=500)  # None = 유지, "" = 삭제


def _path():
    return storage.DATA_DIR / "speech.json"


def load() -> SpeechSettings:
    saved = storage.read_json(_path())
    if saved:
        return SpeechSettings(**saved)
    if os.environ.get("WHOWILLSEE_STT_BASE_URL") or os.environ.get("WHOWILLSEE_STT_MODEL"):
        return SpeechSettings(enabled=True, preset="custom", base_url=os.environ.get("WHOWILLSEE_STT_BASE_URL", ""),
                              model=os.environ.get("WHOWILLSEE_STT_MODEL", ""))
    return SpeechSettings()


def _merge(update: SpeechUpdate) -> SpeechSettings:
    current = load()
    keys = dict(current.api_keys)
    if update.api_key is not None:
        if update.api_key.strip():
            keys[update.preset] = update.api_key.strip()
        else:
            keys.pop(update.preset, None)
    return SpeechSettings(enabled=update.enabled, preset=update.preset, base_url=update.base_url.strip(),
                          model=update.model.strip(), language=update.language.strip() or "ko", api_keys=keys)


def save(update: SpeechUpdate) -> SpeechSettings:
    merged = _merge(update)
    storage.write_json(_path(), merged.model_dump())
    return merged


def merge_for_test(update: SpeechUpdate) -> SpeechSettings:
    return _merge(update)


def preset_of(s: SpeechSettings) -> dict:
    return next((p for p in PRESETS if p["id"] == s.preset), PRESETS[-1])


def base_url(s: SpeechSettings) -> str:
    return (s.base_url or preset_of(s)["base_url"]).rstrip("/")


def resolve_key(s: SpeechSettings) -> tuple[str, str | None]:
    """(키, 출처). OpenAI 를 고르고 키를 비워두면 분석용 AI 연결의 OpenAI 키를 함께 쓴다."""
    if s.api_keys.get(s.preset):
        return s.api_keys[s.preset], "saved"
    if s.preset == "openai":
        shared = providers.load_settings().api_keys.get("openai:openai")
        if shared:
            return shared, "shared"
        if os.environ.get("OPENAI_API_KEY"):
            return os.environ["OPENAI_API_KEY"], "env"
    if os.environ.get("WHOWILLSEE_STT_API_KEY"):
        return os.environ["WHOWILLSEE_STT_API_KEY"], "env"
    return "", None


def problems(s: SpeechSettings) -> list[str]:
    if not s.enabled:
        return ["음성 인식이 꺼져 있습니다. 'AI 연결 > 음성 인식'에서 켜세요."]
    out = []
    if not base_url(s):
        out.append("음성 인식 API 주소를 입력하세요.")
    if not s.model:
        out.append("음성 인식 모델 이름을 입력하세요.")
    if preset_of(s)["needs_key"] and not resolve_key(s)[0]:
        out.append("음성 인식 API 키를 입력하세요.")
    return out


def describe(s: SpeechSettings) -> dict:
    key, source = resolve_key(s)
    return {
        "enabled": s.enabled, "preset": s.preset, "base_url": s.base_url, "model": s.model, "language": s.language,
        "has_key": bool(key), "key_hint": providers._mask(key), "key_source": source,
        "ready": not problems(s), "problems": [] if not s.enabled else problems(s),
        "keys": {p: providers._mask(k) for p, k in s.api_keys.items()},
        "presets": PRESETS, "locked": providers.settings_locked(),
    }


def _http_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(900.0, connect=15.0))


def transcribe(s: SpeechSettings, filename: str, data: bytes) -> dict:
    """{"text", "duration", "segments": [{"start", "end", "text"}]} 를 돌려준다."""
    issues = problems(s)
    if issues:
        raise SpeechError(" ".join(issues))
    key, _ = resolve_key(s)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    url = f"{base_url(s)}/audio/transcriptions"
    attempts = [
        {"response_format": "verbose_json", "timestamp_granularities[]": "segment"},
        {"response_format": "verbose_json"},
        {"response_format": "json"},
    ]
    errors = []
    with _http_client() as http:
        for extra in attempts:
            form = {"model": s.model, **({"language": s.language} if s.language else {}), **extra}
            try:
                resp = http.post(url, headers=headers, data=form, files={"file": (filename, data)})
            except httpx.HTTPError as exc:
                raise SpeechError(f"음성 인식 서버에 연결할 수 없습니다 ({base_url(s)}).") from exc
            if resp.status_code == 400:
                errors.append(providers._error_text(resp))
                continue
            if resp.status_code in (401, 403):
                raise SpeechError("음성 인식 API 키가 올바르지 않거나 권한이 없습니다.")
            if resp.status_code == 404:
                raise SpeechError(f"음성 인식 주소 또는 모델을 찾을 수 없습니다: {url} / {s.model}")
            if resp.status_code == 413:
                raise SpeechError("녹음 파일이 너무 큽니다. 25MB 이하로 줄이거나 나눠서 올려주세요.")
            if resp.status_code >= 400:
                raise SpeechError(f"음성 인식 오류 ({resp.status_code}): {providers._error_text(resp)}")
            return _normalize(resp.json())
    raise SpeechError("음성 인식 서버가 요청을 거부했습니다: " + " | ".join(errors[-2:]))


def _normalize(data: dict) -> dict:
    segments = []
    for seg in data.get("segments") or []:
        text = str(seg.get("text") or "").strip()
        try:
            start, end = float(seg.get("start", 0)), float(seg.get("end", 0))
        except (TypeError, ValueError):
            continue
        if text and end >= start:
            segments.append({"start": round(start, 2), "end": round(end, 2), "text": text})
    duration = data.get("duration")
    try:
        duration = float(duration) if duration is not None else (segments[-1]["end"] if segments else None)
    except (TypeError, ValueError):
        duration = segments[-1]["end"] if segments else None
    return {"text": str(data.get("text") or " ".join(s["text"] for s in segments)).strip(),
            "duration": duration, "segments": segments}


def silent_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def test_connection(s: SpeechSettings) -> dict:
    started = time.time()
    transcribe(s, "test.wav", silent_wav())
    return {"ok": True, "message": f"{preset_of(s)['label']} · {s.model} 음성 인식 연결 성공",
            "latency_ms": int((time.time() - started) * 1000)}
