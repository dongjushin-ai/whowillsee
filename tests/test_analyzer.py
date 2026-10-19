import json
from types import SimpleNamespace

import pytest

from app import analyzer, converter, personas, providers
from app.personas import get_persona


@pytest.fixture
def briefing(samples, tmp_path):
    _, pages = converter.convert(samples / "sample_briefing.pdf", tmp_path)
    return {"pages": [p.to_dict() for p in pages]}, tmp_path


def test_resolve_box_prefers_blocks():
    blocks = [{"id": 1, "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.1},
              {"id": 2, "x": 0.5, "y": 0.5, "w": 0.2, "h": 0.2}]
    box, used = analyzer.resolve_box({"block_ids": [1, 2, 99], "bbox": {"x": 0, "y": 0, "w": 1, "h": 1}}, blocks)
    assert used == [1, 2]
    assert box["x"] == pytest.approx(0.094, abs=1e-3)
    assert box["x"] + box["w"] == pytest.approx(0.706, abs=1e-3)


def test_resolve_box_falls_back_to_bbox_and_page():
    box, used = analyzer.resolve_box({"block_ids": [], "bbox": {"x": 0.2, "y": 0.3, "w": 0.4, "h": 0.1}}, [])
    assert used == [] and box == {"x": 0.2, "y": 0.3, "w": 0.4, "h": 0.1}
    box, _ = analyzer.resolve_box({"bbox": {"x": 0, "y": 0, "w": 0, "h": 0}}, [])
    assert box == {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    box, _ = analyzer.resolve_box({"bbox": {"x": 100, "y": 200, "w": 300, "h": 100}}, [])  # 0~1000 스케일
    assert box == {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.1}


def test_demo_mode_flags_known_problems(briefing):
    doc, path = briefing
    result = analyzer.analyze(doc, path, get_persona(personas.DEFAULT_PERSONA_ID))
    assert result["mode"] == "demo"
    issues = [i for p in result["pages"] for i in p["issues"]]
    cats = {i["category"] for i in issues}
    assert {"용어", "가독성", "누락정보"} <= cats
    assert [i["n"] for i in issues] == list(range(1, len(issues) + 1))
    assert 0 <= result["overall"]["score"] < 100
    assert result["overall"]["counts"]["critical"] >= 1


def test_expert_gets_fewer_jargon_issues(briefing):
    doc, path = briefing
    lay = analyzer.analyze(doc, path, get_persona("general-citizen"))
    expert = analyzer.analyze(doc, path, get_persona("domain-expert"))
    count = lambda r: sum(1 for p in r["pages"] for i in p["issues"] if i["category"] == "용어")
    assert count(expert) < count(lay)


class FakeStream:
    def __init__(self, payload, calls):
        self.payload, self.calls = payload, calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


def test_claude_path_batches_and_merges(briefing, monkeypatch):
    doc, path = briefing
    calls = []

    def stream(**kwargs):
        calls.append(kwargs)
        schema = kwargs["output_config"]["format"]["schema"]
        if "pages" not in schema["properties"]:  # 종합 호출
            return FakeStream({"score": 42, "first_impression": "어렵다", "summary": "s",
                               "strengths": [], "top_fixes": ["a"]}, calls)
        page_no = int(kwargs["messages"][0]["content"][1]["text"].split()[2])
        return FakeStream({"overall": {"score": 50, "first_impression": "", "summary": "", "strengths": [],
                                       "top_fixes": []},
                           "pages": [{"page": page_no, "page_comment": "", "issues": [
                               {"block_ids": [1], "bbox": {"x": 0, "y": 0, "w": 0, "h": 0},
                                "severity": "critical", "category": "용어", "title": "t", "reason": "r",
                                "suggestion": "s", "persona_reaction": "?"}]}]}, calls)

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(providers, "_anthropic_client", lambda key: fake)
    monkeypatch.setattr(analyzer, "PAGES_PER_CALL", 1)
    progress = []
    result = analyzer.analyze(doc, path, get_persona("general-citizen"), "주민설명회",
                              progress=lambda d, t: progress.append((d, t)))
    assert result["mode"] == "ai"
    assert "Claude" in result["engine"]
    assert len(calls) == 3  # 페이지 2묶음 + 종합 1회
    assert calls[0]["model"] == providers.ANTHROPIC_DEFAULT_MODEL
    assert calls[0]["fallbacks"] == "default"
    assert "주민설명회" in calls[0]["messages"][0]["content"][0]["text"]
    assert result["overall"]["score"] == 42
    assert [p["issues"][0]["block_ids"] for p in result["pages"]] == [[1], [1]]
    assert progress[-1] == (3, 3)


def test_refusal_raises(briefing, monkeypatch):
    doc, path = briefing

    class Refusing(FakeStream):
        def get_final_message(self):
            return SimpleNamespace(stop_reason="refusal", content=[])

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=lambda **k: Refusing({}, []))))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(providers, "_anthropic_client", lambda key: fake)
    with pytest.raises(analyzer.AnalysisError):
        analyzer.analyze(doc, path, get_persona("general-citizen"))
