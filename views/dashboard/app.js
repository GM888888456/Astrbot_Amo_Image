/* AstrBot 把 bridge-sdk.js 注入在 app.js **之后**（实测 56 行是本文件、57 行才是 SDK），
   所以脚本刚执行时 window.AstrBotPluginView 还不存在——只抓一次就会永远抓不到，
   面板会一直「读取失败」。这里每次调用都重新找，并给启动加一个等待桥接的重试。 */
let bridge = null;
function resolveBridge() {
  if (bridge) return bridge;
  const candidates = [];
  try { candidates.push(window.AstrBotPluginView, window.AstrBotPluginPage); } catch (err) {}
  try { candidates.push(window.parent.AstrBotPluginView, window.parent.AstrBotPluginPage); } catch (err) {}
  try { candidates.push(window.top.AstrBotPluginView, window.top.AstrBotPluginPage); } catch (err) {}
  bridge = candidates.find((item) => item && typeof item.apiGet === "function") || null;
  return bridge;
}
function waitForBridge(fn, tries) {
  const left = (typeof tries === "number") ? tries : 50;   // 默认最多等 6 秒
  if (resolveBridge()) { fn(); return; }
  if (left > 0) setTimeout(() => waitForBridge(fn, left - 1), 120);
}

const $ = (id) => document.getElementById(id);

const TEXT_FIELDS = [
  "api_base", "model", "size", "aspect_ratio", "quality",
  "custom_url", "custom_body", "custom_response_path",
  "character_appearance", "character_persona", "character_hairstyle",
  "reply_prefix", "blocked_reply", "negative_prompt",
];
const NUMBER_FIELDS = [
  "n", "outfit_change_cooldown_minutes", "cooldown_seconds",
  "private_daily_limit", "group_daily_limit", "batch_max",
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
  model: $("model"),
  modelHint: $("modelHint"),
  fetchModels: $("fetchModels"),
  modelManual: $("modelManual"),
  save: $("save"),
  saveHint: $("saveHint"),
  wearNow: $("wearNow"),
  wardrobeMeta: $("wardrobeMeta"),
  wName: $("wName"),
  wScenes: $("wScenes"),
  wGender: $("wGender"),
  wNote: $("wNote"),
  wHint: $("wHint"),
  wList: $("wList"),
  wRefresh: $("wRefresh"),
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
  const b = resolveBridge();
  if (!b) throw new Error("没有检测到 AstrBot 面板桥接，请在 AstrBot WebUI 里打开这个页面");
  return b.apiGet(path, params || {});
}

async function post(path, body) {
  const b = resolveBridge();
  if (!b) throw new Error("没有检测到 AstrBot 面板桥接，请在 AstrBot WebUI 里打开这个页面");
  return b.apiPost(path, body || {});
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("on", tab.dataset.tab === name);
  });
  document.querySelectorAll(".pane").forEach((pane) => {
    pane.hidden = pane.id !== "pane-" + name;
  });
  if (name === "config") {
    loadConfig().then(() => loadModels());
  }
  if (name === "wardrobe") {
    loadWardrobe();
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => showTab(tab.dataset.tab));
});

function syncCustomBlock() {
  els.customBlock.hidden = $("provider").value !== "custom";
}

$("provider").addEventListener("change", syncCustomBlock);

