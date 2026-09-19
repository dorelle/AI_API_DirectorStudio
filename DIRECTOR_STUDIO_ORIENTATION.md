# Director Studio — Orientation Report (Task 01, Part A)

Read-only survey of the AI API Studio codebase as it exists in the working copy on
2026-09-19. No files were modified to produce this. Line numbers refer to the working copy
(which includes ~3,000 lines of uncommitted local changes on top of upstream `7b988e3`).

Corrections to the task header, found while reading:

- Local path is `C:\Users\dorel\.claude_project_folder\AI_API`, not `E:\Code\comfy_app\AI_API_Studio`.
- The app runs on **port 8000**, not 5000 (`nbs.py:11346`, `app.run(debug=True, port=8000)`).
- `origin` in the local checkout still points at upstream `pixteur/AI_API_Studio`. The fork
  `dorelle/AI_API_Studio` exists on GitHub at the same commit as upstream (`7b988e3`) with
  nothing pushed to it yet.

---

## 1. Application structure

### `nbs.py` vs `app.py`

- **`nbs.py`** (11,352 lines) is the entire application: bootstrap, config, pricing tables,
  model catalogs, asset helpers, SQLite, all routes, all provider calls, and `__main__`.
  It is the entrypoint (`python nbs.py`).
- **`app.py`** (5 lines) is a compatibility shim: `runpy.run_path("nbs.py", run_name="__main__")`
  so `python app.py` still works (`app.py:1-5`).

### One Flask app, no blueprints

- A single `app = Flask(__name__)` at `nbs.py:77`. `grep Blueprint nbs.py` → no matches.
- Auth is a hardcoded dict `USERS = {"admin": "banana2024"}` (`nbs.py:85-87`) and a
  `login_required` decorator that checks `session["user"]` (`nbs.py:4072-4078`).
- Dependencies are auto-installed by `_bootstrap()` at `nbs.py:18-45`: `flask`, `Pillow`,
  `requests`, `fal-client`. `fal_catalog.py` is a local module imported at `nbs.py:75`
  (currently **untracked** in git — see Risks).

### Every route

Page routes (render a template):

| Route | Method | Line | What it does |
| --- | --- | --- | --- |
| `/` | GET | 4084 | Redirect to `/index` if logged in, else `/login` |
| `/login` | GET, POST | 4091 | Renders `login.html`; POST checks `USERS` dict, sets `session["user"]` |
| `/logout` | GET | 4104 | `session.clear()`, redirect to login |
| `/index` | GET | 4113 | Generator page; renders `index.html` with model tables, pricing, full generation history |
| `/settings` | GET | 4137 | Renders `settings.html` with masked API keys, stats, fal catalog status |
| `/credits` | GET | 4227 | Renders `credits.html` |
| `/workbench` | GET | 4236 | Renders `workbench.html` with `fetch_task_templates()` and `get_workbench_report()` |
| `/reports` | GET | 4251 | Renders `reports.html` with `get_workbench_report()` |
| `/images` | GET | 5880 | Assets tab; `?kind=history|loved|references|videos`; renders `asset_gallery.html` |
| `/loved`, `/references`, `/history` | GET | 5889, 5895, 5901 | Redirects to `/images?kind=…` |

Static file serving (files under `Image_assets/` and `Elements/`):

| Route | Line | Serves |
| --- | --- | --- |
| `/loved/<path:asset_relpath>` | 5958 | `Image_assets/loved/…` |
| `/reference-archive/<date>/<filename>` | 5966 | `Image_assets/reference_archive/<date>/…` |
| `/reference-mask/<date>/<filename>` | 5973 | `Image_assets/reference_masks/…` |
| `/reference-render/<date>/<filename>` | 5980 | `Image_assets/reference_renders/…` |
| `/generations/<path:asset_relpath>` | 5987 | `Image_assets/generations/…` |
| `/videos/<path:asset_relpath>` | 5995 | `Image_assets/videos/…` |
| `/edit-sessions/<path:asset_relpath>` | 6003 | `Image_assets/edit_sessions/…` |
| `/elements/<path:filepath>` | 7649 | `Elements/…` (talent images) |

JSON API routes:

