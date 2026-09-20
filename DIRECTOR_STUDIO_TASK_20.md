# Director Studio — Task 20: Wan 3.0 and Workflow Fixes

Repo: `dorelle/AI_API_DirectorStudio`

Found while building the first real scene. Item 1 is blocking production right now.

---

## 1. Add Wan 3.0 reference-to-video

The catalog has Wan 2.7 but not Wan 3.0. Wan 3.0 is live on Fal and it is the right model for
dialogue coverage — it holds identity from reference images through the whole clip, which
image-to-video does not.

**Endpoint:** `alibaba/wan-3.0/reference-to-video`

### Schema, verbatim from Fal

| Field | Notes |
| --- | --- |
| `prompt` | Text directing how the reference media is used. **Reference media is addressed positionally** — "the subject in Image 1 walks past Video 1" |
| `reference_image_urls` | **Up to 10 reference images** |
| `reference_video_urls` | Up to 5, 15 seconds total, each at least 16 fps |
| `reference_audio_urls` | Up to 5, 15 seconds total |
| `resolution` | `480p`, `720p`, `1080p`. Default `1080p` |
| `aspect_ratio` | `adaptive`, `16:9`, `4:3`, `1:1`, `3:4`, `9:16`. Default `adaptive` |
| `duration` | Integer seconds. Default 5. Null means the model picks |
| `audio` | Boolean. **Defaults to true** |
| `enable_prompt_expansion` | Default true. Disabling saves 20-60s latency but degrades quality |
| `enable_thinking` | Default false |
| `seed` | |
| `enable_safety_checker` | Default true |
| `file_url` | Document to base the video on. Requires `enable_thinking` |
| `web_url` | Public webpage. Requires `enable_thinking` |

**No start image.** Reference mode only. Input mode resolution must know this — attaching a
first frame to a Wan 3.0 shot is a dropped input and should block per Task 19.

### Defaults for this project

- `audio` — **default to false.** This project renders silent and syncs audio afterward
- `enable_thinking` — **default to true.** Multi-subject prompts need it

Both must remain overridable per shot.

### Also add, if they exist on Fal

Check for other Wan 3.0 endpoints — image-to-video, text-to-video, first-and-last-frame — and
add them with distinct labels. **Do not label them identically.** See item 2.

### Pricing

Report what Fal publishes per second for each Wan 3.0 tier. If nothing is published, record
as `unknown`, never zero.

---

## 2. Engine labels must distinguish input modes

Task 19's root cause: the catalog lists three separate Fal endpoints as an identical
**"Kling 3.0 Pro"** — text-to-video, image-to-video and reference. The user picked one and
got text mode with everything dropped.

The render block catches it now, but only after the wrong pick.

**Every catalog label must say its input mode.** "Kling 3.0 Pro — text", "Kling 3.0 Pro —
image", "Kling 3.0 Pro — reference". Same for Wan and anything else with siblings.

Audit the whole catalog for duplicate labels and report how many were found.

---

## 3. Compiler must address references positionally

Wan 3.0's own documentation states that reference media is addressed positionally in the
prompt — "the subject in Image 1". Without it, the references are attached but the prompt
does not tell the model what to do with them.

`COMPILER_RULES` already says to address references by position. Code's Task 15 report found
**gpt-4o-mini ignores this rule.**

Two things:

- Report whether switching the Settings model to `gpt-4o` fixes it. Test the same shot on
  both and paste both outputs
- If it still fails, sharpen the rule. Make positional addressing the first instruction, not
  the third

This is the difference between references that work and references that are sent and ignored.

---

## 4. First and last frame cannot be set from Shot mode

The Generation section has First Frame and Last Frame fields with no picker. Setting them
requires the Video workspace, where Shot mode greys out and the shot stops driving the render.

- Add a picker to both, using the existing reference picker
- Add a drop target on each
- With both set on an engine that takes them, input mode must resolve to that mode, not text

---

## 5. Automatic resize for oversized references

Observed: `@Element1 frontal: 19.1 MB is over the 10 MB limit for this field.`

Comp cards are large. Model fields have caps. The app knows the cap and should resize rather
than fail at the edge.

**Resize before sending:** JPEG, quality 90, max 1500px on the long edge — the fix already
recorded for Kling O3 Pro. Apply wherever a field has a size limit.

Keep the original on disk. Only the transmitted copy is resized. Say in the report which
fields this covers.

---

## 6. Dialogue field is in the wrong place

Dialogue / VO sits in Content, step 1. The Prompt section is step 5. By compile time the field
that most affects the output is four sections away and easy to leave empty — and nothing says
so.

- Move Dialogue / VO to the Prompt section, or surface it there
- **Before compiling, say what the compiler is reading.** "No dialogue on this shot" is enough
  to catch the mistake before it costs a render

---

## 7. Takes should lay out horizontally

Takes are for comparing, and comparing wants side by side. They currently stack vertically, so
you scroll between them.

Horizontal scrolling row, newest first, approved one marked.

---

## 8. Duplicate shot

Common case: the same setup from a different angle. Shot 26 covered as four singles is one
shot duplicated four times with the camera changed.

Duplicate carries: content, dialogue, camera, elements, references, style, first and last
frame, engine, settings.

Duplicate does **not** carry: slug (new one assigned), takes, status, note.

Lands immediately after the original.

---

## 9. Upload a script into a scene

The scene's Brief and Script are paste-only. The project playbook already has `.md` upload
from Task 14 — apply the same pattern to both scene fields.

---

## 10. Add a gallery render to a shot as a take

A one-off render made in the Video workspace has nowhere to go. It lands in the gallery and the
shot never sees it.

- An **Add to shot** action on a gallery item, picking a target shot
- It becomes a take on that shot — approvable, and files under the shot's slug
- **Mark it as imported.** It has no prompt, no references and no engine recorded on the shot,
  unlike a take rendered from the row. That difference must be visible

---

## Constraints

- Do not change the `_approved` rule, the ID convention, or slug behavior.
- Do not change which prompt is sent to the render.
- Do not auto-select an engine.
- Test harnesses must not copy `Image_assets`, `studio.db`, or any generated media.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. Wan 3.0 reference-to-video is in the catalog and sends up to 10 reference images.
2. Audio defaults off and thinking defaults on for it, both overridable.
3. Attaching a first frame to a Wan 3.0 shot blocks, per Task 19.
4. No two catalog entries share a label. Input mode is in every label.
5. A compiled prompt addresses references positionally, or the report explains why not.
6. First and last frame can be set from Shot mode and resolve the input mode correctly.
7. An oversized reference is resized before sending instead of failing.
8. Dialogue is visible from the Prompt section, and the compiler says when it is empty.
9. Takes lay out horizontally.
10. A shot can be duplicated, with a new slug and no takes.
11. A script can be uploaded into a scene.
12. A gallery render can be added to a shot as an imported take.
13. Everything from Tasks 02 through 19 still works.

## Report back

- Files changed, one line each.
- The Wan 3.0 payload for one real shot, pasted.
- How many duplicate catalog labels were found.
- Both compiler outputs from item 3, `gpt-4o-mini` and `gpt-4o`.
- Which fields the resize covers.
- Anything deferred, and why.
