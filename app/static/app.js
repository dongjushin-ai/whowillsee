"use strict";

const LEVELS = { 1: "전혀 모름", 2: "기초", 3: "보통", 4: "능숙", 5: "전문가" };
const SEV = { critical: "치명", warning: "주의", suggestion: "제안" };

const state = {
  personas: [],
  selected: new Set(["general-citizen"]),
  docs: [],
  doc: null,
  results: [],       // 현재 문서의 결과 목록 요약
  activePersona: null,
  result: null,
  filter: new Set(["critical", "warning", "suggestion"]),
  editing: null,     // 수정 중인 페르소나 id
  polling: null,
};

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `${res.status}`;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch (_) { /* 본문 없음 */ }
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), ms);
}

// ------------------------------------------------------------------ 상태 뱃지

async function loadStatus() {
  const s = await api("/api/status");
  const badge = $("#mode-badge");
  if (s.mode === "claude") {
    badge.textContent = `Claude 분석 · ${s.model}`;
  } else {
    badge.textContent = "데모 모드 (API 키 없음)";
    badge.classList.add("demo");
    badge.title = "ANTHROPIC_API_KEY 를 설정하고 서버를 재시작하면 실제 Claude 분석을 사용합니다.";
  }
}

// ------------------------------------------------------------------ 페르소나

async function loadPersonas() {
  state.personas = await api("/api/personas");
  for (const id of [...state.selected]) {
    if (!state.personas.some((p) => p.id === id)) state.selected.delete(id);
  }
  if (!state.selected.size) state.selected.add("general-citizen");
  renderPersonas();
}

function renderPersonas() {
  const list = $("#persona-list");
  list.replaceChildren(...state.personas.map((p) => {
    const selected = state.selected.has(p.id);
    const desc = [p.occupation, `전문성 ${LEVELS[p.expertise_level]}`].filter(Boolean).join(" · ");
    return el("div", {
      class: "persona" + (selected ? " selected" : ""),
      title: p.needs ? `니즈: ${p.needs}` : "",
      onclick: () => {
        if (selected && state.selected.size > 1) state.selected.delete(p.id);
        else state.selected.add(p.id);
        renderPersonas();
      },
    },
      el("input", { type: "checkbox", checked: selected, tabindex: -1 }),
      el("span", { class: "icon" }, p.icon || "🙂"),
      el("div", {}, el("div", { class: "name" }, p.name, p.builtin ? "" : " ✱"), el("div", { class: "desc" }, desc)),
      el("div", { class: "actions" },
        el("button", { title: "복제해서 새로 만들기", onclick: (e) => { e.stopPropagation(); openPersonaDialog(p, true); } }, "⧉"),
        p.builtin ? null : el("button", { title: "수정", onclick: (e) => { e.stopPropagation(); openPersonaDialog(p, false); } }, "✎"),
        p.builtin ? null : el("button", { title: "삭제", onclick: (e) => { e.stopPropagation(); deletePersona(p); } }, "🗑"),
      ),
    );
  }));
}

function openPersonaDialog(persona = null, duplicate = false) {
  const form = $("#persona-form");
  form.reset();
  state.editing = persona && !duplicate ? persona.id : null;
  $("#persona-dialog-title").textContent = state.editing ? "페르소나 수정" : duplicate ? "페르소나 복제" : "새 페르소나";
  if (persona) {
    for (const [k, v] of Object.entries(persona)) {
      if (form.elements[k]) form.elements[k].value = v;
    }
    if (duplicate) form.elements.name.value = `${persona.name} (복사본)`;
  }
  syncRangeOutputs();
  $("#persona-dialog").showModal();
}

function syncRangeOutputs() {
  for (const out of document.querySelectorAll("output[data-for]")) {
    const v = $("#persona-form").elements[out.dataset.for].value;
    out.textContent = `${v}/5 ${LEVELS[v]}`;
  }
}

