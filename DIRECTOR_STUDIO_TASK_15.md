# Director Studio — Task 15: The Prompt Compiler

Repo: `dorelle/AI_API_DirectorStudio`
Read the Task 08, 10, 13 and 14 commits before starting. **Task 14 must be complete** —
this reuses its OpenAI plumbing.

Every task since Task 05 has been building this one's input. The shot row now carries
content, camera, elements with roles, cast with looks, a style, a scene with a brief and a
script, and a project playbook. Nothing assembles them into a prompt. The user still types
it by hand.

This task writes it.

---

## The principle

From the reference material, and it governs the whole design:

> Don't write the prompts yourself. Have the agent write them, then review and correct them
> yourself.

And:

> Lock what's already established in the references. Describe only the changes and missing
> information. Replace vague style words with visible instructions.

So the compiler does not re-describe the world. The playbook established it, the scene
narrowed it, the references carry identity. The prompt says what is **different about this
shot**.

---

## Scope

In:

- A compile action on a shot that writes its prompt
- Full context assembly, reusing Task 14's digest work where possible
- Field locking respected absolutely
- A review step before anything is written
- Separate scene and motion prompts

Out, and not to be started:

- Compiling a whole scene or list at once. One shot at a time
- Generating shot rows from a script
- Changing the render payload's shape
- Any write the user has not approved

---

## 1. Scene prompt and motion prompt are different

A shot's prompt field currently serves both the still and the video, and they need
different text.

> Describe the motion, not the whole scene. The starting image already shows the scene.

New column on `shots`:

| Column | Notes |
| --- | --- |
| `scene_prompt` | Describes the frame. Used when generating a first frame |
| `prompt` | Existing. Now the motion prompt — what moves, camera, what must not change |

**Migration:** existing `prompt` values stay in `prompt`. Do not move or copy them.
`scene_prompt` starts empty on existing rows.

The render payload continues to send `prompt`, unchanged from Task 07. `scene_prompt` is
not sent anywhere in this task — it exists for the user to carry to the Generator when
making a first frame.

Both are compilable, separately, and both lock independently.

## 2. What the compiler sees

Assemble per shot:

| Source | What |
| --- | --- |
| Project | Playbook, register, aspect ratio, format |
| Scene | Brief, environment element, the relevant part of the script |
| Shot | Action, dialogue, audio cue, timing |
| Camera | Shot size, angle, movement, lens, aperture, speed ramp |
| Elements | Each attached element **with its role** |
| Cast | Attached cast members: character name, look name, handle |
| References | Each reference asset **with its role** |
| Style | Text and image count, if attached and enabled |
| Neighbors | The previous and next shot's action text, for continuity |

**Roles are the point.** A reference with role `edit_target` is the composition the model
edits. One with role `character` establishes appearance. The compiled prompt must say which
is which, by position — references are numbered positionally at render time, and the prompt
has to address them the same way.

Report the reference numbering scheme used and confirm it matches Task 07's assembly order.

## 3. Compiler instructions

The system prompt is:

1. The user's **Agent instructions** from Task 14's Settings field
2. Compiler-specific rules, below
3. The assembled context from section 2

The compiler rules are the only defaults to write:

- Say what is different about this shot. Do not re-describe what the playbook, scene or
  references already establish
- Name what must stay unchanged from the references, explicitly
- Address references by position, matching how they are sent
- Replace vague style words with visible, checkable instructions
- Subject first, then setting, then framing
- For a motion prompt: one clear action, what moves, what the camera does, what must not
  change. Not the whole scene
- Output prose, not JSON, not bullet points, not headings

Keep these rules in one place in the code, clearly marked, so they can be edited.

## 4. Locking is absolute

If `prompt` or `scene_prompt` is locked, the compiler **does not write it**. Not with a
warning, not with a merge. It says the field is locked and stops.

This is what field locking has existed for since Task 04.

## 5. Review before write

Nothing is written without the user seeing it.

- Compile produces the text and shows it beside the current value
- Accept writes it and marks the field locked, since the user has now approved it
- Discard writes nothing
- Recompile replaces the proposal, not the field

**No silent writes under any circumstances.**

## 6. Where it lives

A compile action in the Prompt section of the shot editor, one per field.

Show while compiling. Show the cost or token count of the call if that is available without
extra work.

---

## Constraints

- Reuse Task 14's OpenAI call path and Settings key. Do not add a second one.
- Do not change the render payload from Task 07.
- Do not write any field without explicit user acceptance.
- Do not compile a locked field.
- Do not touch the provider layer or the job layer.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. `scene_prompt` exists, saves, reloads and locks independently of `prompt`.
2. Existing shots keep their `prompt` untouched and get an empty `scene_prompt`.
3. Compiling a prompt produces text that reflects the playbook, scene, cast, style, camera
   and roles.
4. The compiled prompt addresses references by position, matching the render order.
5. A locked field is never compiled into.
6. Nothing is written until accepted.
7. Accepting writes the field and locks it.
8. The render payload is unchanged.
9. With no OpenAI key, compile says so and does nothing.
10. Everything from Tasks 02 through 14 still works.

## Report back

- Files changed, one line each.
- The compiler rules block, pasted.
- One real shot's assembled context, and the prompt it produced.
- The reference numbering scheme, and confirmation it matches Task 07.
- Anything in this spec that was wrong once the code was read.
- Anything deferred, and why.
