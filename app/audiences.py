"""청중 그룹: 여러 페르소나를 비율(가중치)로 묶은 실제 청중 구성.

예) 주민설명회 = 일반 시민 60 + 고령 어르신 30 + 기자 10
구성원별 분석 결과를 합쳐 가중 점수를 내고, 여러 청중이 함께 걸려 넘어지는 지적을 묶는다.
"""
from __future__ import annotations

import time
from difflib import SequenceMatcher

from pydantic import BaseModel, Field

from . import storage
from .messages import m as msg
from .personas import get_persona

SEV_RANK = {"critical": 0, "warning": 1, "suggestion": 2}
SEV_WEIGHT = {"critical": 3, "warning": 2, "suggestion": 1}


class Member(BaseModel):
    persona_id: str
    weight: int = Field(ge=1, le=100)


class AudienceIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    icon: str = Field(default="👥", max_length=8)
    members: list[Member] = Field(min_length=1, max_length=8)
    context: str = Field(default="", max_length=2000)  # 기본 발표 상황


class Audience(AudienceIn):
    id: str
    builtin: bool = False


PRESETS = [
    Audience(id="town-hall", builtin=True, icon="🏛️", name="주민설명회",
             members=[Member(persona_id="general-citizen", weight=60), Member(persona_id="senior-citizen", weight=30),
                      Member(persona_id="journalist", weight=10)],
             context="구청 주민설명회 발표. 다양한 연령의 주민이 스크린으로 보고 질의응답이 이어짐."),
    Audience(id="exec-report", builtin=True, icon="📊", name="임원 보고",
             members=[Member(persona_id="busy-executive", weight=70), Member(persona_id="domain-expert", weight=30)],
             context="임원 회의 보고. 10분 안에 결론과 승인 요청 사항을 전달해야 함."),
]


PRESETS_EN = {
    "town-hall": {"name": "Town hall meeting",
                  "context": "A district town hall briefing. Residents of all ages watch on a screen, followed by Q&A."},
    "exec-report": {"name": "Executive briefing",
                    "context": "An executive meeting. You have 10 minutes to deliver the conclusion and the approval request."},
}


def localize(audience: "Audience", lang: str) -> "Audience":
    if lang == "en" and audience.builtin and audience.id in PRESETS_EN:
        return audience.model_copy(update=PRESETS_EN[audience.id])
    return audience


def _path():
    return storage.DATA_DIR / "audiences.json"


def _load_custom() -> list[Audience]:
    return [Audience(**a) for a in storage.read_json(_path(), default=[])]


def _save_custom(items: list[Audience]) -> None:
    storage.write_json(_path(), [a.model_dump() for a in items])


def _validate(data: AudienceIn) -> None:
    ids = [m.persona_id for m in data.members]
    if len(set(ids)) != len(ids):
        raise ValueError("같은 페르소나가 두 번 들어 있습니다.")
    missing = [pid for pid in ids if not get_persona(pid)]
    if missing:
        raise ValueError(f"없는 페르소나입니다: {', '.join(missing)}")


def list_audiences(lang: str = "ko") -> list[Audience]:
    return [localize(a, lang) for a in PRESETS] + _load_custom()


def get_audience(audience_id: str, lang: str = "ko") -> Audience | None:
    return next((a for a in list_audiences(lang) if a.id == audience_id), None)


def create_audience(data: AudienceIn) -> Audience:
    _validate(data)
    items = _load_custom()
    audience = Audience(id="a-" + storage.new_id(), **data.model_dump())
    items.append(audience)
    _save_custom(items)
    return audience


def update_audience(audience_id: str, data: AudienceIn) -> Audience:
    if any(a.id == audience_id for a in PRESETS):
        raise PermissionError("기본 청중 그룹은 수정할 수 없습니다. 복제해서 사용하세요.")
    _validate(data)
    items = _load_custom()
    for i, a in enumerate(items):
        if a.id == audience_id:
            items[i] = Audience(id=audience_id, **data.model_dump())
            _save_custom(items)
            return items[i]
    raise KeyError(audience_id)


def delete_audience(audience_id: str) -> None:
    if any(a.id == audience_id for a in PRESETS):
        raise PermissionError("기본 청중 그룹은 삭제할 수 없습니다.")
    items = _load_custom()
    remaining = [a for a in items if a.id != audience_id]
    if len(remaining) == len(items):
        raise KeyError(audience_id)
    _save_custom(remaining)


# ---------------------------------------------------------------- 종합

def _iou(a: dict, b: dict) -> float:
    x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x1, y1 = min(a["x"] + a["w"], b["x"] + b["w"]), min(a["y"] + a["h"], b["y"] + b["h"])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union else 0.0


