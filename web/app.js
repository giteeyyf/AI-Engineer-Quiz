/* Keepstack single-page client. Vanilla JS, no build step. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const state = { token: localStorage.getItem("keepstack_token"), user: null,
  q: "", mode: "keyword", media: null, collection: null, collectionName: null,
  includeTags: [], excludeTags: [], allTags: [], sort: "newest",
  page: 1, pageSize: 12, total: 0, pages: 0,
  reqSeq: 0, controller: null, recovering: false };

// ---------- API helper ----------
async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  if (opts.json) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.json);
    // fetch defaults to GET, and GET cannot carry a body. JSON calls without an
    // explicit method (login, new collection) are writes, so default to POST.
    opts.method = opts.method || "POST";
  }
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) { logout(); throw new Error("unauthorised"); }
  if (!res.ok) { const t = await res.text(); throw new Error(t || res.statusText); }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res;
}

function toast(msg, kind = "ok") {
  const t = $("#toast"); t.textContent = msg; t.className = "toast " + kind;
  setTimeout(() => t.classList.add("hidden"), 2600);
}
const fmtBytes = (b) => { if (!b) return "0 B"; const u = ["B","KB","MB","GB","TB"]; const i = Math.floor(Math.log(b)/Math.log(1024)); return (b/Math.pow(1024,i)).toFixed(i?1:0)+" "+u[i]; };
const esc = (s) => (s ?? "").toString().replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

// ---------- auth ----------
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await api("/api/auth/login", { json: { username: $("#login-user").value, password: $("#login-pass").value } });
    state.token = r.token; localStorage.setItem("keepstack_token", r.token);
    state.user = r.user; enterApp();
  } catch { $("#login-error").textContent = "Invalid credentials"; $("#login-error").classList.remove("hidden"); }
});
$("#btn-logout").addEventListener("click", logout);
function logout() { localStorage.removeItem("keepstack_token"); state.token = null; state.user = null;
  $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); }

function enterApp() {
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  $("#user-label").textContent = `${state.user.username} (${state.user.role})`;
  const isAdmin = state.user.role === "admin";
  $$(".admin-only").forEach(el => el.classList.toggle("hidden", !isAdmin));
  loadFilterOptions();
  loadLibrary();
}

// ---------- navigation ----------
$$(".nav-item").forEach(btn => btn.addEventListener("click", () => {
  $$(".nav-item").forEach(b => b.classList.remove("active")); btn.classList.add("active");
  const view = btn.dataset.view;
  $$(".view").forEach(v => v.classList.add("hidden"));
  $("#view-" + (view === "library" ? "library" : view)).classList.remove("hidden");
  if (view === "library") loadLibrary();
  if (view === "collections") loadCollections();
  if (view === "dashboard") loadDashboard();
  if (view === "users") loadUsers();
  if (view === "audit") loadAudit();
}));

// ---------- library / search ----------
let searchTimer;

$("#search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => setFilter({ q: e.target.value }), 250);
});
$("#sort").addEventListener("change", (e) => setFilter({ sort: e.target.value }));
$("#filter-collection").addEventListener("change", (e) => {
  setFilter({ collection: e.target.value || null });
});
$("#btn-clear-filters").addEventListener("click", clearFilters);
$("#pg-prev").addEventListener("click", () => { if (state.page > 1) { state.page -= 1; loadLibrary(); } });
$("#pg-next").addEventListener("click", () => { if (state.page < state.pages) { state.page += 1; loadLibrary(); } });
$("#btn-export").addEventListener("click", exportCsv);

// Facet and chip clicks are delegated once; their lists are re-rendered on
// every response, so per-render listeners would pile up.
$("#facet-media").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-media]");
  if (!li) return;
  setFilter({ media: state.media === li.dataset.media ? null : li.dataset.media });
});
$("#facet-tags").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-tag]");
  if (!li) return;
  if (e.target.closest("[data-exclude]")) toggleTag("exclude", li.dataset.tag);
  else toggleTag("include", li.dataset.tag);
});
$("#filter-chips").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-chip]");
  if (!btn) return;
  removeChip(btn.dataset.chip);
});

// Tag picker: one row per tag, two explicit checkboxes. Include and exclude
// are mutually exclusive per tag in the UI; the API still handles conflicts.
$("#tag-picker-toggle").addEventListener("click", (e) => {
  e.stopPropagation();
  const panel = $("#tag-picker");
  panel.classList.toggle("hidden");
  $("#tag-picker-toggle").setAttribute("aria-expanded", String(!panel.classList.contains("hidden")));
});
$("#tag-picker").addEventListener("click", (e) => e.stopPropagation());
document.addEventListener("click", () => {
  $("#tag-picker").classList.add("hidden");
  $("#tag-picker-toggle").setAttribute("aria-expanded", "false");
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  $("#tag-picker").classList.add("hidden");
  $("#tag-picker-toggle").setAttribute("aria-expanded", "false");
});
$("#tag-picker-done").addEventListener("click", () => {
  $("#tag-picker").classList.add("hidden");
  $("#tag-picker-toggle").setAttribute("aria-expanded", "false");
});
$("#tag-search").addEventListener("input", renderTagList);
$("#tag-picker-clear").addEventListener("click", () => {
  setFilter({ includeTags: [], excludeTags: [] });
  renderTagList();
});
$("#tag-list").addEventListener("change", (e) => {
  const box = e.target;
  if (!box.dataset || !box.dataset.mode) return;
  const name = box.closest("[data-tag]").dataset.tag;
  const include = state.includeTags.filter(t => t !== name);
  const exclude = state.excludeTags.filter(t => t !== name);
  if (box.checked && box.dataset.mode === "include") include.push(name);
  if (box.checked && box.dataset.mode === "exclude") exclude.push(name);
  setFilter({ includeTags: include, excludeTags: exclude });
  syncTagPicker();
});

// Every filter change goes through here so the page always resets to 1.
function setFilter(patch) {
  Object.assign(state, patch);
  state.page = 1;
  loadLibrary();
}

function clearFilters() {
  $("#search").value = "";
  $("#tag-search").value = "";
  $("#filter-collection").value = "";
  setFilter({ q: "", media: null, collection: null, includeTags: [], excludeTags: [] });
  renderTagList();
}

function renderTagList() {
  const term = ($("#tag-search").value || "").trim().toLowerCase();
  const rows = state.allTags.filter(t => !term || t.name.includes(term));
  $("#tag-list").innerHTML = rows.map(t => `
    <div class="tag-row-pick" data-tag="${esc(t.name)}">
      <span class="tag-name">${esc(t.name)}<span class="count">${t.count}</span></span>
      <input type="checkbox" data-mode="include" aria-label="Include ${esc(t.name)}"
             ${state.includeTags.includes(t.name) ? "checked" : ""}>
      <input type="checkbox" data-mode="exclude" aria-label="Exclude ${esc(t.name)}"
             ${state.excludeTags.includes(t.name) ? "checked" : ""}>
    </div>`).join("") || '<p class="muted tag-empty">No tags match this search.</p>';
  syncTagPickerLabel();
}

// Reflect sidebar clicks and chip removals into the picker without a reload.
function syncTagPicker() {
  $$("#tag-list .tag-row-pick").forEach(row => {
    const name = row.dataset.tag;
    row.querySelector('input[data-mode="include"]').checked = state.includeTags.includes(name);
    row.querySelector('input[data-mode="exclude"]').checked = state.excludeTags.includes(name);
  });
  syncTagPickerLabel();
}

function syncTagPickerLabel() {
  const parts = [];
  if (state.includeTags.length) parts.push(`${state.includeTags.length} include`);
  if (state.excludeTags.length) parts.push(`${state.excludeTags.length} exclude`);
  $("#tag-picker-toggle").textContent = parts.length ? `Tags: ${parts.join(", ")}` : "Tags: none";
}

function toggleTag(kind, name) {
  const key = kind === "include" ? "includeTags" : "excludeTags";
  const otherKey = kind === "include" ? "excludeTags" : "includeTags";
  const list = state[key].slice();
  const idx = list.indexOf(name);
  if (idx >= 0) list.splice(idx, 1);
  else list.push(name);
  const other = state[otherKey].filter(t => t !== name);
  setFilter({ [key]: list, [otherKey]: other });
}

function removeChip(key) {
  if (key === "q") { $("#search").value = ""; setFilter({ q: "" }); }
  else if (key === "media") setFilter({ media: null });
  else if (key === "collection") { $("#filter-collection").value = ""; setFilter({ collection: null }); }
  else if (key.startsWith("include:")) toggleTag("include", key.slice("include:".length));
  else if (key.startsWith("exclude:")) toggleTag("exclude", key.slice("exclude:".length));
}

function queryParams(forExport = false) {
  const p = new URLSearchParams();
  const q = state.q.trim();
  if (q) p.set("q", q);
  p.set("sort", state.sort);
  if (state.media) p.set("media_type", state.media);
  if (state.collection) p.set("collection_id", state.collection);
  state.includeTags.forEach(t => p.append("tags", t));
  state.excludeTags.forEach(t => p.append("exclude_tags", t));
  if (!forExport) {
    p.set("page", String(state.page));
    p.set("page_size", String(state.pageSize));
  }
  return p;
}

async function loadFilterOptions() {
  try {
    const [tags, cols] = await Promise.all([api("/api/tags"), api("/api/collections")]);
    state.allTags = (tags.tags || []).map(t => ({ name: t.name, count: t.c }));
    renderTagList();
    $("#filter-collection").innerHTML = '<option value="">All collections</option>' +
      (cols.collections || []).map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  } catch { /* filter options are optional; the library still loads */ }
}

