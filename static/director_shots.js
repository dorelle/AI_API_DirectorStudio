(function () {
  // Director Studio - shot list (right panel) + shot editor (bottom panel, "Shot" pill).
  // Only active when the scope bar's project record is type = film.
  const STORAGE_KEYS = {
    rail: "ds_rail_mode",
    sections: "ds_shot_sections",
    selected: "ds_selected_shot",
  };
  const REFERENCE_ROLES = ["unassigned", "edit_target", "character", "garment", "environment", "prop", "style"];
  const ROLE_LABELS = { unassigned: "unassigned", edit_target: "edit target", character: "character", garment: "garment", environment: "environment", prop: "prop", style: "style" };
  const refOf = (entry) => (entry && typeof entry === "object") ? String(entry.ref || "") : String(entry || "");
  const roleOf = (entry) => (entry && typeof entry === "object" && REFERENCE_ROLES.includes(entry.role)) ? entry.role : "unassigned";
  const asEntry = (entry) => ({ ref: refOf(entry), role: roleOf(entry) });
  const STATUS_LABELS = { empty: "Empty", queued: "Queued", rendering: "Rendering", done: "Done", rejected: "Rejected", failed: "Failed" };
  const FIELD_GROUPS = [
    // Working order (Task 09): numbered steps 1-7. Timing and Notes carry no number.
    { key: "timing", label: "Timing", hint: "Duration and where it lands in the beat map.", fields: [
      { key: "duration_seconds", label: "Duration (seconds)", type: "number" },
      { key: "beat_marker", label: "Beat marker" },
    ] },
    { key: "content", label: "Content", step: 1, hint: "What happens in the shot \u2014 action, dialogue, sound.", fields: [
      { key: "action_text", label: "Action", type: "textarea" },
      { key: "dialogue", label: "Dialogue / VO", type: "textarea" },
      { key: "audio_cue", label: "Audio cue" },
    ] },
    { key: "camera", label: "Camera", step: 2, hint: "How it is shot \u2014 size, angle, movement, lens.", fields: [
      { key: "shot_size", label: "Shot size", list: ["Wide", "Medium", "Close"] },
      { key: "angle", label: "Angle", list: ["Eye level", "Low", "High", "Overhead"] },
      { key: "movement", label: "Movement (up to three, comma separated)", type: "movement" },
      { key: "lens", label: "Lens / focal length" },
      { key: "aperture", label: "Aperture" },
      { key: "speed_ramp", label: "Speed ramp" },
    ] },
    // Ordered attachments: what the shot sends besides the prompt. Order = provider reference order.
    { key: "elements", label: "Elements", step: 3, list: "elements", addLabel: "+ Add elements", empty: "No talent attached yet. Add who is in the shot." },
    { key: "assets", label: "Assets", step: 4, list: "reference_assets", addLabel: "+ Add references", empty: "No references yet. Add plates, garments, props or style images." },
    { key: "style", label: "Style", style: true },
    { key: "prompt", label: "Prompt", step: 5, hint: "The text the model receives.", fields: [
      { key: "prompt", label: "Prompt", type: "textarea", rows: 4, wide: true },
      { key: "negative_prompt", label: "Negative prompt", type: "textarea", wide: true },
    ] },
    { key: "generation", label: "Generation", step: 6, hint: "First frame, engine, and whether it continues from the previous shot.", fields: [
      { key: "first_frame", label: "First frame" },
      { key: "last_frame", label: "Last frame" },
      { key: "chain_from_previous", label: "Chain from previous shot's last frame", type: "checkbox" },
      { key: "engine", label: "Engine (video model)", type: "engine" },
    ] },
    { key: "takes", label: "Takes", step: 7, takes: true },
    { key: "notes", label: "Notes", hint: "Anything the crew should know.", fields: [
      { key: "status", label: "Status", type: "select", options: Object.keys(STATUS_LABELS) },
      { key: "note", label: "Note", type: "textarea" },
    ] },
  ];

  let project = null;
  let shots = [];
  let selectedId = "";
  let railMode = "library";
  let dragId = "";
  let stripDragId = "";
  let stripPickShotId = "";
  let videoModels = null;          // id -> info from /api/video-models-info
  let videoModelsPromise = null;
  const takesCache = {};           // shotId -> takes[]
  let stylesCache = null;          // styles available to the active project
  let stylesPromise = null;
  let styleForm = null;            // { mode: "new"|"edit", id, name, text, images[], projectOnly } while the inline form is open
  let stylePickingImages = false;  // reference picker is open for the style form, not the shot
  const activeRenders = {};        // shotId -> jobId being polled

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
    syncStrip();
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
      <div class="ds-shot-row${String(shot.id) === String(selectedId) ? " is-selected" : ""}" draggable="true" data-id="${shot.id}" title="${esc(shot.slug)} \u00b7 ${esc(STATUS_LABELS[shot.status] || shot.status)}${shot.takes && shot.takes.approved ? " \u00b7 approved" : ""}">
        <span class="ds-shot-status" data-status="${esc(listStatus(shot))}" aria-label="${esc(listStatus(shot))}"></span>
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
    renderStrip();
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
    const card = document.querySelector(`.ds-card[data-id="${selectedId}"]`);
    if (card && typeof card.scrollIntoView === "function") card.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
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

  // ---- center area: the shot strip (Shot mode only) ---------------------
  function isShotMode() {
    const bar = $("promptBar");
    return Boolean(bar && bar.dataset.promptMode === "shot");
  }

  function sizeStripToViewer() {
    // The strip covers exactly the viewer (#gallery); the prompt bar below it stays where it is.
    const strip = $("dsShotStrip");
    const gallery = $("gallery");
    if (!strip || !gallery) return;
    strip.style.height = `${Math.max(0, Math.round(gallery.getBoundingClientRect().height))}px`;
  }

  function syncStrip() {
    const strip = $("dsShotStrip");
    if (!strip) return;
    const show = isFilm() && isShotMode();
    strip.style.display = show ? "" : "none";
    if (show) {
      sizeStripToViewer();
      renderStrip();
    }
  }

  function shotVisual(shot) {
    // Card image priority: approved take, else latest done take, else first frame, else text.
    const takes = shot.takes || {};
    const pick = (take) => take ? { kind: "take", take, poster: take.poster_path || "", video: take.asset_path || "" } : null;
    if (takes.approved && takes.approved.asset_path) return { ...pick(takes.approved), approved: true };
    if (takes.latest_done && takes.latest_done.asset_path) return { ...pick(takes.latest_done), approved: false };
    const url = frameUrl(shot);
    if (url) return { kind: "frame", url, approved: false };
    return { kind: "none", approved: false };
  }

  function visualMarkup(visual, alt) {
    if (visual.kind === "take") {
      if (visual.poster) return `<img src="${esc(visual.poster)}" alt="${esc(alt)}" draggable="false">`;
      return `<video src="${esc(visual.video)}#t=0.1" muted playsinline preload="auto" draggable="false"></video>`;
    }
    if (visual.kind === "frame") return `<img src="${esc(visual.url)}" alt="${esc(alt)}" draggable="false">`;
    return "";
  }

  function listStatus(shot) {
    return (shot.takes && shot.takes.approved) ? "approved" : shot.status;
  }

  function frameUrl(shot) {
    const value = String(shot.first_frame || "").trim();
    if (!value) return "";
    if (/^(https?:)?\/\//i.test(value) || value.startsWith("/") || value.startsWith("data:")) return value;
    return "";
  }

  function renderStrip() {
    const strip = $("dsShotStrip");
    if (!strip || strip.style.display === "none") return;
    const cards = shots.map((shot, index) => {
      const url = frameUrl(shot);
      const visual = shotVisual(shot);
      const hasVisual = visual.kind !== "none";
      const rendering = Boolean(shot.takes && shot.takes.rendering) || shot.status === "queued" || shot.status === "rendering";
      const selected = String(shot.id) === String(selectedId);
      const frame = hasVisual
        ? visualMarkup(visual, `${shot.slug} ${visual.kind === "take" ? "take" : "first frame"}`)
        : `<div class="ds-card-placeholder"><span class="ds-shot-slug">${esc(shot.slug)}</span>${esc(shot.scene || "No scene yet")}<span class="ds-card-placeholder-hint">No frame</span></div>`;
      const frameActions = url
        ? `<button type="button" class="ds-card-btn" data-card-action="pick">Replace</button><button type="button" class="ds-card-btn" data-card-action="clear">Clear</button>`
        : `<button type="button" class="ds-card-btn" data-card-action="pick">Pick frame</button>`;
      const ready = renderReadiness(shot);
      const renderAction = rendering
        ? ""
        : `<button type="button" class="ds-card-btn ds-card-btn-render" data-card-action="render" ${ready.ok ? "" : "disabled"} title="${esc(ready.ok ? "Render this shot" : ready.reasons.join(" \u00b7 "))}">\u25B6 Render</button>`;
      return `
      <div class="ds-card${selected ? " is-selected" : ""}${hasVisual ? "" : " ds-card-empty"}${visual.approved ? " is-approved" : ""}${rendering ? " is-rendering" : ""}" draggable="true" data-id="${shot.id}" title="${esc(shot.slug)} \u00b7 ${esc(STATUS_LABELS[shot.status] || shot.status)}${visual.approved ? " \u00b7 approved" : ""}">
        <div class="ds-card-frame">
          ${frame}
          <span class="ds-card-status" data-status="${esc(listStatus(shot))}" aria-label="${esc(listStatus(shot))}"></span>
          <span class="ds-card-pos">${index + 1}</span>
          ${visual.approved ? `<span class="ds-card-approved" title="Approved take">\u2713</span>` : ""}
          ${rendering ? `<div class="ds-card-rendering"><span class="spinner"></span>Rendering\u2026</div>` : ""}
          <div class="ds-card-actions">${renderAction}${frameActions}</div>
        </div>
        ${hasVisual ? `<div class="ds-card-foot"><span class="ds-shot-slug">${esc(shot.slug)}</span><span class="ds-shot-scene">${esc(shot.scene || "")}</span></div>` : ""}
      </div>`;
    }).join("");
    strip.innerHTML = `
      <div class="ds-strip-head">
        <span class="ds-strip-title">Sequence <span class="ds-shot-count">${shots.length}</span> \u00b7 ${esc(project ? project.name : "")}</span>
        <span class="ds-strip-hint">Drag cards to reorder \u00b7 drop a gallery image on a card to set its first frame</span>
      </div>
      <div class="ds-strip-track" id="dsStripTrack">${cards || '<div class="ds-strip-empty">No shots yet. Add one in the Shots panel on the right.</div>'}</div>`;
    bindCardEvents();
  }

  function urlFromDataTransfer(dataTransfer) {
    if (!dataTransfer) return "";
    let raw = "";
    try { raw = dataTransfer.getData("text/uri-list") || dataTransfer.getData("text/plain") || ""; } catch (e) {}
    raw = String(raw || "").split(/\r?\n/).find((line) => line && !line.startsWith("#")) || "";
    if (!raw) return "";
    try {
      const parsed = new URL(raw, window.location.origin);
      if (parsed.origin !== window.location.origin) return "";
      if (!/^\/(generations|loved|reference-archive|reference-render|videos|elements)\//.test(parsed.pathname)) return "";
      try { return decodeURIComponent(parsed.pathname); } catch (e) { return parsed.pathname; }
    } catch (e) {
      return "";
    }
  }

  function bindCardEvents() {
    const track = $("dsStripTrack");
    if (!track) return;
    track.querySelectorAll(".ds-card").forEach((card) => {
      const id = card.dataset.id;
      card.addEventListener("click", (event) => {
        const action = event.target.closest("[data-card-action]");
        if (action) {
          event.stopPropagation();
          if (action.dataset.cardAction === "pick") pickFrame(id);
          if (action.dataset.cardAction === "clear") setFirstFrame(id, "");
          if (action.dataset.cardAction === "render") renderShot(id);
          return;
        }
        selectShot(id);
      });
      card.addEventListener("dragstart", (event) => {
        stripDragId = id;
        card.classList.add("is-dragging");
        event.dataTransfer.effectAllowed = "move";
        try { event.dataTransfer.setData("text/plain", "ds-shot:" + id); } catch (e) {}
      });
      card.addEventListener("dragend", () => {
        stripDragId = "";
        track.querySelectorAll(".ds-card").forEach((el) => el.classList.remove("is-dragging", "drop-before", "drop-after", "is-drop-target"));
      });
      card.addEventListener("dragover", (event) => {
        if (stripDragId) {
          if (stripDragId === id) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
          const rect = card.getBoundingClientRect();
          const before = event.clientX < rect.left + rect.width / 2;
          card.classList.toggle("drop-before", before);
          card.classList.toggle("drop-after", !before);
          return;
        }
        // Not a card drag: accept an image from the gallery (native <img> drag carries its URL).
        const types = Array.from(event.dataTransfer?.types || []);
        if (types.includes("text/uri-list") || types.includes("text/plain") || types.includes("Files")) {
          event.preventDefault();
          event.dataTransfer.dropEffect = "copy";
          card.classList.add("is-drop-target");
        }
      });
      card.addEventListener("dragleave", () => card.classList.remove("drop-before", "drop-after", "is-drop-target"));
      card.addEventListener("drop", (event) => {
        if (stripDragId) {
          if (stripDragId === id) return;
          event.preventDefault();
          const rect = card.getBoundingClientRect();
          const before = event.clientX < rect.left + rect.width / 2;
          const ids = shots.map((shot) => String(shot.id)).filter((value) => value !== stripDragId);
          const targetIndex = ids.indexOf(id);
          ids.splice(before ? targetIndex : targetIndex + 1, 0, stripDragId);
          reorder(ids);
          return;
        }
        const url = urlFromDataTransfer(event.dataTransfer);
        card.classList.remove("is-drop-target");
        if (!url) return;
        event.preventDefault();
        setFirstFrame(id, url);
      });
    });
  }

  function pickFrame(shotId) {
    stripPickShotId = String(shotId);
    if (typeof window.openLovedPicker === "function") {
      window.openLovedPicker("shot-frame");
    } else {
      setStatusLine("The reference picker is not available on this page.", "error");
    }
  }

  function onFramePicked(url, item) {
    const shotId = stripPickShotId || selectedId;
    stripPickShotId = "";
    if (!shotId || !url) return;
    setFirstFrame(shotId, url);
  }

  async function setFirstFrame(shotId, url) {
    const shot = findShot(shotId);
    if (!shot) return;
    // Picking from the card is a hand edit too: first_frame gets locked like any other field.
    const locked = Array.isArray(shot.locked_fields) ? shot.locked_fields.slice() : [];
    if (url && !locked.includes("first_frame")) locked.push("first_frame");
    try {
      const payload = await api(`/api/shots/${shotId}`, { method: "PATCH", body: { first_frame: url || "", locked_fields: locked } });
      const index = shots.findIndex((item) => item.id === payload.shot.id);
      if (index >= 0) shots[index] = payload.shot;
      renderList();
      if (String(selectedId) === String(shotId)) renderEditor();
      flashSaved(url ? "Frame set" : "Frame cleared");
    } catch (error) {
      setStatusLine(error.message || "Could not set the frame.", "error");
    }
  }

  function watchPromptMode() {
    const bar = $("promptBar");
    if (bar && typeof MutationObserver !== "undefined") {
      new MutationObserver(() => syncStrip()).observe(bar, { attributes: true, attributeFilter: ["data-prompt-mode"] });
    }
    const gallery = $("gallery");
    if (gallery && typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => sizeStripToViewer()).observe(gallery);
    }
    window.addEventListener("resize", sizeStripToViewer);
  }

  // ---- bottom panel editor ----------------------------------------------
  function sectionState() {
    try { const raw = JSON.parse(readStorage(STORAGE_KEYS.sections, "{}")); return raw && typeof raw === "object" ? raw : {}; } catch (e) { return {}; }
  }
  function isSectionOpen(key) {
    const state = sectionState();
    if (key in state) return Boolean(state[key]);
    return key !== "guide"; // sections open by default; the guide strip starts collapsed
  }

  // ---- guidance: section state, readiness, guide strip -----------------------
  const GUIDE_STEPS = [
    ["Content", "Write what happens: the action, any dialogue, the sound."],
    ["Camera", "Say how it is shot: size, angle, movement, lens."],
    ["Elements", "Attach the talent in the shot and give each one a role."],
    ["Assets", "Attach anything else it references: plates, garments, props, style."],
    ["Prompt", "Write the text the model receives."],
    ["Generation", "Pick a first frame, choose the engine, and chain from the previous shot if it continues it."],
    ["Takes", "Render, compare the takes, approve the keeper."],
  ];

  function hasText(value) { return String(value ?? "").trim() !== ""; }

  function sectionHasContent(shot, group) {
    if (group.style) return Boolean(shot.style_id);
    if (group.list) return (shot[group.list] || []).length > 0;
    if (group.takes) return Boolean(shot.takes && shot.takes.count);
    return (group.fields || []).some((field) => {
      const value = shot[field.key];
      if (field.type === "checkbox") return Boolean(value);
      if (field.key === "status") return false; // a default status is not "content"
      if (Array.isArray(value)) return value.length > 0;
      return hasText(value);
    });
  }

  function sectionStateMarkup(shot, group) {
    if (group.style) return shot.style_id ? `<span class="ds-section-check${shot.style_enabled ? "" : " is-off"}" title="${shot.style_enabled ? "Applied" : "Attached, switched off"}">${shot.style_enabled ? "\u2713" : "off"}</span>` : "";
    if (group.list) return `<span class="ds-section-count">${(shot[group.list] || []).length}</span>`;
    if (group.takes) return `<span class="ds-section-count">${(shot.takes && shot.takes.count) || 0}</span>`;
    return sectionHasContent(shot, group) ? `<span class="ds-section-check" title="Has content">\u2713</span>` : "";
  }

  function engineResolved(shot) {
    const engine = String(shot.engine || "").trim();
    if (engine && videoModels && videoModels[engine]) return true;
    if (engine && !videoModels) return true; // catalog not loaded yet; the server checks
    if (typeof window.getCurrentVideoSelection === "function") {
      try { return Boolean(window.getCurrentVideoSelection().modelId); } catch (e) { return false; }
    }
    return false;
  }

  // Mirrors the server's checks in build_shot_render_payload so the reason shows before the click.
  function renderReadiness(shot) {
    const reasons = [];
    if (shot.takes && shot.takes.rendering) return { ok: false, rendering: true, reasons: ["Rendering\u2026"] };
    if (!hasText(shot.prompt)) reasons.push("Needs a prompt");
    if (!engineResolved(shot)) reasons.push("No engine selected");
    if (shot.chain_from_previous) {
      const index = shots.findIndex((item) => item.id === shot.id);
      const prev = index > 0 ? shots[index - 1] : null;
      if (!prev) reasons.push("Chain is on, but this is the first shot");
      else if (!(prev.takes && prev.takes.approved && prev.takes.approved.asset_path)) reasons.push(`Chain is on, but ${prev.slug} has no approved take`);
    }
    return { ok: reasons.length === 0, rendering: false, reasons };
  }

  function readinessMarkup(shot) {
    const ready = renderReadiness(shot);
    const title = ready.ok ? "Render this shot" : ready.reasons.join(" \u00b7 ");
    const button = `<button type="button" class="history-filter-toggle ds-render-btn" id="dsRenderBtn" ${ready.ok ? "" : "disabled"} title="${esc(title)}">${ready.rendering ? "Rendering\u2026" : "\u25B6 Render shot"}</button>`;
    const hint = (ready.ok || ready.rendering) ? "" : `<span class="ds-ready-hint" id="dsReadyHint">${ready.reasons.map(esc).join(" \u00b7 ")}</span>`;
    return `<span class="ds-ready">${hint}${button}</span>`;
  }

  // After a field save: refresh readiness + section state in place (the editor is not rebuilt, to keep focus).
  function refreshEditorState(shot) {
    const host = $("dsShotEditor");
    if (!host || findShot(selectedId) !== shot) return;
    const ready = host.querySelector(".ds-ready");
    if (ready) {
      ready.outerHTML = readinessMarkup(shot);
      $("dsRenderBtn")?.addEventListener("click", () => renderShot(shot.id));
    }
    FIELD_GROUPS.forEach((group) => {
      const details = host.querySelector(`details.ds-section[data-section="${group.key}"]`);
      if (!details) return;
      const filled = sectionHasContent(shot, group);
      details.classList.toggle("has-content", filled);
      details.classList.toggle("is-empty", !filled);
      const summary = details.querySelector("summary");
      if (summary) summary.innerHTML = `${group.step ? `<span class="ds-step">${group.step}</span>` : ""}${esc(group.label)}${sectionStateMarkup(shot, group)}`;
      const hint = details.querySelector(".ds-section-hint");
      if (hint && filled) hint.remove();
      if (!hint && !filled && !group.list && !group.takes && group.hint) summary?.insertAdjacentHTML("afterend", `<div class="ds-section-hint">${esc(group.hint)}</div>`);
    });
  }

  function guideMarkup() {
    return `<details class="ds-guide" data-section="guide" ${isSectionOpen("guide") ? "open" : ""}>
      <summary>How a shot comes together</summary>
      <ol class="ds-guide-steps">${GUIDE_STEPS.map(([name, text]) => `<li><strong>${esc(name)}</strong> \u2014 ${esc(text)}</li>`).join("")}</ol>
      <div class="ds-guide-note">This is the working order, not a rule. Timing and Notes can be filled at any point.</div>
    </details>`;
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
      control = `<textarea class="ds-field-input" data-field="${field.key}" rows="${field.rows || 2}">${esc(value)}</textarea>`;
    } else if (field.type === "checkbox") {
      control = `<label class="ds-field-check"><input type="checkbox" data-field="${field.key}" ${value ? "checked" : ""}> <span>${esc(field.label)}</span></label>`;
    } else if (field.type === "engine") {
      const current = String(value || "");
      const groups = {};
      Object.entries(videoModels || {}).forEach(([id, info]) => {
        const provider = String(info.provider_label || info.provider || "other");
        (groups[provider] = groups[provider] || []).push([id, info]);
      });
      const options = Object.keys(groups).sort().map((provider) => `<optgroup label="${esc(provider)}">${groups[provider]
        .sort((a, b) => String(a[1].label || a[0]).localeCompare(String(b[1].label || b[0])))
        .map(([id, info]) => `<option value="${esc(id)}" ${id === current ? "selected" : ""}>${esc(info.label || id)}</option>`).join("")}</optgroup>`).join("");
      const unknown = current && !(videoModels && videoModels[current]) ? `<option value="${esc(current)}" selected>${esc(current)} (unknown)</option>` : "";
      control = `<select class="ds-field-input" data-field="${field.key}"><option value="" ${current ? "" : "selected"}>Generator's current video model</option>${unknown}${options}</select>`;
    } else if (field.type === "select") {
      control = `<select class="ds-field-input" data-field="${field.key}">${field.options.map((option) => `<option value="${esc(option)}" ${option === value ? "selected" : ""}>${esc(STATUS_LABELS[option] || option)}</option>`).join("")}</select>`;
    } else {
      const listId = field.list ? `dsList_${field.key}` : "";
      control = `<input class="ds-field-input" data-field="${field.key}" type="${field.type === "number" ? "number" : "text"}" ${field.type === "number" ? 'step="0.1" min="0"' : ""} value="${esc(value)}" ${listId ? `list="${listId}"` : ""} autocomplete="off">` +
        (field.list ? `<datalist id="${listId}">${field.list.map((option) => `<option value="${esc(option)}"></option>`).join("")}</datalist>` : "");
    }
    return `<div class="ds-field${locked ? " is-locked" : ""}${field.wide ? " ds-field-wide" : ""}" data-field-wrap="${field.key}">
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
    const sections = FIELD_GROUPS.map((group) => {
      const filled = sectionHasContent(shot, group);
      const hint = (!group.list && !group.takes && !filled && group.hint) ? `<div class="ds-section-hint">${esc(group.hint)}</div>` : "";
      return `
      <details class="ds-section${group.list ? " ds-section-list" : ""}${filled ? " has-content" : " is-empty"}" data-section="${group.key}" ${isSectionOpen(group.key) ? "open" : ""}>
        <summary>${group.step ? `<span class="ds-step">${group.step}</span>` : ""}${esc(group.label)}${sectionStateMarkup(shot, group)}</summary>
        ${hint}
        ${group.list ? renderAttachmentList(shot, group) : (group.takes ? renderTakesSection(shot) : (group.style ? renderStyleSection(shot) : `<div class="ds-section-grid">${group.fields.map((field) => renderField(shot, field)).join("")}</div>`))}
      </details>`;
    }).join("");
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
        ${readinessMarkup(shot)}
      </div>
      ${guideMarkup()}
      <div class="ds-sections">${sections}</div>`;
    host.querySelectorAll("details.ds-section, details.ds-guide").forEach((details) => {
      details.addEventListener("toggle", () => setSectionOpen(details.dataset.section, details.open));
    });
    host.querySelectorAll("[data-field]").forEach((input) => {
      input.addEventListener("change", () => saveField(shot.id, input.dataset.field, input));
    });
    host.querySelectorAll("[data-lock]").forEach((button) => {
      button.addEventListener("click", () => toggleLock(shot.id, button.dataset.lock));
    });
    bindAttachmentLists(shot);
    bindStyleSection(shot);
    ensureStyles().then((changed) => { if (changed && findShot(selectedId) === shot) renderEditor(); });
    ensureElementCatalog().then((changed) => { if (changed && findShot(selectedId) === shot) renderEditor(); });
    ensureVideoModels().then((changed) => { if (changed && findShot(selectedId) === shot) renderEditor(); });
    $("dsRenderBtn")?.addEventListener("click", () => renderShot(shot.id));
    bindTakesSection(shot);
    loadTakes(shot.id);
  }

  // ---- style: one reusable look, attached per shot, on/off without detaching -----
  function ensureStyles(force = false) {
    if (stylesCache && !force) return Promise.resolve(false);
    if (!stylesPromise || force) {
      const query = project ? `?project_id=${encodeURIComponent(project.id)}` : "";
      stylesPromise = api(`/api/styles${query}`)
        .then((payload) => { stylesCache = payload.styles || []; return true; })
        .catch(() => { stylesCache = stylesCache || []; return false; });
    }
    return stylesPromise;
  }

  function styleThumb(url) {
    return `<span class="ds-style-thumb"><img src="${esc(url)}" alt="" draggable="false" title="${esc(String(url).split("/").pop())}"></span>`;
  }

  function renderStyleForm() {
    const form = styleForm;
    const images = (form.images || []).map((url, index) => `<span class="ds-style-thumb ds-style-thumb-edit">${styleThumb(url).replace('<span class="ds-style-thumb">', "").replace(/<\/span>$/, "")}<button type="button" class="ds-chip-remove" data-style-img-remove="${index}" title="Remove">&times;</button></span>`).join("");
    return `<div class="ds-style-form" id="dsStyleForm">
      <div class="ds-field"><div class="ds-field-label"><span>Name</span></div><input class="ds-field-input" id="dsStyleName" type="text" value="${esc(form.name || "")}" placeholder="e.g. Night Bus look" autocomplete="off"></div>
      <div class="ds-field ds-field-wide"><div class="ds-field-label"><span>Text</span></div><textarea class="ds-field-input" id="dsStyleText" rows="3" placeholder="Grade, film stock, lighting register, photographic treatment.">${esc(form.text || "")}</textarea></div>
      <div class="ds-field ds-field-wide"><div class="ds-field-label"><span>Look board</span></div>
        <div class="ds-style-board">${images || '<span class="ds-chip-empty">No images yet.</span>'}</div>
        <div class="ds-attach-actions"><button type="button" class="history-filter-toggle" id="dsStyleAddImages">+ Add images</button></div>
      </div>
      <label class="ds-field-check"><input type="checkbox" id="dsStyleProjectOnly" ${form.projectOnly ? "checked" : ""}> <span>Only for this project (unchecked = available to every project)</span></label>
      <div class="ds-attach-actions">
        <button type="button" class="history-filter-toggle ds-style-save" id="dsStyleSave">${form.mode === "edit" ? "Save style" : "Create style"}</button>
        <button type="button" class="history-filter-toggle" id="dsStyleCancel">Cancel</button>
        <span class="ds-shot-status-line" id="dsStyleFormError"></span>
      </div>
    </div>`;
  }

  function renderStyleSection(shot) {
    const locked = Array.isArray(shot.locked_fields) && shot.locked_fields.includes("style_id");
    const lock = `<button type="button" class="ds-lock${locked ? " is-locked" : ""}" data-lock="style_id" title="${locked ? "Locked by hand edit \u2014 click to unlock" : "Not locked"}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="${locked ? "M7 11V7a5 5 0 0 1 10 0v4" : "M7 11V7a5 5 0 0 1 9.9-1"}"/></svg></button>`;
    if (styleForm) return `<div class="ds-style">${renderStyleForm()}</div>`;
    const options = (stylesCache || []).map((style) => `<option value="${style.id}" ${String(style.id) === String(shot.style_id || "") ? "selected" : ""}>${esc(style.name)}${style.project_id ? "" : " \u00b7 all projects"}</option>`).join("");
    const picker = `<select class="ds-field-input ds-style-select" id="dsStyleSelect"><option value="">${shot.style_id ? "Change style\u2026" : "Attach a style\u2026"}</option>${options}</select>`;
    const style = shot.style;
    if (!shot.style_id) {
      return `<div class="ds-style">
        <div class="ds-section-hint">No style attached. A style carries the look \u2014 grade, stock, lighting \u2014 and appends to the prompt when applied.</div>
        <div class="ds-attach-actions">${picker}<button type="button" class="history-filter-toggle" id="dsStyleNew">+ New style</button>${lock}</div>
      </div>`;
    }
    const enabled = Boolean(shot.style_enabled);
    const missing = !style;
    const board = style && style.images && style.images.length ? `<div class="ds-style-board">${style.images.map(styleThumb).join("")}</div>` : "";
    return `<div class="ds-style ${enabled ? "is-on" : "is-off"}">
      <div class="ds-style-card">
        <div class="ds-style-head">
          <label class="ds-style-toggle" title="${enabled ? "Applied to this shot's render. Click to switch off (stays attached)." : "Attached but switched off. Click to apply."}">
            <input type="checkbox" id="dsStyleEnabled" ${enabled ? "checked" : ""}><span class="ds-style-switch"></span><span class="ds-style-state">${enabled ? "ON" : "OFF"}</span>
          </label>
          <span class="ds-style-name">${esc(missing ? `Style #${shot.style_id} (missing)` : style.name)}</span>
          ${style && !style.project_id ? '<span class="ds-style-scope">all projects</span>' : ""}
        </div>
        ${style && style.text ? `<div class="ds-style-text">${esc(style.text)}</div>` : (missing ? "" : '<div class="ds-section-hint">No text on this style.</div>')}
        ${board}
        ${!enabled ? '<div class="ds-style-offnote">Switched off \u2014 not sent with the render. Still attached.</div>' : ""}
      </div>
      <div class="ds-attach-actions">
        ${picker}
        ${style ? '<button type="button" class="history-filter-toggle" id="dsStyleEdit">Edit</button>' : ""}
        <button type="button" class="history-filter-toggle" id="dsStyleNew">+ New style</button>
        <button type="button" class="history-filter-toggle ds-take-delete" id="dsStyleRemove">Remove</button>
        ${lock}
      </div>
    </div>`;
  }

  async function saveStyleFields(shot, fields) {
    const locked = Array.isArray(shot.locked_fields) ? shot.locked_fields.slice() : [];
    if (!locked.includes("style_id")) locked.push("style_id");
    try {
      const payload = await api(`/api/shots/${shot.id}`, { method: "PATCH", body: { ...fields, locked_fields: locked } });
      const index = shots.findIndex((item) => item.id === payload.shot.id);
      if (index >= 0) shots[index] = payload.shot;
      renderList();
      if (String(selectedId) === String(shot.id)) renderEditor();
      flashSaved("Saved");
    } catch (error) {
      setStatusLine(error.message || "Could not save the style.", "error");
    }
  }

  function bindStyleSection(shot) {
    const host = $("dsShotEditor");
    if (!host) return;
    host.querySelector("#dsStyleSelect")?.addEventListener("change", (event) => {
      const value = event.target.value;
      if (!value) return;
      saveStyleFields(shot, { style_id: Number(value), style_enabled: true });
    });
    host.querySelector("#dsStyleEnabled")?.addEventListener("change", (event) => {
      saveStyleFields(shot, { style_enabled: Boolean(event.target.checked) });
    });
    host.querySelector("#dsStyleRemove")?.addEventListener("click", () => saveStyleFields(shot, { style_id: null }));
    host.querySelector("#dsStyleNew")?.addEventListener("click", () => {
      styleForm = { mode: "new", id: null, name: "", text: "", images: [], projectOnly: true };
      renderEditor();
    });
    host.querySelector("#dsStyleEdit")?.addEventListener("click", () => {
      const style = shot.style;
      if (!style) return;
      styleForm = { mode: "edit", id: style.id, name: style.name || "", text: style.text || "", images: (style.images || []).slice(), projectOnly: Boolean(style.project_id) };
      renderEditor();
    });
    // inline form
    host.querySelector("#dsStyleCancel")?.addEventListener("click", () => { styleForm = null; renderEditor(); });
    host.querySelector("#dsStyleAddImages")?.addEventListener("click", () => {
      syncStyleFormFromInputs();
      stylePickingImages = true;
      if (typeof window.openLovedPicker === "function") window.openLovedPicker("shot-refs");
    });
    host.querySelectorAll("[data-style-img-remove]").forEach((button) => {
      button.addEventListener("click", () => {
        syncStyleFormFromInputs();
        styleForm.images.splice(Number(button.dataset.styleImgRemove), 1);
        renderEditor();
      });
    });
    host.querySelector("#dsStyleSave")?.addEventListener("click", async () => {
      syncStyleFormFromInputs();
      const errorEl = $("dsStyleFormError");
      if (!styleForm.name.trim()) { if (errorEl) { errorEl.textContent = "Give the style a name."; errorEl.className = "ds-shot-status-line error"; } return; }
      const body = { name: styleForm.name, text: styleForm.text, images: styleForm.images, project_id: styleForm.projectOnly && project ? project.id : null };
      try {
        const payload = styleForm.mode === "edit"
          ? await api(`/api/styles/${styleForm.id}`, { method: "PATCH", body })
          : await api("/api/styles", { method: "POST", body });
        const mode = styleForm.mode;
        styleForm = null;
        await ensureStyles(true);
        if (mode === "new") {
          await saveStyleFields(shot, { style_id: payload.style.id, style_enabled: true });
        } else {
          // Edited: every shot using it shows the change on its next load; refresh this one now.
          await loadShots();
          if (String(selectedId) === String(shot.id)) renderEditor();
        }
        flashSaved(mode === "edit" ? "Style saved" : "Style created");
      } catch (error) {
        if (errorEl) { errorEl.textContent = error.message || "Could not save the style."; errorEl.className = "ds-shot-status-line error"; }
      }
    });
  }

  function syncStyleFormFromInputs() {
    if (!styleForm) return;
    styleForm.name = $("dsStyleName")?.value ?? styleForm.name;
    styleForm.text = $("dsStyleText")?.value ?? styleForm.text;
    const only = $("dsStyleProjectOnly");
    if (only) styleForm.projectOnly = only.checked;
  }

  function onStyleImagesPicked(urls) {
    stylePickingImages = false;
    if (!styleForm) return;
    (urls || []).forEach((url) => { const path = String(url || "").trim(); if (path && !styleForm.images.includes(path)) styleForm.images.push(path); });
    renderEditor();
  }

  // ---- takes + render --------------------------------------------------------
  function ensureVideoModels() {
    if (videoModels) return Promise.resolve(false);
    if (!videoModelsPromise) {
      videoModelsPromise = fetch("/api/video-models-info", { credentials: "same-origin" })
        .then((response) => response.json())
        .then((payload) => { videoModels = payload && typeof payload === "object" ? payload : {}; return true; })
        .catch(() => { videoModels = {}; return false; });
    }
    return videoModelsPromise;
  }

  function formatTakeTime(iso) {
    const text = String(iso || "");
    return text ? text.slice(0, 16).replace("T", " ") : "";
  }

  function renderTakesSection(shot) {
    const takes = takesCache[String(shot.id)];
    if (!takes) return `<div class="ds-takes" id="dsTakes"><div class="ds-chip-empty">Loading takes\u2026</div></div>`;
    if (!takes.length) return `<div class="ds-takes" id="dsTakes"><div class="ds-chip-empty">No takes yet. Render the shot to make one.</div></div>`;
    const items = takes.map((take) => {
      const failed = take.status === "failed";
      const busy = take.status === "queued" || take.status === "rendering";
      const thumb = failed
        ? `<div class="ds-take-error">${esc(take.error || "Render failed.")}</div>`
        : busy
          ? `<div class="ds-take-busy"><span class="spinner"></span>${esc(STATUS_LABELS[take.status] || take.status)}\u2026</div>`
          : (take.poster_path
              ? `<img src="${esc(take.poster_path)}" alt="Take ${take.id}" draggable="false">`
              : `<video src="${esc(take.asset_path)}#t=0.1" muted playsinline preload="metadata" controls></video>`);
      const engineLabel = videoModels && videoModels[take.engine] ? videoModels[take.engine].label : take.engine;
      return `<div class="ds-take${take.approved ? " is-approved" : ""}${failed ? " is-failed" : ""}" data-take="${take.id}">
        <div class="ds-take-thumb">${thumb}${take.approved ? `<span class="ds-card-approved" title="Approved">\u2713</span>` : ""}</div>
        <div class="ds-take-meta">
          <span class="ds-take-cost">$${Number(take.cost || 0).toFixed(2)}</span>
          <span class="ds-take-engine" title="${esc(take.engine)}">${esc(engineLabel || "\u2014")}</span>
          <span class="ds-take-time">${esc(formatTakeTime(take.completed_at || take.created_at))}</span>
        </div>
        <div class="ds-take-actions">
          ${(!failed && !busy) ? `<button type="button" class="ds-card-btn" data-take-action="${take.approved ? "unapprove" : "approve"}">${take.approved ? "Unapprove" : "Approve"}</button>` : ""}
          ${!busy ? `<button type="button" class="ds-card-btn ds-take-delete" data-take-action="delete">Delete</button>` : ""}
        </div>
      </div>`;
    }).join("");
    return `<div class="ds-takes" id="dsTakes">${items}</div>`;
  }

  function bindTakesSection(shot) {
    document.querySelectorAll("#dsTakes [data-take-action]").forEach((button) => {
      button.addEventListener("click", async (event) => {
        event.stopPropagation();
        const takeId = button.closest("[data-take]")?.dataset.take;
        const action = button.dataset.takeAction;
        if (!takeId) return;
        try {
          if (action === "approve" || action === "unapprove") {
            const payload = await api(`/api/takes/${takeId}`, { method: "PATCH", body: { approved: action === "approve" } });
            applyShotUpdate(payload.shot);
            flashSaved(action === "approve" ? "Approved" : "Unapproved");
          } else if (action === "delete") {
            if (button.dataset.armed !== "1") {
              button.dataset.armed = "1"; button.textContent = "Sure?";
              window.setTimeout(() => { button.dataset.armed = ""; button.textContent = "Delete"; }, 2500);
              return;
            }
            const payload = await api(`/api/takes/${takeId}`, { method: "DELETE" });
            applyShotUpdate(payload.shot);
          }
          delete takesCache[String(shot.id)];
          await loadTakes(shot.id);
        } catch (error) {
          setStatusLine(error.message || "Take update failed.", "error");
        }
      });
    });
  }

  function applyShotUpdate(updated) {
    if (!updated) return;
    const index = shots.findIndex((item) => item.id === updated.id);
    if (index >= 0) shots[index] = updated;
    renderList();
  }

  async function loadTakes(shotId) {
    try {
      const payload = await api(`/api/shots/${shotId}/takes`);
      takesCache[String(shotId)] = payload.takes || [];
    } catch (error) {
      takesCache[String(shotId)] = [];
    }
    if (String(selectedId) === String(shotId)) {
      const shot = findShot(shotId);
      const host = $("dsTakes");
      if (shot && host) {
        host.outerHTML = renderTakesSection(shot);
        bindTakesSection(shot);
        const count = document.querySelector('#dsShotEditor details[data-section="takes"] .ds-section-count');
        if (count) count.textContent = String((takesCache[String(shotId)] || []).length);
      }
    }
  }

  // Last frame of a rendered take, captured in the browser (the server has no video decoder).
  function extractLastFrame(url) {
    return new Promise((resolve, reject) => {
      const video = document.createElement("video");
      video.muted = true; video.playsInline = true; video.preload = "auto";
      video.style.cssText = "position:fixed;left:-9999px;top:0;width:160px;height:90px;opacity:0;pointer-events:none";
      document.body.appendChild(video);
      const fail = (message) => { video.remove(); reject(new Error(message)); };
      const timer = window.setTimeout(() => fail("Timed out reading the approved take."), 30000);
      video.addEventListener("error", () => { window.clearTimeout(timer); fail("The approved take could not be decoded in this browser."); });
      let seeked = false;
      video.addEventListener("loadeddata", () => {
        if (seeked) return;
        seeked = true;
        const duration = isFinite(video.duration) ? video.duration : 0;
        video.currentTime = Math.max(0, duration - 0.05);
      });
      video.addEventListener("seeked", () => {
        try {
          const canvas = document.createElement("canvas");
          canvas.width = video.videoWidth; canvas.height = video.videoHeight;
          canvas.getContext("2d").drawImage(video, 0, 0);
          const dataUrl = canvas.toDataURL("image/png");
          window.clearTimeout(timer);
          video.remove();
          resolve({ mime_type: "image/png", data: dataUrl.split(",")[1] || "" });
        } catch (error) {
          window.clearTimeout(timer);
          fail("Could not capture the last frame: " + (error.message || error));
        }
      });
      video.src = url;
      video.load();
    });
  }

  async function renderShot(shotId) {
    const shot = findShot(shotId);
    if (!shot || activeRenders[String(shotId)]) return;
    const body = {};
    if (typeof window.getCurrentVideoSelection === "function") {
      try { body.fallbackModel = window.getCurrentVideoSelection().modelId || ""; } catch (e) {}
    }
    if (shot.chain_from_previous) {
      const index = shots.findIndex((item) => item.id === shot.id);
      const prev = index > 0 ? shots[index - 1] : null;
      const approved = prev && prev.takes && prev.takes.approved;
      if (!prev) { setStatusLine("Chain from previous is set, but this is the first shot.", "error"); return; }
      if (!approved || !approved.asset_path) { setStatusLine(`Chain from previous: ${prev.slug} has no approved take. Approve one first, or turn chaining off.`, "error"); return; }
      setStatusLine(`Reading the last frame of ${prev.slug}\u2019s approved take\u2026`);
      try {
        body.chainStartImage = await extractLastFrame(approved.asset_path);
      } catch (error) {
        setStatusLine(error.message || "Could not read the previous take.", "error");
        return;
      }
    }
    try {
      const payload = await api(`/api/shots/${shotId}/render`, { method: "POST", body });
      activeRenders[String(shotId)] = payload.job && payload.job.jobId;
      applyShotUpdate(payload.shot);
      if (String(selectedId) === String(shotId)) { delete takesCache[String(shotId)]; renderEditor(); }
      (payload.warnings || []).forEach((warning) => setStatusLine(warning, "error"));
      setStatusLine(`${shot.slug}: render started.`, "success");
      pollRender(shotId, payload.job && payload.job.jobId);
    } catch (error) {
      setStatusLine(error.message || "Render failed to start.", "error");
    }
  }

  function pollRender(shotId, jobId) {
    if (!jobId) { delete activeRenders[String(shotId)]; return; }
    const tick = async () => {
      try {
        const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { credentials: "same-origin", cache: "no-store" });
        const job = await response.json().catch(() => ({}));
        if (job && (job.status === "completed" || job.status === "failed")) {
          // The take row is written right after the job flips; give it a beat.
          window.setTimeout(async () => {
            delete activeRenders[String(shotId)];
            await loadShots();
            delete takesCache[String(shotId)];
            if (String(selectedId) === String(shotId)) renderEditor();
            const shot = findShot(shotId);
            setStatusLine(job.status === "completed" ? `${shot ? shot.slug : "Shot"}: take ready.` : `${shot ? shot.slug : "Shot"}: render failed \u2014 ${job.error || "see the take"}`, job.status === "completed" ? "success" : "error");
          }, 600);
          return;
        }
      } catch (error) {}
      window.setTimeout(tick, 2500);
    };
    window.setTimeout(tick, 2500);
  }

  // ---- attachments: elements (talent ids) and reference_assets (paths) ---
  let elementCatalog = null;        // id -> asset from /api/elements
  let elementCatalogPromise = null;
  let attachmentDrag = null;        // { list, index }
  let pendingAttachShotId = "";

  function ensureElementCatalog() {
    if (elementCatalog) return Promise.resolve(false);
    if (!elementCatalogPromise) {
      elementCatalogPromise = fetch("/api/elements?category=all&per_page=500", { credentials: "same-origin" })
        .then((response) => response.json())
        .then((payload) => {
          elementCatalog = {};
          (payload.items || []).forEach((item) => { elementCatalog[String(item.id)] = item; });
          return true;
        })
        .catch(() => { elementCatalog = {}; return false; });
    }
    return elementCatalogPromise;
  }

  function attachmentThumb(listKey, value) {
    if (listKey === "elements") {
      const asset = elementCatalog ? elementCatalog[String(value)] : null;
      return { url: asset ? asset.img_url : "", label: asset ? asset.name : String(value), missing: Boolean(elementCatalog && !asset) };
    }
    const label = String(value).split("/").pop();
    return { url: frameUrl({ first_frame: value }), label, missing: false };
  }

  function renderAttachmentList(shot, group) {
    const values = Array.isArray(shot[group.list]) ? shot[group.list] : [];
    const locked = Array.isArray(shot.locked_fields) && shot.locked_fields.includes(group.list);
    const chips = values.map((value, index) => {
      const thumb = attachmentThumb(group.list, refOf(value));
      const role = roleOf(value);
      return `<div class="ds-chip${thumb.missing ? " is-missing" : ""} role-${role}" draggable="true" data-list="${group.list}" data-index="${index}" title="${esc(thumb.label)}${thumb.missing ? " (not found in Elements)" : ""}">
        <span class="ds-chip-pos">${index + 1}</span>
        ${thumb.url ? `<img src="${esc(thumb.url)}" alt="" draggable="false">` : `<span class="ds-chip-noimg"></span>`}
        <span class="ds-chip-text">
          <span class="ds-chip-label">${esc(thumb.label)}</span>
          <button type="button" class="ds-chip-role role-${role}" data-role-for="${index}" title="Role: ${esc(ROLE_LABELS[role])} \u2014 click to change">${esc(ROLE_LABELS[role])}</button>
        </span>
        <button type="button" class="ds-chip-remove" data-remove="${index}" title="Remove">&times;</button>
      </div>`;
    }).join("");
    return `<div class="ds-attachments" data-list="${group.list}">
      <div class="ds-chip-row">${chips || `<span class="ds-chip-empty">${esc(group.empty)}</span>`}</div>
      <div class="ds-attach-actions">
        <button type="button" class="history-filter-toggle" data-attach-add="${group.list}">${esc(group.addLabel)}</button>
        <button type="button" class="ds-lock${locked ? " is-locked" : ""}" data-lock="${group.list}" title="${locked ? "Locked by hand edit \u2014 click to unlock" : "Not locked"}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="${locked ? "M7 11V7a5 5 0 0 1 10 0v4" : "M7 11V7a5 5 0 0 1 9.9-1"}"/></svg></button>
        <span class="ds-attach-hint">Drag to reorder \u00b7 order is the reference order</span>
      </div>
    </div>`;
  }

  function bindAttachmentLists(shot) {
    document.querySelectorAll("#dsShotEditor [data-attach-add]").forEach((button) => {
      button.addEventListener("click", () => {
        pendingAttachShotId = String(shot.id);
        if (button.dataset.attachAdd === "elements") {
          if (typeof window.openElements === "function") window.openElements("shot-elements");
        } else if (typeof window.openLovedPicker === "function") {
          window.openLovedPicker("shot-refs");
        }
      });
    });
    document.querySelectorAll("#dsShotEditor .ds-attachments").forEach((container) => {
      const listKey = container.dataset.list;
      container.querySelectorAll("[data-remove]").forEach((button) => {
        button.addEventListener("click", (event) => {
          event.stopPropagation();
          const values = (shot[listKey] || []).map(asEntry);
          values.splice(Number(button.dataset.remove), 1);
          saveAttachmentList(shot.id, listKey, values);
        });
      });
      container.querySelectorAll("[data-role-for]").forEach((button) => {
        button.addEventListener("click", (event) => {
          event.stopPropagation();
          openRoleMenu(shot, listKey, Number(button.dataset.roleFor), button);
        });
      });
      container.querySelectorAll(".ds-chip").forEach((chip) => {
        const index = Number(chip.dataset.index);
        chip.addEventListener("dragstart", (event) => {
          attachmentDrag = { list: listKey, index };
          chip.classList.add("is-dragging");
          event.dataTransfer.effectAllowed = "move";
          try { event.dataTransfer.setData("text/plain", `ds-attach:${listKey}:${index}`); } catch (e) {}
        });
        chip.addEventListener("dragend", () => {
          attachmentDrag = null;
          container.querySelectorAll(".ds-chip").forEach((el) => el.classList.remove("is-dragging", "drop-before", "drop-after"));
        });
        chip.addEventListener("dragover", (event) => {
          if (!attachmentDrag || attachmentDrag.list !== listKey || attachmentDrag.index === index) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
          const rect = chip.getBoundingClientRect();
          const before = event.clientX < rect.left + rect.width / 2;
          chip.classList.toggle("drop-before", before);
          chip.classList.toggle("drop-after", !before);
        });
        chip.addEventListener("dragleave", () => chip.classList.remove("drop-before", "drop-after"));
        chip.addEventListener("drop", (event) => {
          if (!attachmentDrag || attachmentDrag.list !== listKey || attachmentDrag.index === index) return;
          event.preventDefault();
          const rect = chip.getBoundingClientRect();
          const before = event.clientX < rect.left + rect.width / 2;
          const values = (shot[listKey] || []).map(asEntry);
          const [moved] = values.splice(attachmentDrag.index, 1);
          let target = index - (attachmentDrag.index < index ? 1 : 0);
          values.splice(before ? target : target + 1, 0, moved);
          saveAttachmentList(shot.id, listKey, values);
        });
      });
    });
  }

  function closeRoleMenus() {
    document.querySelectorAll(".ds-role-menu").forEach((menu) => menu.remove());
  }

  function openRoleMenu(shot, listKey, index, anchor) {
    closeRoleMenus();
    const current = roleOf((shot[listKey] || [])[index]);
    const menu = document.createElement("div");
    menu.className = "ds-role-menu";
    menu.innerHTML = REFERENCE_ROLES.map((role) => `<button type="button" class="ds-role-option role-${role}${role === current ? " is-current" : ""}" data-role="${role}">${esc(ROLE_LABELS[role])}${role === "edit_target" ? '<span class="ds-role-note">one per shot</span>' : ""}</button>`).join("");
    menu.addEventListener("click", (event) => {
      const option = event.target.closest("[data-role]");
      if (!option) return;
      event.stopPropagation();
      closeRoleMenus();
      setAttachmentRole(shot, listKey, index, option.dataset.role);
    });
    anchor.closest(".ds-chip").appendChild(menu);
    window.setTimeout(() => document.addEventListener("pointerdown", (event) => { if (!event.target.closest(".ds-role-menu")) closeRoleMenus(); }, { once: true }), 0);
  }

  function setAttachmentRole(shot, listKey, index, role) {
    // Role is a property of the attachment; position is untouched. edit_target is unique across both lists.
    const values = (shot[listKey] || []).map(asEntry);
    if (!values[index]) return;
    const displaced = [];
    if (role === "edit_target") {
      SHOT_LIST_KEYS.forEach((key) => {
        (shot[key] || []).forEach((entry, i) => {
          if (roleOf(entry) === "edit_target" && !(key === listKey && i === index)) displaced.push(attachmentThumb(key, refOf(entry)).label);
        });
      });
    }
    values[index].role = role;
    saveAttachmentList(shot.id, listKey, values).then(() => {
      if (displaced.length) setStatusLine(`Edit target moved from ${displaced.join(", ")} to ${attachmentThumb(listKey, values[index].ref).label}.`, "success");
    });
  }
  const SHOT_LIST_KEYS = ["elements", "reference_assets"];

  async function saveAttachmentList(shotId, listKey, values) {
    const shot = findShot(shotId);
    if (!shot) return;
    const locked = Array.isArray(shot.locked_fields) ? shot.locked_fields.slice() : [];
    if (!locked.includes(listKey)) locked.push(listKey);
    try {
      const payload = await api(`/api/shots/${shotId}`, { method: "PATCH", body: { [listKey]: values, locked_fields: locked } });
      const index = shots.findIndex((item) => item.id === payload.shot.id);
      if (index >= 0) shots[index] = payload.shot;
      if (String(selectedId) === String(shotId)) renderEditor();
      flashSaved("Saved");
    } catch (error) {
      setStatusLine(error.message || "Could not save.", "error");
    }
  }

  function onElementsPicked(assets) {
    const shotId = pendingAttachShotId || selectedId;
    pendingAttachShotId = "";
    const shot = findShot(shotId);
    if (!shot || !Array.isArray(assets) || !assets.length) return;
    if (!elementCatalog) elementCatalog = {};
    assets.forEach((asset) => { if (asset && asset.id) elementCatalog[String(asset.id)] = asset; });
    const values = (shot.elements || []).map(asEntry);
    assets.forEach((asset) => { const id = String(asset.id || ""); if (id && !values.some((entry) => entry.ref === id)) values.push({ ref: id, role: "unassigned" }); });
    saveAttachmentList(shot.id, "elements", values);
  }

  function onReferencesPicked(urls, items) {
    if (stylePickingImages) { onStyleImagesPicked(urls); return; }
    const shotId = pendingAttachShotId || selectedId;
    pendingAttachShotId = "";
    const shot = findShot(shotId);
    if (!shot || !Array.isArray(urls) || !urls.length) return;
    const values = (shot.reference_assets || []).map(asEntry);
    urls.forEach((url) => { const path = String(url || "").trim(); if (path && !values.some((entry) => entry.ref === path)) values.push({ ref: path, role: "unassigned" }); });
    saveAttachmentList(shot.id, "reference_assets", values);
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
      refreshEditorState(payload.shot);
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
    if (changed) { selectedId = ""; stylesCache = null; stylesPromise = null; styleForm = null; }
    applyProjectGate();
  }

  function init() {
    if (!$("dsShotList") || !$("dsShotEditor")) return;
    bindRailToggle();
    watchPromptMode();
    window.addEventListener("asset-meta-change", (event) => onScopeChange(event.detail));
    onScopeChange(null);
  }

  window.directorShots = { reload: loadShots, select: selectShot, onFramePicked, setFirstFrame, onElementsPicked, onReferencesPicked, renderShot };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
