const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;

const $ = (id) => document.getElementById(id);

const TEXT_FIELDS = [
  "api_base", "model", "size", "aspect_ratio", "quality",
  "custom_url", "custom_body", "custom_response_path",
  "character_appearance", "character_persona", "character_hairstyle",
  "reply_prefix", "blocked_reply", "negative_prompt",
];
const NUMBER_FIELDS = [
  "n", "outfit_change_cooldown_minutes", "cooldown_seconds",
  "private_daily_limit", "group_daily_limit",
];
const CHECK_FIELDS = [
  "chat_modalities", "lock_scene_to_schedule", "enable", "safe_mode",
  "allow_private", "allow_group", "require_at_in_group", "quote_in_group",
];

const els = {
  theme: $("theme"),
  extractPersona: $("extractPersona"),
  personaHint: $("personaHint"),
  enablePill: $("enablePill"),
  providerName: $("providerName"),
  apiBase: $("apiBase"),
  modelName: $("modelName"),
  sizeText: $("sizeText"),
  keyMasked: $("keyMasked"),
  usedToday: $("usedToday"),
  callsTotal: $("callsTotal"),
  statusMeta: $("statusMeta"),
  preset: $("preset"),
  presetHint: $("presetHint"),
  save: $("save"),
  saveHint: $("saveHint"),
  prompt: $("prompt"),
  draw: $("draw"),
  drawHint: $("drawHint"),
  preview: $("preview"),
  previewImg: $("previewImg"),
  previewMeta: $("previewMeta"),
  history: $("history"),
  refresh: $("refresh"),
  customBlock: $("customBlock"),
};

let presets = [];

function setTheme(mode) {
  document.documentElement.dataset.theme = mode;
  try { localStorage.setItem("amo-image-theme", mode); } catch (_) {}
}

(function initTheme() {
  let mode = "light";
  try { mode = localStorage.getItem("amo-image-theme") || "light"; } catch (_) {}
  setTheme(mode);
})();

els.theme.addEventListener("click", () => {
  setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

function escapeHtml(text) {
  return String(text == null ? "" : text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function fmtBytes(n) {
  const value = Number(n) || 0;
  if (value >= 1024 * 1024) return (value / 1024 / 1024).toFixed(1) + " MB";
  if (value >= 1024) return (value / 1024).toFixed(0) + " KB";
  return value + " B";
}

function unwrap(result) {
  if (result && typeof result === "object" && "data" in result &&
      ("code" in result || "status" in result)) {
    return result.data ?? result;
  }
  return result;
}

async function get(path, params) {
  if (!bridge) throw new Error("没有检测到 AstrBot 面板桥接，请在 AstrBot WebUI 里打开这个页面");
  return bridge.apiGet(path, params || {});
}

async function post(path, body) {
  if (!bridge) throw new Error("没有检测到 AstrBot 面板桥接，请在 AstrBot WebUI 里打开这个页面");
  return bridge.apiPost(path, body || {});
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("on", tab.dataset.tab === name);
  });
  document.querySelectorAll(".pane").forEach((pane) => {
    pane.hidden = pane.id !== "pane-" + name;
  });
  if (name === "config") loadConfig();
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => showTab(tab.dataset.tab));
});

function syncCustomBlock() {
  els.customBlock.hidden = $("provider").value !== "custom";
}

$("provider").addEventListener("change", syncCustomBlock);

function renderStatus(data) {
  const on = !!data.enabled;
  els.enablePill.textContent = on ? "已启用" : "已关闭";
  els.enablePill.className = "pill" + (on ? "" : " off");
  els.providerName.textContent = data.provider || "openai";
  els.apiBase.textContent = data.api_base || "未配置";
  els.modelName.textContent = data.model || "—";
  els.sizeText.textContent = data.size || "—";
  els.keyMasked.textContent = data.api_key_masked || "未配置";
  els.usedToday.textContent = String(data.users_today ?? 0);
  els.callsTotal.textContent = String(data.calls_total ?? 0);
  els.statusMeta.textContent = [
    "私聊" + (data.allow_private ? "开" : "关"),
    "群聊" + (data.allow_group ? "开" : "关"),
    "冷却 " + (data.cooldown_seconds ?? 0) + "s",
    "私聊日限 " + ((data.private_daily_limit ?? 0) || "不限"),
    "群日限 " + ((data.group_daily_limit ?? 0) || "不限") + "/群",
    "边界 " + (data.safe_mode ? "开" : "关"),
    "运行 " + Math.floor((data.uptime_seconds || 0) / 60) + " 分钟",
    "今天穿 " + (data.look || "—"),
  ].join(" · ");
}

function renderHistory(items) {
  if (!items || !items.length) {
    els.history.innerHTML = '<li class="empty">还没有记录</li>';
    return;
  }
  els.history.innerHTML = items.map((item) => {
    const flag = item.ok
      ? '<span class="s ok">' + (item.ms || 0) + "ms</span>"
      : '<span class="s bad">失败</span>';
    return '<li><span class="t">' + escapeHtml(item.time || "") + "</span>" +
      '<span class="p" title="' + escapeHtml(item.prompt || "") + '">' + escapeHtml(item.prompt || "") + "</span>" +
      flag + "</li>";
  }).join("");
}

