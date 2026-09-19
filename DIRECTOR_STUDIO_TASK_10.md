# Director Studio — Task 10: Style References

Repo: `dorelle/AI_API_DirectorStudio`
Read the Task 07 (`8d7dd37`) and Task 08 (`965e155`) commits before starting.

A shot currently carries no look. If the style is not written into the prompt for that
shot, nothing supplies it, and shots in the same film drift apart.

This task adds a style: reusable, attached per shot, and switchable on and off without
detaching it.

---

## Scope

In:

- A style record, holding text and images
- Attach a style to a shot
- A toggle on the shot that applies or suspends it
- Style contributes to the render payload when applied

Out, and not to be started:

- Project-level default styles
- Camera treatments, or any other treatment type
- The compiler
- Changing the role system from Task 08

---

## 1. Why a record and not a field

A style is reused. The same look runs across a whole film, so typing it per shot means
typing it forty times and having it drift.

So a style is created once and attached to many shots.

## 2. The style record

New table:

| Column | Notes |
| --- | --- |
| `id` | Primary key |
| `name` | Short label — how it reads on the chip |
| `text` | The written half: grade, film stock, lighting register, photographic treatment |
| `images` | JSON array of asset paths. The look board. May be empty |
| `project_id` | Nullable. Null means available to every project |
| `archived` | Boolean, hide without deleting |
| `created_at` | |

Either half may be empty. A style can be text only, images only, or both.

### Routes

Follow the conventions used for `/api/projects` and `/api/shots`.

- `GET /api/styles` — list, optionally filtered by project
- `POST /api/styles`
- `GET /api/styles/<id>`
- `PATCH /api/styles/<id>`
- `DELETE /api/styles/<id>`

### Where styles are managed

Create and edit from the shot editor's Style section — a "New style" action and an edit
action on the attached style. Do not build a separate management page for this task.

Images on a style are picked with the **existing** reference picker, multi-select. Do not
build a new one.

## 3. On the shot

Two new columns on `shots`:

| Column | Notes |
| --- | --- |
| `style_id` | The attached style, nullable |
| `style_enabled` | Boolean, default true |

A Style section in the shot editor, collapsible like the rest:

- Shows the attached style by name, with its text and image thumbnails visible
- A toggle that turns it on and off. **Off keeps the attachment** — the style stays
  attached and can be switched back on without re-picking it
- Off is visually obvious. A disabled style must not look like an applied one
- Attach, change and remove actions
- Same field-lock behavior as everything else

## 4. How a style reaches the render

In `build_shot_render_payload`, when a style is attached **and** enabled:

- Its `text` is appended to the prompt. **Append, do not replace.** The shot's own prompt
  comes first, the style follows
- Its `images` are appended to the reference list, **after** elements and after reference
  assets, so existing reference ordering is unchanged

When a style is attached but disabled, or none is attached, the payload is byte-identical
to what Task 07 produces today.

**Report the exact assembly order used**, so it can be checked.

### Interaction with role `style`

Task 08's role vocabulary already includes `style` for an attached reference asset. That
stays and is unaffected. A reference asset with role `style` and an attached style record
are two separate things and both may be present. Do not merge them, do not deduplicate
them, and say in the report how they sit together in the payload.

---

## Constraints

- Additive. New table, two new columns.
- Reuse the existing reference picker for style images.
- Do not change the role system from Task 08.
- Do not change the payload when no style is applied.
- Do not touch the provider layer or the job layer.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. A style can be created with text, images, or both.
2. A style can be attached to a shot and shows its text and thumbnails.
3. The toggle switches it off without detaching it, and back on.
4. A disabled style is visually distinct from an applied one.
5. The same style can be attached to several shots.
6. With a style applied, its text appends after the shot's prompt.
7. With a style applied, its images append after all existing references.
8. With no style, or a disabled one, the payload matches Task 07 exactly.
9. A style can be edited, and the change shows on every shot using it.
10. Everything from Tasks 02 through 09 still works.

## Report back

- Files changed, one line each.
- The exact payload for one shot with a style applied, and the same shot with it disabled,
  pasted side by side.
- How a style record and a role-`style` reference asset sit together in the payload.
- Anything in this spec that was wrong once the code was read.
- Anything deferred, and why.
