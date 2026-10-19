import shutil
import subprocess
import time

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import analyzer, providers, speech, video
from app.main import app
from app.personas import get_persona

cv2 = pytest.importorskip("cv2")


def _make_video(path, seconds=16, fps=10):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 240))
    for i in range(seconds * fps):
        w.write(np.full((240, 320, 3), 40 if i < 5 * fps else 200, np.uint8))  # 앞부분은 어둡게
    w.release()
    return path


class FakeDetector:
    """limit 초 전까지는 정면, 그 뒤는 화면 밖."""

    def __init__(self, limit=10):
        self.t, self.limit = 0, limit

    def __call__(self, gray):
        frontal = self.t < self.limit
        self.t += video.SAMPLE_EVERY
        return {"frontal": frontal, "visible": frontal,
                "box": {"x": 0.4, "y": 0.2, "w": 0.2, "h": 0.25} if frontal else None}


def test_metrics_and_longest_away():
    samples = [{"t": t, "frontal": f, "visible": v, "brightness": 0.5, "box": {"x": 0.4, "y": 0, "w": 0.2, "h": 0.3} if v else None}
               for t, f, v in [(0, True, True), (2, False, True), (4, False, False), (6, False, False), (8, True, True)]]
    m = video.metrics(samples, 10, 2)
    assert m["frontal_ratio"] == 0.4 and m["visible_ratio"] == 0.6
    assert m["longest_away_seconds"] == 6 and m["longest_away_at"] == 2 and m["first_away_at"] == 4
    assert [x["state"] for x in m["timeline"]] == ["frontal", "side", "away", "away", "frontal"]


def test_analyze_file_with_fake_detector(tmp_path):
    src = _make_video(tmp_path / "talk.mp4")
    r = video.analyze_file(src, tmp_path / "out", detector=FakeDetector())
    m = r["metrics"]
    assert 15 <= m["duration_seconds"] <= 17 and m["samples"] == 8
    assert m["frontal_ratio"] == 0.62 and m["longest_away_seconds"] == 6.0
    assert 0 < m["brightness"] < 1
    assert r["keyframes"] and all((tmp_path / "out" / k["image"]).exists() for k in r["keyframes"])


def test_demo_feedback_rules():
    v = {"duration_seconds": 60, "frontal_ratio": 0.2, "visible_ratio": 0.5, "longest_away_seconds": 30,
         "longest_away_at": 12.0, "face_size": 0.05, "brightness": 0.2, "movement": 0.2, "keyframes": []}
    items = analyzer._normalize_feedback(analyzer._video_review_demo(v, get_persona("general-citizen")))
    titles = [i["title"] for i in items]
    assert titles[0] == "청중을 보는 시간이 짧습니다" and items[0]["severity"] == "critical" and items[0]["at_seconds"] == 12
    assert {"화면 밖으로 자주 벗어납니다", "발표자가 너무 작게 보입니다", "조명이 어둡습니다", "좌우로 많이 움직입니다"} <= set(titles)
    assert [i["n"] for i in items] == [f"V{k}" for k in range(1, len(items) + 1)]
    good = dict(v, frontal_ratio=0.9, visible_ratio=0.95, longest_away_seconds=4, face_size=0.2, brightness=0.6, movement=0.02)
    assert analyzer._video_review_demo(good, get_persona("general-citizen")) == []


def _wait(client, job_id):
    for _ in range(300):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("timeout")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg 없음")
def test_video_flow_with_audio(samples, tmp_path, monkeypatch):
    src = tmp_path / "talk.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=12",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=12", "-shortest", "-c:v", "mpeg4", "-c:a", "aac",
                    str(src)], check=True)
    monkeypatch.setattr(video, "FaceDetector", lambda: FakeDetector(limit=4))
    segs = [{"start": 0, "end": 5, "text": "스마트시티 데이터 거버넌스 고도화 추진계획"},
            {"start": 6, "end": 11, "text": "추진 현황 및 향후 일정 이제 이제"}]
    calls = []

    def stt(request):
        calls.append(request)
        return httpx.Response(200, json={"text": "", "duration": 12, "segments": segs})

    monkeypatch.setattr(speech, "_http_client", lambda: httpx.Client(transport=httpx.MockTransport(stt)))
    client = TestClient(app)
    client.put("/api/speech", json={"enabled": True, "preset": "local", "model": "whisper"})
    with open(samples / "sample_briefing.pdf", "rb") as f:
        doc = client.post("/api/documents", files={"file": ("b.pdf", f, "application/pdf")}).json()
    assert client.post(f"/api/documents/{doc['id']}/video", files={"file": ("a.txt", b"x")}).status_code == 400
    job = client.post(f"/api/documents/{doc['id']}/video", files={"file": ("rehearsal.mp4", src.read_bytes())}).json()
    assert _wait(client, job["id"])["status"] == "done"
    meta = client.get(f"/api/documents/{doc['id']}").json()
    assert meta["video"]["audio"] == "transcribed" and len(calls) == 1
    assert meta["recording"]["filename"] == "rehearsal.mp4" and meta["script"]["source"] == "recording"
    frame = meta["video"]["keyframes"][0]["image"]
    assert client.get(f"/api/documents/{doc['id']}/video/{frame}").headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/documents/{doc['id']}/video/..%2Fmeta.json").status_code == 404

    _wait(client, client.post(f"/api/documents/{doc['id']}/analyze", json={}).json()["id"])
    r = client.get(f"/api/documents/{doc['id']}/results/general-citizen").json()
    assert r["overall"]["video"]["frontal_ratio"] == 0.33
    assert r["overall"]["video"]["feedback"][0]["n"] == "V1"
    html = client.get(f"/api/documents/{doc['id']}/results/general-citizen/export.html").text
    assert "발표 모습" in html
    pdf = client.get(f"/api/documents/{doc['id']}/results/general-citizen/export.pdf")
    assert pdf.status_code == 200

    assert client.delete(f"/api/documents/{doc['id']}/video").status_code == 204
    assert "video" not in client.get(f"/api/documents/{doc['id']}").json()


def test_ai_video_review_sends_frames(tmp_path, monkeypatch):
    (tmp_path / "video").mkdir()
    (tmp_path / "video" / "frame-01.jpg").write_bytes(b"\xff\xd8fakejpeg")
    seen = []

    class Fake:
        engine = "Fake · m"

        def complete_json(self, system, content, schema, max_tokens=0):
            seen.append((content, schema))
            return {"items": [{"severity": "warning", "category": "시선", "title": "원고만 봅니다", "reason": "r",
                               "suggestion": "s", "persona_reaction": "?", "at_seconds": 30}]}

    v = {"duration_seconds": 60, "frontal_ratio": 0.4, "visible_ratio": 0.9, "longest_away_seconds": 10,
         "longest_away_at": 30.0, "face_size": 0.2, "brightness": 0.5, "movement": 0.01,
         "keyframes": [{"t": 30.0, "image": "frame-01.jpg"}]}
    items = analyzer._video_review_ai(Fake(), get_persona("busy-executive"), "", v, tmp_path)
    content, schema = seen[0]
    assert "정면 얼굴이 보인 장면 40%" in content[0]["text"]
    assert content[2]["source"]["media_type"] == "image/jpeg"
    assert schema is analyzer.VIDEO_SCHEMA and items[0]["title"] == "원고만 봅니다"
