const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const store = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem("spectra." + key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem("spectra." + key, JSON.stringify(value));
    } catch {}
  },
};

const ICONS = {
  play: '<path d="M7 4.5v15l12-7.5z" fill="currentColor" stroke="none"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/>',
  up: '<path d="M6 15l6-6 6 6"/>',
  down: '<path d="M6 9l6 6 6-6"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>',
  download: '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>',
  monitor: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  terminal: '<path d="M5 7l5 5-5 5M12 17h7"/>',
  dice: '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="9" cy="9" r="1.2" fill="currentColor"/><circle cx="15" cy="15" r="1.2" fill="currentColor"/><circle cx="15" cy="9" r="1.2" fill="currentColor"/><circle cx="9" cy="15" r="1.2" fill="currentColor"/>',
  restore: '<path d="M4 12a8 8 0 1 0 2.4-5.7M4 4v5h5"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
};
const icon = (name) => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmtBytes = (n) => {
  if (!n && n !== 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
};
const fmtSec = (s) => (s || s === 0 ? `${Number(s).toFixed(1)} s` : "");
const fmtDate = (t) => new Date(t * 1000).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: {} };
  if (options.body instanceof FormData) init.body = options.body;
  else if (options.body !== undefined) {
    init.body = JSON.stringify(options.body);
    init.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, init);
  const type = res.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = data && data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `${res.status} ${res.statusText}`;
    throw new Error(detail);
  }
  return data;
}

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3800);
}

function modal({ title, body = "", actions = [] }) {
  return new Promise((resolve) => {
    const root = $("#modal");
    $("#modal-title").textContent = title;
    const bodyEl = $("#modal-body");
    bodyEl.innerHTML = body;
    const actionsEl = $("#modal-actions");
    actionsEl.innerHTML = "";
    const close = (value) => {
      root.hidden = true;
      document.removeEventListener("keydown", onKey);
      resolve(value);
    };
    const onKey = (e) => {
      if (e.key === "Escape") close(null);
      if (e.key === "Enter" && e.target.tagName !== "TEXTAREA") {
        const primary = actions.find((a) => a.primary);
        if (primary) { e.preventDefault(); close(primary.value(bodyEl)); }
      }
    };
    for (const action of actions) {
      const btn = document.createElement("button");
      btn.className = `btn ${action.primary ? "primary" : "ghost"} ${action.danger ? "danger" : ""}`;
      btn.textContent = action.label;
      btn.onclick = () => close(action.value ? action.value(bodyEl) : action.label);
      actionsEl.appendChild(btn);
    }
    root.hidden = false;
    root.onclick = (e) => { if (e.target === root) close(null); };
    document.addEventListener("keydown", onKey);
    setTimeout(() => (bodyEl.querySelector("input,textarea") || actionsEl.querySelector(".primary"))?.focus(), 30);
  });
}

const confirmBox = (title, text, label = "Delete") =>
  modal({ title, body: `<p class="muted">${esc(text)}</p>`, actions: [{ label: "Cancel", value: () => false }, { label, primary: true, danger: true, value: () => true }] });

const S = {
  engines: {},
  engineList: [],
  models: [],
  voices: [],
  modelId: store.get("model", ""),
  clips: store.get("clips", []),
  voiceId: store.get("voice", ""),
  settings: store.get("settings", {}),
  advanced: store.get("advanced", false),
  collapsed: store.get("collapsed", {}),
  worker: null,
  gpu: null,
  job: null,
  results: [],
  activeJobs: new Map(),
  logCursor: 0,
  history: { offset: 0, total: 0, query: "" },
  pinnedHelp: null,
  helpAnchor: null,
  asr: null,
  reading: null,
  readingDict: [],
  readingResult: null,
  readingStale: false,
  readingOverrides: store.get("readingOverrides", {}),
  preparing: false,
};

const GROUP_ORDER = ["Model", "Basic", "Sampling", "Guidance", "Duration", "Reference", "Advanced"];
const TRANSCRIPT_LABEL = { required: "Transcript (required)", recommended: "Transcript (recommended)", optional: "Transcript (optional)" };
const TEXT_HINTS = {
  irodori: "Tip: emoji such as 😊 😭 🤭 😮‍💨 in the text can steer emotion and add non-verbal sounds on v4 models. The Emoji button lists every supported cue. Keep each generation to one or two sentences for the most natural result. English words and acronyms are converted to katakana before generation. Click a word in the Reading box to change how it is read.",
  qwen3tts: "Tip: set Language to match the text. The reference may be in a different language (cross-lingual cloning).",
  voxcpm: "Tip: in 'Reference only' mode, the Style instruction setting controls emotion and speed while keeping the cloned timbre.",
};

const HELP = {
  model: { kicker: "Studio", title: "Model", summary: "The checkpoint that will speak. Models are grouped by engine. Generating loads the model automatically. Switching to another engine restarts its worker process, which frees all VRAM held by the previous one.", tip: "Download more checkpoints from the Models tab by pasting a Hugging Face link." },
  load: { kicker: "Studio", title: "Load", summary: "Loads the selected model now with the current Model settings, so the first generation starts without waiting.", tip: "Loading is skipped if the same model with the same Model settings is already loaded." },
  unload: { kicker: "Studio", title: "Unload", summary: "Stops the engine worker process. All GPU memory it used is returned to the system.", tip: "Useful before running another GPU-heavy application." },
  voiceLibrary: { kicker: "Reference", title: "Voice library", summary: "Loads a saved voice: its clips and transcripts replace the current reference. Changes you make afterwards stay local until you save the voice again.", tip: "Manage saved voices in the Voices tab." },
  saveVoice: { kicker: "Reference", title: "Save as voice", summary: "Stores the current clips and transcripts in the voice library under a name, so you can reuse them later with one click.", tip: "If a saved voice is selected and you changed it, you can update it or save a new copy." },
  clearClips: { kicker: "Reference", title: "Clear", summary: "Removes all clips from the current reference. The converted audio stays cached on disk, so saved voices and history are not affected." },
  reference: {
    kicker: "Reference", title: "Reference audio",
    summary: "The voice to clone. Drop or browse audio or video files. They are converted to 48 kHz mono WAV with ffmpeg. Use clean speech from a single speaker without music, sound effects, reverb or other voices.",
    up: ["More", "More clean speech gives a more accurate identity, with diminishing returns after about 30 s."],
    down: ["Less", "Very short clips (under 3 s) give an unstable identity and a generic voice."],
    tip: "Several short clips of the same speaker are better than one long recording with gaps. Remove clips that contain laughter, noise or other speakers.",
  },
  record: { kicker: "Reference", title: "Record", summary: "Records a clip from your microphone. The browser asks for permission the first time. Press again to stop and add the recording.", tip: "Speak naturally for 10 to 20 s in a quiet room, about a hand's width from the microphone." },
  transcript: {
    kicker: "Reference", title: "Transcript",
    summary: "The exact words spoken in this clip. Engines that read it (Qwen3-TTS ICL mode, VoxCPM continuation) align the audio with the text, which strongly improves similarity and prosody.",
    up: ["Exact", "Every word, including fillers like 'um', in the right order."],
    down: ["Wrong", "A transcript that does not match the audio is worse than none and can cause mumbling or repeated words."],
    tip: "Irodori-TTS does not use transcripts. You can leave them empty for it.",
  },
  text: { kicker: "Studio", title: "Text", summary: "What the cloned voice will say. Punctuation shapes pauses and intonation: use 、 and 。 (or commas and periods) naturally.", tip: "Long passages are more stable when split into a few sentences per generation." },
  generate: { kicker: "Studio", title: "Generate", summary: "Synthesizes the text with the selected model and reference voice. Every result is saved to History with its settings. Shortcut: Ctrl+Enter.", tip: "Cancel during generation stops the worker process. The model reloads on the next generation." },
  advanced: { kicker: "Settings", title: "Advanced", summary: "Shows expert settings that rarely need changing. Hidden settings keep their current values." },
  reset: { kicker: "Settings", title: "Reset", summary: "Restores every setting of the current engine to its default value." },
  hfLink: { kicker: "Models", title: "Hugging Face link", summary: "Paste a model URL such as https://huggingface.co/owner/name, a link to a subfolder (…/tree/main/subfolder), or just owner/name. Check lists exactly what will be downloaded before anything is saved.", tip: "Files go to %USERPROFILE%\\.spectra\\models\\<engine>\\<owner>--<name>. Models that are already downloaded cannot be downloaded again. Interrupted downloads can be resumed." },
  hfEngine: { kicker: "Models", title: "Engine", summary: "Which engine the checkpoint belongs to. Auto detects it from the repository name and its config.json. Override it only for fine-tunes with unusual names." },
  worker: { kicker: "System", title: "Worker", summary: "The engine process that holds the current model in GPU memory. Only one engine runs at a time so that a 12 GB GPU is never shared between two models." },
  gpu: { kicker: "System", title: "GPU memory", summary: "Total GPU memory in use according to nvidia-smi, including the desktop and other applications.", tip: "When VRAM is full, Windows spills into system RAM and generation becomes many times slower." },
};

function currentModel() {
  return S.models.find((m) => m.id === S.modelId && m.status === "ready") || null;
}
function currentEngine() {
  const model = currentModel();
  return model ? S.engines[model.engine] : null;
}
function engineSettings(engineId) {
  const engine = S.engines[engineId];
  const stored = S.settings[engineId] || {};
  const values = {};
  for (const spec of engine.settings) {
    let value = spec.key in stored ? stored[spec.key] : spec.default;
    if (spec.type === "select" && !spec.options.some((o) => String(o.value) === String(value))) value = spec.default;
    values[spec.key] = value;
  }
  return values;
}
function setSetting(engineId, key, value) {
  S.settings[engineId] = { ...(S.settings[engineId] || {}), [key]: value };
  store.set("settings", S.settings);
}
function saveClips() {
  store.set("clips", S.clips);
  store.set("voice", S.voiceId);
}

