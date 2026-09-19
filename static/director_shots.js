(function () {
  // Director Studio - shot list (right panel) + shot editor (bottom panel, "Shot" pill).
  // Only active when the scope bar's project record is type = film.
  const STORAGE_KEYS = {
    rail: "ds_rail_mode",
    sections: "ds_shot_sections",
    selected: "ds_selected_shot",
    selectedScene: "ds_selected_scene",
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
      { key: "scene_prompt", label: "Scene prompt \u2014 the frame (carry it to the Generator for a first frame)", type: "textarea", rows: 4, wide: true, compile: true },
      { key: "prompt", label: "Motion prompt \u2014 sent to the render", type: "textarea", rows: 4, wide: true, compile: true },
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
  let scenes = [];                 // the project's scenes, in sort_order
  let selectedSceneId = "";        // "" = all shots
  let sceneDragId = "";
  let environmentCatalog = null;   // environment elements for the scene editor
  const castCache = {};            // sceneId -> cast[]
  let castForm = null;             // inline cast form state while open
  let castPickingImages = false;   // reference picker is open for the cast form
  let castDragId = "";
  const CAST_PREFIX = "cast:";
  const isCastRef = (ref) => String(ref || "").startsWith(CAST_PREFIX);
  const castIdOf = (ref) => String(ref || "").slice(CAST_PREFIX.length);
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
    const scenePill = $("promptModeSceneBtn");
    if (toggle) toggle.style.display = film ? "" : "none";
    if (pill) pill.style.display = film ? "" : "none";
    if (scenePill) scenePill.style.display = film ? "" : "none";
    syncStrip();
    if (!film) {
      shots = [];
      scenes = [];
      selectedId = "";
      selectedSceneId = "";
      setRailMode("library", { persist: false });
      const bar = $("promptBar");
      if (bar && (bar.dataset.promptMode === "shot" || bar.dataset.promptMode === "scene") && typeof window.setPromptMode === "function") {
        window.setPromptMode("creation");
      }
      renderEditor();
      renderSceneEditor();
      return;
    }
    const remembered = readStorage(STORAGE_KEYS.rail, "library");
    setRailMode(remembered === "shots" || remembered === "scenes" ? remembered : "library", { persist: false });
    loadShots();
  }

  // ---- right panel -------------------------------------------------------
  function setRailMode(mode, { persist = true } = {}) {
    railMode = (mode === "shots" || mode === "scenes") && isFilm() ? mode : "library";
    const shotsOn = railMode !== "library";   // Scenes and Shots both replace the Library block
    const list = $("dsShotList");
    if (list) list.style.display = railMode === "shots" ? "" : "none";
    const sceneList = $("dsSceneList");
    if (sceneList) sceneList.style.display = railMode === "scenes" ? "" : "none";
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

  async function loadScenes() {
    if (!isFilm()) return;
    try {
      const payload = await api(`/api/scenes?project_id=${encodeURIComponent(project.id)}`);
      scenes = Array.isArray(payload.scenes) ? payload.scenes : [];
      const remembered = readStorage(STORAGE_KEYS.selectedScene + ":" + project.id, "");
      if (!findScene(selectedSceneId)) selectedSceneId = findScene(remembered) ? remembered : "";
    } catch (error) {
      scenes = [];
    }
  }

  async function loadShots() {
    if (!isFilm()) return;
    try {
      await loadScenes();
      const payload = await api(`/api/shots?project_id=${encodeURIComponent(project.id)}`);
      shots = Array.isArray(payload.shots) ? payload.shots : [];
      const remembered = readStorage(STORAGE_KEYS.selected + ":" + project.id, "");
      if (!findShot(selectedId)) selectedId = findShot(remembered) ? remembered : "";
      renderList();
      renderEditor();
      renderSceneList();
      renderSceneEditor();
    } catch (error) {
      setStatusLine(error.message || "Could not load shots.", "error");
    }
  }

  const findScene = (id) => scenes.find((scene) => String(scene.id) === String(id)) || null;
  const sceneOf = (shot) => (shot && shot.scene_id ? findScene(shot.scene_id) : null);
  // The strip and the filtered list show this subset; ordering is always the project's.
  function visibleShots() {
    return selectedSceneId ? shots.filter((shot) => String(shot.scene_id || "") === String(selectedSceneId)) : shots;
  }

  function shotRowMarkup(shot) {
    return `
      <div class="ds-shot-row${String(shot.id) === String(selectedId) ? " is-selected" : ""}" draggable="true" data-id="${shot.id}" title="${esc(shot.slug)} \u00b7 ${esc(STATUS_LABELS[shot.status] || shot.status)}${shot.takes && shot.takes.approved ? " \u00b7 approved" : ""}">
        <span class="ds-shot-status" data-status="${esc(listStatus(shot))}" aria-label="${esc(listStatus(shot))}"></span>
        <span class="ds-shot-slug">${esc(shot.slug)}</span>
        <span class="ds-shot-scene">${esc(shot.scene || "")}</span>
        <span class="ds-shot-row-actions">
          <button type="button" class="ds-shot-mini" data-action="insert" title="Insert a shot after this one">+</button>
          <button type="button" class="ds-shot-mini ds-shot-mini-danger" data-action="delete" title="Delete this shot">&times;</button>
        </span>
      </div>`;
  }

  function renderList() {
    const list = $("dsShotList");
    if (!list) return;
    const selectedScene = findScene(selectedSceneId);
    let rows = "";
    if (selectedScene) {
      const mine = visibleShots();
      rows = `<div class="ds-group-head"><span class="ds-shot-slug">${esc(selectedScene.slug)}</span><span class="ds-group-name">${esc(selectedScene.name || "")}</span><button type="button" class="ds-shot-mini" id="dsShowAllShots" title="Show every shot">All</button></div>`
        + (mine.map(shotRowMarkup).join("") || '<div class="ds-shot-empty">No shots in this scene yet.</div>');
    } else {
      // Grouped under their scene, in project order; unassigned shots in their own group at the end.
      const groups = scenes.map((scene) => ({ scene, items: shots.filter((shot) => String(shot.scene_id || "") === String(scene.id)) }));
      const unassigned = shots.filter((shot) => !shot.scene_id || !findScene(shot.scene_id));
      rows = groups.map(({ scene, items }) => `<div class="ds-group-head" data-scene="${scene.id}" title="Open scene"><span class="ds-shot-slug">${esc(scene.slug)}</span><span class="ds-group-name">${esc(scene.name || "")}</span><span class="ds-shot-count">${items.length}</span></div>${items.map(shotRowMarkup).join("")}`).join("")
        + (unassigned.length || !scenes.length ? `<div class="ds-group-head is-unassigned"><span class="ds-group-name">${scenes.length ? "Unassigned" : "Shots"}</span><span class="ds-shot-count">${unassigned.length}</span></div>${unassigned.map(shotRowMarkup).join("")}` : "");
      if (!shots.length) rows += '<div class="ds-shot-empty">No shots yet. Add the first one.</div>';
    }
    list.innerHTML = `
      <div class="ds-shot-list-head">
        <span class="ds-shot-list-title">Shots <span class="ds-shot-count">${selectedScene ? visibleShots().length + "/" + shots.length : shots.length}</span></span>
        <button type="button" class="history-filter-toggle" id="dsAddShotBtn">+ Add shot</button>
      </div>
      <div class="ds-shot-status-line" id="dsShotStatusLine"></div>
      <div class="ds-shot-rows" id="dsShotRows">${rows}</div>`;
    $("dsAddShotBtn")?.addEventListener("click", () => createShot(selectedScene ? { scene_id: selectedScene.id, after_id: lastShotIdInScene(selectedScene.id) } : {}));
    $("dsShowAllShots")?.addEventListener("click", () => selectScene(""));
    list.querySelectorAll(".ds-group-head[data-scene]").forEach((head) => head.addEventListener("click", () => selectScene(head.dataset.scene, { openEditor: true })));
    bindRowEvents();
    renderStrip();
  }

  function lastShotIdInScene(sceneId) {
    const mine = shots.filter((shot) => String(shot.scene_id || "") === String(sceneId));
    return mine.length ? mine[mine.length - 1].id : undefined;
  }

  function bindRowEvents(containerId = "dsShotRows") {
    const container = $(containerId);
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
    const bar = $("promptBar");
    if (bar && bar.dataset.promptMode === "scene") renderSceneEditor();
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
    const scene = findScene(selectedSceneId);
    const cards = visibleShots().map((shot) => {
      const index = shots.indexOf(shot);
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
        <span class="ds-strip-title">${scene ? `${esc(scene.slug)} \u00b7 ${esc(scene.name || "")} <span class="ds-shot-count">${visibleShots().length}</span> <button type="button" class="ds-shot-mini" id="dsStripAllShots" title="Show the whole film">All shots</button>` : `Sequence <span class="ds-shot-count">${shots.length}</span> \u00b7 ${esc(project ? project.name : "")}`}</span>
        <span class="ds-strip-hint">Drag cards to reorder \u00b7 drop a gallery image on a card to set its first frame</span>
      </div>
      <div class="ds-strip-track" id="dsStripTrack">${cards || `<div class="ds-strip-empty">${scene ? "No shots in this scene yet." : "No shots yet. Add one in the Shots panel on the right."}</div>`}</div>`;
    $("dsStripAllShots")?.addEventListener("click", () => selectScene(""));
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
    const compile = field.compile
      ? `<div class="ds-compile-row"><button type="button" class="history-filter-toggle ds-compile-btn" data-compile="${field.key}" ${locked ? "disabled" : ""} title="${locked ? "Locked \u2014 unlock the field to compile into it" : "Have the agent write this from the playbook, scene, cast, references and camera"}">\u2726 Compile</button><span class="ds-compile-status" data-compile-status="${field.key}"></span></div><div class="ds-compile-review" data-compile-review="${field.key}" style="display:none"></div>`
      : "";
    return `<div class="ds-field${locked ? " is-locked" : ""}${field.wide ? " ds-field-wide" : ""}" data-field-wrap="${field.key}">
      ${field.type === "checkbox" ? "" : `<div class="ds-field-label"><span>${esc(field.label)}</span>${lock}</div>`}
      ${control}
      ${field.type === "checkbox" ? lock : ""}
      ${compile}
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
        <span class="ds-field ds-field-inline ds-shot-scene-pick${Array.isArray(shot.locked_fields) && shot.locked_fields.includes("scene_id") ? " is-locked" : ""}" data-field-wrap="scene_id" title="The scene this shot belongs to">
          <select class="ds-field-input" data-field="scene_id"><option value="">No scene</option>${scenes.map((scene) => `<option value="${scene.id}" ${String(scene.id) === String(shot.scene_id || "") ? "selected" : ""}>${esc(scene.slug)} \u00b7 ${esc(scene.name || "")}</option>`).join("")}</select>
          ${sceneOf(shot) ? `<button type="button" class="ds-shot-mini" id="dsOpenShotScene" title="Open this scene">Open</button>` : ""}
        </span>
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
    host.querySelectorAll("[data-compile]").forEach((button) => button.addEventListener("click", () => compileField(shot.id, button.dataset.compile)));
    ensureStyles().then((changed) => { if (changed && findShot(selectedId) === shot) renderEditor(); });
    ensureElementCatalog().then((changed) => { if (changed && findShot(selectedId) === shot) renderEditor(); });
    ensureVideoModels().then((changed) => { if (changed && findShot(selectedId) === shot) renderEditor(); });
    $("dsRenderBtn")?.addEventListener("click", () => renderShot(shot.id));
    $("dsOpenShotScene")?.addEventListener("click", () => selectScene(shot.scene_id, { openEditor: true }));
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
        <div class="ds-attach-actions">
          <button type="button" class="history-filter-toggle" id="dsStyleAddImages">+ Add images</button>
          <button type="button" class="history-filter-toggle ds-style-scan" id="dsStyleScanPick" title="Pick a reference and write the treatment from it">Scan reference\u2026</button>
          <label class="history-filter-toggle ds-style-scan" title="Drop or choose an image file to scan">Scan file<input type="file" id="dsStyleScanFile" accept="image/*" style="display:none"></label>
          <span class="ds-shot-status-line" id="dsStyleScanStatus"></span>
        </div>
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
    host.querySelector("#dsStyleScanPick")?.addEventListener("click", () => {
      syncStyleFormFromInputs();
      styleScanPending = true;
      stylePickingImages = true;
      if (typeof window.openLovedPicker === "function") window.openLovedPicker("shot-refs");
    });
    host.querySelector("#dsStyleScanFile")?.addEventListener("change", (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      syncStyleFormFromInputs();
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = String(reader.result || "");
        const data = dataUrl.split(",")[1] || "";
        scanStyleImage({ data, mime_type: file.type || "image/png", name: file.name });
      };
      reader.readAsDataURL(file);
    });
    const dropzone = host.querySelector("#dsStyleForm");
    if (dropzone) {
      dropzone.addEventListener("dragover", (event) => { event.preventDefault(); dropzone.classList.add("is-drop"); });
      dropzone.addEventListener("dragleave", () => dropzone.classList.remove("is-drop"));
      dropzone.addEventListener("drop", (event) => {
        dropzone.classList.remove("is-drop");
        const file = event.dataTransfer?.files && event.dataTransfer.files[0];
        if (file && file.type.startsWith("image/")) {
          event.preventDefault();
          syncStyleFormFromInputs();
          const reader = new FileReader();
          reader.onload = () => scanStyleImage({ data: String(reader.result || "").split(",")[1] || "", mime_type: file.type, name: file.name });
          reader.readAsDataURL(file);
          return;
        }
        const url = urlFromDataTransfer(event.dataTransfer);
        if (url) { event.preventDefault(); syncStyleFormFromInputs(); scanStyleImage({ asset_url: url }); }
      });
    }
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

  let styleScanPending = false;

  async function scanStyleImage(body) {
    if (!styleForm) return;
    const status = $("dsStyleScanStatus");
    if (status) { status.textContent = "Scanning\u2026 Gemini Vision is reading the look."; status.className = "ds-shot-status-line"; }
    try {
      const payload = await api("/api/styles/scan", { method: "POST", body });
      if (payload.text) styleForm.text = payload.text;   // scan output replaces the text; still editable
      if (payload.image_url && !styleForm.images.includes(payload.image_url)) styleForm.images.push(payload.image_url);
      renderEditor();
      const after = $("dsStyleScanStatus");
      if (after) { after.textContent = `Scanned \u00b7 $${Number(payload.usage?.cost_usd || 0).toFixed(5)}`; after.className = "ds-shot-status-line success"; }
    } catch (error) {
      const after = $("dsStyleScanStatus") || status;
      if (after) { after.textContent = error.message || "Scan failed."; after.className = "ds-shot-status-line error"; }
    }
  }

  function onStyleImagesPicked(urls) {
    stylePickingImages = false;
    if (castPickingImages) { onCastImagesPicked(urls); return; }
    if (!styleForm) return;
    if (styleScanPending) {
      styleScanPending = false;
      if (urls && urls[0]) scanStyleImage({ asset_url: urls[0] });
      return;
    }
    (urls || []).forEach((url) => { const path = String(url || "").trim(); if (path && !styleForm.images.includes(path)) styleForm.images.push(path); });
    renderEditor();
  }

  // ---- scenes: list (right panel) + editor (Scene pill) ------------------------
  function selectScene(id, { openEditor = false } = {}) {
    selectedSceneId = findScene(id) ? String(id) : "";
    if (project) writeStorage(STORAGE_KEYS.selectedScene + ":" + project.id, selectedSceneId);
    renderSceneList();
    renderList();
    renderSceneEditor();
    if (openEditor && selectedSceneId && typeof window.setPromptMode === "function") window.setPromptMode("scene");
  }

  function renderSceneList() {
    const list = $("dsSceneList");
    if (!list) return;
    const rows = scenes.map((scene, index) => `
      <div class="ds-shot-row ds-scene-row${String(scene.id) === String(selectedSceneId) ? " is-selected" : ""}" draggable="true" data-scene-id="${scene.id}" title="${esc(scene.slug)}">
        <span class="ds-scene-pos">${index + 1}</span>
        <span class="ds-shot-slug">${esc(scene.slug)}</span>
        <span class="ds-shot-scene">${esc(scene.name || "")}</span>
        <span class="ds-shot-count" title="Shots in this scene">${scene.shot_count || 0}</span>
        <span class="ds-shot-row-actions">
          <button type="button" class="ds-shot-mini" data-scene-action="insert" title="Insert a scene after this one">+</button>
          <button type="button" class="ds-shot-mini ds-shot-mini-danger" data-scene-action="delete" title="Delete this scene (its shots stay)">&times;</button>
        </span>
      </div>`).join("");
    list.innerHTML = `
      <div class="ds-shot-list-head">
        <span class="ds-shot-list-title">Scenes <span class="ds-shot-count">${scenes.length}</span></span>
        <button type="button" class="history-filter-toggle" id="dsAddSceneBtn">+ Add scene</button>
      </div>
      <div class="ds-shot-status-line" id="dsSceneStatusLine">${selectedSceneId ? `Filtering to ${esc((findScene(selectedSceneId) || {}).slug || "")} \u00b7 <button type="button" class="ds-shot-mini" id="dsSceneShowAll">All shots</button>` : ""}</div>
      <div class="ds-shot-rows" id="dsSceneRows">${rows || '<div class="ds-shot-empty">No scenes yet. Add the first one.</div>'}</div>`;
    $("dsAddSceneBtn")?.addEventListener("click", () => createScene());
    $("dsSceneShowAll")?.addEventListener("click", () => selectScene(""));
    const container = $("dsSceneRows");
    container?.querySelectorAll(".ds-scene-row").forEach((row) => {
      const id = row.dataset.sceneId;
      row.addEventListener("click", (event) => { if (!event.target.closest("[data-scene-action]")) selectScene(id, { openEditor: true }); });
      row.querySelector('[data-scene-action="insert"]').addEventListener("click", (event) => { event.stopPropagation(); createScene({ after_id: Number(id) }); });
      const del = row.querySelector('[data-scene-action="delete"]');
      del.addEventListener("click", (event) => {
        event.stopPropagation();
        if (del.dataset.armed === "1") { deleteScene(id); return; }
        del.dataset.armed = "1"; del.textContent = "Sure?"; del.classList.add("is-armed");
        window.setTimeout(() => { del.dataset.armed = ""; del.innerHTML = "&times;"; del.classList.remove("is-armed"); }, 2500);
      });
      row.addEventListener("dragstart", (event) => { sceneDragId = id; row.classList.add("is-dragging"); event.dataTransfer.effectAllowed = "move"; try { event.dataTransfer.setData("text/plain", "ds-scene:" + id); } catch (e) {} });
      row.addEventListener("dragend", () => { sceneDragId = ""; container.querySelectorAll(".ds-scene-row").forEach((el) => el.classList.remove("is-dragging", "drop-before", "drop-after")); });
      row.addEventListener("dragover", (event) => {
        if (!sceneDragId || sceneDragId === id) return;
        event.preventDefault(); event.dataTransfer.dropEffect = "move";
        const rect = row.getBoundingClientRect(); const before = event.clientY < rect.top + rect.height / 2;
        row.classList.toggle("drop-before", before); row.classList.toggle("drop-after", !before);
      });
      row.addEventListener("dragleave", () => row.classList.remove("drop-before", "drop-after"));
      row.addEventListener("drop", (event) => {
        if (!sceneDragId || sceneDragId === id) return;
        event.preventDefault();
        const rect = row.getBoundingClientRect(); const before = event.clientY < rect.top + rect.height / 2;
        const ids = scenes.map((scene) => String(scene.id)).filter((value) => value !== sceneDragId);
        const targetIndex = ids.indexOf(id);
        ids.splice(before ? targetIndex : targetIndex + 1, 0, sceneDragId);
        reorderScenes(ids);
      });
    });
  }

  async function createScene(extra = {}) {
    if (!isFilm()) return;
    try {
      const payload = await api("/api/scenes", { method: "POST", body: { project_id: project.id, ...extra } });
      await loadShots();
      selectScene(payload.scene.id, { openEditor: true });
      setSceneStatus(`${payload.scene.slug} added.`, "success");
    } catch (error) {
      setSceneStatus(error.message || "Could not add the scene.", "error");
    }
  }

  async function deleteScene(id) {
    try {
      const payload = await api(`/api/scenes/${id}`, { method: "DELETE" });
      if (String(selectedSceneId) === String(id)) selectedSceneId = "";
      await loadShots();
      setSceneStatus(`${payload.deleted} deleted \u00b7 ${payload.detached_shots} shot(s) kept and unassigned.`, "success");
    } catch (error) {
      setSceneStatus(error.message || "Could not delete the scene.", "error");
    }
  }

  async function reorderScenes(ids) {
    try {
      const payload = await api("/api/scenes/reorder", { method: "POST", body: { project_id: project.id, ids: ids.map(Number) } });
      scenes = Array.isArray(payload.scenes) ? payload.scenes : scenes;
      renderSceneList(); renderList(); renderSceneEditor();
    } catch (error) {
      setSceneStatus(error.message || "Could not reorder.", "error");
      renderSceneList();
    }
  }

  function setSceneStatus(message, tone = "") {
    const el = $("dsSceneStatusLine");
    if (!el) { setStatusLine(message, tone); return; }
    el.textContent = message || "";
    el.className = "ds-shot-status-line" + (tone ? " " + tone : "");
    if (message) window.setTimeout(() => { if (el.textContent === message) renderSceneList(); }, 3000);
  }

  const SCENE_SECTIONS = [
    { key: "brief", label: "Brief", field: "brief", rows: 4, hint: "What happens here, where, who is in it." },
    { key: "script", label: "Script", field: "script", rows: 16, hint: "The dialogue and action for the whole scene. Paste the pages.", script: true },
    { key: "environment", label: "Environment", environment: true },
    { key: "cast", label: "Cast", cast: true },
    { key: "shots", label: "Shots", shots: true },
    { key: "notes", label: "Notes", field: "note", rows: 3, hint: "Anything the crew should know." },
  ];

  function ensureEnvironmentCatalog() {
    if (environmentCatalog) return Promise.resolve(false);
    return fetch("/api/elements?category=locations&per_page=500", { credentials: "same-origin" })
      .then((response) => response.json())
      .then((payload) => { environmentCatalog = (payload.items || []).filter((item) => item.type === "environment" || true); return true; })
      .catch(() => { environmentCatalog = []; return false; });
  }

  function sceneLockMarkup(scene, field) {
    const locked = Array.isArray(scene.locked_fields) && scene.locked_fields.includes(field);
    return `<button type="button" class="ds-lock${locked ? " is-locked" : ""}" data-scene-lock="${field}" title="${locked ? "Locked by hand edit \u2014 click to unlock" : "Not locked"}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="${locked ? "M7 11V7a5 5 0 0 1 10 0v4" : "M7 11V7a5 5 0 0 1 9.9-1"}"/></svg></button>`;
  }

  function renderSceneEditor() {
    const host = $("dsSceneEditor");
    if (!host) return;
    if (!isFilm()) { host.innerHTML = ""; return; }
    const scene = findScene(selectedSceneId);
    if (!scene) {
      host.innerHTML = `<div class="ds-editor-empty">${scenes.length ? "Select a scene in the Scenes panel, or " : "No scenes yet. "}<button type="button" class="history-filter-toggle" id="dsSceneEmptyAdd">+ New scene</button></div>`;
      $("dsSceneEmptyAdd")?.addEventListener("click", () => createScene());
      return;
    }
    const mine = shots.filter((shot) => String(shot.scene_id || "") === String(scene.id));
    const env = scene.environment_id && environmentCatalog ? environmentCatalog.find((item) => String(item.id) === String(scene.environment_id)) : null;
    const lockedOf = (field) => Array.isArray(scene.locked_fields) && scene.locked_fields.includes(field);
    const sections = SCENE_SECTIONS.map((section) => {
      let body = "";
      let filled = false;
      if (section.field) {
        const value = scene[section.field] || "";
        filled = hasText(value);
        body = `<div class="ds-field ds-field-wide${lockedOf(section.field) ? " is-locked" : ""}" data-scene-wrap="${section.field}">
          <div class="ds-field-label"><span>${esc(section.label)}</span>${sceneLockMarkup(scene, section.field)}</div>
          <textarea class="ds-field-input${section.script ? " ds-script" : ""}" data-scene-field="${section.field}" rows="${section.rows}" placeholder="${esc(section.hint)}">${esc(value)}</textarea>
        </div>`;
      } else if (section.environment) {
        filled = Boolean(scene.environment_id);
        const options = (environmentCatalog || []).map((item) => `<option value="${esc(item.id)}" ${String(item.id) === String(scene.environment_id || "") ? "selected" : ""}>${esc(item.name || item.id)}</option>`).join("");
        body = `<div class="ds-scene-env${lockedOf("environment_id") ? " is-locked" : ""}" data-scene-wrap="environment_id">
          ${env ? `<img class="ds-scene-env-thumb" src="${esc(env.img_url)}" alt="" draggable="false">` : ""}
          <div class="ds-scene-env-body">
            ${env ? `<div class="ds-style-name">${esc(env.name)}</div><div class="ds-section-hint">${esc(env.description || "")}</div>` : (scene.environment_id ? `<div class="ds-section-hint">Environment "${esc(scene.environment_id)}" is not in the Elements library.</div>` : `<div class="ds-section-hint">No environment attached. Pick a location element scanned in Elements.</div>`)}
            <div class="ds-attach-actions"><select class="ds-field-input ds-style-select" data-scene-field="environment_id"><option value="">No environment</option>${options}</select>${sceneLockMarkup(scene, "environment_id")}</div>
          </div>
        </div>`;
      } else if (section.cast) {
        const cast = castCache[String(scene.id)];
        filled = Boolean(cast && cast.length);
        body = renderCastSection(scene, cast);
      } else if (section.shots) {
        filled = mine.length > 0;
        body = `<div class="ds-shot-rows ds-scene-shots" id="dsSceneShotRows">${mine.map(shotRowMarkup).join("") || '<div class="ds-shot-empty">No shots in this scene yet.</div>'}</div>
          <div class="ds-attach-actions"><button type="button" class="history-filter-toggle" id="dsSceneAddShot">+ Add shot to scene</button><span class="ds-attach-hint">Drag to reorder \u00b7 one ordering for the whole film</span></div>`;
      }
      return `<details class="ds-section${filled ? " has-content" : " is-empty"}${section.script ? " ds-section-script" : ""}" data-section="scene_${section.key}" ${isSectionOpen("scene_" + section.key) ? "open" : ""}>
        <summary>${esc(section.label)}${section.shots ? `<span class="ds-section-count">${mine.length}</span>` : (section.cast ? `<span class="ds-section-count">${(castCache[String(scene.id)] || []).length}</span>` : (filled ? '<span class="ds-section-check">\u2713</span>' : ""))}</summary>${body}</details>`;
    }).join("");
    const nameLocked = lockedOf("name");
    host.innerHTML = `
      <div class="ds-editor-head">
        <span class="ds-editor-slug" title="Fixed at creation; never changes">${esc(scene.slug)}</span>
        <div class="ds-field ds-field-inline${nameLocked ? " is-locked" : ""}" data-scene-wrap="name">
          <input class="ds-field-input" data-scene-field="name" type="text" placeholder="Scene name, as you would say it" value="${esc(scene.name || "")}" autocomplete="off">
          ${sceneLockMarkup(scene, "name")}
        </div>
        <span class="ds-editor-order">#${scenes.findIndex((item) => item.id === scene.id) + 1} of ${scenes.length} \u00b7 ${mine.length} shot${mine.length === 1 ? "" : "s"}</span>
        <span class="ds-editor-saved" id="dsSceneSaved"></span>
        <button type="button" class="history-filter-toggle" id="dsSceneShowShots" title="Filter the strip and the Shots panel to this scene">Show shots</button>
      </div>
      <div class="ds-sections ds-scene-sections">${sections}</div>`;
    host.querySelectorAll("details.ds-section").forEach((details) => details.addEventListener("toggle", () => setSectionOpen(details.dataset.section, details.open)));
    host.querySelectorAll("[data-scene-field]").forEach((input) => input.addEventListener("change", () => saveSceneField(scene.id, input.dataset.sceneField, input.value)));
    host.querySelectorAll("[data-scene-lock]").forEach((button) => button.addEventListener("click", () => toggleSceneLock(scene.id, button.dataset.sceneLock)));
    $("dsSceneAddShot")?.addEventListener("click", () => createShot({ scene_id: scene.id, after_id: lastShotIdInScene(scene.id) }));
    bindCastSection(scene);
    if (!castCache[String(scene.id)]) loadCast(scene.id);
    $("dsSceneShowShots")?.addEventListener("click", () => { selectScene(scene.id); if (typeof window.setPromptMode === "function") window.setPromptMode("shot"); });
    bindRowEvents("dsSceneShotRows");
    ensureEnvironmentCatalog().then((changed) => { if (changed && findScene(selectedSceneId) === scene) renderSceneEditor(); });
  }

  // ---- cast: one talent, dressed for this scene ------------------------------
  async function loadCast(sceneId) {
    try {
      const payload = await api(`/api/scenes/${sceneId}/cast`);
      castCache[String(sceneId)] = payload.cast || [];
    } catch (error) {
      castCache[String(sceneId)] = [];
    }
    if (String(selectedSceneId) === String(sceneId)) renderSceneEditor();
    if (findShot(selectedId) && String(findShot(selectedId).scene_id || "") === String(sceneId)) renderEditor();
  }

  function castThumbs(images, editable) {
    return (images || []).map((url, index) => `<span class="ds-style-thumb${editable ? " ds-style-thumb-edit" : ""}"><img src="${esc(url)}" alt="" draggable="false" title="${esc(String(url).split("/").pop())}">${editable ? `<button type="button" class="ds-chip-remove" data-cast-img-remove="${index}" title="Remove">&times;</button>` : ""}</span>`).join("");
  }

  function renderCastForm(scene) {
    const form = castForm;
    const el = form.element || {};
    return `<div class="ds-style-form ds-cast-form" id="dsCastForm">
      <div class="ds-cast-form-id">${el.img_url ? `<img class="ds-cast-id" src="${esc(el.img_url)}" alt="" draggable="false">` : '<span class="ds-cast-id ds-cast-id-missing"></span>'}<div><div class="ds-style-name">${esc(el.name || form.element_id || "")}</div><div class="ds-section-hint">Talent element \u00b7 the identity</div></div></div>
      <div class="ds-section-grid">
        <div class="ds-field"><div class="ds-field-label"><span>Character name</span></div><input class="ds-field-input" id="dsCastCharacter" type="text" value="${esc(form.character_name || "")}" placeholder="Who they are in the story" autocomplete="off"></div>
        <div class="ds-field"><div class="ds-field-label"><span>Look name</span></div><input class="ds-field-input" id="dsCastLook" type="text" value="${esc(form.look_name || "")}" placeholder="Trench, BW, Suit\u2026" autocomplete="off"></div>
        ${form.mode === "edit" ? `<div class="ds-field"><div class="ds-field-label"><span>Handle</span></div><input class="ds-field-input" id="dsCastHandle" type="text" value="${esc(form.handle || "")}" placeholder="@Character_Look" autocomplete="off"></div>` : ""}
        <div class="ds-field"><div class="ds-field-label"><span>Note</span></div><input class="ds-field-input" id="dsCastNote" type="text" value="${esc(form.note || "")}" autocomplete="off"></div>
      </div>
      <div class="ds-field ds-field-wide"><div class="ds-field-label"><span>Dressed reference sheet</span></div>
        <div class="ds-style-board">${castThumbs(form.images, true) || '<span class="ds-chip-empty">No images yet.</span>'}</div>
        <div class="ds-attach-actions"><button type="button" class="history-filter-toggle" id="dsCastAddImages">+ Add images</button></div>
      </div>
      <div class="ds-attach-actions">
        <button type="button" class="history-filter-toggle ds-style-save" id="dsCastSave">${form.mode === "edit" ? "Save cast member" : "Add to cast"}</button>
        <button type="button" class="history-filter-toggle" id="dsCastCancel">Cancel</button>
        <span class="ds-shot-status-line" id="dsCastFormError"></span>
      </div>
    </div>`;
  }

  function renderCastSection(scene, cast) {
    if (castForm && !castForm.pendingPick && String(castForm.scene_id) === String(scene.id)) return `<div class="ds-cast">${renderCastForm(scene)}</div>`;
    if (!cast) return '<div class="ds-cast"><div class="ds-chip-empty">Loading cast\u2026</div></div>';
    const cards = cast.map((member, index) => {
      const locked = (field) => Array.isArray(member.locked_fields) && member.locked_fields.includes(field);
      const missing = !member.element;
      return `<div class="ds-cast-card${missing ? " is-missing" : ""}" draggable="true" data-cast-id="${member.id}" data-index="${index}">
        ${missing ? '<span class="ds-cast-id ds-cast-id-missing" title="The talent element is gone from the library"></span>' : `<img class="ds-cast-id" src="${esc(member.element.img_url)}" alt="" draggable="false" title="${esc(member.element.name)}">`}
        <div class="ds-cast-body">
          <div class="ds-cast-head"><span class="ds-cast-pos">${index + 1}</span><span class="ds-style-name">${esc(member.character_name || member.element?.name || member.element_id)}</span>${member.look_name ? `<span class="ds-cast-look">${esc(member.look_name)}</span>` : ""}<code class="ds-cast-handle" title="Handle \u2014 the compiler will use this">${esc(member.handle || "")}</code>${missing ? '<span class="ds-style-offnote">element missing</span>' : ""}</div>
          <div class="ds-style-board">${castThumbs(member.images, false) || '<span class="ds-chip-empty">No dressed images yet.</span>'}</div>
          ${member.note ? `<div class="ds-section-hint">${esc(member.note)}</div>` : ""}
        </div>
        <div class="ds-cast-actions">
          <button type="button" class="ds-shot-mini" data-cast-action="edit" title="Edit">Edit</button>
          <button type="button" class="ds-shot-mini ds-shot-mini-danger" data-cast-action="delete" title="Remove from this scene">&times;</button>
          <button type="button" class="ds-lock${(locked("character_name") || locked("look_name") || locked("images")) ? " is-locked" : ""}" data-cast-lock="${member.id}" title="${(locked("character_name") || locked("look_name") || locked("images")) ? "Locked by hand edit \u2014 click to unlock" : "Not locked"}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="${(locked("character_name") || locked("look_name") || locked("images")) ? "M7 11V7a5 5 0 0 1 10 0v4" : "M7 11V7a5 5 0 0 1 9.9-1"}"/></svg></button>
        </div>
      </div>`;
    }).join("");
    return `<div class="ds-cast">
      <div class="ds-cast-list" id="dsCastList">${cards || '<div class="ds-chip-empty">No cast yet. Add who is in this scene, dressed for it.</div>'}</div>
      <div class="ds-attach-actions"><button type="button" class="history-filter-toggle" id="dsCastAdd">+ Add cast member</button><span class="ds-attach-hint">Pick a talent element, then name the character and the look. Drag to reorder.</span></div>
      <div class="ds-shot-status-line" id="dsCastStatus"></div>
    </div>`;
  }

  function setCastStatus(message, tone = "") {
    const el = $("dsCastStatus") || $("dsCastFormError");
    if (!el) { setSceneStatus(message, tone); return; }
    el.textContent = message || ""; el.className = "ds-shot-status-line" + (tone ? " " + tone : "");
  }

  function syncCastFormFromInputs() {
    if (!castForm) return;
    castForm.character_name = $("dsCastCharacter")?.value ?? castForm.character_name;
    castForm.look_name = $("dsCastLook")?.value ?? castForm.look_name;
    castForm.note = $("dsCastNote")?.value ?? castForm.note;
    if ($("dsCastHandle")) castForm.handle = $("dsCastHandle").value;
  }

  function bindCastSection(scene) {
    const host = $("dsSceneEditor");
    if (!host) return;
    host.querySelector("#dsCastAdd")?.addEventListener("click", () => {
      // Talent comes from the existing Elements modal, filtered to Characters, single pick.
      try { elCurrentCat = "characters"; } catch (e) {}
      castForm = { mode: "new", scene_id: scene.id, element_id: "", element: null, character_name: "", look_name: "", images: [], note: "", pendingPick: true };
      if (typeof window.openElements === "function") window.openElements("scene-cast");
    });
    host.querySelector("#dsCastCancel")?.addEventListener("click", () => { castForm = null; renderSceneEditor(); });
    host.querySelector("#dsCastAddImages")?.addEventListener("click", () => {
      syncCastFormFromInputs();
      castPickingImages = true; stylePickingImages = true;
      if (typeof window.openLovedPicker === "function") window.openLovedPicker("shot-refs");
    });
    host.querySelectorAll("[data-cast-img-remove]").forEach((button) => button.addEventListener("click", () => { syncCastFormFromInputs(); castForm.images.splice(Number(button.dataset.castImgRemove), 1); renderSceneEditor(); }));
    host.querySelector("#dsCastSave")?.addEventListener("click", async () => {
      syncCastFormFromInputs();
      const err = $("dsCastFormError");
      if (!castForm.character_name.trim()) { if (err) { err.textContent = "Give the character a name."; err.className = "ds-shot-status-line error"; } return; }
      const body = { element_id: castForm.element_id, character_name: castForm.character_name, look_name: castForm.look_name, note: castForm.note, images: castForm.images };
      if (castForm.mode === "edit") body.handle = castForm.handle;
      try {
        const payload = castForm.mode === "edit"
          ? await api(`/api/cast/${castForm.id}`, { method: "PATCH", body })
          : await api(`/api/scenes/${scene.id}/cast`, { method: "POST", body });
        castForm = null;
        await loadCast(scene.id);
        if (payload.handle_note) setCastStatus(payload.handle_note, "error");
        else setCastStatus(`${payload.cast_member.handle} saved.`, "success");
      } catch (error) {
        if (err) { err.textContent = error.message || "Could not save."; err.className = "ds-shot-status-line error"; }
      }
    });
    const list = host.querySelector("#dsCastList");
    if (!list) return;
    list.querySelectorAll(".ds-cast-card").forEach((card) => {
      const id = card.dataset.castId;
      const member = (castCache[String(scene.id)] || []).find((m) => String(m.id) === id);
      card.querySelector('[data-cast-action="edit"]')?.addEventListener("click", () => {
        castForm = { mode: "edit", id: member.id, scene_id: scene.id, element_id: member.element_id, element: member.element, character_name: member.character_name, look_name: member.look_name, handle: member.handle, images: (member.images || []).slice(), note: member.note || "" };
        renderSceneEditor();
      });
      const del = card.querySelector('[data-cast-action="delete"]');
      del?.addEventListener("click", async () => {
        if (del.dataset.armed !== "1") { del.dataset.armed = "1"; del.textContent = "Sure?"; del.classList.add("is-armed"); window.setTimeout(() => { del.dataset.armed = ""; del.innerHTML = "&times;"; del.classList.remove("is-armed"); }, 2500); return; }
        try { await api(`/api/cast/${id}`, { method: "DELETE" }); await loadCast(scene.id); } catch (error) { setCastStatus(error.message || "Could not remove.", "error"); }
      });
      card.querySelector("[data-cast-lock]")?.addEventListener("click", async () => {
        const locked = Array.isArray(member.locked_fields) ? member.locked_fields : [];
        const next = locked.length ? [] : ["character_name", "look_name", "images"];
        try { await api(`/api/cast/${id}`, { method: "PATCH", body: { locked_fields: next } }); await loadCast(scene.id); } catch (error) { setCastStatus(error.message || "Could not change the lock.", "error"); }
      });
      card.addEventListener("dragstart", (event) => { castDragId = id; card.classList.add("is-dragging"); event.dataTransfer.effectAllowed = "move"; try { event.dataTransfer.setData("text/plain", "ds-cast:" + id); } catch (e) {} });
      card.addEventListener("dragend", () => { castDragId = ""; list.querySelectorAll(".ds-cast-card").forEach((el) => el.classList.remove("is-dragging", "drop-before", "drop-after")); });
      card.addEventListener("dragover", (event) => {
        if (!castDragId || castDragId === id) return;
        event.preventDefault(); event.dataTransfer.dropEffect = "move";
        const rect = card.getBoundingClientRect(); const before = event.clientY < rect.top + rect.height / 2;
        card.classList.toggle("drop-before", before); card.classList.toggle("drop-after", !before);
      });
      card.addEventListener("dragleave", () => card.classList.remove("drop-before", "drop-after"));
      card.addEventListener("drop", async (event) => {
        if (!castDragId || castDragId === id) return;
        event.preventDefault();
        const rect = card.getBoundingClientRect(); const before = event.clientY < rect.top + rect.height / 2;
        const ids = (castCache[String(scene.id)] || []).map((m) => String(m.id)).filter((v) => v !== castDragId);
        const target = ids.indexOf(id); ids.splice(before ? target : target + 1, 0, castDragId);
        try { const payload = await api(`/api/scenes/${scene.id}/cast/reorder`, { method: "POST", body: { ids: ids.map(Number) } }); castCache[String(scene.id)] = payload.cast || []; renderSceneEditor(); }
        catch (error) { setCastStatus(error.message || "Could not reorder.", "error"); }
      });
    });
  }

  function onCastElementPicked(asset) {
    if (!castForm || !castForm.pendingPick) return;
    castForm.pendingPick = false;
    castForm.element_id = String(asset.id || "");
    castForm.element = { id: asset.id, name: asset.name, img_url: asset.img_url };
    if (!castForm.character_name) castForm.character_name = String(asset.name || "").split(" ")[0] || "";
    renderSceneEditor();
    window.setTimeout(() => $("dsCastLook")?.focus(), 50);
  }

  function onCastImagesPicked(urls) {
    castPickingImages = false;
    if (!castForm) return;
    (urls || []).forEach((url) => { const path = String(url || "").trim(); if (path && !castForm.images.includes(path)) castForm.images.push(path); });
    renderSceneEditor();
  }

  async function saveSceneField(sceneId, field, value) {
    const scene = findScene(sceneId);
    if (!scene) return;
    const locked = Array.isArray(scene.locked_fields) ? scene.locked_fields.slice() : [];
    if (!locked.includes(field)) locked.push(field);
    try {
      const payload = await api(`/api/scenes/${sceneId}`, { method: "PATCH", body: { [field]: value, locked_fields: locked } });
      const index = scenes.findIndex((item) => item.id === payload.scene.id);
      if (index >= 0) scenes[index] = payload.scene;
      const wrap = document.querySelector(`#dsSceneEditor [data-scene-wrap="${field}"]`);
      if (wrap) { wrap.classList.add("is-locked"); const btn = wrap.querySelector("[data-scene-lock]"); if (btn) { btn.classList.add("is-locked"); btn.querySelector("path")?.setAttribute("d", "M7 11V7a5 5 0 0 1 10 0v4"); } }
      if (field === "name") { renderSceneList(); renderList(); }
      if (field === "environment_id") renderSceneEditor();
      const el = $("dsSceneSaved"); if (el) { el.textContent = "Saved"; window.setTimeout(() => { if (el.textContent === "Saved") el.textContent = ""; }, 1800); }
    } catch (error) {
      setSceneStatus(error.message || "Could not save.", "error");
    }
  }

  async function toggleSceneLock(sceneId, field) {
    const scene = findScene(sceneId);
    if (!scene) return;
    const locked = Array.isArray(scene.locked_fields) ? scene.locked_fields.slice() : [];
    const next = locked.includes(field) ? locked.filter((key) => key !== field) : [...locked, field];
    try {
      const payload = await api(`/api/scenes/${sceneId}`, { method: "PATCH", body: { locked_fields: next } });
      const index = scenes.findIndex((item) => item.id === payload.scene.id);
      if (index >= 0) scenes[index] = payload.scene;
      renderSceneEditor();
    } catch (error) {
      setSceneStatus(error.message || "Could not change the lock.", "error");
    }
  }

  // ---- prompt compiler (Task 15): proposal -> review -> accept writes + locks ----
  const compileProposals = {};   // `${shotId}:${field}` -> last proposal

  async function compileField(shotId, field) {
    const shot = findShot(shotId);
    const status = document.querySelector(`[data-compile-status="${field}"]`);
    const review = document.querySelector(`[data-compile-review="${field}"]`);
    const button = document.querySelector(`[data-compile="${field}"]`);
    if (!shot || !status || !review) return;
    if (Array.isArray(shot.locked_fields) && shot.locked_fields.includes(field)) {
      status.textContent = "Locked. Unlock the field to compile into it."; status.className = "ds-compile-status error"; return;
    }
    if (button) { button.disabled = true; button.textContent = "Compiling\u2026"; }
    status.textContent = "Assembling the playbook, scene, cast, references and camera\u2026"; status.className = "ds-compile-status";
    try {
      const payload = await api(`/api/shots/${shotId}/compile`, { method: "POST", body: { field } });
      compileProposals[`${shotId}:${field}`] = payload;
      const usage = payload.usage || {};
      const tokens = usage.prompt_tokens != null ? `${Number(usage.prompt_tokens).toLocaleString()} in / ${Number(usage.completion_tokens || 0).toLocaleString()} out tokens` : `~${Number(payload.system_tokens || 0).toLocaleString()} tokens sent`;
      status.textContent = `Proposal ready \u00b7 ${tokens} \u00b7 ${payload.slots.length} reference(s) numbered`; status.className = "ds-compile-status success";
      renderCompileReview(shot, field, payload);
    } catch (error) {
      status.textContent = error.message || "Compile failed."; status.className = "ds-compile-status error";
    } finally {
      if (button) { button.disabled = false; button.textContent = "\u2726 Compile"; }
    }
  }

  function renderCompileReview(shot, field, payload) {
    const review = document.querySelector(`[data-compile-review="${field}"]`);
    if (!review) return;
    const current = String(shot[field] || "");
    review.style.display = "";
    review.innerHTML = `
      <div class="ds-compile-cols">
        <div class="ds-compile-col"><div class="ds-field-label"><span>Current</span></div><div class="ds-compile-text is-current">${current ? esc(current) : "<em>(empty)</em>"}</div></div>
        <div class="ds-compile-col"><div class="ds-field-label"><span>Proposed</span></div><div class="ds-compile-text is-proposed">${esc(payload.proposal)}</div></div>
      </div>
      <div class="ds-attach-actions">
        <button type="button" class="history-filter-toggle ds-style-save" data-compile-accept="${field}">Accept \u2014 write and lock</button>
        <button type="button" class="history-filter-toggle" data-compile-recompile="${field}">Recompile</button>
        <button type="button" class="history-filter-toggle" data-compile-discard="${field}">Discard</button>
        <button type="button" class="ds-shot-mini" data-compile-context="${field}" title="What the compiler saw">context</button>
      </div>
      <pre class="ds-compile-context" data-compile-context-box="${field}" style="display:none">${esc(payload.context)}</pre>`;
    review.querySelector("[data-compile-accept]").addEventListener("click", () => acceptCompile(shot.id, field));
    review.querySelector("[data-compile-recompile]").addEventListener("click", () => compileField(shot.id, field));
    review.querySelector("[data-compile-discard]").addEventListener("click", () => { delete compileProposals[`${shot.id}:${field}`]; review.style.display = "none"; review.innerHTML = ""; const st = document.querySelector(`[data-compile-status="${field}"]`); if (st) st.textContent = "Discarded. Nothing was written."; });
    review.querySelector("[data-compile-context]").addEventListener("click", () => { const box = review.querySelector("[data-compile-context-box]"); box.style.display = box.style.display === "none" ? "" : "none"; });
  }

  async function acceptCompile(shotId, field) {
    const payload = compileProposals[`${shotId}:${field}`];
    const shot = findShot(shotId);
    if (!payload || !shot) return;
    const locked = Array.isArray(shot.locked_fields) ? shot.locked_fields.slice() : [];
    if (!locked.includes(field)) locked.push(field);   // accepted by the user -> locked, like any hand edit
    try {
      const result = await api(`/api/shots/${shotId}`, { method: "PATCH", body: { [field]: payload.proposal, locked_fields: locked } });
      const index = shots.findIndex((item) => item.id === result.shot.id);
      if (index >= 0) shots[index] = result.shot;
      delete compileProposals[`${shotId}:${field}`];
      renderList();
      renderEditor();
      flashSaved("Written and locked");
    } catch (error) {
      setStatusLine(error.message || "Could not write the prompt.", "error");
    }
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
    if (listKey === "elements" && isCastRef(value)) {
      const shot = findShot(selectedId);
      const cast = shot ? (castCache[String(shot.scene_id || "")] || []) : [];
      const member = cast.find((m) => String(m.id) === castIdOf(value));
      if (!member) return { url: "", label: `cast #${castIdOf(value)}`, missing: Boolean(shot && castCache[String(shot.scene_id || "")]), cast: true };
      const url = (member.images && member.images[0]) || (member.element && member.element.img_url) || "";
      return { url, label: `${member.handle || "@" + member.character_name} \u00b7 ${member.look_name || "look"}`, missing: false, cast: true, member };
    }
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
      return `<div class="ds-chip${thumb.missing ? " is-missing" : ""}${thumb.cast ? " is-cast" : ""} role-${role}" draggable="true" data-list="${group.list}" data-index="${index}" title="${esc(thumb.label)}${thumb.missing ? " (not found)" : ""}${thumb.member ? ` \u00b7 ${thumb.member.images.length} dressed image(s)` : ""}">
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
        ${group.list === "elements" && shot.scene_id ? `<select class="ds-field-input ds-style-select" id="dsAttachCast"><option value="">+ Add cast\u2026</option>${(castCache[String(shot.scene_id)] || []).map((m) => `<option value="${m.id}">${esc(m.handle || m.character_name)} \u00b7 ${esc(m.look_name || "")}</option>`).join("")}</select>` : ""}
        <button type="button" class="ds-lock${locked ? " is-locked" : ""}" data-lock="${group.list}" title="${locked ? "Locked by hand edit \u2014 click to unlock" : "Not locked"}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="${locked ? "M7 11V7a5 5 0 0 1 10 0v4" : "M7 11V7a5 5 0 0 1 9.9-1"}"/></svg></button>
        <span class="ds-attach-hint">Drag to reorder \u00b7 order is the reference order</span>
      </div>
    </div>`;
  }

  function bindAttachmentLists(shot) {
    if (shot.scene_id && !castCache[String(shot.scene_id)]) loadCast(shot.scene_id);
    document.querySelector("#dsShotEditor #dsAttachCast")?.addEventListener("change", (event) => {
      const id = event.target.value;
      if (!id) return;
      const values = (shot.elements || []).map(asEntry);
      const ref = CAST_PREFIX + id;
      if (!values.some((entry) => entry.ref === ref)) values.push({ ref, role: "character" });
      saveAttachmentList(shot.id, "elements", values);
    });
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
      if (fieldKey === "scene_id") { await loadScenes(); renderList(); renderSceneList(); renderEditor(); renderSceneEditor(); }
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

  window.directorShots = { reload: loadShots, select: selectShot, selectScene, onFramePicked, setFirstFrame, onElementsPicked, onReferencesPicked, onCastElementPicked, renderShot };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
