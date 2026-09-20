(function () {
  // Director Studio, Task 21 - the Elements library as a page: browse by type, create, edit, delete.
  // The picker modal on the Generator page is untouched; this page talks to the same /api/elements routes
  // plus the record routes (/api/elements/record/<folder>/<id>).
  const TYPES = window.__ELEMENT_TYPES__ || {};
  const CATEGORIES = window.__ELEMENT_CATEGORIES__ || {};
  const TYPE_BY_SLUG = { characters: "talent", locations: "environment", props: "prop" };
  const FOLDER_BY_TYPE = Object.fromEntries(Object.entries(TYPES).map(([key, cfg]) => [key, cfg.folder]));
  const PER_PAGE = 60;
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pretty = (value) => String(value || "").replace(/_/g, " ");

  let cat = "all";            // all | favorites | characters | locations | props
  let page = 1;
  let query = "";
  let items = [];
  let categories = [];
  let vocab = null;
  let selected = null;        // { folder, id } of the open record
  let draft = null;           // create mode: { type, image: {data, mime_type, name, previewUrl}, metadata }
  let searchTimer = null;

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...options, body: options.body !== undefined ? JSON.stringify(options.body) : undefined });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload || payload.ok === false) throw new Error((payload && payload.error) || `Request failed (${response.status})`);
    return payload;
  }

  function ensureVocab() {
    if (vocab) return Promise.resolve(vocab);
    return api("/api/elements/vocabularies").then((payload) => { vocab = payload.vocabularies || {}; return vocab; }).catch(() => { vocab = {}; return vocab; });
  }

  function typeForCat() { return TYPE_BY_SLUG[cat] || "talent"; }

  // ---- list ----------------------------------------------------------------
  async function loadList() {
    $("elpLoading").style.display = "flex";
    $("elpGrid").style.display = "none";
    $("elpEmpty").style.display = "none";
    $("elpPagination").style.display = "none";
    const catParam = (cat === "all" || cat === "favorites") ? "all" : cat;
    const perPage = cat === "favorites" ? 500 : PER_PAGE;
    try {
      const data = await api(`/api/elements?category=${encodeURIComponent(catParam)}&q=${encodeURIComponent(query)}&page=${page}&per_page=${perPage}`);
      categories = data.categories || [];
      items = (data.items || []).filter((item) => cat !== "favorites" || item.is_favorite);
      renderCats();
      $("elpLoading").style.display = "none";
      $("elpCount").textContent = cat === "favorites" ? `${items.length} pinned` : `${data.total || 0} element${data.total === 1 ? "" : "s"}`;
      if (!items.length) { $("elpEmpty").style.display = "block"; $("elpGrid").style.display = "none"; return; }
      renderGrid();
      if (cat !== "favorites" && (data.pages || 1) > 1) {
        $("elpPagination").style.display = "flex";
        $("elpPageInfo").textContent = `${data.page} / ${data.pages}`;
        $("elpPrevBtn").disabled = data.page <= 1;
        $("elpNextBtn").disabled = data.page >= data.pages;
      }
    } catch (error) {
      $("elpLoading").style.display = "none";
      $("elpEmpty").style.display = "block";
      $("elpEmpty").innerHTML = `<p>${esc(error.message || "Could not load the library.")}</p>`;
    }
  }

  function renderCats() {
    const host = $("elpCats");
    const fixed = [["all", "&#9679;", "All"], ["favorites", "&#9733;", "Pinned"]];
    host.innerHTML = fixed.map(([slug, icon, label]) => `<button type="button" class="el-cat-btn${cat === slug ? " active" : ""}" data-cat="${slug}"><span class="el-cat-icon">${icon}</span> ${label}</button>`).join("")
      + '<div class="el-cat-separator"></div>'
      + categories.map((c) => `<button type="button" class="el-cat-btn${cat === c.slug ? " active" : ""}" data-cat="${esc(c.slug)}"><span class="el-cat-icon">${c.icon}</span> ${esc(c.label)}</button>`).join("");
    host.querySelectorAll("[data-cat]").forEach((button) => button.addEventListener("click", () => { cat = button.dataset.cat; page = 1; loadList(); }));
    $("elpNewBtn").textContent = `+ New ${TYPES[typeForCat()] ? TYPES[typeForCat()].label.toLowerCase() : "element"}`;
  }

  function renderGrid() {
    const grid = $("elpGrid");
    grid.style.display = "grid";
    grid.innerHTML = items.map((asset) => `
      <div class="el-card${selected && selected.id === asset.id && selected.folder === asset.folder ? " selected" : ""}" data-id="${esc(asset.id)}" data-folder="${esc(asset.folder)}" title="${esc(asset.name)}">
        <div class="el-card-img"><img src="${esc(asset.img_url)}" alt="${esc(asset.name)}" loading="lazy"></div>
        <button type="button" class="el-card-star${asset.is_favorite ? " starred" : ""}" data-star="${esc(asset.id)}" title="${asset.is_favorite ? "Unpin" : "Pin"}">&#9733;</button>
        <div class="el-card-name">${esc(asset.name)}<span class="elp-card-type">${esc(asset.cat_label || asset.type || "")}</span></div>
      </div>`).join("");
    grid.querySelectorAll(".el-card").forEach((card) => card.addEventListener("click", (event) => {
      if (event.target.closest("[data-star]")) return;
      openRecord(card.dataset.folder, card.dataset.id);
    }));
    grid.querySelectorAll("[data-star]").forEach((star) => star.addEventListener("click", async (event) => {
      event.stopPropagation();
      const asset = items.find((item) => item.id === star.dataset.star);
      if (!asset) return;
      const next = !asset.is_favorite;
      try {
        await fetch("/api/elements/toggle-favorite", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: asset.id, folder: asset.folder, favorite: next }) });
        asset.is_favorite = next;
        star.classList.toggle("starred", next);
        star.title = next ? "Unpin" : "Pin";
        if (selected && selected.id === asset.id) openRecord(asset.folder, asset.id, { keepScroll: true });
      } catch (error) { setDetailStatus(error.message || "Could not change the pin.", "error"); }
    }));
  }

  // ---- detail: view / edit -------------------------------------------------
  function setDetailStatus(text, tone = "") {
    const el = $("elpStatus");
    if (!el) return;
    el.textContent = text || "";
    el.className = "ds-shot-status-line" + (tone ? " " + tone : "");
  }

  function fieldControl(key, value, type) {
    const options = ((vocab || {})[type] || {})[key];
    if (Array.isArray(options) && options.length) {
      const known = options.includes(value);
      return `<select class="ds-field-input" data-el-field="${esc(key)}"><option value="">(unset)</option>${known ? "" : (value ? `<option value="${esc(value)}" selected>${esc(pretty(value))} (not in vocabulary)</option>` : "")}${options.map((o) => `<option value="${esc(o)}" ${o === value ? "selected" : ""}>${esc(pretty(o))}</option>`).join("")}</select>`;
    }
    return `<input class="ds-field-input" data-el-field="${esc(key)}" type="text" value="${esc(value)}" autocomplete="off">`;
  }

  function formMarkup(record, type) {
    const fields = (TYPES[type] || {}).fields || [];
    return `
      <div class="ds-section-grid elp-form">
        <div class="ds-field ds-field-wide"><div class="ds-field-label"><span>Name</span></div><input class="ds-field-input" data-el-field="name" type="text" value="${esc(record.name || "")}" autocomplete="off"></div>
        ${fields.map((key) => `<div class="ds-field"><div class="ds-field-label"><span>${esc(pretty(key))}</span></div>${fieldControl(key, String(record[key] || ""), type)}</div>`).join("")}
        <div class="ds-field ds-field-wide"><div class="ds-field-label"><span>Description</span></div><textarea class="ds-field-input" data-el-field="description" rows="4">${esc(record.description || "")}</textarea></div>
        <div class="ds-field ds-field-wide"><div class="ds-field-label"><span>Tags (comma separated)</span></div><input class="ds-field-input" data-el-field="tags" type="text" value="${esc((record.tags || []).join(", "))}" autocomplete="off"></div>
      </div>`;
  }

  function readForm(host) {
    const body = {};
    host.querySelectorAll("[data-el-field]").forEach((input) => { body[input.dataset.elField] = input.value; });
    body.tags = String(body.tags || "").split(",").map((t) => t.trim()).filter(Boolean);
    return body;
  }

  async function openRecord(folder, id, { keepScroll = false } = {}) {
    draft = null;
    selected = { folder, id };
    renderGrid();
    const host = $("elpDetail");
    host.innerHTML = '<div class="elp-detail-empty"><div class="spinner"></div></div>';
    try {
      await ensureVocab();
      const payload = await api(`/api/elements/record/${encodeURIComponent(folder)}/${encodeURIComponent(id)}`);
      renderRecord(payload.element);
    } catch (error) {
      host.innerHTML = `<div class="elp-detail-empty">${esc(error.message || "Could not open the element.")}</div>`;
    }
  }

  function renderRecord(el) {
    const host = $("elpDetail");
    const type = el.type || "talent";
    const images = el.images || [];
    host.innerHTML = `
      <div class="elp-detail-head">
        <div>
          <div class="elp-detail-name">${esc(el.name || el.id)}</div>
          <div class="elp-detail-meta"><code>${esc(el.id)}</code> · ${esc((TYPES[type] || {}).label || type)} · ${images.length} image${images.length === 1 ? "" : "s"}${el.updated_at ? ` · updated ${esc(String(el.updated_at).slice(0, 10))}` : ""}</div>
        </div>
        <div class="elp-detail-actions">
          <button type="button" class="ds-shot-mini${el.is_favorite ? " is-on" : ""}" id="elpPin" title="${el.is_favorite ? "Unpin" : "Pin (shows first in the shot picker)"}">&#9733; ${el.is_favorite ? "Pinned" : "Pin"}</button>
          <button type="button" class="ds-shot-mini" id="elpClose" title="Close">&times;</button>
        </div>
      </div>
      <div class="elp-images" id="elpImages">
        ${images.map((img) => `<div class="elp-image${img.is_primary ? " is-primary" : ""}${img.exists ? "" : " is-missing"}" data-image="${esc(img.path || img.filename)}">
          ${img.exists ? `<img src="${esc(img.url)}" alt="" draggable="false">` : '<div class="elp-image-missing">file missing</div>'}
          <div class="elp-image-actions">
            ${img.is_primary ? '<span class="elp-image-tag">primary</span>' : `<button type="button" class="ds-shot-mini" data-make-primary="${esc(img.path || img.filename)}">Make primary</button>`}
            <button type="button" class="ds-shot-mini ds-shot-mini-danger" data-remove-image="${esc(img.path || img.filename)}" title="Remove this image">&times;</button>
          </div>
        </div>`).join("")}
        <label class="elp-image elp-image-add" title="Add an image to this element">
          <span>+ Add image</span><input type="file" accept="image/*" id="elpAddImage" style="display:none">
        </label>
      </div>
      ${formMarkup(el, type)}
      <div class="ds-attach-actions elp-save-row">
        <button type="button" class="history-filter-toggle ds-style-save" id="elpSave">Save changes</button>
        <button type="button" class="history-filter-toggle ds-take-delete" id="elpDelete">Delete element</button>
        <span class="ds-shot-status-line" id="elpStatus"></span>
      </div>
      <div class="elp-hint">Deleting removes the record and its image files. Shots that reference it will show it as missing.</div>`;
    $("elpClose").addEventListener("click", () => { selected = null; draft = null; renderGrid(); host.innerHTML = '<div class="elp-detail-empty">Pick an element to see and edit it, or press <strong>+ New</strong>.</div>'; });
    $("elpPin").addEventListener("click", async () => {
      try { await api(`/api/elements/record/${encodeURIComponent(el.folder)}/${encodeURIComponent(el.id)}`, { method: "PATCH", body: { is_favorite: !el.is_favorite } }); await loadList(); openRecord(el.folder, el.id); }
      catch (error) { setDetailStatus(error.message || "Could not change the pin.", "error"); }
    });
    $("elpSave").addEventListener("click", async () => {
      const body = readForm(host);
      setDetailStatus("Saving…");
      try {
        const payload = await api(`/api/elements/record/${encodeURIComponent(el.folder)}/${encodeURIComponent(el.id)}`, { method: "PATCH", body });
        await loadList();
        renderRecord(payload.element);
        setDetailStatus("Saved.", "success");
      } catch (error) { setDetailStatus(error.message || "Could not save.", "error"); }
    });
    const del = $("elpDelete");
    del.addEventListener("click", async () => {
      if (del.dataset.armed !== "1") { del.dataset.armed = "1"; del.textContent = "Delete for good?"; window.setTimeout(() => { del.dataset.armed = ""; del.textContent = "Delete element"; }, 3000); return; }
      try {
        await api(`/api/elements/record/${encodeURIComponent(el.folder)}/${encodeURIComponent(el.id)}`, { method: "DELETE" });
        selected = null;
        await loadList();
        host.innerHTML = `<div class="elp-detail-empty">${esc(el.name || el.id)} deleted.</div>`;
      } catch (error) { setDetailStatus(error.message || "Could not delete.", "error"); }
    });
    host.querySelectorAll("[data-make-primary]").forEach((button) => button.addEventListener("click", async () => {
      try { const payload = await api(`/api/elements/record/${encodeURIComponent(el.folder)}/${encodeURIComponent(el.id)}`, { method: "PATCH", body: { primary_image: button.dataset.makePrimary } }); await loadList(); renderRecord(payload.element); }
      catch (error) { setDetailStatus(error.message || "Could not set the primary image.", "error"); }
    }));
    host.querySelectorAll("[data-remove-image]").forEach((button) => button.addEventListener("click", async () => {
      if (button.dataset.armed !== "1") { button.dataset.armed = "1"; button.textContent = "Sure?"; window.setTimeout(() => { button.dataset.armed = ""; button.innerHTML = "&times;"; }, 2500); return; }
      try { const payload = await api(`/api/elements/record/${encodeURIComponent(el.folder)}/${encodeURIComponent(el.id)}`, { method: "PATCH", body: { remove_image: button.dataset.removeImage } }); await loadList(); renderRecord(payload.element); }
      catch (error) { setDetailStatus(error.message || "Could not remove the image.", "error"); }
    }));
    $("elpAddImage").addEventListener("change", async (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      setDetailStatus(`Adding ${file.name}…`);
      try {
        const data = await readFileB64(file);
        const payload = await api(`/api/elements/record/${encodeURIComponent(el.folder)}/${encodeURIComponent(el.id)}/images`, { method: "POST", body: { data, mime_type: file.type || "image/jpeg" } });
        await loadList();
        renderRecord(payload.element);
        setDetailStatus("Image added.", "success");
      } catch (error) { setDetailStatus(error.message || "Could not add the image.", "error"); }
      event.target.value = "";
    });
  }

  function readFileB64(file) {
    return new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result || "").split(",")[1] || ""); r.onerror = reject; r.readAsDataURL(file); });
  }

  // ---- create ----------------------------------------------------------------
  async function startCreate(type) {
    selected = null;
    renderGrid();
    await ensureVocab();
    draft = { type: type || typeForCat(), image: null, metadata: { name: "", description: "", tags: [] } };
    renderCreate();
  }

  function renderCreate() {
    const host = $("elpDetail");
    const type = draft.type;
    const label = (TYPES[type] || {}).label || type;
    host.innerHTML = `
      <div class="elp-detail-head">
        <div><div class="elp-detail-name">New ${esc(label.toLowerCase())}</div><div class="elp-detail-meta">Drop or choose an image, let Gemini read it (optional), then save.</div></div>
        <div class="elp-detail-actions">
          <select class="ds-field-input" id="elpDraftType">${Object.entries(TYPES).map(([key, cfg]) => `<option value="${key}" ${key === type ? "selected" : ""}>${esc(cfg.label)}</option>`).join("")}</select>
          <button type="button" class="ds-shot-mini" id="elpClose" title="Cancel">&times;</button>
        </div>
      </div>
      <div class="elp-drop${draft.image ? " has-image" : ""}" id="elpDrop">
        ${draft.image ? `<img src="${esc(draft.image.previewUrl)}" alt="" draggable="false">` : '<div class="elp-drop-text"><div class="wizard-drop-icon">+</div>Drag an image here, or click to choose</div>'}
        <input type="file" accept="image/*" id="elpDraftFile" style="display:none">
      </div>
      <div class="ds-attach-actions">
        <button type="button" class="history-filter-toggle" id="elpAnalyze" ${draft.image ? "" : "disabled"} title="Gemini Vision fills the fields from the image">✦ Read the image</button>
        <span class="ds-shot-status-line" id="elpStatus"></span>
      </div>
      ${formMarkup(draft.metadata, type)}
      <div class="ds-attach-actions elp-save-row">
        <button type="button" class="history-filter-toggle ds-style-save" id="elpCreate" ${draft.image ? "" : "disabled"}>Create ${esc(label.toLowerCase())}</button>
      </div>`;
    $("elpClose").addEventListener("click", () => { draft = null; host.innerHTML = '<div class="elp-detail-empty">Pick an element to see and edit it, or press <strong>+ New</strong>.</div>'; });
    $("elpDraftType").addEventListener("change", (event) => { draft.metadata = { ...readForm(host), tags: readForm(host).tags }; draft.type = event.target.value; renderCreate(); });
    const drop = $("elpDrop");
    const takeFile = async (file) => {
      if (!file || !file.type || !file.type.startsWith("image/")) return;
      draft.metadata = readForm(host);
      const data = await readFileB64(file);
      draft.image = { data, mime_type: file.type, name: file.name, previewUrl: `data:${file.type};base64,${data}` };
      if (!draft.metadata.name) draft.metadata.name = file.name.replace(/\.[a-z0-9]+$/i, "").replace(/[_-]+/g, " ");
      renderCreate();
    };
    drop.addEventListener("click", () => $("elpDraftFile").click());
    $("elpDraftFile").addEventListener("change", (event) => takeFile(event.target.files && event.target.files[0]));
    drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("is-drop"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("is-drop"));
    drop.addEventListener("drop", (event) => { event.preventDefault(); drop.classList.remove("is-drop"); takeFile(event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0]); });
    $("elpAnalyze").addEventListener("click", async () => {
      if (!draft.image) return;
      draft.metadata = readForm(host);
      setDetailStatus("Gemini Vision is reading the image…");
      try {
        const payload = await api("/api/elements/analyze-image", { method: "POST", body: { data: draft.image.data, mime_type: draft.image.mime_type, element_type: draft.type } });
        const meta = payload.metadata || {};
        draft.metadata = { ...draft.metadata, ...meta, name: draft.metadata.name || meta.name || "", tags: Array.isArray(meta.tags) ? meta.tags : draft.metadata.tags };
        renderCreate();
        setDetailStatus(`Fields filled · $${Number((payload.usage || {}).cost_usd || 0).toFixed(5)}. Check them, then create.`, "success");
      } catch (error) { setDetailStatus(error.message || "Could not read the image.", "error"); }
    });
    $("elpCreate").addEventListener("click", async () => {
      if (!draft.image) return;
      const metadata = readForm(host);
      if (!metadata.name.trim()) { setDetailStatus("Give it a name.", "error"); return; }
      setDetailStatus("Creating…");
      try {
        const payload = await api("/api/elements/save-talent", { method: "POST", body: { image_data: draft.image.data, mime_type: draft.image.mime_type, element_type: draft.type, folder: FOLDER_BY_TYPE[draft.type], metadata } });
        draft = null;
        cat = { talent: "characters", environment: "locations", prop: "props" }[type] || cat;
        page = 1;
        await loadList();
        openRecord(FOLDER_BY_TYPE[type], payload.talent_id);
      } catch (error) { setDetailStatus(error.message || "Could not create the element.", "error"); }
    });
  }

  // ---- wiring ----------------------------------------------------------------
  function init() {
    if (!$("elpGrid")) return;
    $("elpSearch").addEventListener("input", (event) => { window.clearTimeout(searchTimer); searchTimer = window.setTimeout(() => { query = event.target.value.trim(); page = 1; loadList(); }, 250); });
    $("elpPrevBtn").addEventListener("click", () => { if (page > 1) { page -= 1; loadList(); } });
    $("elpNextBtn").addEventListener("click", () => { page += 1; loadList(); });
    $("elpNewBtn").addEventListener("click", () => startCreate(typeForCat()));
    const params = new URLSearchParams(window.location.search);
    if (params.get("cat")) cat = params.get("cat");
    loadList().then(() => { if (params.get("id") && params.get("folder")) openRecord(params.get("folder"), params.get("id")); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true }); else init();
})();
