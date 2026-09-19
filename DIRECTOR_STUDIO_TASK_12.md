# Director Studio — Task 12: Scenes

Repo: `dorelle/AI_API_DirectorStudio`
Read `DIRECTOR_STUDIO_ORIENTATION.md` and the Task 04 through 11 commits before starting.

A shot currently has a `scene` field that is free text. A label, not a record. So there is
nowhere for a scene's brief, its script, or its dialog to live, and shots have nothing to
belong to.

This task makes the scene real.

---

## Scope

In:

- A `scenes` table, belonging to a project
- Brief and script on the scene
- Shots belong to a scene
- Navigation both ways — open a scene and see its shots, open a shot and see its scene
- A scene list and a scene editor

Out, and not to be started:

- The scene's cast, or dressed character cards. That is the next task
- The project playbook
- Agent scene breakdown, or generating shots from a script
- Rewiring the Workbench film templates to save into a scene

---

## 1. The scene record

New table:

| Column | Notes |
| --- | --- |
| `id` | Primary key |
| `project_id` | |
| `slug` | **Fixed at creation. Never changes.** Same rule as shots |
| `name` | Display name — the scene as you would say it out loud |
| `sort_order` | Position in the film. Independent of slug |
| `brief` | What happens here, where, who is in it. The scene-level brief |
| `script` | The dialog and action. Free text, multi-line, can be long |
| `environment_id` | An environment element, nullable. From Task 11 |
| `note` | |
| `created_at` | |

Slug and order follow the shot rules exactly: slug is assigned once and never renumbers,
order lives in `sort_order`, insertion between scenes is free.

`script` is the field a whole scene's dialog goes in. It must handle length — this is not
a one-line input.

### Routes

Follow the conventions from `/api/shots`.

- `GET /api/scenes?project_id=`
- `POST /api/scenes`
- `GET /api/scenes/<id>`
- `PATCH /api/scenes/<id>`
- `DELETE /api/scenes/<id>`
- `POST /api/scenes/reorder`

Deleting a scene that has shots must not delete the shots. Detach them and say so.

## 2. Shots belong to scenes

New column on `shots`:

| Column | Notes |
| --- | --- |
| `scene_id` | Nullable |

**The existing free-text `scene` field stays.** Do not migrate it, do not delete it, do not
try to match it to new records automatically. Existing shots keep their text and have no
`scene_id` until one is set by hand.

A shot with no `scene_id` is valid and still renders. Scenes are organization, not a
requirement.

## 3. Navigation both ways

This is the part that matters most. Opening either one shows the connection.

### From the scene

- The scene editor lists its shots, in order
- Clicking one selects it and opens the shot editor
- Shots can be added directly to a scene

### From the shot

- The shot editor shows which scene it belongs to
- Clicking it opens that scene
- The scene can be changed from the shot

### In the right panel

The Shots / Library toggle becomes **Scenes / Shots / Library**.

- **Scenes** — the ordered scene list. Selecting one filters the shot list to that scene
- **Shots** — as today, with shots grouped under their scene and unassigned shots in
  their own group at the end
- **Library** — unchanged

## 4. The scene editor

A **Scene** pill in the bottom panel, beside Image, Video, Edit, Upscale, Shot. Only
present for film projects, same gate as Shot.

Collapsible sections, same pattern as the shot editor:

| Section | Holds |
| --- | --- |
| Brief | What happens here |
| Script | Dialog and action. Give this real room |
| Environment | The attached environment element, picked from Elements |
| Shots | Its shots in order, selectable, reorderable |
| Notes | |

Field locking applies, as everywhere else.

## 5. The strip

When a scene is selected, the shot strip shows that scene's shots only.

When no scene is selected, it shows all shots in the project, as today.

Reordering within a scene reorders within the project's `sort_order`, not a separate
per-scene order. **One ordering, as always.**

---

## Constraints

- Additive. New table, one new column on `shots`.
- Do not migrate or auto-match the existing free-text `scene` field.
- Do not change shot slugs, shot ordering, or anything from Tasks 04 through 11.
- Do not change the render payload. A scene contributes nothing to it in this task.
- Do not touch the provider layer or the job layer.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. Scenes can be created, edited, reordered and deleted within a project.
2. Scene slugs never change on reorder or insert. Verify against the database.
3. Deleting a scene leaves its shots intact and unassigned.
4. A shot can be assigned to a scene, and changed.
5. Shots with no scene still work and still render.
6. From a scene, its shots are listed and selectable.
7. From a shot, its scene is shown and openable.
8. The right panel toggles between Scenes, Shots and Library.
9. The shot list groups by scene, with unassigned shots in their own group.
10. Selecting a scene filters the strip to it.
11. The script field handles a long scene without breaking the layout.
12. The render payload is unchanged.
13. Everything from Tasks 02 through 11 still works.

## Report back

- Files changed, one line each.
- Paste scene `slug` and `sort_order` before and after a reorder and an insert.
- Confirm shots survive their scene being deleted.
- Anything in this spec that was wrong once the code was read.
- Anything deferred, and why.
