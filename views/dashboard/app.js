const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;

const $ = (id) => document.getElementById(id);
const els = {
  theme: $("theme"),
  enablePill: $("enablePill"),
  apiBase: $("apiBase"),
  model: $("model"),
  size: $("size"),
  keyMasked: $("keyMasked"),
  usedToday: $("usedToday"),
  callsTotal: $("callsTotal"),
  statusMeta: $("statusMeta"),
  prompt: $("prompt"),
  draw: $("draw"),
  drawHint: $("drawHint"),
  preview: $("preview"),
  previewImg: $("previewImg"),
  previewMeta: $("previewMeta"),
  history: $("history"),
  refresh: $("refresh"),
};

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

async function get(path, params) {
  if (!bridge) throw new Error("没有检测到 AstrBot 面板桥接，请在 AstrBot WebUI 里打开这个页面");
  return bridge.apiGet(path, params || {});
}

async function post(path, body) {
  if (!bridge) throw new Error("没有检测到 AstrBot 面板桥接，请在 AstrBot WebUI 里打开这个页面");
  return bridge.apiPost(path, body || {});
}

function renderStatus(data) {
  const on = !!data.enabled;
  els.enablePill.textContent = on ? "已启用" : "已关闭";
  els.enablePill.className = "pill" + (on ? "" : " off");
  els.apiBase.textContent = data.api_base || "未配置";
  els.model.textContent = data.model || "—";
  els.size.textContent = data.size || "—";
  els.keyMasked.textContent = data.api_key_masked || "未配置";
  els.usedToday.textContent = String(data.users_today ?? 0);
  els.callsTotal.textContent = String(data.calls_total ?? 0);
  els.statusMeta.textContent = [
    "私聊" + (data.allow_private ? "开" : "关"),
    "群聊" + (data.allow_group ? "开" : "关"),
    "冷却 " + (data.cooldown_seconds ?? 0) + "s",
    "日限 " + ((data.daily_limit ?? 0) || "不限"),
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
    if (status && status.data) renderStatus(status.data);
    if (history && history.data) renderHistory(history.data.items);
  } catch (err) {
    els.statusMeta.textContent = String(err.message || err);
  }
}

els.draw.addEventListener("click", async () => {
  const prompt = (els.prompt.value || "").trim();
  if (!prompt) { els.drawHint.textContent = "先写点什么"; return; }
  els.draw.disabled = true;
  els.drawHint.textContent = "画着呢…";
  els.preview.hidden = true;
  try {
    const result = await post("test", { prompt });
    if (result && result.data && result.data.image) {
      els.previewImg.src = result.data.image;
      els.previewMeta.textContent = fmtBytes(result.data.bytes) + " · " + result.data.ms + "ms";
      els.preview.hidden = false;
      els.drawHint.textContent = "画好了";
    } else {
      els.drawHint.textContent = (result && (result.message || result.error)) || "没画出来";
    }
  } catch (err) {
    els.drawHint.textContent = String(err.message || err);
  } finally {
    els.draw.disabled = false;
    refresh();
  }
});

els.refresh.addEventListener("click", refresh);

refresh();
setInterval(refresh, 15000);
