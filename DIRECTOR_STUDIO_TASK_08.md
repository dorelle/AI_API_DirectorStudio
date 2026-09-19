# Director Studio — Task 08: Reference Roles

Repo: `dorelle/AI_API_DirectorStudio`
Read `DIRECTOR_STUDIO_ORIENTATION.md` and the Task 05 commit (`620583a`) before starting.

Task 05 gave a shot ordered arrays of elements and reference assets. Order is recorded.
**What each reference is for is not.**

That matters because references do different jobs. One establishes the composition and is
the image the model edits. Another establishes a character's appearance. Another is a
garment, or a plate, or a style. A prompt that treats all of them the same gets a worse
result than one that says which is which.

This task records the job.

---

## Scope

In:

- A role on every attached element and reference asset
- Role shown and editable on the chip
- One reference per shot can be the edit target

Out, and not to be started:

- The compiler, or any prompt text generated from roles
- Rendering changes. Task 07 sends references in array order and continues to
- New reference types, pickers, or modals

---

## 1. The role vocabulary

**This list is the user's to set. It is a starting point, not a decision — expect it to
change.**

| Role | What it establishes |
| --- | --- |
| `edit_target` | The composition. The image the model edits. At most one per shot |
| `character` | A character's appearance |
| `garment` | A garment or wardrobe piece |
| `environment` | The location, set or plate |
| `prop` | An object that must stay consistent |
| `style` | Look, grade, photographic register |
| `unassigned` | Default. Attached but not yet given a job |

`edit_target` is the only one with a constraint: at most one per shot. Setting a second
clears the first.

Everything else can repeat. Two characters, three garments, all fine.

## 2. Storage

`elements` and `reference_assets` on `shots` are currently JSON arrays of plain strings.
Each entry becomes an object carrying its reference and its role.

**Existing rows must keep working.** Read must accept both shapes — a plain string is
treated as `unassigned`. Write always produces the new shape. Do not run a migration, do
not rewrite existing rows on read. Upgrade a row when it is next saved.

Order stays exactly as it is. Role is additional, not a replacement for position.

## 3. On the chip

Each chip already shows its position number, thumbnail and name.

Add the role:

- Visible on the chip, short enough not to break the layout
- Clickable to change, from the vocabulary in section 1
- `unassigned` is visually quieter than an assigned role, so unassigned ones are easy to
  spot
- `edit_target` is visually distinct from the rest. It is the one that changes how the
  whole prompt reads

Setting a second `edit_target` clears the first and shows that it did.

## 4. Role and order are independent

Changing a role must not change position. Reordering must not change roles.

They are two separate properties of the same attachment, and a later compiler reads both —
position for reference numbering, role for what the prompt says about each one.

---

## Constraints

- Additive. No new tables. The two existing columns change shape, tolerantly.
- Do not touch the Elements modal or the reference picker. Roles are set after attaching,
  on the chip.
- Do not change how Task 07 assembles or sends a payload. Roles are recorded now and
  consumed by a later task.
- Do not touch the provider layer or the job layer.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. Every attached element and reference asset carries a role, defaulting to `unassigned`.
2. A shot saved before this task still loads, with its references reading as
   `unassigned`.
3. Role can be changed from the chip.
4. At most one `edit_target` exists per shot, and setting a second clears the first.
5. Changing a role does not change order.
6. Reordering does not change roles.
7. Both survive a reload. Verify against the database.
8. Rendering from Task 07 behaves exactly as before.
9. Everything from Tasks 02 through 07 still works.

## Report back

- Files changed, one line each.
- Paste one shot's `elements` and `reference_assets` before and after roles are set, so
  the shape change and the preserved order can both be read.
- Confirm an old-shape row still loads.
- Anything in this spec that was wrong once the code was read.
- Anything deferred, and why.