| Route | Method | Line | What it does |
| --- | --- | --- | --- |
| `/api/catalog/status` | GET | 4169 | fal model catalog freshness |
| `/api/catalog/refresh` | POST | 4175 | Re-fetch fal OpenAPI schemas → `models_video.generated.json` |
| `/api/catalog/probe` | POST | 4187 | Probe one fal endpoint id |
| `/api/catalog/add` | POST | 4205 | Append an endpoint to `fal_endpoints.json` |
| `/api/edit-session` | GET | 6474 | Load an edit-session JSON |
| `/api/edit-session` | POST | 6536 | Save an edit-session JSON |
| `/api/edit-session/sam3-image` | POST | 6598 | SAM3 segmentation on an image (fal) |
| `/api/edit-session/sam3-video` | POST | 6803 | SAM3 segmentation on a video (fal) |
| `/api/generations/open-folder` | POST | 6949 | Open the OS folder for a generation |
| `/api/assets/open-folder` | POST | 6972 | Open the OS folder for any asset kind |
| `/api/loved/<relpath>` | DELETE | 6996 | Delete a loved asset |
| `/api/reference-archive/<date>/<fn>` | DELETE | 7027 | Delete an archived reference |
| `/api/reference-mask/<date>/<fn>` | GET, POST | 7085 | Read/write an alpha mask for a reference |
| `/api/loved-list` | GET | 7127 | Loved images for the ref picker |
| `/api/asset-metadata-options` | GET | 7518 | Scope-bar option lists + records |
| `/api/asset-metadata-memory` | POST | 7528 | Upsert scope values into `config.json` memory |
| `/api/reference-archive-list` | GET | 7539 | List archived references |
| `/api/reference-archive-payload/<date>/<fn>` | GET | 7562 | Base64 payload of an archived reference |
| `/api/asset-gallery/<kind>` | GET | 7617 | Records for an Assets tab kind |
| `/api/import-ref-image` | POST | 7625 | Import a URL/file as a reference |
| `/api/elements` | GET | 7659 | Elements catalog (characters/locations/props) with filters |
| `/api/elements/toggle-favorite` | POST | 7817 | Toggle `is_favorite` on a talent |
| `/api/elements/migrate-catalog` | POST | 7862 | Legacy `catalog.json` → per-talent JSON files |
| `/api/elements/analyze-image` | POST | 7956 | Gemini vision → talent descriptor JSON |
| `/api/references/describe` | POST | 8126 | Gemini vision → describe a reference image |
| `/api/elements/save-talent` | POST | 8267 | Save new talent image + JSON |
| `/api/save-config` | POST | 8360 | Save API keys to `config.json` |
| `/api/verify-fal-key`, `/api/verify-seedream-key` | POST | 8383-8384 | Verify fal key (two routes, one handler) |
| `/api/verify-byteplus-key` | POST | 8415 | Verify BytePlus key |
| `/api/verify-luma-key` | POST | 8447 | Verify Luma key |
| `/api/verify-kling-token` | POST | 8469 | Verify Kling token |
| `/api/verify-key` | POST | 8492 | Verify Gemini key |
| `/api/generate` | POST | 10913 | Synchronous image generation |
| `/api/jobs/generate` | POST | 10932 | Async image generation (thread + in-memory job) |
| `/api/jobs/edit` | POST | 10943 | Async edit job |
| `/api/jobs/video` | POST | 10954 | Async video job |
| `/api/upscale` | POST | 10965 | Synchronous upscale |
| `/api/jobs/upscale` | POST | 10986 | Async upscale |
| `/api/jobs/<job_id>` | GET | 10997 | Poll async job status |
| `/api/workbench/run` | POST | 11006 | Execute a saved task run through the generator |
| `/api/generations` | GET | 11049 | List generation records |
| `/api/videos` | GET | 11075 | List video records |
| `/api/videos/<relpath>` | DELETE | 11097 | Delete a video |
| `/api/generations/<relpath>` | DELETE | 11150 | Delete a generation |
| `/api/publish` | POST | 11194 | Copy an image into `loved/` ("love" it) |
| `/api/workbench/templates` | GET | 11249 | Task templates as JSON |
| `/api/workbench/report` | GET | 11255 | Workbench report JSON |
| `/api/reports/overview` | GET | 11263 | Same report JSON |
| `/api/workbench/plan` | POST | 11267 | Build + save a prompt plan (creates a `task_runs` row) |
| `/api/stats` | GET | 11284 | Stats from `config.json` |
| `/api/reset-stats` | POST | 11291 | Reset stats |
| `/api/models-info` | GET | 11308 | Image model tables |
| `/api/video-models-info` | GET | 11314 | Video model tables |

Also: one `@app.context_processor` at `nbs.py:7495` (`inject_asset_metadata_bootstrap`)
that injects `asset_meta_bootstrap` into **every** template render.

### Templates → routes

| Template | Rendered by |
| --- | --- |
| `login.html` | `/login` (`nbs.py:4101`) |
| `index.html` (15,512 lines) | `/index` (`nbs.py:4123`) |
| `settings.html` | `/settings` (`nbs.py:4147`) |
| `credits.html` | `/credits` (`nbs.py:4230`) |
| `workbench.html` | `/workbench` (`nbs.py:4239`) |
| `reports.html` | `/reports` (`nbs.py:4254`) |
| `asset_gallery.html` | `/images` (`nbs.py:5886`) |
| `_project_meta_bar.html` | `{% include %}`-ed by all 8 page templates (the scope bar) |
| `loved.html` | **No route renders it.** Dead template. |
| `published.html` | **No route renders it.** Dead template. |

---

## 2. Persistence

### SQLite

- File: `studio.db` at repo root — `STUDIO_DB_FILE = os.path.join(BASE_DIR, "studio.db")`
  (`nbs.py:102`). Gitignored.
- Created by `init_studio_db()` (`nbs.py:2028-2106`), called from `__main__`
  (`nbs.py:11332`) and defensively at the top of every task-run helper
  (`fetch_task_templates`, `save_task_run`, `get_task_run`, `update_task_run_after_generation`,
  `get_workbench_report` — `nbs.py:2109, 2302, 2360, 2382, 2429`).
- Connection: `get_db_connection()` at `nbs.py:2004-2007`, `sqlite3.connect` with
  `row_factory = sqlite3.Row`. Opened and closed per call; no pooling, no context manager.