async function savePersona(e) {
  e.preventDefault();
  const form = $("#persona-form");
  const data = Object.fromEntries(new FormData(form).entries());
  data.expertise_level = Number(data.expertise_level);
  data.digital_literacy = Number(data.digital_literacy);
  if (!data.icon) data.icon = "🙂";
  try {
    const saved = state.editing
      ? await api(`/api/personas/${state.editing}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
      : await api("/api/personas", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    state.selected.add(saved.id);
    $("#persona-dialog").close();
    await loadPersonas();
    toast(`'${saved.name}' 페르소나를 저장했습니다.`);
  } catch (err) {
    toast(`저장 실패: ${err.message}`);
  }
}

async function deletePersona(p) {
  if (!confirm(`'${p.name}' 페르소나를 삭제할까요?`)) return;
  await api(`/api/personas/${p.id}`, { method: "DELETE" });
  state.selected.delete(p.id);
  await loadPersonas();
}

// ------------------------------------------------------------------ 문서

async function loadDocs() {
  state.docs = await api("/api/documents");
  renderDocs();
}

function renderDocs() {
  $("#doc-list").replaceChildren(...state.docs.slice(0, 12).map((d) =>
    el("div", { class: "doc-item" + (state.doc?.id === d.id ? " active" : ""), onclick: () => openDoc(d.id) },
      el("span", { class: "fn", title: d.filename }, `${d.filename}`),
      el("span", { class: "muted" }, `${d.page_count}p`),
      el("button", { title: "삭제", onclick: (e) => { e.stopPropagation(); deleteDoc(d); } }, "✕"),
    )));
}

async function upload(file) {
  const fd = new FormData();
  fd.append("file", file);
  await sendDocument(fd, file.name);
}

async function sendDocument(fd, label) {
  setProgress([{ name: `'${label}' 변환 중…`, done: 0, total: 1, status: "running" }]);
  try {
    const doc = await api("/api/documents", { method: "POST", body: fd });
    setProgress([]);
    await loadDocs();
    await openDoc(doc.id);
    toast(`${doc.pages.length}페이지로 변환했습니다. 이제 분석을 시작하세요.`);
  } catch (err) {
    setProgress([{ name: "변환 실패", status: "error", error: err.message, done: 0, total: 1 }]);
  }
}

async function deleteDoc(d) {
  if (!confirm(`'${d.filename}' 과 분석 결과를 삭제할까요?`)) return;
  await api(`/api/documents/${d.id}`, { method: "DELETE" });
  if (state.doc?.id === d.id) {
    state.doc = null;
    $("#viewer").hidden = true;
    $("#empty").hidden = false;
    $("#analyze-btn").disabled = true;
  }
  await loadDocs();
}

async function openDoc(id, personaId = null) {
  state.doc = await api(`/api/documents/${id}`);
  state.results = await api(`/api/documents/${id}/results`);
  $("#empty").hidden = true;
  $("#viewer").hidden = false;
  $("#analyze-btn").disabled = false;
  $("#doc-title").textContent = state.doc.filename;
  $("#doc-sub").textContent = `${state.doc.pages.length}페이지 · ${state.doc.created_at}`;
  renderDocs();
  const pick = personaId || state.activePersona;
  const target = state.results.find((r) => r.persona_id === pick) || state.results[0];
  if (target) await openResult(target.persona_id);
  else {
    state.result = null;
    state.activePersona = null;
    render();
  }
}

// ------------------------------------------------------------------ 분석

async function startAnalysis() {
  if (!state.doc) return;
  const body = { persona_ids: [...state.selected], context: $("#context-input").value };
  $("#analyze-btn").disabled = true;
  try {
    const job = await api(`/api/documents/${state.doc.id}/analyze`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    pollJob(job.id, state.doc.id);
  } catch (err) {
    toast(`분석 시작 실패: ${err.message}`);
    $("#analyze-btn").disabled = false;
  }
}

function pollJob(jobId, docId) {
  clearInterval(state.polling);
  const tick = async () => {
    let job;
    try {
      job = await api(`/api/jobs/${jobId}`);
    } catch (err) {
      clearInterval(state.polling);
      setProgress([{ name: "진행 상태 확인 실패", status: "error", error: err.message, done: 0, total: 1 }]);
      $("#analyze-btn").disabled = false;
      return;
    }
    setProgress(job.items);
    if (job.status !== "running") {
      clearInterval(state.polling);
      $("#analyze-btn").disabled = false;
      const firstDone = job.items.find((i) => i.status === "done");
      if (state.doc?.id === docId) await openDoc(docId, firstDone?.persona_id);
      await loadDocs();
      if (job.status === "done") {
        setProgress([]);
        toast("분석이 끝났습니다.");
      }
    }
  };
  state.polling = setInterval(tick, 1200);
  tick();
}

function setProgress(items) {
  $("#progress").replaceChildren(...items.map((i) => {
    const pct = i.status === "done" ? 100 : Math.round((i.done / Math.max(i.total, 1)) * 100);
    const label = { queued: "대기 중", running: "분석 중…", done: "완료", error: "실패" }[i.status] || "";
    return el("div", {},
      el("div", {}, `${i.name} `, el("span", { class: "muted" }, label)),
      i.status === "error" ? el("div", { class: "err" }, i.error) : el("div", { class: "bar" }, el("i", { style: `width:${Math.max(pct, 4)}%` })),
    );
  }));
}

// ------------------------------------------------------------------ 결과 표시

async function openResult(personaId) {
  state.activePersona = personaId;
  state.result = await api(`/api/documents/${state.doc.id}/results/${personaId}`);
  render();
}

function scoreColor(score) {
  if (score >= 80) return "#16a34a";
  if (score >= 60) return "#ca8a04";
  if (score >= 40) return "#ea580c";
  return "#dc2626";
}

function render() {
  renderTabs();
  renderOverall();
  renderFilters();
  renderPages();
  renderMemos();
  renderExports();
}

function renderTabs() {
  $("#tabs").replaceChildren(...state.results.map((r) =>
    el("button", { class: "tab" + (r.persona_id === state.activePersona ? " active" : ""), onclick: () => openResult(r.persona_id) },
      `${r.icon || ""} ${r.persona_name}`, el("span", { class: "score" }, r.score))));
}

function renderOverall() {
  const box = $("#overall");
  const r = state.result;
  if (!r) {
    box.replaceChildren(el("div", { class: "muted", style: "grid-column:1/-1" },
      "아직 분석 결과가 없습니다. 왼쪽에서 페르소나를 고르고 '분석 시작'을 누르세요."));
    return;
  }
  const o = r.overall;
  const color = scoreColor(o.score);
  box.replaceChildren(
    el("div", {},
      el("div", { class: "score-ring", style: `background: conic-gradient(${color} ${o.score * 3.6}deg, #eceef2 0)` },
        el("div", { style: "background:#fff;border-radius:50%;width:84px;height:84px;display:grid;place-items:center;color:" + color },
          el("div", {}, o.score, el("small", {}, "전달력")))),
      el("div", { class: "counts" }, ...["critical", "warning", "suggestion"].map((s) => el("span", { class: `pill ${s}` }, `${SEV[s]} ${o.counts[s]}`)))),
    el("div", {},
      o.first_impression ? [el("h3", {}, `${r.persona.icon || ""} ${r.persona.name}의 첫인상`), el("p", {}, o.first_impression)] : null,
      o.summary ? [el("h3", {}, "종합 평가"), el("p", {}, o.summary)] : null,
      el("div", { class: "cols" },
        o.top_fixes?.length ? el("div", {}, el("h3", {}, "우선 고칠 것"), el("ol", {}, ...o.top_fixes.map((t) => el("li", {}, t)))) : null,
        o.strengths?.length ? el("div", {}, el("h3", {}, "잘된 점"), el("ul", {}, ...o.strengths.map((t) => el("li", {}, t)))) : null,
      ),
    ),
    r.mode === "demo" ? el("div", { class: "demo-note" },
      "데모 모드 결과입니다. 텍스트 규칙(전문용어·글자 크기·정보 밀도·누락 정보)만으로 추정했습니다. ANTHROPIC_API_KEY 를 설정하면 Claude 가 페르소나가 되어 이미지까지 읽고 분석합니다.") : null,
  );
}

