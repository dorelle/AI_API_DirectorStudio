# Resolve structure — where Director Studio puts rendered takes

This is the on-disk contract for a Resolve bridge. The bridge reads the filesystem only;
it needs no database and no marker files. Everything below is verified by
`test_task16.py` and `test_task17.py` against the code in `nbs.py` (Tasks 16 and 17).

## IDs (Task 17: the ID spine)

Scenes and shots carry the production-wide ID convention. Both are assigned once, at
creation, and never renamed.

```
Scene ID   {FILM}-S{scene}                              NEX01-S02
Shot ID    {FILM}-S{scene}-{setup}-{CLASS}-{slug}       NEX01-S02-01-DETAIL-walk
```

| Part | Meaning |
|---|---|
| `FILM` | The project's film code: upper-case letters, digits, hyphens (`NEX01`, `RDOA-E01`). Fixed per project once any scene carries it. |
| `S{scene}` | Two-digit scene number from a per-project counter that only increments (a deleted scene's number is never reused). |
| `{setup}` | Two-digit setup number, **per scene**, from a per-scene counter that only increments. Scene 02 and scene 03 both have a setup 01. |
| `CLASS` | Exposure class: `FLASH`, `POP` or `DETAIL`. Set at creation. |
| `{slug}` | One to three human-readable words, hyphenated, kept in the case typed. |

Editing a shot's class or name afterwards relabels it in the app only; the stored ID (and
therefore every path below) does not change. Reordering, inserting or moving a shot to
another scene never changes an ID either: a shot moved from S02 to S03 keeps `…-S02-…`.

Numbers exceed two digits only past 99 (`S100`); nothing is truncated.

## Root

```
<app>/Image_assets/videos/
```

Served by the app as `/videos/<relative path>`. Directory and file names below are
relative to this root.

## Directory layout

| Case | Directory |
|---|---|
| Film project, shot **has a scene** | `<Client>/<Project>/<SceneID>/<ShotID>/` |
| Film project, shot has **no scene** (moved out of its scene) | `<Client>/<Project>/<ShotID>/` |
| Campaign project, or a Generator run outside the shot system | `<Client>/<Project>/<Shot text>/` (unchanged legacy layout; `uncategorized` fills any blank) |

`<Client>` and `<Project>` are the project's client and name as typed, with only the
characters `< > : " / \ | ? *` replaced by `_` and trailing dots/spaces trimmed. Spaces
are kept in directory names (`Night Bus`). Slugs contain no spaces.

Real example (film, scene assigned):

```
Image_assets/videos/Lumen/Night Bus/NEX01-S02/NEX01-S02-01-DETAIL-walk/
```

Real example (film, shot moved out of its scene):

```
Image_assets/videos/Lumen/Night Bus/NEX01-S01-01-POP-walk/
```

The bridge can tell the two apart by depth: a shot directory that sits **four** levels
below the root has a scene; **three** levels means no scene. Scene directories match
`^[A-Z0-9-]+-S\d{2,}$`, shot directories `^[A-Z0-9-]+-S\d{2,}-\d{2,}-(FLASH|POP|DETAIL)-[A-Za-z0-9-]+$`.

Renders made before Task 17 (the app's own earlier naming) sit under the same roots as
`<ProjectStem>_SC###/<ProjectStem>_SH###/` and `<ProjectStem>_SH###/`. They were not
moved or renamed; the bridge may ignore anything not matching the patterns above.

## Filename format

```
<client>_<project>_<sceneID>_<shotID>_<filename>.<ext>    (shot has a scene)
<client>_<project>_<shotID>_<filename>.<ext>              (no scene)
```

Each segment is the directory segment with whitespace collapsed to `_` (so `Night Bus`
becomes `Night_Bus`). `<filename>` is always `take` for a shot render. The extension is
whatever the provider delivered, normally `mp4`.

Real example:

```
Lumen_Night_Bus_NEX01-S02_NEX01-S02-01-DETAIL-walk_take.mp4
```

The scene ID appears twice (once on its own, once inside the shot ID). That is the
existing filename convention; do not parse the name — match on the shot ID, which is the
unambiguous part, and on the `_approved` segment.

Next to every video the app writes:

- `<same stem>.json` — the generation metadata (prompt, model, `assetRelpath`, …)
- `<same stem>_poster.png` — a poster frame, when the provider supplied one

The bridge ignores both; they rename together with the video.

## Numbered takes

The first take of a shot is `…_take.mp4`; further takes get a counter: `…_take_2.mp4`,
`…_take_3.mp4`, and so on. The counter is "next free name in this directory", **not**
chronological order — after an approval frees a numbered name, a later render can reuse
it and the un-approved take then moves to the next free number. Never infer order from
the number; use the app if order matters.

## Approved take

Approval lives in the filename. The approved take's numbered segment (`_take`,
`_take_2`, …) is replaced by `_approved`:

```
Lumen_Night_Bus_NEX01-S02_NEX01-S02-01-DETAIL-walk_approved.mp4     <- the bridge takes this
Lumen_Night_Bus_NEX01-S02_NEX01-S02-01-DETAIL-walk_take.mp4         <- ignored
Lumen_Night_Bus_NEX01-S02_NEX01-S02-01-DETAIL-walk_take_2.mp4       <- ignored
```

Guarantees:

- **At most one `*_approved.<ext>` per shot directory.** Approving another take first
  renames the previous one back to a numbered name, then renames the new one.
- Un-approving renames the file back to a numbered name; the directory then has no
  `_approved` file at all.
- The sidecar and poster follow: `…_approved.json`, `…_approved_poster.png`.
- The database path is updated only after every rename succeeded. If a rename fails
  (file missing, name taken), nothing changes on disk or in the database and the editor
  shows the error. Disk and database never disagree.
- Only finished takes can be approved; failed renders never produce a file.

Matcher: `glob("<root>/*/*/*-S??/*-S??-??-*/*_approved.*")` for film shots with scenes,
then take the shot ID from the directory name (or from the filename between
`<sceneID>_` and `_approved`).
A shot directory with no `_approved` file has no approved take — that is a gap, and the
app writes **nothing** for it: no filler, no placeholder, no black clip. Handle gaps in
Resolve.

## IDs are stable

Scene and shot IDs are assigned once at creation (see **IDs** above) and are never
renumbered, reused or changed by reordering, reassigning a shot to another scene,
editing its name or class, or deleting other shots or scenes. A path, once written,
stays valid. Match on IDs.

Order (what goes before what) is *not* in the slug number. Read it from the app
(`sort_order` in the shots and scenes API) when the bridge needs to lay clips out.

## What never changes

- Assets that already exist on disk are never moved, renamed or migrated. Older renders
  stay at `<Client>/<Project>/<ShotSlug>/` even after the shot gets a scene; only new
  renders land in the scene path.
- Campaign projects and plain Generator runs keep their layout exactly.