function helpHtml(help) {
  const effects = [];
  if (help.up) effects.push(`<div class="effect up"><b>${esc(help.up[0])}</b><span>${esc(help.up[1])}</span></div>`);
  if (help.down) effects.push(`<div class="effect down"><b>${esc(help.down[0])}</b><span>${esc(help.down[1])}</span></div>`);
  return `
    <div class="insp-kicker">${esc(help.kicker || "Help")}${help.scope ? `<span class="badge ${help.scope === "load" ? "warn" : ""}">${help.scope === "load" ? "Reloads model" : "Per generation"}</span>` : ""}</div>
    <h3>${esc(help.title)}</h3>
    <p>${esc(help.summary || "")}</p>
    ${effects.length ? `<div class="effects">${effects.join("")}</div>` : ""}
    ${help.options ? `<ul class="opts">${help.options.map((o) => `<li class="${o.on ? "on" : ""}"><strong>${esc(o.label)}</strong>${esc(o.help || "")}</li>`).join("")}</ul>` : ""}
    ${help.notes && help.notes.length ? `<div class="insp-sub">Trade-offs</div><ul class="notes">${help.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    ${help.tip ? `<div class="tip">${esc(help.tip)}</div>` : ""}
    ${help.facts && help.facts.length ? `<div class="facts">${help.facts.map((f) => `<span class="chip">${esc(f)}</span>`).join("")}</div>` : ""}`;
}

function showHelp(help, anchor) {
  const pop = $("#help-pop");
  if (!help || !anchor) {
    hideHelp();
    return;
  }
  const fresh = pop.hidden || S.helpAnchor !== anchor;
  S.helpAnchor = anchor;
  $("#help-pop-body").innerHTML = helpHtml(help);
  if (fresh) {
    $("#help-pop-body").scrollTop = 0;
    pop.classList.remove("show");
    pop.hidden = false;
    void pop.offsetWidth;
    pop.classList.add("show");
    trackHelp();
  }
  placeHelp();
}

let helpFrame = 0;
function trackHelp() {
  cancelAnimationFrame(helpFrame);
  let last = "";
  const step = () => {
    const anchor = S.helpAnchor;
    if (!anchor || $("#help-pop").hidden) return;
    const r = anchor.getBoundingClientRect();
    const key = `${r.left}|${r.top}|${r.width}|${r.height}|${innerWidth}|${innerHeight}|${anchor.isConnected}`;
    if (key !== last) {
      last = key;
      placeHelp();
    }
    helpFrame = requestAnimationFrame(step);
  };
  helpFrame = requestAnimationFrame(step);
}

function hideHelp() {
  $("#help-pop").hidden = true;
  S.helpAnchor = null;
  S.pinnedHelp = null;
  $$(".setting.focused").forEach((n) => n.classList.remove("focused"));
}

function placeHelp() {
  const pop = $("#help-pop");
  const anchor = S.helpAnchor;
  if (pop.hidden || !anchor) return;
  if (!anchor.isConnected) {
    hideHelp();
    return;
  }
  const r = anchor.getBoundingClientRect();
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  pop.style.visibility = r.bottom < 0 || r.top > vh ? "hidden" : "";
  const w = pop.offsetWidth;
  const h = pop.offsetHeight;
  const gap = 10;
  const margin = 8;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const fits = {
    left: r.left - gap - w >= margin,
    right: r.right + gap + w <= vw - margin,
    below: r.bottom + gap + h <= vh - margin,
    above: r.top - gap - h >= margin,
  };
  const order = anchor.closest(".studio-side") ? ["left", "below", "above", "right"] : ["below", "above", "right", "left"];
  let side = order.find((s) => fits[s]);
  let x;
  let y;
  if (side === "left" || side === "right") {
    x = side === "left" ? r.left - gap - w : r.right + gap;
    y = clamp(r.top, margin, vh - h - margin);
    pop.style.setProperty("--arrow", `${clamp(r.top + Math.min(r.height / 2, 18) - y, 14, h - 14)}px`);
  } else {
    if (!side) side = "none";
    x = clamp(r.left, margin, vw - w - margin);
    y = side === "below" ? r.bottom + gap : side === "above" ? r.top - gap - h : clamp(r.bottom + gap, margin, vh - h - margin);
    pop.style.setProperty("--arrow", `${clamp(r.left + Math.min(r.width / 2, 24) - x, 16, w - 16)}px`);
  }
  pop.dataset.side = side;
  pop.style.left = `${Math.round(x)}px`;
  pop.style.top = `${Math.round(y)}px`;
}

function formatValue(spec, value) {
  if (value === undefined || value === null || value === "") return "empty";
  if (spec.type === "bool") return value ? "On" : "Off";
  if (spec.type === "select") return (spec.options.find((o) => String(o.value) === String(value)) || {}).label || String(value);
  if (spec.type === "seed") return Number(value) < 0 ? "Random (-1)" : String(value);
  if ((spec.type === "int" || spec.type === "float") && Number(value) === 0 && spec.zero_label) return `${spec.zero_label} (0)`;
  return `${value}${spec.unit ? " " + spec.unit : ""}`;
}

function settingHelp(spec, value) {
  const h = spec.help || {};
  const help = { kicker: spec.group || "Setting", title: spec.label, summary: h.summary, tip: h.tip, scope: spec.scope || "generate" };
  if (spec.type === "select") {
    help.options = (spec.options || []).map((o) => ({ label: o.label, help: o.help ? ` ${o.help}` : "", on: String(o.value) === String(value) }));
    help.notes = [h.increase, h.decrease].filter(Boolean);
  } else {
    const labels = { bool: ["On", "Off"], text: ["Filled", "Empty"], textarea: ["Filled", "Empty"], seed: ["Fixed", "Random"] }[spec.type] || ["Higher", "Lower"];
    if (h.increase) help.up = [labels[0], h.increase];
    if (h.decrease) help.down = [labels[1], h.decrease];
  }
  const facts = [`Default: ${formatValue(spec, spec.default)}`, `Current: ${formatValue(spec, value)}`];
  if ((spec.type === "int" || spec.type === "float") && spec.min !== undefined) facts.push(`Range: ${spec.min} to ${spec.max}${spec.unit ? " " + spec.unit : ""}`);
  help.facts = facts;
  return help;
}

function bindHelp(el, getHelp) {
  el.dataset.helpBound = "";
  const owns = (target) => target.closest("[data-help-bound]") === el;
  el.addEventListener("focusin", (e) => {
    if (!owns(e.target) || S.helpAnchor === el) return;
    $$(".setting.focused").forEach((n) => n.classList.remove("focused"));
    if (el.classList.contains("setting")) el.classList.add("focused");
    S.pinnedHelp = getHelp;
    showHelp(getHelp(), el);
  });
  el.addEventListener("focusout", (e) => {
    if (S.helpAnchor !== el || (e.relatedTarget && owns(e.relatedTarget))) return;
    hideHelp();
  });
}

function refreshHelp() {
  if (S.helpAnchor && S.pinnedHelp) showHelp(S.pinnedHelp(), S.helpAnchor);
}

function decimals(step) {
  const s = String(step ?? 1);
  return s.includes(".") ? s.split(".")[1].length : 0;
}

function renderSettings() {
  const root = $("#settings");
  const engine = currentEngine();
  root.innerHTML = "";
  if (!engine) {
    root.innerHTML = `<p class="muted">Select a model to see its settings.</p>`;
    return;
  }
  const values = engineSettings(engine.id);
  const groups = new Map();
  for (const spec of engine.settings) {
    if (spec.advanced && !S.advanced) continue;
    const group = spec.group || "Advanced";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(spec);
  }
  const rank = (group) => {
    const index = GROUP_ORDER.indexOf(group);
    return index < 0 ? GROUP_ORDER.length : index;
  };
  const ordered = [...groups.keys()].sort((a, b) => rank(a) - rank(b));
  for (const group of ordered) {
    const details = document.createElement("details");
    details.className = "settings-group";
    const collapseKey = `${engine.id}:${group}`;
    details.open = !S.collapsed[collapseKey];
    details.innerHTML = `<summary>${esc(group)}</summary>`;
    details.addEventListener("toggle", () => {
      S.collapsed[collapseKey] = !details.open;
      store.set("collapsed", S.collapsed);
    });
    for (const spec of groups.get(group)) details.appendChild(renderControl(engine, spec, values[spec.key]));
    root.appendChild(details);
  }
}

function renderControl(engine, spec, value) {
  const wrap = document.createElement("div");
  wrap.className = `setting ${spec.type}`;
  const id = `set-${engine.id}-${spec.key}`;
  const reload = spec.scope === "load" ? `<span class="reload">reload</span>` : "";
  let current = value;
  const markChanged = () => wrap.classList.toggle("changed", JSON.stringify(current) !== JSON.stringify(spec.default));
  const commit = (v) => {
    current = v;
    setSetting(engine.id, spec.key, v);
    markChanged();
    if (S.helpAnchor === wrap) refreshHelp();
    if (spec.scope === "load") renderModelMeta();
    if (spec.key === "reading_conversion" || spec.key === "reading_context") scheduleReading(0);
  };
  if (spec.type === "bool") {
    wrap.innerHTML = `<div class="setting-label"><label for="${id}">${esc(spec.label)} ${reload}</label><label class="switch"><input type="checkbox" id="${id}"><span></span></label></div>`;
    const input = wrap.querySelector("input");
    input.checked = !!value;
    input.addEventListener("change", () => commit(input.checked));
  } else if (spec.type === "select") {
    wrap.innerHTML = `<label class="setting-label" for="${id}"><span>${esc(spec.label)} ${reload}</span></label><select id="${id}">${spec.options.map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("")}</select>`;
    const input = wrap.querySelector("select");
    input.value = String(value);
    input.addEventListener("change", () => commit(input.value));
  } else if (spec.type === "text" || spec.type === "textarea") {
    const tag = spec.type === "textarea" ? `<textarea id="${id}" rows="2" placeholder="${esc(spec.placeholder || "")}"></textarea>` : `<input type="text" id="${id}" placeholder="${esc(spec.placeholder || "")}">`;
    wrap.innerHTML = `<label class="setting-label" for="${id}"><span>${esc(spec.label)} ${reload}</span></label>${tag}`;
    const input = wrap.querySelector("input,textarea");
    input.value = value ?? "";
    input.addEventListener("input", () => commit(input.value));
  } else if (spec.type === "seed") {
    wrap.innerHTML = `<label class="setting-label" for="${id}"><span>${esc(spec.label)}</span><span class="zero"></span></label>
      <div class="setting-row seed-row"><input type="number" id="${id}" step="1" min="-1"><button class="icon-btn" type="button" title="Random seed now" aria-label="Pick a random seed">${icon("dice")}</button><button class="btn ghost small" type="button">Random</button></div>`;
    const input = wrap.querySelector("input");
    const zero = wrap.querySelector(".zero");
    const [dice, randomBtn] = wrap.querySelectorAll("button");
    const sync = () => { zero.textContent = Number(input.value) < 0 ? "new seed every run" : "fixed"; };
    input.value = value;
    sync();
    input.addEventListener("input", () => { const v = parseInt(input.value, 10); commit(Number.isFinite(v) ? v : -1); sync(); });
    dice.addEventListener("click", () => { input.value = Math.floor(Math.random() * 2147483647); input.dispatchEvent(new Event("input")); });
    randomBtn.addEventListener("click", () => { input.value = -1; input.dispatchEvent(new Event("input")); });
  } else {
    const ranged = spec.min !== undefined && spec.max !== undefined;
    const digits = decimals(spec.step);
    wrap.innerHTML = `<label class="setting-label" for="${id}"><span>${esc(spec.label)} ${reload}</span><span class="zero"></span></label>
      <div class="setting-row">${ranged ? `<input type="range" min="${spec.min}" max="${spec.max}" step="${spec.step || 1}" aria-label="${esc(spec.label)}">` : ""}<input type="number" id="${id}" min="${spec.min ?? ""}" max="${spec.max ?? ""}" step="${spec.step || 1}">${spec.unit ? `<span class="unit">${esc(spec.unit)}</span>` : ""}</div>`;
    const range = wrap.querySelector('input[type="range"]');
    const input = wrap.querySelector('input[type="number"]');
    const zero = wrap.querySelector(".zero");
    const paint = (v) => {
      if (range) {
        range.value = v;
        const pct = ((v - spec.min) / (spec.max - spec.min)) * 100;
        range.style.setProperty("--fill", `${Math.max(0, Math.min(100, pct))}%`);
      }
      zero.textContent = Number(v) === 0 && spec.zero_label ? spec.zero_label : "";
    };
    const parse = (raw) => {
      let v = spec.type === "int" ? parseInt(raw, 10) : parseFloat(raw);
      if (!Number.isFinite(v)) v = spec.default;
      return v;
    };
    input.value = Number(value).toFixed(digits);
    paint(value);
    if (range) range.addEventListener("input", () => { const v = parse(range.value); input.value = v.toFixed(digits); paint(v); commit(v); });
    input.addEventListener("input", () => { const v = parse(input.value); paint(v); commit(v); });
    input.addEventListener("change", () => {
      let v = parse(input.value);
      if (spec.min !== undefined) v = Math.max(spec.min, v);
      if (spec.max !== undefined) v = Math.min(spec.max, v);
      input.value = v.toFixed(digits);
      paint(v);
      commit(v);
    });
  }
  markChanged();
  bindHelp(wrap, () => settingHelp(spec, current));
  return wrap;
}

function renderModelSelect() {
  const select = $("#model-select");
  const ready = S.models.filter((m) => m.status === "ready");
  if (!ready.length) {
    select.innerHTML = `<option value="">No models yet. Download one in the Models tab.</option>`;
    select.disabled = true;
  } else {
    select.disabled = false;
    const byEngine = {};
    for (const m of ready) (byEngine[m.engine] ||= []).push(m);
    select.innerHTML = Object.entries(byEngine).map(([engineId, items]) => {
      const engine = S.engines[engineId];
      const flag = engine && !engine.installed ? " (environment not installed)" : "";
      return `<optgroup label="${esc((engine ? engine.name : engineId) + flag)}">${items.map((m) => `<option value="${esc(m.id)}">${esc(m.label)} · ${fmtBytes(m.size)}</option>`).join("")}</optgroup>`;
    }).join("");
    if (!ready.some((m) => m.id === S.modelId)) S.modelId = ready[0].id;
    select.value = S.modelId;
  }
  store.set("model", S.modelId);
}

function loadedMatches() {
  const model = currentModel();
  const loaded = S.worker && S.worker.loaded;
  if (!model || !loaded || loaded.model_id !== model.id) return null;
  const engine = S.engines[model.engine];
  const values = engineSettings(engine.id);
  const wanted = {};
  for (const spec of engine.settings) if (spec.scope === "load") wanted[spec.key] = values[spec.key];
  const same = Object.keys(wanted).every((k) => String(wanted[k]) === String((loaded.load_settings || {})[k]));
  return { loaded, same };
}

function renderModelMeta() {
  const el = $("#model-meta");
  const model = currentModel();
  const engine = currentEngine();
  if (!model || !engine) {
    el.innerHTML = "";
    $("#load-btn").disabled = true;
    return;
  }
  const match = loadedMatches();
  const chips = [`<span class="badge">${esc(engine.name)}</span>`];
  if (!engine.installed) chips.push(`<span class="badge danger">environment not installed</span>`);
  if (match) {
    const l = match.loaded;
    const precision = l.precision || l.dtype || l.quantization || "";
    chips.push(`<span class="badge ok">loaded${precision ? " · " + esc(precision) : ""}</span>`);
    if (l.load_seconds) chips.push(`<span class="chip">load ${fmtSec(l.load_seconds)}</span>`);
    if (!match.same) chips.push(`<span class="badge warn">model settings changed · reloads on next run</span>`);
  } else {
    chips.push(`<span class="chip">not loaded</span>`);
  }
  if (model.repo_id) chips.push(`<a class="chip" href="https://huggingface.co/${esc(model.repo_id)}" target="_blank" rel="noreferrer">${icon("link")}${esc(model.repo_id)}${model.subfolder ? "/" + esc(model.subfolder) : ""}</a>`);
  el.innerHTML = `<div class="tagline">${esc(engine.tagline || "")}</div>${chips.join("")}`;
  $("#load-btn").disabled = !engine.installed || (match && match.same) || !!S.job;
  $("#unload-btn").disabled = !(S.worker && S.worker.engine) || !!S.job;
}

let player = null;
let playingId = null;
function playClip(id, button) {
  if (player && playingId === id) {
    player.pause();
    return;
  }
  if (player) player.pause();
  player = new Audio(`/api/clips/${id}/audio`);
  playingId = id;
  $$(".play-btn.playing").forEach((b) => { b.classList.remove("playing"); b.innerHTML = icon("play"); });
  button.classList.add("playing");
  button.innerHTML = icon("pause");
  const done = () => {
    button.classList.remove("playing");
    button.innerHTML = icon("play");
    if (playingId === id) { playingId = null; player = null; }
  };
  player.addEventListener("ended", done);
  player.addEventListener("pause", done);
  player.play().catch(done);
}

function waveSvg(peaks) {
  const bars = (peaks || []).map((p, i) => {
    const h = Math.max(1.5, p * 24);
    return `<rect x="${i * 3}" y="${(26 - h) / 2}" width="2" height="${h}" rx="1"/>`;
  }).join("");
  return `<svg class="clip-wave" viewBox="0 0 ${Math.max(1, (peaks || []).length * 3)} 26" preserveAspectRatio="none" aria-hidden="true">${bars}</svg>`;
}

function clipFlags(clip) {
  const flags = [];
  if (clip.missing) flags.push(`<span class="badge danger">missing</span>`);
  if (clip.clipping) flags.push(`<span class="badge warn" title="Peaks reach 0 dBFS. Distortion will be cloned too.">clipping</span>`);
  if (clip.rms_db !== undefined && clip.rms_db < -38) flags.push(`<span class="badge warn" title="Very quiet recording">quiet</span>`);
  if (clip.duration !== undefined && clip.duration < 2) flags.push(`<span class="badge warn">short</span>`);
  return flags.join("");
}

function renderClipItem(clip, index, count, { transcriptMode, onChange, onRemove, onMove }) {
  const li = document.createElement("li");
  li.className = "clip";
  li.innerHTML = `
    <button class="play-btn play" type="button" aria-label="Play clip">${icon("play")}</button>
    <div class="clip-top"><span class="clip-name" title="${esc(clip.name)}">${esc(clip.name)}</span>${waveSvg(clip.peaks)}<span class="clip-dur">${fmtSec(clip.duration)}</span><span class="clip-flags">${clipFlags(clip)}</span></div>
    <div class="clip-tools">
      ${onMove ? `<button class="icon-btn plain" data-act="up" title="Move up" ${index === 0 ? "disabled" : ""}>${icon("up")}</button><button class="icon-btn plain" data-act="down" title="Move down" ${index === count - 1 ? "disabled" : ""}>${icon("down")}</button>` : ""}
      <button class="icon-btn plain danger" data-act="remove" title="Remove clip">${icon("x")}</button>
    </div>`;
  li.querySelector(".play").addEventListener("click", (e) => playClip(clip.id, e.currentTarget));
  li.querySelector('[data-act="remove"]').addEventListener("click", onRemove);
  if (onMove) {
    li.querySelector('[data-act="up"]').addEventListener("click", () => onMove(-1));
    li.querySelector('[data-act="down"]').addEventListener("click", () => onMove(1));
    bindHelp(li.querySelector(".clip-tools"), () => ({ kicker: "Reference", title: "Clip order", summary: "Clips are joined in this order. Engines with a reference length cap use the first clips first, and single-reference engines receive one joined clip.", tip: "Put the cleanest, most representative clip first." }));
  }
  if (transcriptMode && transcriptMode !== "unused") {
    const input = document.createElement("input");
    input.type = "text";
    input.className = "transcript";
    input.placeholder = TRANSCRIPT_LABEL[transcriptMode] || "Transcript";
    input.value = clip.transcript || "";
    input.addEventListener("input", () => {
      clip.transcript = input.value;
      input.classList.toggle("missing", transcriptMode === "required" && !input.value.trim());
      onChange();
    });
    input.classList.toggle("missing", transcriptMode === "required" && !input.value.trim());
    bindHelp(input, () => HELP.transcript);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn ghost small asr-btn";
    button.innerHTML = `${icon("sparkle")} Generate`;
    button.setAttribute("aria-disabled", String(!(S.asr && S.asr.ready)));
    button.addEventListener("click", () => autoTranscribe(clip, input, button, onChange));
    bindHelp(button, asrHelp);
    const row = document.createElement("div");
    row.className = "transcript-row";
    row.append(button, input);
    li.appendChild(row);
  }
  return li;
}

function asrHelp() {
  const a = S.asr;
  const ready = !!(a && a.ready);
  return {
    kicker: "Reference",
    title: "Generate transcript",
    summary: `Transcribes this clip with ${a ? a.label : "the speech recognition model"} and writes the spoken words into the transcript box. The language is detected automatically.`,
    tip: ready ? "Check proper nouns and fillers afterwards. Ctrl+Z in the box restores the previous text." : "Set up Transcription in the Models tab first.",
    facts: ready ? [a.loaded ? "Model loaded" : "Loads on first use (about 10 to 30 s)", "Stops after 3 min idle to free VRAM"] : [],
  };
}

async function autoTranscribe(clip, input, button, onChange) {
  if (button.classList.contains("busy")) return;
  if (!S.asr || !S.asr.ready) {
    toast("Set up Transcription in the Models tab first.", "error");
    return;
  }
  button.classList.add("busy");
  button.setAttribute("aria-disabled", "true");
  button.innerHTML = `<span class="spinner" aria-hidden="true"></span> Generating`;
  try {
    const res = await api(`/api/clips/${clip.id}/transcribe`, { method: "POST", body: {} });
    const text = (res.text || "").trim();
    if (!text) {
      toast(`No speech was recognized in ${clip.name}.`, "error");
    } else if (input.isConnected) {
      input.focus();
      input.select();
      if (!document.execCommand("insertText", false, text)) {
        input.value = text;
        input.dispatchEvent(new Event("input"));
      }
      input.dispatchEvent(new Event("change"));
    } else {
      clip.transcript = text;
      onChange();
    }
    if (text) toast(`Transcribed ${clip.name}${res.language ? ` (${res.language})` : ""} in ${fmtSec(res.elapsed)}`, "ok");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    button.classList.remove("busy");
    button.innerHTML = `${icon("sparkle")} Generate`;
    refreshAsr();
  }
}

async function refreshAsr() {
  try {
    S.asr = await api("/api/asr");
  } catch {
    S.asr = null;
  }
  $$(".asr-btn").forEach((b) => { if (!b.classList.contains("busy")) b.setAttribute("aria-disabled", String(!(S.asr && S.asr.ready))); });
  renderAsr();
}

function renderAsr() {
  const root = $("#asr-panel");
  const a = S.asr;
  if (!a) {
    root.innerHTML = `<p class="muted">Transcription status is unavailable.</p>`;
    return;
  }
  const job = [...S.activeJobs.values()].find((j) => j.kind === "install" && j.meta.engine === "asr");
  const badge = job ? `<span class="badge accent">setting up</span>`
    : a.ready ? `<span class="badge ok">ready${a.loaded ? " · loaded" : ""}</span>`
    : a.model === "incomplete" ? `<span class="badge warn">incomplete</span>`
    : `<span class="badge">not installed</span>`;
  root.innerHTML = `
    <div class="engine">
      <div class="engine-head"><strong>${esc(a.label)}</strong>${badge}</div>
      <p>Speech recognition for reference transcripts. Use the Generate button next to a transcript box in the Studio or Voices tab.</p>
      <p><a href="https://huggingface.co/${esc(a.repo_id)}" target="_blank" rel="noreferrer">${esc(a.repo_id)}</a> · Apache-2.0${a.size ? ` · ${fmtBytes(a.size)}` : ""}</p>
      <div class="card-actions asr-actions"></div>
    </div>`;
  const actions = root.querySelector(".asr-actions");
  if (!a.ready && !job) {
    const install = document.createElement("button");
    install.className = "btn small";
    install.textContent = a.model === "ready" ? "Install environment" : a.model === "incomplete" ? "Resume setup" : "Install · about 4.7 GB";
    install.onclick = async () => {
      try {
        await api("/api/asr/install", { method: "POST" });
        toast("Setting up transcription. Progress is shown in Activity.", "ok");
        await refreshSystem();
        refreshJobs();
        renderAsr();
      } catch (err) {
        toast(err.message, "error");
      }
    };
    actions.appendChild(install);
  }
  if (a.model && !job) {
    const del = document.createElement("button");
    del.className = "icon-btn plain danger";
    del.innerHTML = icon("trash");
    del.title = "Delete the speech recognition model";
    del.onclick = async () => {
      if (!(await confirmBox("Delete model", `Delete ${a.label} (${fmtBytes(a.size)}) from disk? The environment stays installed.`))) return;
      try {
        await api("/api/asr/model", { method: "DELETE" });
        refreshAsr();
      } catch (err) {
        toast(err.message, "error");
      }
    };
    actions.appendChild(del);
  }
}

const READING_KIND = {
  user: "Your dictionary",
  dict: "Built-in dictionary",
  letters: "Spelled out letter by letter",
  auto: "Estimated by kanalizer",
  context: "Depends on context",
};

function readingEnabled() {
  const engine = currentEngine();
  return !!(engine && engine.reading && engineSettings(engine.id).reading_conversion);
}

function readingContextOn() {
  const engine = currentEngine();
  return !!(engine && engineSettings(engine.id).reading_context);
}

function readingOverride(seg) {
  return seg.key && Object.prototype.hasOwnProperty.call(S.readingOverrides, seg.key) ? S.readingOverrides[seg.key] : null;
}

function isConverted(seg) {
  return seg.reading !== null && seg.reading !== undefined && seg.kind !== "gap";
}

function readingText(result) {
  return result.segments.map((s) => (s.reading === null || s.reading === undefined ? s.text : readingOverride(s) ?? s.reading)).join("");
}

function usedOverrides(result) {
  const used = {};
  for (const seg of result.segments) {
    const value = readingOverride(seg);
    if (value !== null) used[seg.key] = value;
  }
  return used;
}

function saveOverrides() {
  const keys = Object.keys(S.readingOverrides);
  if (keys.length > 300) for (const key of keys.slice(0, keys.length - 300)) delete S.readingOverrides[key];
  store.set("readingOverrides", S.readingOverrides);
}

let readingTimer = null;
let readingSeq = 0;

function scheduleReading(delay = 450) {
  clearTimeout(readingTimer);
  readingTimer = setTimeout(() => refreshReading().catch(() => {}), delay);
}

async function refreshReading() {
  clearTimeout(readingTimer);
  const text = $("#text-input").value.trim();
  if (!readingEnabled() || !text) {
    readingSeq++;
    S.readingResult = null;
    renderReading();
    return null;
  }
  const context = readingContextOn();
  const cached = S.readingResult;
  if (cached && !S.readingStale && cached.source === text && cached.context.enabled === context) {
    renderReading();
    return cached;
  }
  const seq = ++readingSeq;
  $("#reading-panel").classList.add("busy");
  try {
    let result = await api("/api/reading/convert", { method: "POST", body: { text, context: false } });
    if (context) {
      result.context.enabled = true;
      if (result.context.words && result.context.available) {
        if (seq !== readingSeq) return result;
        S.readingResult = { ...result, context: { ...result.context, pending: true } };
        renderReading();
        result = await api("/api/reading/convert", { method: "POST", body: { text, context: true } });
      }
    }
    if (seq === readingSeq) {
      S.readingResult = result;
      S.readingStale = false;
      renderReading();
    }
    return result;
  } catch (err) {
    if (seq === readingSeq) $("#reading-status").textContent = `Could not convert: ${err.message}`;
    throw err;
  } finally {
    if (seq === readingSeq) $("#reading-panel").classList.remove("busy");
  }
}

function renderReading() {
  const panel = $("#reading-panel");
  const result = S.readingResult;
  const converted = result ? result.segments.filter(isConverted) : [];
  panel.hidden = !converted.length;
  if (panel.hidden) return;
  const body = $("#reading-body");
  body.innerHTML = "";
  result.segments.forEach((seg, index) => {
    if (seg.kind === "gap") return;
    if (!isConverted(seg)) {
      body.appendChild(document.createTextNode(seg.text));
      return;
    }
    const override = readingOverride(seg);
    const shown = override ?? seg.reading;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `rd-seg ${seg.kind}${override !== null ? " edited" : ""}`;
    btn.textContent = shown;
    const source = override !== null ? "Edited by you" : seg.kind === "context" && seg.by === "context" ? `Chosen by context${seg.confidence ? ` (${Math.round(seg.confidence * 100)}%)` : ""}` : READING_KIND[seg.kind] || "";
    btn.title = `${seg.text} → ${shown} · ${source}. Click to change.`;
    btn.onclick = () => editReading(index);
    body.appendChild(btn);
  });
  const ctx = result.context || {};
  const parts = [`${converted.length} word${converted.length === 1 ? "" : "s"} converted`];
  if (ctx.words) {
    const plural = ctx.words === 1 ? "" : "s";
    if (!ctx.enabled) parts.push(`context model off, ${ctx.words} word${plural} use the most common reading`);
    else if (!ctx.available) parts.push(`${ctx.words} word${plural} use the most common reading. Set up the context model in Models > Reading`);
    else if (ctx.pending) parts.push(`choosing ${ctx.words} word${plural} by context${ctx.loaded ? "" : ", loading the model first (up to about 30 s)"}`);
    else if (ctx.error) parts.push(`the context model failed (${ctx.error})`);
    else {
      const load = !ctx.loaded && ctx.wall && ctx.elapsed !== null ? `, model loaded in ${fmtSec(ctx.wall - ctx.elapsed)}` : "";
      parts.push(`${ctx.decided} chosen by ${ctx.label}${ctx.elapsed !== null && ctx.elapsed !== undefined ? ` in ${fmtSec(ctx.elapsed)}` : ""}${load}`);
    }
  }
  const edited = converted.filter((s) => readingOverride(s) !== null).length;
  if (edited) parts.push(`${edited} edited`);
  $("#reading-status").textContent = parts.join(" · ");
}

async function editReading(index) {
  const seg = S.readingResult?.segments[index];
  if (!seg) return;
  const override = readingOverride(seg);
  const current = override ?? seg.reading;
  const options = seg.kind === "context" ? seg.candidates : [{ reading: seg.reading, note: READING_KIND[seg.kind] || "" }];
  const custom = !options.some((c) => c.reading === current);
  const tag = (j) => {
    if (seg.kind !== "context" || j !== seg.choice) return "";
    if (seg.by === "context") return `<em>chosen by context${seg.confidence ? ` · ${Math.round(seg.confidence * 100)}%` : ""}</em>`;
    return "<em>default</em>";
  };
  const radios = options.map((c, j) => `
    <label class="rd-option"><input type="radio" name="rd-choice" value="${esc(c.reading)}" ${!custom && c.reading === current ? "checked" : ""}>
      <span><strong>${esc(c.reading)}</strong>${c.note ? `<small>${esc(c.note)}</small>` : ""}${tag(j)}</span></label>`).join("");
  const pending = modal({
    title: `Reading of “${seg.text}”`,
    body: `
      <div class="rd-options">${radios}
        <label class="rd-option"><input type="radio" name="rd-choice" value="__custom" ${custom ? "checked" : ""}>
          <span><strong>Custom</strong><input type="text" id="rd-custom" value="${esc(custom ? current : "")}" placeholder="Type the reading, e.g. in katakana" maxlength="128" autocomplete="off"></span></label>
      </div>
      <label class="switch small rd-save"><input type="checkbox" id="rd-save"><span></span>Save to the dictionary for every text</label>`,
    actions: [
      { label: "Cancel", value: () => null },
      ...(override !== null ? [{ label: "Reset", value: () => ({ reset: true }) }] : []),
      {
        label: "Apply",
        primary: true,
        value: (b) => {
          const picked = b.querySelector('input[name="rd-choice"]:checked')?.value;
          const reading = picked === "__custom" ? b.querySelector("#rd-custom").value.trim() : picked;
          return { reading, save: b.querySelector("#rd-save").checked };
        },
      },
    ],
  });
  const customInput = $("#rd-custom");
  customInput.addEventListener("input", () => { $('#modal-body input[value="__custom"]').checked = true; });
  const choice = await pending;
  if (!choice) return;
  if (choice.reset) {
    delete S.readingOverrides[seg.key];
    saveOverrides();
    renderReading();
    return;
  }
  if (!choice.reading) {
    toast("Enter a reading.", "error");
    return;
  }
  if (choice.save) {
    try {
      await api("/api/reading/dictionary", { method: "PUT", body: { word: seg.text, reading: choice.reading } });
      delete S.readingOverrides[seg.key];
      saveOverrides();
      S.readingStale = true;
      toast(`Saved “${seg.text}” as ${choice.reading} in the dictionary`, "ok");
      await refreshReading().catch(() => {});
      refreshReadingDict();
    } catch (err) {
      toast(err.message, "error");
    }
    return;
  }
  if (choice.reading === seg.reading) delete S.readingOverrides[seg.key];
  else S.readingOverrides[seg.key] = choice.reading;
  saveOverrides();
  renderReading();
}

async function refreshReadingStatus() {
  try {
    S.reading = await api("/api/reading");
  } catch {
    S.reading = null;
  }
  renderReadingModel();
}

function renderReadingModel() {
  const root = $("#reading-model-panel");
  const r = S.reading;
  if (!r) {
    root.innerHTML = `<p class="muted">Reading status is unavailable.</p>`;
    return;
  }
  const job = [...S.activeJobs.values()].find((j) => j.kind === "install" && j.meta.engine === "reading");
  const badge = job ? `<span class="badge accent">setting up</span>`
    : r.ready ? `<span class="badge ok">ready${r.loaded ? " · loaded" : ""}</span>`
    : r.model === "incomplete" ? `<span class="badge warn">incomplete</span>`
    : `<span class="badge">not installed</span>`;
  root.innerHTML = `
    <div class="engine">
      <div class="engine-head"><strong>kanalizer ${esc(r.kanalizer)}</strong><span class="badge ok">built in</span></div>
      <p>Converts English words to katakana for Irodori-TTS. A built-in dictionary fixes common words such as button (ボタン) and acronyms are spelled out (GUI becomes ジーユーアイ).</p>
      <p><a href="https://github.com/VOICEVOX/kanalizer" target="_blank" rel="noreferrer">VOICEVOX/kanalizer</a> · MIT</p>
    </div>
    <div class="engine">
      <div class="engine-head"><strong>${esc(r.label)}</strong>${badge}</div>
      <p>Optional context model. Picks between readings that depend on the sentence, such as sake (サケ or セイク). Runs on the CPU, so the TTS model keeps all of the VRAM. Stops after 10 min idle.</p>
      <p><a href="https://huggingface.co/${esc(r.repo_id)}" target="_blank" rel="noreferrer">${esc(r.repo_id)}</a> · LFM Open License v1.0${r.size ? ` · ${fmtBytes(r.size)}` : ""}</p>
      <div class="card-actions reading-actions"></div>
    </div>`;
  const actions = root.querySelector(".reading-actions");
  if (!r.ready && !job) {
    const install = document.createElement("button");
    install.className = "btn small";
    install.textContent = r.model === "ready" ? "Install environment" : r.model === "incomplete" ? "Resume setup" : "Install · about 2.5 GB";
    install.onclick = async () => {
      try {
        await api("/api/reading/install", { method: "POST" });
        toast("Setting up the context model. Progress is shown in Activity.", "ok");
        await refreshSystem();
        refreshJobs();
        renderReadingModel();
      } catch (err) {
        toast(err.message, "error");
      }
    };
    actions.appendChild(install);
  }
  if (r.model && !job) {
    const del = document.createElement("button");
    del.className = "icon-btn plain danger";
    del.innerHTML = icon("trash");
    del.title = "Delete the context model";
    del.onclick = async () => {
      if (!(await confirmBox("Delete model", `Delete ${r.label} (${fmtBytes(r.size)}) from disk? The environment stays installed.`))) return;
      try {
        await api("/api/reading/model", { method: "DELETE" });
        S.readingStale = true;
        refreshReadingStatus();
      } catch (err) {
        toast(err.message, "error");
      }
    };
    actions.appendChild(del);
  }
}

async function refreshReadingDict() {
  try {
    S.readingDict = (await api("/api/reading/dictionary")).entries || [];
  } catch (err) {
    toast(err.message, "error");
  }
  renderReadingDict();
}

function renderReadingDict() {
  const root = $("#reading-dict");
  const entries = S.readingDict || [];
  root.innerHTML = `
    <div class="engine">
      <div class="engine-head"><strong>Dictionary</strong><span class="badge">${entries.length} entr${entries.length === 1 ? "y" : "ies"}</span></div>
      <p>Your readings take priority over everything else. Entries match whole words and ignore case, except entries written in all capitals, which only match all-capital text. Japanese words work too, for example 神戸 → こうべ.</p>
      <form class="rd-add">
        <input type="text" name="word" placeholder="Word, e.g. Spectra" maxlength="64" autocomplete="off">
        <input type="text" name="reading" placeholder="Reading, e.g. スペクトラ" maxlength="128" autocomplete="off">
        <button class="btn small" type="submit">Add</button>
      </form>
      <ul class="rd-list">${entries.map((e, i) => `<li><span class="rd-w">${esc(e.word)}</span><span class="rd-arrow">→</span><span class="rd-r">${esc(e.reading)}</span><button class="icon-btn plain danger" type="button" data-i="${i}" title="Remove ${esc(e.word)}">${icon("trash")}</button></li>`).join("")}</ul>
    </div>`;
  const changed = (list) => {
    S.readingDict = list;
    S.readingStale = true;
    renderReadingDict();
    scheduleReading(0);
  };
  root.querySelector(".rd-add").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    try {
      const res = await api("/api/reading/dictionary", { method: "PUT", body: { word: form.word.value, reading: form.reading.value } });
      toast(`Saved “${form.word.value.trim()}”`, "ok");
      changed(res.entries);
      root.querySelector('.rd-add input[name="word"]').focus();
    } catch (err) {
      toast(err.message, "error");
    }
  });
  root.querySelectorAll(".rd-list button").forEach((btn) => {
    btn.onclick = async () => {
      const entry = entries[Number(btn.dataset.i)];
      try {
        const res = await api("/api/reading/dictionary", { method: "DELETE", body: { word: entry.word } });
        changed(res.entries);
      } catch (err) {
        toast(err.message, "error");
      }
    };
  });
}

function voiceModified() {
  if (!S.voiceId) return false;
  const voice = S.voices.find((v) => v.id === S.voiceId);
  if (!voice) return true;
  const a = JSON.stringify(voice.clips.map((c) => [c.id, (c.transcript || "").trim()]));
  const b = JSON.stringify(S.clips.map((c) => [c.id, (c.transcript || "").trim()]));
  return a !== b;
}

function renderVoiceSelect() {
  const select = $("#voice-select");
  const modified = voiceModified();
  select.innerHTML = `<option value="">Voice library…</option>` + S.voices.map((v) => `<option value="${esc(v.id)}">${esc(v.name)}${v.id === S.voiceId && modified ? " (modified)" : ""} · ${v.clips.length} clip${v.clips.length === 1 ? "" : "s"}</option>`).join("");
  select.value = S.voices.some((v) => v.id === S.voiceId) ? S.voiceId : "";
}

function renderClips() {
  const list = $("#clip-list");
  const engine = currentEngine();
  const mode = engine ? engine.reference.transcript : "optional";
  list.innerHTML = "";
  S.clips.forEach((clip, index) => {
    list.appendChild(renderClipItem(clip, index, S.clips.length, {
      transcriptMode: mode,
      onChange: () => { saveClips(); renderRefSummary(); renderVoiceSelect(); updateGenerateState(); },
      onRemove: () => { S.clips.splice(index, 1); saveClips(); renderClips(); },
      onMove: (delta) => {
        const j = index + delta;
        if (j < 0 || j >= S.clips.length) return;
        [S.clips[index], S.clips[j]] = [S.clips[j], S.clips[index]];
        saveClips();
        renderClips();
      },
    }));
  });
  renderRefSummary();
  renderVoiceSelect();
  updateGenerateState();
}

function renderRefSummary() {
  const el = $("#ref-summary");
  const engine = currentEngine();
  if (!S.clips.length) {
    el.innerHTML = engine ? `<span>${esc(engine.name)} works best with: ${esc(engine.reference.recommended)}.</span>` : "";
    return;
  }
  const total = S.clips.reduce((a, c) => a + (c.duration || 0), 0);
  const parts = [`<strong>${S.clips.length} clip${S.clips.length === 1 ? "" : "s"} · ${fmtSec(total)}</strong>`];
  if (engine) {
    const ref = engine.reference;
    const pct = Math.min(100, (total / ref.max_seconds) * 100);
    const cls = total > ref.max_seconds ? "over" : total < ref.min_seconds ? "low" : "";
    parts.push(`<span class="duration-meter" title="${fmtSec(total)} of ${ref.max_seconds} s"><span class="${cls}" style="width:${pct}%"></span></span>`);
    parts.push(`<span>${esc(ref.recommended)}</span>`);
    if (total > ref.max_seconds) parts.push(`<span class="badge warn">over ${ref.max_seconds} s, the rest is ignored or slows generation</span>`);
    if (!ref.multi_clip && S.clips.length > 1) parts.push(`<span class="badge">${esc(engine.name)} takes one reference, clips will be joined</span>`);
    if (ref.transcript === "required" && S.clips.some((c) => !(c.transcript || "").trim())) parts.push(`<span class="badge danger">transcripts required</span>`);
  }
  el.innerHTML = parts.join("");
}

function updateGenerateState() {
  const btn = $("#generate-btn");
  const reason = $("#gen-reason");
  const model = currentModel();
  const engine = currentEngine();
  const text = $("#text-input").value.trim();
  let why = "";
  if (!model) why = "Choose a model (or download one in the Models tab).";
  else if (!engine.installed) why = `Install the ${engine.name} environment in the Models tab.`;
  else if (!S.clips.length) why = "Add reference audio: this studio only clones voices.";
  else if (engine.reference.transcript === "required" && S.clips.some((c) => !(c.transcript || "").trim())) why = `${engine.name} needs a transcript for every reference clip.`;
  else if (!text) why = "Enter the text to speak.";
  btn.disabled = !!why || !!S.job || S.preparing;
  reason.textContent = S.job ? "" : why;
  $("#text-counter").textContent = `${$("#text-input").value.length} chars`;
}

const EMOJI_DETAIL = "Hover an emoji to see what it does. The effect depends on context and is not always consistent.";

function renderEmojiPalette() {
  const engine = currentEngine();
  const items = (engine && engine.emoji_palette) || [];
  $("#emoji-btn").hidden = !items.length;
  if (!items.length) {
    toggleEmojiPop(false);
    return;
  }
  const grid = $("#emoji-grid");
  if (grid.dataset.engine === engine.id) return;
  grid.dataset.engine = engine.id;
  grid.innerHTML = items.map((item) => `<button type="button" class="emoji-tile" data-emoji="${esc(item.emoji)}" data-detail="${esc(`${item.emoji} ${item.label}: ${item.description}`)}" aria-label="${esc(`${item.label}: ${item.description}`)}"><span class="emoji-glyph" aria-hidden="true">${esc(item.emoji)}</span><span class="emoji-label">${esc(item.label)}</span></button>`).join("");
  $("#emoji-detail").textContent = EMOJI_DETAIL;
}

function toggleEmojiPop(open) {
  const pop = $("#emoji-pop");
  const btn = $("#emoji-btn");
  if (open === undefined) open = pop.hidden;
  if (!open && pop.contains(document.activeElement)) btn.focus({ preventScroll: true });
  pop.hidden = !open;
  btn.classList.toggle("active", open);
  btn.setAttribute("aria-expanded", String(open));
  if (open) {
    if (S.helpAnchor === $("#text-input")) hideHelp();
    $("#emoji-detail").textContent = EMOJI_DETAIL;
    pop.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function insertEmoji(emoji) {
  const input = $("#text-input");
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;
  const active = document.activeElement;
  input.focus({ preventScroll: true });
  input.setSelectionRange(start, end);
  if (!document.execCommand("insertText", false, emoji)) {
    input.setRangeText(emoji, start, end, "end");
    input.dispatchEvent(new Event("input"));
  }
  if (active && active !== input && $("#emoji-pop").contains(active)) active.focus({ preventScroll: true });
}

function bindEmojiPalette() {
  const pop = $("#emoji-pop");
  const btn = $("#emoji-btn");
  const grid = $("#emoji-grid");
  const detail = $("#emoji-detail");
  $("#emoji-close").innerHTML = icon("x");
  $("#emoji-close").onclick = () => toggleEmojiPop(false);
  btn.onclick = () => toggleEmojiPop();
  pop.addEventListener("mousedown", (e) => { if (e.target.closest(".emoji-tile")) e.preventDefault(); });
  grid.addEventListener("click", (e) => {
    const tile = e.target.closest(".emoji-tile");
    if (tile) insertEmoji(tile.dataset.emoji);
  });
  const describe = (e) => {
    const tile = e.target.closest(".emoji-tile");
    detail.textContent = tile ? tile.dataset.detail : EMOJI_DETAIL;
  };
  grid.addEventListener("mouseover", describe);
  grid.addEventListener("focusin", describe);
  grid.addEventListener("mouseleave", () => { detail.textContent = EMOJI_DETAIL; });
  document.addEventListener("pointerdown", (e) => {
    if (pop.hidden || pop.contains(e.target) || btn.contains(e.target) || e.target === $("#text-input")) return;
    toggleEmojiPop(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !pop.hidden && $("#help-pop").hidden) toggleEmojiPop(false);
  });
}

function setGenerating(on) {
  $("#generate-btn").innerHTML = on ? `${icon("sparkle")} Generating…` : `${icon("sparkle")} Generate <span class="kbd">Ctrl ↵</span>`;
  $("#cancel-btn").hidden = !on;
  $("#gen-progress").hidden = !on;
  if (!on) $("#gen-stage").textContent = "";
  updateGenerateState();
  renderModelMeta();
}

async function uploadFiles(files) {
  if (!files || !files.length) return [];
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const zone = $("#dropzone");
  zone.classList.add("busy");
  try {
    const res = await api("/api/clips", { method: "POST", body: form });
    for (const err of res.errors || []) toast(`${err.name}: ${err.error}`, "error");
    return res.clips || [];
  } catch (err) {
    toast(err.message, "error");
    return [];
  } finally {
    zone.classList.remove("busy");
  }
}

async function addFilesToStudio(files) {
  const added = await uploadFiles(files);
  let count = 0;
  for (const clip of added) {
    if (S.clips.some((c) => c.id === clip.id)) continue;
    S.clips.push({ ...clip, transcript: "" });
    count++;
  }
  if (count) toast(`Added ${count} clip${count === 1 ? "" : "s"}`, "ok");
  saveClips();
  renderClips();
}

let recorder = null;
async function toggleRecording() {
  const btn = $("#record-btn");
  if (recorder) {
    recorder.stop();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
    const chunks = [];
    recorder = new MediaRecorder(stream);
    const started = Date.now();
    const timer = setInterval(() => { btn.innerHTML = `<span class="rec-dot">${icon("stop")}</span> ${((Date.now() - started) / 1000).toFixed(0)} s`; }, 250);
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      clearInterval(timer);
      stream.getTracks().forEach((t) => t.stop());
      recorder = null;
      btn.classList.remove("recording");
      btn.innerHTML = `${icon("mic")} Record`;
      const blob = new Blob(chunks, { type: chunks[0]?.type || "audio/webm" });
      const stamp = new Date().toISOString().replace(/[:T]/g, "-").slice(0, 19);
      await addFilesToStudio([new File([blob], `recording-${stamp}.webm`, { type: blob.type })]);
    };
    recorder.start();
    btn.classList.add("recording");
  } catch (err) {
    recorder = null;
    toast(`Microphone unavailable: ${err.message}`, "error");
  }
}

function entryCard(meta, { fresh = false, onRestore = null, onDelete = null } = {}) {
  const el = document.createElement("article");
  el.className = `result${fresh ? " fresh" : ""}`;
  const totalAudio = (meta.takes || []).reduce((a, t) => a + (t.duration || 0), 0);
  const rtf = totalAudio > 0 && meta.elapsed ? meta.elapsed / totalAudio : null;
  const chips = [
    `<span class="chip">${esc(meta.engine_name || meta.model?.engine)} · ${esc(meta.model?.label || "")}</span>`,
    meta.voice ? `<span class="chip">voice: ${esc(meta.voice.name)}</span>` : `<span class="chip">${(meta.clips || []).length} ref clip${(meta.clips || []).length === 1 ? "" : "s"}</span>`,
    meta.elapsed ? `<span class="chip" title="Generation time (excluding model load)">${fmtSec(meta.elapsed)}${rtf ? ` · RTF ${rtf.toFixed(2)}` : ""}</span>` : "",
    `<span class="chip">${fmtDate(meta.created_at)}</span>`,
  ];
  el.innerHTML = `
    <div class="result-head">
      <div class="result-text"></div>
      <div class="card-actions"></div>
    </div>
    ${meta.reading ? `<div class="result-reading" title="The text after reading conversion, as sent to the model"><span>Read as</span><span class="rr-text"></span></div>` : ""}
    <div class="result-meta">${chips.join("")}</div>
    <div class="takes"></div>
    ${(meta.messages || []).length ? `<ul class="messages">${meta.messages.map((m) => `<li class="${m.startsWith("warning") ? "warning" : m.startsWith("error") ? "error" : ""}">${esc(m.replace(/^(info|warning|error):\s*/, ""))}</li>`).join("")}</ul>` : ""}`;
  el.querySelector(".result-text").textContent = meta.text;
  if (meta.reading) el.querySelector(".rr-text").textContent = meta.reading;
  const actions = el.querySelector(".card-actions");
  if (onRestore) {
    const b = document.createElement("button");
    b.className = "btn ghost small";
    b.innerHTML = `${icon("restore")} Restore`;
    b.title = "Load this text, reference, model and settings into the Studio";
    b.onclick = onRestore;
    actions.appendChild(b);
  }
  if (onDelete) {
    const b = document.createElement("button");
    b.className = "icon-btn plain danger";
    b.innerHTML = icon("trash");
    b.title = "Delete from history";
    b.onclick = onDelete;
    actions.appendChild(b);
  }
  const takes = el.querySelector(".takes");
  (meta.takes || []).forEach((take, i) => {
    const row = document.createElement("div");
    row.className = "take";
    const src = `/api/history/${encodeURIComponent(meta.id)}/audio/${encodeURIComponent(take.file)}`;
    row.innerHTML = `
      <span class="take-label">Take ${i + 1}<br><span class="clip-dur">${fmtSec(take.duration)}</span></span>
      <audio controls preload="metadata" src="${src}"></audio>
      <span class="take-actions">
        <button class="chip" title="Use this seed in the Studio settings">seed ${esc(take.seed)}</button>
        <a class="icon-btn plain" href="${src}?download=true" title="Download WAV">${icon("download")}</a>
      </span>`;
    row.querySelector("button.chip").onclick = () => useSeed(meta.model?.engine, take.seed);
    takes.appendChild(row);
  });
  return el;
}

function useSeed(engineId, seed) {
  if (!engineId || !S.engines[engineId]) return;
  setSetting(engineId, "seed", Number(seed));
  if (currentEngine() && currentEngine().id === engineId) renderSettings();
  toast(`Seed ${seed} set for ${S.engines[engineId].name}`, "ok");
}

async function restoreEntry(meta) {
  if (S.models.some((m) => m.id === meta.model?.id && m.status === "ready")) {
    S.modelId = meta.model.id;
    store.set("model", S.modelId);
  } else {
    toast("The model of this entry is no longer installed. Other parts were restored.", "error");
  }
  const engineId = meta.model?.engine;
  if (engineId && S.engines[engineId]) {
    const known = new Set(S.engines[engineId].settings.map((s) => s.key));
    const values = {};
    for (const [k, v] of Object.entries(meta.settings || {})) if (known.has(k)) values[k] = v;
    S.settings[engineId] = { ...(S.settings[engineId] || {}), ...values };
    store.set("settings", S.settings);
  }
  const restored = [];
  for (const c of meta.clips || []) {
    try {
      const info = await api(`/api/clips/${c.id}`);
      restored.push({ ...info, transcript: c.transcript || "" });
    } catch {}
  }
  if (restored.length) S.clips = restored;
  S.voiceId = meta.voice?.id || "";
  if (meta.reading_overrides) {
    Object.assign(S.readingOverrides, meta.reading_overrides);
    saveOverrides();
  }
  $("#text-input").value = meta.text || "";
  store.set("text", meta.text || "");
  saveClips();
  location.hash = "studio";
  refreshStudio();
  toast("Restored into the Studio", "ok");
}

function addResult(meta) {
  S.results.unshift(meta);
  S.results = S.results.slice(0, 12);
  renderResults(true);
}

function renderResults(freshFirst = false) {
  const root = $("#results");
  root.innerHTML = "";
  S.results.forEach((meta, i) => root.appendChild(entryCard(meta, { fresh: freshFirst && i === 0, onRestore: () => restoreEntry(meta) })));
  if (freshFirst && root.firstElementChild) {
    const audio = root.firstElementChild.querySelector("audio");
    root.firstElementChild.scrollIntoView({ behavior: "smooth", block: "nearest" });
    if (audio && store.get("autoplay", true)) audio.play().catch(() => {});
  }
}

async function pollJob(id, onUpdate) {
  let failures = 0;
  while (true) {
    let job;
    try {
      job = await api(`/api/jobs/${id}`);
      failures = 0;
    } catch (err) {
      if (++failures > 10) throw err;
      await sleep(1000);
      continue;
    }
    onUpdate?.(job);
    if (!["queued", "running"].includes(job.status)) return job;
    await sleep(450);
  }
}

async function generate() {
  updateGenerateState();
  if ($("#generate-btn").disabled) return;
  const model = currentModel();
  const engine = currentEngine();
  const payload = {
    model_id: model.id,
    text: $("#text-input").value.trim(),
    clips: S.clips.map((c) => ({ id: c.id, transcript: (c.transcript || "").trim() })),
    voice_id: S.voiceId && !voiceModified() ? S.voiceId : null,
    settings: engineSettings(engine.id),
  };
  let job;
  S.preparing = true;
  updateGenerateState();
  try {
    if (readingEnabled()) {
      const result = await refreshReading().catch(() => null);
      if (result && result.source === payload.text) payload.reading = { source: result.source, text: readingText(result), overrides: usedOverrides(result) };
    }
    job = await api("/api/generate", { method: "POST", body: payload });
  } catch (err) {
    toast(err.message, "error");
    return;
  } finally {
    S.preparing = false;
    updateGenerateState();
  }
  S.job = job.id;
  setGenerating(true);
  const started = Date.now();
  const bar = $("#gen-progress");
  const final = await pollJob(job.id, (j) => {
    const secs = ((Date.now() - started) / 1000).toFixed(0);
    $("#gen-stage").textContent = `${j.stage || j.status} · ${secs} s`;
    bar.classList.toggle("indeterminate", j.progress === null || j.progress === undefined);
    bar.querySelector(".bar").style.width = `${Math.round((j.progress || 0) * 100)}%`;
  }).catch((err) => ({ status: "error", error: err.message }));
  S.job = null;
  setGenerating(false);
  refreshSystem();
  if (final.status === "done" && final.result) {
    addResult(final.result);
    toast(`Generated ${final.result.takes.length} take${final.result.takes.length === 1 ? "" : "s"} in ${fmtSec(final.result.wall_seconds)}`, "ok");
  } else {
    $("#gen-reason").textContent = final.error || "Generation failed.";
    toast(final.error || "Generation failed.", "error");
  }
}

async function cancelCurrent() {
  if (!S.job) return;
  try {
    await api(`/api/jobs/${S.job}/cancel`, { method: "POST" });
  } catch (err) {
    toast(err.message, "error");
  }
}

async function loadModelNow() {
  const model = currentModel();
  if (!model) return;
  try {
    const job = await api("/api/models/load", { method: "POST", body: { model_id: model.id, settings: engineSettings(model.engine) } });
    S.job = job.id;
    setGenerating(true);
    $("#generate-btn").innerHTML = `${icon("sparkle")} Loading…`;
    const final = await pollJob(job.id, (j) => {
      $("#gen-stage").textContent = j.stage || j.status;
      $("#gen-progress").classList.add("indeterminate");
    });
    S.job = null;
    setGenerating(false);
    await refreshSystem();
    if (final.status === "done") toast(`${model.label} loaded`, "ok");
    else toast(final.error || "Load failed", "error");
  } catch (err) {
    S.job = null;
    setGenerating(false);
    toast(err.message, "error");
  }
}

async function unloadModelNow() {
  try {
    const job = await api("/api/models/unload", { method: "POST" });
    await pollJob(job.id);
    await refreshSystem();
    toast("Worker stopped and VRAM released", "ok");
  } catch (err) {
    toast(err.message, "error");
  }
}

async function saveVoiceDialog() {
  if (!S.clips.length) {
    toast("Add reference clips first", "error");
    return;
  }
  const current = S.voices.find((v) => v.id === S.voiceId);
  const actions = [{ label: "Cancel", value: () => null }];
  if (current) actions.push({ label: `Update “${current.name}”`, value: (b) => ({ mode: "update", name: b.querySelector("#voice-name").value, notes: b.querySelector("#voice-notes").value }) });
  actions.push({ label: current ? "Save as new" : "Save", primary: true, value: (b) => ({ mode: "new", name: b.querySelector("#voice-name").value, notes: b.querySelector("#voice-notes").value }) });
  const result = await modal({
    title: "Save voice",
    body: `<div class="field"><label for="voice-name">Name</label><input type="text" id="voice-name" value="${esc(current ? current.name : "")}" placeholder="e.g. Narrator A"></div>
      <div class="field"><label for="voice-notes">Notes</label><textarea id="voice-notes" rows="2" placeholder="Source, mood, recording conditions…">${esc(current ? current.notes : "")}</textarea></div>
      <p class="muted">${S.clips.length} clip(s) with their transcripts will be saved.</p>`,
    actions,
  });
  if (!result) return;
  const clips = S.clips.map((c) => ({ id: c.id, transcript: (c.transcript || "").trim() }));
  try {
    const voice = result.mode === "update"
      ? await api(`/api/voices/${current.id}`, { method: "PUT", body: { name: result.name, notes: result.notes, clips } })
      : await api("/api/voices", { method: "POST", body: { name: result.name, notes: result.notes, clips } });
    S.voiceId = voice.id;
    saveClips();
    await refreshVoices();
    toast(`Saved voice “${voice.name}”`, "ok");
  } catch (err) {
    toast(err.message, "error");
  }
}

function useVoice(voice) {
  S.voiceId = voice.id;
  S.clips = voice.clips.filter((c) => !c.missing).map((c) => ({ ...c }));
  saveClips();
  renderClips();
}

async function refreshVoices() {
  try {
    S.voices = await api("/api/voices");
  } catch (err) {
    toast(err.message, "error");
  }
  renderVoiceSelect();
  if (currentTab() === "voices") renderVoices();
}

function renderVoices() {
  const grid = $("#voice-grid");
  grid.innerHTML = "";
  if (!S.voices.length) {
    grid.innerHTML = `<div class="empty-state"><strong>No saved voices yet</strong>Add reference clips in the Studio and click “Save as voice”.</div>`;
    return;
  }
  for (const voice of S.voices) {
    const card = document.createElement("article");
    card.className = "card voice-card";
    card.innerHTML = `
      <div class="card-head"><input class="voice-name" type="text" value="${esc(voice.name)}" aria-label="Voice name"><span class="chip">${voice.clips.length} clip${voice.clips.length === 1 ? "" : "s"} · ${fmtSec(voice.total_duration)}</span></div>
      <textarea placeholder="Notes" aria-label="Notes">${esc(voice.notes || "")}</textarea>
      <ul class="clip-list"></ul>
      <div class="voice-actions">
        <div class="card-actions"><button class="btn ghost small" data-act="add">${icon("plus")} Add clips</button><input type="file" accept="audio/*,video/*" multiple hidden></div>
        <div class="card-actions"><button class="btn ghost small danger" data-act="delete">${icon("trash")} Delete</button><button class="btn small" data-act="use">Use in Studio</button></div>
      </div>`;
    const clips = voice.clips.map((c) => ({ ...c }));
    const persist = async (patch) => {
      try {
        const updated = await api(`/api/voices/${voice.id}`, { method: "PUT", body: patch });
        Object.assign(voice, updated);
        renderVoiceSelect();
      } catch (err) {
        toast(err.message, "error");
      }
    };
    const list = card.querySelector(".clip-list");
    const drawClips = () => {
      list.innerHTML = "";
      clips.forEach((clip, index) => list.appendChild(renderClipItem(clip, index, clips.length, {
        transcriptMode: "optional",
        onChange: () => {},
        onRemove: async () => {
          if (clips.length === 1) { toast("A voice needs at least one clip. Delete the voice instead.", "error"); return; }
          clips.splice(index, 1);
          drawClips();
          await persist({ clips: clips.map((c) => ({ id: c.id, transcript: c.transcript || "" })) });
        },
        onMove: async (delta) => {
          const j = index + delta;
          if (j < 0 || j >= clips.length) return;
          [clips[index], clips[j]] = [clips[j], clips[index]];
          drawClips();
          await persist({ clips: clips.map((c) => ({ id: c.id, transcript: c.transcript || "" })) });
        },
      })));
      $$("input.transcript", list).forEach((input) => input.addEventListener("change", () => persist({ clips: clips.map((c) => ({ id: c.id, transcript: c.transcript || "" })) })));
    };
    drawClips();
    card.querySelector(".voice-name").addEventListener("change", (e) => persist({ name: e.target.value }));
    card.querySelector("textarea").addEventListener("change", (e) => persist({ notes: e.target.value }));
    const fileInput = card.querySelector('input[type="file"]');
    card.querySelector('[data-act="add"]').onclick = () => fileInput.click();
    fileInput.onchange = async () => {
      const added = await uploadFiles(fileInput.files);
      fileInput.value = "";
      for (const c of added) if (!clips.some((x) => x.id === c.id)) clips.push({ ...c, transcript: "" });
      drawClips();
      await persist({ clips: clips.map((c) => ({ id: c.id, transcript: c.transcript || "" })) });
    };
    card.querySelector('[data-act="use"]').onclick = () => { useVoice({ ...voice, clips }); location.hash = "studio"; toast(`Using “${voice.name}”`, "ok"); };
    card.querySelector('[data-act="delete"]').onclick = async () => {
      if (!(await confirmBox("Delete voice", `Delete “${voice.name}” from the library? Its audio stays in history entries that used it.`))) return;
      await api(`/api/voices/${voice.id}`, { method: "DELETE" });
      if (S.voiceId === voice.id) { S.voiceId = ""; saveClips(); }
      await refreshVoices();
    };
    grid.appendChild(card);
  }
}

async function loadHistory(reset = true) {
  if (reset) S.history.offset = 0;
  const list = $("#history-list");
  try {
    const res = await api(`/api/history?offset=${S.history.offset}&limit=20&q=${encodeURIComponent(S.history.query)}`);
    if (reset) list.innerHTML = "";
    S.history.total = res.total;
    for (const meta of res.items) {
      const card = entryCard(meta, {
        onRestore: () => restoreEntry(meta),
        onDelete: async () => {
          if (!(await confirmBox("Delete generation", "Delete this entry and its audio files?"))) return;
          await api(`/api/history/${meta.id}`, { method: "DELETE" });
          card.remove();
          S.results = S.results.filter((r) => r.id !== meta.id);
          renderResults();
        },
      });
      list.appendChild(card);
    }
    S.history.offset += res.items.length;
    if (!list.children.length) list.innerHTML = `<div class="empty-state"><strong>Nothing here yet</strong>${S.history.query ? "No entry matches the search." : "Generated audio will appear here."}</div>`;
    $("#history-more").hidden = S.history.offset >= S.history.total;
  } catch (err) {
    toast(err.message, "error");
  }
}

function renderEngineOptions() {
  $("#dl-engine").innerHTML = `<option value="">Auto-detect engine</option>` + S.engineList.map((e) => `<option value="${esc(e.id)}">${esc(e.name)}</option>`).join("");
}

function renderSuggested() {
  const root = $("#suggested");
  const have = new Set(S.models.filter((m) => m.status === "ready" && m.repo_id).map((m) => (m.repo_id + (m.subfolder ? "/" + m.subfolder : "")).toLowerCase()));
  root.innerHTML = S.engineList.map((e) => `<div><h4>${esc(e.name)}</h4><div class="chips">${(e.suggested || []).map((s) => {
    const owned = have.has(s.repo.toLowerCase());
    return `<button class="chip${owned ? " installed" : ""}" type="button" data-repo="${esc(s.repo)}" data-engine="${esc(e.id)}" title="${esc(s.note || "")}">${owned ? icon("check") : ""}${esc(s.label)}</button>`;
  }).join("")}</div></div>`).join("");
  $$("button[data-repo]", root).forEach((b) => {
    b.onclick = () => {
      $("#dl-link").value = b.dataset.repo;
      $("#dl-engine").value = b.dataset.engine;
      checkLink();
    };
    bindHelp(b, () => ({ kicker: "Suggested", title: b.textContent, summary: b.title || "", tip: `Repository: ${b.dataset.repo}` }));
  });
}

async function checkLink() {
  const link = $("#dl-link").value.trim();
  const preview = $("#dl-preview");
  if (!link) return;
  preview.innerHTML = `<div class="preview"><div class="progress indeterminate"><div class="bar"></div></div></div>`;
  try {
    const p = await api("/api/models/resolve", { method: "POST", body: { link, engine: $("#dl-engine").value || null } });
    const engine = S.engines[p.engine];
    const ready = p.existing === "ready";
    const action = p.existing === "incomplete" ? "Resume download" : "Download";
    const go = ready
      ? `<button class="btn" data-act="use">Use in Studio</button><button class="btn primary" disabled title="Delete it from Installed models first to download it again.">Already downloaded</button>`
      : `<button class="btn primary" data-act="go">${icon("download")} ${action} · ${fmtBytes(p.total_size)}</button>`;
    preview.innerHTML = `
      <div class="preview">
        <dl class="preview-grid">
          <dt>Repository</dt><dd><a href="${esc(p.source_url)}" target="_blank" rel="noreferrer">${esc(p.repo_id)}${p.subfolder ? " / " + esc(p.subfolder) : ""}</a> <span class="chip">${esc((p.sha || "").slice(0, 10))}</span></dd>
          <dt>Engine</dt><dd>${esc(engine ? engine.name : p.engine)} ${p.detected === p.engine ? `<span class="badge ok">detected</span>` : `<span class="badge warn">manual</span>`} ${engine && !engine.installed ? `<span class="badge danger">environment not installed</span>` : ""}</dd>
          <dt>Download</dt><dd><strong>${fmtBytes(p.total_size)}</strong> in ${p.files.length} file${p.files.length === 1 ? "" : "s"}</dd>
          <dt>Saved to</dt><dd><code>${esc(p.target)}</code> ${p.existing ? `<span class="badge ${ready ? "ok" : "warn"}">${ready ? "already downloaded" : "incomplete"}</span>` : ""}</dd>
        </dl>
        ${ready ? `<ul class="messages"><li>Already downloaded as <code>${esc(p.existing_id)}</code>. Re-downloading is disabled. Delete it from Installed models first if you really need a fresh copy.</li></ul>` : ""}
        ${p.warnings.length ? `<ul class="messages">${p.warnings.map((w) => `<li class="${w.startsWith("License") ? "" : "warning"}">${esc(w)}</li>`).join("")}</ul>` : ""}
        <details><summary>Files</summary><ul>${p.files.map((f) => `<li>${esc(f.name)} · ${fmtBytes(f.size)}</li>`).join("")}</ul></details>
        <div class="preview-actions"><button class="btn ghost" data-act="cancel">Cancel</button>${go}</div>
      </div>`;
    preview.querySelector('[data-act="cancel"]').onclick = () => (preview.innerHTML = "");
    if (ready) {
      preview.querySelector('[data-act="use"]').onclick = () => { S.modelId = p.existing_id; store.set("model", p.existing_id); preview.innerHTML = ""; location.hash = "studio"; refreshStudio(); };
      return;
    }
    preview.querySelector('[data-act="go"]').onclick = async () => {
      try {
        await api("/api/models/download", { method: "POST", body: { link, engine: p.engine } });
        preview.innerHTML = "";
        toast("Download started", "ok");
        refreshJobs();
      } catch (err) {
        toast(err.message, "error");
      }
    };
  } catch (err) {
    preview.innerHTML = `<div class="preview"><ul class="messages"><li class="error">${esc(err.message)}</li></ul></div>`;
  }
}

function renderModelTable() {
  const root = $("#model-table");
  if (!S.models.length) {
    root.innerHTML = `<div class="empty-state"><strong>No models downloaded</strong>Pick a suggested model above or paste a Hugging Face link.</div>`;
    return;
  }
  root.innerHTML = `<table><thead><tr><th>Model</th><th>Engine</th><th>Size</th><th>Status</th><th></th></tr></thead><tbody></tbody></table>`;
  const body = root.querySelector("tbody");
  for (const m of S.models) {
    const engine = S.engines[m.engine];
    const job = (S.downloadJobs || new Map()).get(m.path);
    const status = job
      ? `<span class="badge accent">downloading ${Math.round((job.progress || 0) * 100)}%</span>`
      : m.status === "ready" ? `<span class="badge ok">ready</span>` : `<span class="badge warn">incomplete</span>`;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${esc(m.label)}</strong><span class="sub">${esc(m.repo_id || m.folder)}${m.subfolder ? " / " + esc(m.subfolder) : ""}${m.revision ? " · " + esc(String(m.revision).slice(0, 8)) : ""}${m.note ? " · " + esc(m.note) : ""}</span></td>
      <td>${esc(engine ? engine.name : m.engine)}</td>
      <td>${job ? fmtBytes((job.progress || 0) * (job.meta.total || 0)) : fmtBytes(m.size)}</td>
      <td>${status}</td>
      <td class="actions"></td>`;
    const actions = tr.querySelector(".actions");
    if (job) {
      const cancel = document.createElement("button");
      cancel.className = "btn ghost small";
      cancel.textContent = "Cancel";
      cancel.onclick = () => api(`/api/jobs/${job.id}/cancel`, { method: "POST" }).then(refreshJobs);
      actions.appendChild(cancel);
    } else if (m.status === "ready") {
      const use = document.createElement("button");
      use.className = "btn ghost small";
      use.textContent = "Use";
      use.onclick = () => { S.modelId = m.id; store.set("model", m.id); location.hash = "studio"; refreshStudio(); };
      actions.appendChild(use);
    } else {
      const resume = document.createElement("button");
      resume.className = "btn small";
      resume.textContent = "Resume";
      resume.onclick = async () => {
        try {
          await api(`/api/models/${m.engine}/${encodeURIComponent(m.folder)}/resume`, { method: "POST" });
          refreshJobs();
        } catch (err) {
          toast(err.message, "error");
        }
      };
      actions.appendChild(resume);
    }
    const del = document.createElement("button");
    del.className = "icon-btn plain danger";
    del.innerHTML = icon("trash");
    del.title = "Delete model files";
    del.onclick = async () => {
      if (!(await confirmBox("Delete model", `Delete ${m.label} (${fmtBytes(m.size)}) from disk?`))) return;
      try {
        await api(`/api/models/${m.engine}/${encodeURIComponent(m.folder)}`, { method: "DELETE" });
        await refreshModels();
        refreshSystem();
      } catch (err) {
        toast(err.message, "error");
      }
    };
    actions.appendChild(del);
    body.appendChild(tr);
  }
}

