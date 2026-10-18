"""페르소나 관점에서 자료를 분석해 지적사항(박스 + 메모)을 만든다.

- 사용자가 연결한 AI(providers.py: Claude 또는 OpenAI 호환)가 있으면 그 AI 로 분석한다.
- 없으면 텍스트 블록 기반 휴리스틱 '데모 모드'로 동작한다.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Callable

from . import providers, recording, video
from .messages import m, norm_lang
from . import script as scripts
from .personas import Persona

PAGES_PER_CALL = int(os.environ.get("WHOWILLSEE_PAGES_PER_CALL", "6"))
MAX_BLOCK_CHARS = 400

SEVERITIES = ["critical", "warning", "suggestion"]
CATEGORIES = ["용어", "정보과밀", "가독성", "논리·구성", "신뢰성·근거", "시각디자인",
              "니즈불일치", "누락정보", "톤·표현", "접근성", "기타"]

ProgressFn = Callable[[int, int], None]


class AnalysisError(Exception):
    pass


# ====================================================================== 공통

def analyze(doc: dict, doc_path: Path, persona: Persona, context: str = "",
            progress: ProgressFn | None = None, lang: str = "ko") -> dict:
    """문서 메타(doc)와 페르소나로 분석 결과 dict 를 만든다."""
    progress = progress or (lambda done, total: None)
    lang = norm_lang(lang)
    pages = doc["pages"]
    script = doc.get("script")
    delivery = recording.delivery_summary(doc.get("recording"))
    vsum = video.summary(doc.get("video"))
    try:
        provider = providers.get_provider()
    except providers.ProviderError as exc:
        raise AnalysisError(str(exc)) from exc
    if provider:
        raw = _analyze_with_ai(provider, pages, doc_path, persona, context, progress, script, delivery, lang)
        mode, engine = "ai", provider.engine
        feedback = _video_review_ai(provider, persona, context, vsum, doc_path, lang) if vsum else []
    else:
        raw, mode, engine = _analyze_demo(pages, persona, script, doc.get("recording"), lang), "demo", None
        feedback = _video_review_demo(vsum, persona, lang) if vsum else []
        progress(1, 1)
    timing = scripts.timing(script, [p["index"] for p in pages])
    result = finalize(raw, pages, persona, mode, engine, context, timing, delivery)
    result["lang"] = lang
    if vsum:
        result["overall"]["video"] = {**vsum, "feedback": _normalize_feedback(feedback)}
    return result


def finalize(raw: dict, pages: list[dict], persona: Persona, mode: str,
             engine: str | None, context: str, timing: dict | None = None, delivery: dict | None = None) -> dict:
    """모델/휴리스틱의 원시 출력에 정확한 박스 좌표와 일련번호를 붙인다."""
    by_page = {p["index"]: p for p in pages}
    out_pages, counter = [], 0
    raw_pages = {rp.get("page"): rp for rp in raw.get("pages", [])}
    for index in sorted(by_page):
        rp = raw_pages.get(index, {})
        issues = []
        for issue in rp.get("issues", []):
            severity = issue.get("severity") if issue.get("severity") in SEVERITIES else "warning"
            target = "script" if issue.get("target") == "script" else "slide"
            # 대본 지적은 화면 위치가 없다
            box, used = (None, []) if target == "script" else resolve_box(issue, by_page[index].get("blocks", []))
            counter += 1
            issues.append({
                "n": counter,
                "page": index,
                "target": target,
                "box": box,
                "block_ids": used,
                "severity": severity,
                "category": issue.get("category") or "기타",
                "title": (issue.get("title") or "").strip(),
                "reason": (issue.get("reason") or "").strip(),
                "suggestion": (issue.get("suggestion") or "").strip(),
                "persona_reaction": (issue.get("persona_reaction") or "").strip(),
            })
        order = {s: i for i, s in enumerate(SEVERITIES)}
        issues.sort(key=lambda i: (i["target"] == "script", order[i["severity"]],
                                   i["box"]["y"] if i["box"] else 0, i["box"]["x"] if i["box"] else 0))
        out_pages.append({"page": index, "page_comment": rp.get("page_comment", ""), "issues": issues})
    # 정렬 후 번호를 다시 매겨 화면 순서와 맞춘다
    n = 0
    for p in out_pages:
        for issue in p["issues"]:
            n += 1
            issue["n"] = n
    overall = raw.get("overall") or {}
    counts = {s: sum(1 for p in out_pages for i in p["issues"] if i["severity"] == s) for s in SEVERITIES}
    return {
        "persona": persona.model_dump(),
        "mode": mode,
        "engine": engine,
        "context": context,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "overall": {
            "score": _clamp_int(overall.get("score", 0), 0, 100),
            "first_impression": overall.get("first_impression", ""),
            "summary": overall.get("summary", ""),
            "strengths": list(overall.get("strengths") or []),
            "top_fixes": list(overall.get("top_fixes") or []),
            "counts": counts,
            "timing": timing,
            "delivery": delivery,
        },
        "pages": out_pages,
    }


def resolve_box(issue: dict, blocks: list[dict]) -> tuple[dict, list[int]]:
    """block_ids 가 유효하면 블록 영역의 합집합, 아니면 모델이 준 bbox 를 쓴다."""
    by_id = {b["id"]: b for b in blocks}
    used = [i for i in issue.get("block_ids") or [] if isinstance(i, int) and i in by_id]
    if used:
        sel = [by_id[i] for i in used]
        x0 = min(b["x"] for b in sel)
        y0 = min(b["y"] for b in sel)
        x1 = max(b["x"] + b["w"] for b in sel)
        y1 = max(b["y"] + b["h"] for b in sel)
        pad = 0.006
        return _box(x0 - pad, y0 - pad, x1 + pad, y1 + pad), used
    bb = issue.get("bbox") or {}
    try:
        x, y, w, h = (float(bb.get(k, 0)) for k in ("x", "y", "w", "h"))
    except (TypeError, ValueError):
        x = y = w = h = 0.0
    if max(x, y, w, h) > 1.5:  # 혹시 0~1000 같은 스케일로 오면 보정
        x, y, w, h = x / 1000, y / 1000, w / 1000, h / 1000
    if w <= 0 or h <= 0:
        return _box(0.0, 0.0, 1.0, 1.0), []
    return _box(x, y, x + w, y + h), []


def _box(x0: float, y0: float, x1: float, y1: float) -> dict:
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(1.0, x1), min(1.0, y1)
    return {"x": round(x0, 4), "y": round(y0, 4), "w": round(max(x1 - x0, 0.01), 4),
            "h": round(max(y1 - y0, 0.01), 4)}


def _clamp_int(v, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return lo


# ====================================================================== Claude

ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "block_ids": {"type": "array", "items": {"type": "integer"}},
        "bbox": {
            "type": "object",
            "properties": {k: {"type": "number"} for k in ("x", "y", "w", "h")},
            "required": ["x", "y", "w", "h"],
            "additionalProperties": False,
        },
        "target": {"type": "string", "enum": ["slide", "script"]},
        "severity": {"type": "string", "enum": SEVERITIES},
        "category": {"type": "string", "enum": CATEGORIES},
        "title": {"type": "string"},
        "reason": {"type": "string"},
        "suggestion": {"type": "string"},
        "persona_reaction": {"type": "string"},
    },
    "required": ["block_ids", "bbox", "target", "severity", "category", "title", "reason",
                 "suggestion", "persona_reaction"],
    "additionalProperties": False,
}

OVERALL_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "first_impression": {"type": "string"},
        "summary": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "top_fixes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "first_impression", "summary", "strengths", "top_fixes"],
    "additionalProperties": False,
}

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "overall": OVERALL_SCHEMA,
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer"},
                    "page_comment": {"type": "string"},
                    "issues": {"type": "array", "items": ISSUE_SCHEMA},
                },
                "required": ["page", "page_comment", "issues"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overall", "pages"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """당신은 발표·브리핑 자료를 특정 청중(페르소나)의 눈으로 읽어 보는 커뮤니케이션 검토 전문가입니다.
