"""업로드된 파일을 '페이지 이미지 + 텍스트 블록' 목록으로 변환한다.

모든 좌표는 페이지 크기 대비 0~1 로 정규화한다 (x, y, w, h).
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf
from PIL import Image

# Claude Vision 권장 해상도(긴 변 약 1568px)에 맞춘다.
MAX_EDGE = 1568
MAX_PAGES = int(os.environ.get("WHOWILLSEE_MAX_PAGES", "40"))
HTML_VIEWPORT = (1280, 800)
HTML_CHUNK_HEIGHT = 1100
HTML_MAX_HEIGHT = 15000

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
OFFICE_EXTS = {".ppt", ".pptx", ".odp", ".key", ".doc", ".docx", ".odt"}
HTML_EXTS = {".html", ".htm"}
PDF_EXTS = {".pdf"}
SUPPORTED_EXTS = IMAGE_EXTS | OFFICE_EXTS | HTML_EXTS | PDF_EXTS


class ConversionError(Exception):
    pass


@dataclass
class Block:
    id: int
    text: str
    x: float
    y: float
    w: float
    h: float
    font_size: float = 0.0


@dataclass
class Page:
    index: int  # 1부터 시작
    image: str  # 문서 폴더 기준 파일명
    width: int
    height: int
    blocks: list[Block] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def kind_of(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in PDF_EXTS:
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in HTML_EXTS:
        return "html"
    raise ConversionError(f"지원하지 않는 형식입니다: {ext or filename}")


def convert(src: Path, out_dir: Path) -> tuple[str, list[Page]]:
    """src 파일을 out_dir 에 페이지 PNG 로 변환하고 (종류, 페이지 목록)을 반환한다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    kind = kind_of(src.name)
    if kind == "pdf":
        pages = convert_pdf(src, out_dir)
    elif kind == "image":
        pages = convert_image(src, out_dir)
    elif kind == "office":
        pages = convert_office(src, out_dir)
    else:
        pages = convert_html(src.resolve().as_uri(), out_dir)
    if not pages:
        raise ConversionError("변환된 페이지가 없습니다.")
    return kind, pages


def convert_url(url: str, out_dir: Path) -> list[Page]:
    if not url.startswith(("http://", "https://")):
        raise ConversionError("http(s) URL만 지원합니다.")
    out_dir.mkdir(parents=True, exist_ok=True)
    return convert_html(url, out_dir)


# ---------------------------------------------------------------- PDF

def convert_pdf(src: Path, out_dir: Path) -> list[Page]:
    try:
        doc = pymupdf.open(src)
    except Exception as exc:  # noqa: BLE001 - PyMuPDF raises many types
        raise ConversionError(f"PDF를 열 수 없습니다: {exc}") from exc
    pages: list[Page] = []
    with doc:
        if doc.needs_pass:
            raise ConversionError("암호가 걸린 PDF는 지원하지 않습니다.")
        for i, page in enumerate(doc):
            if i >= MAX_PAGES:
                break
            rect = page.rect
            scale = MAX_EDGE / max(rect.width, rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            name = f"page-{i + 1:03d}.png"
            pix.save(out_dir / name)
            pages.append(Page(i + 1, name, pix.width, pix.height, _pdf_blocks(page)))
    return pages


def _pdf_blocks(page: "pymupdf.Page") -> list[Block]:
    rect = page.rect
    blocks: list[Block] = []
    for raw in page.get_text("dict").get("blocks", []):
        if raw.get("type") != 0:
            continue
        lines, max_size = [], 0.0
        for line in raw.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans).strip()
            if text:
                lines.append(text)
                max_size = max([max_size] + [s.get("size", 0.0) for s in spans])
        text = "\n".join(lines).strip()
        if not text:
            continue
        x0, y0, x1, y1 = raw["bbox"]
        blocks.append(Block(
            id=len(blocks) + 1,
            text=text,
            x=_clip((x0 - rect.x0) / rect.width),
            y=_clip((y0 - rect.y0) / rect.height),
            w=_clip((x1 - x0) / rect.width),
            h=_clip((y1 - y0) / rect.height),
            font_size=round(max_size, 1),
        ))
    return blocks


# ---------------------------------------------------------------- 이미지

def convert_image(src: Path, out_dir: Path) -> list[Page]:
    try:
        with Image.open(src) as img:
            img = img.convert("RGB")
            img.thumbnail((MAX_EDGE, MAX_EDGE))
            name = "page-001.png"
            img.save(out_dir / name)
            return [Page(1, name, img.width, img.height)]
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(f"이미지를 열 수 없습니다: {exc}") from exc


# ---------------------------------------------------------------- PPT / 오피스

