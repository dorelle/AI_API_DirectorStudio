# Director Studio — Task 01

Repo: `dorelle/AI_API_Studio` (fork of `pixteur/AI_API_Studio`)
Local: `E:\Code\comfy_app\AI_API_Studio`
Run: `python nbs.py` → http://localhost:5000

This is the first task in building a Director Studio inside AI API Studio. It has two
parts. **Do not start Part B until Part A is reported back and confirmed.**

---

## Part A — Orientation (read only, change nothing)

Nobody has read this codebase yet. Before any code is written, report on what is
actually there. Do not modify any file during this part.

Read `nbs.py`, `app.py`, the `templates/` directory and the `static/` directory, and
answer the following. Where something does not exist, say so plainly rather than
describing what a reasonable implementation would look like.

### 1. Application structure

- What is in `nbs.py` versus `app.py`? Which is the entrypoint and what does the other do?
- Is this one Flask app or two? Are there blueprints?
- List every route, its method, and one line on what it does.
- Which templates exist and which route renders each?

### 2. Persistence

- Where is the SQLite database file and what creates it?
- Print the full schema: every table, every column, every index.
- Is there any migration mechanism, or is the schema created inline on startup?
- What else persists outside SQLite? Name each JSON file, what writes it, what reads it.

### 3. Scope (Client / Project / Shot / Filename)

This is the most important part of the report.

- Where are these four values stored between requests? Session, database, JSON, cookie?
- How do the top-bar dropdowns get populated? Are the options read from existing folder
  names on disk, from a table, or from somewhere else?
- Where is the asset folder path built from them, and where is the filename prefix built?
- Is there any record representing a project, or is "project" only ever a string used to
  build a path?

### 4. Workbench

- Which route serves it, which template renders it?
- Where do the Task Templates come from — hardcoded in Python, in a JSON file, in a table?
- Print the full definition of one template so the shape is clear.
- What does "Build Prompt Plan" do, end to end? What does it return?
- What does "Run from Workbench" do, and how does it hand off to generation?
- Where do `PLANNED RUNS`, `EXECUTED`, `COMPLETED`, `ACTUAL COST` come from?

### 5. Elements

- What is `talent_vocabulary.json` and what reads it?
- What is in `Elements/Model Managment`?
- Where is a Talent record stored, and what are its exact fields?
- How does the "New Talent" modal save? Which route, which storage?
- How does an Element get attached to a generation? Trace the path from picking one in
  the Elements modal to it reaching the provider call.
- Is there anything resembling an `@handle` today, or is selection purely by click?

### 6. Providers

- Where does provider dispatch live? Is there a shared abstraction or a branch per provider?
- Show the shape of one provider call, start to finish: request built, sent, response
  parsed, asset saved, metadata written.
- How much work would adding a new provider be, given how it is currently written?

### 7. Front end

- Is there a build step, or is this plain JS served from `static/`?
- Which JS files exist and roughly what does each own?
- How does the page talk to the server — form posts, fetch, or both?

### Report format

Write the report to `DIRECTOR_STUDIO_ORIENTATION.md` at the repo root. Use file paths and
line numbers throughout, so claims can be checked. Where the answer is "this does not
exist", write that, do not infer.

At the end, add a section called **Risks** listing anything that would make the change in
Part B harder than it looks.

**Stop here. Wait for confirmation before continuing.**

---

## Part B — Project record (do not begin until Part A is reviewed)

### The problem

There is currently no way to start a project.

Workbench has its own free-text `Client` and `Project` inputs. The top scope bar has its
own `Client`, `Project`, `Shot` dropdowns. They are not connected. Typing a project name
in Workbench leaves the scope bar reading `uncategorized`, so the value typed and the
value used to file assets are two different things.

Project is currently a string used to build a folder path. It needs to become a record.

### What to build

A project record, stored in SQLite, that both Workbench and the scope bar read from and
write to.

Fields:

| Field | Notes |
| --- | --- |
| `id` | Primary key |
| `name` | Display name |
| `client` | |
| `type` | `film` or `campaign` — this is the switch for everything downstream |
| `created_at` | |
| `settings` | JSON blob, type-dependent, see below |

For `type = film`, `settings` holds: format, runtime, aspect ratio, frame rate, register
or genre, Resolve output folder. Leave the cast and environment lists out for now.

For `type = campaign`, `settings` holds whatever the existing Workbench templates need.
Do not invent fields.

### Behavior

- A "New Project" action that creates the record. Name, client and type at minimum.
- The scope bar's Project dropdown lists project records, not folder names scraped from disk.
- Selecting a project in the scope bar sets the active project for the session.
- Workbench reads the active project instead of using its own free-text boxes. If the
  existing Workbench inputs are load-bearing for something else found in Part A, say so
  and stop rather than working around it.
- Asset folder paths and filename prefixes keep their current format, built from the
  record instead of from the raw strings.
- `type = film` hides the Channels field in Workbench. Nothing else changes yet.

### Constraints

- **Do not break existing assets.** Anything already saved under the current folder
  structure must still be browsable in the Assets tab. If that requires a migration or a
  fallback path for projects with no record, build the fallback, not the migration.
- **Do not rename or restructure existing tables.** Add.
- Do not touch the Generator, the Elements modal, or the provider layer in this task.
- No new dependencies. The app bootstraps its own packages on first run and that list
  stays as it is.
- Match the existing code style, however it turns out to be written. Do not refactor
  surrounding code to a preferred pattern.

### Done means

1. A project can be created from the UI.
2. It appears in the scope bar dropdown and can be selected.
3. Workbench shows the selected project rather than an empty text box.
4. A generation saved with a project selected lands in the same folder structure as before.
5. A generation with no project selected still works and still files under `uncategorized`.
6. Existing assets still browse correctly in the Assets tab.

Report what was changed, file by file, and anything in the spec above that turned out to
be wrong once the code was read.
