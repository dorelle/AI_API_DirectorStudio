# Director Studio — Task 03: Film Projects in Workbench

Repo: `dorelle/AI_API_DirectorStudio`
Read `DIRECTOR_STUDIO_ORIENTATION.md` and the Task 02 commit (`c42ed7d`) before starting.

Two parts, in order. Part 1 completes the project record. Part 2 makes Workbench work for
film. Part 2 depends on Part 1, because film templates need project settings to read.

---

## Why this task exists

Task 02 landed the project record, and its report flagged the problem this task fixes:

> Film + Channels hidden still yields a channels line — the hidden checkboxes' defaults
> still submit, and even if they didn't, `_normalize_channels` defaults to
> `["instagram_feed"]` server-side. All five templates are campaign-shaped.

So a film project today still produces a plan telling the model to design for Instagram
Feed and Meta Ads at 4:5. Hiding the field in the UI did not stop it.

---

# Part 1 — Complete the project record

## 1.1 Edit an existing project

There is currently no way to change a project once created. A project made with a blank
Client is stuck with `uncategorized` forever.

- Add an edit action reachable from the Record picker.
- It edits `name`, `client`, `type`, and the settings in 1.2.
- The `PATCH /api/projects/<id>` route already exists. Use it.

**Renaming has a consequence.** The record's `name` is what gets written into the asset
scope, so renaming changes where new assets file while old ones stay under the old name.
Do not try to migrate anything. Warn in the edit UI in one line, and note it in the report.

## 1.2 Film settings

The `settings` JSON column exists but nothing writes to it. The New Project modal captures
only Name, Client and Type.

When `type = film`, capture and store: format, runtime, aspect ratio, frame rate, register
or genre, resolve folder. All optional, all nullable. Blank is a valid state.

When `type = campaign`, no extra fields for now.

Show these fields in both the New Project modal and the edit UI, revealed when Film is
selected.

## 1.3 Client suggestions

The Client field in the New Project modal is free text, so the same client typed slightly
differently creates a second folder silently.

Feed it the same suggestion list the scope bar's Client input already uses. Reuse that
mechanism, do not build a second one. No client record, no new table.

---

# Part 2 — Film in Workbench

## 2.1 Kill channels for film, properly

Hiding the field is not enough. Fix it at both ends:

- Client side: when the active project is `type = film`, channels must not be submitted at
  all, not merely hidden.
- Server side: `_normalize_channels` must not default to `["instagram_feed"]` for a film
  run. Find the right place for that branch — it may belong where the plan is assembled
  rather than in the normalizer itself.
- The generated plan text must contain no channel line and no placement aspect ratio for a
  film project.

Verify by running a film plan and reading the output. This is the acceptance test.

## 2.2 Film template set

The five existing templates are campaign-shaped. Add a film set alongside them, following
whatever template definition pattern the orientation report found in use. Do not modify
the campaign templates.

Which templates load is driven by the active project's `type`. Film project shows film
templates only. Campaign project shows the existing five only. No project selected shows
the existing five, as today.

The film templates:

| Template | Takes | Produces |
| --- | --- | --- |
| Story | Premise, register | Structure and beats |
| Scene Breakdown | A scene | The shots in it |
| Casting | A role described | Talent candidates |
| Wardrobe | A character and a scene | Look candidates |
| Location | A place described | Environment candidates |
| Shot Direction | A shot | Camera and movement |

Each needs the same shape the campaign templates have: a name, a short description, and
whatever prompt scaffolding the existing ones carry. Keep the descriptions plain.

## 2.3 Film fields replace channel fields

Where a campaign template shows Channels, a film template shows Scene and Treatment as
free text. Aspect ratio comes from the project settings in 1.2, not from a placement.

If project settings are blank, omit the line from the plan rather than defaulting to
anything.

## 2.4 Plan output stays as it is

The plan still renders in the Generated Plan panel and still routes to a model, exactly as
now. Do not change where output goes. There is no board yet and this task does not add one.

---

## Constraints

- Additive. No renamed columns, no restructured tables, no removed routes.
- Do not touch the Generator, the Elements modal, the provider layer, or the job layer.
- Do not modify the five campaign templates.
- No new dependencies.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. An existing project can be edited, including its client.
2. A film project captures and stores its settings.
3. The Client field in the modal suggests existing clients.
4. A film project's plan contains no channel line and no placement aspect ratio.
5. Film templates appear for film projects, campaign templates for campaign projects.
6. A film plan reads as film direction, not campaign direction.
7. Everything from Task 02 still works: picker, free-text fallback, Workbench scope, asset
   browsing.

## Report back

- Files changed, one line each.
- Anything in this spec that was wrong once the code was read.
- Paste the full generated plan text for one film run, so the output can be read directly.
- Anything deferred, and why.

## Known deferrals from Task 02, not in scope here

- `task_runs` has no `project_id`. Leave it.
- Reset Brief clears the prefilled Workbench boxes until the next selection change. Leave it.
- Upscale does not resolve the project id server-side. Leave it.
