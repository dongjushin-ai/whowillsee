"""대시보드용 집계."""
from __future__ import annotations

from collections import defaultdict

from . import storage

SEVERITIES = ("critical", "warning", "suggestion")


def _load_all() -> list[tuple[dict, list[dict]]]:
    """(문서 메타, 결과 목록) 쌍. 결과는 페르소나별 최신 1건."""
    out = []
    for d in storage.docs_dir().iterdir():
        meta = storage.read_json(d / "meta.json")
        if not meta:
            continue
        results = []
        for path in sorted((d / "results").glob("*.json")) if (d / "results").exists() else []:
            r = storage.read_json(path)
            if r:
                r["_persona_id"] = path.stem
                results.append(r)
        out.append((meta, results))
    out.sort(key=lambda x: x[0]["created_at"], reverse=True)
    return out


def dashboard() -> dict:
    data = _load_all()
    all_results = [(meta, r) for meta, rs in data for r in rs]

    scores = [r["overall"]["score"] for _, r in all_results]
    sev_total = {s: sum(r["overall"]["counts"][s] for _, r in all_results) for s in SEVERITIES}

    by_persona: dict[str, dict] = {}
    for _, r in all_results:
        pid = r["_persona_id"]
        p = by_persona.setdefault(pid, {"persona_id": pid, "name": r["persona"]["name"],
                                        "icon": r["persona"].get("icon", ""), "scores": []})
        p["scores"].append(r["overall"]["score"])
    personas = []
    for p in by_persona.values():
        personas.append({"persona_id": p["persona_id"], "name": p["name"], "icon": p["icon"],
                         "count": len(p["scores"]), "avg_score": round(sum(p["scores"]) / len(p["scores"]), 1)})
    personas.sort(key=lambda p: p["avg_score"])

    cats: dict[str, dict] = defaultdict(lambda: {s: 0 for s in SEVERITIES})
    for _, r in all_results:
        for page in r["pages"]:
            for issue in page["issues"]:
                cats[issue["category"]][issue["severity"]] += 1
    categories = [{"category": c, **v, "total": sum(v.values())} for c, v in cats.items()]
    categories.sort(key=lambda c: (-c["total"], -c["critical"]))

    matrix_docs = [meta for meta, rs in data if rs][:12]
    matrix_personas = sorted({(r["_persona_id"], r["persona"]["name"], r["persona"].get("icon", ""))
                              for meta, rs in data if meta in matrix_docs for r in rs},
                             key=lambda x: x[1])
    cells = {meta["id"]: {r["_persona_id"]: r["overall"]["score"] for r in rs}
             for meta, rs in data if meta in matrix_docs}

    recent = sorted(all_results, key=lambda x: x[1]["created_at"], reverse=True)[:10]
    return {
        "totals": {
            "documents": len(data),
            "analyses": len(all_results),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            "issues": sum(sev_total.values()),
            **sev_total,
        },
        "personas": personas,
        "categories": categories,
        "matrix": {
            "docs": [{"id": m["id"], "filename": m["filename"], "version": m.get("version", 1)} for m in matrix_docs],
            "personas": [{"id": pid, "name": name, "icon": icon} for pid, name, icon in matrix_personas],
            "cells": cells,
        },
        "recent": [{"doc_id": meta["id"], "filename": meta["filename"], "persona_id": r["_persona_id"],
                    "persona_name": r["persona"]["name"], "icon": r["persona"].get("icon", ""),
                    "score": r["overall"]["score"], "counts": r["overall"]["counts"],
                    "engine": r.get("engine"), "created_at": r["created_at"]} for meta, r in recent],
    }
