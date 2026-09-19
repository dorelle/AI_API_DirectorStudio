# Director Studio — Task 02: Project Record

Repo: `dorelle/AI_API_DirectorStudio`
Read `DIRECTOR_STUDIO_ORIENTATION.md` at the repo root before starting. This task is
written against its findings. Where it and this file disagree, the report wins — say so
and stop rather than working around it.

---

## What changed since Task 01

The Part B spec in Task 01 assumed the project record would connect two things that
already existed. The orientation report found that is not the situation:

- There is **no server-side scope at all**. Client/Project/Shot/Filename live in browser
  localStorage (`project_meta.js`).
- The scope bar is **free-text inputs with a suggestion menu**, not dropdowns. Suggestions
  are a union of disk folder names, per-asset sidecar JSON, and
  `asset_metadata_memory` in `config.json`.
- `/api/workbench/run` **sends no scope**, so every Workbench run already files under
  `uncategorized/uncategorized/uncategorized`.
- Workbench's Client and Project text boxes are **load-bearing**: their values are written
  into the prompt text and drive the Reports "Clients" breakdown.

So this task introduces server-side project state where none exists, rather than wiring
two existing things together.

---

## Goal

A project record in `studio.db` that the scope bar and Workbench both read from, without
removing the existing free-text path.

## Non-goals for this task

- No shot rows, no board, no timeline, no Director tab.
- No changes to the Generator, the Elements modal, the provider layer, or the job layer.
- No conversion of the scope bar into a locked dropdown.

---

## 1. The record

New table in `studio.db`. Follow whatever table-creation pattern the report found in use.

| Column | Notes |
| --- | --- |
| `id` | Primary key |
| `name` | Project name. Used as the `project` path segment |
| `client` | Used as the `client` path segment |
| `type` | `film` or `campaign` |
| `settings` | JSON text blob |
| `created_at` | |
| `archived` | Boolean, default false. Hide from pickers without deleting |

`settings` for `type = film`: format, runtime, aspect ratio, frame rate, register,
resolve_folder. All optional, all nullable.

`settings` for `type = campaign`: leave empty for now. Do not invent fields. If the
existing Workbench templates need per-project values, name them and stop.

`name` and `client` must produce the same path segments the current code produces. Find
the existing sanitizer the path builder uses and reuse it. Do not write a second one.

## 2. Routes

Follow the existing route naming and response conventions found in the report.

- `GET /api/projects` — list, excluding archived unless asked
- `POST /api/projects` — create
- `GET /api/projects/<id>` — one record
- `PATCH /api/projects/<id>` — update, including archive

## 3. Scope bar

**Keep the free-text inputs and the suggestion menu exactly as they are.** They are how
existing assets stay reachable and how ad-hoc filing works.

Add alongside them:

- A project picker listing project records.
- Selecting one fills Client and Project with that record's values and stores its `id` in
  localStorage next to the existing scope values.
- Typing freely into Client or Project clears the stored `id`. Free text still works and
  still files exactly as it does today, with no record.
- A "New Project" action that creates a record and selects it.

So there are two states: a project is selected and the `id` travels with requests, or it
is not and behavior is unchanged from today.

## 4. Generation requests

Where the client currently sends Client/Project/Shot/Filename with a generation, add the
project `id` when one is selected.

Server side: when the `id` is present, resolve the record and build the path from it.
When absent, build the path from the strings exactly as now.

**The path format does not change.** `Client/Project/Shot` folders,
`client_project_shot_filename.ext` filenames. Verify against `persist_generation_result`.

## 5. Workbench

- Prefill the Client and Project boxes from the selected project.
- **Leave them editable.** They feed prompt text and the Clients report, and an override
  has to stay possible.
- Fix the scope bug: `/api/workbench/run` should send the same scope a Generator run
  sends, so Workbench output stops filing under `uncategorized`. Confirm the fix against
  the line numbers in the report.
- When the selected project is `type = film`, hide the Channels field. Nothing else
  changes in Workbench in this task. Do not add film templates yet.

## 6. Reports

The Clients breakdown currently reads from whatever Workbench wrote. Once Workbench sends
real scope, those numbers will start being populated where they previously were not.

Do not restructure Reports. If the fix in section 5 would make existing report rows
inconsistent with new ones, say so and describe the options rather than picking one.

---

## Constraints

- **Existing assets must stay browsable.** Everything already on disk was filed with no
  project record. The Assets tab must keep working unchanged for all of it.
- **Additive only.** No renamed columns, no restructured tables, no removed routes.
- **No new dependencies.** The first-run bootstrap list stays as it is.
- **No refactoring.** Match the surrounding style, whatever it is. Do not tidy adjacent
  code.
- `config.json` and `studio.db` stay gitignored.
- Anything in this spec contradicted by the code: stop and report, do not improvise.

## Done means

1. A project can be created from the UI, with a type.
2. It appears in the picker and selecting it fills the scope bar.
3. A generation with a project selected files to the same folder structure as before.
4. A generation with free-text scope and no project selected behaves exactly as today.
5. A Workbench run files under the correct scope instead of `uncategorized`.
6. Workbench's Client and Project boxes prefill but remain editable.
7. Channels is hidden for a film project.
8. Every existing asset still browses correctly in the Assets tab.

## Report back

- Files changed, one line each.
- Anything in this spec that was wrong once the code was read.
- The Reports question from section 6, if it applies.
- Anything deferred, and why.
