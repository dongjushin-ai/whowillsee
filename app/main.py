"""WhoWillSee API 서버.

    uvicorn app.main:app --reload
"""
from __future__ import annotations

import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (analyzer, audiences, compare, converter, exporter, persona_gen, personas, providers, report_html,
               script, stats, storage)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

_executor = ThreadPoolExecutor(max_workers=4)
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
INTERRUPTED = "서버가 다시 시작되어 분석이 중단되었습니다. 다시 분석하세요."


def _jobs_dir() -> Path:
    path = storage.DATA_DIR / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_job(job: dict) -> None:
    """작업 상태를 파일에도 남겨 서버를 재시작해도 결과를 확인할 수 있게 한다."""
    storage.write_json(_jobs_dir() / f"{job['id']}.json", job)


def recover_jobs() -> int:
    """재시작 전에 끝나지 않은 작업을 '중단됨'으로 표시한다. 고친 작업 수를 돌려준다."""
    fixed = 0
    for path in _jobs_dir().glob("*.json"):
        job = storage.read_json(path)
        if not job or job.get("status") != "running":
            continue
        for item in job["items"].values():
            if item["status"] in ("queued", "running"):
                item["status"], item["error"] = "error", INTERRUPTED
        states = {i["status"] for i in job["items"].values()}
        job["status"] = "error" if states == {"error"} else "partial"
        storage.write_json(path, job)
        fixed += 1
    return fixed


@asynccontextmanager
async def lifespan(_app: FastAPI):
    recover_jobs()
    yield


app = FastAPI(title="WhoWillSee", version="1.0", lifespan=lifespan)


def _id_or_404(value: str) -> str:
    try:
        return storage.check_id(value)
    except ValueError:
        raise HTTPException(404, "찾을 수 없습니다.") from None


def _doc_meta(doc_id: str) -> dict:
    meta = storage.read_json(storage.doc_dir(_id_or_404(doc_id)) / "meta.json")
    if not meta:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    return meta


# ------------------------------------------------------------------ 상태

@app.get("/api/status")
def status():
    ai = providers.describe(providers.load_settings())
    return {
        "mode": "ai" if ai["ready"] else "demo",
        "engine": ai["engine"] if ai["ready"] else None,
        "ai": ai,
        "office": converter.soffice_path() is not None,
        "formats": sorted(converter.SUPPORTED_EXTS),
    }


@app.get("/api/stats")
def get_stats():
    return stats.dashboard()


# ------------------------------------------------------------------ AI 연결 설정

@app.get("/api/settings")
def get_settings():
    return providers.describe(providers.load_settings()) | {
        "providers": providers.PROVIDERS, "presets": providers.OPENAI_PRESETS, "efforts": providers.EFFORTS,
    }


@app.put("/api/settings")
def put_settings(update: providers.SettingsUpdate):
    if providers.settings_locked():
        raise HTTPException(403, "이 서버는 AI 설정이 잠겨 있습니다 (WHOWILLSEE_LOCK_SETTINGS).")
    return providers.describe(providers.save_settings(update))


@app.post("/api/settings/test")
def test_settings(update: providers.SettingsUpdate):
    if providers.settings_locked():
        raise HTTPException(403, "이 서버는 AI 설정이 잠겨 있습니다 (WHOWILLSEE_LOCK_SETTINGS).")
    try:
        return providers.test_connection(providers.merge_for_test(update))
    except providers.ProviderError as exc:
        return {"ok": False, "message": str(exc), "latency_ms": 0}


# ------------------------------------------------------------------ 페르소나

@app.get("/api/personas")
def list_personas():
    return [p.model_dump() for p in personas.list_personas()]


@app.post("/api/personas", status_code=201)
def create_persona(data: personas.PersonaIn):
    return personas.create_persona(data).model_dump()


class GenerateRequest(BaseModel):
    description: str = Field(min_length=2, max_length=500)


@app.post("/api/personas/generate")
def generate_persona(req: GenerateRequest):
    """저장하지 않고 초안만 돌려준다 - 사용자가 폼에서 검토 후 저장한다."""
    try:
        persona, engine = persona_gen.generate(req.description)
    except providers.ProviderError as exc:
        raise HTTPException(502, str(exc)) from None
    return {"persona": persona.model_dump(), "engine": engine}


@app.put("/api/personas/{persona_id}")
def update_persona(persona_id: str, data: personas.PersonaIn):
    try:
        return personas.update_persona(persona_id, data).model_dump()
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except KeyError:
        raise HTTPException(404, "페르소나를 찾을 수 없습니다.") from None


