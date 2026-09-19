# nbs.py — Provenance and Code Analysis

Measured 2026-09-19 by direct comparison of the three `nbs.py` files.

---

## The chain

| Stage | Project | Author | Language |
| --- | --- | --- | --- |
| Idea | Node Banana | shrimbly | TypeScript / Next.js / React Flow — **MIT licensed** |
| Origin of this codebase | Nano Banana Studio v1.0 beta | Sergio Valsecchi | Python / Flask |
| Fork | AI API Studio | pixteur | Python / Flask |
| Current | Directors Studio | Dorelle | Python / Flask |

**Node Banana shares no code with this chain.** It is a TypeScript node-canvas app. Sergio
wrote a Python Flask application taking the idea and the name (`nbs.py` = Nano Banana
Studio), not the code. Its MIT license therefore neither grants rights nor imposes
obligations here.

The only lineage that matters legally is **Sergio → pixteur → Dorelle**.

---

## Size at each stage

| | Sergio | pixteur | Dorelle |
| --- | --- | --- | --- |
| Lines | 1,629 | 10,725 | 14,081 |
| Top-level functions | 38 | 281 | 434 |
| Flask routes | 24 | 70 | 113 |
| Growth over previous | — | +9,096 | +3,356 |

---

## What Sergio actually wrote

24 routes covering: login and session, settings, API key verification, a Gemini image
generation call, generations list and delete, a loved gallery, the Elements catalog with
favorites and filtering, a talent image analyzer using Gemini vision, save talent, publish,
and stats.

**Not present in his version:** any video generation, Fal, Kling, Luma, BytePlus, upscale,
edit sessions, cost estimates, the async job layer, SQLite, Workbench, Reports, or
client/project/shot scope.

---

## Sergio → pixteur

All 38 of Sergio's functions were retained. Several are unchanged:

| Function | Sergio | pixteur | Note |
| --- | --- | --- | --- |
| `api_elements_catalog` | 158 | 158 | Identical length |
| `api_save_talent` | 93 | 93 | Identical length |
| `api_analyze_talent_image` | 173 | 170 | Near-identical |
| `api_publish` | 54 | 55 | Near-identical |
| `api_generate` | 222 | 19 | Gutted — replaced by multi-provider dispatch |
| `loved_gallery` | 44 | 6 | Thinned to a wrapper |
| `api_loved_list` | 49 | 19 | Thinned |
| `api_generations` | 63 | 26 | Thinned |

Sergio's functions account for **1,294 lines, 12.1%** of pixteur's file, with a large share
of that verbatim.

pixteur's own contribution is roughly **9,100 lines**: the entire multi-provider layer,
video, upscale, the job layer, SQLite, Workbench, Reports, and scope.

---

## pixteur → Dorelle

153 new functions, 43 new routes, 3,356 lines.

Added: project records with type, film templates, scenes, shots, takes with approval
separate from status, scene cast, styles, reference roles, the shot strip, element intake
for environment and prop, the readiness system, field locking, and the Workbench agent.

Sergio's functions thinned further in places — `api_analyze_talent_image` from 170 to 99.

---

## Sergio's surviving contribution to the current file

Functions bearing his names total **1,163 lines, 8.3%** of the file. That figure is an
upper bound, since most have been rewritten inside.

Recognizably still his: `load_config`, `save_config`, `login`, `login_required`,
`name_to_slug`, the talent JSON helpers, `api_elements_catalog` (162 vs his 158),
`api_migrate_catalog`, `api_publish`.

**Realistic estimate: 300 to 500 lines, or 2 to 4 percent.**

His architecture remains the skeleton — Flask structure, the config and login pattern, the
Elements catalog concept, the talent vocabulary and analyzer, the file-naming scheme —
even where the code behind it has been replaced.

---

## Licensing position

**Sergio's README states:** "Open source — free to use and modify."

That is a permission, not a license. It grants use and modification. It does **not**
explicitly grant redistribution or sale, which MIT does spell out. There is no LICENSE file
backing it.

**pixteur's repository has no LICENSE file and no attribution to Sergio.** Under default
copyright, a public repository without a license reserves all rights to its author. The
permission Sergio granted does not appear anywhere in the repository that was forked.

**Consequence:** redistribution or sale of the current codebase is not clearly permitted.
Personal use and modification are within what Sergio stated.

**Cleanest resolution:** contact Sergio Valsecchi directly and ask him to add an explicit
license, or to confirm in writing that commercial redistribution is permitted. He is the
actual author, he intends the project to be open, and he is reachable on LinkedIn and
Patreon.

Worth noting in any such contact: pixteur added roughly 9,100 lines to Sergio's 1,600 and
published without credit or license. In proportion, pixteur's contribution to the chain is
larger than Dorelle's — and pixteur is the one who removed the attribution.

---

## Incidental findings

- The current `nbs.py` begins with a byte-order mark and the header comment contains
  mojibake from repeated UTF-8 re-encoding. This is the same class of issue that caused a
  double-BOM crash during the build and that produced garbled filter labels in the Elements
  modal.
- Sergio's default credentials (`admin` / `banana2024`) are published in the README of
  every repository in the chain.

---

## Note

This is a code measurement and a description of the licensing situation as documented in
the repositories. It is not legal advice.