function renderFilters() {
  const box = $("#filters");
  if (!state.result) return box.replaceChildren();
  box.replaceChildren(...["critical", "warning", "suggestion"].map((s) =>
    el("label", {},
      el("input", { type: "checkbox", checked: state.filter.has(s), onchange: (e) => {
        e.target.checked ? state.filter.add(s) : state.filter.delete(s);
        renderPages();
        renderMemos();
      } }),
      el("span", { class: `pill ${s}` }, SEV[s]))));
}

function visibleIssues(page) {
  return (page?.issues || []).filter((i) => state.filter.has(i.severity));
}

function renderPages() {
  const byPage = Object.fromEntries((state.result?.pages || []).map((p) => [p.page, p]));
  $("#pages").replaceChildren(...state.doc.pages.map((pg) => {
    const rp = byPage[pg.index];
    const issues = visibleIssues(rp);
    // 같은 영역을 가리키는 지적이 여러 개면 번호 배지를 옆으로 나란히 놓는다
    const sameBox = {};
    const badgeOffset = (i) => {
      const key = [i.box.x, i.box.y, i.box.w, i.box.h].map((v) => v.toFixed(2)).join();
      sameBox[key] = (sameBox[key] ?? -1) + 1;
      return sameBox[key] * 24;
    };
    return el("div", { class: "page-card", id: `page-${pg.index}` },
      el("div", { class: "page-label" }, el("span", {}, `페이지 ${pg.index}`), el("span", {}, rp ? `지적 ${issues.length}건` : "")),
      el("div", { class: "page-wrap" },
        el("img", { src: `/api/documents/${state.doc.id}/pages/${pg.index}.png`, alt: `페이지 ${pg.index}`, loading: "lazy" }),
        ...issues.map((i) => el("div", {
          class: `box ${i.severity}`, id: `box-${i.n}`, title: `#${i.n} ${i.title}`,
          style: `left:${i.box.x * 100}%;top:${i.box.y * 100}%;width:${i.box.w * 100}%;height:${i.box.h * 100}%`,
          onmouseenter: () => highlight(i.n, false), onmouseleave: () => highlight(null),
          onclick: () => highlight(i.n, "memo"),
        }, el("span", { class: "num", style: `margin-left:${badgeOffset(i)}px` }, i.n)))),
      rp?.page_comment ? el("div", { class: "page-comment" }, rp.page_comment) : null,
    );
  }));
}

