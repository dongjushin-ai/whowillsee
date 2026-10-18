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
  status: null,
  settings: null,
  clearKey: false,
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
  state.status = s;
  const badge = $("#mode-badge");
  badge.classList.toggle("demo", s.mode !== "ai");
  badge.textContent = s.mode === "ai" ? `AI 연결됨 · ${s.engine}` : "데모 모드 (AI 미연결)";
  badge.title = "AI 연결 설정 열기";
  $("#ai-banner").hidden = s.mode === "ai" || location.hash === "#settings";
}

// ------------------------------------------------------------------ 화면 전환

const VIEWS = ["analyze", "dashboard", "settings"];

function route() {
  const view = VIEWS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "analyze";
  for (const v of VIEWS) $(`#view-${v}`).hidden = v !== view;
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  $("#ai-banner").hidden = state.status?.mode === "ai" || view === "settings";
  if (view === "settings") loadSettings();
  if (view === "dashboard") loadDashboard();
}

// ------------------------------------------------------------------ AI 연결 설정

async function loadSettings() {
  const s = await api("/api/settings");
  state.settings = s;
  const cards = $("#provider-cards");
  const desc = {
    anthropic: "Claude API 키로 연결합니다. 이미지와 텍스트를 함께 읽어 가장 정확하게 분석합니다.",
    openai_compatible: "OpenAI, Gemini, OpenRouter, Ollama·LM Studio(내 컴퓨터) 등 OpenAI 형식 API.",
    demo: "AI 없이 규칙(전문용어·글자 크기·누락 정보)만으로 점검합니다.",
  };
  cards.replaceChildren(...s.providers.map((p) => el("label", { class: "provider-card" },
    el("input", { type: "radio", name: "provider", value: p.id, checked: p.id === s.provider }),
    el("b", {}, p.label), el("small", {}, desc[p.id]))));
  $("#preset-select").replaceChildren(...s.presets.map((p) =>
    el("option", { value: p.id, selected: p.id === s.preset }, p.label)));
  $("#base-url").value = s.base_url || "";
  $("#model-input").value = s.model || "";
  state.settingsSlot = slotOf(s.provider, s.preset);
  $("#effort-select").value = s.effort ?? "medium";
  $("#key-input").value = "";
  $("#settings-msg").hidden = true;
  $("#settings-locked").hidden = !s.locked;
  for (const x of $("#settings-form").elements) x.disabled = s.locked;
  syncSettingsForm();
}

function settingsFormValue() {
  const f = $("#settings-form");
  const provider = f.querySelector("input[name=provider]:checked")?.value || "demo";
  const key = $("#key-input").value.trim();
  return {
    provider,
    preset: $("#preset-select").value,
    base_url: $("#base-url").value.trim(),
    model: $("#model-input").value.trim(),
    effort: $("#effort-select").value,
    api_key: state.clearKey ? "" : key || null, // null = 저장된 키 유지
  };
}

function slotOf(provider, preset) {
  return provider === "openai_compatible" ? `openai:${preset}` : provider;
}

function syncSettingsForm(changedPreset = false) {
  const s = state.settings;
  const v = settingsFormValue();
  // 서비스를 바꾸면 그 서비스에 저장된 모델·키 상태를 불러온다 (키는 서비스별로 따로 저장됨)
  const slot = slotOf(v.provider, v.preset);
  if (slot !== state.settingsSlot) {
    state.settingsSlot = slot;
    state.clearKey = false;
    $("#model-input").value = s.slots[slot]?.model || "";
    $("#key-input").value = "";
  }
  document.querySelectorAll("#settings-form .field-group").forEach((g) => {
    g.hidden = !g.dataset.for.split(" ").includes(v.provider);
  });
  const preset = s.presets.find((p) => p.id === v.preset) || s.presets.at(-1);
  if (changedPreset) $("#base-url").value = "";
  $("#base-url").placeholder = preset.base_url || "https://…/v1";
  const anthropic = s.providers.find((p) => p.id === "anthropic");
  const keyUrl = v.provider === "anthropic" ? anthropic.key_url : preset.key_url;
  const link = $("#key-link");
  link.textContent = keyUrl ? "(키 발급 ↗)" : "";
  link.href = keyUrl || "#";
  if (v.provider === "anthropic") {
    $("#model-input").placeholder = anthropic.default_model;
    $("#model-hint").textContent = `비워두면 ${anthropic.default_model}`;
  } else {
    $("#model-input").placeholder = preset.model_hint;
    $("#model-hint").textContent = preset.needs_key ? "" : "이 서비스는 보통 API 키가 필요 없습니다.";
  }
  const current = slot === slotOf(s.provider, s.preset);
  const saved = s.slots[slot];
  const keyInfo = current && s.has_key ? { source: s.key_source, hint: s.key_hint }
    : saved?.has_key ? { source: "saved", hint: saved.key_hint } : null;
  const stateEl = $("#key-state");
  stateEl.replaceChildren();
  if (state.clearKey) stateEl.append("저장 시 이 서비스의 키가 삭제됩니다.");
  else if (keyInfo) {
    stateEl.append(`${keyInfo.source === "env" ? "환경 변수" : "저장된"} 키 사용 중: ${keyInfo.hint} · 새로 입력하면 교체됩니다. `);
    if (keyInfo.source === "saved") stateEl.append(el("a", { href: "#", onclick: (e) => { e.preventDefault(); state.clearKey = true; syncSettingsForm(); } }, "키 삭제"));
  }
}