async function loadLibrary() {
  const seq = ++state.reqSeq;
  state.controller?.abort();
  const controller = new AbortController();
  state.controller = controller;
  try {
    const data = await api("/api/assets?" + queryParams().toString(), { signal: controller.signal });
    if (seq !== state.reqSeq) return;   // a newer request already answered
    if (!data.items.length && data.total > 0 && data.page > 1 && !state.recovering) {
      // The last page shrank after an edit; step back instead of showing nothing.
      state.recovering = true;
      state.page = data.pages;
      return loadLibrary();
    }
    state.recovering = false;
    state.total = data.total;
    state.pages = data.pages;
    state.collectionName = data.applied?.collection_name || null;
    renderFacets(data.facets);
    renderGrid(data.items, data.total);
    renderChips(data.applied);
    renderPager();
    syncTagPicker();
    showError(null);
  } catch (err) {
    if (err.name === "AbortError" || seq !== state.reqSeq) return;
    showError(err.message || "加载失败，请重试");
  }
}

function hasFilters() {
  return !!(state.q.trim() || state.media || state.collection ||
            state.includeTags.length || state.excludeTags.length);
}

function renderGrid(items, total) {
  $("#result-title").textContent = `Results (${total})`;
  const empty = $("#empty");
  empty.classList.toggle("hidden", items.length > 0);
  empty.innerHTML = hasFilters()
    ? 'No assets match these filters. <button class="btn" id="empty-clear">Clear filters</button>'
    : 'No assets yet. Click Upload to get started.';
  $("#empty-clear")?.addEventListener("click", clearFilters);
  $("#grid").innerHTML = items.map(a => `
    <div class="card" data-uuid="${a.uuid}">
      <img class="thumb" loading="lazy" src="/api/assets/${a.uuid}/thumb" alt="${esc(a.alt_text || a.title)}">
      <div class="meta">
        <span class="title" title="${esc(a.title)}">${esc(a.title)}</span>
        <span class="sub"><span class="badge">${a.media_type}</span><span>${fmtBytes(a.size)}</span></span>
      </div>
    </div>`).join("");
  $$("#grid .card").forEach(c => c.addEventListener("click", () => openAsset(c.dataset.uuid)));
}