### Full live schema (dumped from `studio.db` with `sqlite_master`)

```sql
CREATE TABLE task_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    default_provider TEXT NOT NULL,
    default_workflow TEXT NOT NULL,
    default_model TEXT NOT NULL,
    default_aspect_ratio TEXT NOT NULL,
    default_image_size TEXT NOT NULL,
    default_temperature REAL NOT NULL,
    prompt_scaffold TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uuid TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL,
    client_name TEXT,
    project_name TEXT,
    task_slug TEXT NOT NULL,
    task_name TEXT NOT NULL,
    objective TEXT NOT NULL,
    channels_json TEXT NOT NULL,
    vibe TEXT,
    subject_summary TEXT,
    constraints_summary TEXT,
    automation_level TEXT NOT NULL,
    recommended_provider TEXT NOT NULL,
    execution_provider TEXT NOT NULL,
    recommended_model TEXT,
    recommended_workflow TEXT NOT NULL,
    aspect_ratio TEXT,
    image_size TEXT,
    prompt_text TEXT NOT NULL,
    estimated_outputs INTEGER NOT NULL DEFAULT 0,
    estimated_cost_low REAL NOT NULL DEFAULT 0,
    estimated_cost_high REAL NOT NULL DEFAULT 0,
    -- added by ensure_task_runs_columns():
    status TEXT NOT NULL DEFAULT 'planned',
    run_count INTEGER NOT NULL DEFAULT 0,
    actual_images INTEGER NOT NULL DEFAULT 0,
    actual_cost_usd REAL NOT NULL DEFAULT 0,
    actual_model TEXT,
    actual_provider TEXT,
    last_run_at TEXT,
    last_error TEXT,
    plan_json TEXT
);
```

- **Indexes: none** beyond the implicit ones for `PRIMARY KEY` and `UNIQUE`.
- Two tables only. Current row counts: `task_templates` = 5, `task_runs` = 1.
- **There is no `projects` table.** There is no table for clients, shots, or assets.

### Migration mechanism

- No migration framework. Schema is created inline with `CREATE TABLE IF NOT EXISTS`
  on every startup.
- The one precedent for schema evolution is `ensure_task_runs_columns()`
  (`nbs.py:2010-2025`): reads `PRAGMA table_info(task_runs)` and issues
  `ALTER TABLE … ADD COLUMN` for any missing column. This is the pattern to follow for
  additive changes.
- `DEFAULT_TASK_TEMPLATES` (`nbs.py:1931-1992`) is seeded with `INSERT OR IGNORE` on every
  startup (`nbs.py:2080-2103`), so editing the Python list does not update existing rows.

### What persists outside SQLite

| File | Written by | Read by |
| --- | --- | --- |
| `config.json` (root; gitignored) | `save_config()` `nbs.py:3192`; called from `/api/save-config`, `persist_generation_result` (stats + scope memory, `nbs.py:8739, 8807`), `/api/asset-metadata-memory` `nbs.py:7535`, `ensure_flask_secret_key` | `load_config()` `nbs.py:3178` — called at the top of nearly every request. Holds API keys, `flask_secret_key`, `stats` (request log), and **`asset_metadata_memory`** (the scope-bar recent-values lists, shape at `nbs.py:486-491`) |
| `Image_assets/generations/<client>/<project>/<shot>/<stem>.json` | `persist_generation_result` `nbs.py:8802` (one sidecar per image) | `collect_generation_records` `nbs.py:7230`, `collect_asset_metadata_records` `nbs.py:7393`, delete route `nbs.py:11171` |
| `Image_assets/videos/<client>/<project>/<shot>/<stem>.json` | `persist_video_result` `nbs.py:8946` | `collect_video_asset_records` `nbs.py:5908`, delete route `nbs.py:11121` |
| `Image_assets/loved/…/<stem>.json` | `/api/publish` `nbs.py:11235` | `collect_loved_records` `nbs.py:7280` |
| `Image_assets/reference_archive/_index.json` | `save_reference_archive_index` `nbs.py:2691` | `load_reference_archive_index` `nbs.py:2680` |
| `Image_assets/reference_masks/<date>/<fn>.json` | `nbs.py:3901` (mask metadata) | `load_reference_mask_metadata` `nbs.py:3832` |
| `Image_assets/edit_sessions/…json` | `/api/edit-session` POST and the sam3 routes (`nbs.py:6530, 6583, 6791, 6935`) | `/api/edit-session` GET `nbs.py:6495` |
| `Elements/Model Managment/json/<slug>.json` | `save_talent_json` `nbs.py:2530`; called by `/api/elements/save-talent`, toggle-favorite, migrate-catalog | `load_talent_json` `nbs.py:2522`; `/api/elements` `nbs.py:7703-7716` |
| `Elements/<folder>/catalog.json` (legacy) | toggle-favorite fallback `nbs.py:7853` | `/api/elements` fallback `nbs.py:7718-7724`; migrate-catalog `nbs.py:7879` |
| `talent_vocabulary.json` | Nothing in the app writes it | Loaded once at import into `TALENT_VOCABULARY` `nbs.py:1849-1851` |
| `fal_endpoints.json` | `add_fal_catalog_endpoint` via `/api/catalog/add` | `fal_catalog.load_endpoints` |
| `models_video.generated.json` | `fal_catalog.write_json_atomic` via `/api/catalog/refresh` | Video model catalog at startup |
| Browser `localStorage` | `project_meta.js:72-79` (`ai_api_asset_client/project/shot/filename`), plus theme and Generator drafts in `index.html` | `project_meta.js:61-70` |

