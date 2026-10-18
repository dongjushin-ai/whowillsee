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
  audiences: [],
  audience: null,        // 분석에 쓸 청중 그룹 id
  docAudiences: [],      // 현재 문서에서 결과가 있는 청중 그룹
  activeAudience: null,  // 결과 화면에서 보고 있는 청중 그룹
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

// replaceChildren 은 null 을 "null" 글자로 바꿔 넣으므로, 빈 값은 걸러서 채운다
function fill(node, ...children) {
  node.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
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

// ------------------------------------------------------------------ 테마 (자동 / 라이트 / 다크)

const THEMES = ["auto", "light", "dark"];
const THEME_LABEL = { auto: "🌓 자동", light: "☀️ 라이트", dark: "🌙 다크" };

function currentTheme() {
  try { return localStorage.getItem("whowillsee.theme") || "auto"; } catch (_) { return "auto"; }
}

function applyTheme(theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  $("#theme-btn").textContent = THEME_LABEL[theme];
  $("#theme-btn").title = "화면 테마 바꾸기 (자동은 시스템 설정을 따릅니다)";
}

function cycleTheme() {
  const next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
  try { localStorage.setItem("whowillsee.theme", next); } catch (_) { /* 저장 못 해도 이번 화면에는 적용 */ }
  applyTheme(next);
}

// ------------------------------------------------------------------ 화면 전환

const VIEWS = ["analyze", "dashboard", "settings"];

function route() {
  const view = VIEWS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "analyze";
  for (const v of VIEWS) $(`#view-${v}`).hidden = v !== view;
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  $("#ai-banner").hidden = state.status?.mode === "ai" || view === "settings";
  if (view === "settings") {
    loadSettings();
    loadSpeech();
  }
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
  fill(cards, ...s.providers.map((p) => el("label", { class: "provider-card" },
    el("input", { type: "radio", name: "provider", value: p.id, checked: p.id === s.provider }),
    el("b", {}, p.label), el("small", {}, desc[p.id]))));
  fill($("#preset-select"), ...s.presets.map((p) =>
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
  fill(stateEl);
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
  fill(list, ...state.personas.map((p) => {
    const selected = state.selected.has(p.id);
    const desc = [p.occupation, `전문성 ${LEVELS[p.expertise_level]}`].filter(Boolean).join(" · ");
    const member = currentAudience()?.members.find((m) => m.persona_id === p.id);
    return el("div", {
      class: "persona" + (selected ? " selected" : ""),
      title: p.needs ? `니즈: ${p.needs}` : "",
      onclick: () => {
        if (selected && state.selected.size > 1) state.selected.delete(p.id);
        else state.selected.add(p.id);
        state.audience = null; // 개별로 고치면 그룹 선택은 풀린다
        renderAudiences();
        renderPersonas();
      },
    },
      el("input", { type: "checkbox", checked: selected, tabindex: -1 }),
      el("span", { class: "icon" }, p.icon || "🙂"),
      el("div", {}, el("div", { class: "name" }, p.name, p.builtin ? "" : " ✱", member ? el("span", { class: "weight" }, `${member.weight}`) : null),
        el("div", { class: "desc" }, desc)),
      el("div", { class: "actions" },
        el("button", { title: "복제해서 새로 만들기", onclick: (e) => { e.stopPropagation(); openPersonaDialog(p, true); } }, "⧉"),
        p.builtin ? null : el("button", { title: "수정", onclick: (e) => { e.stopPropagation(); openPersonaDialog(p, false); } }, "✎"),
        p.builtin ? null : el("button", { title: "삭제", onclick: (e) => { e.stopPropagation(); deletePersona(p); } }, "🗑"),
      ),
    );
  }));
}

// ------------------------------------------------------------------ 청중 그룹

function currentAudience() {
  return state.audiences.find((a) => a.id === state.audience) || null;
}

async function loadAudiences() {
  state.audiences = await api("/api/audiences");
  if (state.audience && !currentAudience()) state.audience = null;
  renderAudiences();
}

function renderAudiences() {
  fill($("#audience-list"), 
    ...state.audiences.map((a) => el("span", {
      class: "chip" + (a.id === state.audience ? " selected" : ""),
      title: a.members.map((m) => `${personaName(m.persona_id)} ${m.weight}`).join(" · "),
      onclick: () => selectAudience(a.id),
    }, `${a.icon} ${a.name}`,
      el("button", { class: "edit", title: a.builtin ? "복제해서 새로 만들기" : "수정",
        onclick: (e) => { e.stopPropagation(); openAudienceDialog(a, a.builtin); } }, a.builtin ? "⧉" : "✎"))),
    el("span", { class: "chip add", onclick: () => openAudienceDialog() }, "+ 그룹"),
  );
}

function personaName(id) {
  const p = state.personas.find((x) => x.id === id);
  return p ? `${p.icon} ${p.name}` : id;
}

function selectAudience(id) {
  if (state.audience === id) {
    state.audience = null;
  } else {
    const a = state.audiences.find((x) => x.id === id);
    state.audience = id;
    state.selected = new Set(a.members.map((m) => m.persona_id));
    if (a.context && !$("#context-input").value.trim()) $("#context-input").value = a.context;
  }
  renderAudiences();
  renderPersonas();
}

function openAudienceDialog(audience = null, duplicate = false) {
  const form = $("#audience-form");
  form.reset();
  state.editingAudience = audience && !duplicate ? audience.id : null;
  $("#audience-dialog-title").textContent = state.editingAudience ? "청중 그룹 수정" : duplicate ? "청중 그룹 복제" : "새 청중 그룹";
  $("#audience-delete").hidden = !state.editingAudience;
  form.elements.name.value = audience ? (duplicate ? `${audience.name} (복사본)` : audience.name) : "";
  form.elements.icon.value = audience?.icon || "👥";
  form.elements.context.value = audience?.context || "";
  const weights = Object.fromEntries((audience?.members || []).map((m) => [m.persona_id, m.weight]));
  fill($("#audience-members"), ...state.personas.map((p) => {
    const on = p.id in weights;
    const range = el("input", { type: "range", min: 1, max: 100, value: weights[p.id] || 20, disabled: !on, "data-id": p.id });
    const out = el("output", {}, on ? weights[p.id] : "–");
    const check = el("input", { type: "checkbox", checked: on, "data-id": p.id });
    check.addEventListener("change", () => {
      range.disabled = !check.checked;
      syncAudienceSum();
    });
    range.addEventListener("input", syncAudienceSum);
    return el("label", { class: "member-row" }, check, el("span", {}, `${p.icon} ${p.name}`), range, out);
  }));
  syncAudienceSum();
  $("#audience-dialog").showModal();
}

function audienceMembers() {
  return [...document.querySelectorAll("#audience-members .member-row")]
    .filter((row) => row.querySelector("input[type=checkbox]").checked)
    .map((row) => ({ persona_id: row.querySelector("input[type=checkbox]").dataset.id,
      weight: Number(row.querySelector("input[type=range]").value) }));
}

function syncAudienceSum() {
  const members = audienceMembers();
  const total = members.reduce((a, m) => a + m.weight, 0);
  for (const row of document.querySelectorAll("#audience-members .member-row")) {
    const on = row.querySelector("input[type=checkbox]").checked;
    const w = Number(row.querySelector("input[type=range]").value);
    row.querySelector("output").textContent = on ? `${Math.round((100 * w) / Math.max(total, 1))}%` : "–";
  }
  $("#audience-sum").textContent = members.length
    ? `${members.length}명 · 비율은 자동으로 100%가 되도록 맞춰집니다 (최대 8명)`
    : "구성원을 한 명 이상 고르세요.";
}

async function saveAudience(e) {
  e.preventDefault();
  const form = $("#audience-form");
  const body = { name: form.elements.name.value.trim(), icon: form.elements.icon.value.trim() || "👥",
    context: form.elements.context.value.trim(), members: audienceMembers() };
  if (!body.members.length) return toast("구성원을 한 명 이상 고르세요.");
  try {
    const saved = state.editingAudience
      ? await api(`/api/audiences/${state.editingAudience}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      : await api("/api/audiences", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    $("#audience-dialog").close();
    await loadAudiences();
    selectAudience(saved.id);
    toast(`'${saved.name}' 청중 그룹을 저장했습니다.`);
  } catch (err) {
    toast(`저장 실패: ${err.message}`);
  }
}

async function deleteAudience() {
  const id = state.editingAudience;
  if (!id || !confirm("이 청중 그룹을 삭제할까요?")) return;
  await api(`/api/audiences/${id}`, { method: "DELETE" });
  if (state.audience === id) state.audience = null;
  $("#audience-dialog").close();
  await loadAudiences();
  renderPersonas();
}

function openPersonaDialog(persona = null, duplicate = false) {
  const form = $("#persona-form");
  form.reset();
  $("#autofill-input").value = "";
  $("#autofill-msg").textContent = "";
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

async function autofillPersona() {
  const desc = $("#autofill-input").value.trim();
  const msg = $("#autofill-msg");
  if (desc.length < 2) {
    msg.textContent = "설명을 조금 더 적어주세요.";
    return;
  }
  const btn = $("#autofill-btn");
  btn.disabled = true;
  msg.textContent = state.status?.mode === "ai" ? "AI가 페르소나를 구체화하는 중…" : "초안을 만드는 중…";
  try {
    const r = await api("/api/personas/generate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ description: desc }),
    });
    const form = $("#persona-form");
    for (const [k, v] of Object.entries(r.persona)) {
      if (form.elements[k]) form.elements[k].value = v;
    }
    syncRangeOutputs();
    msg.textContent = r.engine
      ? `${r.engine} 가 채웠습니다. 확인하고 고친 뒤 저장하세요.`
      : "규칙 기반 초안입니다. AI를 연결하면 모든 항목을 더 구체적으로 채웁니다. 확인 후 저장하세요.";
  } catch (err) {
    msg.textContent = `자동 채우기 실패: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
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
  try {
    await api(`/api/personas/${p.id}`, { method: "DELETE" });
  } catch (err) {
    return toast(err.message, 5000);
  }
  state.selected.delete(p.id);
  await loadPersonas();
}

// ------------------------------------------------------------------ 문서

async function loadDocs() {
  state.docs = await api("/api/documents");
  renderDocs();
}

function renderDocs() {
  fill($("#doc-list"), ...state.docs.slice(0, 12).map((d) =>
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

async function openDoc(id, personaId = null, audienceId = null) {
  state.doc = await api(`/api/documents/${id}`);
  [state.results, state.docAudiences] = await Promise.all([
    api(`/api/documents/${id}/results`), api(`/api/documents/${id}/audiences`)]);
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
  updateScriptButton();
  const aud = audienceId || (!personaId && state.activeAudience);
  if (aud && state.docAudiences.some((a) => a.audience_id === aud)) return openAudience(aud);
  const pick = personaId || state.activePersona;
  const target = state.results.find((r) => r.persona_id === pick) || state.results[0];
  if (target) await openResult(target.persona_id);
  else {
    state.result = null;
    state.activePersona = null;
    state.activeAudience = null;
    render();
  }
}

// ------------------------------------------------------------------ 분석

async function startAnalysis() {
  if (!state.doc) return;
  const body = { persona_ids: state.audience ? [] : [...state.selected], audience_id: state.audience || "",
    context: $("#context-input").value };
  $("#analyze-btn").disabled = true;
  try {
    const job = await api(`/api/documents/${state.doc.id}/analyze`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    pollJob(job.id, state.doc.id, state.audience);
    try {
      localStorage.setItem("whowillsee.job", JSON.stringify({ id: job.id, doc: state.doc.id, audience: state.audience }));
    } catch (_) { /* 저장소를 못 써도 동작에는 문제없음 */ }
  } catch (err) {
    toast(`분석 시작 실패: ${err.message}`);
    $("#analyze-btn").disabled = false;
  }
}

function pollJob(jobId, docId, audienceId = null) {
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
      try { localStorage.removeItem("whowillsee.job"); } catch (_) { /* 무시 */ }
      $("#analyze-btn").disabled = false;
      const firstDone = job.items.find((i) => i.status === "done");
      if (state.doc?.id === docId) await openDoc(docId, audienceId ? null : firstDone?.persona_id, audienceId);
      await loadDocs();
      if (job.status === "done") {
        setProgress([]);
        const doneMsg = {
          recording: "받아쓰기가 끝났습니다. '🎤 발표 대본'에서 확인하고, '분석 시작'을 누르면 실제 발표 기준으로 평가합니다.",
          video: "영상 분석이 끝났습니다. '분석 시작'을 누르면 청중 관점의 발표 모습 평가가 함께 나옵니다.",
        }[job.kind];
        toast(doneMsg || "분석이 끝났습니다.", doneMsg ? 7000 : 3500);
      }
    }
  };
  state.polling = setInterval(tick, 1200);
  tick();
}

function setProgress(items) {
  fill($("#progress"), ...items.map((i) => {
    const pct = i.status === "done" ? 100 : Math.round((i.done / Math.max(i.total, 1)) * 100);
    const label = { queued: "대기 중", running: "분석 중…", done: "완료", error: "실패" }[i.status] || "";
    return el("div", {},
      el("div", {}, `${i.name} `, el("span", { class: "muted" }, label)),
      i.status === "error" ? el("div", { class: "err" }, i.error) : el("div", { class: "bar" }, el("i", { style: `width:${Math.max(pct, 4)}%` })),
    );
  }));
}

// ------------------------------------------------------------------ 음성 인식 설정

async function loadSpeech() {
  const s = await api("/api/speech");
  state.speech = s;
  fill($("#speech-preset"), ...s.presets.map((p) => el("option", { value: p.id, selected: p.id === s.preset }, p.label)));
  $("#speech-enabled").checked = s.enabled;
  $("#speech-base").value = s.base_url || "";
  $("#speech-model").value = s.model || "";
  $("#speech-lang").value = s.language || "ko";
  $("#speech-key").value = "";
  $("#speech-msg").hidden = true;
  for (const x of $("#speech-form").elements) x.disabled = s.locked;
  syncSpeechForm();
}

function syncSpeechForm() {
  const s = state.speech;
  if (!s) return;
  const preset = s.presets.find((p) => p.id === $("#speech-preset").value) || s.presets.at(-1);
  $("#speech-fields").hidden = !$("#speech-enabled").checked;
  $("#speech-base").placeholder = preset.base_url || "https://…/v1";
  $("#speech-model").placeholder = preset.model_hint;
  const link = $("#speech-key-link");
  link.textContent = preset.key_url ? "(키 발급 ↗)" : "";
  link.href = preset.key_url || "#";
  const saved = s.keys[preset.id];
  const st = $("#speech-key-state");
  if (preset.id === s.preset && s.key_source === "shared") st.textContent = `분석용 AI 연결의 OpenAI 키를 함께 씁니다 (${s.key_hint}).`;
  else if (saved) st.textContent = `저장된 키 사용 중: ${saved} · 새로 입력하면 교체됩니다.`;
  else st.textContent = preset.needs_key ? (preset.id === "openai" ? "비워두면 'AI 연결'에 저장한 OpenAI 키를 함께 씁니다." : "") : "보통 키가 필요 없습니다.";
}

function speechFormValue() {
  const key = $("#speech-key").value.trim();
  return { enabled: $("#speech-enabled").checked, preset: $("#speech-preset").value, base_url: $("#speech-base").value.trim(),
    model: $("#speech-model").value.trim(), language: $("#speech-lang").value.trim() || "ko", api_key: key || null };
}

function speechMsg(kind, text) {
  const m = $("#speech-msg");
  m.className = `note ${kind}`;
  m.textContent = text;
  m.hidden = false;
}

async function testSpeech() {
  speechMsg("", "연결 테스트 중… (1초짜리 무음 파일을 보내 확인합니다)");
  try {
    const r = await api("/api/speech/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(speechFormValue()) });
    speechMsg(r.ok ? "ok" : "err", r.ok ? `✓ ${r.message} · ${(r.latency_ms / 1000).toFixed(1)}초` : `✕ ${r.message}`);
  } catch (err) {
    speechMsg("err", `✕ ${err.message}`);
  }
}

async function saveSpeech(e) {
  e.preventDefault();
  try {
    const r = await api("/api/speech", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(speechFormValue()) });
    await loadSpeech();
    speechMsg(!r.enabled || r.ready ? "ok" : "err", !r.enabled ? "저장했습니다. 음성 인식을 쓰지 않습니다."
      : r.ready ? "저장했습니다. 이제 발표 녹음을 올릴 수 있습니다." : `저장했지만 아직 쓸 수 없습니다: ${r.problems.join(" ")}`);
  } catch (err) {
    speechMsg("err", `저장 실패: ${err.message}`);
  }
}

// ------------------------------------------------------------------ 발표 녹음

async function uploadRecording(file) {
  if (!state.doc) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const job = await api(`/api/documents/${state.doc.id}/recording`, { method: "POST", body: fd });
    pollJob(job.id, state.doc.id);
  } catch (err) {
    toast(err.message.includes("음성 인식") ? `${err.message} → 상단 'AI 연결' 아래 '음성 인식'을 설정하세요.` : `녹음 올리기 실패: ${err.message}`, 7000);
  }
}

async function deleteRecording() {
  if (!confirm("녹음 분석 결과를 지울까요? (받아쓴 대본은 남습니다)")) return;
  await api(`/api/documents/${state.doc.id}/recording`, { method: "DELETE" });
  state.doc = await api(`/api/documents/${state.doc.id}`);
  $("#script-dialog").close();
  toast("녹음 분석을 지웠습니다. 발표 시간은 다시 대본 글자 수로 추정합니다.");
}

function deliveryLine(d) {
  if (!d) return null;
  const fillers = Object.entries(d.fillers || {}).slice(0, 3).map(([k, v]) => `${k} ${v}`).join(", ");
  return el("div", { class: "delivery" },
    el("span", {}, "🎙 실제 발표 ", el("b", {}, `${(d.duration_seconds / 60).toFixed(1)}분`)),
    d.rate ? el("span", {}, "말 속도 ", el("b", {}, `${d.rate}자/분`), el("span", { class: "muted" }, " (보통 300~350)")) : null,
    el("span", {}, "말버릇 ", el("b", {}, `${d.filler_total}회`), fillers ? el("span", { class: "muted" }, ` (${fillers})`) : null),
    el("span", {}, "긴 멈춤 ", el("b", {}, `${d.long_pauses}회`)),
    d.timed ? null : el("span", { class: "muted" }, "· 이 음성 인식 서버는 구간 시간을 주지 않아 페이지별 시간은 추정치입니다"));
}

// ------------------------------------------------------------------ 발표 영상

async function uploadVideo(file) {
  if (!state.doc) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const job = await api(`/api/documents/${state.doc.id}/video`, { method: "POST", body: fd });
    pollJob(job.id, state.doc.id);
  } catch (err) {
    toast(`영상 올리기 실패: ${err.message}`, 6000);
  }
}

const VIDEO_AUDIO_NOTE = {
  transcribed: "영상 속 음성도 받아써서 발표 대본과 실제 시간을 채웠습니다.",
  no_speech_setup: "음성은 분석하지 않았습니다. 'AI 연결 > 음성 인식'을 켜면 영상 속 말도 함께 봅니다.",
  no_audio: "영상에 음성이 없습니다.",
  no_ffmpeg: "서버에 ffmpeg 가 없어 음성을 뽑지 못했습니다.",
};
const VSTATE = { frontal: "정면", side: "옆·아래", away: "화면 밖" };

function renderVideo() {
  const box = $("#video-panel");
  const v = state.doc?.video;
  box.hidden = !v;
  if (!v) return fill(box);
  const m = v.metrics;
  const fb = state.result?.overall?.video?.feedback;
  const frameUrl = (k) => `/api/documents/${state.doc.id}/video/${k.image}`;
  const nearest = (t) => v.keyframes.reduce((a, k) => (Math.abs(k.t - t) < Math.abs(a.t - t) ? k : a), v.keyframes[0]);
  const audio = v.audio?.startsWith("error") ? `음성 받아쓰기 실패: ${v.audio.slice(7)}` : VIDEO_AUDIO_NOTE[v.audio] || "";
  fill(box,
    el("div", { class: "video-head" },
      el("h3", {}, `🎥 발표 모습 · ${v.filename}`),
      el("button", { class: "btn small", onclick: deleteVideo }, "영상 분석 지우기")),
    el("div", { class: "video-stats" },
      el("span", {}, "정면을 본 장면 ", el("b", {}, `${Math.round(m.frontal_ratio * 100)}%`)),
      el("span", {}, "화면 안 ", el("b", {}, `${Math.round(m.visible_ratio * 100)}%`)),
      el("span", {}, "정면을 안 본 가장 긴 구간 ", el("b", {}, `${Math.round(m.longest_away_seconds)}초`)),
      el("span", {}, "밝기 ", el("b", {}, m.brightness.toFixed(2))),
      el("span", { class: "muted" }, "(정면 얼굴 검출로 잰 대략적 추정)")),
    el("div", { class: "legend" }, ...Object.entries(VSTATE).map(([k, label]) => el("span", {}, el("i", { class: k }), label))),
    el("div", { class: "timeline" }, ...m.timeline.map((x) =>
      bindTip(el("i", { class: x.state }), () => [`${Math.floor(x.t / 60)}:${String(Math.round(x.t % 60)).padStart(2, "0")} · ${VSTATE[x.state]}`]))),
    el("div", { class: "timeline-axis" }, el("span", {}, "0:00"),
      el("span", {}, `${Math.floor(m.duration_seconds / 60)}:${String(Math.round(m.duration_seconds % 60)).padStart(2, "0")}`)),
    audio ? el("div", { class: "muted" }, audio) : null,
    !state.result ? null
      : !fb ? el("div", { class: "note" }, "영상을 올리기 전에 분석한 결과입니다. '분석 시작'을 다시 누르면 이 청중 관점의 발표 모습 평가가 나옵니다.")
        : !fb.length ? el("div", { class: "note ok" }, "발표 모습에서 눈에 띄는 문제가 없습니다.")
          : el("div", { class: "vfeed" }, ...fb.map((f) => {
            const k = nearest(f.at_seconds);
            return el("div", { class: `memo ${f.severity}` },
              k ? el("img", { src: frameUrl(k), alt: `${Math.round(k.t)}초 장면`, loading: "lazy" }) : el("span"),
              el("div", {},
                el("div", { class: "memo-head" }, el("span", { class: "n" }, f.n), el("span", { class: "title" }, f.title)),
                el("div", { class: "memo-meta" }, `${SEV[f.severity]} · ${f.category} · ${Math.round(f.at_seconds)}초`),
                el("dl", {}, el("dt", {}, "왜 문제인가"), el("dd", {}, f.reason), el("dt", {}, "이렇게 고치세요"), el("dd", {}, f.suggestion)),
                f.persona_reaction ? el("div", { class: "reaction" }, `💭 “${f.persona_reaction}”`) : null));
          })),
  );
}

async function deleteVideo() {
  if (!confirm("영상 분석 결과(장면·지표)를 지울까요?")) return;
  await api(`/api/documents/${state.doc.id}/video`, { method: "DELETE" });
  state.doc = await api(`/api/documents/${state.doc.id}`);
  renderVideo();
}

async function resumeJob() {
  // 새로고침 전에 진행 중이던 분석이 있으면 이어서 진행 상황을 보여준다
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem("whowillsee.job") || "null"); } catch (_) { /* 무시 */ }
  if (!saved?.id) return;
  try {
    await openDoc(saved.doc);
    pollJob(saved.id, saved.doc, saved.audience);
  } catch (_) {
    try { localStorage.removeItem("whowillsee.job"); } catch (__) { /* 무시 */ }
  }
}

// ------------------------------------------------------------------ 발표 대본

const CHARS_PER_MINUTE = 330; // 서버(app/script.py)와 같은 값

function speakMinutes(text) {
  return text.replace(/\s/g, "").length / CHARS_PER_MINUTE;
}

function openScriptDialog() {
  if (!state.doc) return;
  const sc = state.doc.script || { target_minutes: null, pages: {} };
  const form = $("#script-form");
  const rec = state.doc.recording;
  const src = $("#script-source");
  src.hidden = !rec;
  if (rec) {
    fill(src, el("span", {}, `🎙 녹음 '${rec.filename}'(${(rec.metrics.duration_seconds / 60).toFixed(1)}분)에서 받아쓴 대본입니다. `,
      rec.metrics.align_method === "ai" ? "페이지 나누기는 AI가 했습니다." : "페이지 나누기는 자동 추정이니 경계를 확인하세요."),
    el("button", { type: "button", class: "btn small", onclick: deleteRecording }, "녹음 분석 지우기"));
  }
  form.elements.target_minutes.value = sc.target_minutes ?? "";
  fill($("#script-pages"), ...state.doc.pages.map((pg) => {
    const ta = el("textarea", { "data-page": pg.index, placeholder: "이 페이지에서 말할 내용" });
    ta.value = sc.pages?.[String(pg.index)] || "";
    const est = el("span", {});
    const actual = sc.actual_seconds?.[String(pg.index)];
    const sync = () => {
      est.textContent = actual !== undefined ? `실제 ${(actual / 60).toFixed(1)}분`
        : ta.value.trim() ? `약 ${speakMinutes(ta.value).toFixed(1)}분` : "";
      syncScriptEstimate();
    };
    ta.addEventListener("input", sync);
    setTimeout(sync);
    return el("div", { class: "script-row" },
      el("img", { src: `/api/documents/${state.doc.id}/pages/${pg.index}.png`, alt: `페이지 ${pg.index}`, loading: "lazy" }),
      el("div", {}, el("div", { class: "label" }, el("span", {}, `페이지 ${pg.index}`), est), ta));
  }));
  $("#script-dialog").showModal();
}

function syncScriptEstimate() {
  const texts = [...document.querySelectorAll("#script-pages textarea")].map((t) => t.value);
  const actual = state.doc?.script?.actual_seconds;
  const total = actual && Object.keys(actual).length
    ? Object.values(actual).reduce((a, v) => a + v, 0) / 60
    : texts.reduce((a, t) => a + speakMinutes(t), 0);
  const target = Number($("#script-form").elements.target_minutes.value) || null;
  const over = target && total > target * 1.1;
  const out = $("#script-estimate");
  out.textContent = total ? `${actual && Object.keys(actual).length ? "실제" : "예상"} 발표 시간 ${total.toFixed(1)}분${target ? ` / 목표 ${target}분` : ""}${over ? " · 목표 초과" : ""}` : "";
  out.style.color = over ? "var(--critical)" : "";
}

async function saveScript(e) {
  e.preventDefault();
  const pages = Object.fromEntries([...document.querySelectorAll("#script-pages textarea")]
    .map((t) => [t.dataset.page, t.value]));
  const target = Number($("#script-form").elements.target_minutes.value) || null;
  try {
    const r = await api(`/api/documents/${state.doc.id}/script`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ target_minutes: target, pages }),
    });
    state.doc.script = r.script;
    $("#script-dialog").close();
    updateScriptButton();
    toast("대본을 저장했습니다. '분석 시작'을 누르면 대본도 함께 평가합니다.", 5000);
  } catch (err) {
    toast(`저장 실패: ${err.message}`);
  }
}

