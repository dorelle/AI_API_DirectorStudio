# Director Studio — Task 07: Render, Takes, Approval

Repo: `dorelle/AI_API_DirectorStudio`
Read `DIRECTOR_STUDIO_ORIENTATION.md` and the Task 04, 05 and 06 commits before starting.

The shot row now carries everything needed to generate: prompt, negative prompt, elements,
reference assets, first and last frame, camera, timing. Nothing turns it into a video.

This task does. It is the one where the shot list becomes a tool rather than a list.

---

## Scope

In:

- A `takes` table — every render of a shot, not just the keeper
- A render action on a shot
- Payload assembly from the shot row through the **existing** generation path
- Approval, separate from status
- Card and list reflecting take state

Out, and not to be started:

- Batch rendering the whole list. One shot at a time
- Resolve export, filler shots
- The preset compiler
- Image generation from a shot. Shots render video. A still is picked as a first frame
  using the Task 06 picker

---

## 1. Status and approval are different things

This is the central rule of the task and everything else follows from it.

| | Means | Lives on |
| --- | --- | --- |
| **Status** | What the system did — queued, rendering, done, failed | The shot row |
| **Approved** | What the user decided is good | A specific take |

A shot can be `done` and have nothing approved. Twelve takes can all be `done` and none
approved. They are never the same field and one must never be derived from the other.

**Approval lives on the take, not the row.** Approving a take makes it the one the row
will export. A later render produces a new take that arrives **unapproved**, and the
previously approved take stays approved until the user moves it.

A fresh render must never silently replace something already signed off.

## 2. The takes table

New table:

| Column | Notes |
| --- | --- |
| `id` | Primary key |
| `shot_id` | |
| `asset_path` | The rendered file, same path form the app already uses |
| `approved` | Boolean, default false |
| `cost` | What this take cost |
| `engine` | What actually rendered it, recorded at run time |
| `prompt_sent` | The exact prompt string sent, for later comparison |
| `status` | `queued`, `rendering`, `done`, `failed` |
| `error` | Failure text where there is one |
| `created_at` | |

**Keep every take.** Failed ones too. Bridge shots get rendered five times before they
work, and the losing takes are useful while a seam is being fixed.

At most one take per shot may be `approved`. Approving a second clears the first.

## 3. Routes

- `GET /api/shots/<id>/takes` — list, newest first
- `POST /api/shots/<id>/render` — start a render
- `PATCH /api/takes/<id>` — approve or unapprove
- `DELETE /api/takes/<id>` — remove a take

## 4. Rendering

**Reuse the existing generation path. Do not build a second one.**

The orientation report found `normalize_video_request`, `persist_generation_result` and an
async job layer. A shot render assembles the same payload a Generator video run assembles
and goes through the same machinery.

Assembling the payload from the shot row:

| Shot field | Goes to |
| --- | --- |
| `prompt` | The prompt |
| `negative_prompt` | The negative prompt |
| `first_frame` | Start image, where the model takes one |
| `last_frame` | End image, where the model takes one |
| `elements` + `reference_assets` | References, **in array order** |
| `duration_seconds` | Duration, where the model takes one |
| `engine` | Which model and provider |

Reference order matters. Providers number references positionally, and the arrays from
Task 05 are ordered deliberately. Elements first, then reference assets, unless the code
says otherwise — in which case say what it says.

### Engine

`engine` is free text today, which cannot drive a provider call. Make it a select
populated from the app's existing video model list. Reuse whatever the Generator's video
model dropdown is built from.

If `engine` is blank, fall back to whatever model is currently selected in the Generator's
settings panel, and record what was actually used on the take.

### Scope and filing

A shot render files like any other generation: the project record's client and project,
and the shot's slug as the shot segment. So a take lands where the shot's name says it
should.

### Chain from previous

If `chain_from_previous` is set and the previous shot has an approved take, use that
take's last frame as this shot's start image.

If the previous shot has no approved take, **do not render**. Say why. Do not fall back to
an unapproved take, and do not render without a start frame.

## 5. Where it appears

### On the card

- A render action
- While rendering, the card shows it is rendering
- The card image resolves in this order: **approved take, else latest done take, else
  first frame, else text placeholder**
- An approved card is visibly marked. A check or a border, your judgment, but unmistakable

### In the editor

A Takes section, collapsible like the rest:

- Every take, newest first, as thumbnails
- Each shows its cost, engine and time
- Approve and unapprove on each
- Delete on each
- Failed takes show their error rather than a broken thumbnail

### In the right-panel list

The status indicator already there gains an approved state. Reading the list top to bottom
should tell you how much of the film is actually finished.

## 6. Progress

Rendering is slow. Use whatever the app already does for generation progress — the
orientation report found an async job layer. Do not build a second progress mechanism.

The page must stay usable while a shot renders.

---

## Constraints

- **Reuse the existing generation path.** Add a caller, do not modify the provider layer,
  the job layer, or `persist_generation_result`. If a change to one of them is genuinely
  unavoidable, stop and report before making it.
- Additive. New table and new columns only.
- Do not change anything from Tasks 02 through 06 beyond what is described here.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. A shot renders, and a take is recorded.
2. The take lands in the right folder for its project and slug.
3. Rendering again produces a second take. The first is still there.
4. Approving a take marks it. Rendering again leaves the approved one approved.
5. Approving a second take clears the first.
6. A failed render records a failed take with its error, and does not lose the shot.
7. The card shows approved, then latest, then first frame, then text.
8. An approved shot is visibly distinct in both the card and the list.
9. Chain from previous uses the previous shot's approved take, and refuses clearly when
   there is not one.
10. References are sent in array order.
11. The page stays usable during a render.
12. The Generator's own video generation is byte-identical in behavior.
13. Everything from Tasks 02 through 06 still works.

## Report back

- Files changed, one line each.
- The exact payload assembled for one real shot, pasted, so it can be read directly.
- Whether anything in the provider or job layer had to change, and why.
- Paste the takes table for one shot after two renders and an approval.
- Anything in this spec that was wrong once the code was read.
- Anything deferred, and why.
