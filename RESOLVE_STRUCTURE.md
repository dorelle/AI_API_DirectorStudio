# Resolve structure — where Director Studio puts rendered takes

This is the on-disk contract for a Resolve bridge. The bridge reads the filesystem only;
it needs no database and no marker files. Everything below is verified by
`test_task16.py` against the code in `nbs.py` (Task 16).

## Root

```
<app>/Image_assets/videos/
```

Served by the app as `/videos/<relative path>`. Directory and file names below are
relative to this root.

## Directory layout

| Case | Directory |
|---|---|
| Film project, shot **has a scene** | `<Client>/<Project>/<SceneSlug>/<ShotSlug>/` |
| Film project, shot has **no scene** | `<Client>/<Project>/<ShotSlug>/` |
| Campaign project, or a Generator run outside the shot system | `<Client>/<Project>/<Shot text>/` (unchanged legacy layout; `uncategorized` fills any blank) |

`<Client>` and `<Project>` are the project's client and name as typed, with only the
characters `< > : " / \ | ? *` replaced by `_` and trailing dots/spaces trimmed. Spaces
are kept in directory names (`Night Bus`). Slugs contain no spaces.

Real example (film, scene assigned):

```
Image_assets/videos/Lumen/Night Bus/Night_Bus_SC002/Night_Bus_SH001/
```

Real example (film, no scene):

```
Image_assets/videos/Lumen/Night Bus/Night_Bus_SH002/
```

The bridge can tell the two apart by depth: a shot directory that sits **four** levels
below the root has a scene; **three** levels means no scene. Scene directories match
`^.+_SC\d{3}$`, shot directories `^.+_SH\d{3}$`.

## Filename format

```
<client>_<project>_<scene>_<shot>_<filename>.<ext>        (shot has a scene)
<client>_<project>_<shot>_<filename>.<ext>                (no scene)
```

Each segment is the directory segment with whitespace collapsed to `_` (so `Night Bus`
becomes `Night_Bus`). `<filename>` is always `take` for a shot render. The extension is
whatever the provider delivered, normally `mp4`.

Real example:

```
Lumen_Night_Bus_Night_Bus_SC002_Night_Bus_SH001_take.mp4
```

Yes, the project stem appears twice (once as the project segment, once inside each slug).
That is the existing convention; do not parse the name — match on the slugs, which are
the unambiguous part.

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
Lumen_Night_Bus_Night_Bus_SC002_Night_Bus_SH001_approved.mp4        <- the bridge takes this
Lumen_Night_Bus_Night_Bus_SC002_Night_Bus_SH001_take.mp4            <- ignored
Lumen_Night_Bus_Night_Bus_SC002_Night_Bus_SH001_take_2.mp4          <- ignored
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

Matcher: `glob("<root>/*/*/*_SC???/*_SH???/*_approved.*")` for film shots with scenes.
A shot directory with no `_approved` file has no approved take — that is a gap, and the
app writes **nothing** for it: no filler, no placeholder, no black clip. Handle gaps in
Resolve.

## Slugs are stable

`<SceneSlug>` = `<ProjectStem>_SC###`, `<ShotSlug>` = `<ProjectStem>_SH###`. Both are
assigned once from a per-project counter at creation and are never renumbered, reused
or changed by reordering, reassigning a shot to another scene, or deleting other shots
or scenes. A path, once written, stays valid. Match on slugs.

Order (what goes before what) is *not* in the slug number. Read it from the app
(`sort_order` in the shots and scenes API) when the bridge needs to lay clips out.

## What never changes

- Assets that already exist on disk are never moved, renamed or migrated. Older renders
  stay at `<Client>/<Project>/<ShotSlug>/` even after the shot gets a scene; only new
  renders land in the scene path.
- Campaign projects and plain Generator runs keep their layout exactly.