function renderStatus(data) {
  if (data && data.link) amoLinkRender(data.link);
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

async function loadModels(keepValue) {
  els.modelHint.textContent = "拉取中…";
  try {
    const result = unwrap(await get("models")) || {};
    const models = result.models || [];
    if (!models.length) {
      els.modelHint.textContent = result.message || result.msg || "没拉到模型，检查 api_base / key";
      els.modelManual.hidden = false;
      return;
    }
    const current = keepValue || $("model").value;
    $("model").innerHTML = models.map((name) =>
      '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + "</option>").join("");
    if (current && models.includes(current)) $("model").value = current;
    // 后端可能只返回聊天模型；图片模型不在列表里时允许手填一次
    const hasImage = models.some((name) => /image|dall|flux|kolors|sd|draw|wanx|cogview/i.test(name));
    els.modelManual.hidden = hasImage;
    els.modelHint.textContent = hasImage
      ? "共 " + models.length + " 个模型"
      : "共 " + models.length + " 个模型，没看到图片模型；可在下面手填";
  } catch (err) {
    els.modelHint.textContent = "拉取失败：" + String(err.message || err);
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
  loadModels();
});

async function loadConfig() {
  try {
    const result = unwrap(await get("config"));
    const data = result || {};
    TEXT_FIELDS.forEach((key) => {
      const el = $(key);
      if (!el || data[key] == null) return;
      if (key === "model" && el.tagName === "SELECT") {
        const value = String(data[key]);
        if (value && !Array.from(el.options).some((opt) => opt.value === value)) {
          el.appendChild(new Option(value, value));
        }
        el.value = value;
        return;
      }
      el.value = String(data[key]);
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
    $("modelManual").value = "";
    $("modelManual").hidden = true;
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
  payload.model = $("model").value;
  payload.provider = $("provider").value;
  payload.character_gender = $("character_gender").value;
  payload.camera_mode = $("camera_mode").value;
  payload.character_outfits = $("character_outfits").value
    .split("\n").map((line) => line.trim()).filter(Boolean);
  const manual = $("modelManual").value.trim();
  if (manual && !$("modelManual").hidden) payload.model = manual;
  const key = $("api_key").value.trim();
  if (key) payload.api_key = key;
  return payload;
}

els.fetchModels.addEventListener("click", () => loadModels());

els.save.addEventListener("click", async () => {
  if (!$("model").value) {
    els.saveHint.textContent = "先拉取模型并选一个";
    return;
  }
  els.save.disabled = true;
  els.saveHint.textContent = "保存中…";
  try {
    const saved = unwrap(await post("config/save", collectConfig())) || {};
    if (saved.saved !== undefined || saved.applied) {
      els.saveHint.textContent = saved.saved ? "已保存并写回配置" : "已生效（当前配置对象不支持落盘）";
      $("api_key").value = "";
    $("modelManual").value = "";
    $("modelManual").hidden = true;
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

let scenes = [];
let selectedScenes = new Set();

function renderScenes() {
  if (!els.wScenes) return;
  els.wScenes.innerHTML = scenes.map((scene) =>
    '<button type="button" class="chip' + (selectedScenes.has(scene.key) ? " on" : "") +
    '" data-scene="' + scene.key + '">' + escapeHtml(scene.label) + "</button>").join("");
  els.wScenes.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const key = chip.dataset.scene;
      if (selectedScenes.has(key)) selectedScenes.delete(key);
      else selectedScenes.add(key);
      renderScenes();
    });
  });
}

function renderWardrobe(data) {
  scenes = data.scenes || scenes;
  if (!scenes.length) return;
  renderScenes();
  els.wearNow.textContent = data.current || "待定";
  els.wearNow.className = "pill" + (data.current ? "" : " off");
  const items = data.items || [];
  els.wardrobeMeta.textContent = "库里 " + items.length + " 件 · 写入 " + (data.path || "");
  if (!items.length) {
    els.wList.innerHTML = '<li class="empty">还没有衣服，先加一件</li>';
    return;
  }
  els.wList.innerHTML = items.map((item) => {
    const tags = (item.scenes || []).map((key) => {
      const found = scenes.find((scene) => scene.key === key);
      return found ? found.label : key;
    }).join(" / ");
    const off = item.enabled === false ? " · 已停用" : "";
    return '<li data-id="' + escapeHtml(item.id) + '">' +
      '<span class="p">' + escapeHtml(item.name) + "</span>" +
      '<span class="t">' + escapeHtml(tags + off) + "</span>" +
      '<button class="link wear" type="button">今天穿</button>' +
      '<button class="link del" type="button">删</button></li>';
  }).join("");
  els.wList.querySelectorAll("li[data-id]").forEach((row) => {
    const id = row.dataset.id;
    const wear = row.querySelector(".wear");
    const del = row.querySelector(".del");
    if (wear) wear.addEventListener("click", async () => {
      const name = row.querySelector(".p").textContent;
      els.wardrobeMeta.textContent = "换装中…";
      try {
        const result = unwrap(await post("wardrobe/wear", { name })) || {};
        els.wardrobeMeta.textContent = result.msg || "换好了";
        await loadWardrobe();
      } catch (err) {
        els.wardrobeMeta.textContent = "换装失败：" + String(err.message || err);
      }
    });
    if (del) del.addEventListener("click", async () => {
      try {
        await post("wardrobe/delete", { id });
        await loadWardrobe();
      } catch (err) {
        els.wardrobeMeta.textContent = "删除失败：" + String(err.message || err);
      }
    });
  });
}

async function loadWardrobe() {
  try {
    const data = unwrap(await get("wardrobe")) || {};
    renderWardrobe(data);
  } catch (err) {
    els.wardrobeMeta.textContent = "读取失败：" + String(err.message || err);
  }
}

/* 添加衣服入口已下线：衣服从市场买 */

els.wRefresh.addEventListener("click", loadWardrobe);

els.refresh.addEventListener("click", refresh);

waitForBridge(() => {
  loadPresets();
  refresh();
});
setInterval(refresh, 15000);

/* ---- 连接状态：我这个挂载插件自己有没有连上终端/记忆库 ---- */
function amoLinkDot(ok) { return '<span style="color:' + (ok ? "#2e9e5b" : "#d83b3b") + '">\u25cf</span>'; }
function amoLinkRender(d) {
  const box = document.getElementById("linkbar");
  if (!box) return;
  box.onclick = null;
  if (!d) {
    // 失败要能自己说明白、能点重试，别只留一句「读不到」
    box.innerHTML = '连接状态：读取失败（health 接口没响应，<span style="text-decoration:underline;cursor:pointer">点这里重试</span>）';
    box.onclick = () => amoLinkLoad();
    return;
  }
  const hb = (typeof d.last_heartbeat === "number" && d.last_heartbeat >= 0)
    ? ("心跳 " + d.last_heartbeat + " 秒前") : "从未连上过";
  box.innerHTML = [
    amoLinkDot(d.terminal_connected) + " 终端" + (d.terminal_connected ? " v" + (d.terminal_version || "?") : " 未连接"),
    amoLinkDot(d.data_connected) + " 记忆库" + (d.data_connected ? " v" + (d.data_version || "?") : " 未连接"),
    "bot_id " + (d.bot_id || "\u2014"),
    hb
  ].join(' <span style="opacity:.35">\uff5c</span> ');
  box.title = d.error || "";
}

async function amoLinkFetch() {
  try { return unwrap(await get("health")); } catch (e) { return null; }
}
async function amoLinkLoad() {
  // 面板脚本比 AstrBot 的桥接先跑，头几次拿不到数据是正常的：
  // 等桥接就绪再重试，别一上来就把「读不到」画给人看。
  const box = document.getElementById("linkbar");
  if (box && /检查中|读取失败/.test(box.textContent || "")) box.textContent = "连接状态：读取中…";
  for (let i = 0; i < 20; i += 1) {
    const d = await amoLinkFetch();
    if (d) { amoLinkRender(d); return; }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  amoLinkRender(null);
}

amoLinkLoad();
setInterval(amoLinkLoad, 30000);
