"""WhoWillSee API 서버.

    uvicorn app.main:app --reload
"""
from __future__ import annotations

import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analyzer, converter, exporter, personas, storage

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

app = FastAPI(title="WhoWillSee", version="1.0")
_executor = ThreadPoolExecutor(max_workers=4)
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


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
    return {
        "mode": "claude" if analyzer.use_claude() else "demo",
        "model": analyzer.MODEL if analyzer.use_claude() else None,
        "office": converter.soffice_path() is not None,
        "formats": sorted(converter.SUPPORTED_EXTS),
    }


# ------------------------------------------------------------------ 페르소나

@app.get("/api/personas")
def list_personas():
    return [p.model_dump() for p in personas.list_personas()]


@app.post("/api/personas", status_code=201)
def create_persona(data: personas.PersonaIn):
    return personas.create_persona(data).model_dump()


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
    try:
        personas.delete_persona(persona_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except KeyError:
        raise HTTPException(404, "페르소나를 찾을 수 없습니다.") from None
    return Response(status_code=204)


# ------------------------------------------------------------------ 문서

@app.post("/api/documents", status_code=201)
def upload_document(file: UploadFile | None = File(default=None), url: str = Form(default="")):
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
        "pages": [p.to_dict() for p in pages],
    }
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


@app.get("/api/documents/{doc_id}/pages/{index}.png")
def page_image(doc_id: str, index: int):
    meta = _doc_meta(doc_id)
    page = next((p for p in meta["pages"] if p["index"] == index), None)
    if not page:
        raise HTTPException(404, "페이지가 없습니다.")
    return FileResponse(storage.doc_dir(doc_id) / page["image"], media_type="image/png")


# ------------------------------------------------------------------ 분석

class AnalyzeRequest(BaseModel):
    persona_ids: list[str] = Field(default_factory=lambda: [personas.DEFAULT_PERSONA_ID], min_length=1, max_length=8)
    context: str = Field(default="", max_length=2000)


@app.post("/api/documents/{doc_id}/analyze", status_code=202)
def start_analysis(doc_id: str, req: AnalyzeRequest):
    meta = _doc_meta(doc_id)
    selected = []
    for pid in dict.fromkeys(req.persona_ids):
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
    for persona in selected:
        _executor.submit(_run_analysis, job, meta, persona, req.context)
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


def _job_view(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "created_at"} | {"items": list(job["items"].values())}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = _jobs.get(job_id)
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


# ------------------------------------------------------------------ 내보내기

@app.get("/api/documents/{doc_id}/results/{persona_id}/export.pdf")
def export_pdf(doc_id: str, persona_id: str):
    meta, result = _doc_meta(doc_id), _result(doc_id, persona_id)
    data = exporter.annotated_pdf(meta, storage.doc_dir(doc_id), result)
    name = f"whowillsee_{doc_id}_{persona_id}.pdf"
    return Response(data, media_type="application/pdf",
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
