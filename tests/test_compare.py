import time

from fastapi.testclient import TestClient

from app import compare
from app.main import app


def _issue(n, title, category, severity="critical", block_ids=(1,), page=1, reason="r"):
    return {"n": n, "page": page, "severity": severity, "category": category, "title": title,
            "block_ids": list(block_ids), "reason": reason}


def _doc(doc_id, texts):
    return {"id": doc_id, "filename": f"{doc_id}.pdf",
            "pages": [{"index": 1, "blocks": [{"id": i + 1, "text": t} for i, t in enumerate(texts)]}]}


def _result(score, issues):
    counts = {s: sum(1 for i in issues if i["severity"] == s) for s in ("critical", "warning", "suggestion")}
    return {"overall": {"score": score, "counts": counts}, "pages": [{"page": 1, "issues": issues}]}


def test_compare_classifies_resolved_remaining_new():
    prev_meta = _doc("v1", ["TCO 23.7% 절감, ROI 312%", "상세 근거는 별첨 7-3 참조", "추후 공지 예정"])
    cur_meta = _doc("v2", ["비용 23.7% 절감, ROI 312%", "추후 공지 예정", "신청은 앱에서만 가능"])
    prev = _result(30, [_issue(1, "전문용어·약어가 많습니다", "용어", block_ids=[1]),
                        _issue(2, "정보가 다른 곳으로 미뤄져 있습니다", "누락정보", block_ids=[2]),
                        _issue(3, "신청 방법이 없습니다", "누락정보", block_ids=[3])])
    cur = _result(55, [_issue(1, "약어가 남아 있습니다", "용어", severity="warning", block_ids=[1]),
                       _issue(2, "신청 방법이 없습니다", "누락정보", block_ids=[2]),
                       _issue(3, "디지털 취약층 배제", "접근성", block_ids=[3])])
    out = compare.compare(prev_meta, prev, cur_meta, cur)
    assert out["delta_score"] == 25
    assert [r["prev"]["n"] for r in out["remaining"]] == [1, 3]
    assert out["remaining"][0]["change"] == "better"  # 치명 → 주의
    assert [r["n"] for r in out["resolved"]] == [2]
    assert [r["n"] for r in out["new"]] == [3]


def _wait(client, job_id):
    for _ in range(300):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("timeout")


def test_version_upload_and_compare_api(samples):
    client = TestClient(app)
    with open(samples / "sample_briefing.pdf", "rb") as f:
        v1 = client.post("/api/documents", files={"file": ("b.pdf", f, "application/pdf")}).json()
    with open(samples / "sample_briefing.pptx", "rb") as f:
        v2 = client.post("/api/documents", data={"parent_id": v1["id"]},
                         files={"file": ("b.pptx", f, "application/octet-stream")}).json()
    assert v2["version"] == 2 and v2["parent_id"] == v1["id"] and v2["group_id"] == v1["id"]

    assert client.get(f"/api/documents/{v1['id']}/compare/general-citizen").status_code == 404
    for doc in (v1, v2):
        _wait(client, client.post(f"/api/documents/{doc['id']}/analyze", json={}).json()["id"])
    out = client.get(f"/api/documents/{v2['id']}/compare/general-citizen").json()
    assert out["prev"]["version"] == 1 and out["cur"]["version"] == 2
    total_prev = sum(out["prev"]["counts"].values())
    assert len(out["resolved"]) + len(out["remaining"]) == total_prev

    stats = client.get("/api/stats").json()
    assert stats["versions"][0]["points"][0]["version"] == 1
    assert len(stats["versions"][0]["points"]) == 2

    bad = client.post("/api/documents", data={"parent_id": "nope"}, files={"file": ("c.png", b"x", "image/png")})
    assert bad.status_code == 404