def soffice_path() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def convert_office(src: Path, out_dir: Path) -> list[Page]:
    exe = soffice_path()
    if not exe:
        raise ConversionError("PPT/DOCX 변환에는 LibreOffice(soffice)가 필요합니다. PDF로 저장해서 올려주세요.")
    with tempfile.TemporaryDirectory() as tmp:
        profile = Path(tmp, "profile").as_uri()
        cmd = [exe, f"-env:UserInstallation={profile}", "--headless",
               "--convert-to", "pdf", "--outdir", tmp, str(src)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        except subprocess.TimeoutExpired as exc:
            raise ConversionError("LibreOffice 변환 시간이 초과되었습니다.") from exc
        except subprocess.CalledProcessError as exc:
            raise ConversionError(f"LibreOffice 변환 실패: {exc.stderr.decode(errors='ignore')[:300]}") from exc
        pdfs = list(Path(tmp).glob("*.pdf"))
        if not pdfs:
            raise ConversionError("LibreOffice가 PDF를 만들지 못했습니다.")
        shutil.copy(pdfs[0], out_dir / "converted.pdf")
    return convert_pdf(out_dir / "converted.pdf", out_dir)


# ---------------------------------------------------------------- HTML

_DOM_BLOCKS_JS = """
() => {
  const out = [];
  const seen = new Set();
  const els = document.body ? document.body.querySelectorAll('*') : [];
  for (const el of els) {
    if (['SCRIPT','STYLE','NOSCRIPT','svg','SVG'].includes(el.tagName)) continue;
    let own = '';
    for (const n of el.childNodes) if (n.nodeType === 3) own += n.textContent;
    own = own.replace(/\\s+/g, ' ').trim();
    if (!own && !['IMG','BUTTON','INPUT'].includes(el.tagName)) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || +style.opacity === 0) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    let text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    if (el.tagName === 'IMG') text = '[이미지] ' + (el.alt || '(대체 텍스트 없음)');
    if (el.tagName === 'INPUT') text = '[입력창] ' + (el.placeholder || el.value || '');
    if (!text) continue;
    const key = text + '|' + Math.round(r.top + scrollY);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({text: text.slice(0, 500), x: r.left + scrollX, y: r.top + scrollY,
              w: r.width, h: r.height, font_size: parseFloat(style.fontSize) || 0});
  }
  return out;
}
"""


def _chromium_executable() -> str | None:
    env = os.environ.get("CHROMIUM_PATH")
    if env:
        return env
    found = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")
    return found[0] if found else None


def convert_html(url: str, out_dir: Path) -> list[Page]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ConversionError("HTML 변환에는 playwright 패키지가 필요합니다.") from exc

    full = out_dir / "full.png"
    try:
        with sync_playwright() as pw:
            exe = _chromium_executable()
            browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": HTML_VIEWPORT[0], "height": HTML_VIEWPORT[1]})
                page.goto(url, wait_until="load", timeout=30000)
                page.wait_for_timeout(500)
                height = min(int(page.evaluate("document.documentElement.scrollHeight")), HTML_MAX_HEIGHT)
                page.set_viewport_size({"width": HTML_VIEWPORT[0], "height": max(height, HTML_VIEWPORT[1])})
                dom_blocks = page.evaluate(_DOM_BLOCKS_JS)
                page.screenshot(path=str(full), full_page=False)
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise ConversionError(f"HTML 렌더링 실패: {exc}") from exc

    pages: list[Page] = []
    with Image.open(full) as img:
        img = img.convert("RGB")
        width, height = img.size
        top, index = 0, 1
        while top < height and index <= MAX_PAGES:
            bottom = min(top + HTML_CHUNK_HEIGHT, height)
            if bottom - top < 80 and pages:  # 아주 짧은 꼬리 조각은 버린다
                break
            chunk = img.crop((0, top, width, bottom))
            name = f"page-{index:03d}.png"
            chunk.save(out_dir / name)
            ch = bottom - top
            blocks: list[Block] = []
            for b in dom_blocks:
                cy = b["y"] + b["h"] / 2
                if not (top <= cy < bottom):
                    continue
                y0 = max(b["y"], top) - top
                y1 = min(b["y"] + b["h"], bottom) - top
                blocks.append(Block(
                    id=len(blocks) + 1, text=b["text"],
                    x=_clip(b["x"] / width), y=_clip(y0 / ch),
                    w=_clip(b["w"] / width), h=_clip((y1 - y0) / ch),
                    font_size=round(b["font_size"], 1),
                ))
            pages.append(Page(index, name, width, ch, _drop_nested(blocks)))
            top, index = bottom, index + 1
    full.unlink(missing_ok=True)
    return pages


def _drop_nested(blocks: list[Block]) -> list[Block]:
    """같은 텍스트가 부모/자식 요소에 중복으로 잡힌 경우 작은 쪽만 남긴다."""
    kept: list[Block] = []
    for b in sorted(blocks, key=lambda b: b.w * b.h):
        if any(k.text == b.text for k in kept):
            continue
        kept.append(b)
    kept.sort(key=lambda b: (round(b.y, 3), b.x))
    for i, b in enumerate(kept, 1):
        b.id = i
    return kept


def _clip(v: float) -> float:
    return round(min(max(v, 0.0), 1.0), 4)
