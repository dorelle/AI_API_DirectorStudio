# Director Studio — Task 11: Element Intake for All Types

Repo: `dorelle/AI_API_DirectorStudio`
Read `DIRECTOR_STUDIO_ORIENTATION.md` section 5 and the Task 10 commit before starting.

The New Talent modal is a good intake: drop an image, Gemini Vision extracts attributes
into fields, the user corrects them, save. It only works for talent.

This task generalizes it. Environment and Prop become element types with the same intake.
Style gets the scan too, writing into the record it already has from Task 10.

---

## Scope

In:

- Element types: Environment and Prop, alongside the existing Talent
- The same scan-and-extract intake for each, with its own attribute set
- A vocabulary file per type, following the `talent_vocabulary.json` pattern
- Style scan: drop a reference image, extract into the style's existing `text` field

Out, and not to be started:

- Garment as an element type. Deliberately excluded
- Editing vocabularies in the app. They are JSON files, edited directly
- Changing the Style record from Task 10, beyond adding the scan
- Lineage, comp card sheets, handle resolution

---

## 1. Element types

Talent is currently the only type. Add `environment` and `prop`.

Whatever currently identifies a talent record gains a type. Existing records are `talent`.
**Do not migrate anything** — treat a record with no type as `talent` on read, and write
the type when it is next saved.

The Elements modal gains type filtering, alongside the existing Characters category and
Pinned. Attaching an element to a shot is unchanged — any type can be attached, and
Task 08's roles already cover what each one is for.

## 2. Attribute sets

Each type has its own fields. Follow the New Talent modal's layout and behavior.

### Environment

| Field |
| --- |
| Name |
| Location type |
| Interior or exterior |
| Time of day |
| Materials |
| Light behavior |
| Palette |
| Period |
| Condition |
| Tags |
| Description |

### Prop

| Field |
| --- |
| Name |
| Category |
| Material |
| Scale |
| Period |
| Condition |
| Tags |
| Description |

### Talent

Unchanged. Do not alter the existing fields or their extraction.

## 3. Vocabularies

`talent_vocabulary.json` exists and is edited directly as a file. Follow that pattern
exactly.

- `environment_vocabulary.json`
- `prop_vocabulary.json`

Same structure, same loading mechanism, same place on disk. **No in-app editing.** If the
files are absent, extraction should still work with whatever the model returns.

## 4. Extraction

Reuse the Gemini Vision path the talent intake already uses. Do not build a second one.

Each type needs its own extraction prompt, asking for that type's attributes and
constrained by that type's vocabulary the way talent's already is.

The intake UI is the same for every type — the scanning state, the field list filling in,
the debug log, the correct-then-save flow. Only the fields and the prompt differ.

## 5. Style scan

Task 10's style record has `name`, `text` and `images`. Adding a style means typing the
text by hand.

Add a scan: drop or pick a reference image, Gemini Vision reads it and writes the style
description into `text`.

- **Image upload only.** No separate attribute fields, no vocabulary file for style
- What to extract: grade and color treatment, light quality and direction, palette, stock
  or sensor character, overall photographic register
- Output is prose in the style's `text` field, editable afterward like anything else
- The scanned image is added to the style's `images` array
- Keep it simple. A style is "Cloudy Day" or a dark cyberpunk alley — a short written
  treatment, not a document

The style record, its toggle, and how it reaches the payload are unchanged from Task 10.

---

## Constraints

- Additive. No renamed columns, no restructured tables, no migration.
- **Reuse the existing Gemini Vision path.** Do not add a second extraction mechanism.
- Do not change the talent intake's fields or extraction behavior.
- Do not change how elements attach to shots, or Task 08's roles.
- Do not change the Style record's shape or payload behavior from Task 10.
- No garment type.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. An element can be created as Environment or Prop, with its own fields.
2. Dropping an image scans and fills that type's fields.
3. Extraction is constrained by that type's vocabulary file where one exists, and still
   works where one does not.
4. Existing talent records load unchanged and are treated as `talent`.
5. The Elements modal can filter by type.
6. Any element type can be attached to a shot, and roles work as before.
7. A style can be created by dropping an image, and its text is written from the scan.
8. The scanned image lands in the style's `images` array.
9. Style attachment, toggle and payload behavior are identical to Task 10.
10. The talent intake behaves exactly as it did.
11. Everything from Tasks 02 through 10 still works.

## Report back

- Files changed, one line each.
- The extraction prompt used for each type, pasted.
- One scanned Environment record and one scanned Prop record, pasted as stored.
- One scanned Style's resulting text, pasted.
- Anything in this spec that was wrong once the code was read.
- Anything deferred, and why.
