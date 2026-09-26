# HUD visual rubric

Score each screenshot from **pixels**, not from memory of prior bugs.
Prefer evidence over taste essays. When a defect fits a **category**
below, name the category in the shot note (not only the surface symptom).

## Verdicts per shot

| Tag | Meaning |
|-----|---------|
| **ok** | Usable, hierarchy clear, no category failure |
| **ugly** | Usable but density/contrast/polish issues |
| **broken** | Wrong state, dishonest chrome, clipped/overlapped controls, unreadable, or dead-end UX |

Any **broken** in the walk → overall report **BROKEN** unless the step is an intentional error demo.

---

## Category A — Geometry (clip, overlap, occlusion)

Ask on **every** shot that has chrome or a filter:

1. **Clip** — is any label, chip, badge, pick list, button, or **search field** cut off by the window, parent, or sibling? A field reduced to a sliver or a single word remnant is broken.
2. **Overlap** — do two controls paint on top of each other (pick list over search, session search over filter search, count meta over Type, cards under footer)? Stacked full-width search rows still need clear separation and no shared hit box.
3. **Occlusion** — is a primary control covered so it cannot be used without guessing? Dropdown open state covering the only search field counts.
4. **Edges** — content inset from card edge (~12–16px). No text flush to border.
5. **Alignment** — rail vs detail; filter baselines; multi-row filters still leave every control fully visible.
6. **Structured fields** — Overview (and any other label/value stack) must share **one label gutter**: every value column starts on the same vertical line. Short keys (`path`) and long keys (`session` / `last turn`) must not shift values. Mixed widgets (fixed-width label row + intrinsic-width `value_field`) without a shared gutter = **broken**.

Do not mark “ok” because *most* of the filter row fits if **any** primary control is clipped or overlapped.

---

## Category B — Control honesty (label matches behavior)

Chrome text must not lie about what the product will do.

| Pattern | Broken when |
|---------|-------------|
| Dropdown / chip label | Says **All turns** (or similar) but body is empty *and* no fetch is underway *and* search cannot produce a list without a hidden extra step |
| Empty-state copy | Contradicts the controls (“search all events” while search is dead, or “all turns” while only turn-scoped load works) |
| Count / range meta | Shows a11y ids, placeholders, or junk (`count`, `label`, widget ids) instead of a real range, blank, or honest zero |
| Loading | Chrome implies data exists while body is a permanent empty with no load path |

**Honest empty** is allowed: clear copy, controls that match the empty reason, a path to get data (pick a turn, type a query, *or* auto-load All turns — whatever the product claims).

**Dishonest empty** is broken: label promises scope that never loads; search is advertised but inert; meta invents text from accessibility names when caption is empty.

---

## Category C — Gate consistency (enabled chrome vs required state)

Controls that require a session (or overview) must match body state.

| State | Expect |
|-------|--------|
| No session selected / no overview | Body: select/empty. **Pane tabs** must not look fully live if switching only shows the same empty with no load — either disable non-Overview panes, or selecting a pane must load/select a session. “Select a session” + fully interactive Turns/Timeline/Findings/Notes = broken. |
| Overview loading | Loading chrome; tabs may wait. |
| Overview ready | All panes usable; Timeline can show filter + list or honest empty. |
| Tab active | Filename/step and selected tab chrome match. |

Ask: *If I click this control, does the UI already claim I can, while the body says I cannot?*

---

## Category D — Identity and labels (scanability)

Timeline / turns / findings must stay scannable like the TUI product language.

1. **Type color** — event families keep brand roles (cream / complete green / running yellow / failed red / cancelled). Monochrome titles that only color an inner line after expand are **ugly** at best; missing type cue on closed cards is a product regression if TUI shows type color in the list.
2. **Human labels** — Grok wire ids (`user_message_chunk`, `tool_call`) appear as spaced human labels in chrome the operator reads (title row), not only buried in the body. Prefer TUI `type_label` style (underscores → spaces).
3. **Hierarchy** — title = who/what (type or turn), face = preview, open body = full content. Do not put coarse kind (`User`) as the only title while the real type is a muted subtitle.
4. **No a11y leakage** — accessibility names (`count`, `Turn`, role strings) never paint as the only visible caption when data is empty.

---

## Category E — Density and empty shells

1. **Card height** — open/closed cards should not be large blank slabs (height estimates vs paint). Sparse chrome events as full empty expanders = **ugly** or **broken** if unusable.
2. **Spacing rhythm** — ~8px; more space between sections than within a card.
3. **Contrast** — muted meta still readable; status color means something.

---

## Category F — Product-state validity (by pane)

1. **Session match** — rail selection and detail title/id are the intended session.
2. **Overview** — status, model, context, summary, tools/errors when present, last turn, path; not a raw event-count spreadsheet. Label/value columns aligned (Category A.6).
3. **Turns** — closed: user prompt + marks; open: stats and assistant when complete.
4. **Events / Timeline** — with a selected session: **All turns** either loads a page of events or shows honest loading/empty that matches controls; turn-scoped list when a turn is picked; filter shows Turn, Type, unclipped search, honest count/range.
5. **Findings / Notes** — empty states use status patterns, not raw errors.
6. **Hard broken** — control socket down, panic, zero-size window, all-black frame.

---

## Category G — Walk-path transitions (cross-shot)

Compare adjacent steps, not only isolated frames:

1. **Cold / pre-select** (`01-summon` or equivalent) — gate consistency (Category C) before Enter.
2. **Enter → Overview** — body fills; not stuck on select.
3. **Overview → Turns** — cards appear without requiring a second selection.
4. **→ Timeline with All turns** (before `]`) — Category B: does “All turns” show events or a *honest* path? Flag if empty + search dead + no load.
5. **`]` turn pick** — list updates; count/range updates; not a bit-identical pane.
6. **Open expand** — body content appears; not an empty open shell.

If the harness skips a cold-start or All-turns-before-`]` frame, still reason about those states from the nearest shots and note the gap.

---

## “Highlight polished” bar

Polished = **calm density**: clear who/what hierarchy, consistent chip/badge language, quiet filters/footer, no competing borders. Flag **ugly** if it feels like a debug dump or terminal chrome pasted into a card.

## Timing interpretation

### Interactive bar (in-bar UI — hard)

**First meaningful pixel after key/click (`response_ms`) must stay under
100ms** for operator hot-path actions: list nav, typeahead re-rank, pane
switch chrome, open/close local detail shell. See skill §2b.

| `response_ms` (in-bar) | Verdict |
|------------------------|---------|
| &lt; 100ms | ok for latency |
| ≥ 100ms | **broken** (cite step + ms); overall walk **BROKEN** if in-bar |

Settled step `ms` includes harness settle sleep — **do not** use it for the
100ms bar. Prefer release binary; note debug as non-binding.

### Control RPC (out of bar — report, separate)

| Class | Guidance |
|-------|----------|
| Control RPC &lt; 100ms | Fine for local disk sessions |
| Control RPC 100ms–1s | Note size; large host sessions expected |
| Control RPC &gt; 1s | Call out; overview/timeline cost |

A slow overview RPC is not an automatic fail if loading chrome painted in
&lt;100ms; a frozen input loop or blank freeze **is** a fail.

## Anti-patterns for the reviewing agent

- Scoring “ok” from tab label alone without reading filter + body.
- Treating “empty Events” as fine without checking control honesty (B).
- Only checking for last week’s bug (e.g. search clipped) without Category A full geometry pass.
- Ignoring dual search fields (session catalog vs Events search) for overlap/confusion.
- Filename-only review without multimodal inspection of the PNG.
