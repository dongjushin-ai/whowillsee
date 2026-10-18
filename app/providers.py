"""AI 연결 설정과 제공자(Provider) 구현.

이 프로젝트는 특정 AI 계정에 묶여 있지 않다. 내려받은 사람이 화면의
'AI 연결' 메뉴(또는 환경 변수)에서 자기 AI 를 연결한다.

- anthropic          : Claude (Anthropic API)
- openai_compatible  : OpenAI 호환 Chat Completions API
                       (OpenAI, Google Gemini, Ollama, LM Studio, OpenRouter, vLLM 등)
- demo               : AI 없이 규칙 기반으로만 분석

설정은 data/settings.json 에 저장되며(.gitignore 대상) 환경 변수보다 우선한다.
"""
from __future__ import annotations

import json
import os
import re
import time

import httpx
from pydantic import BaseModel, Field, model_validator

from . import storage

ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"
# 서버측 거절(refusal) 대체 모델 기능을 지원하는 모델
_ANTHROPIC_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
EFFORTS = ["", "low", "medium", "high", "xhigh", "max"]

PROVIDERS = [
    {"id": "anthropic", "label": "Claude (Anthropic)", "needs_key": True,
     "key_url": "https://console.anthropic.com/settings/keys", "default_model": ANTHROPIC_DEFAULT_MODEL},
    {"id": "openai_compatible", "label": "OpenAI 호환 API", "needs_key": False, "key_url": "", "default_model": ""},
    {"id": "demo", "label": "AI 없이 (데모 모드)", "needs_key": False, "key_url": "", "default_model": ""},
]

