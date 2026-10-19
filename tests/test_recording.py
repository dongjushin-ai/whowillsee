import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import providers, recording, speech
from app.main import app

META = {"id": "d1", "pages": [
    {"index": 1, "blocks": [{"id": 1, "text": "스마트시티 데이터 거버넌스 추진배경"}]},
    {"index": 2, "blocks": [{"id": 1, "text": "추진 일정 1단계 시범 운영 2단계 전면 확산"}]},
    {"index": 3, "blocks": [{"id": 1, "text": "문의처 데이터정책과 전화번호"}]},
]}
SEGMENTS = [
    {"start": 0, "end": 8, "text": "음 안녕하세요 스마트시티 데이터 거버넌스 추진배경을 말씀드리겠습니다"},
    {"start": 8, "end": 15, "text": "이제 어 이 사업은 시민 생활을 바꿉니다"},
    {"start": 19, "end": 27, "text": "추진 일정은 1단계 시범 운영 그리고 2단계 전면 확산입니다"},
    {"start": 27, "end": 33, "text": "이제 이제 시범 운영은 3월부터입니다"},
    {"start": 37, "end": 44, "text": "문의는 데이터정책과 전화번호로 주세요"},
]


def test_align_dp_follows_slide_order():
    pages = recording.page_texts(META)
    assert recording.align_dp(SEGMENTS, pages) == [1, 1, 2, 2, 3]


def test_metrics():
    m = recording.analyze(META, {"text": "", "duration": 45, "segments": SEGMENTS})
    assert m["duration_seconds"] == 45 and m["timed"] and m["align_method"] == "auto"
    assert [p["seconds"] for p in m["per_page"]] == [19.0, 18.0, 7.0]
    assert m["fillers"]["이제"] == 3 and m["fillers"]["음"] == 1 and m["fillers"]["어"] == 1
    assert [p["seconds"] for p in m["long_pauses"]] == [4.0, 4.0]
    assert 100 < m["rate"] < 400
    assert "추진 일정" in m["page_texts"]["2"]


def test_metrics_without_timestamps():
    m = recording.analyze(META, {"text": "안녕하세요 스마트시티", "duration": 30, "segments": []})
    assert m["timed"] is False and m["duration_seconds"] == 30 and m["align_method"] == "none"


def _mock(monkeypatch, handler):
    monkeypatch.setattr(speech, "_http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def test_transcribe_retries_and_normalizes(monkeypatch):
    seen = []

    def handler(request):
        body = request.content.decode("utf-8", "ignore")
        seen.append(body)
        assert request.url.path == "/openai/v1/audio/transcriptions"  # Groq 주소
        assert request.headers["authorization"] == "Bearer gsk-1"
        if len(seen) == 1:
            return httpx.Response(400, json={"error": {"message": "timestamp_granularities not supported"}})
        return httpx.Response(200, json={"text": "안녕", "duration": 3.2,
                                         "segments": [{"start": 0, "end": 1.5, "text": " 안녕 "}, {"start": "x"}]})

    _mock(monkeypatch, handler)
    s = speech.SpeechSettings(enabled=True, preset="groq", model="whisper-large-v3", api_keys={"groq": "gsk-1"})
    out = speech.transcribe(s, "a.wav", speech.silent_wav(0.1))
    assert out == {"text": "안녕", "duration": 3.2, "segments": [{"start": 0.0, "end": 1.5, "text": "안녕"}]}
    assert 'name="model"' in seen[0] and "whisper-large-v3" in seen[0] and 'name="language"' in seen[0]
    assert "timestamp_granularities" in seen[0] and "timestamp_granularities" not in seen[1]


def test_speech_settings_keys_and_sharing():
    assert speech.problems(speech.load())[0].startswith("음성 인식이 꺼져")
    providers.save_settings(providers.SettingsUpdate(provider="openai_compatible", preset="openai",
                                                     model="m", api_key="sk-openai-shared-123"))
    s = speech.save(speech.SpeechUpdate(enabled=True, preset="openai", model="whisper-1"))
    assert speech.resolve_key(s) == ("sk-openai-shared-123", "shared")  # 분석용 OpenAI 키를 함께 씀
    s = speech.save(speech.SpeechUpdate(enabled=True, preset="groq", model="w", api_key="gsk-secret-99"))
    d = speech.describe(s)
    assert d["ready"] and "gsk-secret-99" not in json.dumps(d)
    s = speech.save(speech.SpeechUpdate(enabled=True, preset="openai", model="whisper-1", api_key=None))
    assert speech.resolve_key(s)[1] == "shared"  # Groq 키가 OpenAI 로 새지 않음


def _wait(client, job_id):
    for _ in range(300):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("timeout")


def test_recording_flow(samples, monkeypatch):
    client = TestClient(app)
    with open(samples / "sample_briefing.pdf", "rb") as f:
        doc = client.post("/api/documents", files={"file": ("b.pdf", f, "application/pdf")}).json()
    wav = speech.silent_wav(0.2)
    assert client.post(f"/api/documents/{doc['id']}/recording", files={"file": ("r.wav", wav)}).status_code == 409
    client.put("/api/speech", json={"enabled": True, "preset": "local", "model": "whisper"})
    assert client.post(f"/api/documents/{doc['id']}/recording", files={"file": ("r.txt", b"x")}).status_code == 400

    fast = "이제 음 스마트시티 데이터 거버넌스 고도화 추진계획을 아주 빠르게 말씀드리면 이제 어 디지털 트윈 기반으로 " * 3
    segs = [{"start": 0, "end": 10, "text": fast}, {"start": 14, "end": 20, "text": "추진 현황 및 향후 일정 이제 음 어 그"}]
    _mock(monkeypatch, lambda r: httpx.Response(200, json={"text": "", "duration": 20, "segments": segs}))
    job = client.post(f"/api/documents/{doc['id']}/recording", files={"file": ("rehearsal.m4a", wav)}).json()
    assert _wait(client, job["id"])["status"] == "done"
    meta = client.get(f"/api/documents/{doc['id']}").json()
    assert meta["recording"]["filename"] == "rehearsal.m4a"
    assert meta["script"]["source"] == "recording" and set(meta["script"]["actual_seconds"]) == {"1", "2"}

    # 직접 고쳐 저장해도 실측 시간은 남는다
    client.put(f"/api/documents/{doc['id']}/script", json={"target_minutes": 0.2, "pages": meta["script"]["pages"]})
    assert client.get(f"/api/documents/{doc['id']}").json()["script"]["actual_seconds"]

    _wait(client, client.post(f"/api/documents/{doc['id']}/analyze", json={}).json()["id"])
    r = client.get(f"/api/documents/{doc['id']}/results/general-citizen").json()
    assert r["overall"]["timing"]["measured"] is True and r["overall"]["timing"]["over"] is True
    assert r["overall"]["delivery"]["filler_total"] >= 8
    titles = {i["title"] for p in r["pages"] for i in p["issues"]}
    assert {"말이 너무 빠릅니다", "말버릇이 자주 나옵니다"} <= titles

    assert client.delete(f"/api/documents/{doc['id']}/recording").status_code == 204
    meta = client.get(f"/api/documents/{doc['id']}").json()
    assert "recording" not in meta and "actual_seconds" not in meta["script"]


def test_transcribe_auth_error(monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(401))
    s = speech.SpeechSettings(enabled=True, preset="custom", base_url="http://x/v1", model="m")
    with pytest.raises(speech.SpeechError, match="키"):
        speech.test_connection(s)
