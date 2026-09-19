# Director Studio — Task 02b: Rename, Accent, Popup Layout

Repo: `dorelle/AI_API_DirectorStudio`
Read `DIRECTOR_STUDIO_ORIENTATION.md` before starting.

Three items. None of them touch the project record work from Task 02 (`c42ed7d`).

Items 1 and 2 were asked for during Task 02 and did not land. They are repeated here.

---

## 1. Rename

The app still reads **AI API Studio**. It becomes **Directors Studio**.

Change it everywhere it appears:

- Header wordmark
- Browser tab title
- Page titles
- Any occurrence in templates or in Python strings

Do not rename files, directories, the repo, or the database. Display strings only.

## 2. Accent color

The primary accent becomes `#AA34F8`, replacing the current red.

**Find every hardcoded occurrence of the red first.** If it is written literally across
`style.css` and the templates rather than defined once, pull it into a CSS custom property
on `:root`, then change the value in one place.

Report how many places it was hardcoded.

Check contrast after the swap. The purple is lighter than the red it replaces, so small
text, disabled states and anything sitting on the accent may no longer be readable. Fix
what fails, and say what you changed.

Leave `docs/images` alone. Those screenshots are stale regardless and get replaced when
the README does.

## 3. Asset detail popup

The full-view popup currently stacks the image above its metadata. With a long prompt, the
metadata panel is cut off and there is no scroll, so fields below the fold are unreachable.

### Layout

Two columns. **Image left, metadata right.**

- Image in the left column, scaled to fit the available height rather than the full width
- Metadata in the right column, full height
- The action row — heart, download, reuse, info, compare, delete — stays with the image

### Content rules

- **Everything visible with no scrolling in the default state.** Fields first: Model,
  Provider, Client, Project, Shot, and the rest. Two columns inside the metadata panel if
  that is what it takes to fit.
- **Prompt last, capped at two or three lines, with an expand control.** The prompt is the
  only element allowed to grow. Expanding it is the one case where the panel may scroll.
- Long prompts must not push any field below the fold when collapsed.

### Cases to test

- **Landscape image.** The common case, and the one that motivates the side-by-side layout.
- **Portrait image.** 3:4 and 9:16 are frequent here. A tall image must not squeeze the
  metadata column or overrun the panel height. Give the metadata column a sensible minimum
  width and let the image take what is left.
- **Square image.**
- **Long prompt.** Five sentences or more, collapsed and expanded.
- **Short window.** Above the fold depends on window height. Test at a laptop-height
  window, not only a maximized one.

### Narrow windows

Below a breakpoint, fall back to the stacked layout. Pick the breakpoint and state it in
the report.

---

## Constraints

- Additive. No refactoring of adjacent code.
- Match existing style.
- No new dependencies.
- Do not touch the project record work, the Generator, the Elements modal, the provider
  layer, or the job layer.
- Anything here contradicted by the code: stop and report.

## Done means

1. The app reads Directors Studio everywhere, including the browser tab.
2. The accent is `#AA34F8` and the red is gone from the UI.
3. Contrast holds on small text and disabled states.
4. The popup shows image left, metadata right.
5. Every field is visible with no scrolling, with the prompt collapsed.
6. The prompt expands and collapses.
7. Portrait, landscape and square images all lay out correctly.
8. Narrow windows fall back to stacked.

## Report back

- Files changed, one line each.
- How many places the accent was hardcoded.
- What failed contrast and what was changed.
- The breakpoint chosen for the stacked fallback.
- Anything deferred, and why.
