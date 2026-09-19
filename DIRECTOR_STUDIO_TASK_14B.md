# Director Studio — Task 14b: Temp Cleanup

Repo: `dorelle/AI_API_DirectorStudio`

A test harness during Task 14 copied `Image_assets` to `%TEMP%` on every run without
cleaning up. 54 copies accumulated, roughly 42 GB, and filled the C: drive.

The harness is fixed. This task adds a way to find and clear leftovers from the app itself,
and sets a standing rule so it does not recur.

---

## 1. Settings — Disk and temp

A new card on the Settings page.

### What it shows

- Total size of `Image_assets` and its main subfolders
- Total size of app-created temp directories, with a count
- Free space on the drive the app is running from

### What it clears

A **Clean temp files** action that removes **only directories the app created**.

**Scoping is the whole of this task.** Identify app-created temp folders by a naming
convention the app controls — a fixed prefix, or a marker file written inside each. Never
by a general sweep of the temp directory.

Before deleting:

- List exactly what will be removed, with paths and sizes
- Require explicit confirmation
- Never delete anything under `Image_assets`, `config.json`, `studio.db`, or the project
  folder

After deleting, report what was removed and how much space was freed.

### If nothing is found

Say so. Do not offer a clean action that does nothing.

## 2. Standing constraint — test harnesses

Add this to the repo's contributor notes, or create `CLAUDE.md` at the root if there is no
such file, under a heading that a future task will read.

> **Test harnesses must not copy `Image_assets`, `studio.db`, or any generated media.**
> Build fixtures instead. If a test genuinely requires a copy of the asset tree, exclude
> media files by extension, write it to a directory with the app's temp prefix, and delete
> it in a `finally` block whether the test passes or fails. A test run must leave no
> directory behind.

---

## Constraints

- Deletion is scoped to app-created directories only, identified by a convention the app
  controls.
- No deletion without an explicit confirmation showing what will go.
- Read-only size reporting is fine without confirmation.
- Do not touch the provider layer, the job layer, or any generation path.
- No new dependencies.
- Match existing Settings card style.

## Done means

1. Settings shows asset sizes, temp sizes and free space.
2. Clean temp lists what it will remove before removing it.
3. It removes only app-created directories.
4. `Image_assets`, `config.json` and `studio.db` cannot be touched by it.
5. With nothing to clean, it says so.
6. The standing constraint is written into the repo.
7. Everything else still works.

## Report back

- Files changed, one line each.
- How app-created temp directories are identified.
- What was found and cleaned on a real run.
- Where the standing constraint was written.