function updateScriptButton() {
  const n = Object.keys(state.doc?.script?.pages || {}).length;
  $("#script-btn").textContent = n ? `🎤 발표 대본 (${n}p)` : "🎤 발표 대본";
}

// ------------------------------------------------------------------ 결과 표시

async function openAudience(audienceId) {
  state.activeAudience = audienceId;
  state.activePersona = null;
  state.result = await api(`/api/documents/${state.doc.id}/audiences/${audienceId}`);
  render();
}

async function openResult(personaId) {
  state.activeAudience = null;
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
  renderVideo();
  renderFilters();
  renderPages();
  renderMemos();
  renderExports();
}

function renderTabs() {
  fill($("#tabs"), ...state.docAudiences.map((a) =>
    el("button", { class: "tab aud" + (a.audience_id === state.activeAudience ? " active" : ""), onclick: () => openAudience(a.audience_id),
      title: "청중 그룹 종합" }, `${a.icon} ${a.name} 종합`, el("span", { class: "score" }, a.score))),
  ...state.results.map((r) =>
    el("button", { class: "tab" + (r.persona_id === state.activePersona ? " active" : ""), onclick: () => openResult(r.persona_id) },
      `${r.icon || ""} ${r.persona_name}`, el("span", { class: "score" }, r.score))));
}