function showSettingsMsg(kind, text) {
  const m = $("#settings-msg");
  m.className = `note ${kind}`;
  m.textContent = text;
  m.hidden = false;
}

async function testSettings() {
  showSettingsMsg("", "연결 테스트 중… (작은 이미지 1장을 보내 확인합니다)");
  try {
    const r = await api("/api/settings/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settingsFormValue()) });
    showSettingsMsg(r.ok ? "ok" : "err", r.ok ? `✓ ${r.message} · ${(r.latency_ms / 1000).toFixed(1)}초` : `✕ ${r.message}`);
  } catch (err) {
    showSettingsMsg("err", `✕ ${err.message}`);
  }
}

async function saveSettings(e) {
  e.preventDefault();
  try {
    const r = await api("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settingsFormValue()) });
    state.clearKey = false;
    await loadStatus();
    await loadSettings();
    showSettingsMsg(r.provider === "demo" || r.ready ? "ok" : "err",
      r.provider === "demo" ? "저장했습니다. 데모 모드로 분석합니다."
        : r.ready ? `저장했습니다. 이제 ${r.engine} 로 분석합니다.` : `저장했지만 아직 사용할 수 없습니다: ${r.problems.join(" ")}`);
  } catch (err) {
    showSettingsMsg("err", `저장 실패: ${err.message}`);
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
      el("span", { class: "fn", title: d.filename }, `${d.filename}`, d.version > 1 ? el("span", { class: "vtag" }, `v${d.version}`) : null),
      el("span", { class: "muted" }, `${d.page_count}p`),
      el("button", { title: "삭제", onclick: (e) => { e.stopPropagation(); deleteDoc(d); } }, "✕"),
    )));
}

async function upload(file, parentId = null) {
  const fd = new FormData();
  fd.append("file", file);
  if (parentId) fd.append("parent_id", parentId);
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
  const ver = state.doc.version > 1 ? ` · v${state.doc.version} (수정본)` : "";
  $("#doc-sub").textContent = `${state.doc.pages.length}페이지 · ${state.doc.created_at}${ver}`;
  if (!state.results.length && state.doc.parent_id) {
    // 수정본은 이전 버전과 같은 청중으로 분석하도록 미리 선택한다
    try {
      const prev = await api(`/api/documents/${state.doc.parent_id}/results`);
      if (prev.length) {
        state.selected = new Set(prev.map((r) => r.persona_id).filter((id) => state.personas.some((p) => p.id === id)));
        if (!state.selected.size) state.selected.add("general-citizen");
        renderPersonas();
        toast("이전 버전과 같은 청중을 선택했습니다. '분석 시작'을 누르면 비교 결과가 나옵니다.", 5000);
      }
    } catch (_) { /* 이전 버전이 삭제됨 */ }
  }
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
  renderCompare();
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
      el("p", { class: "muted" }, `${r.engine ? `분석: ${r.engine}` : "데모 모드(규칙 기반)"} · ${r.created_at}`),
      el("div", { class: "cols" },
        o.top_fixes?.length ? el("div", {}, el("h3", {}, "우선 고칠 것"), el("ol", {}, ...o.top_fixes.map((t) => el("li", {}, t)))) : null,
        o.strengths?.length ? el("div", {}, el("h3", {}, "잘된 점"), el("ul", {}, ...o.strengths.map((t) => el("li", {}, t)))) : null,
      ),
    ),
    r.mode === "demo" ? el("div", { class: "demo-note" },
      "데모 모드 결과입니다. 텍스트 규칙(전문용어·글자 크기·정보 밀도·누락 정보)만으로 추정했습니다. 상단 'AI 연결'에서 내 AI를 연결하면 페르소나가 이미지까지 읽고 분석합니다.") : null,
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

