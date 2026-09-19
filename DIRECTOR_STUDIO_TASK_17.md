# Director Studio — Task 17: The ID Spine

Repo: `dorelle/AI_API_DirectorStudio`
Read the Task 04, 12 and 16 commits before starting.

Director Studio generates its own slugs. They do not match the ID convention the wider NeX
production system runs on, and that convention is what the Resolve round trip matches
against.

**Fix this before any real scene is rendered.** Slugs never renumber once written, so every
file made under the current scheme is permanently on the wrong convention.

---

## The conflict

| | Current | Required |
| --- | --- | --- |
| Scene | `New_test_Run_SC001` | `S02` |
| Shot | `New_test_Run_SH004` | `NEX01-S02-04-DETAIL-walk` |

The required form is:

```
{FILM}-S{scene}-{setup}-{CLASS}-{slug}
```

| Part | Meaning |
| --- | --- |
| `FILM` | Short code, fixed per project. `NEX01`, `RDOA-E01` |
| `S{scene}` | Two digits. Matches the scene unit — one location, one time of day |
| `{setup}` | Two digits. One per frame, one per prompt |
| `{CLASS}` | Exposure class: `FLASH`, `POP`, `DETAIL` |
| `{slug}` | Two or three words, human readable, no spaces |

Example: `NEX01-S02-04-DETAIL-walk`

### Why it has to match

The ID is assigned once and never renamed. Every downstream system inherits it — Flora node
labels, downloaded filenames, Resolve slug names, render folder filenames, delivery cuts.
The Resolve round trip works by matching filenames to slug names, and anything unmatched is
reported as still missing.

A different convention inside Director Studio breaks that match.

---

## 1. Film code on the project

New column on `projects`:

| Column | Notes |
| --- | --- |
| `film_code` | Short code. `NEX01`, `RDOA-E01`. Required for film projects |

Add it to the project create and edit UI. Validate: no spaces, no characters that break a
filename.

A film project without a film code cannot create scenes or shots. Say so plainly rather
than generating a fallback.

## 2. Scene numbers

Scenes currently get `<ProjectStem>_SC###`. They become **two-digit numbers** — `01`, `02` —
and the scene's full ID for display is `{FILM}-S{scene}`.

- Assigned at creation, never renumbered on reorder or insert. **Same rule as today**
- Two digits, zero padded
- The counter only increments, so a deleted scene's number is never reused

## 3. Shot IDs

A shot's slug becomes the full ID.

Three new columns on `shots`:

| Column | Notes |
| --- | --- |
| `setup` | Two digits, zero padded. One per shot, assigned at creation, never renumbered |
| `exposure_class` | `FLASH`, `POP`, or `DETAIL` |
| `name_slug` | Two or three words, human readable, no spaces. Hyphens between words |

The `slug` column holds the assembled ID and stays the immutable key.

### Assembly

`{project.film_code}-S{scene.number}-{shot.setup}-{shot.exposure_class}-{shot.name_slug}`

### Rules

- **Assembled once at creation and never reassembled.** If the exposure class or the name is
  edited afterward, the stored `slug` does not change. It is the permanent ID
- A shot with no scene cannot be assembled. Either require a scene at creation for film
  projects, or hold the shot unslugged until one is assigned — **say which you chose and
  why**
- Setup numbers are per scene, not per film. Scene 02 and scene 03 both have a setup 01

## 4. Exposure class

Three values: `FLASH`, `POP`, `DETAIL`.

A select in the shot editor, required at creation since it is part of the ID. Default to
`POP` if that is the sensible middle, but the user must be able to change it before the
shot is created — after creation it is fixed in the slug.

## 5. Existing data

There are test shots and scenes on the current convention.

**Do not migrate them and do not rename them.** Their files are on disk under those names.
Leave them.

Report how many exist, so they can be deleted by hand if wanted.

New scenes and shots use the new convention. A project with no `film_code` keeps working for
anything already created.

## 6. Paths and filenames

Task 16's structure holds. Only the segments change:

```
Image_assets/videos/<Client>/<Project>/<SceneID>/<ShotID>/
<client>_<project>_<sceneID>_<shotID>_approved.mp4
```

Update `RESOLVE_STRUCTURE.md` with the new form and a real example.

**The `_approved` rule from Task 16 is unchanged.**

---

## Constraints

- Additive. New columns only.
- Do not migrate or rename existing scenes, shots or files.
- Do not change the `_approved` filename rule.
- Do not change how slugs behave: assigned once, never renumbered, order independent.
- Do not touch the provider layer or the job layer.
- Test harnesses must not copy `Image_assets`, `studio.db`, or any generated media.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. A film project requires a film code, and says so when missing.
2. A new scene gets a two-digit number and displays as `{FILM}-S{number}`.
3. A new shot's slug is the full assembled ID.
4. Exposure class is set at creation and appears in the ID.
5. Editing the name or class afterward does not change the stored slug.
6. Setup numbers are per scene.
7. Reorder and insert never change any slug. Verify against the database.
8. Existing shots and scenes are untouched and still browse.
9. A render lands under the new path with the new filename.
10. `RESOLVE_STRUCTURE.md` matches what is actually written.
11. Everything from Tasks 02 through 16 still works.

## Report back

- Files changed, one line each.
- A real assembled scene ID and shot ID, pasted.
- A real rendered path and filename under the new convention.
- How shots with no scene are handled, and why.
- How many existing scenes and shots are on the old convention.
- Anything in this spec that was wrong once the code was read.
