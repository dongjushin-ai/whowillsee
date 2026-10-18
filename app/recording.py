"""발표 녹음: 받아쓴 구간을 페이지에 나누고, 말하기 지표를 잰다.

페이지 나누기
- AI 가 연결되어 있으면 AI 에게 각 구간이 어느 페이지 설명인지 묻는다.
- 없거나 실패하면 '슬라이드 글과 겹치는 말' + '진행 위치'로 순서를 지키며 나눈다 (동적 계획법).
"""
from __future__ import annotations

import re
from collections import Counter

from . import providers

LONG_PAUSE = 2.5  # 초. 이보다 긴 침묵을 '긴 멈춤'으로 센다
# 받아쓰기가 남기는 대표적인 말버릇 (음성 인식기가 일부를 지우기도 한다)
FILLERS = ["음", "어", "아", "그", "저", "이제", "약간", "뭐", "그러니까", "막", "좀", "사실"]
_FILLER_RE = re.compile(r"^(음+|어+|아+|그+|저+|이제|약간|뭐|그러니까|막|좀|사실)[,.!?…]*$")


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"\s+", "", text.lower())
    return {t[i:i + 2] for i in range(len(t) - 1)}


def page_texts(meta: dict) -> dict[int, str]:
    return {p["index"]: " ".join(b["text"] for b in p.get("blocks", [])) for p in meta["pages"]}


def align_dp(segments: list[dict], pages: dict[int, str], position_weight: float = 0.35) -> list[int]:
    """각 구간의 페이지 번호 (페이지 순서를 거스르지 않음)."""
    if not segments:
        return []
    page_ids = sorted(pages)
    n_seg, n_page = len(segments), len(page_ids)
    grams = [_bigrams(pages[p]) for p in page_ids]
    total = segments[-1]["end"] or 1.0
    score = []
    for s in segments:
        sg = _bigrams(s["text"])
        mid = (s["start"] + s["end"]) / 2 / total * n_page  # 시간상 예상 위치
        row = []
        for j in range(n_page):
            overlap = len(sg & grams[j]) / (len(sg) or 1)
            row.append(overlap - position_weight * abs((j + 0.5) - mid) / n_page)
        score.append(row)
    # dp[i][j]: i번째 구간을 j페이지에 둘 때 최대 점수 (이전 구간은 j 이하 페이지)
    neg = float("-inf")
    dp = [[neg] * n_page for _ in range(n_seg)]
    back = [[0] * n_page for _ in range(n_seg)]
    dp[0] = score[0][:]
    for i in range(1, n_seg):
        best, arg = neg, 0
        for j in range(n_page):
            if dp[i - 1][j] > best:
                best, arg = dp[i - 1][j], j
            dp[i][j] = score[i][j] + best
            back[i][j] = arg
    j = max(range(n_page), key=lambda k: dp[-1][k])
    path = [0] * n_seg
    for i in range(n_seg - 1, -1, -1):
        path[i] = page_ids[j]
        j = back[i][j]
    return path


_ALIGN_SCHEMA = {
    "type": "object",
    "properties": {"segment_pages": {"type": "array", "items": {"type": "integer"}}},
    "required": ["segment_pages"],
    "additionalProperties": False,
}


def align_ai(provider, segments: list[dict], pages: dict[int, str]) -> list[int]:
    lines = ["## 슬라이드 내용"]
    lines += [f"[페이지 {p}] {pages[p][:600]}" for p in sorted(pages)]
    lines += ["", "## 발표 녹음 받아쓰기 (구간 번호: 시작초 내용)"]
    lines += [f"{i}: {s['start']:.0f}s {s['text'][:200]}" for i, s in enumerate(segments)]
    lines += ["", f"각 구간(0~{len(segments) - 1})이 어느 페이지를 띄워 놓고 한 말인지 segment_pages 배열로 답하세요. "
                  "배열 길이는 구간 수와 같아야 하고, 발표는 앞 페이지부터 진행되므로 페이지 번호는 줄어들지 않습니다."]
    raw = provider.complete_json("당신은 발표 녹음과 슬라이드를 맞추는 편집자입니다. JSON 으로만 답합니다.",
                                 [{"type": "text", "text": "\n".join(lines)}], _ALIGN_SCHEMA, max_tokens=8000)
    result = [int(x) for x in raw.get("segment_pages") or []]
    if len(result) != len(segments):
        raise ValueError("구간 수와 맞지 않음")
    # 범위를 맞추고 순서가 거꾸로 가지 않게 보정한다
    lo, hi = min(pages), max(pages)
    fixed, current = [], lo
    for p in result:
        current = max(current, min(max(p, lo), hi))
        fixed.append(current)
    return fixed