---

## 3. Scope (Client / Project / Shot / Filename)

This is the most important finding: **there is no server-side "active scope."**

### Where the four values live between requests

- **Browser `localStorage`**, on the client. `static/project_meta.js:2-7` defines the keys
  `ai_api_asset_client`, `ai_api_asset_project`, `ai_api_asset_shot`,
  `ai_api_asset_filename`. `loadState()` at `:61-70`, `saveState()` at `:72-79`.
- They are **not** in the Flask `session`, **not** in SQLite, **not** in a cookie.
- Each generation request carries them explicitly as `assetClient`, `assetProject`,
  `assetShot`, `assetFilename` in the JSON body — the Generator spreads
  `collectAssetMetaPayload()` into the payload at `templates/index.html:7474`
  (`project_meta.js:324-326` returns the current state).
- Server side, `config.json → asset_metadata_memory` stores *recently used values*
  (`nbs.py:313-352`), not an active selection. It is a suggestion list.

### How the top-bar "dropdowns" get populated

They are not `<select>` elements. `templates/_project_meta_bar.html:8-24` renders four
`<input class="project-meta-input">` text boxes; `project_meta.js:157-208` builds a custom
suggestion menu under each. Option values come from a **union of three sources**, built by
`collect_asset_metadata_options()` (`nbs.py:7448-7492`):

1. **Folder names on disk**: `collect_asset_metadata_records()` (`nbs.py:7393-7445`)
   `os.walk`s `Image_assets/generations/` and `Image_assets/videos/` and reads
   `client/project/shot/filename` from path segments (`:7420-7434`).
2. **Per-asset JSON sidecars**: the same function then reads `assetClient/assetProject/
   assetShot/assetFilename` from every generation, video, loved, and reference-archive
   record (`:7436-7444`).
3. **`config.json → asset_metadata_memory`** lists (`:7477-7491`).

`uncategorized` is always prepended. The result is injected into every page by the
context processor (`nbs.py:7495-7515`) as `asset_meta_bootstrap`, read by
`project_meta.js:16-22`, and refreshed via `GET /api/asset-metadata-options`
(`project_meta.js:210-228`).

So the task's premise ("folder names scraped from disk") is correct but incomplete — it is
disk folders **plus** sidecar metadata **plus** a recents list in `config.json`.

### Where the folder path and filename prefix are built

- Normalization: `normalize_asset_metadata()` `nbs.py:163-183`. Empty/`-`/`uncategorized`
  → `"uncategorized"` (`normalize_asset_scope_text`, `nbs.py:138-143`). Also infers
  client/project/shot from `assetRelpath` segments if present (`:166-171`).
- **Folder**: `build_asset_storage_relative_dir()` `nbs.py:248-253` →
  `<client>/<project>/<shot>` with each segment through `sanitize_asset_path_segment()`
  (`nbs.py:146-152`, strips `<>:"/\|?*`, caps 120 chars).
- **Filename prefix**: `build_asset_storage_file_prefix()` `nbs.py:256-264` →
  `<client>_<project>_<shot>_<filename>` where any segment equal to `uncategorized` is
  **omitted** from the prefix (but still present in the folder path). So an uncategorized
  asset is `uncategorized/uncategorized/uncategorized/golden_flower.png`, confirmed on disk.
- **Both combined + collision handling**: `build_asset_storage_paths()` `nbs.py:267-287`
  appends `_2`, `_3`, … if the stem exists; multi-image results get `_1`, `_2` variant
  suffixes (`nbs.py:8767-8772`).
- Called from `persist_generation_result` (`nbs.py:8767`) and `persist_video_result`.

### Is there a project record?

**No.** "Project" is only ever a string. It appears as:

- `assetProject` in request payloads and sidecar JSON,
- a path segment under `Image_assets/generations/` and `videos/`,
- an entry in `config.json → asset_metadata_memory.projects`,
- `task_runs.project_name TEXT` in SQLite (`nbs.py:2056`) — free text typed in the
  Workbench form, unrelated to the scope bar.

---

## 4. Workbench

### Route and template

- `GET /workbench` (`nbs.py:4236-4248`) renders `templates/workbench.html` (496 lines)
  with `task_templates=fetch_task_templates()`, `report=get_workbench_report()`,
  `channel_labels=WORKBENCH_CHANNEL_LABELS`.
- The template **does** include the scope bar (`workbench.html:101`) and loads
  `project_meta.js` (`:11`), but its JS never calls `collectAssetMetaPayload()`.

### Where task templates come from

- Seeded from a hardcoded Python list `DEFAULT_TASK_TEMPLATES` (`nbs.py:1931-1992`) into
  the `task_templates` SQLite table on startup via `INSERT OR IGNORE` (`nbs.py:2080-2103`).
- Read back from the table by `fetch_task_templates()` (`nbs.py:2108-2119`).
- There is no UI to create or edit templates. Five slugs exist: `campaign_launch`,
  `ad_variation_batch`, `editorial_lookbook`, `product_hero`, `location_concept`.