# OpenAI 호환 API 프리셋. 모델 이름은 사용자가 직접 입력한다 (예시만 제공).
OPENAI_PRESETS = [
    {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1", "needs_key": True,
     "model_hint": "이미지 입력을 지원하는 모델 (예: gpt-4o)", "key_url": "https://platform.openai.com/api-keys"},
    {"id": "gemini", "label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "needs_key": True, "model_hint": "예: gemini-2.5-flash", "key_url": "https://aistudio.google.com/apikey"},
    {"id": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "needs_key": True,
     "model_hint": "예: anthropic/claude-opus-5-5", "key_url": "https://openrouter.ai/keys"},
    {"id": "ollama", "label": "Ollama (내 컴퓨터)", "base_url": "http://localhost:11434/v1", "needs_key": False,
     "model_hint": "비전 모델 (예: qwen2.5vl, llava)", "key_url": ""},
    {"id": "lmstudio", "label": "LM Studio (내 컴퓨터)", "base_url": "http://localhost:1234/v1", "needs_key": False,
     "model_hint": "불러온 비전 모델 이름", "key_url": ""},
    {"id": "custom", "label": "직접 입력", "base_url": "", "needs_key": False,
     "model_hint": "서버가 제공하는 모델 이름", "key_url": ""},
]


class ProviderError(Exception):
    pass


class Settings(BaseModel):
    """키와 모델은 서비스(슬롯)별로 따로 저장한다 - Claude 키가 다른 서비스로 새지 않도록."""

    provider: str = "demo"
    preset: str = "openai"
    base_url: str = ""
    effort: str = "medium"
    models: dict[str, str] = Field(default_factory=dict)
    api_keys: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _accept_flat_fields(cls, data):
        # Settings(provider=..., model=..., api_key=...) 처럼 현재 슬롯 값을 바로 넘겨도 된다
        if isinstance(data, dict) and ("model" in data or "api_key" in data):
            data = dict(data)
            slot = slot_of(data.get("provider", "demo"), data.get("preset", "openai"))
            model, key = data.pop("model", None), data.pop("api_key", None)
            if model:
                data.setdefault("models", {})[slot] = model
            if key:
                data.setdefault("api_keys", {})[slot] = key
        return data

    @property
    def slot(self) -> str:
        return slot_of(self.provider, self.preset)

    @property
    def model(self) -> str:
        return self.models.get(self.slot, "")

    @property
    def api_key(self) -> str:
        return self.api_keys.get(self.slot, "")


def slot_of(provider: str, preset: str) -> str:
    return f"openai:{preset}" if provider == "openai_compatible" else provider


class SettingsUpdate(BaseModel):
    provider: str = Field(pattern="^(anthropic|openai_compatible|demo)$")
    preset: str = "openai"
    model: str = Field(default="", max_length=200)
    base_url: str = Field(default="", max_length=500)
    effort: str = ""
    api_key: str | None = Field(default=None, max_length=500)  # None = 기존 키 유지, "" = 키 삭제


# ---------------------------------------------------------------- 설정 저장/조회

def _path():
    return storage.DATA_DIR / "settings.json"


def settings_locked() -> bool:
    return os.environ.get("WHOWILLSEE_LOCK_SETTINGS") == "1"


def _from_env() -> Settings:
    explicit = os.environ.get("WHOWILLSEE_PROVIDER", "")
    if explicit == "openai_compatible" or (not explicit and os.environ.get("OPENAI_API_KEY")
                                            and not os.environ.get("ANTHROPIC_API_KEY")):
        return Settings(provider="openai_compatible", preset="custom",
                        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                        model=os.environ.get("WHOWILLSEE_MODEL", ""))
    if explicit == "anthropic" or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return Settings(provider="anthropic", model=os.environ.get("WHOWILLSEE_MODEL", ANTHROPIC_DEFAULT_MODEL),
                        effort=os.environ.get("WHOWILLSEE_EFFORT", "medium"))
    return Settings(provider="demo")


def load_settings() -> Settings:
    if os.environ.get("WHOWILLSEE_DEMO") == "1":
        return Settings(provider="demo")
    saved = storage.read_json(_path())
    return Settings(**saved) if saved else _from_env()


def _merge(update: SettingsUpdate) -> Settings:
    current = load_settings()
    merged = Settings(provider=update.provider, preset=update.preset, base_url=update.base_url.strip(),
                      effort=update.effort, models=dict(current.models), api_keys=dict(current.api_keys))
    slot = merged.slot
    if update.model.strip():
        merged.models[slot] = update.model.strip()
    else:
        merged.models.pop(slot, None)
    if update.api_key is not None:  # None 이면 저장된 키 유지
        if update.api_key.strip():
            merged.api_keys[slot] = update.api_key.strip()
        else:
            merged.api_keys.pop(slot, None)
    return merged


def save_settings(update: SettingsUpdate) -> Settings:
    merged = _merge(update)
    storage.write_json(_path(), merged.model_dump())
    return merged


def merge_for_test(update: SettingsUpdate) -> Settings:
    return _merge(update)


def resolve_key(s: Settings) -> tuple[str, str | None]:
    """(키, 출처) - 출처는 'saved' | 'env' | None."""
    if s.api_key:
        return s.api_key, "saved"
    env = "ANTHROPIC_API_KEY" if s.provider == "anthropic" else "OPENAI_API_KEY"
    if os.environ.get(env):
        return os.environ[env], "env"
    return "", None


def preset_of(s: Settings) -> dict:
    return next((p for p in OPENAI_PRESETS if p["id"] == s.preset), OPENAI_PRESETS[-1])


def effective_model(s: Settings) -> str:
    if s.provider == "anthropic":
        return s.model or ANTHROPIC_DEFAULT_MODEL
    return s.model


def effective_base_url(s: Settings) -> str:
    return (s.base_url or preset_of(s)["base_url"]).rstrip("/")


def problems(s: Settings) -> list[str]:
    """설정이 분석에 쓸 수 없는 이유 목록 (비어 있으면 사용 가능)."""
    if s.provider == "demo":
        return []
    out = []
    key, _ = resolve_key(s)
    if s.provider == "anthropic":
        if not key and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            out.append("Claude API 키를 입력하세요.")
    else:
        if not effective_base_url(s):
            out.append("API 주소(Base URL)를 입력하세요.")
        if not s.model:
            out.append("모델 이름을 입력하세요.")
        if preset_of(s)["needs_key"] and not key:
            out.append("API 키를 입력하세요.")
    return out


def describe(s: Settings) -> dict:
    """화면/API 용 설정 요약. 키 원문은 절대 포함하지 않는다."""
    key, source = resolve_key(s)
    issues = problems(s)
    label = next(p["label"] for p in PROVIDERS if p["id"] == s.provider)
    if s.provider == "openai_compatible":
        label = preset_of(s)["label"]
    return {
        "provider": s.provider,
        "preset": s.preset,
        "model": s.model,
        "base_url": s.base_url,
        "effort": s.effort,
        "has_key": bool(key),
        "key_hint": _mask(key),
        "key_source": source,
        "ready": s.provider != "demo" and not issues,
        "problems": issues,
        "engine": None if s.provider == "demo" else f"{label} · {effective_model(s) or '(모델 미지정)'}",
        "locked": settings_locked(),
        "slots": {slot: {"model": s.models.get(slot, ""), "has_key": bool(k), "key_hint": _mask(k)}
                  for slot, k in [(x, s.api_keys.get(x, "")) for x in set(s.models) | set(s.api_keys)]},
    }


def _mask(key: str) -> str:
    if not key:
        return ""
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "…" + key[-2:]


# ---------------------------------------------------------------- 제공자

def get_provider(s: Settings | None = None):
    """분석에 쓸 제공자. 데모 모드이거나 설정이 불완전하면 None."""
    s = s or load_settings()
    if s.provider == "demo":
        return None
    issues = problems(s)
    if issues:
        raise ProviderError("AI 연결 설정이 완전하지 않습니다: " + " ".join(issues))
    if s.provider == "anthropic":
        return AnthropicProvider(s)
    return OpenAICompatibleProvider(s)


def _anthropic_client(api_key: str):
    import anthropic

    # 키가 비어 있으면 SDK 가 환경 변수(ANTHROPIC_AUTH_TOKEN 등)에서 찾는다
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


class AnthropicProvider:
    def __init__(self, s: Settings):
        self.settings = s
        self.model = effective_model(s)
        self.engine = describe(s)["engine"]

    def complete_json(self, system: str, content: list[dict], schema: dict, max_tokens: int = 32000) -> dict:
        import anthropic

        key, _ = resolve_key(self.settings)
        client = _anthropic_client(key)
        output_config = {"format": {"type": "json_schema", "schema": schema}}
        if self.settings.effort:
            output_config["effort"] = self.settings.effort
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system, output_config=output_config,
                      messages=[{"role": "user", "content": content}])
        if self.model in _ANTHROPIC_FALLBACK_MODELS:
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        try:
            with client.beta.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise ProviderError("API 키가 올바르지 않습니다.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderError("이 API 키로는 해당 모델을 쓸 권한이 없습니다.") from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError(f"모델을 찾을 수 없습니다: {self.model}") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Claude API 오류 ({exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("Claude API 에 연결할 수 없습니다.") from exc

        if message.stop_reason == "refusal":
            raise ProviderError("모델이 이 자료의 분석을 거절했습니다.")
        if message.stop_reason == "max_tokens":
            raise ProviderError("응답이 너무 길어 잘렸습니다. WHOWILLSEE_PAGES_PER_CALL 값을 줄여보세요.")
        text = next((b.text for b in message.content if b.type == "text"), "")
        return parse_json(text)


def _http_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(600.0, connect=15.0))


