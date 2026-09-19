# Director Studio — Task 05: The Shot Strip

Repo: `dorelle/AI_API_DirectorStudio`
Read `DIRECTOR_STUDIO_ORIENTATION.md` and the Task 04 commit (`6f85193`) before starting.

Task 04 built the shot list and editor. In Shot mode the center area still shows a single
asset in the viewer, which is the wrong thing to look at while building a sequence.

This task turns the center area into the sequence.

**It does not render anything.** Rendering, takes and approval are Task 06.

---

## Scope

In:

- A horizontal shot strip in the center area, active only in Shot mode
- Cards showing a frame where one is set, text where one is not
- A frame picker on the card
- Drag to reorder along the strip

Out, and not to be started:

- Rendering, takes, approval, cost
- The board as an unordered thinking canvas — this is the ordered strip, not that
- Script-to-shots, the preset compiler, export

---

## 1. The strip

When the Shot pill is active, the center area shows the shot strip instead of the single
asset viewer.

- Horizontal, left to right, in `sort_order`. It reads as a sequence
- Scrolls horizontally when it overflows
- Every other mode — Image, Video, Edit, Upscale — keeps the existing viewer, unchanged
- Leaving Shot mode restores the viewer as it was

The strip and the right-panel list are two views of one order. Reordering in either
updates the other live. **This is not a second ordering.**

## 2. The card

Three states, same card:

| State | Shows |
| --- | --- |
| Frame set | The image, with slug and scene over or under it |
| No frame | Slug and scene as text, on a placeholder |
| Selected | Same content, visibly selected |

Also on every card:

- The status indicator, consistent with the left-edge bar used in the right-panel list
- Position in the sequence

Clicking a card selects that shot. The editor in the bottom panel fills with it, and the
right-panel list selection follows. One selection, three surfaces.

Cards are a fixed size so the strip reads evenly. Portrait and landscape frames both have
to sit in that box without distorting — fit, do not stretch.

## 3. The frame picker

**On the card, not in the editor.** The card is where the eye already is.

- An empty card offers a pick action
- A card with a frame offers replace and clear
- Picking writes to the shot's `first_frame`
- The editor's First Frame field stays, shows the same value, and stays in sync

Use the existing reference-picker modal rather than building a new one. The orientation
report found a "Choose reference images" modal with Loved and References tabs — reuse it,
in single-select mode.

If dragging an image from the gallery onto a card is straightforward given how the page is
already wired, add it. If it is not, skip it and say so.

## 4. Reorder on the strip

Drag a card along the strip to reorder.

- Uses the same `POST /api/shots/reorder` route from Task 04
- **Never touches `slug`.** Same rule as before, verify it again
- The chain-clearing rule from Task 04 applies identically here: clear
  `chain_from_previous` on the moved row and on the row that gains a new predecessor
- The right-panel list updates without a reload

Insert and delete stay in the right-panel list for now. Do not duplicate them on the card
unless it falls out of the work for free.

---

## Constraints

- Additive. No renamed columns, no restructured tables, no removed routes.
- Do not touch the Generator's own modes, the Elements modal's own behavior, the provider
  layer, or the job layer.
- Do not change the shots schema from Task 04. This task is UI over the existing row.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. Shot mode shows the strip. Every other mode shows the viewer, unchanged.
2. Cards appear in `sort_order` and scroll horizontally.
3. A shot with a first frame shows it. A shot without shows slug and scene as text.
4. Picking a frame from a card saves it and the card updates.
5. The editor's First Frame field and the card stay in sync in both directions.
6. Clicking a card selects the shot in the editor and the right-panel list.
7. Dragging a card reorders, and the right-panel list follows.
8. **No slug changes on reorder.** Verify against the database again.
9. Portrait and landscape frames both sit in the card without distortion.
10. Campaign projects and no-project state are unchanged.
11. Everything from Tasks 02, 03 and 04 still works.

## Report back

- Files changed, one line each.
- Whether drag-from-gallery was feasible, and what was done.
- Paste slug and sort_order before and after a strip reorder.
- Anything in this spec that was wrong once the code was read.
- Anything deferred, and why.
