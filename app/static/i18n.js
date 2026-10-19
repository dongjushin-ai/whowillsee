"use strict";
/*
 * 화면 다국어 처리.
 *  - 한국어 문구 자체를 키로 쓴다. 한국어 화면에서는 아무것도 바꾸지 않는다.
 *  - t("{n}페이지", {n: 3})  : 변수가 들어가는 문구
 *  - 정적 문구는 화면에 나타날 때 자동으로 번역한다 (MutationObserver).
 *  - <b> 등으로 쪼개진 문장은 data-i18n-html="키" 를 붙여 통째로 번역한다.
 *  - 사전: /static/i18n/en.json  { "한국어": "English", "{n}페이지": "Page {n}", "@html.키": "<b>…</b>" }
 *    {n…} 자리는 숫자, 그 밖의 {이름} 자리는 아무 글자나 받고, 받은 글자도 다시 번역한다.
 */
const I18N = { lang: "ko", dict: {}, patterns: [] };

function detectLang() {
  try {
    const saved = localStorage.getItem("whowillsee.lang");
    if (saved === "ko" || saved === "en") return saved;
  } catch (_) { /* 저장소를 못 쓰면 브라우저 언어를 따른다 */ }
  return (navigator.language || "ko").toLowerCase().startsWith("ko") ? "ko" : "en";
}

async function loadI18n() {
  I18N.lang = detectLang();
  document.documentElement.lang = I18N.lang;
  if (I18N.lang === "ko") return;
  try {
    const res = await fetch(`/static/i18n/${I18N.lang}.json`);
    I18N.dict = await res.json();
  } catch (_) {
    I18N.lang = "ko";
    return;
  }
  // {변수} 가 들어간 키는 정규식 패턴으로 바꿔 둔다 (서버 오류 메시지 등)
  for (const [key, value] of Object.entries(I18N.dict)) {
    if (!key.includes("{") || key.startsWith("@")) continue;
    const names = [];
    const source = key.split(/(\{\w+\})/).map((part) => {
      const m = part.match(/^\{(\w+)\}$/);
      if (m) {
        names.push(m[1]);
        // n 으로 시작하는 자리는 숫자만 받는다 (예: {n}분 이 '부분' 같은 말과 맞지 않도록)
        return m[1].startsWith("n") ? "(-?[\\d.,]+)" : "(.+?)";
      }
      return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }).join("");
    I18N.patterns.push({ re: new RegExp(`^${source}$`, "s"), names, value, weight: key.replace(/\{\w+\}/g, "").length });
  }
  I18N.patterns.sort((a, b) => b.weight - a.weight); // 글자가 많이 고정된(구체적인) 패턴부터
}

function fillVars(text, vars) {
  return vars ? text.replace(/\{(\w+)\}/g, (all, k) => (k in vars ? String(vars[k]) : all)) : text;
}

function t(key, vars) {
  const text = (I18N.lang !== "ko" && I18N.dict[key]) || key;
  return fillVars(text, vars);
}

/** 자유 텍스트 번역: 정확히 일치 → 패턴 → " · " 로 나눈 조각별. 없으면 그대로. */
function tr(text) {
  if (I18N.lang === "ko" || !text) return text;
  const norm = text.replace(/\s+/g, " ").trim();
  if (!norm || !/[가-힣]/.test(norm)) return text;
  const lead = text.match(/^\s*/)[0];
  const trail = text.match(/\s*$/)[0];
  const done = (s) => lead + s + trail;
  if (I18N.dict[norm]) return done(I18N.dict[norm]);
  for (const p of I18N.patterns) {
    const m = norm.match(p.re);
    if (m) {
      const vars = Object.fromEntries(p.names.map((n, i) => [n, tr(m[i + 1])]));
      return done(fillVars(p.value, vars));
    }
  }
  if (norm.includes(" · ")) {
    const parts = norm.split(" · ").map((x) => tr(x));
    if (parts.join(" · ") !== norm) return done(parts.join(" · "));
  }
  return text;
}

const I18N_ATTRS = ["placeholder", "title", "alt", "aria-label"];

function translateNode(root) {
  if (I18N.lang === "ko") return;
  if (root.nodeType === Node.TEXT_NODE) {
    const next = tr(root.data);
    if (next !== root.data) root.data = next;
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE) return;
  const els = [root, ...root.querySelectorAll("*")];
  for (const el of els) {
    if (el.closest("[data-no-i18n]")) continue;
    if (el.dataset.i18nHtml && !el.dataset.i18nDone) {
      const html = I18N.dict[`@html.${el.dataset.i18nHtml}`];
      if (html) el.innerHTML = html;
      el.dataset.i18nDone = "1";
    }
    for (const a of I18N_ATTRS) {
      const v = el.getAttribute(a);
      if (v) {
        const next = tr(v);
        if (next !== v) el.setAttribute(a, next);
      }
    }
  }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (n.parentElement?.closest("[data-no-i18n], script, style, textarea")) continue;
    const next = tr(n.data);
    if (next !== n.data) n.data = next;
  }
}

function startI18nObserver() {
  if (I18N.lang === "ko") return;
  translateNode(document.body);
  document.title = tr(document.title);
  new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === "characterData") translateNode(r.target);
      else if (r.type === "attributes") translateNode(r.target);
      else r.addedNodes.forEach((n) => translateNode(n));
    }
  }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: I18N_ATTRS });
}

function setLang(lang) {
  try { localStorage.setItem("whowillsee.lang", lang); } catch (_) { /* 저장 못 해도 이번 화면에는 적용 */ }
  location.reload();
}