class OpenAICompatibleProvider:
    """OpenAI Chat Completions 형식을 쓰는 모든 서버용."""

    def __init__(self, s: Settings):
        self.settings = s
        self.model = s.model
        self.base_url = effective_base_url(s)
        self.engine = describe(s)["engine"]

    def complete_json(self, system: str, content: list[dict], schema: dict, max_tokens: int = 32000) -> dict:
        key, _ = resolve_key(self.settings)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        user = [_to_openai_part(part) for part in content]
        schema_hint = ("\n\n반드시 아래 JSON 스키마를 따르는 JSON 객체 하나만 출력하세요. 다른 글은 쓰지 마세요.\n"
                       + json.dumps(schema, ensure_ascii=False))
        # 서버마다 지원 범위가 달라 점점 단순한 요청으로 재시도한다
        variants = [
            {"response_format": {"type": "json_schema",
                                 "json_schema": {"name": "result", "schema": schema, "strict": True}},
             "max_tokens": max_tokens, "hint": False},
            {"response_format": {"type": "json_schema",
                                 "json_schema": {"name": "result", "schema": schema, "strict": True}},
             "max_completion_tokens": max_tokens, "hint": False},
            {"response_format": {"type": "json_object"}, "max_tokens": max_tokens, "hint": True},
            {"max_tokens": max_tokens, "hint": True},
        ]
        errors = []
        with _http_client() as http:
            for variant in variants:
                hint = variant.pop("hint")
                body = {"model": self.model, "messages": [
                    {"role": "system", "content": system + (schema_hint if hint else "")},
                    {"role": "user", "content": user}], **variant}
                try:
                    resp = http.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
                except httpx.HTTPError as exc:
                    raise ProviderError(f"AI 서버에 연결할 수 없습니다 ({self.base_url}). 주소와 서버 실행 여부를 확인하세요.") from exc
                if resp.status_code == 400:
                    errors.append(_error_text(resp))
                    continue
                if resp.status_code in (401, 403):
                    raise ProviderError("API 키가 올바르지 않거나 권한이 없습니다.")
                if resp.status_code == 404:
                    raise ProviderError(f"주소 또는 모델을 찾을 수 없습니다: {self.base_url} / {self.model}")
                if resp.status_code == 429:
                    raise ProviderError("API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.")
                if resp.status_code >= 400:
                    raise ProviderError(f"AI 서버 오류 ({resp.status_code}): {_error_text(resp)}")
                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                if choice.get("finish_reason") == "length":
                    raise ProviderError("응답이 너무 길어 잘렸습니다. WHOWILLSEE_PAGES_PER_CALL 값을 줄여보세요.")
                text = (choice.get("message") or {}).get("content") or ""
                if isinstance(text, list):  # 일부 서버는 content 를 파트 배열로 준다
                    text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
                return parse_json(text)
        raise ProviderError("AI 서버가 요청을 거부했습니다: " + " | ".join(errors[-2:]))