function renderEngines() {
  const root = $("#engine-list");
  root.innerHTML = "";
  for (const e of S.engineList) {
    const el = document.createElement("div");
    el.className = "engine";
    el.innerHTML = `
      <div class="engine-head"><strong>${esc(e.name)}</strong>${e.installed ? `<span class="badge ok">environment ready</span>` : `<button class="btn small">Install environment</button>`}</div>
      <p>${esc(e.tagline)}</p>
      <p><a href="${esc(e.homepage)}" target="_blank" rel="noreferrer">${esc(e.homepage.replace("https://", ""))}</a> · ${esc(e.license)}</p>`;
    const btn = el.querySelector("button");
    if (btn) btn.onclick = async () => {
      try {
        await api(`/api/engines/${e.id}/install`, { method: "POST" });
        toast(`Installing ${e.name}. This can take several minutes.`, "ok");
        refreshJobs();
      } catch (err) {
        toast(err.message, "error");
      }
    };
    root.appendChild(el);
  }
}

let jobsTimer = null;
async function refreshJobs() {
  try {
    const items = await api("/api/jobs?limit=12");
    const downloads = new Map(items.filter((j) => j.kind === "download" && ["queued", "running"].includes(j.status)).map((j) => [j.meta.target, j]));
    const finished = [...(S.downloadJobs || new Map()).keys()].some((target) => !downloads.has(target));
    S.downloadJobs = downloads;
    if (finished) await refreshModels();
    else if (downloads.size && currentTab() === "models") renderModelTable();
    const root = $("#job-list");
    const relevant = items.filter((j) => ["download", "install", "load", "unload"].includes(j.kind) || j.active);
    root.innerHTML = relevant.length ? "" : `<p class="muted">Downloads and installs appear here.</p>`;
    for (const j of relevant) {
      const el = document.createElement("div");
      el.className = "job";
      const active = ["queued", "running"].includes(j.status);
      const badge = { done: "ok", error: "danger", cancelled: "warn" }[j.status] || "accent";
      el.innerHTML = `
        <div class="job-head"><span>${esc(j.title)}</span><span class="card-actions"><span class="badge ${badge}">${esc(j.status)}</span>${active && j.kind !== "unload" ? `<button class="btn ghost small" data-act="cancel">Cancel</button>` : ""}</span></div>
        ${active ? `<div class="progress ${j.progress === null ? "indeterminate" : ""}"><div class="bar" style="width:${Math.round((j.progress || 0) * 100)}%"></div></div>` : ""}
        ${j.stage ? `<div class="gen-stage">${esc(j.stage)}</div>` : ""}
        ${j.error ? `<div class="job-error">${esc(j.error)}</div>` : ""}
        ${j.log && j.log.length ? `<details><summary class="muted">Log</summary><pre>${esc(j.log.join("\n"))}</pre></details>` : ""}`;
      const cancel = el.querySelector('[data-act="cancel"]');
      if (cancel) cancel.onclick = () => api(`/api/jobs/${j.id}/cancel`, { method: "POST" }).then(refreshJobs);
      root.appendChild(el);
    }
    const anyActive = items.some((j) => ["queued", "running"].includes(j.status));
    clearTimeout(jobsTimer);
    if (currentTab() === "models") jobsTimer = setTimeout(refreshJobs, anyActive ? 900 : 4000);
  } catch {}
}