function renderMemos() {
  const box = $("#memos");
  if (!state.result) return box.replaceChildren();
  const issues = state.result.pages.flatMap(visibleIssues);
  if (!issues.length) return box.replaceChildren(el("div", { class: "memos-empty" }, "표시할 지적사항이 없습니다."));
  box.replaceChildren(...issues.map((i) => el("div", {
    class: `memo ${i.severity}`, id: `memo-${i.n}`,
    onmouseenter: () => highlight(i.n, false), onmouseleave: () => highlight(null),
    onclick: () => highlight(i.n, "box"),
  },
    el("div", { class: "memo-head" }, el("span", { class: "n" }, `#${i.n}`), el("span", { class: "title" }, i.title)),
    el("div", { class: "memo-meta" }, `p.${i.page} · ${SEV[i.severity]} · ${i.category}`),
    el("dl", {},
      el("dt", {}, "왜 문제인가"), el("dd", {}, i.reason),
      i.suggestion ? [el("dt", {}, "이렇게 고치세요"), el("dd", {}, i.suggestion)] : null),
    i.persona_reaction ? el("div", { class: "reaction" }, `💭 “${i.persona_reaction}”`) : null,
  )));
}

function highlight(n, scrollTo) {
  document.querySelectorAll(".box.hl, .memo.hl").forEach((x) => x.classList.remove("hl"));
  if (n === null) return;
  const b = document.getElementById(`box-${n}`);
  const m = document.getElementById(`memo-${n}`);
  b?.classList.add("hl");
  m?.classList.add("hl");
  if (scrollTo === "memo") m?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  if (scrollTo === "box") b?.scrollIntoView({ behavior: "smooth", block: "center" });
}

function renderExports() {
  const box = $("#exports");
  if (!state.result) return box.replaceChildren();
  const base = `/api/documents/${state.doc.id}/results/${state.activePersona}`;
  box.replaceChildren(
    el("a", { class: "btn", href: `${base}/export.pdf` }, "⬇ 주석 PDF"),
    el("a", { class: "btn", href: `${base}/export.json` }, "⬇ JSON"),
  );
}

// ------------------------------------------------------------------ 이벤트

function bind() {
  $("#new-persona").addEventListener("click", () => openPersonaDialog());
  $("#persona-cancel").addEventListener("click", () => $("#persona-dialog").close());
  $("#persona-form").addEventListener("submit", savePersona);
  $("#persona-form").addEventListener("input", syncRangeOutputs);
  $("#analyze-btn").addEventListener("click", startAnalysis);

  const dz = $("#dropzone");
  $("#file-input").addEventListener("change", (e) => {
    if (e.target.files[0]) upload(e.target.files[0]);
    e.target.value = "";
  });
  for (const ev of ["dragenter", "dragover"]) dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); });
  for (const ev of ["dragleave", "drop"]) dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); });
  dz.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); });

  $("#url-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const url = $("#url-input").value.trim();
    if (!url) return;
    const fd = new FormData();
    fd.append("url", url);
    sendDocument(fd, url);
  });
}

(async function init() {
  bind();
  try {
    await Promise.all([loadStatus(), loadPersonas(), loadDocs()]);
  } catch (err) {
    toast(`서버 연결 실패: ${err.message}`);
  }
})();