### Full definition of one template (`nbs.py:1932-1943`)

```python
{
    "slug": "campaign_launch",
    "name": "Launch Campaign",
    "description": "Multi-channel campaign visuals with clear brand direction and commercial polish.",
    "default_provider": "gemini",
    "default_workflow": "campaign-launch-v1",
    "default_model": "gemini-3.1-flash-image-preview",
    "default_aspect_ratio": "4:5",
    "default_image_size": "1K",
    "default_temperature": 0.95,
    "prompt_scaffold": "Build a flagship campaign image that feels art-directed, premium, and ready for client review.",
}
```

Note `default_provider: "comfyui"` on two templates is aspirational — `_route_workbench_task`
hardcodes `execution_provider = "gemini"` (`nbs.py:2162`) and only ever routes to Gemini.

### "Build Prompt Plan", end to end

1. `workbench.html:383-418` `buildPlan()` posts `{client_name, project_name, task_slug,
   objective, subject_summary, channels[], automation_level, vibe, constraints_summary}` to
   `POST /api/workbench/plan`. `client_name`/`project_name` come from the Workbench's own
   `#clientName` / `#projectName` text inputs (`workbench.html:133-138`), **not** the scope bar.
2. `api_workbench_plan` (`nbs.py:11267-11281`) → `build_workbench_plan(body)`
   (`nbs.py:2239-2298`):
   - looks up the template by slug; requires `objective`;
   - `_normalize_channels` (`:2130-2141`) filters to keys in `WORKBENCH_CHANNEL_LABELS`
     (`:1994-2001`: instagram_feed, meta_ads, stories_reels, website, email, print),
     defaulting to `["instagram_feed"]`;
   - `_route_workbench_task` (`:2160-2202`) picks model/aspect/size/temperature from the
     template + channels + automation level (`_pick_aspect_ratio` `:2144-2151`);
   - `_build_prompt_from_brief` (`:2205-2236`) concatenates scaffold + `Client:` +
     `Project:` + objective + subject + vibe + channels + constraints into a prompt string;
   - estimates output count (`:2154-2157`) and cost from `PRICING`.
3. Returns a plan dict (`:2270-2298`): `task_slug, task_name, description, brief{…},
   recommended_provider, recommended_workflow, execution_target{provider, model,
   model_label, aspectRatio, imageSize, temperature, topP}, estimated_outputs,
   estimated_cost_range_usd{low, high}, reasoning[], prompt`.
4. `save_task_run(plan)` (`nbs.py:2301-2356`) assigns a `run_uuid`, `INSERT OR REPLACE`s
   a `task_runs` row with `status='planned'` and the whole plan serialised into `plan_json`.
5. Response: `{ok, plan, report}`; the page renders the plan and refreshes the snapshot.

### "Run from Workbench" and the hand-off to generation

1. `workbench.html:419-450` `runPlanDirect()` posts `{run_uuid, numberOfImages, useSearch,
   outputMode}` to `POST /api/workbench/run`.
2. `api_workbench_run` (`nbs.py:11006-11046`) loads the row with `get_task_run`, rebuilds a
   generation payload from `plan.execution_target` + `plan.prompt` (`:11019-11031`), then
   calls **the same** `run_generation_job(payload, config)` and `persist_generation_result`
   the Generator uses (`:11033-11034`), synchronously (no async job).
3. `update_task_run_after_generation` (`nbs.py:2381-2425`) bumps `run_count`, adds
   `actual_images` and `actual_cost_usd`, sets `status` to `completed` or `failed`.
4. **The payload at `:11019-11031` contains no `assetClient/assetProject/assetShot/
   assetFilename`.** `normalize_asset_metadata` therefore resolves all three scope values
   to `uncategorized` and the filename to a slug of the prompt. Every Workbench run files
   under `generations/uncategorized/uncategorized/uncategorized/`, regardless of what was
   typed in the Workbench Client/Project boxes or selected in the scope bar. This is the
   concrete form of the disconnect described in Part B.

### Where `PLANNED RUNS`, `EXECUTED`, `COMPLETED`, `ACTUAL COST` come from

All from one aggregate query over `task_runs` in `get_workbench_report()`
(`nbs.py:2428-2506`):

- **Planned Runs** = `COUNT(*)` (`summary.total_runs`)
- **Executed** = `SUM(run_count > 0)` (`summary.executed_runs`)
- **Completed** = `SUM(status = 'completed')` (`summary.completed_runs`)
- **Actual Images** = `SUM(actual_images)`
- **Actual Cost** = `SUM(actual_cost_usd)`

Rendered by `renderReport()` in `workbench.html:336-347` and again in `reports.html:163-168`.

---

## 5. Elements

### `talent_vocabulary.json`

- Root-level JSON of allowed values for 7 talent descriptor fields (`gender`, `ethnicity`,
  `age_group`, `skin_tone`, `hair_color`, `hair_style`, `eye_color`, `body_type`).
- Loaded once at import into `TALENT_VOCABULARY` (`nbs.py:1849-1851`) with an inline
  fallback copy if the file is missing (`:1852-1872`).
- Read by `_build_vocab_prompt_block()` (`:1875-1880`) which injects "MANDATORY ALLOWED
  VALUES" into the Gemini vision prompt used by `/api/elements/analyze-image`, and by
  `/api/elements` for filter matching. Nothing writes it.