async function refreshModels() {
  try {
    S.models = await api("/api/models");
  } catch (err) {
    toast(err.message, "error");
  }
  renderModelSelect();
  renderModelTable();
  renderSuggested();
  refreshStudio();
}

async function refreshEngines() {
  S.engineList = await api("/api/engines");
  S.engines = Object.fromEntries(S.engineList.map((e) => [e.id, e]));
  renderEngineOptions();
  renderSuggested();
  renderEngines();
}

function refreshStudio() {
  renderModelSelect();
  renderModelMeta();
  renderSettings();
  renderClips();
  const engine = currentEngine();
  $("#text-hint").textContent = engine ? TEXT_HINTS[engine.id] || "" : "";
  renderEmojiPalette();
  updateGenerateState();
  scheduleReading(0);
}

async function refreshSystem() {
  try {
    const sys = await api("/api/system");
    const prevActive = new Set(S.activeJobs.keys());
    S.worker = sys.worker;
    S.gpu = sys.gpu;
    S.activeJobs = new Map((sys.jobs || []).map((j) => [j.id, j]));
    for (const id of prevActive) {
      if (!S.activeJobs.has(id)) {
        refreshModels();
        refreshAsr();
        refreshReadingStatus();
        S.readingStale = true;
        refreshEngines().then(refreshStudio).catch(() => {});
        break;
      }
    }
    const pill = $("#worker-pill");
    const w = sys.worker || {};
    pill.classList.toggle("busy", !!w.busy);
    pill.classList.toggle("ok", !w.busy && !!w.loaded);
    pill.querySelector(".pill-text").textContent = w.busy ? (w.activity || "Working…") : w.loaded ? `${w.loaded.label}` : w.engine ? `${S.engines[w.engine]?.name || w.engine} (no model)` : "Idle";
    pill.title = w.loaded ? `${w.loaded.label} · ${w.loaded.precision || w.loaded.dtype || w.loaded.quantization || ""}` : "No model loaded";
    const gpu = $("#gpu-pill");
    if (sys.gpu) {
      const pct = (sys.gpu.used_mb / sys.gpu.total_mb) * 100;
      const meter = gpu.querySelector(".meter span");
      meter.style.width = `${pct}%`;
      meter.className = pct > 95 ? "full" : pct > 80 ? "high" : "";
      gpu.querySelector(".pill-text").textContent = `${(sys.gpu.used_mb / 1024).toFixed(1)} / ${(sys.gpu.total_mb / 1024).toFixed(1)} GB`;
      gpu.title = `${sys.gpu.name} · ${sys.gpu.util}% utilization`;
      gpu.hidden = false;
    } else {
      gpu.hidden = true;
    }
    renderModelMeta();
  } catch {}
}

