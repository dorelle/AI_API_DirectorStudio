# Director Studio — Task 18: First Production Run Fixes

Repo: `dorelle/AI_API_DirectorStudio`

The first real scene was built and rendered on 2026-09-19. It worked, but the shot row did
not drive the render — the work had to be rebuilt by hand in the Generator.

These are the findings from that run, in the order they cost time or money.

---

## 1. Takes are at the bottom of the editor

**This cost real money.** After rendering, the take was far enough down the editor that it
was not visible, so more renders were fired because the first one appeared not to have
landed.

**Move the Takes section to the top of the shot editor**, above Content.

It is the only section whose contents change while the user waits. Everything else is input;
this is output.

The step numbering from Task 09 can stay as a working order in the guide strip, but Takes
does not belong last on screen.

While rendering, the Takes section should show that a render is in flight, without the user
scrolling to find out.

## 2. Shot mode has no video settings

The left settings panel in Shot mode reads "No video model selected — switch the workspace
to Video and pick a model."

So in Shot mode there is no way to set duration, resolution, aspect, audio on or off, or the
reference tray. The shot's engine field falls back to the Generator's current selection,
which is how a Fal shot ended up attempting Kling and failing on credentials.

**Shot mode needs its own video settings.**

- When the Shot pill is active, the left panel shows the video model settings for the shot's
  engine, built from that model's schema the same way the Video workspace does
- Duration, resolution, aspect ratio and audio are set on the shot and saved to the row
- Changing the shot's engine rebuilds the panel for that model
- The Generator's own Video workspace is unchanged

**Add whichever columns the shot row needs** to hold these. They already partly exist —
check before adding.

If a full schema-driven panel is too large for this task, say so and implement duration,
resolution and audio as plain fields, and report what was deferred.

## 3. The scene is not reaching the compiled prompt

A scene brief was written. The compiled prompt did not carry its cues — the render came back
generic.

**Diagnose before fixing.** Two possibilities and they have different fixes:

- The compiler's assembled context does not include the scene's brief and script
- It does include them, and the model is dropping them

Use the existing compile-context endpoint to dump the assembled context for a shot whose
scene has a brief, and **paste it in the report.**

If the scene is missing from the context, add it. If it is present, the fix is in
`COMPILER_RULES` or the model, and that is a separate decision — report it rather than
guessing.

## 4. References cannot be uploaded

The Assets section says "Add plates, garments, props or style images" but the only action is
picking from the existing library. There is no way to bring in a new image from disk at that
moment.

**The picker as it stands:** two tabs, **Loved** and **References**, both listing assets
generated inside the app. There is no upload control anywhere in it, and the footer reads "You
can select up to 16 images."

**Add upload in two places:**

- **On the References tab of the picker**, as an upload button beside the tabs and a drop
  target on the grid itself. References is the correct tab — Loved is for generated work the
  user marked, References is for material brought in
- As a drop target on the Assets section of the shot editor

Both write to the same place. Do not build two upload paths. Do not change the Loved tab.

**Where uploads land.** An uploaded image goes into the reference library — the same store the
References tab already reads — so it is available to every later shot, not only the one it was
uploaded for. It must appear in the picker immediately without a reload.

This matters now: character sheets, portraits and editorial stills are being made outside the
app and need a way in. Item 5 depends on this working.

Accept the image types the app already handles. Keep the original filename where possible,
since the slug convention makes filenames meaningful.

## 5. Character sheets must be selectable as assets

A talent element contributes its casting portrait. Character sheets are made outside the app
and there is no way to attach one to a shot.

**The fix is selection, not structure.** Do not build multi-image elements, and do not build
the per-scene cast record — that is Task 13 and it is not needed for this.

What is needed:

- The reference picker can reach character sheets wherever they live on disk, alongside the
  existing Loved and References tabs
- Both a portrait and a sheet can be attached to the same shot, as separate references, each
  with its own role from Task 08
- The user chooses which to attach per shot. A close gets the portrait, a wide gets the sheet.
  **No automatic selection by shot size**

If character sheets are not currently in a location the picker can see, say where they are and
what the smallest change is to reach them — a folder the picker indexes, or upload from item 4
covering it.

Report which location was used and how a sheet appears in the payload.

## 6. Shot card says "No scene yet"

The strip card reads "No scene yet" on a shot whose ID is `RDOA-E01-S01-01-POP-martina-egg`
and which is listed under its scene in the right panel.

Display bug. The card is not reading `scene_id`, or is reading a stale value. Fix and check
every place a shot's scene is displayed.

## 7. Element analysis failure

Creating an element showed: "Analysis failed: Could not extract JSON from response — fill in
fields manually."

The Gemini vision response could not be parsed. Investigate whether it is a response-shape
change, a prompt problem, or a transient failure.

**At minimum, fail better.** The error should say what came back, and the manual fallback
should be obvious rather than reading as a crash. The debug log already exists — point the
user at it.

---

## Constraints

- Additive where possible. No renamed columns, no restructured tables.
- Do not change the `_approved` filename rule, the ID convention, or slug behavior.
- Do not change the Generator's own Image or Video workspaces.
- Do not touch the provider layer or the job layer.
- Test harnesses must not copy `Image_assets`, `studio.db`, or any generated media.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. Takes is the top section of the shot editor and shows a render in flight.
2. Shot mode has video settings, and they save to the shot.
3. A shot's engine drives the render without falling back to the Generator's selection.
4. The compiled prompt carries the scene's brief, or the report explains why it does not.
5. A reference image can be uploaded from disk, lands in the reference library, and is
   attachable in one pass without a reload.
6. A character sheet can be selected and attached to a shot alongside a portrait.
7. The shot card shows its scene.
8. Element analysis either works or fails with a readable message and a clear manual path.
9. Everything from Tasks 02 through 17 still works.

## Report back

- Files changed, one line each.
- The assembled compile context for a shot whose scene has a brief, pasted.
- What was deferred from item 2, if anything.
- Where character sheets are read from, and how one appears in the payload.
- What the element analysis failure turned out to be.
