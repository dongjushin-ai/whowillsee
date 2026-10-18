"""분석 결과를 주석이 달린 PDF 로 내보낸다.

구성: 요약 페이지 → (원본 페이지 + 빨간 박스/번호/PDF 주석) → 해당 페이지 메모 → ...
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

# 화면과 같은 심각도 단계: #9b1c1c, #d4483a, #eb9479
RED = (0.608, 0.11, 0.11)
COLORS = {"critical": RED, "warning": (0.831, 0.282, 0.227), "suggestion": (0.922, 0.58, 0.475)}
WIDTHS = {"critical": 3.0, "warning": 2.2, "suggestion": 1.6}
LABELS = {"critical": "치명", "warning": "주의", "suggestion": "제안"}
NOTE_PAGE = (595, 842)  # A4 (pt)
MARGIN = 48

_FONT = pymupdf.Font("korea")


class _Writer:
    """줄바꿈·페이지 넘김을 처리하며 한글 텍스트를 쓴다."""

    def __init__(self, doc: pymupdf.Document):
        self.doc = doc
        self.page = None
        self.y = 0.0

    def new_page(self):
        self.page = self.doc.new_page(width=NOTE_PAGE[0], height=NOTE_PAGE[1])
        self.page.insert_font(fontname="kr", fontbuffer=_FONT.buffer)
        self.y = MARGIN

    def text(self, value: str, size: float = 10.5, color=(0.1, 0.1, 0.1), indent: float = 0, gap: float = 4):
        if self.page is None:
            self.new_page()
        width = NOTE_PAGE[0] - 2 * MARGIN - indent
        for line in _wrap(value, size, width):
            if self.y + size * 1.5 > NOTE_PAGE[1] - MARGIN:
                self.new_page()
            self.page.insert_text((MARGIN + indent, self.y + size), line, fontname="kr", fontsize=size, color=color)
            self.y += size * 1.45
        self.y += gap


def _wrap(text: str, size: float, width: float) -> list[str]:
    lines: list[str] = []
    for para in (text or "").split("\n"):
        line = ""
        for ch in para:
            if _FONT.text_length(line + ch, fontsize=size) > width:
                cut = line.rfind(" ")
                if cut > len(line) * 0.6:
                    lines.append(line[:cut])
                    line = line[cut + 1:] + ch
                else:
                    lines.append(line)
                    line = ch
            else:
                line += ch
        lines.append(line)
    return lines


def annotated_pdf(meta: dict, doc_path: Path, result: dict) -> bytes:
    doc = pymupdf.open()
    persona = result["persona"]
    overall = result["overall"]

    w = _Writer(doc)
    w.text("WhoWillSee 검토 리포트", size=20, gap=10)
    w.text(f"자료: {meta['filename']}", size=10, color=(0.35, 0.35, 0.35), gap=2)
    w.text(f"페르소나: {persona.get('icon', '')} {persona['name']}", size=10, color=(0.35, 0.35, 0.35), gap=2)
    mode = result.get("engine") or "데모 모드(규칙 기반)"
    w.text(f"분석: {mode} · {result['created_at']}", size=10, color=(0.35, 0.35, 0.35), gap=14)
    c = overall["counts"]
    w.text(f"전달력 점수 {overall['score']} / 100   ·   치명 {c['critical']} · 주의 {c['warning']} · 제안 {c['suggestion']}",
           size=13, color=RED, gap=12)
    if result.get("context"):
        w.text("자료 사용 상황", size=12, gap=2)
        w.text(result["context"], gap=10)
    for title, body in (("첫인상", overall.get("first_impression")), ("종합 평가", overall.get("summary"))):
        if body:
            w.text(title, size=12, gap=2)
            w.text(body, gap=10)
    for title, items in (("잘된 점", overall.get("strengths")), ("우선 고칠 것", overall.get("top_fixes"))):
        if items:
            w.text(title, size=12, gap=2)
            for i, item in enumerate(items, 1):
                w.text(f"{i}. {item}", indent=8, gap=2)
            w.y += 8

    pages = {p["index"]: p for p in meta["pages"]}
    for rpage in result["pages"]:
        src = pages[rpage["page"]]
        pw, ph = src["width"] * 0.75, src["height"] * 0.75  # px → pt
        page = doc.new_page(width=pw, height=ph)
        page.insert_image(page.rect, filename=str(doc_path / src["image"]))
        page.insert_font(fontname="kr", fontbuffer=_FONT.buffer)
        same_box: dict[tuple, int] = {}
        for issue in rpage["issues"]:
            b = issue["box"]
            rect = pymupdf.Rect(b["x"] * pw, b["y"] * ph, (b["x"] + b["w"]) * pw, (b["y"] + b["h"]) * ph)
            color = COLORS[issue["severity"]]
            annot = page.add_rect_annot(rect)
            annot.set_colors(stroke=color)
            annot.set_border(width=WIDTHS[issue["severity"]], dashes=[4, 2] if issue["severity"] == "suggestion" else None)
            annot.set_info(title=f"#{issue['n']} {LABELS[issue['severity']]} · {persona['name']}",
                           content=_memo(issue))
            annot.update()
            key = tuple(round(issue["box"][k], 2) for k in ("x", "y", "w", "h"))
            same_box[key] = same_box.get(key, -1) + 1
            _badge(page, rect, issue["n"], color, same_box[key],
                   text_color=(0.04, 0.04, 0.04) if issue["severity"] == "suggestion" else (1, 1, 1))
        if rpage["issues"]:
            w.new_page()
            w.text(f"페이지 {rpage['page']} 메모", size=15, gap=8)
            if rpage.get("page_comment"):
                w.text(rpage["page_comment"], color=(0.35, 0.35, 0.35), gap=10)
            for issue in rpage["issues"]:
                w.text(f"#{issue['n']}  [{LABELS[issue['severity']]}·{issue['category']}]  {issue['title']}",
                       size=11.5, color=COLORS[issue["severity"]], gap=2)
                w.text(_memo(issue), indent=10, gap=10)
    return doc.tobytes(garbage=3, deflate=True)


def _memo(issue: dict) -> str:
    parts = []
    if issue.get("voices"):
        who = ", ".join(f"{v['name']}({v['weight']})" for v in issue["voices"])
        parts.append(f"지적한 청중 (비중 {issue['share']}%): {who}")
    parts.append(f"왜 문제인가: {issue['reason']}")
    if issue.get("suggestion"):
        parts.append(f"이렇게 고치세요: {issue['suggestion']}")
    if issue.get("persona_reaction"):
        parts.append(f"속마음: “{issue['persona_reaction']}”")
    return "\n".join(parts)


def _badge(page: pymupdf.Page, rect: pymupdf.Rect, n: int, color, slot: int = 0, text_color=(1, 1, 1)) -> None:
    # 박스 왼쪽 바깥에 두어 위아래로 붙은 박스끼리 배지가 겹치지 않게 한다.
    # 같은 박스를 가리키는 지적이 여럿이면(slot) 박스 위쪽 가장자리를 따라 나란히 놓는다.
    r = 9
    cx = rect.x0 - r - 2 if rect.x0 > 2 * r + 4 else rect.x0 + r + 2
    cy = rect.y0 + min(r + 1, rect.height / 2)
    if slot:
        cx = rect.x0 + r + 2 + (slot - 1) * (2 * r + 3)
        cy = rect.y0 - r - 1 if rect.y0 > 2 * r + 2 else rect.y0 + r + 1
    page.draw_circle((cx, cy), r, color=color, fill=color)
    label = str(n)
    size = 10 if n < 100 else 8
    tw = _FONT.text_length(label, fontsize=size)
    page.insert_text((cx - tw / 2, cy + size * 0.36), label, fontname="kr", fontsize=size, color=text_color)