주어진 페르소나가 되어 자료를 실제로 읽는다고 상상하고, 그 사람이 막히거나 오해하거나 불편해하거나 신뢰를 잃는 지점을 찾아냅니다.

원칙
- 페르소나의 전문성, 직군, 니즈, 취향, 성격, 자료를 보는 상황을 모두 반영합니다. 같은 자료라도 페르소나가 다르면 지적도 달라야 합니다.
- 일반적인 디자인 훈수보다 "이 사람에게" 실제로 문제가 되는 것을 우선합니다. 사소한 것을 억지로 만들지 않습니다.
- 각 지적은 구체적이어야 합니다: 무엇이(title), 왜 이 페르소나에게 문제인지(reason), 어떻게 고치면 되는지(suggestion, 가능하면 바꿀 문구 예시), 이 사람의 속마음 한마디(persona_reaction, 1인칭 구어체).
- severity: critical = 이해 불가·오해·신뢰 상실·핵심 니즈 미충족, warning = 이해를 방해함, suggestion = 개선하면 더 좋음.
- 페이지당 지적은 중요한 것 위주로 최대 8개.

위치 지정
- 각 페이지에는 추출된 텍스트 블록 목록이 [B번호] 와 좌표로 주어질 수 있습니다. 지적 대상이 텍스트라면 해당 블록 번호를 block_ids 에 넣으세요 (가장 정확합니다). 대상이 아닌 블록은 넣지 마세요.
- 그림·차트·표·여백처럼 블록이 없거나 블록 일부만 해당되는 경우 block_ids 는 비우고 bbox 로 위치를 지정하세요.
- bbox 는 항상 채웁니다. 페이지 이미지 기준 좌상단 (0,0), 우하단 (1,1) 인 정규화 좌표 x, y, w, h 입니다.
- 페이지 전체에 해당하는 문제라면 bbox 를 {x:0,y:0,w:1,h:1} 로 하세요.
- 화면(슬라이드)에 대한 지적은 target 을 "slide" 로 합니다.

