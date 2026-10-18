"""앱 없이 열어볼 수 있는 단일 HTML 리포트 (이미지 내장, 외부 리소스 없음)."""
from __future__ import annotations

import base64
from html import escape
from pathlib import Path

SEV = {"critical": "치명", "warning": "주의", "suggestion": "제안"}

CSS = """
:root { --text:#1d2330; --muted:#6b7280; --line:#e3e6eb; --bg:#f5f6f8; --panel:#fff; --accent:#2348c7;
  --critical:#9b1c1c; --warning:#d4483a; --suggestion:#eb9479; }
@media (prefers-color-scheme: dark) {
  :root { color-scheme: dark; --text:#f0efec; --muted:#a3a29b; --line:#2e3036; --bg:#111318; --panel:#1a1b1f; --accent:#8aa4ff; }
  .memo .who, .tag { background: #1f2a4d !important; }
  /* 메모 왼쪽 띠(화면 요소)는 다크 표면용 단계 */
  .memo { border-left-color: #f6a091; } .memo.warning { border-left-color: #cf5244; } .memo.suggestion { border-left-color: #8f3329; }
  .pill::before { background: #f6a091; } .pill.warning::before { background: #cf5244; } .pill.suggestion::before { background: #8f3329; }
}
/* 문서 위 박스는 흰 슬라이드 위에 그려지므로 테마와 관계없이 라이트 단계 */
.box { --critical:#9b1c1c; --warning:#d4483a; --suggestion:#eb9479; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text);
  font:14px/1.6 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic",sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 60px; }
h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 16px; margin: 0 0 10px; } h3 { font-size: 13px; color: var(--muted); margin: 12px 0 4px; }
.muted { color: var(--muted); font-size: 13px; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; margin-top: 16px; }
.summary { display: grid; grid-template-columns: 120px 1fr; gap: 18px; }
.score { font-size: 44px; font-weight: 800; line-height: 1; }
.score small { display: block; font-size: 12px; font-weight: 400; color: var(--muted); margin-top: 4px; }
.pill { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; border: 1px solid var(--line); border-radius: 99px; padding: 1px 8px; margin: 2px 2px 0 0; }
.pill::before { content: ""; width: 9px; height: 9px; border-radius: 50%; background: var(--critical); }
.pill.warning::before { background: var(--warning); } .pill.suggestion::before { background: var(--suggestion); }
ul, ol { margin: 0; padding-left: 20px; }
.page { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 16px; align-items: start; }
.wrap { position: relative; line-height: 0; }
.wrap img { width: 100%; height: auto; border: 1px solid var(--line); }
.box { position: absolute; border: 3px solid var(--critical); border-radius: 3px; }
.box.warning { border-color: var(--warning); border-width: 2.5px; }
.box.suggestion { border-color: var(--suggestion); border-width: 2px; border-style: dashed; }
.num { position: absolute; left: -12px; top: -12px; width: 22px; height: 22px; border-radius: 50%; background: var(--critical);
  color: #fff; font: 700 12px/22px sans-serif; text-align: center; text-decoration: none; }
.box.warning .num { background: var(--warning); } .box.suggestion .num { background: var(--suggestion); color: #0b0b0b; }
.memo { border: 1px solid var(--line); border-left: 4px solid var(--critical); border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; font-size: 13px; }
.memo.warning { border-left-color: var(--warning); } .memo.suggestion { border-left-color: var(--suggestion); }
.memo b { font-size: 14px; } .memo p { margin: 4px 0 0; } .memo .k { color: var(--muted); font-weight: 700; font-size: 12px; }
.memo .who { background: #e8edfd; border-radius: 6px; padding: 2px 6px; font-size: 12px; margin-top: 4px; }
.reaction { font-style: italic; color: var(--muted); }
.tag { font-size: 11px; font-weight: 700; color: var(--accent); background: #e8edfd; border-radius: 4px; padding: 0 5px; }
footer { margin-top: 24px; text-align: center; color: var(--muted); font-size: 12px; }
@media (max-width: 800px) { .page, .summary { grid-template-columns: 1fr; } }
@media print { body { background: #fff; } .card { break-inside: avoid; } .page { grid-template-columns: 1fr 300px; } }
"""


def _e(value) -> str:
    return escape(str(value or ""))


