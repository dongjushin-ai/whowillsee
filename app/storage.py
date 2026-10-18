"""data/ 디렉터리 기반 단순 파일 저장소."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

DATA_DIR = Path(os.environ.get("WHOWILLSEE_DATA", Path(__file__).resolve().parent.parent / "data"))

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def check_id(value: str) -> str:
    """경로 조작을 막기 위해 ID 형식을 검증한다."""
    if not _ID_RE.match(value or ""):
        raise ValueError(f"invalid id: {value!r}")
    return value


def docs_dir() -> Path:
    path = DATA_DIR / "docs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def doc_dir(doc_id: str) -> Path:
    return docs_dir() / check_id(doc_id)


def results_dir(doc_id: str) -> Path:
    path = doc_dir(doc_id) / "results"
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