발표 대본
- 일부 페이지에는 발표자가 그 페이지를 띄워 놓고 말할 대본이 함께 주어집니다.
- 페르소나가 그 말을 "귀로 듣는다"고 생각하고 평가하세요: 한 번에 따라가기 어려운 긴 문장, 들어서는 모르는 용어·약어,
  화면과 말이 어긋나는 부분, 페르소나가 궁금해할 핵심을 말하지 않는 부분, 톤 문제 등.
- 대본에 대한 지적은 target 을 "script" 로 하고 block_ids 는 비우고 bbox 는 {x:0,y:0,w:0,h:0} 로 하세요.
  suggestion 에는 고쳐 말할 문장 예시를 쓰세요.
- 대본이 없는 페이지에는 대본 지적을 만들지 마세요.

출력
- 모든 문장은 한국어로 씁니다.
- pages 에는 입력으로 받은 모든 페이지를 page 번호와 함께 포함합니다 (문제가 없으면 issues 를 빈 배열로).
- overall.score 는 이 페르소나에게 자료가 얼마나 잘 전달되는지 0~100 점입니다.
- overall.first_impression 은 페르소나가 처음 10초 동안 받는 인상, summary 는 종합 평가, strengths 는 잘된 점, top_fixes 는 가장 먼저 고칠 3~5가지입니다."""


def _page_content(page: dict, doc_path: Path, script_text: str = "") -> list[dict]:
    lines = [f"=== 페이지 {page['index']} ==="]
    blocks = page.get("blocks") or []
    if blocks:
        lines.append("텍스트 블록 (x, y, w, h 는 0~1 정규화 좌표):")
        for b in blocks:
            text = b["text"].replace("\n", " / ")
            if len(text) > MAX_BLOCK_CHARS:
                text = text[:MAX_BLOCK_CHARS] + "…"
            size = f", 글자크기 {b['font_size']}" if b.get("font_size") else ""
            lines.append(f"[B{b['id']}] ({b['x']:.3f}, {b['y']:.3f}, {b['w']:.3f}, {b['h']:.3f}{size}) {text}")
    else:
        lines.append("텍스트 블록 없음 — 이미지에서 직접 위치를 bbox 로 지정하세요.")
    if script_text:
        lines.append("발표 대본 (이 페이지를 띄워 놓고 말할 내용):")
        lines.append(script_text[:4000])
    data = base64.standard_b64encode((doc_path / page["image"]).read_bytes()).decode()
    return [
        {"type": "text", "text": "\n".join(lines)},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
    ]


def _call(provider, content: list[dict], schema: dict, max_tokens: int = 32000) -> dict:
    try:
        return provider.complete_json(SYSTEM_PROMPT, content, schema, max_tokens)
    except providers.ProviderError as exc:
        raise AnalysisError(str(exc)) from exc


def _intro(persona: Persona, context: str, page_nums: list[int], total: int, timing: dict | None = None,
           delivery: dict | None = None) -> str:
    parts = ["## 페르소나", persona.to_prompt()]
    if context.strip():
        parts += ["", "## 자료 사용 상황 (발표자가 입력)", context.strip()]
    if timing:
        target = f", 목표 {timing['target_minutes']}분" if timing.get("target_minutes") else ""
        parts += ["", f"## 발표 시간\n대본 기준 예상 {timing['est_minutes']}분{target}. "
                      "목표를 넘으면 무엇을 줄일지 페르소나 관점에서 지적하세요."]
    if delivery:
        fill = ", ".join(f"{k} {v}회" for k, v in (delivery.get("fillers") or {}).items()) or "거의 없음"
        parts += ["", "## 실제 발표 녹음 지표",
                  f"- 실제 발표 시간 {delivery['duration_seconds'] / 60:.1f}분, 말 속도 {delivery.get('rate') or '알 수 없음'}자/분"
                  " (공백 제외, 보통 300~350)",
                  f"- 말버릇: {fill}", f"- 2.5초 넘는 긴 멈춤 {delivery['long_pauses']}회",
                  "대본은 실제 녹음을 받아쓴 것입니다. 이 사람이 듣기에 말하기 방식(속도, 말버릇, 멈춤)이 문제라면 "
                  "가장 관련 있는 페이지에 target 'script' 지적으로 남기세요."]
    if len(page_nums) < total:
        parts += ["", f"## 범위\n전체 {total}페이지 중 {page_nums[0]}~{page_nums[-1]}페이지입니다. "
                      "overall 은 이 범위 기준으로 작성하세요."]
    parts += ["", "아래 페이지들을 이 페르소나의 관점에서 검토하세요. 대본이 있는 페이지는 대본도 함께 검토하세요."]
    return "\n".join(parts)


def _analyze_with_ai(client, pages: list[dict], doc_path: Path, persona: Persona, context: str,
                     progress: ProgressFn, script: dict | None = None, delivery: dict | None = None,
                     lang: str = "ko") -> dict:
    batches = [pages[i:i + PAGES_PER_CALL] for i in range(0, len(pages), PAGES_PER_CALL)]
    timing = scripts.timing(script, [p["index"] for p in pages])
    total_steps = len(batches) + (1 if len(batches) > 1 else 0)
    merged_pages, overalls = [], []
    for done, batch in enumerate(batches):
        progress(done, total_steps)
        intro = _intro(persona, context, [p["index"] for p in batch], len(pages), timing, delivery)
        content = [{"type": "text", "text": intro + "\n\n" + m(lang, "ai.output_language")}]
        for page in batch:
            content += _page_content(page, doc_path, scripts.page_text(script, page["index"]))
        result = _call(client, content, RESULT_SCHEMA)
        merged_pages += result.get("pages", [])
        overalls.append(result.get("overall", {}))
    if len(batches) > 1:
        progress(len(batches), total_steps)
        overall = _synthesize(client, persona, context, merged_pages, overalls, lang)
    else:
        overall = overalls[0]
    progress(total_steps, total_steps)
    return {"overall": overall, "pages": merged_pages}


def _synthesize(client, persona: Persona, context: str, pages: list[dict], overalls: list[dict],
                lang: str = "ko") -> dict:
    """여러 묶음으로 나눠 분석한 경우 종합 평가를 한 번 더 만든다 (텍스트만 사용)."""
    digest = {
        "partial_overalls": overalls,
        "issues": [{"page": p.get("page"), "severity": i.get("severity"), "title": i.get("title")}
                   for p in pages for i in p.get("issues", [])],
    }
    content = [{"type": "text", "text": "\n".join([
        "## 페르소나", persona.to_prompt(),
        "## 자료 사용 상황", context or "(입력 없음)",
        "## 부분 분석 결과",
        json.dumps(digest, ensure_ascii=False),
        "", "위 부분 결과를 바탕으로 자료 전체에 대한 overall 을 작성하세요.", m(lang, "ai.output_language"),
    ])}]
    return _call(client, content, OVERALL_SCHEMA, max_tokens=8000)


# ====================================================================== 발표 영상

VIDEO_CATEGORIES = ["시선", "표정", "자세", "손동작", "목소리·속도", "화면구성", "복장·배경", "기타"]
VIDEO_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "severity": {"type": "string", "enum": SEVERITIES},
            "category": {"type": "string", "enum": VIDEO_CATEGORIES},
            "title": {"type": "string"}, "reason": {"type": "string"}, "suggestion": {"type": "string"},
            "persona_reaction": {"type": "string"}, "at_seconds": {"type": "number"},
        },
        "required": ["severity", "category", "title", "reason", "suggestion", "persona_reaction", "at_seconds"],
        "additionalProperties": False,
    }}},
    "required": ["items"],
    "additionalProperties": False,
}


def _video_metrics_text(v: dict) -> str:
    away = f", {v['longest_away_at']:.0f}초부터" if v.get("longest_away_at") is not None else ""
    return "\n".join([
        f"- 영상 길이 {v['duration_seconds'] / 60:.1f}분",
        f"- 정면 얼굴이 보인 장면 {v['frontal_ratio'] * 100:.0f}% (카메라·청중을 보는 정도의 대략적 추정)",
        f"- 얼굴이 화면 안에 있는 장면 {v['visible_ratio'] * 100:.0f}%",
        f"- 정면을 보지 않은 가장 긴 구간 {v['longest_away_seconds']:.0f}초{away}",
        f"- 얼굴 크기(화면 높이 대비) {v['face_size'] if v.get('face_size') is not None else '알 수 없음'}",
        f"- 평균 밝기 {v['brightness']:.2f} (0 어두움 ~ 1 밝음), 좌우 움직임 {v['movement']:.3f}",
    ])


def _video_review_ai(provider, persona: Persona, context: str, v: dict, doc_path: Path,
                     lang: str = "ko") -> list[dict]:
    content = [{"type": "text", "text": "\n".join([
        "## 페르소나", persona.to_prompt(),
        *(["## 자료 사용 상황", context] if context.strip() else []),
        "## 발표 영상 지표 (자동 측정)", _video_metrics_text(v),
        "", "아래는 발표 영상의 대표 장면입니다. 이 페르소나가 이 발표를 직접 본다면 발표자의 시선, 표정, 자세, 손동작,"
            " 화면 구성(조명·거리·배경)에서 무엇이 신경 쓰일지 지적하세요. 잘하는 점은 지적으로 만들지 마세요."
            " at_seconds 는 그 문제가 잘 보이는 장면의 시간(초)입니다. 최대 6개.", m(lang, "ai.output_language"),
    ])}]
    for k in v.get("keyframes", []):
        path = doc_path / "video" / k["image"]
        if path.exists():
            content.append({"type": "text", "text": f"장면 {k['t']:.0f}초"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.standard_b64encode(path.read_bytes()).decode()}})
    try:
        return provider.complete_json(SYSTEM_PROMPT, content, VIDEO_SCHEMA, max_tokens=8000).get("items", [])
    except providers.ProviderError as exc:
        raise AnalysisError(f"영상 평가 중 오류: {exc}") from exc


def _video_review_demo(v: dict, persona: Persona, lang: str = "ko") -> list[dict]:
    out = []

    def add(key, severity, category, at=0.0, **reason):
        out.append({"severity": severity, "category": category, "title": m(lang, f"{key}.title"),
                    "reason": m(lang, f"{key}.reason", **reason), "suggestion": m(lang, f"{key}.suggestion"),
                    "persona_reaction": m(lang, f"{key}.reaction"), "at_seconds": at})

    fr = v["frontal_ratio"]
    if fr < 0.5:
        add("vid.gaze", "critical" if fr < 0.3 else "warning", "시선", v.get("longest_away_at") or 0.0,
            pct=f"{fr * 100:.0f}", persona=persona.name)
    elif v["longest_away_seconds"] >= 20:
        add("vid.away", "warning", "시선", v.get("longest_away_at") or 0.0,
            at=f"{v['longest_away_at']:.0f}", seconds=f"{v['longest_away_seconds']:.0f}")
    if v["visible_ratio"] < 0.7:
        add("vid.offscreen", "warning", "화면구성", v.get("first_away_at") or 0.0, pct=f"{v['visible_ratio'] * 100:.0f}")
    if v.get("face_size") is not None and v["face_size"] < 0.08:
        add("vid.small", "suggestion", "화면구성")
    if v["brightness"] < 0.3:
        add("vid.dark", "warning", "화면구성", value=f"{v['brightness']:.2f}")
    if v["movement"] > 0.15:
        add("vid.move", "suggestion", "자세")
    return out


def _normalize_feedback(items: list[dict]) -> list[dict]:
    out = []
    order = {s: i for i, s in enumerate(SEVERITIES)}
    for item in items:
        severity = item.get("severity") if item.get("severity") in SEVERITIES else "warning"
        try:
            at = max(0.0, float(item.get("at_seconds") or 0))
        except (TypeError, ValueError):
            at = 0.0
        out.append({"severity": severity, "category": item.get("category") or "기타",
                    "title": (item.get("title") or "").strip(), "reason": (item.get("reason") or "").strip(),
                    "suggestion": (item.get("suggestion") or "").strip(),
                    "persona_reaction": (item.get("persona_reaction") or "").strip(), "at_seconds": round(at, 1)})
    out.sort(key=lambda i: (order[i["severity"]], i["at_seconds"]))
    for n, item in enumerate(out, 1):
        item["n"] = f"V{n}"
    return out


# ====================================================================== 데모 모드

JARGON = ["고도화", "내재화", "정합성", "제고", "거버넌스", "레거시", "마이그레이션", "인터페이스",
          "아키텍처", "플랫폼화", "리팩토링", "파이프라인", "연계", "가명처리", "전송요구권", "정보주체",
          "체계 수립", "추진체계", "실효성", "선제적", "디지털 트윈", "레이크하우스", "interoperability"]
MISSING = re.compile(r"미정|추후|TBD|미기재|별첨|별도\s*절차|확인\s*중|검토\s*중")
ACRONYM = re.compile(r"\b[A-Z][A-Za-z0-9/+-]*[A-Z0-9]\b")
NUMBER = re.compile(r"\d[\d,.]*\s*%?")


def _analyze_demo(pages: list[dict], persona: Persona, script: dict | None = None, rec: dict | None = None,
                  lang: str = "ko") -> dict:
    lay = persona.expertise_level <= 2
    mid = persona.expertise_level <= 3
    elderly = persona.digital_literacy <= 1 or "70" in persona.age_group or "고령" in persona.name \
        or persona.id == "senior-citizen"
    small_limit = 14 if elderly else 10
    who = persona.name
    out_pages, penalty = [], 0
    for page in pages:
        issues = []
        blocks = page.get("blocks") or []
        if not blocks:
            issues.append(_demo("demo.image", lang, [], "suggestion", "기타", bbox={"x": 0, "y": 0, "w": 1, "h": 1}))
        # 아주 작은 글씨(각주 등)는 블록별로, 조금 작은 글씨는 페이지당 하나로 묶어 지적한다
        smallish = [b for b in blocks if b.get("font_size") and 10 <= b["font_size"] < small_limit]
        if smallish:
            sizes = sorted({b["font_size"] for b in smallish})
            issues.append(_demo("demo.smallish", lang, [b["id"] for b in smallish], "warning", "가독성",
                                reason=dict(lo=sizes[0], hi=sizes[-1], count=len(smallish), persona=who),
                                suggestion=dict(size=small_limit + 4)))
        for b in blocks:
            text = b["text"]
            if b.get("font_size") and b["font_size"] < 10:
                issues.append(_demo("demo.small", lang, [b["id"]], "critical" if elderly else "warning", "가독성",
                                    reason=dict(size=b["font_size"], persona=who), suggestion=dict(size=small_limit + 4)))
            words = [w for w in JARGON if w.lower() in text.lower()]
            acronyms = sorted(set(ACRONYM.findall(text)))
            if mid and (len(words) + len(acronyms)) >= 2:
                issues.append(_demo("demo.jargon", lang, [b["id"]], "critical" if lay else "warning", "용어",
                                    reason=dict(found=", ".join((words + acronyms)[:6]), persona=who,
                                                level=persona.expertise_level)))
            if mid and len(text) > 160:
                issues.append(_demo("demo.dense", lang, [b["id"]], "warning", "정보과밀", reason=dict(chars=len(text))))
            if MISSING.search(text):
                issues.append(_demo("demo.missing", lang, [b["id"]], "critical" if lay else "warning", "누락정보",
                                    reason=dict(word=MISSING.search(text).group(0))))
            nums = NUMBER.findall(text)
            if lay and len(nums) >= 4:
                issues.append(_demo("demo.numbers", lang, [b["id"]], "warning", "정보과밀", reason=dict(count=len(nums))))
        weights = {"critical": 12, "warning": 6, "suggestion": 2}
        penalty += sum(weights[i["severity"]] for i in issues)
        issues += _demo_script_issues(scripts.page_text(script, page["index"]), persona, lay, mid, lang)
        issues.sort(key=lambda i: SEVERITIES.index(i["severity"]))
        out_pages.append({"page": page["index"], "page_comment": "", "issues": issues[:8]})
    timing = scripts.timing(script, [p["index"] for p in pages])
    if timing and timing["over"]:
        longest = max(timing["per_page"], key=lambda p: p["chars"])
        page_out = next(p for p in out_pages if p["page"] == longest["page"])
        page_out["issues"].insert(0, _demo("script.over", lang, [], "warning", "논리·구성", target="script",
                                           reason=dict(est=timing["est_minutes"], target=timing["target_minutes"],
                                                       page_minutes=longest["minutes"])))
        penalty += 6
    for page_no, issue in _demo_delivery_issues(rec, persona, lang):
        target = next((p for p in out_pages if p["page"] == page_no), None)
        if target:
            target["issues"].append(issue)
            penalty += 4
    score = max(5, 100 - penalty // max(len(pages), 1))
    return {
        "overall": {
            "score": score,
            "first_impression": m(lang, "demo.overall.first"),
            "summary": m(lang, "demo.overall.summary", persona=who),
            "strengths": [],
            "top_fixes": [m(lang, f"demo.overall.fix{i}") for i in (1, 2, 3)],
        },
        "pages": out_pages,
    }


def _demo_delivery_issues(rec: dict | None, persona: Persona, lang: str = "ko") -> list[tuple[int, dict]]:
    """녹음 지표(말 속도·말버릇·긴 멈춤)에 대한 규칙 기반 지적. (페이지, 지적) 목록."""
    if not rec:
        return []
    mt = rec["metrics"]
    pages = [p for p in mt["per_page"] if p["seconds"] > 0] or mt["per_page"]
    out = []
    fast_limit = 340 if persona.expertise_level <= 2 or persona.digital_literacy <= 1 else 400
    timed = [p for p in pages if p.get("rate")]
    if mt.get("rate") and mt["rate"] > fast_limit and timed:
        fastest = max(timed, key=lambda p: p["rate"])
        out.append((fastest["page"], _demo("rec.fast", lang, [], "warning", "가독성", target="script",
                                           reason=dict(rate=mt["rate"], page_rate=fastest["rate"], persona=persona.name))))
    elif mt.get("rate") and mt["rate"] < 220 and timed:
        out.append((timed[0]["page"], _demo("rec.slow", lang, [], "suggestion", "톤·표현", target="script",
                                            reason=dict(rate=mt["rate"]))))
    per_min = mt.get("fillers_per_minute") or 0
    if per_min >= 3:
        worst = max(pages, key=lambda p: p["fillers"])
        top = ", ".join(m(lang, "rec.filler.item", word=k, count=v) for k, v in list(mt["fillers"].items())[:3])
        out.append((worst["page"], _demo("rec.filler", lang, [], "warning" if per_min >= 6 else "suggestion", "톤·표현",
                                         target="script", reason=dict(per_min=per_min, top=top))))
    pauses = mt.get("long_pauses") or []
    if len(pauses) >= 3:
        out.append((pages[0]["page"], _demo("rec.pause", lang, [], "suggestion", "논리·구성", target="script",
                                            reason=dict(count=len(pauses), at=f"{pauses[0]['at']:.0f}",
                                                        seconds=pauses[0]["seconds"]))))
    return out


def _demo_script_issues(text: str, persona: Persona, lay: bool, mid: bool, lang: str = "ko") -> list[dict]:
    """대본(귀로 듣는 말)에 대한 규칙 기반 지적."""
    if not text:
        return []
    out = []
    long = scripts.long_sentences(text)
    if long:
        out.append(_demo("script.long", lang, [], "warning", "가독성", target="script",
                         reason=dict(sample=long[0][:40] + "…", persona=persona.name, count=len(long))))
    words = [w for w in JARGON if w.lower() in text.lower()]
    acronyms = sorted(set(ACRONYM.findall(text)))
    if mid and len(words) + len(acronyms) >= 2:
        out.append(_demo("script.jargon", lang, [], "critical" if lay else "warning", "용어", target="script",
                         reason=dict(found=", ".join((words + acronyms)[:6]))))
    if MISSING.search(text):
        out.append(_demo("script.missing", lang, [], "warning", "누락정보", target="script",
                         reason=dict(word=MISSING.search(text).group(0))))
    return out


def _demo_issue(block_ids, severity, category, title, reason, suggestion, reaction, bbox=None, target="slide") -> dict:
    return {"block_ids": block_ids, "bbox": bbox or {"x": 0, "y": 0, "w": 0, "h": 0}, "target": target,
            "severity": severity, "category": category, "title": title, "reason": reason, "suggestion": suggestion,
            "persona_reaction": reaction}


def _demo(key: str, lang: str, block_ids, severity, category, bbox=None, target="slide",
          reason: dict | None = None, suggestion: dict | None = None) -> dict:
    """메시지 카탈로그(messages.py)의 key.title/reason/suggestion/reaction 으로 지적을 만든다."""
    return _demo_issue(block_ids, severity, category, m(lang, f"{key}.title"), m(lang, f"{key}.reason", **(reason or {})),
                       m(lang, f"{key}.suggestion", **(suggestion or {})), m(lang, f"{key}.reaction"),
                       bbox=bbox, target=target)