def same_problem(a: dict, b: dict) -> bool:
    """서로 다른 페르소나의 지적이 같은 문제를 가리키는지 추정한다."""
    if a["page"] != b["page"] or a.get("target", "slide") != b.get("target", "slide"):
        return False
    shared_blocks = set(a.get("block_ids") or []) & set(b.get("block_ids") or [])
    near = bool(shared_blocks) or (a.get("box") and b.get("box") and _iou(a["box"], b["box"]) >= 0.5)
    if not near:
        return False
    # 같은 위치라도 유형이 다르면 다른 문제로 본다 (제목이 거의 같을 때만 예외)
    return a["category"] == b["category"] or SequenceMatcher(None, a["title"], b["title"]).ratio() >= 0.75


def aggregate(audience: Audience, results: dict[str, dict], lang: str = "ko") -> dict:
    """구성원별 결과(persona_id → result)를 결과와 같은 모양의 dict 로 합친다."""
    weights = {m.persona_id: m.weight for m in audience.members}
    present = [m for m in audience.members if m.persona_id in results]
    total_w = sum(m.weight for m in present) or 1

    clusters: list[dict] = []
    for m in present:
        r = results[m.persona_id]
        for page in r["pages"]:
            for issue in page["issues"]:
                voice = {"persona_id": m.persona_id, "name": r["persona"]["name"], "icon": r["persona"].get("icon", ""),
                         "weight": m.weight, "severity": issue["severity"], "reaction": issue.get("persona_reaction", "")}
                target = next((c for c in clusters if same_problem(c["rep"], issue)), None)
                if target is None:
                    clusters.append({"rep": issue, "rep_weight": m.weight, "voices": [voice]})
                    continue
                if not any(v["persona_id"] == m.persona_id for v in target["voices"]):
                    target["voices"].append(voice)
                # 대표 지적: 더 심각하거나, 같은 심각도면 비중이 큰 청중의 것
                if (SEV_RANK[issue["severity"]], -m.weight) < (SEV_RANK[target["rep"]["severity"]], -target["rep_weight"]):
                    target["rep"], target["rep_weight"] = issue, m.weight

    for c in clusters:
        c["share"] = round(100 * sum(v["weight"] for v in c["voices"]) / total_w)
        c["impact"] = sum(v["weight"] * SEV_WEIGHT[v["severity"]] for v in c["voices"])
    clusters.sort(key=lambda c: (-c["impact"], c["rep"]["page"]))

    pages: dict[int, list[dict]] = {}
    for n, c in enumerate(clusters, 1):
        rep = c["rep"]
        voices = sorted(c["voices"], key=lambda v: -v["weight"])
        pages.setdefault(rep["page"], []).append({
            **{k: rep[k] for k in ("page", "box", "block_ids", "severity", "category", "title", "reason", "suggestion")},
            "target": rep.get("target", "slide"),
            "n": n,
            "persona_reaction": voices[0]["reaction"],
            "voices": voices,
            "share": c["share"],
            "impact": c["impact"],
        })
    page_nums = sorted({p["page"] for r in results.values() for p in r["pages"]})
    out_pages = [{"page": i, "page_comment": "", "issues": pages.get(i, [])} for i in page_nums]

    score = round(sum(results[m.persona_id]["overall"]["score"] * m.weight for m in present) / total_w)
    counts = {s: sum(1 for c in clusters if c["rep"]["severity"] == s) for s in SEV_RANK}
    shared = [c for c in clusters if len(c["voices"]) > 1]
    member_rows = [{"persona_id": m.persona_id, "name": results[m.persona_id]["persona"]["name"],
                    "icon": results[m.persona_id]["persona"].get("icon", ""), "weight": m.weight,
                    "share": round(100 * m.weight / total_w), "score": results[m.persona_id]["overall"]["score"]}
                   for m in present]
    worst = min(member_rows, key=lambda r: r["score"]) if member_rows else None
    summary = msg(lang, "aud.summary", count=len(present), shared=len(shared))
    if worst:
        summary += msg(lang, "aud.worst", **worst)
    engines = {r.get("engine") for r in results.values()}
    return {
        "persona": {"id": f"aud:{audience.id}", "name": audience.name, "icon": audience.icon},
        "audience": {"id": audience.id, "name": audience.name, "icon": audience.icon, "members": member_rows,
                     "missing": [m.persona_id for m in audience.members if m.persona_id not in results]},
        "mode": "audience",
        "engine": engines.pop() if len(engines) == 1 else msg(lang, "aud.engines"),
        "context": next((r.get("context") for r in results.values() if r.get("context")), ""),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "overall": {
            "score": score,
            "first_impression": "",
            "summary": summary,
            "strengths": [],
            "top_fixes": [msg(lang, "aud.fix", title=c["rep"]["title"], share=c["share"]) for c in clusters[:5]],
            "counts": counts,
            "timing": next((r["overall"].get("timing") for r in results.values() if r["overall"].get("timing")), None),
            # 영상 피드백은 페르소나별이라 비중이 가장 큰 구성원의 것을 보여준다
            "video": next((results[m.persona_id]["overall"].get("video") for m in sorted(present, key=lambda m: -m.weight)
                           if results[m.persona_id]["overall"].get("video")), None),
        },
        "pages": out_pages,
        "weights": weights,
    }