function renderOverall() {
  const box = $("#overall");
  const r = state.result;
  if (!r) {
    fill(box, el("div", { class: "muted", style: "grid-column:1/-1" },
      "아직 분석 결과가 없습니다. 왼쪽에서 페르소나를 고르고 '분석 시작'을 누르세요."));
    return;
  }
  const o = r.overall;
  const color = scoreColor(o.score);
  fill(box, 
    el("div", {},
      el("div", { class: "score-ring", style: `background: conic-gradient(${color} ${o.score * 3.6}deg, var(--line) 0)` },
        el("div", { class: "hole", style: `color:${color}` },
          el("div", {}, o.score, el("small", {}, "전달력")))),
      el("div", { class: "counts" }, ...["critical", "warning", "suggestion"].map((s) => el("span", { class: `pill ${s}` }, `${SEV[s]} ${o.counts[s]}`)))),
    el("div", {},
      o.first_impression ? [el("h3", {}, `${r.persona.icon || ""} ${r.persona.name}의 첫인상`), el("p", {}, o.first_impression)] : null,
      o.summary ? [el("h3", {}, "종합 평가"), el("p", {}, o.summary)] : null,
      r.audience ? [el("h3", {}, "구성원 (비중 · 점수)"), el("div", { class: "members" }, ...r.audience.members.map((m) =>
        el("div", { class: "member" }, el("span", {}, `${m.icon} ${m.name}`),
          el("span", { class: "share", title: `비중 ${m.share}%` }, el("i", { style: `width:${m.share}%` })),
          el("b", {}, m.score))))] : null,
      o.timing ? el("p", { class: "timing" + (o.timing.over ? " over" : "") },
        `🎤 ${o.timing.measured ? "실제" : "예상"} 발표 시간 ${o.timing.est_minutes}분`,
        o.timing.target_minutes ? ` / 목표 ${o.timing.target_minutes}분` : "", o.timing.over ? " · 목표 초과" : "",
        el("span", { class: "muted" }, o.timing.measured ? " (녹음 기준)" : " (대본 기준)")) : null,
      deliveryLine(o.delivery),
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
  if (!state.result) return fill(box);
  fill(box, ...["critical", "warning", "suggestion"].map((s) =>
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
  fill($("#pages"), ...state.doc.pages.map((pg) => {
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
        ...issues.filter((i) => i.box).map((i) => el("div", {
          class: `box ${i.severity}`, id: `box-${i.n}`, title: `#${i.n} ${i.title}`,
          style: `left:${i.box.x * 100}%;top:${i.box.y * 100}%;width:${i.box.w * 100}%;height:${i.box.h * 100}%`,
          onmouseenter: () => highlight(i.n, false), onmouseleave: () => highlight(null),
          onclick: () => highlight(i.n, "memo"),
        }, el("span", { class: "num", style: `margin-left:${badgeOffset(i)}px` }, i.n)))),
      rp?.page_comment ? el("div", { class: "page-comment" }, rp.page_comment) : null,
      scriptLine(pg.index, issues.filter((i) => !i.box).length),
    );
  }));
}

function scriptLine(index, count) {
  const text = state.doc.script?.pages?.[String(index)];
  if (!text && !count) return null;
  const parts = [];
  if (text) parts.push(`대본 약 ${speakMinutes(text).toFixed(1)}분`);
  if (count) parts.push(`대본 지적 ${count}건 (오른쪽 메모)`);
  return el("div", { class: "page-script" }, "🎤 ", parts.join(" · "));
}

function renderMemos() {
  const box = $("#memos");
  if (!state.result) return fill(box);
  const issues = state.result.pages.flatMap(visibleIssues);
  if (!issues.length) return fill(box, el("div", { class: "memos-empty" }, "표시할 지적사항이 없습니다."));
  fill(box, ...issues.map((i) => el("div", {
    class: `memo ${i.severity}`, id: `memo-${i.n}`,
    onmouseenter: () => highlight(i.n, false), onmouseleave: () => highlight(null),
    onclick: () => highlight(i.n, "box"),
  },
    el("div", { class: "memo-head" }, el("span", { class: "n" }, `#${i.n}`), el("span", { class: "title" }, i.title)),
    el("div", { class: "memo-meta" }, i.target === "script" ? el("span", { class: "tag-script" }, "🎤 대본") : null,
      `${i.target === "script" ? " " : ""}p.${i.page} · ${SEV[i.severity]} · ${i.category}`),
    i.voices ? el("div", { class: "voices" }, `👥 청중의 ${i.share}%가 지적 · `, i.voices.map((v) => `${v.icon} ${v.name}`).join(", ")) : null,
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
  if (scrollTo === "box") {
    if (b) b.scrollIntoView({ behavior: "smooth", block: "center" });
    else {
      const page = state.result?.pages.find((p) => p.issues.some((i) => i.n === n))?.page;
      document.getElementById(`page-${page}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }
}

function renderExports() {
  const box = $("#exports");
  if (!state.result) return fill(box);
  const base = state.activeAudience ? `/api/documents/${state.doc.id}/audiences/${state.activeAudience}`
    : `/api/documents/${state.doc.id}/results/${state.activePersona}`;
  fill(box, 
    el("a", { class: "btn", href: `${base}/export.pdf` }, "⬇ 주석 PDF"),
    el("a", { class: "btn", href: `${base}/export.html`, title: "앱 없이 브라우저로 열어보는 공유용 파일" }, "⬇ 공유용 HTML"),
    el("a", { class: "btn", href: `${base}/export.json` }, "⬇ JSON"),
  );
}

async function renderCompare() {
  const box = $("#compare");
  box.hidden = true;
  if (!state.result || !state.doc.parent_id || state.activeAudience) return;
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
  fill(box, 
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
  $("#autofill-btn").addEventListener("click", autofillPersona);
  $("#audience-form").addEventListener("submit", saveAudience);
  $("#script-btn").addEventListener("click", openScriptDialog);
  $("#video-input").addEventListener("change", (e) => {
    if (e.target.files[0]) uploadVideo(e.target.files[0]);
    e.target.value = "";
  });
  $("#recording-input").addEventListener("change", (e) => {
    if (e.target.files[0]) uploadRecording(e.target.files[0]);
    e.target.value = "";
  });
  $("#speech-form").addEventListener("change", syncSpeechForm);
  $("#speech-form").addEventListener("submit", saveSpeech);
  $("#speech-test-btn").addEventListener("click", testSpeech);
  $("#script-form").addEventListener("submit", saveScript);
  $("#script-form").addEventListener("input", syncScriptEstimate);
  $("#script-cancel").addEventListener("click", () => $("#script-dialog").close());
  $("#audience-cancel").addEventListener("click", () => $("#audience-dialog").close());
  $("#audience-delete").addEventListener("click", deleteAudience);
  $("#autofill-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      autofillPersona();
    }
  });
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
  applyTheme(currentTheme());
  $("#theme-btn").addEventListener("click", cycleTheme);
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
    await loadAudiences();
    resumeJob();
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
    fill(tip, ...html());
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
    fill(box, el("div", { class: "dash-empty" }, `불러오기 실패: ${err.message}`));
    return;
  }
  const t = d.totals;
  const head = el("div", { class: "dash-head" },
    el("h1", {}, "대시보드"),
    el("span", { class: "muted" }, state.status?.mode === "ai" ? `분석 엔진: ${state.status.engine}` : "데모 모드 (AI 미연결)"));
  if (!t.analyses) {
    fill(box, head, el("div", { class: "card dash-empty" },
      el("p", {}, "아직 분석한 자료가 없습니다."),
      el("a", { class: "btn primary", href: "#analyze" }, "자료 분석하러 가기")));
    return;
  }
  fill(box, 
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
          class: `s${step + 1}`,
          onclick: () => goToResult(doc.id, p.id),
        }, score), () => [el("b", {}, doc.filename), el("br"), `${p.icon} ${p.name}: ${score}점`]);
      }))))));
}

async function goToResult(docId, personaId) {
  $("#tooltip").hidden = true;
  location.hash = "#analyze";
  await openDoc(docId, personaId);
}
