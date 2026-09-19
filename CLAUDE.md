# Directors Studio - contributor notes

Read `DIRECTOR_STUDIO_ORIENTATION.md` first. Tasks arrive as `DIRECTOR_STUDIO_TASK_NN.md`
files at the repo root; each is additive to the last.

## Working rules

- Additive only: new tables and columns through the `ensure_*_columns` /
  `CREATE TABLE IF NOT EXISTS` pattern in `init_studio_db()`. Never rename, restructure or
  migrate; read old shapes tolerantly and upgrade a row when it is next saved.
- Do not touch the provider layer (`run_*_job`), the job layer (`ASYNC_JOBS`,
  `_run_*_async_job`) or `persist_*_result`. Add callers, do not modify them.
- Match the surrounding style. No refactoring of adjacent code.
- No new dependencies. The first-run bootstrap list in `nbs.py` stays as it is.
- `nbs.py` starts with a UTF-8 BOM. Read it with `utf-8-sig` and write it back with exactly
  one BOM. A double BOM stops the app from starting.
- Pages are served with `Cache-Control: no-store`; bump the `?v=` on a static file when it
  changes.

## Test harnesses

**Test harnesses must not copy `Image_assets`, `studio.db`, or any generated media.**
Build fixtures instead. If a test genuinely requires a copy of the asset tree, exclude
media files by extension, write it to a directory with the app's temp prefix, and delete
it in a `finally` block whether the test passes or fails. A test run must leave no
directory behind.

The app's temp convention is `APP_TEMP_PREFIX` (`dstudio_tmp_`) plus a marker file
`APP_TEMP_MARKER` (`.dstudio_temp`) inside the directory; `make_app_temp_dir()` in
`nbs.py` creates one correctly. Settings > Disk and temp lists and removes only
directories that follow it.
