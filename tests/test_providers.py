import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import providers
from app.main import app


def test_default_is_demo_without_keys():
    s = providers.load_settings()
    assert s.provider == "demo"
    assert providers.get_provider(s) is None
    assert providers.describe(s)["ready"] is False


def test_env_key_selects_claude(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-123456789")
    d = providers.describe(providers.load_settings())
    assert d["provider"] == "anthropic" and d["ready"] and d["key_source"] == "env"
    assert "123456789" not in json.dumps(d)


def test_saved_settings_override_env_and_keep_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-key")
    providers.save_settings(providers.SettingsUpdate(provider="openai_compatible", preset="ollama",
                                                     model="qwen2.5vl", api_key=""))
    s = providers.load_settings()
    assert s.provider == "openai_compatible"
    assert providers.effective_base_url(s) == "http://localhost:11434/v1"
    assert providers.describe(s)["ready"]  # Ollama 는 키가 필요 없다

    providers.save_settings(providers.SettingsUpdate(provider="anthropic", api_key="sk-ant-saved-abcdefgh"))
    providers.save_settings(providers.SettingsUpdate(provider="anthropic", api_key=None, effort="high"))
    s = providers.load_settings()
    assert s.api_key == "sk-ant-saved-abcdefgh" and s.effort == "high"
    d = providers.describe(s)
    assert d["key_source"] == "saved" and d["key_hint"] == "sk-ant…efgh"


def test_incomplete_openai_settings_report_problems():
    s = providers.Settings(provider="openai_compatible", preset="openai", model="")
    issues = providers.problems(s)
    assert any("모델" in i for i in issues) and any("키" in i for i in issues)
    with pytest.raises(providers.ProviderError):
        providers.get_provider(s)


def test_parse_json_tolerates_fences():
    assert providers.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert providers.parse_json('결과입니다: {"a": 2} 끝') == {"a": 2}
    with pytest.raises(providers.ProviderError):
        providers.parse_json("죄송합니다")


def test_openai_compatible_falls_back_and_sends_images(monkeypatch):
    seen = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer k-123"
        if len(seen) == 1:  # json_schema 미지원 서버 흉내
            return httpx.Response(400, json={"error": {"message": "response_format not supported"}})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '```json\n{"ok": true, "message": "hi"}\n```'}}]})

    monkeypatch.setattr(providers, "_http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    s = providers.Settings(provider="openai_compatible", preset="custom", base_url="http://x/v1/",
                           model="m", api_key="k-123")
    out = providers.get_provider(s).complete_json(
        "sys", [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
                {"type": "text", "text": "hello"}], {"type": "object"})
    assert out == {"ok": True, "message": "hi"}
    assert len(seen) == 2
    image = seen[0]["messages"][1]["content"][0]
    assert image == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}
    assert seen[0]["response_format"]["type"] == "json_schema"


def test_openai_compatible_auth_error(monkeypatch):
    monkeypatch.setattr(providers, "_http_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    s = providers.Settings(provider="openai_compatible", preset="custom", base_url="http://x/v1", model="m")
    with pytest.raises(providers.ProviderError, match="키"):
        providers.test_connection(s)


def test_settings_api_masks_key_and_can_lock(monkeypatch):
    client = TestClient(app)
    r = client.put("/api/settings", json={"provider": "anthropic", "api_key": "sk-ant-secret-0000111122"})
    assert r.status_code == 200
    assert "secret" not in r.text
    got = client.get("/api/settings").json()
    assert got["has_key"] and got["ready"] and got["presets"]
    assert "secret" not in json.dumps(got)
    status = client.get("/api/status").json()
    assert status["mode"] == "ai" and "Claude" in status["engine"]

    r = client.post("/api/settings/test", json={"provider": "openai_compatible", "preset": "openai", "model": ""})
    assert r.json()["ok"] is False

    monkeypatch.setenv("WHOWILLSEE_LOCK_SETTINGS", "1")
    assert client.put("/api/settings", json={"provider": "demo"}).status_code == 403
    assert client.get("/api/settings").json()["locked"] is True


def test_keys_and_models_are_isolated_per_service():
    providers.save_settings(providers.SettingsUpdate(provider="anthropic", model="claude-opus-5-5",
                                                     api_key="sk-ant-claude-key-123"))
    s = providers.save_settings(providers.SettingsUpdate(provider="openai_compatible", preset="gemini",
                                                         model="gemini-x", api_key=None))
    assert s.api_key == ""  # Claude 키가 Gemini 로 넘어가지 않는다
    assert providers.problems(s) == ["API 키를 입력하세요."]
    s = providers.save_settings(providers.SettingsUpdate(provider="anthropic", api_key=None,
                                                         model="claude-opus-5-5"))
    assert s.api_key == "sk-ant-claude-key-123"
    assert s.models == {"anthropic": "claude-opus-5-5", "openai:gemini": "gemini-x"}