async function refresh() {
  try {
    const [status, history] = await Promise.all([get("status"), get("history")]);
    const info = unwrap(status);
    const records = unwrap(history);
    if (info) renderStatus(info);
    if (records) renderHistory(records.items || []);
  } catch (err) {
    els.statusMeta.textContent = String(err.message || err);
  }
}

async function loadPresets() {
  try {
    const result = unwrap(await get("presets"));
    presets = (result && result.items) || [];
  } catch (_) {
    presets = [];
  }
  els.preset.innerHTML = '<option value="">手动填写</option>' +
    presets.map((item, index) =>
      '<option value="' + index + '">' + escapeHtml(item.name || item.id) + "</option>").join("");
}

els.preset.addEventListener("change", () => {
  const item = presets[Number(els.preset.value)];
  if (!item) return;
  $("provider").value = item.provider || "openai";
  if (item.api_base) $("api_base").value = item.api_base;
  if (item.model) $("model").value = item.model;
  syncCustomBlock();
  els.presetHint.textContent = "已填入 " + (item.name || item.id) + "，密钥要自己填";
});

async function loadConfig() {
  try {
    const result = unwrap(await get("config"));
    const data = result || {};
    TEXT_FIELDS.forEach((key) => {
      const el = $(key);
      if (el && data[key] != null) el.value = String(data[key]);
    });
    NUMBER_FIELDS.forEach((key) => {
      const el = $(key);
      if (el && data[key] != null) el.value = String(data[key]);
    });
    CHECK_FIELDS.forEach((key) => {
      const el = $(key);
      if (el) el.checked = !!data[key];
    });
    if (data.provider) $("provider").value = data.provider;
    if (data.character_gender) $("character_gender").value = data.character_gender;
    if (data.camera_mode) $("camera_mode").value = data.camera_mode;
    const outfits = data.character_outfits;
    $("character_outfits").value = Array.isArray(outfits) ? outfits.join("\n") : (outfits || "");
    const masked = data.api_key_masked || (/…/.test(data.api_key || "") ? data.api_key : "");
    $("api_key").value = "";
    $("api_key").placeholder = masked && masked !== "未配置"
      ? "已配置 " + masked + "（留空不改）"
      : "还没配置";
    syncCustomBlock();
  } catch (err) {
    els.saveHint.textContent = "读取配置失败：" + String(err.message || err);
  }
}

function collectConfig() {
  const payload = {};
  TEXT_FIELDS.forEach((key) => {
    const el = $(key);
    if (el) payload[key] = el.value;
  });
  NUMBER_FIELDS.forEach((key) => {
    const el = $(key);
    if (el) payload[key] = Number(el.value || 0);
  });
  CHECK_FIELDS.forEach((key) => {
    const el = $(key);
    if (el) payload[key] = !!el.checked;
  });
  payload.provider = $("provider").value;
  payload.character_gender = $("character_gender").value;
  payload.camera_mode = $("camera_mode").value;
  payload.character_outfits = $("character_outfits").value
    .split("\n").map((line) => line.trim()).filter(Boolean);
  const key = $("api_key").value.trim();
  if (key) payload.api_key = key;
  return payload;
}

els.save.addEventListener("click", async () => {
  els.save.disabled = true;
  els.saveHint.textContent = "保存中…";
  try {
    const saved = unwrap(await post("config/save", collectConfig())) || {};
    if (saved.saved !== undefined || saved.applied) {
      els.saveHint.textContent = saved.saved ? "已保存并写回配置" : "已生效（当前配置对象不支持落盘）";
      $("api_key").value = "";
      await refresh();
      await loadConfig();
    } else {
      els.saveHint.textContent = saved.message || saved.msg || "保存失败";
    }
  } catch (err) {
    els.saveHint.textContent = String(err.message || err);
  } finally {
    els.save.disabled = false;
  }
});

els.draw.addEventListener("click", async () => {
  const prompt = (els.prompt.value || "").trim();
  if (!prompt) { els.drawHint.textContent = "先写点什么"; return; }
  els.draw.disabled = true;
  els.drawHint.textContent = "画着呢…";
  els.preview.hidden = true;
  try {
    const result = unwrap(await post("test", { prompt })) || {};
    if (result.image) {
      els.previewImg.src = result.image;
      els.previewMeta.textContent = fmtBytes(result.bytes) + " · " + result.ms + "ms";
      els.preview.hidden = false;
      els.drawHint.textContent = "画好了";
    } else {
      els.drawHint.textContent = result.message || result.error || "没画出来";
    }
  } catch (err) {
    els.drawHint.textContent = String(err.message || err);
  } finally {
    els.draw.disabled = false;
    refresh();
  }
});

els.extractPersona.addEventListener("click", async () => {
  els.extractPersona.disabled = true;
  els.personaHint.textContent = "读人格中…";
  try {
    const info = unwrap(await post("persona/extract", {})) || {};
    if (info.appearance) $("character_appearance").value = info.appearance;
    if (info.gender) $("character_gender").value = info.gender;
    if (info.persona) $("character_persona").value = info.persona;
    els.personaHint.textContent = info.source
      ? "已从" + info.source + "提取，记得点保存"
      : "已提取，记得点保存";
  } catch (err) {
    els.personaHint.textContent = "提取失败：" + String(err.message || err);
  } finally {
    els.extractPersona.disabled = false;
  }
});

els.refresh.addEventListener("click", refresh);

loadPresets();
refresh();
setInterval(refresh, 15000);
