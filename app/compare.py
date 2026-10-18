"""이전 버전과 수정본의 분석 결과를 비교한다.

지적사항은 버전 간 고유 ID가 없으므로, 유형(category)·제목·가리키는 텍스트의 유사도로
같은 문제인지 추정한다. 결과 화면에도 '추정'임을 밝힌다.
"""
from __future__ import annotations

from difflib import SequenceMatcher

SEV_RANK = {"critical": 0, "warning": 1, "suggestion": 2}
MATCH_THRESHOLD = 0.55


def _issues(meta: dict, result: dict) -> list[dict]:
    blocks = {(p["index"], b["id"]): b["text"] for p in meta["pages"] for b in p.get("blocks", [])}
    out = []
    for page in result["pages"]:
        for issue in page["issues"]:
            text = " ".join(blocks.get((issue["page"], bid), "") for bid in issue.get("block_ids", []))
            out.append({**issue, "_text": text.strip() or issue["reason"]})
    return out


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def similarity(a: dict, b: dict) -> float:
    score = 0.45 * _ratio(a["title"], b["title"]) + 0.55 * _ratio(a["_text"], b["_text"])
    if a["category"] == b["category"]:
        score += 0.2
    return min(score, 1.0)


def _brief(issue: dict) -> dict:
    return {k: issue[k] for k in ("n", "page", "severity", "category", "title")}


def compare(prev_meta: dict, prev_result: dict, cur_meta: dict, cur_result: dict) -> dict:
    prev, cur = _issues(prev_meta, prev_result), _issues(cur_meta, cur_result)
    # 유사도가 높은 쌍부터 짝짓는다 (탐욕적 매칭)
    pairs = sorted(((similarity(p, c), i, j) for i, p in enumerate(prev) for j, c in enumerate(cur)), reverse=True)
    used_prev, used_cur, remaining = set(), set(), []
    for score, i, j in pairs:
        if score < MATCH_THRESHOLD:
            break
        if i in used_prev or j in used_cur:
            continue
        used_prev.add(i)
        used_cur.add(j)
        delta = SEV_RANK[cur[j]["severity"]] - SEV_RANK[prev[i]["severity"]]
        remaining.append({"prev": _brief(prev[i]), "cur": _brief(cur[j]), "similarity": round(score, 2),
                          "change": "better" if delta > 0 else "worse" if delta < 0 else "same"})
    remaining.sort(key=lambda r: r["cur"]["n"])
    resolved = [_brief(p) for i, p in enumerate(prev) if i not in used_prev]
    new = [_brief(c) for j, c in enumerate(cur) if j not in used_cur]
    p_score, c_score = prev_result["overall"]["score"], cur_result["overall"]["score"]
    return {
        "prev": {"doc_id": prev_meta["id"], "filename": prev_meta["filename"], "version": prev_meta.get("version", 1),
                 "score": p_score, "counts": prev_result["overall"]["counts"]},
        "cur": {"doc_id": cur_meta["id"], "filename": cur_meta["filename"], "version": cur_meta.get("version", 1),
                "score": c_score, "counts": cur_result["overall"]["counts"]},
        "delta_score": c_score - p_score,
        "resolved": resolved,
        "remaining": remaining,
        "new": new,
    }
