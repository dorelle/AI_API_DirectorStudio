# Director Studio — Task 13: Scene Cast

Repo: `dorelle/AI_API_DirectorStudio`
Read the Task 11 and Task 12 commits before starting.

Scenes exist. Talent elements exist. Nothing connects them, so there is no record of who is
in a scene, and no record of how they look in it.

A character's identity does not change across a film, but their wardrobe and look change
per scene. So the scene owns the dressed version, and the talent element stays the
unchanging source.

---

## Scope

In:

- A cast record: one character, in one scene, dressed for it
- Images on the cast record — the dressed reference sheet
- A Cast section in the scene editor
- Attaching a cast member to a shot in that scene

Out, and not to be started:

- Generating comp cards in the app. Images are picked or uploaded
- Resolving a view by shot size
- Lineage or staleness tracking when a talent element changes
- Garment as an element type

---

## 1. Who owns what

| | Owns | Changes |
| --- | --- | --- |
| Talent element | The identity — the face, the person | Rarely. It is the source |
| Scene cast record | How that person looks in **this** scene | Per scene |

One talent, many cast records. Wren in the trench in scene 4 and Wren in the suit in scene
9 are two cast records pointing at one talent element.

Changing the talent changes the source everyone was built from. Changing a cast record
changes one scene only.

## 2. The cast record

New table:

| Column | Notes |
| --- | --- |
| `id` | Primary key |
| `scene_id` | The scene that owns it |
| `element_id` | The talent element. The identity |
| `character_name` | Who they are in the story — may differ from the element's name |
| `look_name` | The wardrobe or state for this scene. "Trench", "BW", "Suit" |
| `handle` | The `@` handle, generated. See section 3 |
| `images` | JSON array of asset paths — the dressed reference sheet, **ordered** |
| `note` | |
| `sort_order` | Position in the scene's cast list |
| `created_at` | |

Deleting a scene deletes its cast records. Deleting a talent element does **not** delete
cast records that point at it — leave them, and show that the element is missing.

### Routes

- `GET /api/scenes/<id>/cast`
- `POST /api/scenes/<id>/cast`
- `PATCH /api/cast/<id>`
- `DELETE /api/cast/<id>`
- `POST /api/scenes/<id>/cast/reorder`

## 3. Handles

Generate a handle from the character name and the look: `@Wren_Trench`, `@Zoey_BW`.

- Generated on create, editable afterward
- Unique within a project. On collision, append a suffix and say so
- Use the existing sanitizer

The handle is recorded now. Nothing consumes it in this task — the compiler will.

## 4. Cast in the scene editor

A **Cast** section, collapsible, in the scene editor alongside Brief, Script, Environment,
Shots and Notes.

Each cast member shows as a card:

- The talent element's thumbnail, small, as the identity marker
- Character name and look name
- The dressed images as thumbnails
- The handle
- Edit and remove

Adding a cast member:

- Pick a talent element from the **existing** Elements modal, filtered to Characters
- Enter character name and look name
- Add images from the **existing** reference picker, multi-select

Reorder by drag. Field locking as everywhere else.

## 5. Cast on a shot

When a shot belongs to a scene, its Elements section can attach that scene's cast members
as well as plain elements.

- A cast member attaches like an element, with a role from Task 08 — normally `character`
- Attaching a cast member contributes **its images**, in order, to the reference list at
  render time, in the position it sits in the shot's array
- A cast member from a different scene cannot be attached. Only the shot's own scene's cast
- A shot with no scene behaves exactly as today

Cast members and plain elements can both be attached to the same shot. Do not merge them,
do not deduplicate them.

**Report how a cast member sits in the payload** relative to plain elements and reference
assets.

---

## Constraints

- Additive. New table, no changes to `shots` or `scenes` schemas beyond what is needed to
  reference cast.
- Reuse the existing Elements modal and reference picker.
- Do not change Task 08's roles.
- Do not change the payload for a shot with no cast attached.
- Do not touch the provider layer or the job layer.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. A cast member can be created in a scene from a talent element.
2. Character name, look name and images save and reload.
3. A handle is generated, is unique within the project, and can be edited.
4. One talent element can appear as cast in several scenes, independently.
5. Cast can be reordered within a scene.
6. Deleting a scene removes its cast. Deleting a talent element leaves cast records intact
   and visibly missing their element.
7. A cast member can be attached to a shot in its own scene.
8. A cast member from another scene cannot be attached.
9. Its images reach the payload in order, in its array position.
10. A shot with no cast attached produces an unchanged payload.
11. Everything from Tasks 02 through 12 still works.

## Report back

- Files changed, one line each.
- One cast record as stored, pasted.
- The payload for one shot with a cast member attached, and the same shot without.
- How cast images sit relative to plain elements and reference assets.
- Anything in this spec that was wrong once the code was read.
- Anything deferred, and why.
