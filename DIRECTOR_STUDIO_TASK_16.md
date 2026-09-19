# Director Studio — Task 16: Scene Structure, Ordering, Keys

Repo: `dorelle/AI_API_DirectorStudio`
Read the Task 07 and Task 12 commits before starting.

Three parts. Part 1 is the reason for the task — renders need to land in a structure a
Resolve MCP bridge can read. Part 2 makes the ordering match. Part 3 is housekeeping.

There is **no export feature** in this task. No file writer, no filler shots, no copying,
no video generation of any kind. The bridge pulls from disk, so the only job is to put
things in the right place with the right names.

---

# Part 1 — Scene in the path

## Today

Assets file as `Client/Project/Shot/` with filenames `client_project_shot_filename.ext`.
The shot segment is the shot's slug. **Scene appears nowhere.**

## Required

For a **film project** where the shot has a scene, insert the scene between project and
shot:

```
Image_assets/videos/<Client>/<Project>/<SceneSlug>/<ShotSlug>/
```

Filename gains the scene segment, following the existing convention:

```
<client>_<project>_<scene>_<shot>_<filename>.<ext>
```

### Unchanged cases

- A shot with no scene files exactly as it does today
- A campaign project files exactly as it does today
- Generator runs outside the shot system file exactly as they do today
- **Existing assets on disk are not moved, renamed or migrated.** The Assets browser walks
  the tree, so older paths keep working

### Why slugs make this safe

Scene and shot slugs are fixed at creation and never renumber. So a path, once written,
stays valid no matter how the film is reordered. That was the point of the slug rule from
Task 04, and this is where it pays off.

## Document the convention

Write `RESOLVE_STRUCTURE.md` at the repo root describing:

- The exact directory layout, with a real example
- The exact filename format, with a real example
- Which cases produce which layout
- That slugs are stable and safe to match on
- Where takes land, and how an approved take is identified on disk

This file is what gets handed to the Resolve bridge. Make it precise enough to write a
matcher from, with no guessing.

## Approved takes on disk

Task 07 stores approval in the database, not on disk. A bridge reading the filesystem
cannot tell an approved take from a rejected one.

**Solution: the state goes in the filename.** The approved take carries `_approved` where
its numbered segment would be. The bridge matches on that and ignores everything else.

```
Dorelle_New_test_Run_SC002_SH001_approved.mp4     ← the bridge takes this
Dorelle_New_test_Run_SC002_SH001_take_2.mp4       ← ignored
Dorelle_New_test_Run_SC002_SH001_take_3.mp4       ← ignored
```

Rules:

- Approving a take renames its file to the `_approved` form
- Approving a different take reverts the previous one to its numbered name and renames the
  new one
- **At most one `_approved` file per shot directory, always.** The database already
  enforces one approved take per shot; the filesystem must match it
- Unapproving reverts the file to its numbered name
- The database record's stored path updates with the rename
- If a rename fails, the database is not changed and the failure is surfaced. The two must
  never disagree

No marker file. No database lookup required by the bridge.

## Filler is not the app's job

A shot with no approved take produces no file, and that is correct.

**Do not write filler clips.** No black video, no placeholder, no ffmpeg, no new
dependency. Gaps are handled in Resolve as a separate bin. The app renders takes and marks
one approved; nothing else.

---

# Part 2 — Assignment moves the shot

## Today

Assigning a shot to a scene sets membership only. Its `sort_order` does not change, so a
shot can belong to scene 2 while sitting between scene 5's shots in the strip. Flagged as
deferred in Task 12.

## Required

When a shot's `scene_id` changes, move its `sort_order` to sit **after the last shot of its
new scene**.

- Other shots keep their relative order
- Slugs never change
- Moving a shot out of a scene to unassigned leaves it where it is
- If the new scene has no shots, place it where that scene sits in scene order

This matters more now: `sort_order` is what the strip shows, and the directory structure
needs to reflect the film's real shape.

---

# Part 3 — API keys

Settings currently holds Gemini, Fal, Kling, Luma, BytePlus and OpenAI.

### Active

- **OpenAI** — done in Task 14
- **Google Gemini** — exists
- **Fal** — exists, handles video

### Add

- **Topaz** — a key field, verify and save, following the existing card pattern

### Dormant

- **BFL**
- **Clarity**

Add both as key fields so they can be filled later, but do not wire them to any provider
call. Mark them clearly as not yet in use.

No provider integration work in this task beyond storing and verifying keys.

---

## Constraints

- Additive. Existing assets are never moved, renamed or migrated.
- Do not change the render payload's shape.
- Do not change shot or scene slugs, ever.
- Do not touch the provider layer or the job layer beyond adding key storage.
- **Test harnesses must not copy `Image_assets`, `studio.db`, or any generated media.**
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. A film shot with a scene renders into a path containing the scene slug.
2. Its filename contains the scene segment.
3. A shot with no scene, and a campaign project, file exactly as before.
4. Existing assets still browse correctly in the Assets tab.
5. `RESOLVE_STRUCTURE.md` describes the layout precisely enough to write a matcher from.
6. The approved take carries `_approved` in its filename, and no other take does.
6a. Approving a different take renames both files, and the database paths follow.
6b. No filler or placeholder file is ever written.
7. Assigning a shot to a scene moves it to sit with that scene's shots.
8. Slugs are unchanged by any of the above. Verify against the database.
9. Topaz, BFL and Clarity key fields exist; Topaz verifies; the other two are marked
   dormant.
10. Everything from Tasks 02 through 15 still works.

## Report back

- Files changed, one line each.
- A real rendered path and filename, pasted.
- A shot directory listing after two renders and an approval, pasted, then again after
  approving the other take.
- Shot `sort_order` before and after a scene reassignment.
- `RESOLVE_STRUCTURE.md`, pasted.
- Anything deferred, and why.