### `Elements/Model Managment/`

`ELEMENTS_CATEGORIES` (`nbs.py:471-475`) maps three folder names to categories:
`Model Managment` → characters, `Locations` → locations, `Props` → props. Only
`Model Managment` exists on disk. Its contents:

- `json/` — 18 per-talent JSON files (`elena_rossi.json`, `kenji_sato.json`,
  `wren_calloway.json`, `zoey_hale.json`, `model_01.json` … `model_384.json`).
- `images/` — 14 legacy `model_NN.jpg` files referenced by the `model_*.json` records.
- Root-level `<slug>_001.jpg` for talents saved through the New Talent flow
  (`elena_rossi_001.jpg`, `kenji_sato_001.jpg`, `wren_calloway_001.jpg`, `zoey_hale_001.jpg`).
- No `catalog.json` (the legacy format; `/api/elements/migrate-catalog` converts it).

`talent_json_dir()` (`nbs.py:2508-2515`) special-cases the folder name: for
`Model Managment` JSON lives in `json/`; for any other folder, at the folder root.

### Talent record — exact fields

Stored at `Elements/Model Managment/json/<slug>.json`. Shape as written by
`/api/elements/save-talent` (`nbs.py:8330-8347`) and confirmed by `wren_calloway.json`:

```json
{
  "id": "wren_calloway",
  "name": "Wren Calloway",
  "gender": "female",
  "ethnicity": "south_asian",
  "age_group": "young_adult",
  "skin_tone": "medium_olive",
  "hair_color": "black",
  "hair_style": "tied_back",
  "eye_color": "brown",
  "body_type": "slim",
  "description": "Oval face with prominent cheekbones …",
  "tags": ["editorial", "natural", "portrait", "minimalist", "beauty"],
  "profile": {},
  "is_favorite": false,
  "images": [
    { "filename": "wren_calloway_001.jpg", "path": "wren_calloway_001.jpg",
      "added_at": "2026-09-13T14:24:15.743791", "is_primary": true, "analyzed": true }
  ],
  "created_at": "2026-09-13T14:24:15.743791",
  "updated_at": "2026-09-13T14:24:15.743791"
}
```

`profile` is always written as `{}` and never populated by the app.

### How "New Talent" saves

- Wizard JS lives in `index.html` from `:14905` (`NEW TALENT WIZARD`). Optional analysis
  step posts the image to `POST /api/elements/analyze-image` (`index.html:15074`,
  `nbs.py:7956`) → Gemini vision returns descriptor JSON constrained to the vocabulary.
- Save posts `{image_data, mime_type, folder, metadata{…}}` to
  `POST /api/elements/save-talent` (`index.html:15166`, `nbs.py:8267-8355`):
  slug from `name_to_slug(name)`, image normalised to JPG, written as
  `<slug>_<NNN>.jpg` at the folder root, JSON written/appended via `save_talent_json`.
  Storage is **files on disk only** — nothing in SQLite.

### How an Element reaches the provider call

1. User clicks "Elements" in the Generator → `openElements(target)` (`index.html:14518`)
   sets `elementsTarget` (`refs`, `video`, `video-end`, `video-refs`, `edit-global-refs`,
   `edit-selection-refs`) and `loadElements()` (`:14564`) fetches `GET /api/elements?…`.
2. `/api/elements` (`nbs.py:7659-7815`) reads the per-talent JSONs, resolves the primary
   image, and returns `{id, name, img_url:"/elements/<folder>/<file>", …descriptors}`.
3. On confirm, for `refs` target (`index.html:14876-14903`):
   `addRefImageFromUrl(asset.img_url, asset.name)` (`:6491-6520`) fetches the image over
   HTTP from `/elements/…`, base64-encodes it, and pushes `{mime_type, data, previewUrl,
   name, …}` onto the global `refImages` array (`:2171`).
4. `generate()` (`index.html:7457-7494`) sends `refImages: refImages.map(buildGenerationRefPayload)`
   in the JSON body to `/api/generate` or `/api/jobs/generate`.
5. Server: `run_generation_job` → provider job (e.g. `run_fal_nano_banana_generation_job`
   `nbs.py:10170`) calls `normalize_ref_image_payloads` (`:10196`) and, for fal, converts to
   data URIs in `payload["image_urls"]` (`:10209`); for Gemini, inline base64 parts.
   The element's identity (its `id`/slug) is **not** carried to the server — only the
   pixels and a display `name`.

### `@handle`

Partly. When an element is added as a ref, the Generator **inserts text into the prompt**
(`index.html:14881-14897`): `@<Name>, <skin_tone>, <hair_color>, <hair_style>, <eye_color>.
<description>` at the cursor. This is a one-way text insertion — the server does not parse
`@mentions`, there is no lookup from `@name` back to a talent record, and nothing survives
after the prompt text is edited. Selection is by click in the modal; the `@` text is a
side-effect, not a mechanism.

---

## 6. Providers

### Where dispatch lives

Branch-per-provider, in three `if provider == …` dispatchers — no shared abstraction, no
base class, no registry:

- Images: `run_generation_job` (`nbs.py:10760-10792`) — `gemini` → `run_gemini_generation_job`;
  `fal` → sub-branch on `family` (`gpt-image-2`, `gpt-image-2-edit`, `seedream*`, else
  nano-banana); `byteplus` → `run_byteplus_seedream_generation_job`.
