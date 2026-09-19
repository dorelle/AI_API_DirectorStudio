# Director Studio — Task 05: What The Shot Sends

Repo: `dorelle/AI_API_DirectorStudio`
Read `DIRECTOR_STUDIO_ORIENTATION.md` and the Task 04 commit (`6f85193`) before starting.

Task 04 built the shot row, the list and the editor. The row describes what a shot **is** —
timing, content, camera, first and last frame — but carries nothing about what actually
gets sent to a model. No prompt, no references, no elements.

So a shot row currently cannot produce anything, however well described it is.

This task adds those three. **It does not render.** Rendering is a later task, and it has
nothing to send until this exists.

The shot strip in the center area is deferred to Task 06.

---

## Scope

In:

- Prompt on the shot row
- Elements attached to the shot
- Assets, meaning reference images, attached to the shot
- A section in the shot editor for each

Out, and not to be started:

- Rendering, takes, approval
- The shot strip in the center area
- The preset compiler, or generating the prompt from the camera fields
- Handle resolution by shot size, comp card views, lineage

---

## 1. Prompt

New columns on `shots`:

| Column | Notes |
| --- | --- |
| `prompt` | The text sent to the model |
| `negative_prompt` | |

Free text for both, in their own section of the shot editor. Same lock behavior as every
other field from Task 04.

**A note on where this is going, so the field is not built the wrong shape.** A later task
adds a compiler that writes this field from the camera and content fields rather than the
user typing it. When that lands, the existing field-lock mechanism is what protects a hand
written prompt from being overwritten. So build it as an ordinary editable field now. Do
not add any compile affordance, and do not make it read-only.

## 2. Elements

Elements are the app's talent records. A shot needs to say which ones are in it.

New column on `shots`:

| Column | Notes |
| --- | --- |
| `elements` | JSON array of element ids, **ordered** |

Order matters. Providers number references positionally, so the array order is the
reference order, and it has to be stable and user-controllable.

In the editor:

- An Elements section showing the attached elements as thumbnails with names
- An add action opening the **existing** Elements modal. Do not build a new picker
- Remove, and reorder by drag
- The orientation report found an `@mention` mechanism already in the codebase. If
  attaching an element can reuse it rather than duplicating the concept, do that and say
  so. If it is unrelated, leave it alone and say so

## 3. Assets

Reference images that are not talent — a plate, a garment, a piece of set.

New column on `shots`:

| Column | Notes |
| --- | --- |
| `reference_assets` | JSON array of asset paths or ids, **ordered** |

In the editor:

- An Assets section showing attached references as thumbnails
- An add action opening the **existing** reference picker modal, the one with Loved and
  References tabs
- Remove, and reorder by drag

## 4. How the three sections sit

The editor already has Timing, Content, Camera, Generation and Notes as collapsible
sections. Add the new ones in an order that reads sensibly alongside them — the intent is
Assets and Elements near Generation, with Prompt beside Content.

Use your judgment on placement, keep every section collapsible, keep collapse state
remembered as it already is.

**First frame and last frame stay where they are.** They are generation inputs, not
references, and they already work.

---

## Constraints

- Additive. New columns only. No renamed columns, no restructured tables.
- **Reuse the existing modals.** The Elements modal and the reference picker both exist.
  Do not build new ones, and do not change how they behave for their current callers.
- Do not touch the Generator's own modes, the provider layer, or the job layer.
- Do not change anything from Tasks 02, 03 or 04 beyond adding to the shot editor.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. A shot has a prompt and a negative prompt, saved and reloaded correctly.
2. Elements can be attached to a shot from the existing Elements modal.
3. Attached elements show as thumbnails, can be removed, and can be reordered.
4. Reference assets can be attached from the existing reference picker.
5. Attached references show as thumbnails, can be removed, and can be reordered.
6. **Order survives a reload** for both arrays. Verify against the database.
7. The lock mechanism works on the prompt fields.
8. The existing Elements modal and reference picker still behave identically for their
   current callers.
9. Campaign projects and no-project state are unchanged.
10. Everything from Tasks 02, 03 and 04 still works.

## Report back

- Files changed, one line each.
- Whether the existing `@mention` mechanism was reusable for element attachment.
- Paste the `elements` and `reference_assets` values for one shot before and after a
  reorder, so ordering can be read directly.
- Anything in this spec that was wrong once the code was read.
- Anything deferred, and why.