let logTimer = null;
async function pollLogs() {
  const drawer = $("#log-drawer");
  if (drawer.hidden) return;
  try {
    const res = await api(`/api/logs?since=${S.logCursor}`);
    const body = $("#log-body");
    if (res.lines.length) {
      const frag = document.createDocumentFragment();
      for (const l of res.lines) {
        const line = document.createElement("div");
        line.innerHTML = `<span class="src">[${esc(l.source)}]</span> ${esc(l.line)}`;
        frag.appendChild(line);
      }
      body.appendChild(frag);
      while (body.childElementCount > 2500) body.firstElementChild.remove();
      if ($("#log-follow").checked) body.scrollTop = body.scrollHeight;
    }
    S.logCursor = res.cursor;
  } catch {}
  logTimer = setTimeout(pollLogs, 1000);
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  store.set("theme", theme);
  const btn = $("#theme-btn");
  btn.innerHTML = icon({ system: "monitor", light: "sun", dark: "moon" }[theme]);
  btn.title = `Theme: ${theme} (click to change)`;
}

function currentTab() {
  const tab = location.hash.replace("#", "");
  return ["studio", "voices", "history", "models"].includes(tab) ? tab : "studio";
}

function showTab() {
  const tab = currentTab();
  $$(".view").forEach((v) => v.classList.toggle("active", v.dataset.view === tab));
  $$(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === tab)));
  store.set("tab", tab);
  if (tab === "voices") renderVoices();
  if (tab === "history") loadHistory(true);
  if (tab === "models") { renderModelTable(); renderEngines(); refreshJobs(); refreshAsr(); refreshReadingStatus(); refreshReadingDict(); }
}