- Edits: `run_edit_job` (`:10795`).
- Video: `run_video_job` (`:10873-10910`) — `kling` native, `fal` with sub-branches on family
  (`seedvr-video`, `wan-video`, `ltx-video`, `catalog_source == "fal_schema"`, `seedance`,
  `kling`), `luma`.
- Upscale: `run_fal_seedvr_upscale_job` (`:10635`).

Model tables `MODELS_INFO`, `MODEL_FAMILIES`, `PRICING`, `VIDEO_MODELS_INFO` etc. are
module-level dicts (`nbs.py:~800-1480`). `resolve_model_selection` picks provider from
the model id; `normalize_generation_request` (`nbs.py:1481-1494`) stamps
`model/modelFamily/provider/modelLabel/providerLabel` and the asset metadata on every payload.

Each provider job is 100–200 lines and repeats the same skeleton. Provider job functions:

`run_gemini_generation_job :8522`, `run_native_kling_video_job :9070`,
`run_fal_kling_video_job :9169`, `run_luma_video_job :9272`, `run_fal_wan_video_job :9384`,
`run_fal_seedance_video_job :9515`, `run_fal_schema_video_job :9697`,
`run_fal_ltx_video_job :9881`, `run_fal_seedvr_video_job :9961`,
`run_fal_seedream_generation_job :10056`, `run_fal_nano_banana_generation_job :10170`,
`run_fal_gpt_image_2_generation_job :10290`, `run_fal_gpt_image_2_edit_job :10408`,
`run_byteplus_seedream_generation_job :10530`, `run_fal_seedvr_upscale_job :10635`.

### One provider call, start to finish — `run_fal_nano_banana_generation_job` (`nbs.py:10170-10287`)

1. **Normalise** (`:10171-10196`): `normalize_generation_request(body)`; read `model`,
   `imageSize`, `numberOfImages` (clamped 1–4), `aspectRatio`, `refImages`, `useSearch`,
   safety tolerance, seed mode/value; stringify dict prompts; validate model and ref-count
   against `MODELS_INFO[model]["max_ref_images"]`.
2. **Build request** (`:10198-10213`): `endpoint = build_fal_nano_banana_endpoint(...)`;
   `payload = {prompt, num_images, resolution, aspect_ratio, output_format:"png",
   sync_mode:True, safety_tolerance}` + optional `image_urls` (data URIs), `enable_web_search`, `seed`.
3. **Send** (`:10215-10227`): `requests.post(f"{FAL_BASE_URL}/{endpoint}", headers={Authorization:
   "Key …"}, json=payload, timeout=240)`; timeout → `TimeoutError`; non-200 →
   `RuntimeError(extract_fal_error(response))`.
4. **Parse** (`:10229-10247`): `result["images"]` or `["data"]` → `decode_fal_image_result`
   → base64 → `convert_image_b64_to_png`.
5. **Cost + params** (`:10249-10275`): `PRICING[model][image_size] * n`; `params_meta =
   merge_request_settings(merge_asset_metadata({model, provider, prompt, imageSize, …}, body), body)`
   — this is where `assetClient/assetProject/assetShot/assetFilename` are carried through.
6. **Return** (`:10279-10287`): `{ok, images:[{mime_type,data}], text, cost, model_label,
   params, _input_ref_images}`.
7. **Persist** happens in the caller, `persist_generation_result` (`nbs.py:8715-8806`):
   bumps `config.json` stats; normalises asset meta (`:8745`) and updates the recents
   memory; builds path via `build_asset_storage_paths(GENERATIONS_DIR, asset_meta, "png")`;
   writes the PNG (`:8781`) and a sidecar JSON of `params` + `generated_at`, `filename`,
   `assetRelpath`, delivered dimensions (`:8783-8802`).

### Cost of adding a provider

Moderate and mechanical, but wide:

- New `run_<provider>_…_job` function (~120 lines) copying the skeleton above.
- New `elif provider == "…"` branch in `run_generation_job` and/or `run_video_job`.
- Entries in `MODELS_INFO` / `MODEL_FAMILIES` / `PRICING` / `PROVIDER_LABELS` (`:840`).
- Key field in `DEFAULT_CONFIG`, `/settings` handler, `settings.html`, a `/api/verify-*-key`
  route, and `index()`'s `has_key` check (`:4116-4121`).
- Front-end model picker in `index.html` reads the tables so it mostly follows, but
  provider-specific controls (safety toggles, seed) are hand-wired per provider.

No part of this is in scope for Part B.

---

## 7. Front end

### Build step

**None.** Plain HTML/JS/CSS served by Flask. `static/` contains three files: `style.css`
(shared, ~7k lines with local changes), `project_meta.js` (357 lines), `favicon.svg`.
Cache-busting is a manual query string (`project_meta.js?v=20260331a`).

### JS files and what each owns

- `static/project_meta.js` — the scope bar only: state in `localStorage`, suggestion
  menus, `GET /api/asset-metadata-options`, `POST /api/asset-metadata-memory`, and four
  window globals: `getAssetMetaSelection`, `collectAssetMetaPayload`,
  `validateAssetMetaSelection` (filename required), `setAssetMetaSelection`
  (`project_meta.js:320-340`). Dispatches an event on change via `emitState` (`:245`).