def render(meta: dict, doc_path: Path, result: dict) -> str:
    o = result["overall"]
    persona = result["persona"]
    counts = o["counts"]
    pages_meta = {p["index"]: p for p in meta["pages"]}
    parts = [
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>WhoWillSee · {_e(meta['filename'])} · {_e(persona['name'])}</title><style>{CSS}</style></head><body><main>",
        f"<h1>{_e(persona.get('icon'))} {_e(persona['name'])}의 눈으로 본 «{_e(meta['filename'])}»</h1>",
        f"<div class='muted'>{_e(result.get('engine') or '데모 모드(규칙 기반)')} · {_e(result['created_at'])}</div>",
        "<section class='card summary'><div>",
        f"<div class='score'>{o['score']}<small>전달력 (100점 만점)</small></div><div style='margin-top:8px'>",
        "".join(f"<span class='pill {s}'>{SEV[s]} {counts[s]}</span>" for s in SEV),
        "</div></div><div>",
    ]
    if result.get("context"):
        parts.append(f"<h3>자료 사용 상황</h3><div>{_e(result['context'])}</div>")
    if o.get("first_impression"):
        parts.append(f"<h3>첫인상</h3><div>{_e(o['first_impression'])}</div>")
    if o.get("summary"):
        parts.append(f"<h3>종합 평가</h3><div>{_e(o['summary'])}</div>")
    if result.get("audience"):
        rows = "".join(f"<li>{_e(m['icon'])} {_e(m['name'])} · 비중 {m['share']}% · {m['score']}점</li>"
                       for m in result["audience"]["members"])
        parts.append(f"<h3>구성원</h3><ul>{rows}</ul>")
    timing = o.get("timing")
    if timing:
        target = f" / 목표 {timing['target_minutes']:g}분" if timing.get("target_minutes") else ""
        parts.append(f"<h3>발표 시간</h3><div>예상 {timing['est_minutes']:g}분{target} (대본 기준)</div>")
    for title, items in (("우선 고칠 것", o.get("top_fixes")), ("잘된 점", o.get("strengths"))):
        if items:
            parts.append(f"<h3>{title}</h3><ol>{''.join(f'<li>{_e(i)}</li>' for i in items)}</ol>")
    parts.append("</div></section>")

    for rp in result["pages"]:
        pm = pages_meta.get(rp["page"])
        if not pm:
            continue
        img = base64.standard_b64encode((doc_path / pm["image"]).read_bytes()).decode()
        boxes, same_box = [], {}
        for i in rp["issues"]:
            if not i.get("box"):
                continue
            b = i["box"]
            # 같은 영역을 가리키는 지적이 여럿이면 번호 배지를 옆으로 나란히 놓는다
            key = tuple(round(b[k], 2) for k in ("x", "y", "w", "h"))
            same_box[key] = same_box.get(key, -1) + 1
            boxes.append(
                f"<div class='box {i['severity']}' style='left:{b['x'] * 100:.2f}%;top:{b['y'] * 100:.2f}%;"
                f"width:{b['w'] * 100:.2f}%;height:{b['h'] * 100:.2f}%'>"
                f"<a class='num' style='margin-left:{same_box[key] * 24}px' href='#m{i['n']}'>{i['n']}</a></div>")
        boxes = "".join(boxes)
        memos = "".join(_memo(i) for i in rp["issues"]) or "<div class='muted'>지적사항 없음</div>"
        comment = f"<p class='muted'>{_e(rp['page_comment'])}</p>" if rp.get("page_comment") else ""
        parts.append(
            f"<section class='card'><h2>페이지 {rp['page']}</h2><div class='page'><div>"
            f"<div class='wrap'><img alt='페이지 {rp['page']}' src='data:image/png;base64,{img}'>{boxes}</div>{comment}"
            f"</div><div>{memos}</div></div></section>")
    parts.append("<footer>WhoWillSee 로 만든 리포트입니다. 결과는 실제 사람의 반응이 아니라 페르소나 시뮬레이션입니다.</footer>")
    parts.append("</main></body></html>")
    return "".join(parts)


def _memo(i: dict) -> str:
    tag = "<span class='tag'>🎤 대본</span> " if i.get("target") == "script" else ""
    who = ""
    if i.get("voices"):
        names = ", ".join(f"{_e(v['icon'])} {_e(v['name'])}" for v in i["voices"])
        who = f"<div class='who'>👥 청중의 {i['share']}%가 지적 · {names}</div>"
    suggestion = f"<p><span class='k'>이렇게 고치세요</span><br>{_e(i['suggestion'])}</p>" if i.get("suggestion") else ""
    reaction = f"<p class='reaction'>💭 “{_e(i['persona_reaction'])}”</p>" if i.get("persona_reaction") else ""
    return (f"<div class='memo {i['severity']}' id='m{i['n']}'>{tag}<b>#{i['n']} {_e(i['title'])}</b>"
            f"<div class='muted'>{SEV[i['severity']]} · {_e(i['category'])}</div>{who}"
            f"<p><span class='k'>왜 문제인가</span><br>{_e(i['reason'])}</p>{suggestion}{reaction}</div>")
