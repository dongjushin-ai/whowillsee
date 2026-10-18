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

from . import providers
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
            progress: ProgressFn | None = None) -> dict:
    """문서 메타(doc)와 페르소나로 분석 결과 dict 를 만든다."""
    progress = progress or (lambda done, total: None)
    pages = doc["pages"]
    try:
        provider = providers.get_provider()
    except providers.ProviderError as exc:
        raise AnalysisError(str(exc)) from exc
    if provider:
        raw = _analyze_with_ai(provider, pages, doc_path, persona, context, progress)
        mode, engine = "ai", provider.engine
    else:
        raw, mode, engine = _analyze_demo(pages, persona), "demo", None
        progress(1, 1)
    return finalize(raw, pages, persona, mode, engine, context)


def finalize(raw: dict, pages: list[dict], persona: Persona, mode: str,
             engine: str | None, context: str) -> dict:
    """모델/휴리스틱의 원시 출력에 정확한 박스 좌표와 일련번호를 붙인다."""
    by_page = {p["index"]: p for p in pages}
    out_pages, counter = [], 0
    raw_pages = {rp.get("page"): rp for rp in raw.get("pages", [])}
    for index in sorted(by_page):
        rp = raw_pages.get(index, {})
        issues = []
        for issue in rp.get("issues", []):
            severity = issue.get("severity") if issue.get("severity") in SEVERITIES else "warning"
            box, used = resolve_box(issue, by_page[index].get("blocks", []))
            counter += 1
            issues.append({
                "n": counter,
                "page": index,
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
        issues.sort(key=lambda i: (order[i["severity"]], i["box"]["y"], i["box"]["x"]))
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
        "severity": {"type": "string", "enum": SEVERITIES},
        "category": {"type": "string", "enum": CATEGORIES},
        "title": {"type": "string"},
        "reason": {"type": "string"},
        "suggestion": {"type": "string"},
        "persona_reaction": {"type": "string"},
    },
    "required": ["block_ids", "bbox", "severity", "category", "title", "reason",
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

출력
- 모든 문장은 한국어로 씁니다.
- pages 에는 입력으로 받은 모든 페이지를 page 번호와 함께 포함합니다 (문제가 없으면 issues 를 빈 배열로).
- overall.score 는 이 페르소나에게 자료가 얼마나 잘 전달되는지 0~100 점입니다.
- overall.first_impression 은 페르소나가 처음 10초 동안 받는 인상, summary 는 종합 평가, strengths 는 잘된 점, top_fixes 는 가장 먼저 고칠 3~5가지입니다."""


def _page_content(page: dict, doc_path: Path) -> list[dict]:
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


def _intro(persona: Persona, context: str, page_nums: list[int], total: int) -> str:
    parts = ["## 페르소나", persona.to_prompt()]
    if context.strip():
        parts += ["", "## 자료 사용 상황 (발표자가 입력)", context.strip()]
    if len(page_nums) < total:
        parts += ["", f"## 범위\n전체 {total}페이지 중 {page_nums[0]}~{page_nums[-1]}페이지입니다. "
                      "overall 은 이 범위 기준으로 작성하세요."]
    parts += ["", "아래 페이지들을 이 페르소나의 관점에서 검토하세요."]
    return "\n".join(parts)


def _analyze_with_ai(client, pages: list[dict], doc_path: Path, persona: Persona, context: str,
                     progress: ProgressFn) -> dict:
    batches = [pages[i:i + PAGES_PER_CALL] for i in range(0, len(pages), PAGES_PER_CALL)]
    total_steps = len(batches) + (1 if len(batches) > 1 else 0)
    merged_pages, overalls = [], []
    for done, batch in enumerate(batches):
        progress(done, total_steps)
        content = [{"type": "text", "text": _intro(persona, context, [p["index"] for p in batch], len(pages))}]
        for page in batch:
            content += _page_content(page, doc_path)
        result = _call(client, content, RESULT_SCHEMA)
        merged_pages += result.get("pages", [])
        overalls.append(result.get("overall", {}))
    if len(batches) > 1:
        progress(len(batches), total_steps)
        overall = _synthesize(client, persona, context, merged_pages, overalls)
    else:
        overall = overalls[0]
    progress(total_steps, total_steps)
    return {"overall": overall, "pages": merged_pages}


def _synthesize(client, persona: Persona, context: str, pages: list[dict], overalls: list[dict]) -> dict:
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
        "", "위 부분 결과를 바탕으로 자료 전체에 대한 overall 을 작성하세요.",
    ])}]
    return _call(client, content, OVERALL_SCHEMA, max_tokens=8000)


# ====================================================================== 데모 모드

JARGON = ["고도화", "내재화", "정합성", "제고", "거버넌스", "레거시", "마이그레이션", "인터페이스",
          "아키텍처", "플랫폼화", "리팩토링", "파이프라인", "연계", "가명처리", "전송요구권", "정보주체",
          "체계 수립", "추진체계", "실효성", "선제적", "디지털 트윈", "레이크하우스", "interoperability"]
MISSING = re.compile(r"미정|추후|TBD|미기재|별첨|별도\s*절차|확인\s*중|검토\s*중")
ACRONYM = re.compile(r"\b[A-Z][A-Za-z0-9/+-]*[A-Z0-9]\b")
NUMBER = re.compile(r"\d[\d,.]*\s*%?")


def _analyze_demo(pages: list[dict], persona: Persona) -> dict:
    lay = persona.expertise_level <= 2
    mid = persona.expertise_level <= 3
    elderly = persona.digital_literacy <= 1 or "70" in persona.age_group or "고령" in persona.name
    small_limit = 14 if elderly else 10
    out_pages, penalty = [], 0
    for page in pages:
        issues = []
        blocks = page.get("blocks") or []
        if not blocks:
            issues.append(_demo_issue([], "suggestion", "기타", "이미지 내용은 데모 모드에서 분석되지 않습니다",
                                      "데모 모드는 추출된 텍스트만 검사합니다. AI를 연결하면 그림·차트까지 분석합니다.",
                                      "상단 'AI 연결'에서 내 AI를 연결하고 다시 분석하세요.", "이 그림은 무슨 뜻이지?",
                                      bbox={"x": 0, "y": 0, "w": 1, "h": 1}))
        # 아주 작은 글씨(각주 등)는 블록별로, 조금 작은 글씨는 페이지당 하나로 묶어 지적한다
        smallish = [b for b in blocks if b.get("font_size") and 10 <= b["font_size"] < small_limit]
        if smallish:
            sizes = sorted({b["font_size"] for b in smallish})
            issues.append(_demo_issue([b["id"] for b in smallish], "warning", "가독성",
                                      "전반적으로 글자가 작습니다",
                                      f"글자 크기 {sizes[0]}~{sizes[-1]} 인 텍스트가 {len(smallish)}곳 있어 "
                                      f"{persona.name}에게는 읽기 버겁습니다.",
                                      f"본문은 {small_limit + 4} 이상으로 키우고, 표는 핵심 수치만 남기세요.",
                                      "눈이 침침해서 잘 안 보이네."))
        for b in blocks:
            text = b["text"]
            if b.get("font_size") and b["font_size"] < 10:
                issues.append(_demo_issue([b["id"]], "critical" if elderly else "warning", "가독성",
                                          "글자가 너무 작습니다",
                                          f"글자 크기 {b['font_size']}로, {persona.name}에게는 읽기 어렵습니다. "
                                          "중요한 조건이나 연락처가 작은 글씨에 숨어 있으면 신뢰도 떨어집니다.",
                                          f"최소 {small_limit + 4} 이상으로 키우고 본문과 같은 색 대비를 쓰세요.",
                                          "작아서 안 보이는데… 일부러 숨긴 건가?"))
            words = [w for w in JARGON if w.lower() in text.lower()]
            acronyms = sorted(set(ACRONYM.findall(text)))
            if mid and (len(words) + len(acronyms)) >= 2:
                found = ", ".join((words + acronyms)[:6])
                issues.append(_demo_issue([b["id"]], "critical" if lay else "warning", "용어",
                                          "전문용어·약어가 많습니다",
                                          f"'{found}' 같은 표현은 {persona.name}의 전문성 수준"
                                          f"({persona.expertise_level}/5)에서 이해하기 어렵습니다.",
                                          "쉬운 우리말로 바꾸고, 꼭 필요한 용어는 괄호로 한 줄 설명을 붙이세요.",
                                          "무슨 말인지 하나도 모르겠네."))
            if mid and len(text) > 160:
                issues.append(_demo_issue([b["id"]], "warning", "정보과밀", "한 덩어리에 정보가 너무 많습니다",
                                          f"{len(text)}자가 한 블록에 몰려 있어 훑어보는 사람은 핵심을 놓칩니다.",
                                          "핵심 한 문장 + 3개 이하의 짧은 항목으로 나누세요.",
                                          "길어서 안 읽을래."))
            if MISSING.search(text):
                issues.append(_demo_issue([b["id"]], "critical" if lay else "warning", "누락정보",
                                          "정보가 비어 있거나 다른 곳으로 미뤄져 있습니다",
                                          f"'{MISSING.search(text).group(0)}' 표현이 있어 꼭 필요한 정보"
                                          "(일정·방법·근거·연락처)를 이 자료만으로 알 수 없습니다.",
                                          "확정된 내용만 적고, 미정이면 언제 어디서 알 수 있는지 명시하세요.",
                                          "그래서 언제, 어떻게 하라는 거야?"))
            nums = NUMBER.findall(text)
            if lay and len(nums) >= 4:
                issues.append(_demo_issue([b["id"]], "warning", "정보과밀", "숫자가 한꺼번에 너무 많습니다",
                                          f"숫자 {len(nums)}개가 나열되어 무엇이 중요한지 알기 어렵습니다.",
                                          "가장 중요한 숫자 하나를 크게 보여주고 생활 속 의미로 풀어 쓰세요 (예: '1인당 연 3만 원 절약').",
                                          "숫자만 잔뜩이네."))
        weights = {"critical": 12, "warning": 6, "suggestion": 2}
        penalty += sum(weights[i["severity"]] for i in issues)
        issues.sort(key=lambda i: SEVERITIES.index(i["severity"]))
        out_pages.append({"page": page["index"], "page_comment": "", "issues": issues[:8]})
    score = max(5, 100 - penalty // max(len(pages), 1))
    return {
        "overall": {
            "score": score,
            "first_impression": "(데모 모드) 텍스트 규칙만으로 추정한 결과입니다.",
            "summary": f"{persona.name} 관점에서 용어·글자 크기·정보 밀도·누락 정보를 규칙 기반으로 점검했습니다. "
                       "실제 페르소나 시뮬레이션은 상단 'AI 연결'에서 내 AI를 연결하면 동작합니다.",
            "strengths": [],
            "top_fixes": ["전문용어를 쉬운 말로 바꾸기", "작은 글씨 키우기", "미정·별첨 정보 채우기"],
        },
        "pages": out_pages,
    }


def _demo_issue(block_ids, severity, category, title, reason, suggestion, reaction, bbox=None) -> dict:
    return {"block_ids": block_ids, "bbox": bbox or {"x": 0, "y": 0, "w": 0, "h": 0}, "severity": severity,
            "category": category, "title": title, "reason": reason, "suggestion": suggestion,
            "persona_reaction": reaction}
