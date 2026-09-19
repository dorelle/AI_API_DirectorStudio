(function () {
  // Director Studio - shot list (right panel) + shot editor (bottom panel, "Shot" pill).
  // Only active when the scope bar's project record is type = film.
  const STORAGE_KEYS = {
    rail: "ds_rail_mode",
    sections: "ds_shot_sections",
    selected: "ds_selected_shot",
  };
  const STATUS_LABELS = { empty: "Empty", queued: "Queued", rendering: "Rendering", done: "Done", rejected: "Rejected" };
  const FIELD_GROUPS = [
    { key: "timing", label: "Timing", fields: [
      { key: "duration_seconds", label: "Duration (seconds)", type: "number" },
      { key: "beat_marker", label: "Beat marker" },
    ] },
    { key: "content", label: "Content", fields: [
      { key: "action_text", label: "Action", type: "textarea" },
      { key: "dialogue", label: "Dialogue / VO", type: "textarea" },
      { key: "audio_cue", label: "Audio cue" },
    ] },
    { key: "camera", label: "Camera", fields: [
      { key: "shot_size", label: "Shot size", list: ["Wide", "Medium", "Close"] },
      { key: "angle", label: "Angle", list: ["Eye level", "Low", "High", "Overhead"] },
      { key: "movement", label: "Movement (up to three, comma separated)", type: "movement" },
      { key: "lens", label: "Lens / focal length" },
      { key: "aperture", label: "Aperture" },
      { key: "speed_ramp", label: "Speed ramp" },
    ] },
    { key: "generation", label: "Generation", fields: [
      { key: "first_frame", label: "First frame" },
      { key: "last_frame", label: "Last frame" },
      { key: "chain_from_previous", label: "Chain from previous shot's last frame", type: "checkbox" },
      { key: "engine", label: "Engine" },
    ] },
    { key: "notes", label: "Notes", fields: [
      { key: "status", label: "Status", type: "select", options: Object.keys(STATUS_LABELS) },
      { key: "note", label: "Note", type: "textarea" },
    ] },
  ];

  let project = null;
  let shots = [];
  let selectedId = "";
  let railMode = "library";
  let dragId = "";

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isFilm = () => Boolean(project && project.type === "film");
  const findShot = (id) => shots.find((shot) => String(shot.id) === String(id)) || null;

  function readStorage(key, fallback) {
    try { const raw = localStorage.getItem(key); return raw === null ? fallback : raw; } catch (e) { return fallback; }
  }
  function writeStorage(key, value) {
    try { localStorage.setItem(key, value); } catch (e) {}
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...options,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload || payload.ok === false) {
      throw new Error((payload && payload.error) || `Request failed (${response.status})`);
    }
    return payload;
  }

  function setStatusLine(message, tone = "") {
    const el = $("dsShotStatusLine");
    if (!el) return;
    el.textContent = message || "";
    el.className = "ds-shot-status-line" + (tone ? " " + tone : "");
    if (message) window.setTimeout(() => { if (el.textContent === message) el.textContent = ""; }, 3000);
  }

  // ---- visibility gate --------------------------------------------------
  function applyProjectGate() {
    const film = isFilm();
    const toggle = $("dsRailToggle");
    const pill = $("promptModeShotBtn");
    if (toggle) toggle.style.display = film ? "" : "none";
    if (pill) pill.style.display = film ? "" : "none";
    if (!film) {
      shots = [];
      selectedId = "";
      setRailMode("library", { persist: false });
      const bar = $("promptBar");
      if (bar && bar.dataset.promptMode === "shot" && typeof window.setPromptMode === "function") {
        window.setPromptMode("creation");
      }
      renderEditor();
      return;
    }
    setRailMode(readStorage(STORAGE_KEYS.rail, "library") === "shots" ? "shots" : "library", { persist: false });
    loadShots();
  }

  // ---- right panel -------------------------------------------------------
  function setRailMode(mode, { persist = true } = {}) {
    railMode = mode === "shots" && isFilm() ? "shots" : "library";
    const shotsOn = railMode === "shots";
    const list = $("dsShotList");
    if (list) list.style.display = shotsOn ? "" : "none";
    // Library = everything that is there today; hidden as a block while Shots is showing.
    ["galleryToolbar"].forEach((id) => { const el = $(id); if (el) el.style.display = shotsOn ? "none" : ""; });
    document.querySelectorAll("#galleryHistorySidebar .gallery-rail").forEach((el) => { el.style.display = shotsOn ? "none" : ""; });
    ["galleryFilterToggleBtn", "gallerySelectModeBtn"].forEach((id) => { const el = $(id); if (el) el.style.display = shotsOn ? "none" : ""; });
    const source = document.querySelector("#galleryHistorySidebar .gallery-source-switch");
    if (source) source.style.display = shotsOn ? "none" : "";
    const bulk = $("galleryBulkDeleteBtn");
    if (bulk && shotsOn) bulk.style.display = "none";
    document.querySelectorAll("#dsRailToggle button").forEach((button) => {
      button.classList.toggle("active", button.dataset.rail === railMode);
      button.setAttribute("aria-pressed", button.dataset.rail === railMode ? "true" : "false");
    });
    if (persist) writeStorage(STORAGE_KEYS.rail, railMode);
    if (!shotsOn && typeof window.updateGalleryRailLayout === "function") {
      window.requestAnimationFrame(() => window.updateGalleryRailLayout());
    }
  }

  async function loadShots() {
    if (!isFilm()) return;
    try {
      const payload = await api(`/api/shots?project_id=${encodeURIComponent(project.id)}`);
      shots = Array.isArray(payload.shots) ? payload.shots : [];
      const remembered = readStorage(STORAGE_KEYS.selected + ":" + project.id, "");
      if (!findShot(selectedId)) selectedId = findShot(remembered) ? remembered : "";
      renderList();
      renderEditor();
    } catch (error) {
      setStatusLine(error.message || "Could not load shots.", "error");
    }
  }

  function renderList() {
    const list = $("dsShotList");
    if (!list) return;
    const rows = shots.map((shot) => `
      <div class="ds-shot-row${String(shot.id) === String(selectedId) ? " is-selected" : ""}" draggable="true" data-id="${shot.id}" title="${esc(shot.slug)} · ${esc(STATUS_LABELS[shot.status] || shot.status)}">
        <span class="ds-shot-status" data-status="${esc(shot.status)}" aria-label="${esc(STATUS_LABELS[shot.status] || shot.status)}"></span>
        <span class="ds-shot-slug">${esc(shot.slug)}</span>
        <span class="ds-shot-scene">${esc(shot.scene || "")}</span>
        <span class="ds-shot-row-actions">
          <button type="button" class="ds-shot-mini" data-action="insert" title="Insert a shot after this one">+</button>
          <button type="button" class="ds-shot-mini ds-shot-mini-danger" data-action="delete" title="Delete this shot">&times;</button>
        </span>
      </div>`).join("");
    list.innerHTML = `
      <div class="ds-shot-list-head">
        <span class="ds-shot-list-title">Shots <span class="ds-shot-count">${shots.length}</span></span>
        <button type="button" class="history-filter-toggle" id="dsAddShotBtn">+ Add shot</button>
      </div>
      <div class="ds-shot-status-line" id="dsShotStatusLine"></div>
      <div class="ds-shot-rows" id="dsShotRows">${rows || '<div class="ds-shot-empty">No shots yet. Add the first one.</div>'}</div>`;
    $("dsAddShotBtn")?.addEventListener("click", () => createShot());
    bindRowEvents();
  }

  function bindRowEvents() {
    const container = $("dsShotRows");
    if (!container) return;
    container.querySelectorAll(".ds-shot-row").forEach((row) => {
      const id = row.dataset.id;
      row.addEventListener("click", (event) => {
        if (event.target.closest("[data-action]")) return;
        selectShot(id);
      });
      row.querySelector('[data-action="insert"]').addEventListener("click", (event) => {
        event.stopPropagation();
        createShot({ after_id: Number(id) });
      });
      const del = row.querySelector('[data-action="delete"]');
      del.addEventListener("click", (event) => {
        event.stopPropagation();
        if (del.dataset.armed === "1") { deleteShot(id); return; }
        del.dataset.armed = "1";
        del.textContent = "Sure?";
        del.classList.add("is-armed");
        window.setTimeout(() => { del.dataset.armed = ""; del.innerHTML = "&times;"; del.classList.remove("is-armed"); }, 2500);
      });
      row.addEventListener("dragstart", (event) => {
        dragId = id;
        row.classList.add("is-dragging");
        event.dataTransfer.effectAllowed = "move";
        try { event.dataTransfer.setData("text/plain", id); } catch (e) {}
      });
      row.addEventListener("dragend", () => {
        dragId = "";
        container.querySelectorAll(".ds-shot-row").forEach((el) => el.classList.remove("is-dragging", "drop-before", "drop-after"));
      });
      row.addEventListener("dragover", (event) => {
        if (!dragId || dragId === id) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        const rect = row.getBoundingClientRect();
        const before = event.clientY < rect.top + rect.height / 2;
        row.classList.toggle("drop-before", before);
        row.classList.toggle("drop-after", !before);
      });
      row.addEventListener("dragleave", () => row.classList.remove("drop-before", "drop-after"));
      row.addEventListener("drop", (event) => {
        if (!dragId || dragId === id) return;
        event.preventDefault();
        const rect = row.getBoundingClientRect();
        const before = event.clientY < rect.top + rect.height / 2;
        const ids = shots.map((shot) => String(shot.id)).filter((value) => value !== dragId);
        const targetIndex = ids.indexOf(id);
        ids.splice(before ? targetIndex : targetIndex + 1, 0, dragId);
        reorder(ids);
      });
    });
  }

  function selectShot(id) {
    selectedId = String(id || "");
    if (project) writeStorage(STORAGE_KEYS.selected + ":" + project.id, selectedId);
    renderList();
    renderEditor();
    if (selectedId && typeof window.setPromptMode === "function") window.setPromptMode("shot");
  }

  async function createShot(extra = {}) {
    if (!isFilm()) return;
    try {
      const payload = await api("/api/shots", { method: "POST", body: { project_id: project.id, ...extra } });
      await loadShots();
      selectShot(payload.shot.id);
      setStatusLine(`${payload.shot.slug} added.`, "success");
    } catch (error) {
      setStatusLine(error.message || "Could not add the shot.", "error");
    }
  }

  async function deleteShot(id) {
    try {
      await api(`/api/shots/${id}`, { method: "DELETE" });
      if (String(selectedId) === String(id)) selectedId = "";
      await loadShots();
      setStatusLine("Shot deleted.", "success");
    } catch (error) {
      setStatusLine(error.message || "Could not delete the shot.", "error");
    }
  }

  async function reorder(ids) {
    try {
      const payload = await api("/api/shots/reorder", { method: "POST", body: { project_id: project.id, ids: ids.map(Number) } });
      shots = Array.isArray(payload.shots) ? payload.shots : shots;
      renderList();
      renderEditor();
    } catch (error) {
      setStatusLine(error.message || "Could not reorder.", "error");
      renderList();
    }
  }

  // ---- bottom panel editor ----------------------------------------------
  function sectionState() {
    try { const raw = JSON.parse(readStorage(STORAGE_KEYS.sections, "{}")); return raw && typeof raw === "object" ? raw : {}; } catch (e) { return {}; }
  }
  function isSectionOpen(key) {
    const state = sectionState();
    return key in state ? Boolean(state[key]) : true;
  }
  function setSectionOpen(key, open) {
    const state = sectionState();
    state[key] = Boolean(open);
    writeStorage(STORAGE_KEYS.sections, JSON.stringify(state));
  }

  function fieldValue(shot, field) {
    const value = shot[field.key];
    if (field.type === "movement") return Array.isArray(value) ? value.join(", ") : "";
    if (field.type === "checkbox") return Boolean(value);
    return value === null || value === undefined ? "" : String(value);
  }

  function renderField(shot, field) {
    const locked = Array.isArray(shot.locked_fields) && shot.locked_fields.includes(field.key);
    const lock = `<button type="button" class="ds-lock${locked ? " is-locked" : ""}" data-lock="${field.key}" title="${locked ? "Locked by hand edit — click to unlock" : "Not locked; generated passes may fill this"}" aria-pressed="${locked ? "true" : "false"}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="${locked ? "M7 11V7a5 5 0 0 1 10 0v4" : "M7 11V7a5 5 0 0 1 9.9-1"}"/></svg>
      </button>`;
    const value = fieldValue(shot, field);
    let control = "";
    if (field.type === "textarea") {
      control = `<textarea class="ds-field-input" data-field="${field.key}" rows="2">${esc(value)}</textarea>`;
    } else if (field.type === "checkbox") {
      control = `<label class="ds-field-check"><input type="checkbox" data-field="${field.key}" ${value ? "checked" : ""}> <span>${esc(field.label)}</span></label>`;
    } else if (field.type === "select") {
      control = `<select class="ds-field-input" data-field="${field.key}">${field.options.map((option) => `<option value="${esc(option)}" ${option === value ? "selected" : ""}>${esc(STATUS_LABELS[option] || option)}</option>`).join("")}</select>`;
    } else {
      const listId = field.list ? `dsList_${field.key}` : "";
      control = `<input class="ds-field-input" data-field="${field.key}" type="${field.type === "number" ? "number" : "text"}" ${field.type === "number" ? 'step="0.1" min="0"' : ""} value="${esc(value)}" ${listId ? `list="${listId}"` : ""} autocomplete="off">` +
        (field.list ? `<datalist id="${listId}">${field.list.map((option) => `<option value="${esc(option)}"></option>`).join("")}</datalist>` : "");
    }
    return `<div class="ds-field${locked ? " is-locked" : ""}" data-field-wrap="${field.key}">
      ${field.type === "checkbox" ? "" : `<div class="ds-field-label"><span>${esc(field.label)}</span>${lock}</div>`}
      ${control}
      ${field.type === "checkbox" ? lock : ""}
    </div>`;
  }

  function renderEditor() {
    const host = $("dsShotEditor");
    if (!host) return;
    if (!isFilm()) {
      host.innerHTML = "";
      return;
    }
    const shot = findShot(selectedId);
    if (!shot) {
      host.innerHTML = `<div class="ds-editor-empty">${shots.length ? "Select a shot in the Shots panel to edit it." : "No shots yet. Use “+ Add shot” in the Shots panel."}</div>`;
      return;
    }
    const sceneLocked = Array.isArray(shot.locked_fields) && shot.locked_fields.includes("scene");
    const sections = FIELD_GROUPS.map((group) => `
      <details class="ds-section" data-section="${group.key}" ${isSectionOpen(group.key) ? "open" : ""}>
        <summary>${esc(group.label)}</summary>
        <div class="ds-section-grid">${group.fields.map((field) => renderField(shot, field)).join("")}</div>
      </details>`).join("");
    host.innerHTML = `
      <div class="ds-editor-head">
        <span class="ds-shot-status ds-shot-status-lg" data-status="${esc(shot.status)}"></span>
        <span class="ds-editor-slug" title="Fixed at creation; never changes">${esc(shot.slug)}</span>
        <div class="ds-field ds-field-inline${sceneLocked ? " is-locked" : ""}" data-field-wrap="scene">
          <input class="ds-field-input" data-field="scene" type="text" placeholder="Scene / sequence" value="${esc(shot.scene || "")}" autocomplete="off">
          <button type="button" class="ds-lock${sceneLocked ? " is-locked" : ""}" data-lock="scene" title="${sceneLocked ? "Locked by hand edit — click to unlock" : "Not locked"}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="${sceneLocked ? "M7 11V7a5 5 0 0 1 10 0v4" : "M7 11V7a5 5 0 0 1 9.9-1"}"/></svg></button>
        </div>
        <span class="ds-editor-order">#${shots.findIndex((item) => item.id === shot.id) + 1} of ${shots.length}</span>
        <span class="ds-editor-saved" id="dsEditorSaved"></span>
      </div>
      <div class="ds-sections">${sections}</div>`;
    host.querySelectorAll("details.ds-section").forEach((details) => {
      details.addEventListener("toggle", () => setSectionOpen(details.dataset.section, details.open));
    });
    host.querySelectorAll("[data-field]").forEach((input) => {
      input.addEventListener("change", () => saveField(shot.id, input.dataset.field, input));
    });
    host.querySelectorAll("[data-lock]").forEach((button) => {
      button.addEventListener("click", () => toggleLock(shot.id, button.dataset.lock));
    });
  }

  function flashSaved(text) {
    const el = $("dsEditorSaved");
    if (!el) return;
    el.textContent = text;
    window.setTimeout(() => { if (el.textContent === text) el.textContent = ""; }, 1800);
  }

  async function saveField(shotId, fieldKey, input) {
    const shot = findShot(shotId);
    if (!shot) return;
    let value;
    if (input.type === "checkbox") value = input.checked;
    else if (fieldKey === "movement") value = input.value.split(",").map((part) => part.trim()).filter(Boolean).slice(0, 3);
    else value = input.value;
    // A hand edit locks the field so later generated passes leave it alone.
    const locked = Array.isArray(shot.locked_fields) ? shot.locked_fields.slice() : [];
    if (!locked.includes(fieldKey)) locked.push(fieldKey);
    try {
      const payload = await api(`/api/shots/${shotId}`, { method: "PATCH", body: { [fieldKey]: value, locked_fields: locked } });
      const index = shots.findIndex((item) => item.id === payload.shot.id);
      if (index >= 0) shots[index] = payload.shot;
      // Re-render the list (slug/scene/status may show there) but keep focus in the editor.
      renderList();
      const wrap = document.querySelector(`[data-field-wrap="${fieldKey}"]`);
      if (wrap) {
        wrap.classList.add("is-locked");
        const lockBtn = wrap.querySelector("[data-lock]");
        if (lockBtn) { lockBtn.classList.add("is-locked"); lockBtn.setAttribute("aria-pressed", "true"); lockBtn.title = "Locked by hand edit — click to unlock"; lockBtn.querySelector("path")?.setAttribute("d", "M7 11V7a5 5 0 0 1 10 0v4"); }
      }
      if (fieldKey === "status") {
        document.querySelectorAll(".ds-editor-head .ds-shot-status").forEach((dot) => dot.setAttribute("data-status", payload.shot.status));
      }
      flashSaved("Saved");
    } catch (error) {
      setStatusLine(error.message || "Could not save.", "error");
      flashSaved("Not saved");
    }
  }

  async function toggleLock(shotId, fieldKey) {
    const shot = findShot(shotId);
    if (!shot) return;
    const locked = Array.isArray(shot.locked_fields) ? shot.locked_fields.slice() : [];
    const next = locked.includes(fieldKey) ? locked.filter((key) => key !== fieldKey) : [...locked, fieldKey];
    try {
      const payload = await api(`/api/shots/${shotId}`, { method: "PATCH", body: { locked_fields: next } });
      const index = shots.findIndex((item) => item.id === payload.shot.id);
      if (index >= 0) shots[index] = payload.shot;
      renderEditor();
      flashSaved(next.includes(fieldKey) ? "Locked" : "Unlocked");
    } catch (error) {
      setStatusLine(error.message || "Could not change the lock.", "error");
    }
  }

  // ---- wiring -------------------------------------------------------------
  function bindRailToggle() {
    document.querySelectorAll("#dsRailToggle button").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        setRailMode(button.dataset.rail);
        if (railMode === "shots") loadShots();
      });
    });
  }

  function onScopeChange(detail) {
    const next = detail && detail.project ? detail.project : (typeof window.getSelectedProjectRecord === "function" ? window.getSelectedProjectRecord() : null);
    const changed = (next && next.id) !== (project && project.id) || (next && next.type) !== (project && project.type);
    project = next || null;
    if (changed) selectedId = "";
    applyProjectGate();
  }

  function init() {
    if (!$("dsShotList") || !$("dsShotEditor")) return;
    bindRailToggle();
    window.addEventListener("asset-meta-change", (event) => onScopeChange(event.detail));
    onScopeChange(null);
  }

  window.directorShots = { reload: loadShots, select: selectShot };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
