import time

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _wait(client, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("job timeout")


def test_status_reports_demo_mode(client):
    s = client.get("/api/status").json()
    assert s["mode"] == "demo"
    assert ".pdf" in s["formats"]


def test_full_flow_upload_analyze_export(client, samples):
    with open(samples / "sample_briefing.pdf", "rb") as f:
        r = client.post("/api/documents", files={"file": ("브리핑.pdf", f, "application/pdf")})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["filename"] == "브리핑.pdf" and len(doc["pages"]) == 2

    img = client.get(f"/api/documents/{doc['id']}/pages/1.png")
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"

    r = client.post(f"/api/documents/{doc['id']}/analyze",
                    json={"persona_ids": ["general-citizen", "domain-expert"], "context": "구청 설명회"})
    assert r.status_code == 202
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done", job

    listing = client.get(f"/api/documents/{doc['id']}/results").json()
    assert {x["persona_id"] for x in listing} == {"general-citizen", "domain-expert"}

    result = client.get(f"/api/documents/{doc['id']}/results/general-citizen").json()
    assert result["context"] == "구청 설명회"
    total = sum(len(p["issues"]) for p in result["pages"])
    assert total > 0

    pdf = client.get(f"/api/documents/{doc['id']}/results/general-citizen/export.pdf")
    assert pdf.status_code == 200
    out = pymupdf.open(stream=pdf.content, filetype="pdf")
    annots = [a for page in out for a in page.annots()]
    assert len(annots) == total
    assert "왜 문제인가" in annots[0].info["content"]
    assert "검토 리포트" in out[0].get_text()

    js = client.get(f"/api/documents/{doc['id']}/results/general-citizen/export.json")
    assert js.json()["persona"]["id"] == "general-citizen"

    docs = client.get("/api/documents").json()
    assert docs[0]["id"] == doc["id"] and docs[0]["analyzed"] == ["domain-expert", "general-citizen"]

    assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404


def test_persona_api(client):
    r = client.post("/api/personas", json={"name": "학부모", "needs": "아이 안전", "expertise_level": 2})
    assert r.status_code == 201
    pid = r.json()["id"]
    assert any(p["id"] == pid for p in client.get("/api/personas").json())
    assert client.put(f"/api/personas/{pid}", json={"name": "학부모2"}).json()["name"] == "학부모2"
    assert client.put("/api/personas/general-citizen", json={"name": "x"}).status_code == 403
    assert client.post("/api/personas", json={"name": ""}).status_code == 422
    assert client.delete(f"/api/personas/{pid}").status_code == 204


def test_rejects_bad_input(client, tmp_path):
    r = client.post("/api/documents", files={"file": ("a.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400
    r = client.post("/api/documents", files={"file": ("broken.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 422
    assert client.post("/api/documents").status_code == 400
    assert client.get("/api/documents/..%2F..%2Fetc").status_code == 404
    assert client.post("/api/documents/nope/analyze", json={}).status_code == 404


def test_dashboard_stats(client, samples):
    empty = client.get("/api/stats").json()
    assert empty["totals"]["analyses"] == 0 and empty["totals"]["avg_score"] is None

    with open(samples / "sample_briefing.pdf", "rb") as f:
        doc = client.post("/api/documents", files={"file": ("a.pdf", f, "application/pdf")}).json()
    job = client.post(f"/api/documents/{doc['id']}/analyze",
                      json={"persona_ids": ["general-citizen", "domain-expert"]}).json()
    _wait(client, job["id"])

    s = client.get("/api/stats").json()
    assert s["totals"]["documents"] == 1 and s["totals"]["analyses"] == 2
    assert s["totals"]["issues"] == s["totals"]["critical"] + s["totals"]["warning"] + s["totals"]["suggestion"]
    names = [p["name"] for p in s["personas"]]
    assert names[0] == "일반 시민"  # 평균 점수가 낮은 순
    assert s["categories"][0]["total"] >= s["categories"][-1]["total"]
    assert set(s["matrix"]["cells"][doc["id"]]) == {"general-citizen", "domain-expert"}
    assert len(s["recent"]) == 2
