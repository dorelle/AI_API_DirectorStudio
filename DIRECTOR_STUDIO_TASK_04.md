# Director Studio — Task 04: The Shot List

Repo: `dorelle/AI_API_DirectorStudio`
Read `DIRECTOR_STUDIO_ORIENTATION.md` and the Task 02 and 03 commits before starting.

This is the first task that builds the Director Studio itself rather than the ground under
it. It creates the shot row and the two surfaces for working with it.

**It does not render anything.** Rendering, takes and approval are Task 05. A shot list
that persists and can be built, ordered and edited is the whole of this task.

---

## Scope

In:

- A `shots` table, belonging to a project
- A shot list in the right panel, ordered, reorderable
- A shot editor in the bottom panel, behind a new pill
- Field locking

Out, and not to be started:

- Rendering a shot, takes, approval, cost
- The board or any thinking canvas
- Script-to-shots or any generation of rows
- The preset compiler, prompt compilation, engine routing
- Resolve export or filler shots

---

## 1. The shot row

New table. Follow the pattern used for `projects` in Task 02.

### Identity

| Column | Notes |
| --- | --- |
| `id` | Primary key |
| `project_id` | The project it belongs to |
| `slug` | **Fixed at creation. Never changes.** See below |
| `scene` | Free text — the scene or sequence it belongs to |
| `sort_order` | Position in the list. Independent of slug |

**The slug is the critical rule.** It is assigned when the row is created and never
changes, no matter where the row moves or what is inserted around it. Order lives in
`sort_order`, identity lives in `slug`, and the two are unrelated.

This is what makes insertion free. Continuity gaps get found late, a shot is needed
between two existing ones, and renumbering downstream would break every filename already
written. Do not renumber anything, ever.

Generate the slug from the project and a counter that only increments. Reuse the existing
sanitizer so it is filename-safe.

### Timing

| Column | Notes |
| --- | --- |
| `duration_seconds` | |
| `beat_marker` | Free text — where it lands in the beat map |

Leave snapped frame count out. It is engine-dependent and there is no engine routing yet.

### Content

| Column | Notes |
| --- | --- |
| `action_text` | What happens in the shot |
| `dialogue` | Dialogue or VO line |
| `audio_cue` | SFX, ambience, music note |

### Camera

| Column | Notes |
| --- | --- |
| `shot_size` | Wide, medium, close |
| `angle` | Eye level, low, high, overhead |
| `movement` | Up to three, stored as JSON array |
| `lens` | Lens and focal length |
| `aperture` | |
| `speed_ramp` | |

These are free text or simple selects for now. The preset vocabulary is a later task.

### Generation

| Column | Notes |
| --- | --- |
| `first_frame` | Asset path or reference |
| `last_frame` | Asset path or reference |
| `chain_from_previous` | Boolean — inherit first frame from the previous shot's last frame |
| `engine` | Free text for now |

### State

| Column | Notes |
| --- | --- |
| `status` | `empty`, `queued`, `rendering`, `done`, `rejected` |
| `locked_fields` | JSON array of field names — see section 4 |
| `note` | Free text |
| `created_at` | |

Do not add approval or take columns. Those are Task 05.

## 2. Routes

Follow the conventions used for `/api/projects`.

- `GET /api/shots?project_id=` — list, ordered by `sort_order`
- `POST /api/shots` — create
- `GET /api/shots/<id>`
- `PATCH /api/shots/<id>`
- `DELETE /api/shots/<id>`
- `POST /api/shots/reorder` — takes an ordered list of ids, rewrites `sort_order` only

Reorder must never touch `slug`.

## 3. The two surfaces

### 3.1 Right panel — Shots or Library

The right panel currently holds twelve filter dropdowns for browsing assets. Keep them.

Add a toggle at the top of that panel, where the VIDEOS / Filter / Select control already
sits:

- **Shots** — the ordered shot list for the active project
- **Library** — the existing filters, unchanged

Shots is only available when the active project is `type = film`. For a campaign project
or no project, the toggle does not appear and the panel behaves as today.

The list:

- One row per shot, in `sort_order`
- Shows slug, scene, and a status indicator
- **Status is a color dot or a left edge bar, not text.** The column is narrow
- Click a row to select it. The editor in 3.2 fills with that row
- Drag to reorder
- Add and delete actions

**Dragging breaks the chain.** When a row is moved, set its `chain_from_previous` to false
rather than silently rewiring it to a different neighbor. The user re-sets it deliberately.

### 3.2 Bottom panel — a Shot pill

Add a fifth pill beside Image, Video, Edit, Upscale, called **Shot**.

- Only present when the active project is `type = film`
- Clicking it swaps the bottom panel from the freeform prompt box to the shot fields
- The fields shown are those of whichever row is selected in 3.1
- Edits save to that row

**The freeform prompt box does not go away.** Video and Image stay exactly as they are for
one-off generations. Shot is a different mode of the same panel, not a replacement.

Group the fields into collapsible sections — Timing, Content, Camera, Generation, Notes —
so the panel is not a wall of inputs. Remember collapse state.

## 4. Field locking

Any field edited by hand gets its name added to `locked_fields` on that row.

Nothing in this task writes to a row automatically, so nothing tests this yet. It is here
because later tasks generate rows in bulk, and those passes must skip locked fields and
fill only empty ones. Retrofitting this later means reworking every writer.

- Editing a field in the shot editor marks it locked
- Show a lock indicator on locked fields
- Clicking the indicator unlocks the field
- No generated writer exists yet, so nothing consumes this. Build the mechanism anyway

## 5. Insert between

An insert action on a row that creates a new shot directly after it, with a fresh slug and
a `sort_order` that places it between its neighbors.

This is the normal case, not an edge case. Continuity gaps are found after the fact.

If `chain_from_previous` is meaningful for the new row, set it from the row above. Do not
attempt to also wire the row below.

---

## Constraints

- Additive. No renamed columns, no restructured tables, no removed routes.
- Do not touch the Generator, the Elements modal, the provider layer, or the job layer.
- Do not touch the project record work from Task 02 or the templates from Task 03.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. A film project can hold shots, and they persist across restart.
2. The right panel toggles between Shots and Library, and Library is unchanged.
3. Shots appear in order with a visible status indicator.
4. Rows can be added, deleted, and dragged into a new order.
5. **Reordering does not change any slug.** Verify directly against the database.
6. Insert between two rows produces a new row with a new slug and no renumbering.
7. Selecting a row fills the Shot editor, and edits save.
8. Editing a field marks it locked, and the lock can be cleared.
9. Campaign projects and no-project state are unchanged — no Shots toggle, no Shot pill.
10. Everything from Tasks 02 and 03 still works.

## Report back

- Files changed, one line each.
- The slug format chosen, with an example from a real row.
- Paste the `slug` and `sort_order` columns of a five-row list before and after a reorder
  and an insert, so the slug stability can be read directly.
- Anything in this spec that was wrong once the code was read.
- Anything deferred, and why.