@app.delete("/api/personas/{persona_id}", status_code=204)
def delete_persona(persona_id: str):
    used = [a.name for a in audiences.list_audiences() if any(m.persona_id == persona_id for m in a.members)]
    if used:
        raise HTTPException(409, f"청중 그룹에서 쓰고 있는 페르소나입니다: {', '.join(used)}. 그룹에서 먼저 빼주세요.")
    try:
        personas.delete_persona(persona_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except KeyError:
        raise HTTPException(404, "페르소나를 찾을 수 없습니다.") from None
    return Response(status_code=204)


# ------------------------------------------------------------------ 청중 그룹

@app.get("/api/audiences")
def list_audiences():
    return [a.model_dump() for a in audiences.list_audiences()]


@app.post("/api/audiences", status_code=201)
def create_audience(data: audiences.AudienceIn):
    try:
        return audiences.create_audience(data).model_dump()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.put("/api/audiences/{audience_id}")
def update_audience(audience_id: str, data: audiences.AudienceIn):
    try:
        return audiences.update_audience(audience_id, data).model_dump()
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except KeyError:
        raise HTTPException(404, "청중 그룹을 찾을 수 없습니다.") from None


@app.delete("/api/audiences/{audience_id}", status_code=204)
def delete_audience(audience_id: str):
    try:
        audiences.delete_audience(audience_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except KeyError:
        raise HTTPException(404, "청중 그룹을 찾을 수 없습니다.") from None
    return Response(status_code=204)


# ------------------------------------------------------------------ 문서

@app.post("/api/documents", status_code=201)
def upload_document(file: UploadFile | None = File(default=None), url: str = Form(default=""),
                    parent_id: str = Form(default="")):
    parent = _doc_meta(parent_id) if parent_id else None  # 수정본이면 이전 버전과 연결
    doc_id = storage.new_id()
    out = storage.doc_dir(doc_id)
    out.mkdir(parents=True)
    try:
        if file is not None and file.filename:
            filename = Path(file.filename).name
            try:
                converter.kind_of(filename)
            except converter.ConversionError as exc:
                raise HTTPException(400, str(exc)) from None
            src = out / ("source" + Path(filename).suffix.lower())
            with src.open("wb") as f:
                size = 0
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "파일이 너무 큽니다 (최대 50MB).")
                    f.write(chunk)
            kind, pages = converter.convert(src, out)
        elif url.strip():
            filename, kind = url.strip(), "url"
            pages = converter.convert_url(url.strip(), out)
        else:
            raise HTTPException(400, "파일 또는 URL을 보내주세요.")
    except converter.ConversionError as exc:
        shutil.rmtree(out, ignore_errors=True)
        raise HTTPException(422, str(exc)) from None
    except HTTPException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    meta = {
        "id": doc_id,
        "filename": filename,
        "kind": kind,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "parent_id": parent["id"] if parent else None,
        "group_id": (parent.get("group_id") or parent["id"]) if parent else doc_id,
        "version": parent.get("version", 1) + 1 if parent else 1,
        "pages": [p.to_dict() for p in pages],
    }
    meta["script"] = script.initial(meta["pages"])  # PPTX 발표자 노트가 있으면 대본으로
    storage.write_json(out / "meta.json", meta)
    return meta


@app.get("/api/documents")
def list_documents():
    docs = []
    for d in storage.docs_dir().iterdir():
        meta = storage.read_json(d / "meta.json")
        if meta:
            docs.append({"id": meta["id"], "filename": meta["filename"], "kind": meta["kind"],
                         "created_at": meta["created_at"], "page_count": len(meta["pages"]),
                         "version": meta.get("version", 1), "parent_id": meta.get("parent_id"),
                         "group_id": meta.get("group_id", meta["id"]),
                         "analyzed": sorted(p.stem for p in (d / "results").glob("*.json"))})
    docs.sort(key=lambda m: m["created_at"], reverse=True)
    return docs


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str):
    return _doc_meta(doc_id)