def align(meta: dict, segments: list[dict]) -> tuple[list[int], str]:
    """(구간별 페이지, 사용한 방법)."""
    pages = page_texts(meta)
    try:
        provider = providers.get_provider()
    except providers.ProviderError:
        provider = None
    if provider and segments:
        try:
            return align_ai(provider, segments, pages), "ai"
        except (providers.ProviderError, ValueError, TypeError):
            pass
    return align_dp(segments, pages), "auto"


def _chars(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def fillers(text: str) -> Counter:
    found = Counter()
    for word in text.split():
        m = _FILLER_RE.match(word)
        if m:
            w = m.group(1)
            found[w[0] if w[0] in "음어아그저" and len(set(w)) == 1 else w] += 1
    return found


def analyze(meta: dict, transcript: dict) -> dict:
    """받아쓰기 결과로 페이지별 대본·실제 시간·말하기 지표를 만든다."""
    segments = transcript.get("segments") or []
    if not segments and transcript.get("text"):
        # 타임스탬프가 없는 서버: 전체를 한 구간으로 보고 길이만 쓴다
        segments = [{"start": 0.0, "end": float(transcript.get("duration") or 0), "text": transcript["text"]}]
    assignment, method = align(meta, segments)
    page_ids = [p["index"] for p in meta["pages"]]
    texts = {i: [] for i in page_ids}
    seconds = {i: 0.0 for i in page_ids}
    for k, (seg, page) in enumerate(zip(segments, assignment)):
        texts[page].append(seg["text"])
        nxt = segments[k + 1]["start"] if k + 1 < len(segments) else seg["end"]
        seconds[page] += max(0.0, nxt - seg["start"])  # 다음 구간 전까지의 침묵도 그 페이지 시간으로
    duration = float(transcript.get("duration") or (segments[-1]["end"] if segments else 0))
    pauses = []
    for a, b in zip(segments, segments[1:]):
        gap = b["start"] - a["end"]
        if gap >= LONG_PAUSE:
            pauses.append({"at": round(a["end"], 1), "seconds": round(gap, 1)})
    full_text = " ".join(s["text"] for s in segments)
    speaking = sum(s["end"] - s["start"] for s in segments) or duration
    per_page = []
    for i in page_ids:
        t = " ".join(texts[i])
        f = fillers(t)
        per_page.append({"page": i, "seconds": round(seconds[i], 1), "chars": _chars(t),
                         "rate": round(_chars(t) / (seconds[i] / 60)) if seconds[i] > 5 else None,
                         "fillers": sum(f.values())})
    filler_counts = fillers(full_text)
    timed = bool(transcript.get("segments"))
    return {
        "duration_seconds": round(duration, 1),
        "timed": timed,
        "align_method": method if timed else "none",
        "rate": round(_chars(full_text) / (speaking / 60)) if speaking > 5 else None,  # 공백 제외 글자/분
        "fillers": dict(filler_counts.most_common(8)),
        "filler_total": sum(filler_counts.values()),
        "fillers_per_minute": round(sum(filler_counts.values()) / (duration / 60), 1) if duration > 5 else None,
        "long_pauses": pauses,
        "per_page": per_page,
        "page_texts": {str(i): " ".join(texts[i]) for i in page_ids if texts[i]},
    }


def delivery_summary(rec: dict | None) -> dict | None:
    """분석 결과(overall.delivery)에 넣을 요약."""
    if not rec:
        return None
    m = rec["metrics"]
    return {k: m.get(k) for k in ("duration_seconds", "rate", "fillers", "filler_total", "fillers_per_minute",
                                  "timed", "align_method")} | {"long_pauses": len(m.get("long_pauses", [])),
                                                               "filename": rec.get("filename")}