function bindStatic() {
  $$(".tab").forEach((t) => (t.onclick = () => (location.hash = t.dataset.tab)));
  window.addEventListener("hashchange", showTab);
  $("#model-select").addEventListener("change", (e) => {
    S.modelId = e.target.value;
    store.set("model", S.modelId);
    refreshStudio();
  });
  $("#load-btn").onclick = loadModelNow;
  $("#unload-btn").onclick = unloadModelNow;
  $("#voice-select").addEventListener("change", (e) => {
    const voice = S.voices.find((v) => v.id === e.target.value);
    if (voice) useVoice(voice);
    else { S.voiceId = ""; saveClips(); renderVoiceSelect(); }
  });
  $("#save-voice-btn").onclick = saveVoiceDialog;
  $("#clear-clips-btn").onclick = () => { S.clips = []; S.voiceId = ""; saveClips(); renderClips(); };
  const zone = $("#dropzone");
  const fileInput = $("#file-input");
  zone.addEventListener("click", (e) => { if (!e.target.closest("button")) fileInput.click(); });
  zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  $("#browse-btn").onclick = () => fileInput.click();
  fileInput.onchange = () => { addFilesToStudio(fileInput.files); fileInput.value = ""; };
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("over"); }));
  zone.addEventListener("drop", (e) => addFilesToStudio(e.dataTransfer.files));
  $(".dz-icon").innerHTML = icon("upload");
  $("#record-btn").innerHTML = `${icon("mic")} Record`;
  $("#record-btn").onclick = toggleRecording;
  const text = $("#text-input");
  text.value = store.get("text", "");
  text.addEventListener("input", () => { store.set("text", text.value); updateGenerateState(); scheduleReading(); });
  bindEmojiPalette();
  $("#generate-btn").onclick = generate;
  $("#cancel-btn").onclick = cancelCurrent;
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && currentTab() === "studio") { e.preventDefault(); generate(); }
  });
  const adv = $("#advanced-toggle");
  adv.checked = S.advanced;
  adv.onchange = () => { S.advanced = adv.checked; store.set("advanced", S.advanced); renderSettings(); };
  $("#reset-settings-btn").onclick = () => {
    const engine = currentEngine();
    if (!engine) return;
    delete S.settings[engine.id];
    store.set("settings", S.settings);
    renderSettings();
    renderModelMeta();
    toast(`${engine.name} settings reset`, "ok");
  };
  for (const el of $$("[data-help]")) {
    const key = el.dataset.help;
    bindHelp(el.closest(".switch") || el, () => {
      if (key === "text" && !$("#emoji-pop").hidden) return null;
      const base = HELP[key];
      if (key === "reference" || key === "text") {
        const engine = currentEngine();
        const extra = engine ? (key === "reference" ? `${engine.name}: ${engine.reference.recommended}.` : TEXT_HINTS[engine.id]) : base.tip;
        return { ...base, tip: extra || base.tip };
      }
      return base;
    });
  }
  $("#dl-form").addEventListener("submit", (e) => { e.preventDefault(); checkLink(); });
  let searchTimer = null;
  $("#history-search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { S.history.query = e.target.value; loadHistory(true); }, 250);
  });
  $("#history-more").onclick = () => loadHistory(false);
  $("#theme-btn").onclick = () => {
    const order = ["system", "light", "dark"];
    applyTheme(order[(order.indexOf(document.documentElement.dataset.theme) + 1) % order.length]);
  };
  $("#logs-btn").innerHTML = icon("terminal");
  $("#log-close").innerHTML = icon("x");
  $("#logs-btn").onclick = () => {
    const drawer = $("#log-drawer");
    drawer.hidden = !drawer.hidden;
    clearTimeout(logTimer);
    if (!drawer.hidden) pollLogs();
  };
  $("#log-close").onclick = () => { $("#log-drawer").hidden = true; clearTimeout(logTimer); };
  $("#log-clear").onclick = () => { $("#log-body").innerHTML = ""; };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#help-pop").hidden) hideHelp(); });
  $("#help-pop").addEventListener("mousedown", (e) => e.preventDefault());
}

async function init() {
  applyTheme(store.get("theme", "system"));
  if (!location.hash) location.hash = store.get("tab", "studio");
  bindStatic();
  setGenerating(false);
  try {
    await refreshEngines();
    S.models = await api("/api/models");
    S.voices = await api("/api/voices");
    await refreshAsr();
  } catch (err) {
    toast(`Could not reach the server: ${err.message}`, "error");
  }
  const known = [];
  for (const c of S.clips) {
    try {
      known.push({ ...(await api(`/api/clips/${c.id}`)), transcript: c.transcript || "" });
    } catch {}
  }
  S.clips = known;
  saveClips();
  refreshStudio();
  renderModelTable();
  renderSuggested();
  showTab();
  try {
    const recent = await api("/api/history?limit=3");
    S.results = recent.items;
    renderResults();
  } catch {}
  await refreshSystem();
  setInterval(refreshSystem, 2500);
}

init();
