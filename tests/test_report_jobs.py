import time

from fastapi.testclient import TestClient

from app import main, storage
from app.main import app


def _wait(client, job_id):
    for _ in range(300):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("timeout")


def test_html_report_is_self_contained_and_escaped(samples):
    client = TestClient(app)
    p = client.post("/api/personas", json={"name": "<script>alert(1)</script>", "expertise_level": 1}).json()
    with open(samples / "sample_briefing.pdf", "rb") as f:
        doc = client.post("/api/documents", files={"file": ("a<b>.pdf", f, "application/pdf")}).json()
    _wait(client, client.post(f"/api/documents/{doc['id']}/analyze", json={"persona_ids": [p["id"]]}).json()["id"])
    r = client.get(f"/api/documents/{doc['id']}/results/{p['id']}/export.html")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    html = r.text
    assert html.count("data:image/png;base64,") == 2
    assert "<script>" not in html and "&lt;script&gt;" in html and "a&lt;b&gt;.pdf" in html
    result = client.get(f"/api/documents/{doc['id']}/results/{p['id']}").json()
    boxed = sum(1 for page in result["pages"] for i in page["issues"] if i["box"])
    assert html.count("class='box ") == boxed
    assert "src=\"http" not in html and "href=\"http" not in html  # 외부 리소스 없음


def test_audience_html_report(samples):
    client = TestClient(app)
    with open(samples / "sample_briefing.pdf", "rb") as f:
        doc = client.post("/api/documents", files={"file": ("a.pdf", f, "application/pdf")}).json()
    _wait(client, client.post(f"/api/documents/{doc['id']}/analyze", json={"audience_id": "exec-report"}).json()["id"])
    html = client.get(f"/api/documents/{doc['id']}/audiences/exec-report/export.html").text
    assert "임원 보고" in html and "청중의" in html and "구성원" in html


def test_jobs_survive_restart(samples):
    client = TestClient(app)
    with open(samples / "sample_briefing.pdf", "rb") as f:
        doc = client.post("/api/documents", files={"file": ("a.pdf", f, "application/pdf")}).json()
    job_id = client.post(f"/api/documents/{doc['id']}/analyze", json={}).json()["id"]
    _wait(client, job_id)
    main._jobs.clear()  # 재시작 흉내: 메모리의 작업 목록이 사라짐
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "done"

    stuck = {"id": "stuckjob1234", "doc_id": doc["id"], "status": "running", "created_at": 0,
             "items": {"general-citizen": {"persona_id": "general-citizen", "name": "일반 시민", "status": "running",
                                           "done": 0, "total": 1, "error": None},
                       "journalist": {"persona_id": "journalist", "name": "기자", "status": "done",
                                      "done": 1, "total": 1, "error": None}}}
    storage.write_json(storage.DATA_DIR / "jobs" / "stuckjob1234.json", stuck)
    assert main.recover_jobs() == 1
    job = client.get("/api/jobs/stuckjob1234").json()
    assert job["status"] == "partial"
    item = next(i for i in job["items"] if i["persona_id"] == "general-citizen")
    assert item["status"] == "error" and "다시 시작" in item["error"]
    assert client.get("/api/jobs/..%2Fsettings").status_code == 404
