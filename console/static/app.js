const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

const state = {
  feeds: [],
  cat: "all",
  q: "",
  draft: null,
};

function toast(msg, ok = true) {
  const el = $("#banner");
  el.textContent = msg;
  el.classList.toggle("hidden", !msg);
  el.style.borderColor = ok ? "var(--red)" : "var(--red)";
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const detail = data && (data.detail || data.message);
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
  }
  return data;
}

function originLabel(origin) {
  return { builtin: "内置", overlay: "自加", wechat: "微信" }[origin] || origin;
}

function runBadge(feed) {
  if (!feed.enabled) return `<span class="badge off">已停用</span>`;
  const run = feed.last_run || {};
  if (!run.status) return `<span class="badge">尚未采集</span>`;
  const extra =
    run.status === "ok"
      ? ` new ${run.new_count ?? 0}`
      : run.error
        ? ` ${escapeHtml(String(run.error).slice(0, 48))}`
        : "";
  return `<span class="badge ${run.status}">${escapeHtml(run.status)}${extra}</span>`;
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function encodeName(name) {
  return encodeURIComponent(name);
}

function filtered() {
  return state.feeds.filter((f) => {
    if (state.cat !== "all" && f.category !== state.cat) return false;
    if (!state.q) return true;
    const blob = `${f.name} ${f.url} ${f.source}`.toLowerCase();
    return blob.includes(state.q.toLowerCase());
  });
}

function renderRows() {
  const tbody = $("#rows");
  const rows = filtered();
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="hint">没有匹配的订阅。</td></tr>`;
    return;
  }
  tbody.innerHTML = rows
    .map(
      (f) => `
      <tr class="${f.enabled ? "" : "row-off"}" data-name="${escapeHtml(f.name)}">
        <td>
          <div class="name">${escapeHtml(f.name)}<span class="origin">${originLabel(f.origin)}</span></div>
          <div class="url">${escapeHtml(f.url)}</div>
        </td>
        <td>${escapeHtml(f.source)} / ${escapeHtml(f.kind)}</td>
        <td>${f.weight ?? "—"}</td>
        <td>${runBadge(f)}</td>
        <td>
          <div class="acts">
            <button type="button" data-act="edit">改</button>
            <button type="button" data-act="collect" ${f.enabled ? "" : "disabled"}>采集</button>
            <button type="button" data-act="toggle">${f.enabled ? "停用" : "启用"}</button>
            ${f.origin === "builtin" ? "" : `<button type="button" class="danger" data-act="del">删除</button>`}
          </div>
        </td>
      </tr>`,
    )
    .join("");
}

function renderDraft(d) {
  const box = $("#draft");
  if (!d) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  const warn = d.warning
    ? `<p class="hint">${escapeHtml(d.warning)}。也可以改贴 WeWe 的 feed URL。</p>`
    : "";
  box.innerHTML = `
    <h3>识别为 ${escapeHtml(d.type)}</h3>
    ${warn}
    <div class="grid">
      <label>名称 <input id="d-name" value="${escapeHtml(d.name)}" /></label>
      <label>权重 <input id="d-weight" type="number" step="0.1" value="${d.weight ?? 2}" /></label>
      <label>URL <input id="d-url" value="${escapeHtml(d.url || "")}" /></label>
      <label>标题过滤 <input id="d-regex" value="${escapeHtml(d.title_regex || "")}" placeholder="可选" /></label>
    </div>
    <p class="url">${escapeHtml(d.source)} · ${escapeHtml(d.kind)}${d.needs_wewe ? " · 将请求 WeWe RSS" : ""}</p>
    <div class="draft-actions">
      <button type="button" class="ink" id="btn-add">添加</button>
      <button type="button" class="ghost" id="btn-draft-cancel">取消</button>
    </div>
  `;
  $("#btn-add").onclick = addDraft;
  $("#btn-draft-cancel").onclick = () => {
    state.draft = null;
    renderDraft(null);
  };
}

async function loadFeeds() {
  const data = await api("/api/feeds");
  state.feeds = data.feeds || [];
  renderRows();
}

async function detect() {
  const url = $("#paste").value.trim();
  if (!url) return;
  toast("正在识别…");
  try {
    const draft = await api("/api/feeds/detect", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    state.draft = draft;
    renderDraft(draft);
    toast("");
  } catch (e) {
    toast(e.message, false);
  }
}

async function addDraft() {
  const d = state.draft;
  if (!d) return;
  const body = {
    ...d,
    name: $("#d-name").value.trim(),
    url: $("#d-url").value.trim(),
    weight: Number($("#d-weight").value || d.weight || 2),
    title_regex: $("#d-regex").value.trim() || null,
  };
  toast("正在写入…");
  try {
    await api("/api/feeds", { method: "POST", body: JSON.stringify(body) });
    state.draft = null;
    renderDraft(null);
    $("#paste").value = "";
    toast("已写入配置。立即采集可用行内按钮；定时轮询需重启 serve。");
    await loadFeeds();
  } catch (e) {
    toast(e.message, false);
  }
}

function openEdit(feed) {
  const dlg = $("#edit-dialog");
  const form = $("#edit-form");
  form.original.value = feed.name;
  form.name.value = feed.name;
  form.url.value = feed.url;
  form.source.value = feed.source;
  form.kind.value = feed.kind;
  form.weight.value = feed.weight ?? "";
  form.title_regex.value = feed.title_regex || "";
  form.interval_minutes.value = feed.interval_minutes || "";
  dlg.showModal();
}

async function saveEdit(ev) {
  ev.preventDefault();
  const form = $("#edit-form");
  const original = form.original.value;
  const title = form.title_regex.value.trim();
  const body = {
    name: form.name.value.trim(),
    url: form.url.value.trim(),
    source: form.source.value.trim(),
    kind: form.kind.value.trim(),
    weight: form.weight.value === "" ? null : Number(form.weight.value),
    interval_minutes:
      form.interval_minutes.value === "" ? null : Number(form.interval_minutes.value),
    title_regex: title || null,
    clear_title_regex: !title,
  };
  try {
    await api(`/api/feeds/${encodeName(original)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    $("#edit-dialog").close();
    toast("已保存");
    await loadFeeds();
  } catch (e) {
    toast(e.message, false);
  }
}

async function onRowClick(ev) {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const tr = btn.closest("tr");
  const name = tr.dataset.name;
  const feed = state.feeds.find((f) => f.name === name);
  if (!feed) return;
  const act = btn.dataset.act;
  try {
    if (act === "edit") {
      openEdit(feed);
      return;
    }
    if (act === "collect") {
      toast(`正在采集 ${name}…`);
      const r = await api(`/api/feeds/${encodeName(name)}/collect`, { method: "POST" });
      toast(`[${r.collector}] new=${r.new} dup=${r.dup}`);
      await loadFeeds();
      return;
    }
    if (act === "toggle") {
      await api(`/api/feeds/${encodeName(name)}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !feed.enabled }),
      });
      await loadFeeds();
      return;
    }
    if (act === "del") {
      const verb = feed.origin === "builtin" ? "停用内置源" : "删除";
      if (!confirm(`${verb}「${name}」？`)) return;
      await api(`/api/feeds/${encodeName(name)}`, { method: "DELETE" });
      toast("已更新配置");
      await loadFeeds();
    }
  } catch (e) {
    toast(e.message, false);
  }
}

async function openWewe() {
  const dlg = $("#wewe-dialog");
  const box = $("#wewe-list");
  box.textContent = "读取 WeWe…";
  dlg.showModal();
  try {
    const data = await api("/api/wewe/feeds");
    const items = data.feeds || [];
    if (!items.length) {
      box.innerHTML = `<p class="hint">WeWe 里还没有公众号。先在 :4000 登录微信读书，或在上方粘贴文章链接。</p>`;
      return;
    }
    box.innerHTML = items
      .map(
        (it) => `
        <label class="wewe-item">
          <input type="checkbox" value="${escapeHtml(it.id)}" ${it.in_fishnet ? "disabled" : "checked"} />
          <span>${escapeHtml(it.name)}</span>
          <span class="origin">${it.in_fishnet ? "已接入" : it.id}</span>
        </label>`,
      )
      .join("");
  } catch (e) {
    box.innerHTML = `<p class="hint">${escapeHtml(e.message)}</p>`;
  }
}

async function importWewe() {
  const ids = $$("#wewe-list input[type=checkbox]:checked:not(:disabled)").map(
    (el) => el.value,
  );
  try {
    const r = await api("/api/wewe/import", {
      method: "POST",
      body: JSON.stringify({ ids }),
    });
    toast(`导入 ${r.added.length} 个，跳过 ${r.skipped.length} 个`);
    $("#wewe-dialog").close();
    await loadFeeds();
  } catch (e) {
    toast(e.message, false);
  }
}

function bind() {
  $("#btn-detect").onclick = detect;
  $("#paste").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      detect();
    }
  });
  $("#rows").addEventListener("click", onRowClick);
  $$(".chip").forEach((btn) => {
    btn.onclick = () => {
      $$(".chip").forEach((b) => b.classList.remove("on"));
      btn.classList.add("on");
      state.cat = btn.dataset.cat;
      renderRows();
    };
  });
  $("#q").addEventListener("input", (e) => {
    state.q = e.target.value;
    renderRows();
  });
  $("#btn-import").onclick = openWewe;
  $("#wewe-close").onclick = () => $("#wewe-dialog").close();
  $("#wewe-go").onclick = importWewe;
  $("#edit-form").addEventListener("submit", saveEdit);
  $("#edit-cancel").onclick = () => $("#edit-dialog").close();
}

async function boot() {
  bind();
  try {
    const meta = await api("/api/meta");
    $("#meta-hint").textContent = meta.hint || "";
  } catch {
    $("#meta-hint").textContent = "";
  }
  try {
    await loadFeeds();
  } catch (e) {
    toast(e.message, false);
  }
}

boot();