async function renderCompare() {
  const box = $("#compare");
  box.hidden = true;
  if (!state.result || !state.doc.parent_id) return;
  const docId = state.doc.id;
  const personaId = state.activePersona;
  let c;
  try {
    c = await api(`/api/documents/${docId}/compare/${personaId}`);
  } catch (_) {
    return; // 이전 버전에 같은 청중의 결과가 없음
  }
  if (state.doc.id !== docId || state.activePersona !== personaId) return;
  const d = c.delta_score;
  const sevLabel = (i) => `${SEV[i.severity]}·${i.category}`;
  const item = (i, link) => el("li", link ? { class: "link", onclick: () => highlight(i.n, "box") } : {},
    `#${i.n} ${i.title} `, el("span", { class: "tag" }, `p.${i.page} ${sevLabel(i)}`));
  const changeText = { better: " (완화됨)", worse: " (악화됨)", same: "" };
  box.replaceChildren(
    el("div", { class: "compare-head" },
      el("h3", {}, `v${c.prev.version} → v${c.cur.version} 비교`),
      el("span", { class: `delta ${d > 0 ? "up" : d < 0 ? "down" : ""}` }, `${c.prev.score} → ${c.cur.score} (${d > 0 ? "+" : ""}${d})`),
      el("span", { class: "muted" }, `해결 ${c.resolved.length} · 남음 ${c.remaining.length} · 새로 생김 ${c.new.length}`)),
    el("div", { class: "compare-cols" },
      el("div", { class: "compare-col" }, el("h4", {}, `✓ 해결된 지적 (${c.resolved.length})`),
        el("ul", {}, ...c.resolved.map((i) => item(i, false)), c.resolved.length ? null : el("li", { class: "muted" }, "없음"))),
      el("div", { class: "compare-col" }, el("h4", {}, `↻ 아직 남은 지적 (${c.remaining.length})`),
        el("ul", {}, ...c.remaining.map((r) => {
          const li = item(r.cur, true);
          li.append(el("span", { class: "tag" }, changeText[r.change]));
          return li;
        }), c.remaining.length ? null : el("li", { class: "muted" }, "없음"))),
      el("div", { class: "compare-col" }, el("h4", {}, `＋ 새로 생긴 지적 (${c.new.length})`),
        el("ul", {}, ...c.new.map((i) => item(i, true)), c.new.length ? null : el("li", { class: "muted" }, "없음")))),
    el("div", { class: "compare-note" }, "같은 문제인지는 유형·제목·해당 문구의 유사도로 추정합니다. 해결된 지적은 이전 버전의 번호입니다."),
  );
  box.hidden = false;
}

// ------------------------------------------------------------------ 이벤트

function bind() {
  $("#new-persona").addEventListener("click", () => openPersonaDialog());
  $("#persona-cancel").addEventListener("click", () => $("#persona-dialog").close());
  $("#persona-form").addEventListener("submit", savePersona);
  $("#persona-form").addEventListener("input", syncRangeOutputs);
  $("#analyze-btn").addEventListener("click", startAnalysis);

  $("#version-input").addEventListener("change", (e) => {
    if (e.target.files[0] && state.doc) upload(e.target.files[0], state.doc.id);
    e.target.value = "";
  });

  const dz = $("#dropzone");
  $("#file-input").addEventListener("change", (e) => {
    if (e.target.files[0]) upload(e.target.files[0]);
    e.target.value = "";
  });
  for (const ev of ["dragenter", "dragover"]) dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); });
  for (const ev of ["dragleave", "drop"]) dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); });
  dz.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); });

  window.addEventListener("hashchange", route);
  $("#settings-form").addEventListener("change", (e) => {
    syncSettingsForm(e.target.id === "preset-select");
  });
  $("#settings-form").addEventListener("submit", saveSettings);
  $("#test-btn").addEventListener("click", testSettings);

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
    route();
  } catch (err) {
    toast(`서버 연결 실패: ${err.message}`);
  }
})();

