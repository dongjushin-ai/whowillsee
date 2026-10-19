import time

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import audiences
from app.main import app


def _issue(n, page, title, category, severity, box, block_ids=(), reaction=""):
    return {"n": n, "page": page, "title": title, "category": category, "severity": severity, "box": box,
            "block_ids": list(block_ids), "reason": "r", "suggestion": "s", "persona_reaction": reaction}


def _result(name, score, issues):
    counts = {s: sum(1 for i in issues if i["severity"] == s) for s in ("critical", "warning", "suggestion")}
    return {"persona": {"name": name, "icon": "🙂"}, "engine": None, "context": "",
            "overall": {"score": score, "counts": counts}, "pages": [{"page": 1, "issues": issues}]}


BOX_A = {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.1}
BOX_B = {"x": 0.1, "y": 0.6, "w": 0.5, "h": 0.1}


def test_aggregate_weights_and_merges_shared_issues():
    aud = audiences.get_audience("town-hall")  # 일반 시민 60, 고령 30, 기자 10
    results = {
        "general-citizen": _result("일반 시민", 40, [
            _issue(1, 1, "전문용어가 많습니다", "용어", "warning", BOX_A, [3], "무슨 말이야?"),
            _issue(2, 1, "신청 방법이 없습니다", "누락정보", "critical", BOX_B, [7])]),
        "senior-citizen": _result("고령 어르신", 20, [
            _issue(1, 1, "어려운 외래어", "용어", "critical", BOX_A, [3], "하나도 모르겠네")]),
        "journalist": _result("기자", 80, [
            _issue(1, 1, "수치 근거가 없습니다", "신뢰성·근거", "warning", BOX_B, [9])]),
    }
    out = audiences.aggregate(aud, results)
    assert out["overall"]["score"] == round((40 * 60 + 20 * 30 + 80 * 10) / 100)
    issues = out["pages"][0]["issues"]
    assert len(issues) == 3  # 용어 지적 2개가 하나로 묶인다
    term = next(i for i in issues if i["category"] == "용어")
    assert term["share"] == 90 and term["severity"] == "critical"  # 더 심각한 쪽이 대표
    assert [v["name"] for v in term["voices"]] == ["일반 시민", "고령 어르신"]
    assert issues[0] is term or issues[0]["impact"] >= term["impact"]  # 영향도 순
    assert [i["n"] for i in sorted(issues, key=lambda i: i["n"])] == [1, 2, 3]
    assert out["audience"]["missing"] == []


def test_audience_crud_and_validation():
    a = audiences.create_audience(audiences.AudienceIn(
        name="학부모 설명회", members=[audiences.Member(persona_id="general-citizen", weight=80),
                                  audiences.Member(persona_id="journalist", weight=20)]))
    assert audiences.get_audience(a.id).name == "학부모 설명회"
    with pytest.raises(ValueError):
        audiences.create_audience(audiences.AudienceIn(name="x", members=[audiences.Member(persona_id="nope", weight=1)]))
    with pytest.raises(PermissionError):
        audiences.delete_audience("town-hall")
    audiences.delete_audience(a.id)
    assert audiences.get_audience(a.id) is None


def test_audience_analysis_api(samples):
    client = TestClient(app)
    with open(samples / "sample_briefing.pdf", "rb") as f:
        doc = client.post("/api/documents", files={"file": ("a.pdf", f, "application/pdf")}).json()
    assert client.get(f"/api/documents/{doc['id']}/audiences").json() == []
    job = client.post(f"/api/documents/{doc['id']}/analyze", json={"audience_id": "town-hall", "persona_ids": []}).json()
    assert {i["persona_id"] for i in job["items"]} == {"general-citizen", "senior-citizen", "journalist"}
    for _ in range(300):
        if client.get(f"/api/jobs/{job['id']}").json()["status"] != "running":
            break
        time.sleep(0.1)
    listed = client.get(f"/api/documents/{doc['id']}/audiences").json()
    assert [a["audience_id"] for a in listed] == ["town-hall"]
    agg = client.get(f"/api/documents/{doc['id']}/audiences/town-hall").json()
    assert agg["mode"] == "audience" and "주민설명회" in agg["context"] + agg["persona"]["name"]
    pdf = client.get(f"/api/documents/{doc['id']}/audiences/town-hall/export.pdf")
    contents = [a.info["content"] for page in pymupdf.open(stream=pdf.content, filetype="pdf") for a in page.annots()]
    assert "지적한 청중" in contents[0]
    assert client.delete("/api/personas/general-citizen").status_code == 409  # 그룹에서 사용 중
