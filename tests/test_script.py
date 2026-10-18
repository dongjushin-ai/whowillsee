import time

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import analyzer, converter, providers, script
from app.main import app
from app.personas import get_persona


def _wait(client, job_id):
    for _ in range(300):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("timeout")


def test_timing_and_long_sentences():
    s = {"target_minutes": 1, "pages": {"1": "가" * 330, "2": "나" * 165}}
    t = script.timing(s, [1, 2])
    assert t["est_minutes"] == 1.5 and t["over"] is True
    assert [p["minutes"] for p in t["per_page"]] == [1.0, 0.5]
    assert script.timing(None, [1]) is None
    assert script.long_sentences("짧다. " + "아" * 100 + ".") == ["아" * 100 + "."]


@pytest.mark.skipif(not converter.soffice_path(), reason="LibreOffice 없음")
def test_pptx_notes_become_script_and_are_analyzed(samples):
    client = TestClient(app)
    with open(samples / "sample_briefing.pptx", "rb") as f:
        doc = client.post("/api/documents", files={"file": ("b.pptx", f, "application/octet-stream")}).json()
    assert "디지털 트윈" in doc["script"]["pages"]["1"]

    r = client.put(f"/api/documents/{doc['id']}/script",
                   json={"target_minutes": 0.2, "pages": doc["script"]["pages"]})
    assert r.status_code == 200 and r.json()["timing"]["over"] is True
    assert client.put(f"/api/documents/{doc['id']}/script", json={"pages": {"9": "x"}}).status_code == 422

    _wait(client, client.post(f"/api/documents/{doc['id']}/analyze", json={}).json()["id"])
    result = client.get(f"/api/documents/{doc['id']}/results/general-citizen").json()
    script_issues = [i for p in result["pages"] for i in p["issues"] if i["target"] == "script"]
    titles = {i["title"] for i in script_issues}
    assert "말로 듣기엔 문장이 너무 깁니다" in titles and "발표 시간이 목표보다 깁니다" in titles
    assert all(i["box"] is None for i in script_issues)
    assert result["overall"]["timing"]["target_minutes"] == 0.2

    pdf = client.get(f"/api/documents/{doc['id']}/results/general-citizen/export.pdf")
    out = pymupdf.open(stream=pdf.content, filetype="pdf")
    rects = sum(1 for page in out for _ in page.annots())
    total = sum(len(p["issues"]) for p in result["pages"])
    assert rects == total - len(script_issues)  # 대본 지적은 박스 없이 메모로만
    assert "[대본]" in "".join(page.get_text() for page in out)


def test_ai_prompt_includes_script(samples, tmp_path, monkeypatch):
    _, pages = converter.convert(samples / "sample_briefing.pdf", tmp_path)
    doc = {"pages": [p.to_dict() for p in pages], "script": {"target_minutes": 5, "pages": {"2": "일정은 화면과 같습니다."}}}
    sent = []

    class Fake:
        engine = "Fake · m"

        def complete_json(self, system, content, schema, max_tokens=0):
            sent.append("\n".join(c.get("text", "") for c in content))
            return {"overall": {"score": 50, "first_impression": "", "summary": "", "strengths": [], "top_fixes": []},
                    "pages": [{"page": 2, "page_comment": "", "issues": [
                        {"target": "script", "block_ids": [1], "bbox": {"x": 0, "y": 0, "w": 0, "h": 0},
                         "severity": "warning", "category": "기타", "title": "t", "reason": "r",
                         "suggestion": "s", "persona_reaction": "?"}]}]}

    monkeypatch.setattr(providers, "get_provider", lambda s=None: Fake())
    result = analyzer.analyze(doc, tmp_path, get_persona("general-citizen"))
    assert "발표 대본" in sent[0] and "일정은 화면과 같습니다." in sent[0] and "목표 5분" in sent[0]
    issue = result["pages"][1]["issues"][0]
    assert issue["target"] == "script" and issue["box"] is None and issue["block_ids"] == []
