(function () {
  const STORAGE_KEYS = {
    client: "ai_api_asset_client",
    project: "ai_api_asset_project",
    shot: "ai_api_asset_shot",
    filename: "ai_api_asset_filename",
    projectId: "ai_api_asset_project_id",
  };

  const DEFAULT_STATE = {
    assetClient: "uncategorized",
    assetProject: "uncategorized",
    assetShot: "uncategorized",
    assetFilename: "",
    assetProjectId: "",
  };

  const bootstrap = window.__ASSET_META_BOOTSTRAP__ || {};
  let optionsCache = {
    clients: Array.isArray(bootstrap?.options?.clients) && bootstrap.options.clients.length ? bootstrap.options.clients : ["uncategorized"],
    projects: Array.isArray(bootstrap?.options?.projects) && bootstrap.options.projects.length ? bootstrap.options.projects : ["uncategorized"],
    shots: Array.isArray(bootstrap?.options?.shots) && bootstrap.options.shots.length ? bootstrap.options.shots : ["uncategorized"],
    filenames: Array.isArray(bootstrap?.options?.filenames) ? bootstrap.options.filenames : [],
  };
  let projectsCache = Array.isArray(bootstrap?.projects) ? bootstrap.projects : [];

  function getBar() {
    return document.getElementById("projectMetaBar");
  }

  function normalizeSelectValue(value, fallback) {
    const clean = String(value || "").trim();
    if (!clean || clean === "-") return fallback;
    return clean;
  }

  function normalizeFilename(value) {
    return String(value || "").trim();
  }

  function normalizeProjectId(value) {
    const clean = String(value ?? "").trim();
    return /^\d+$/.test(clean) ? clean : "";
  }

  function normalizeState(state) {
    return {
      assetClient: normalizeSelectValue(state?.assetClient, "-"),
      assetProject: normalizeSelectValue(state?.assetProject, "-"),
      assetShot: normalizeSelectValue(state?.assetShot, "-"),
      assetFilename: normalizeFilename(state?.assetFilename),
      assetProjectId: normalizeProjectId(state?.assetProjectId),
    };
  }

  function findProjectRecord(projectId) {
    const id = normalizeProjectId(projectId);
    if (!id) return null;
    return projectsCache.find((record) => String(record.id) === id) || null;
  }

  function mergeUniqueValues() {
    const seen = new Set();
    const merged = [];
    for (const group of arguments) {
      for (const rawValue of group || []) {
        const value = String(rawValue || "").trim();
        if (!value || seen.has(value)) continue;
        seen.add(value);
        merged.push(value);
      }
    }
    return merged;
  }

  function loadState() {
    const state = { ...DEFAULT_STATE };
    try {
      state.assetClient = localStorage.getItem(STORAGE_KEYS.client) || DEFAULT_STATE.assetClient;
      state.assetProject = localStorage.getItem(STORAGE_KEYS.project) || DEFAULT_STATE.assetProject;
      state.assetShot = localStorage.getItem(STORAGE_KEYS.shot) || DEFAULT_STATE.assetShot;
      state.assetFilename = localStorage.getItem(STORAGE_KEYS.filename) || DEFAULT_STATE.assetFilename;
      state.assetProjectId = localStorage.getItem(STORAGE_KEYS.projectId) || DEFAULT_STATE.assetProjectId;
    } catch (error) {}
    return state;
  }

  function saveState(state) {
    try {
      localStorage.setItem(STORAGE_KEYS.client, state.assetClient || "-");
      localStorage.setItem(STORAGE_KEYS.project, state.assetProject || "-");
      localStorage.setItem(STORAGE_KEYS.shot, state.assetShot || "-");
      localStorage.setItem(STORAGE_KEYS.filename, state.assetFilename || "");
      localStorage.setItem(STORAGE_KEYS.projectId, state.assetProjectId || "");
    } catch (error) {}
  }

  function getCurrentState() {
    const bar = getBar();
    if (!bar) return loadState();
    return normalizeState({
      assetClient: bar.querySelector("#projectMetaClient")?.value,
      assetProject: bar.querySelector("#projectMetaProject")?.value,
      assetShot: bar.querySelector("#projectMetaShot")?.value,
      assetFilename: bar.querySelector("#projectMetaFilename")?.value,
      assetProjectId: bar.querySelector("#projectMetaRecord")?.value,
    });
  }

  function renderProjectOptions(selectedId) {
    const select = getBar()?.querySelector("#projectMetaRecord");
    if (!select) return;
    const wanted = normalizeProjectId(selectedId);
    select.innerHTML = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "No project";
    select.appendChild(none);
    for (const record of projectsCache) {
      if (record.archived && String(record.id) !== wanted) continue;
      const option = document.createElement("option");
      option.value = String(record.id);
      option.textContent = record.client ? `${record.name} \u00b7 ${record.client}` : record.name;
      select.appendChild(option);
    }
    select.value = findProjectRecord(wanted) ? wanted : "";
  }

  function setInputOptions(input, datalist, values, selectedValue, fallback = "uncategorized") {
    if (!input) return;
    const merged = mergeUniqueValues(values, [selectedValue]);
    if (!merged.includes(fallback)) merged.unshift(fallback);
    if (datalist) datalist.innerHTML = "";
    for (const value of merged) {
      if (!datalist) continue;
      const option = document.createElement("option");
      option.value = value;
      datalist.appendChild(option);
    }
    input.value = merged.includes(selectedValue) ? selectedValue : fallback;
  }

  function setFilenameOptions(datalist, values) {
    if (!datalist) return;
    const merged = mergeUniqueValues(values);
    datalist.innerHTML = "";
    for (const value of merged) {
      const option = document.createElement("option");
      option.value = value;
      datalist.appendChild(option);
    }
  }

  function refreshControls(state) {
    const bar = getBar();
    if (!bar) return;
    const normalized = normalizeState(state || getCurrentState());
    setInputOptions(
      bar.querySelector("#projectMetaClient"),
      bar.querySelector("#projectMetaClients"),
      optionsCache.clients,
      normalized.assetClient,
        "uncategorized"
      );
    setInputOptions(
      bar.querySelector("#projectMetaProject"),
      bar.querySelector("#projectMetaProjects"),
      optionsCache.projects,
      normalized.assetProject,
        "uncategorized"
      );
    setInputOptions(
      bar.querySelector("#projectMetaShot"),
      bar.querySelector("#projectMetaShots"),
      optionsCache.shots,
      normalized.assetShot,
        "uncategorized"
      );
    const filenameInput = bar.querySelector("#projectMetaFilename");
    if (filenameInput) filenameInput.value = normalized.assetFilename;
    setFilenameOptions(bar.querySelector("#projectMetaFilenames"), mergeUniqueValues(optionsCache.filenames, [normalized.assetFilename]));
    bar.classList.toggle("project-meta-missing-filename", !normalized.assetFilename);
    renderProjectOptions(normalized.assetProjectId);
    bar.classList.toggle("project-meta-has-record", Boolean(findProjectRecord(normalized.assetProjectId)));
  }

  function getMenuValuesForInput(input) {
    if (!input) return [];
    if (input.id === "projectMetaClient") return mergeUniqueValues(optionsCache.clients);
    if (input.id === "projectMetaProject") return mergeUniqueValues(optionsCache.projects);
    if (input.id === "projectMetaShot") return mergeUniqueValues(optionsCache.shots);
    if (input.id === "projectMetaFilename") return mergeUniqueValues(optionsCache.filenames, [input.value]);
    if (input.id === "projectModalClient") return mergeUniqueValues(optionsCache.clients).filter((value) => value !== "uncategorized");
    return [];
  }

  function ensureMenu(input) {
    const field = input?.closest(".project-meta-field, .project-modal-suggest-field");
    if (!field) return null;
    let menu = field.querySelector(".project-meta-menu");
    if (!menu) {
      menu = document.createElement("div");
      menu.className = "project-meta-menu";
      field.appendChild(menu);
    }
    return menu;
  }

  function closeAllMenus() {
    document.querySelectorAll(".project-meta-menu").forEach((menu) => {
      menu.classList.remove("is-open");
      menu.innerHTML = "";
    });
    document.querySelectorAll(".project-meta-field").forEach((field) => {
      field.classList.remove("project-meta-field-open");
    });
  }

  function renderMenuForInput(input, { filterWithValue = true } = {}) {
    const menu = ensureMenu(input);
    const field = input?.closest(".project-meta-field, .project-modal-suggest-field");
    if (!menu || !field) return;
    const query = filterWithValue ? String(input.value || "").trim().toLowerCase() : "";
    const values = getMenuValuesForInput(input).filter((value) => {
      if (!query) return true;
      return String(value).toLowerCase().includes(query);
    });
    if (!values.length) {
      closeAllMenus();
      return;
    }
    menu.innerHTML = "";
    values.forEach((value) => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "project-meta-menu-option";
      option.textContent = value;
      option.addEventListener("mousedown", (event) => {
        event.preventDefault();
        input.value = value;
        input.dispatchEvent(new Event("change", { bubbles: true }));
        closeAllMenus();
      });
      menu.appendChild(option);
    });
    field.classList.add("project-meta-field-open");
    menu.classList.add("is-open");
  }

  async function fetchOptions() {
    try {
      const response = await fetch("/api/asset-metadata-options", {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) return;
      const payload = await response.json();
      if (!payload?.ok || !payload.options) return;
      optionsCache = {
          clients: Array.isArray(payload.options.clients) && payload.options.clients.length ? payload.options.clients : ["uncategorized"],
          projects: Array.isArray(payload.options.projects) && payload.options.projects.length ? payload.options.projects : ["uncategorized"],
          shots: Array.isArray(payload.options.shots) && payload.options.shots.length ? payload.options.shots : ["uncategorized"],
        filenames: Array.isArray(payload.options.filenames) ? payload.options.filenames : [],
      };
      refreshControls(getCurrentState());
    } catch (error) {}
  }

  async function fetchProjects() {
    try {
      const response = await fetch("/api/projects", { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      if (!payload?.ok || !Array.isArray(payload.projects)) return;
      projectsCache = payload.projects;
      renderProjectOptions(getCurrentState().assetProjectId);
    } catch (error) {}
  }

  let memorySaveTimer = null;
  function queueMemorySave() {
    window.clearTimeout(memorySaveTimer);
    memorySaveTimer = window.setTimeout(async () => {
      try {
        await fetch("/api/asset-metadata-memory", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(getCurrentState()),
        });
        fetchOptions();
      } catch (error) {}
    }, 250);
  }

  function emitState(state) {
    const normalized = normalizeState(state);
    window.dispatchEvent(new CustomEvent("asset-meta-change", {
      detail: { ...normalized, project: findProjectRecord(normalized.assetProjectId) },
    }));
  }

  function selectProjectRecord(projectId, options = {}) {
    const record = findProjectRecord(projectId);
    const state = getCurrentState();
    if (record) {
      state.assetProjectId = String(record.id);
      state.assetClient = record.assetClient || record.client || "uncategorized";
      state.assetProject = record.assetProject || record.name || "uncategorized";
    } else {
      state.assetProjectId = "";
    }
    applyState(state, { emit: true, persist: true });
    if (options.saveMemory !== false) queueMemorySave();
  }

  function applyState(state, { emit = true, persist = true } = {}) {
    const normalized = normalizeState(state);
    refreshControls(normalized);
    if (persist) saveState(normalized);
    if (emit) emitState(normalized);
  }

  function bindControls() {
    const bar = getBar();
    if (!bar) return;
    const client = bar.querySelector("#projectMetaClient");
    const project = bar.querySelector("#projectMetaProject");
    const shot = bar.querySelector("#projectMetaShot");
    const filename = bar.querySelector("#projectMetaFilename");

    const commit = () => {
      const state = getCurrentState();
      const record = findProjectRecord(state.assetProjectId);
      if (record && (state.assetClient !== (record.assetClient || record.client) || state.assetProject !== (record.assetProject || record.name))) {
        state.assetProjectId = "";
      }
      applyState(state, { emit: true, persist: true });
      queueMemorySave();
    };

    // Typing freely into Client or Project drops the record link; the text still files as before.
    const clearRecordLink = () => {
      const select = bar.querySelector("#projectMetaRecord");
      if (select && select.value) {
        select.value = "";
        bar.classList.remove("project-meta-has-record");
      }
    };
    [client, project].forEach((input) => {
      if (!input) return;
      input.addEventListener("input", clearRecordLink);
    });

    const recordSelect = bar.querySelector("#projectMetaRecord");
    if (recordSelect) {
      recordSelect.addEventListener("focus", () => { fetchProjects(); });
      recordSelect.addEventListener("change", () => {
        selectProjectRecord(recordSelect.value);
      });
    }

    const newBtn = bar.querySelector("#projectMetaNewBtn");
    if (newBtn) newBtn.addEventListener("click", () => openProjectModal());
    const editBtn = bar.querySelector("#projectMetaEditBtn");
    if (editBtn) editBtn.addEventListener("click", () => {
      const record = findProjectRecord(getCurrentState().assetProjectId);
      if (record) openProjectModal(record);
    });

    [client, project, shot].forEach((input) => {
      if (!input) return;
      input.addEventListener("focus", async () => {
        await fetchOptions();
        renderMenuForInput(input, { filterWithValue: false });
      });
      input.addEventListener("click", async () => {
        await fetchOptions();
        renderMenuForInput(input, { filterWithValue: false });
      });
      input.addEventListener("input", () => {
        renderMenuForInput(input, { filterWithValue: true });
      });
      input.addEventListener("change", commit);
      input.addEventListener("blur", () => {
        window.setTimeout(() => {
          closeAllMenus();
          commit();
        }, 120);
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          commit();
          closeAllMenus();
        }
        if (event.key === "Escape") {
          closeAllMenus();
        }
      });
    });

    if (filename) {
      filename.addEventListener("change", commit);
      filename.addEventListener("blur", commit);
      filename.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          commit();
        }
      });
    }

    document.addEventListener("pointerdown", (event) => {
      if (!event.target.closest(".project-meta-field, .project-modal-suggest-field")) {
        closeAllMenus();
      }
    });
  }

  // The New/Edit Project modal's Client input reuses the scope bar's suggestion menu.
  function bindModalClientSuggestions() {
    const input = document.getElementById("projectModalClient");
    if (!input) return;
    input.addEventListener("focus", async () => {
      await fetchOptions();
      renderMenuForInput(input, { filterWithValue: false });
    });
    input.addEventListener("click", () => {
      renderMenuForInput(input, { filterWithValue: false });
    });
    input.addEventListener("input", () => {
      renderMenuForInput(input, { filterWithValue: true });
    });
    input.addEventListener("blur", () => {
      window.setTimeout(closeAllMenus, 120);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeAllMenus();
    });
  }

  window.getAssetMetaSelection = function () {
    return getCurrentState();
  };

  window.collectAssetMetaPayload = function () {
    return { ...getCurrentState() };
  };

  window.validateAssetMetaSelection = function () {
    const state = getCurrentState();
    if (!String(state.assetFilename || "").trim()) {
      return "Filename is required before generating or upscaling.";
    }
    return "";
  };

  window.setAssetMetaSelection = function (patch, options = {}) {
    const next = { ...getCurrentState(), ...(patch || {}) };
    applyState(next, options);
    if (options.saveMemory !== false) queueMemorySave();
  };

  window.getSelectedProjectRecord = function () {
    return findProjectRecord(getCurrentState().assetProjectId);
  };

  window.selectProjectRecord = selectProjectRecord;

  // --- New Project modal --------------------------------------------------
  function getProjectModal() {
    return document.getElementById("projectModalOverlay");
  }

  function setProjectModalError(message) {
    const el = document.getElementById("projectModalError");
    if (el) el.textContent = message || "";
  }

  function syncProjectModalType() {
    const type = document.getElementById("projectModalType")?.value || "campaign";
    const film = document.getElementById("projectModalFilmFields");
    if (film) film.style.display = type === "film" ? "" : "none";
  }

  const FILM_SETTING_INPUTS = {
    format: "projectModalFormat",
    runtime: "projectModalRuntime",
    aspect_ratio: "projectModalAspectRatio",
    frame_rate: "projectModalFrameRate",
    register: "projectModalRegister",
    resolve_folder: "projectModalResolveFolder",
  };
  let projectModalEditingId = "";

  function openProjectModal(record = null) {
    const overlay = getProjectModal();
    if (!overlay) return;
    projectModalEditingId = record ? String(record.id) : "";
    const state = getCurrentState();
    const nameInput = document.getElementById("projectModalName");
    const clientInput = document.getElementById("projectModalClient");
    const typeSelect = document.getElementById("projectModalType");
    const settings = (record && record.settings) || {};
    if (nameInput) nameInput.value = record ? (record.name || "") : "";
    if (clientInput) {
      clientInput.value = record
        ? (record.client || "")
        : (state.assetClient && state.assetClient !== "uncategorized" ? state.assetClient : "");
    }
    if (typeSelect) typeSelect.value = record && record.type ? record.type : "campaign";
    Object.entries(FILM_SETTING_INPUTS).forEach(([key, id]) => {
      const el = document.getElementById(id);
      if (el) el.value = record && settings[key] != null ? String(settings[key]) : "";
    });
    const playbook = document.getElementById("projectModalPlaybook");
    if (playbook) playbook.value = record ? String(record.playbook || "") : "";
    const filmCode = document.getElementById("projectModalFilmCode");
    if (filmCode) filmCode.value = record ? String(record.film_code || "") : "";
    const playbookInfo = document.getElementById("projectModalPlaybookInfo");
    if (playbookInfo) playbookInfo.textContent = record && record.playbook ? `${String(record.playbook).length} characters` : "";
    const title = document.getElementById("projectModalTitle");
    if (title) title.textContent = record ? "Edit Project" : "New Project";
    const saveBtn = document.getElementById("projectModalSave");
    if (saveBtn) saveBtn.textContent = record ? "Save changes" : "Create project";
    const renameNote = document.getElementById("projectModalRenameNote");
    if (renameNote) renameNote.style.display = record ? "" : "none";
    setProjectModalError("");
    syncProjectModalType();
    overlay.style.display = "flex";
    window.setTimeout(() => nameInput?.focus(), 30);
  }

  function closeProjectModal() {
    const overlay = getProjectModal();
    if (overlay) overlay.style.display = "none";
  }

  async function saveProjectModal() {
    const saveBtn = document.getElementById("projectModalSave");
    const type = document.getElementById("projectModalType")?.value || "campaign";
    const body = {
      name: document.getElementById("projectModalName")?.value || "",
      client: document.getElementById("projectModalClient")?.value || "",
      type,
    };
    if (type === "film") {
      body.settings = {};
      Object.entries(FILM_SETTING_INPUTS).forEach(([key, id]) => {
        body.settings[key] = document.getElementById(id)?.value || "";
      });
      body.playbook = document.getElementById("projectModalPlaybook")?.value ?? "";   // as written
      body.film_code = (document.getElementById("projectModalFilmCode")?.value || "").trim().toUpperCase();
    }
    if (!String(body.name).trim()) {
      setProjectModalError("Project name is required.");
      return;
    }
    if (type === "film" && !body.film_code) {
      setProjectModalError("A film project needs a film code (e.g. NEX01). It is the first segment of every scene and shot ID.");
      return;
    }
    if (saveBtn) saveBtn.disabled = true;
    setProjectModalError("");
    const editing = projectModalEditingId;
    try {
      const response = await fetch(editing ? `/api/projects/${editing}` : "/api/projects", {
        method: editing ? "PATCH" : "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.ok || !payload.project) {
        throw new Error(payload?.error || (editing ? "Could not save the project." : "Could not create the project."));
      }
      projectsCache = [...projectsCache.filter((item) => item.id !== payload.project.id), payload.project];
      closeProjectModal();
      // Re-select so Client/Project text follows the (possibly renamed) record and listeners hear about it.
      selectProjectRecord(payload.project.id);
      fetchProjects();
    } catch (error) {
      setProjectModalError(error.message || (editing ? "Could not save the project." : "Could not create the project."));
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  function bindProjectModal() {
    const overlay = getProjectModal();
    if (!overlay) return;
    // The partial lives inside the nav; move the overlay to <body> so position:fixed is not clipped.
    if (overlay.parentElement !== document.body) document.body.appendChild(overlay);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closeProjectModal();
    });
    document.getElementById("projectModalClose")?.addEventListener("click", closeProjectModal);
    document.getElementById("projectModalSave")?.addEventListener("click", saveProjectModal);
    document.getElementById("projectModalType")?.addEventListener("change", syncProjectModalType);
    bindModalClientSuggestions();
    document.getElementById("projectModalPlaybookFile")?.addEventListener("change", (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        const area = document.getElementById("projectModalPlaybook");
        if (area) area.value = String(reader.result || "");
        const info = document.getElementById("projectModalPlaybookInfo");
        if (info) info.textContent = `${file.name} \u00b7 ${String(reader.result || "").length} characters`;
      };
      reader.readAsText(file);
      event.target.value = "";
    });
    overlay.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeProjectModal();
      if (event.key === "Enter" && event.target?.tagName === "INPUT") {
        event.preventDefault();
        saveProjectModal();
      }
    });
  }

  function initBar() {
    const bar = getBar();
    if (!bar) return;
    bindControls();
    bindProjectModal();
    const initial = loadState();
    // A stored record id wins over stored text so a renamed project stays in sync.
    const record = findProjectRecord(initial.assetProjectId);
    if (record) {
      initial.assetClient = record.assetClient || record.client || "uncategorized";
      initial.assetProject = record.assetProject || record.name || "uncategorized";
    } else {
      initial.assetProjectId = "";
    }
    applyState(initial, { emit: false, persist: true });
    fetchOptions().finally(() => {
      applyState(getCurrentState(), { emit: true, persist: false });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initBar, { once: true });
  } else {
    initBar();
  }
})();