function renderFacets(facets) {
  $("#facet-media").innerHTML = (facets.media_types || []).map(m =>
    `<li class="${state.media === m.value ? "active" : ""}" data-media="${esc(m.value)}">
       <span>${esc(m.value)}</span><span class="count">${m.count}</span></li>`).join("");
  $("#facet-tags").innerHTML = (facets.tags || []).slice(0, 18).map(t => {
    const included = state.includeTags.includes(t.value);
    const excluded = state.excludeTags.includes(t.value);
    return `<li class="${included ? "active" : ""}${excluded ? " excluded" : ""}" data-tag="${esc(t.value)}">
      <span>${esc(t.value)}</span><span class="count">${t.count}</span>
      <button class="x-btn" data-exclude="${esc(t.value)}" title="Exclude this tag">&times;</button></li>`;
  }).join("");
}

function renderChips(applied) {
  const chips = [];
  if (!applied) applied = { include_tags: [], exclude_tags: [] };
  if (applied.q) chips.push(["q", `Keyword: ${applied.q}`]);
  if (applied.media_type) chips.push(["media", `Type: ${applied.media_type}`]);
  if (applied.collection_id) chips.push(["collection", `Collection: ${applied.collection_name || applied.collection_id}`]);
  (applied.include_tags || []).forEach(t => chips.push(["include:" + t, `+ ${t}`]));
  (applied.exclude_tags || []).forEach(t => chips.push(["exclude:" + t, `- ${t}`]));
  $("#filter-chips").innerHTML = chips.map(([key, label]) =>
    `<span class="chip ${key.startsWith("exclude:") ? "chip-exclude" : "chip-include"}">${esc(label)}
       <button class="chip-x" data-chip="${esc(key)}">&times;</button></span>`).join("");
  $("#filter-chips").classList.toggle("hidden", chips.length === 0);
}

