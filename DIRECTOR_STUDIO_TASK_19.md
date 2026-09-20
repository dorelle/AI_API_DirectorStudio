# Director Studio — Task 19: Attachments Are Not Reaching The Render

Repo: `dorelle/AI_API_DirectorStudio`

**This is the most serious bug found so far.** Shots render, the app reports success, and the
inputs the user attached are silently discarded. Money is spent on renders that were never
going to work.

---

## 1. Everything attached is being dropped

### What was observed

Shot `RDOA-E01-S01-02-POP-talking-to-CAL`, engine Kling 3.0 Pro via Fal, with:

- A first frame set
- 2 elements attached
- 4 reference assets attached
- A style attached and toggled on
- Both a scene prompt and a motion prompt written

The Reference Tray panel reported:

> 2 element(s), 4 reference(s), style on · first frame set · **model takes: text**

The render sent **the motion prompt only.** Everything else was discarded.

### Why this is worse than a failure

- The shot editor shows **green checks** on completed sections. A check means ready
- The take completed and was recorded as `done`
- The tray line reads as a status, not a problem. "model takes: text" is the entire warning
- The user paid for a render that could not have used any of the work

**A green check on a shot that is about to drop every attachment is the app lying to the
user.**

### Diagnose first, before changing behavior

Task 07 recorded that Fal runs one input mode per call — image, or reference — and that a
start image is only accepted alongside references on models declaring
`supports_start_image`. It also recorded that dropped inputs are returned as a warning on the
render response.

So this is either:

- Mode resolution choosing `text` when it should choose `image` or `reference`, or
- Correct resolution for this model, surfaced only as a passive status line

**Dump the resolved mode and the assembled payload for this exact shot and paste both in the
report before changing anything.** Kling 3.0 Pro's own panel states it takes a start frame,
an end frame and up to 4 elements, so `text` looks wrong for it.

### The fix

**Refuse to render when the resolved mode would drop any attachment.**

- The Render button is disabled, with the reason stated in plain words: which inputs would be
  dropped and why
- The affected sections lose their green check and are marked as a problem
- The reference tray line stops reading as a status and reads as a blocker

**Offer the way out.** The app knows what is attached and knows each model's declared inputs.
Where an engine exists that accepts everything attached, name it. Do not switch engines
automatically.

**Assets and Elements give no signal at all.** They show a count, not a check, so a section
holding four references that are about to be discarded looks identical to one that is fine.
Every section whose contents can be dropped must be able to show a problem state — Elements,
Assets, Style, and the first frame in Generation. A count is not a status.

**Never send a partial payload without the user choosing to.** If proceeding with a reduced
payload is ever allowed, it takes an explicit confirmation that says exactly what will be
dropped.

## 1a. There is no input mode control in Shot mode

The Video workspace has an input mode dropdown — Text to Video, Image to Video, and so on.
**Shot mode has none.** The mode is inferred from what is attached, and the user cannot see
what was chosen except as the tray's passive status line, and cannot override it.

So when inference picks `text` on a shot with a first frame, two elements, four references and
a style, there is no way to correct it.

**Add an input mode control to Shot mode**, matching the Video workspace's options for the
selected engine.

- Default to whatever the app infers, so nothing changes for a shot that resolves correctly
- The chosen mode is visible, not buried in a status line
- The user can override the inference
- Changing the engine re-evaluates the default, but does not silently discard an explicit
  choice without saying so
- Save it on the shot row

This and the block in section 1 work together: the block says what would be dropped, and this
is how the user fixes it without leaving Shot mode.

## 2. The scene prompt is correctly not sent

Confirming, so it does not get "fixed": only the **motion prompt** goes to the render. The
scene prompt is for carrying to the Generator to make a first frame. That is Task 15's design
and it is working.

No change. Make sure section 1's fix does not start sending it.

## 3. Duration exists in two places

The left panel has a Duration field. The Timing section has Duration (seconds). Both showed a
value for the same shot, and at one point the left panel read "Model default" with the note:
"Shot says 0.5s; the render snaps to the nearest offered length."

Two fields for one value, and it is not clear which the render uses.

**One value, one field.** Decide which surface owns it, make the other read from it or
disappear, and say which was chosen. Report what the render actually used before the fix.

## 4. Per-shot settings are still incomplete

The left panel states:

> Only the four settings above and the engine are per shot. Seed, CFG, negative prompt and
> multi-shot lists stay in the Video workspace for now.

That is the Task 18 deferral. Finish it: seed, CFG and negative prompt become per-shot,
saved on the row, and set from Shot mode.

The negative prompt already has a field in the shot editor — check whether it is reaching the
payload at all, since everything else was not.

Multi-shot lists can stay deferred. Say so.

## 5. Cost is recording as $0.00

Kling 3.0 Pro takes recorded `$0.00`. Task 16 found the same on Fal Seedance Lite, and that a
whole block of Fal video models has no entry in `VIDEO_PRICING`.

Fill in the models actually in use — Kling 3.0 Pro, Wan 3.0, Seedance, Veo 3.1 — from the
published per-second rates.

Where a model has no known price, **record it as unknown rather than zero.** A zero is a wrong
number that will be summed later; unknown is honest.

---

## Constraints

- Do not change the `_approved` rule, the ID convention, or slug behavior.
- Do not change which prompt is sent to the render.
- Do not switch a shot's engine automatically.
- Do not touch the provider layer or the job layer beyond what item 1 requires. If a change
  there is unavoidable, stop and report first.
- Test harnesses must not copy `Image_assets`, `studio.db`, or any generated media.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. The resolved mode and payload for the observed shot are reported before any fix.
2. A shot with a first frame, elements, references and a style sends all of them to a model
   that accepts them.
3. When an engine cannot take everything attached, the render is **blocked**, not silently
   reduced.
3a. Shot mode has a visible input mode control, defaulting to the inferred mode and
   overridable, saved on the row.
4. The block states which inputs would be dropped and why.
5. Affected sections show a problem state. Sections that show only a count — Elements,
   Assets, Style — can show one too.
6. Where a suitable engine exists, it is named. It is not selected automatically.
7. The scene prompt is still not sent to the render.
8. Duration exists in one place and the render uses it.
9. Seed, CFG and negative prompt are per shot and reach the payload.
10. Costs record real figures, or `unknown` — never zero.
11. Everything from Tasks 02 through 18 still works.

## Report back

- Files changed, one line each.
- The resolved mode and full payload for the observed shot, **before** the fix.
- The same shot's payload after.
- Which surface now owns duration, and what the render used before.
- Whether the negative prompt was reaching the payload.
- Which models were priced, and which are recorded as unknown.