@app.delete("/api/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str):
    _doc_meta(doc_id)
    shutil.rmtree(storage.doc_dir(doc_id), ignore_errors=True)
    return Response(status_code=204)


class ScriptIn(BaseModel):
    target_minutes: float | None = Field(default=None, gt=0, le=600)
    pages: dict[str, str] = Field(default_factory=dict)


@app.put("/api/documents/{doc_id}/script")
def save_script(doc_id: str, data: ScriptIn):
    meta = _doc_meta(doc_id)
    valid = {str(p["index"]) for p in meta["pages"]}
    bad = [k for k in data.pages if k not in valid]
    if bad:
        raise HTTPException(422, f"없는 페이지 번호입니다: {', '.join(bad)}")
    if any(len(v) > 20000 for v in data.pages.values()):
        raise HTTPException(422, "한 페이지 대본은 20,000자까지입니다.")
    pages = {k: v.strip() for k, v in data.pages.items() if v.strip()}
    meta["script"] = {"target_minutes": data.target_minutes, "pages": pages} if pages or data.target_minutes else None
    storage.write_json(storage.doc_dir(doc_id) / "meta.json", meta)
    return {"script": meta["script"],
            "timing": script.timing(meta["script"], [p["index"] for p in meta["pages"]])}


@app.get("/api/documents/{doc_id}/pages/{index}.png")
def page_image(doc_id: str, index: int):
    meta = _doc_meta(doc_id)
    page = next((p for p in meta["pages"] if p["index"] == index), None)
    if not page:
        raise HTTPException(404, "페이지가 없습니다.")
    return FileResponse(storage.doc_dir(doc_id) / page["image"], media_type="image/png")


# ------------------------------------------------------------------ 분석

class AnalyzeRequest(BaseModel):
    persona_ids: list[str] = Field(default_factory=lambda: [personas.DEFAULT_PERSONA_ID], max_length=8)
    audience_id: str = ""  # 지정하면 그룹 구성원을 함께 분석한다
    context: str = Field(default="", max_length=2000)


@app.post("/api/documents/{doc_id}/analyze", status_code=202)
def start_analysis(doc_id: str, req: AnalyzeRequest):
    meta = _doc_meta(doc_id)
    persona_ids, context = list(req.persona_ids), req.context
    if req.audience_id:
        audience = audiences.get_audience(req.audience_id)
        if not audience:
            raise HTTPException(404, "청중 그룹을 찾을 수 없습니다.")
        persona_ids = [m.persona_id for m in audience.members] + persona_ids
        context = context or audience.context
    if not persona_ids:
        raise HTTPException(400, "분석할 페르소나를 하나 이상 고르세요.")
    if len(set(persona_ids)) > 12:
        raise HTTPException(400, "한 번에 분석할 수 있는 페르소나는 12명까지입니다.")
    selected = []
    for pid in dict.fromkeys(persona_ids):
        persona = personas.get_persona(pid)
        if not persona:
            raise HTTPException(404, f"페르소나를 찾을 수 없습니다: {pid}")
        selected.append(persona)
    job_id = storage.new_id()
    job = {"id": job_id, "doc_id": doc_id, "status": "running", "created_at": time.time(),
           "items": {p.id: {"persona_id": p.id, "name": p.name, "status": "queued", "done": 0, "total": 1,
                            "error": None} for p in selected}}
    with _jobs_lock:
        _jobs[job_id] = job
        _save_job(job)
    for persona in selected:
        _executor.submit(_run_analysis, job, meta, persona, context)
    return _job_view(job)


def _run_analysis(job: dict, meta: dict, persona: personas.Persona, context: str) -> None:
    item = job["items"][persona.id]
    item["status"] = "running"

    def progress(done: int, total: int) -> None:
        item["done"], item["total"] = done, total

    try:
        doc_path = storage.doc_dir(meta["id"])
        result = analyzer.analyze(meta, doc_path, persona, context, progress)
        storage.write_json(storage.results_dir(meta["id"]) / f"{persona.id}.json", result)
        item["status"] = "done"
    except analyzer.AnalysisError as exc:
        item["status"], item["error"] = "error", str(exc)
    except Exception as exc:  # noqa: BLE001 - 작업 스레드가 조용히 죽지 않도록
        traceback.print_exc()
        item["status"], item["error"] = "error", f"예상치 못한 오류: {exc}"
    with _jobs_lock:
        states = {i["status"] for i in job["items"].values()}
        if states <= {"done", "error"}:
            job["status"] = "done" if states == {"done"} else ("error" if states == {"error"} else "partial")
        _save_job(job)


def _job_view(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "created_at"} | {"items": list(job["items"].values())}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = _jobs.get(job_id) or storage.read_json(_jobs_dir() / f"{_id_or_404(job_id)}.json")
    if not job:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return _job_view(job)


@app.get("/api/documents/{doc_id}/results")
def list_results(doc_id: str):
    _doc_meta(doc_id)
    out = []
    for path in sorted(storage.results_dir(doc_id).glob("*.json")):
        r = storage.read_json(path)
        out.append({"persona_id": path.stem, "persona_name": r["persona"]["name"], "icon": r["persona"].get("icon"),
                    "mode": r["mode"], "score": r["overall"]["score"], "counts": r["overall"]["counts"],
                    "created_at": r["created_at"]})
    return out


def _result(doc_id: str, persona_id: str) -> dict:
    _doc_meta(doc_id)
    result = storage.read_json(storage.results_dir(doc_id) / f"{_id_or_404(persona_id)}.json")
    if not result:
        raise HTTPException(404, "분석 결과가 없습니다.")
    return result


@app.get("/api/documents/{doc_id}/results/{persona_id}")
def get_result(doc_id: str, persona_id: str):
    return _result(doc_id, persona_id)


@app.get("/api/documents/{doc_id}/compare/{persona_id}")
def compare_with_previous(doc_id: str, persona_id: str):
    meta = _doc_meta(doc_id)
    if not meta.get("parent_id"):
        raise HTTPException(404, "이전 버전이 없는 문서입니다.")
    prev_meta = storage.read_json(storage.doc_dir(meta["parent_id"]) / "meta.json")
    if not prev_meta:
        raise HTTPException(404, "이전 버전이 삭제되었습니다.")
    prev_result = storage.read_json(storage.results_dir(prev_meta["id"]) / f"{_id_or_404(persona_id)}.json")
    if not prev_result:
        raise HTTPException(404, "이전 버전에 이 청중의 분석 결과가 없습니다.")
    return compare.compare(prev_meta, prev_result, meta, _result(doc_id, persona_id))


def _audience_result(doc_id: str, audience_id: str) -> dict:
    _doc_meta(doc_id)
    audience = audiences.get_audience(audience_id)
    if not audience:
        raise HTTPException(404, "청중 그룹을 찾을 수 없습니다.")
    results = {m.persona_id: r for m in audience.members
               if (r := storage.read_json(storage.results_dir(doc_id) / f"{m.persona_id}.json"))}
    if not results:
        raise HTTPException(404, "이 그룹의 구성원 분석 결과가 없습니다.")
    return audiences.aggregate(audience, results)


@app.get("/api/documents/{doc_id}/audiences")
def list_document_audiences(doc_id: str):
    """이 문서에서 구성원이 모두 분석된 청중 그룹."""
    _doc_meta(doc_id)
    analyzed = {p.stem for p in storage.results_dir(doc_id).glob("*.json")}
    out = []
    for a in audiences.list_audiences():
        if {m.persona_id for m in a.members} <= analyzed:
            agg = _audience_result(doc_id, a.id)
            out.append({"audience_id": a.id, "name": a.name, "icon": a.icon, "score": agg["overall"]["score"],
                        "counts": agg["overall"]["counts"]})
    return out


@app.get("/api/documents/{doc_id}/audiences/{audience_id}")
def get_audience_result(doc_id: str, audience_id: str):
    return _audience_result(doc_id, audience_id)


# ------------------------------------------------------------------ 내보내기

@app.get("/api/documents/{doc_id}/audiences/{audience_id}/export.pdf")
def export_audience_pdf(doc_id: str, audience_id: str):
    meta, result = _doc_meta(doc_id), _audience_result(doc_id, audience_id)
    return _pdf_response(meta, doc_id, result, f"whowillsee_{doc_id}_{audience_id}.pdf")


@app.get("/api/documents/{doc_id}/audiences/{audience_id}/export.json")
def export_audience_json(doc_id: str, audience_id: str):
    result = _audience_result(doc_id, audience_id)
    return JSONResponse(result, headers={"Content-Disposition":
                                         f'attachment; filename="whowillsee_{doc_id}_{audience_id}.json"'})


def _pdf_response(meta: dict, doc_id: str, result: dict, name: str) -> Response:
    data = exporter.annotated_pdf(meta, storage.doc_dir(doc_id), result)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/documents/{doc_id}/results/{persona_id}/export.pdf")
def export_pdf(doc_id: str, persona_id: str):
    meta, result = _doc_meta(doc_id), _result(doc_id, persona_id)
    return _pdf_response(meta, doc_id, result, f"whowillsee_{doc_id}_{persona_id}.pdf")


@app.get("/api/documents/{doc_id}/results/{persona_id}/export.html")
def export_html(doc_id: str, persona_id: str):
    meta, result = _doc_meta(doc_id), _result(doc_id, persona_id)
    return _html_response(meta, doc_id, result, f"whowillsee_{doc_id}_{persona_id}.html")


@app.get("/api/documents/{doc_id}/audiences/{audience_id}/export.html")
def export_audience_html(doc_id: str, audience_id: str):
    meta, result = _doc_meta(doc_id), _audience_result(doc_id, audience_id)
    return _html_response(meta, doc_id, result, f"whowillsee_{doc_id}_{audience_id}.html")


def _html_response(meta: dict, doc_id: str, result: dict, name: str) -> HTMLResponse:
    return HTMLResponse(report_html.render(meta, storage.doc_dir(doc_id), result),
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/documents/{doc_id}/results/{persona_id}/export.json")
def export_json(doc_id: str, persona_id: str):
    result = _result(doc_id, persona_id)
    name = f"whowillsee_{doc_id}_{persona_id}.json"
    return JSONResponse(result, headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------------ 프론트엔드

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