- Everything else is **inline `<script>` in the templates**:
  - `templates/index.html` — ~13,000 lines of JS from `:2154` onward: the Generator, ref
    strip, drafts, model picker, video tab, edit sessions/SAM3, Elements modal, New Talent
    wizard, async job polling.
  - `templates/workbench.html:235-496` — plan/run/report rendering.
  - `templates/asset_gallery.html` — Assets tab grid, filters by scope, delete/love/open-folder.
  - `templates/settings.html` — key verification, catalog refresh.
  - `templates/reports.html` — report cards.

### How the page talks to the server

- **`fetch` with JSON** for everything functional (`/api/*`). No form posts except the
  login form (`login.html`, `POST /login`).
- Generation uses either sync `POST /api/generate` or async `POST /api/jobs/generate` →
  poll `GET /api/jobs/<id>` (in-memory `ASYNC_JOBS` dict + daemon threads,
  `nbs.py:506-800`; jobs are lost on restart).

---

## Risks

Things that will make Part B harder than the spec reads:

1. **"Active project for the session" has no home today.** All scope state is in browser
   `localStorage` (`project_meta.js`), sent per request. Flask `session` currently holds
   only `user`. Part B needs to decide whether "active project" lives in `session`
   (server) or `localStorage` (client, current pattern). Putting it in `session` means
   every consumer (`generate()`, video, upscale, Assets filters) must be changed to stop
   trusting the client-sent `assetProject`; leaving it client-side means "selecting sets
   the active project for the session" is really "sets it in this browser."

2. **The scope bar is not a `<select>`.** It's a free-text input with a suggestion menu
   (`_project_meta_bar.html`, `project_meta.js:157-208`). "Project dropdown lists project
   records" means either converting that one field to a real select (breaks the current
   typed-value UX and the filename-style memory), or filtering the suggestion list to
   records while still allowing arbitrary text. The spec's "dropdown" wording assumes a
   control that doesn't exist.

3. **Three scope-option sources must keep working for the fallback.** Existing assets are
   discoverable only via disk walk + sidecar JSON (`collect_asset_metadata_records`). If
   the Project field switches to records-only, assets under projects with no record
   (`uncategorized`, or old names) vanish from the Assets tab filter unless the fallback
   merges disk-derived names back in. Constraint 1 in Part B requires that merge.

4. **Workbench runs never carried scope.** `/api/workbench/run` (`nbs.py:11019-11031`)
   omits asset metadata entirely, so "keep the current folder format, built from the
   record" is not a swap — it's adding scope to a path that never had it. `task_runs`
   also stores `client_name`/`project_name` as text; Part B should add a nullable
   `project_id` column via the `ensure_task_runs_columns` pattern rather than repurpose them.

5. **Workbench Client/Project inputs are load-bearing in two places**:
   `_build_prompt_from_brief` (`nbs.py:2212-2213`) writes `Client: …` / `Project: …` into
   the generated prompt text, and `get_workbench_report` groups and counts by
   `client_name` (`:2433, 2452-2457`). Replacing the inputs with the active project must
   still feed both — the prompt builder and the report's "Clients" breakdown. Not a
   blocker, but it's the "say so and stop" case the spec anticipates; my read is it can be
   satisfied by sourcing the same strings from the record.

6. **Every request re-reads and re-writes `config.json`** (`load_config`/`save_config`).
   `persist_generation_result` calls `save_config` twice per generation. Adding a
   `projects` table is fine; adding project state to `config.json` would compound this
   and race under the async job threads. SQLite is the right place.

7. **`init_studio_db()` is called on every task-run helper**, so any new `CREATE TABLE IF
   NOT EXISTS projects` placed there runs on every Workbench request. Acceptable
   (cheap), but a new `ensure_projects_columns` for later additive changes should follow
   the same idempotent pattern.

8. **Filename is mandatory for generation** (`validateAssetMetaSelection`,
   `project_meta.js:328-334`). A project record doesn't remove that; "New Project" doesn't
   change the fact that the user still has to type a filename before Generate.

9. **The `index.html` template is 15,512 lines with inline JS.** Any Generator-side change
   to read the active project is a large-file edit with no module boundaries. Part B says
   "do not touch the Generator," which is achievable only if the Generator keeps reading
   `assetProject` from `localStorage` as it does today — i.e. the record's `name` must be
   what gets written into the existing `ai_api_asset_project` key.

10. **`fal_catalog.py`, `fal_endpoints.json`, `models_video.generated.json` are untracked.**
    `nbs.py:75` imports `fal_catalog`, so a fresh clone of the repo as committed today
    does not start. These need to be in the baseline commit.

11. **Windows path/encoding artefacts.** `nbs.py` has a UTF-8 BOM and mojibake in comments
    (`Ã¢â‚¬â€` for `—`); git warns about LF→CRLF on `nbs.py`. Not functional, but any
    diff tooling will show noise, and `.gitattributes` should be checked before the first
    push so the fork doesn't normalise line endings differently from upstream.

12. **Two dead templates** (`loved.html`, `published.html`) and a stale version string
    (`workbench.html:100` says 1.3; `APP_VERSION = "1.4"`). Cosmetic, but easy to mistake
    for live code.
