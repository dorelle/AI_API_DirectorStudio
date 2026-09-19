# Director Studio — Task 14: Playbook and Agent

Repo: `dorelle/AI_API_DirectorStudio`
Read the Task 12 and Task 13 commits before starting.

Two parts. Part 1 is the project playbook, which is small and which Part 2 depends on.
Part 2 is a conversational agent in Workbench that reads the whole project.

---

# Part 1 — The project playbook

## What it is

The playbook is the film's constant: premise, format, cast, setting, world canon, tone.
Written outside the app and brought in. Everything downstream inherits from it.

Without it, the agent in Part 2 has no world to reason about.

## The build

New column on `projects`:

| Column | Notes |
| --- | --- |
| `playbook` | Long text. Markdown, stored as written |

In the project edit UI from Task 03:

- A large text area for the playbook
- An upload action accepting a `.md` or `.txt` file, which fills the text area
- Paste works as well as upload
- No parsing, no section extraction, no validation. **Store it as written.** Markdown
  structure is for the reader and for the model, not for the app

Show it on film projects. Campaign projects do not need it.

---

# Part 2 — The Workbench agent

## What it is and is not

A conversation in Workbench that knows the project and helps with it.

**It reads everything. It proposes anything. It writes nothing.**

Every change it suggests is text for the user to act on. No shot rows are created, no
fields are set, no records are written by the agent in this task. That comes later, once
it has been used enough to trust.

Do not build tool use, function calling, or any write path.

## 2.1 Layout

Workbench becomes three columns:

| Column | Holds |
| --- | --- |
| Left | Structured Brief, as today |
| Center | Generated Plan, as today |
| Right | The agent conversation |

The existing brief and plan behavior is unchanged. The agent is additional.

The right column: message history, an input, a send action. It must be usable at a laptop
height, and the history scrolls.

## 2.2 The model and key

Use OpenAI. Add an `OpenAI API Key` field to the Settings page, following exactly how the
existing keys are stored and verified.

If no key is set, the agent column says so and does nothing. Do not fall back to another
provider.

## 2.3 Agent instructions

A large text area in Settings, `Agent instructions`, whose content becomes the system
prompt.

This is where the user pastes their own rules — fashion film ruleset, drama conventions,
terminology, prompt structure. **Do not write default rules.** Ship it empty with
placeholder text explaining what goes there.

The system prompt sent is: these instructions, then the project context from 2.4, then a
short framing that the agent proposes and does not act.

## 2.4 Project context

The agent sees the **whole project**. Assemble a digest each turn:

| Include | At what depth |
| --- | --- |
| Project | Name, type, settings, **the full playbook** |
| Scenes | Slug, name, order, brief, environment |
| Scene scripts | Full text for the scene under discussion; omitted or truncated for others |
| Cast | Per scene: character name, look name, handle, which element |
| Shots | Slug, scene, order, action, dialogue, camera fields, status, take counts |
| Elements | Name and type. Not full attribute sets |
| Styles | Name and text |

Shot prompts and reference paths can be omitted unless asked for.

**Report the assembled context size in tokens for a real project**, and say what would
happen on a project ten times larger.

If the digest is too large for the model's context, truncate the least important parts
first — other scenes' scripts, then element detail — and **tell the user in the
conversation** what was left out. Never truncate silently.

## 2.5 Conversation state

The API has no memory. Send the full exchange each turn, plus a fresh digest, since the
project changes between turns.

- History persists per project, so closing Workbench does not lose it
- A clear-conversation action
- Show the user when the history is getting long enough to cost real money

## 2.6 What it is for

No special modes or buttons in this task. It is a conversation. But it should be able to:

- Answer questions about the film from the playbook and the records
- Notice contradictions — a shot that conflicts with what a scene established
- Suggest shots for a scene, as text the user can act on
- Draft a prompt for a shot, as text
- Be asked about its own reasoning

Whether these work well is a function of the user's instructions in 2.3, not of code.

---

## Constraints

- **No write path.** The agent cannot create, update or delete any record.
- Do not change the existing Structured Brief or Generated Plan behavior.
- Do not touch the provider layer, the job layer, or any rendering path.
- Do not add default agent instructions or a default system prompt beyond the framing in
  2.3.
- Follow the existing Settings pattern for the key and the instructions field.
- No new dependencies beyond what an OpenAI call requires. If the existing HTTP approach
  covers it, use that.
- No refactoring of adjacent code. Match existing style.
- Anything here contradicted by the code: stop and report.

## Done means

1. A film project can hold a playbook, by paste or by upload.
2. The playbook round-trips unchanged, including markdown.
3. Workbench shows three columns, with brief and plan unchanged.
4. With no OpenAI key, the agent column says so and does nothing.
5. With a key, a message gets a reply.
6. The reply reflects the playbook and the project's actual scenes, cast and shots.
7. Agent instructions from Settings reach the system prompt.
8. Conversation history persists per project and can be cleared.
9. Truncation, when it happens, is stated in the conversation.
10. **No record is written by the agent under any circumstances.**
11. Everything from Tasks 02 through 13 still works.

## Report back

- Files changed, one line each.
- The full system prompt framing used, pasted.
- The assembled digest for a real project, pasted or summarized with its token count.
- What gets truncated first, and at what size.
- Anything in this spec that was wrong once the code was read.
- Anything deferred, and why.
