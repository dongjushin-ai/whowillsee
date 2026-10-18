import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

from app import audiences, messages, personas
from app.main import app

ROOT = Path(__file__).resolve().parent.parent
HANGUL = re.compile(r"[가-힣]")
EN = json.loads((ROOT / "app/static/i18n/en.json").read_text(encoding="utf-8"))


def _placeholders(text):
    return sorted(re.findall(r"\{(\w+)\}", text))


def test_message_catalog_is_complete():
    for key, entry in messages.MESSAGES.items():
        assert set(entry) == {"ko", "en"}, key
        assert _placeholders(entry["ko"]) == _placeholders(entry["en"]), key
        assert not HANGUL.search(entry["en"]), key


def test_ui_dictionary_placeholders_match():
    for key, value in EN.items():
        if key.startswith("@"):
            continue
        assert _placeholders(key) == _placeholders(value), key
        assert not HANGUL.search(value), key


def test_every_korean_string_in_index_html_is_translated():
    """index.html 에 한국어 문구를 추가하면 en.json 에도 넣어야 한다."""
    missing = []

    class P(HTMLParser):
        skip = 0  # data-i18n-html 로 통째로 번역되는 요소 안쪽

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if self.skip or "data-i18n-html" in a:
                self.skip += 1
            for k in ("placeholder", "title", "alt", "aria-label"):
                v = (a.get(k) or "").strip()
                if HANGUL.search(v) and v not in EN and "언어" not in v:
                    missing.append(v)

        def handle_endtag(self, tag):
            if self.skip:
                self.skip -= 1

        def handle_data(self, data):
            text = re.sub(r"\s+", " ", data).strip()
            if self.skip or not HANGUL.search(text) or text in EN or text == "한국어" or "localStorage" in text:
                return
            missing.append(text)

    P().feed((ROOT / "app/static/index.html").read_text(encoding="utf-8"))
    assert missing == []
    for key in ("banner", "empty", "settings_intro", "env_help"):
        assert f"@html.{key}" in EN


def test_presets_are_localized():
    en = {p.id: p for p in personas.list_personas("en")}
    assert en["general-citizen"].name == "General citizen" and not HANGUL.search(en["senior-citizen"].needs)
    assert personas.get_persona("general-citizen").name == "일반 시민"
    assert audiences.get_audience("town-hall", "en").name == "Town hall meeting"


def _wait(client, job_id):
    for _ in range(300):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("timeout")


def test_english_analysis_and_reports(samples):
    client = TestClient(app)
    assert client.get("/api/personas?lang=en").json()[0]["name"] == "General citizen"
    assert client.get("/api/personas?lang=xx").status_code == 422
    with open(samples / "sample_briefing.pdf", "rb") as f:
        doc = client.post("/api/documents", files={"file": ("b.pdf", f, "application/pdf")}).json()
    _wait(client, client.post(f"/api/documents/{doc['id']}/analyze",
                              json={"audience_id": "town-hall", "lang": "en"}).json()["id"])
    r = client.get(f"/api/documents/{doc['id']}/results/general-citizen").json()
    assert r["lang"] == "en" and r["persona"]["name"] == "General citizen"
    issues = [i for p in r["pages"] for i in p["issues"]]
    # 제목·고칠 방법·속마음은 영어. (이유에는 자료 속 한국어 단어를 인용할 수 있다)
    assert all(not HANGUL.search(i["title"] + i["suggestion"] + i["persona_reaction"]) for i in issues)
    assert not HANGUL.search(r["overall"]["summary"] + r["overall"]["first_impression"])

    agg = client.get(f"/api/documents/{doc['id']}/audiences/town-hall?lang=en").json()
    assert agg["persona"]["name"] == "Town hall meeting" and agg["overall"]["summary"].startswith("A combined score")
    assert "of audience" in agg["overall"]["top_fixes"][0]

    html = client.get(f"/api/documents/{doc['id']}/results/general-citizen/export.html?lang=en").text
    assert "<html lang='en'>" in html and "Why it's a problem" in html and "Terminology" in html
    assert "왜 문제인가" not in html and "페이지" not in html
    ko_html = client.get(f"/api/documents/{doc['id']}/results/general-citizen/export.html").text
    assert "왜 문제인가" in ko_html

    import pymupdf
    pdf = client.get(f"/api/documents/{doc['id']}/results/general-citizen/export.pdf?lang=en")
    first = pymupdf.open(stream=pdf.content, filetype="pdf")[0].get_text()
    assert "WhoWillSee review report" in first and "Clarity score" in first


def test_rule_based_persona_in_english():
    client = TestClient(app)
    p = client.post("/api/personas/generate", json={"description": "A retired grandmother in her 70s", "lang": "en"}).json()
    assert p["persona"]["occupation"] == "Retired" and p["persona"]["age_group"] == "70s"
