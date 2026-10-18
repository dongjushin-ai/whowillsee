"""발표 대본: 저장 형식, 예상 발표 시간, 데모 모드 규칙.

meta["script"] = {"target_minutes": 10 | None, "pages": {"1": "대본…", "2": "…"}}
"""
from __future__ import annotations

import re

# 한국어 발표 말하기 속도 (공백 제외 글자 수 / 분). 보통 300~350자.
CHARS_PER_MINUTE = 330
LONG_SENTENCE = 90  # 듣는 사람이 한 번에 따라가기 어려운 문장 길이 (공백 제외)


def initial(pages: list[dict]) -> dict | None:
    """변환 직후 PPTX 노트가 있으면 그것으로 대본을 채운다."""
    texts = {str(p["index"]): p.get("notes", "") for p in pages if p.get("notes")}
    return {"target_minutes": None, "pages": texts} if texts else None


def page_text(script: dict | None, index: int) -> str:
    return ((script or {}).get("pages") or {}).get(str(index), "").strip()


def _chars(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def timing(script: dict | None, page_indexes: list[int]) -> dict | None:
    if not script or not any(page_text(script, i) for i in page_indexes):
        return None
    actual = script.get("actual_seconds") or {}  # 녹음으로 잰 실제 시간이 있으면 그것을 쓴다
    per_page = []
    for i in page_indexes:
        chars = _chars(page_text(script, i))
        minutes = actual[str(i)] / 60 if str(i) in actual else chars / CHARS_PER_MINUTE
        per_page.append({"page": i, "chars": chars, "minutes": round(minutes, 1)})
    if actual:
        est = round(sum(actual.values()) / 60, 1)
    else:
        est = round(sum(p["chars"] for p in per_page) / CHARS_PER_MINUTE, 1)
    target = script.get("target_minutes")
    return {"est_minutes": est, "target_minutes": target, "per_page": per_page, "measured": bool(actual),
            "over": bool(target) and est > target * 1.1}


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", text) if s.strip()]


def long_sentences(text: str) -> list[str]:
    return [s for s in sentences(text) if _chars(s) > LONG_SENTENCE]
