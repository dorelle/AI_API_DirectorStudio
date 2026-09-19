# Director Studio — Task 09: Chip Fix and Shot Guidance

Repo: `dorelle/AI_API_DirectorStudio`
Read the Task 08 commit (`965e155`) before starting.

Two parts. Part 1 is a bug from Task 08. Part 2 makes the shot editor explain itself.

---

# Part 1 — Bug: chips not updated for the new entry shape

## What is wrong

In the live app, Elements and Assets chips show `[object Object]` where the name or path
should be. Thumbnails are blank. No role pill appears on any chip.

The data is correct — the Task 08 report shows properly formed `{ref, role}` objects in the
database with the right roles. **This is display only.**

## Cause

Task 08 changed `elements` and `reference_assets` from arrays of strings to arrays of
objects. The chip renderer in `static/director_shots.js` still treats each entry as a bare
string, so it stringifies the object instead of reading its fields.

## The fix

The chip must read:

- `entry.ref` for the display name, the thumbnail lookup, and identity
- `entry.role` for the role pill

Check **every** path that builds a chip, not just the initial render. At minimum:

- Initial load of a shot
- After adding from the Elements modal
- After adding from the reference picker
- After removing a chip
- After a drag reorder
- After a role change

These may render through different code paths. Fix all of them.

## Verify on

- A shot whose entries are all new-shape objects
- A shot saved before Task 08, whose entries are still plain strings, which the read path
  maps to `unassigned` — chips must render correctly for these too
- Both the Elements section and the Assets section

## Also check

Anywhere else an entry might be rendered or compared as a string. Search for uses of these
two arrays across the front end and confirm each one handles the object shape. Report
anything else found.

---

# Part 2 — Show the order of operations

## The problem

Opening the Shot editor gives no indication of what to do first. The sections are in an
order, but nothing says the order matters, and nothing says what is still missing before a
shot can render.

## 2.1 Numbered sections

Put a step number on each section header, in working order:

| Step | Section | Why here |
| --- | --- | --- |
| 1 | Content | What happens in the shot |
| 2 | Camera | How it is shot |
| 3 | Elements | Who is in it |
| 4 | Assets | What else it references |
| 5 | Prompt | What gets sent |
| 6 | Generation | First frame, engine, chaining |
| 7 | Takes | Render and choose |

Timing and Notes get no number. They can be filled at any point.

**This is a working order, not an enforced one.** Nothing is blocked by skipping a step.
Sections remain collapsible and independently editable exactly as they are now.

If the current section order in the editor does not match this, reorder to match.

## 2.2 Section state

Each numbered section header shows whether it has content:

- Empty — quiet, no emphasis
- Has content — a count or a check, using whatever the Elements and Assets sections already
  do for their counts

Someone scanning the editor should see at a glance which steps are done.

## 2.3 Readiness on the Render button

**The most useful guidance is telling the user why they cannot render yet.**

Before rendering, a shot needs at minimum a prompt or a first frame, and an engine
resolved — either set on the shot or selected in the Generator.

When those are not met:

- The Render button is disabled
- Hovering it, or an adjacent hint, says what is missing in plain words — "Needs a prompt
  or a first frame", "No engine selected"

When chaining is on and the previous shot has no approved take, say that too. Task 07
already enforces this server-side with a 400 and a reason. Surface the same reason before
the click rather than after.

## 2.4 A short guide

One collapsible strip at the top of the Shot panel, collapsed by default, remembered like
every other collapse state.

Open, it lists the seven steps in one line each. Plain sentences, no jargon. Enough to
remind, not a manual.

## 2.5 Empty-state hints

Where a section is empty, one quiet line of placeholder text saying what goes there. The
Takes section already does this — "No takes yet. Render the shot to make one." Match that
tone and brevity for the others.

---

## Constraints

- Additive. No schema changes.
- Do not change how any field saves.
- Do not block or gate any action except the Render button as described in 2.3.
- Do not touch the provider layer or the job layer.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. Elements and Assets chips show the correct name and thumbnail, never `[object Object]`.
2. Role pills appear on every chip.
3. Chips render correctly after add, remove, reorder and role change.
4. Old-shape rows render correctly as `unassigned`.
5. Numbered sections appear in working order.
6. Section headers show whether they have content.
7. The Render button is disabled with a readable reason when a shot is not ready.
8. The chain case gives its reason before the click, not after.
9. The guide strip opens, closes, and remembers its state.
10. Everything from Tasks 02 through 08 still works.

## Report back

- Files changed, one line each.
- Every chip render path found, and confirmation each was fixed.
- Anything else found that treated an entry as a string.
- The exact readiness conditions implemented, and the wording used for each.
- Anything deferred, and why.