function renderPager() {
  $("#pager").classList.toggle("hidden", state.pages <= 1);
  $("#pg-prev").disabled = state.page <= 1;
  $("#pg-next").disabled = state.page >= state.pages;
  $("#pg-info").textContent = state.total
    ? `Page ${state.page} / ${state.pages} · ${state.total} matching assets`
    : "0 matching assets";
}

function showError(message) {
  const el = $("#load-error");
  el.classList.toggle("hidden", !message);
  el.innerHTML = message
    ? `<span>${esc(message)}</span><button class="btn" id="btn-retry">Retry</button>` : "";
  if (message) $("#btn-retry").addEventListener("click", loadLibrary);
}

function filenameFromDisposition(value) {
  if (!value) return null;
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(value);
  if (utf8) { try { return decodeURIComponent(utf8[1]); } catch { /* fall through */ } }
  const plain = /filename="?([^";]+)"?/i.exec(value);
  return plain ? plain[1] : null;
}

async function exportCsv() {
  const btn = $("#btn-export");
  btn.disabled = true;
  try {
    const res = await fetch("/api/assets/export.csv?" + queryParams(true).toString(),
      { headers: { Authorization: "Bearer " + state.token } });
    if (res.status === 401) { logout(); throw new Error("session expired"); }
    if (!res.ok) throw new Error((await res.text()).slice(0, 200) || res.statusText);
    const blob = await res.blob();
    const name = filenameFromDisposition(res.headers.get("Content-Disposition")) || "keepstack-assets.csv";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`Exported ${state.total} assets`);
  } catch (err) {
    toast("Export failed: " + (err.message || "unknown error"), "err");
  } finally {
    btn.disabled = false;
  }
}