// ------------------------------------------------------------------ 대시보드

const SEQ = ["var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)"];
const SEV_COLOR = { critical: "var(--critical)", warning: "var(--warning)", suggestion: "var(--suggestion)" };

function seqStep(score) {
  return Math.min(4, Math.floor(score / 20));
}

function bindTip(node, html) {
  const tip = $("#tooltip");
  node.addEventListener("mousemove", (e) => {
    tip.replaceChildren(...html());
    tip.hidden = false;
    const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    const y = Math.min(e.clientY + 14, window.innerHeight - tip.offsetHeight - 8);
    tip.style.left = `${x}px`;
    tip.style.top = `${y}px`;
  });
  node.addEventListener("mouseleave", () => (tip.hidden = true));
  return node;
}

async function loadDashboard() {
  const box = $("#dashboard");
  let d;
  try {
    d = await api("/api/stats");
  } catch (err) {
    box.replaceChildren(el("div", { class: "dash-empty" }, `불러오기 실패: ${err.message}`));
    return;
  }
  const t = d.totals;
  const head = el("div", { class: "dash-head" },
    el("h1", {}, "대시보드"),
    el("span", { class: "muted" }, state.status?.mode === "ai" ? `분석 엔진: ${state.status.engine}` : "데모 모드 (AI 미연결)"));
  if (!t.analyses) {
    box.replaceChildren(head, el("div", { class: "card dash-empty" },
      el("p", {}, "아직 분석한 자료가 없습니다."),
      el("a", { class: "btn primary", href: "#analyze" }, "자료 분석하러 가기")));
    return;
  }
  box.replaceChildren(
    head,
    el("div", { class: "kpis" },
      kpi("분석한 자료", t.documents, `분석 ${t.analyses}회`),
      kpi("평균 전달력", t.avg_score, "100점 만점, 모든 청중 평균"),
      kpi("발견한 지적", t.issues, null, ["critical", "warning", "suggestion"].map((s) => el("span", { class: `pill ${s}` }, `${SEV[s]} ${t[s]}`))),
      kpi("가장 어려워한 청중", d.personas[0] ? `${d.personas[0].icon} ${d.personas[0].avg_score}` : "–", d.personas[0]?.name || ""),
    ),
    el("div", { class: "dash-grid" },
      el("div", { class: "card" },
        el("h2", {}, "청중별 평균 전달력"),
        el("div", { class: "desc" }, "점수가 낮을수록 그 청중에게 자료가 잘 전달되지 않습니다."),
        personaBars(d.personas)),
      el("div", { class: "card" },
        el("h2", {}, "자주 나온 문제 유형"),
        el("div", { class: "desc" }, "모든 분석의 지적사항을 유형과 심각도로 묶었습니다."),
        el("div", { class: "legend" }, ...["critical", "warning", "suggestion"].map((s) =>
          el("span", {}, el("i", { style: `background:${SEV_COLOR[s]}` }), SEV[s]))),
        categoryBars(d.categories)),
    ),
    el("div", { class: "card", style: "margin-bottom:16px" },
      el("h2", {}, "자료 × 청중 점수표"),
      el("div", { class: "desc" }, "칸을 누르면 그 결과로 이동합니다. 최근 자료 12개까지 표시합니다."),
      el("div", { class: "ramp-legend" }, "0", ...SEQ.map((c) => el("i", { style: `background:${c}` })), "100"),
      heatTable(d.matrix)),
    d.versions.length ? el("div", { class: "card", style: "margin-bottom:16px" },
      el("h2", {}, "수정본 개선 추이"),
      el("div", { class: "desc" }, "버전별 평균 전달력 (그 버전을 분석한 모든 청중의 평균)과 지적 수."),
      ...d.versions.map((v) => el("div", { class: "trend-row" },
        el("span", { class: "fn" }, v.filename),
        el("span", { class: "trend-steps" }, ...v.points.flatMap((pt, i) => [
          i ? el("span", { class: "muted" }, "→") : null,
          el("span", { title: `지적 ${pt.issues}건` }, el("span", { class: "vtag" }, `v${pt.version}`), " ",
            el("span", { class: "step-score" }, pt.avg_score)),
        ]), (() => {
          const diff = Math.round((v.points.at(-1).avg_score - v.points[0].avg_score) * 10) / 10;
          return el("span", { class: `delta ${diff > 0 ? "up" : diff < 0 ? "down" : ""}`, style: "font-size:14px" }, `${diff > 0 ? "+" : ""}${diff}`);
        })())))) : null,
    el("div", { class: "card" },
      el("h2", {}, "최근 분석"),
      el("div", { class: "recent" }, ...d.recent.map((r) =>
        el("div", { class: "recent-row", onclick: () => goToResult(r.doc_id, r.persona_id) },
          el("span", { class: "fn", title: r.filename }, `${r.filename}`, el("span", { class: "muted" }, ` · ${r.icon} ${r.persona_name}`)),
          el("span", { class: "muted" }, `${r.created_at.slice(5, 16)}`),
          el("span", { class: "score" }, r.score))))),
  );
}

