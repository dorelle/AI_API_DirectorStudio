# Director Studio — Task 21: Workflow and Navigation

Repo: `dorelle/AI_API_DirectorStudio`

Found during the first real production session on RDOA-E01-S01, after Task 20 landed.

Wan 3.0 reference is now confirmed as the conversation model — identity, makeup detail and
dialogue all hold with three references and positional addressing. These items are what still
slows that work down or costs renders.

---

## 1. Attach references from Loved, not the whole library

The shot's Assets and Elements pickers open on the full library. For shot work the user is
attaching from a small approved set, not browsing hundreds of items.

- **Default the shot's picker to the Loved tab**
- Filter Loved by the active project — references are per film
- References and the full library stay reachable

**And there is no way to mark something loved from Shot mode.** A render or an upload that
turns out to be a good reference has to be promoted from elsewhere.

- Add the heart action wherever a shot's attached references and takes are shown

This is the single biggest source of wasted renders: attaching the wrong reference because
the right one was hard to find.

## 2. Audio defaults on, and invents voices

Observed: a dialogue shot rendered with audio on, and the model gave C.A.L. a female voice —
because the only voice reference available was the on-screen performer's.

Audio is never wanted on this project. Voice comes from ElevenLabs and is synced afterward.

- New shots already default audio off, per Task 20. **Existing shot rows still carry
  `audio: true`** from before that change
- Offer a way to turn it off across a project's shots, or at minimum say clearly on the shot
  that audio is on
- **Warn when audio is on and the shot has dialogue.** That combination always produces a
  wrong voice

## 3. Elements has no home

Elements is only reachable through the Generator, and only as a picker modal.

With 34 characters plus locations and props, it is a library that needs browsing, editing and
organising on its own.

- Add Elements as a top-level tab, beside Assets and Workbench
- It shows the full library by type, with create, edit and delete
- The picker modal stays as it is for attaching

## 4. The Filename field — report what it does

The scope bar has a Filename field. It is left over from the original app, where it formed
part of a saved asset's name.

Shots now assemble their own filenames from the ID convention. So its role is unclear.

**Report first:**

- What does Filename affect on a Generator render?
- What does it affect on a shot render — anything?
- Is it used anywhere else?

**Then:** if it does nothing for shot renders, hide it in Shot mode. If it still applies, make
clear which renders it affects. Do not change the ID convention or the shot filename format.

## 5. The Video tab shows an empty state while selected

With Video selected, the settings panel reads "No video model selected — switch the workspace
to Video and pick a model."

It is telling the user to do what they have already done. Either the panel is not reading the
selection, or the model selection is lost when switching away and back.

Fix, and check the same for Image.

## 6. Image, Video and Shot are inconsistent

The three workspace tabs are one control but behave differently. Shot gained its own settings
panel in Task 18 and has diverged from the other two rather than matching them.

**Report before changing anything:**

- What does each tab own today — settings panel, reference tray, prompt area, viewer?
- Where do they differ, and is each difference deliberate?

**Then propose** a consistent shape rather than implementing one straight away. This is a
layout decision and it should be agreed before it is built.

---

## Constraints

- Do not change the `_approved` rule, the ID convention, or slug behavior.
- Do not change which prompt is sent to the render.
- Do not change model routing or the catalog.
- Items 4 and 6 are **report first, then act.** Do not restructure on assumption.
- Test harnesses must not copy `Image_assets`, `studio.db`, or any generated media.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. The shot's reference picker opens on Loved, filtered by project.
2. A reference or take can be marked loved from Shot mode.
3. Audio on a shot with dialogue produces a visible warning.
4. Existing shots' audio setting can be turned off without editing each one.
5. Elements is a top-level tab with full browse, create and edit.
6. The Filename field's behavior is reported, and it is hidden where it does nothing.
7. The Video tab shows its settings when Video is selected.
8. A report exists on what each workspace tab owns, with a proposal for consistency.
9. Everything from Tasks 02 through 20 still works.

## Report back

- Files changed, one line each.
- What Filename actually affects, per render path.
- What each workspace tab owns, and where they differ.
- The proposed consistent shape for item 6 — proposal only, not built.
- Anything deferred, and why.