// ---------- asset detail drawer ----------
const drawer = $("#drawer");
$("#drawer-close").addEventListener("click", closeDrawer);
$(".drawer-backdrop").addEventListener("click", closeDrawer);
function closeDrawer() { drawer.classList.add("hidden"); document.body.classList.remove("modal-open"); }

async function openAsset(uuid) {
  drawer.classList.remove("hidden");
  document.body.classList.add("modal-open");
  $("#drawer-body").innerHTML = '<p class="muted">Loading...</p>';
  const [d, meta, sim] = await Promise.all([
    api("/api/assets/" + uuid),
    api("/api/assets/" + uuid + "/metadata"),
    api("/api/assets/" + uuid + "/similar"),
  ]);
  const a = d.asset;
  const canEdit = ["admin","editor","contributor"].includes(state.user.role);
  const preview = a.media_type === "image"
    ? `<img class="detail-preview" src="/api/assets/${uuid}/preview" alt="${esc(a.alt_text||a.title)}">`
    : `<img class="detail-preview" src="/api/assets/${uuid}/thumb" alt="${esc(a.title)}">`;
  const dc = a.dublin_core || {};
  $("#drawer-body").innerHTML = `
    ${preview}
    <h2 class="detail-title">${esc(a.title)}</h2>
    <p class="muted">${a.media_type} &middot; ${a.ext?.toUpperCase()} &middot; ${fmtBytes(a.size)} ${a.width?`&middot; ${a.width}&times;${a.height}`:""}</p>
    <div class="btn-row">
      <a class="btn" href="/api/assets/${uuid}/file" download>Download</a>
      ${canEdit?`<button class="btn" id="d-share">Create share link</button>`:""}
      ${["admin","editor"].includes(state.user.role)?`<button class="btn btn-danger" id="d-delete">Delete</button>`:""}
    </div>

    ${canEdit ? `<div class="detail-section"><h4>Edit metadata</h4>
      <div class="edit-grid">
        <label>Title<input id="e-title" value="${esc(a.title)}"></label>
        <label>Description<textarea id="e-description" rows="2">${esc(a.description)}</textarea></label>
        <label>Alt text (accessibility)<input id="e-alt_text" value="${esc(a.alt_text)}"></label>
        <label>Creator<input id="e-dc_creator" value="${esc(a.dc_creator)}"></label>
        <label>Rights / license<input id="e-rights_statement" value="${esc(a.rights_statement)}"></label>
        <label>Retention until<input id="e-retention_until" type="date" value="${esc((a.retention_until||'').slice(0,10))}"></label>
      </div>
      <div class="btn-row"><button class="btn btn-primary" id="d-save">Save changes</button></div></div>` : ""}

    <div class="detail-section"><h4>Tags</h4>
      <div class="tag-row" id="tag-row">
        ${d.tags.map(t => `<span class="tag ${t.source==='ai'?'ai':''}">${esc(t.name)}${canEdit?`<span class="rm" data-tag="${esc(t.name)}">&times;</span>`:""}</span>`).join("")}
        ${canEdit?`<input class="tag-add" id="tag-add" placeholder="+ add tag">`:""}
      </div></div>

    ${sim.items.length?`<div class="detail-section"><h4>Similar assets</h4>
      <div class="similar-strip">${sim.items.slice(0,8).map(s=>`<img src="/api/assets/${s.uuid}/thumb" data-uuid="${s.uuid}" title="${esc(s.title)}">`).join("")}</div></div>`:""}

    <div class="detail-section"><h4>Dublin Core</h4>
      ${Object.entries(dc).filter(([,v])=>v).map(([k,v])=>`<div class="field-row"><span class="k">${k}</span><span class="v">${esc(v)}</span></div>`).join("")}
      <div class="btn-row"><a class="btn btn-ghost" href="/oai?verb=GetRecord&metadataPrefix=oai_dc&identifier=oai::${a.uuid}" target="_blank">View OAI-PMH XML</a></div>
    </div>

    ${Object.keys(meta.exif||{}).length||Object.keys(meta.iptc||{}).length?`<div class="detail-section"><h4>Embedded metadata (EXIF / IPTC)</h4>
      <div class="kv-scroll">${[...Object.entries(meta.iptc||{}),...Object.entries(meta.exif||{})].slice(0,40).map(([k,v])=>`<div class="field-row"><span class="k">${esc(k)}</span><span class="v">${esc(typeof v==='object'?JSON.stringify(v):v)}</span></div>`).join("")}</div></div>`:""}

    ${d.versions.length?`<div class="detail-section"><h4>Versions</h4>${d.versions.map(v=>`<div class="field-row"><span class="k">v${v.version_no}</span><span class="v">${esc(v.note||'')} &middot; ${fmtBytes(v.size)} &middot; ${(v.created_at||'').slice(0,10)}</span></div>`).join("")}</div>`:""}
  `;

  // wire detail actions
  $$("#drawer-body .similar-strip img").forEach(img => img.addEventListener("click", () => openAsset(img.dataset.uuid)));
  if (canEdit) {
    $("#d-save")?.addEventListener("click", async () => {
      const body = {};
      ["title","description","alt_text","dc_creator","rights_statement","retention_until"].forEach(f => { const el = $("#e-"+f); if (el) body[f] = el.value; });
      await api("/api/assets/"+uuid, { method: "PATCH", json: body });
      toast("Saved"); openAsset(uuid); loadLibrary();
    });
    $("#tag-add")?.addEventListener("keydown", async (e) => {
      if (e.key === "Enter" && e.target.value.trim()) {
        await api("/api/assets/"+uuid+"/tags", { json: { name: e.target.value.trim() } });
        openAsset(uuid);
      }
    });
    $$("#tag-row .rm").forEach(rm => rm.addEventListener("click", async () => {
      await api("/api/assets/"+uuid+"/tags/"+encodeURIComponent(rm.dataset.tag), { method: "DELETE" });
      openAsset(uuid);
    }));
    $("#d-share")?.addEventListener("click", async () => {
      const r = await api("/api/shares", { json: { target_type: "asset", target_id: a.id, permission: "download" } });
      await navigator.clipboard?.writeText(r.url).catch(()=>{});
      toast("Share link copied: " + r.url);
    });
  }
  $("#d-delete")?.addEventListener("click", async () => {
    if (!confirm("Delete this asset?")) return;
    await api("/api/assets/"+uuid, { method: "DELETE" });
    toast("Deleted"); closeDrawer(); loadLibrary();
  });
}