def _to_openai_part(part: dict) -> dict:
    if part["type"] == "image":
        src = part["source"]
        return {"type": "image_url", "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}}
    return {"type": "text", "text": part["text"]}


def _error_text(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error")
        if isinstance(err, dict):
            return str(err.get("message", err))[:300]
        return str(err or resp.text)[:300]
    except ValueError:
        return resp.text[:300]


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json(text: str) -> dict:
    """모델 응답에서 JSON 객체를 꺼낸다 (코드 펜스·앞뒤 설명 허용)."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    cleaned = _FENCE.sub("", text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise ProviderError("AI 응답을 JSON 으로 해석할 수 없습니다. 더 성능이 좋은 모델을 써보세요.")


# ---------------------------------------------------------------- 연결 테스트

_TEST_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}, "message": {"type": "string"}},
                "required": ["ok", "message"], "additionalProperties": False}

# 1x1 흰색 PNG - 이미지 입력을 받는지 함께 확인한다
_PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"


def test_connection(s: Settings) -> dict:
    if s.provider == "demo":
        return {"ok": True, "message": "데모 모드는 AI 연결이 필요 없습니다.", "latency_ms": 0}
    started = time.time()
    provider = get_provider(s)
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _PIXEL}},
        {"type": "text", "text": '연결 테스트입니다. 이미지를 받았다면 {"ok": true, "message": "연결 성공"} 을 출력하세요.'},
    ]
    result = provider.complete_json("JSON 으로만 답하세요.", content, _TEST_SCHEMA, max_tokens=2000)
    return {"ok": bool(result.get("ok", True)), "message": f"{provider.engine} 연결 성공 (이미지 입력 확인)",
            "latency_ms": int((time.time() - started) * 1000)}