function kpi(label, value, sub, subNodes) {
  return el("div", { class: "kpi" },
    el("div", { class: "label" }, label),
    el("div", { class: "value" }, value ?? "–"),
    sub || subNodes ? el("div", { class: "sub" }, sub, subNodes) : null);
}

function personaBars(rows) {
  return el("div", { class: "hbar-chart" },
    ...rows.flatMap((p) => [
      el("div", { class: "hbar-label", title: p.name }, `${p.icon} ${p.name}`),
      el("div", { class: "hbar-track" },
        bindTip(el("div", { class: "hbar", style: `width:${Math.max(p.avg_score, 1)}%` },
          el("div", { class: "seg", style: "flex:1;background:var(--series-1)" })),
        () => [el("b", {}, `${p.icon} ${p.name}`), el("br"), `평균 ${p.avg_score}점 · 분석 ${p.count}회`]),
        el("span", { class: "hbar-value" }, p.avg_score)),
    ]),
    el("div", { class: "hbar-axis" }, ...[0, 25, 50, 75, 100].map((v) => el("span", {}, v))));
}

function categoryBars(rows) {
  const max = Math.max(1, ...rows.map((r) => r.total));
  return el("div", { class: "hbar-chart" },
    ...rows.flatMap((c) => [
      el("div", { class: "hbar-label", title: c.category }, c.category),
      el("div", { class: "hbar-track", style: "background-image:none" },
        el("div", { class: "hbar", style: `width:${(c.total / max) * 88}%` },
          ...["critical", "warning", "suggestion"].filter((s) => c[s] > 0).map((s) =>
            bindTip(el("div", { class: "seg", style: `flex:${c[s]};background:${SEV_COLOR[s]}` }),
              () => [el("b", {}, c.category), el("br"), `${SEV[s]} ${c[s]}건 / 전체 ${c.total}건`]))),
        el("span", { class: "hbar-value" }, c.total)),
    ]));
}

function heatTable(m) {
  return el("div", { class: "heat-wrap" }, el("table", { class: "heat" },
    el("thead", {}, el("tr", {}, el("th", {}, "자료"), ...m.personas.map((p) => el("th", { title: p.name }, `${p.icon} ${p.name}`)))),
    el("tbody", {}, ...m.docs.map((doc) => el("tr", {},
      el("th", { class: "doc", title: doc.filename }, doc.version > 1 ? `${doc.filename} (v${doc.version})` : doc.filename),
      ...m.personas.map((p) => {
        const score = m.cells[doc.id]?.[p.id];
        if (score === undefined) return el("td", { class: "none" }, "–");
        const step = seqStep(score);
        return bindTip(el("td", {
          style: `background:${SEQ[step]};color:${step >= 3 ? "#fff" : "#0b0b0b"}`,
          onclick: () => goToResult(doc.id, p.id),
        }, score), () => [el("b", {}, doc.filename), el("br"), `${p.icon} ${p.name}: ${score}점`]);
      }))))));
}

async function goToResult(docId, personaId) {
  $("#tooltip").hidden = true;
  location.hash = "#analyze";
  await openDoc(docId, personaId);
}