// ---------- upload ----------
const uploadModal = $("#upload-modal");
$("#btn-upload").addEventListener("click", () => { uploadModal.classList.remove("hidden"); document.body.classList.add("modal-open"); });
$("#upload-cancel").addEventListener("click", () => { uploadModal.classList.add("hidden"); document.body.classList.remove("modal-open"); loadLibrary(); });
$(".modal-backdrop").addEventListener("click", () => { uploadModal.classList.add("hidden"); document.body.classList.remove("modal-open"); });
const dz = $("#dropzone"), fileInput = $("#file-input");
dz.addEventListener("click", () => fileInput.click());
dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("drag"); });
dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
dz.addEventListener("drop", e => { e.preventDefault(); dz.classList.remove("drag"); uploadFiles(e.dataTransfer.files); });
fileInput.addEventListener("change", () => uploadFiles(fileInput.files));

async function uploadFiles(files) {
  const list = $("#upload-list");
  for (const file of files) {
    const row = document.createElement("div");
    row.className = "upload-item"; row.innerHTML = `<span>${esc(file.name)}</span><span>uploading...</span>`;
    list.prepend(row);
    const fd = new FormData(); fd.append("files", file); fd.append("run_ai", $("#ai-toggle").checked);
    try {
      const r = await api("/api/assets", { method: "POST", body: fd });
      const created = r.created[0];
      row.classList.add(created.duplicate ? "dup" : "ok");
      row.lastChild.textContent = created.duplicate ? "duplicate (deduped)" : "done";
    } catch (err) { row.classList.add("err"); row.lastChild.textContent = "failed"; }
  }
}

// ---------- collections ----------
async function loadCollections() {
  const data = await api("/api/collections");
  $("#collection-list").innerHTML = data.collections.map(c =>
    `<div class="collection-card" data-id="${c.id}"><span class="n">${esc(c.name)}</span><span class="muted">${c.asset_count} assets</span><span class="muted">${esc(c.description||"")}</span></div>`).join("")
    || '<p class="muted">No collections yet.</p>';
  $$("#collection-list .collection-card").forEach(card => card.addEventListener("click", () => {
    $("#search").value = "";
    $("#tag-search").value = "";
    $$(".nav-item").forEach(b => b.classList.remove("active"));
    $('.nav-item[data-view="library"]').classList.add("active");
    $$(".view").forEach(v => v.classList.add("hidden")); $("#view-library").classList.remove("hidden");
    $("#filter-collection").value = card.dataset.id;
    setFilter({ collection: card.dataset.id, media: null, q: "", includeTags: [], excludeTags: [] });
  }));
}
$("#btn-new-collection").addEventListener("click", async () => {
  const name = prompt("Collection name"); if (!name) return;
  await api("/api/collections", { json: { name } }); loadCollections();
});

// ---------- dashboard ----------
async function loadDashboard() {
  const s = await api("/api/stats");
  const cards = [
    ["Total assets", s.total_assets], ["Storage used", fmtBytes(s.total_bytes)],
    ["Saved by dedup", fmtBytes(s.dedup_saved_bytes)], ["Tags", s.tags], ["Collections", s.collections],
    ...Object.entries(s.by_type).map(([k,v]) => [k.charAt(0).toUpperCase()+k.slice(1), v]),
  ];
  $("#stat-cards").innerHTML = cards.map(([l,n]) => `<div class="stat-card"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
}
$("#btn-fixity").addEventListener("click", async () => {
  $("#fixity-result").textContent = "Checking...";
  const r = await api("/api/admin/fixity", { method: "POST" });
  $("#fixity-result").textContent = r.ok ? `All ${r.checked} blobs verified. Integrity intact.` : `${r.failures.length} of ${r.checked} failed fixity.`;
});

// ---------- users ----------
async function loadUsers() {
  const data = await api("/api/users");
  $("#user-rows").innerHTML = data.users.map(u =>
    `<tr><td>${esc(u.username)}</td><td>${esc(u.email||"")}</td><td>${u.role}</td><td>${u.is_active?"Yes":"No"}</td><td>${(u.last_login||"").slice(0,16).replace("T"," ")}</td></tr>`).join("");
}
$("#btn-new-user").addEventListener("click", async () => {
  const username = prompt("Username"); if (!username) return;
  const password = prompt("Temporary password"); if (!password) return;
  const role = prompt("Role (admin/editor/contributor/viewer)", "contributor") || "viewer";
  await api("/api/users", { json: { username, password, role } }); loadUsers();
});

// ---------- audit ----------
async function loadAudit() {
  const data = await api("/api/audit");
  $("#audit-rows").innerHTML = data.entries.map(e =>
    `<tr><td>${(e.ts||"").slice(0,19).replace("T"," ")}</td><td>${esc(e.username||"-")}</td><td>${esc(e.action)}</td>
     <td>${esc(e.target_type||"")} ${esc((e.target_id||"").slice(0,8))}</td><td>${esc(e.detail||"")}</td><td>${esc(e.ip||"")}</td></tr>`).join("");
}

// ---------- boot ----------
(async function boot() {
  if (state.token) {
    try { state.user = await api("/api/auth/me"); enterApp(); return; } catch {}
  }
  $("#login").classList.remove("hidden");
})();
