---
name: hud-visual-walkthrough
description: >
  Launch groket-hud, open a specific session, click through Overview /
  Turns / Events / Findings / Notes, capture timed screenshots, measure
  UI and control timings, then visually review each grab for polish,
  layout, and broken state using multimodal image inspection. Use when
  the user runs /hud-visual-walkthrough, asks for HUD screenshot
  review, pixel perfection, visual QA, or "is the HUD ugly / broken".
metadata:
  short-description: "HUD timed walkthrough + visual QA"
---

# HUD visual walkthrough

End-to-end **visual** review of the iced desktop HUD, plus control-plane
RPC samples. Product eyes on real pixels — not a unit suite.

## Prerequisites

- Host X11 `DISPLAY` (Xephyr nests on it).
- **`Xephyr`**, **`metacity`**, **`wmctrl`**, **`import`** (ImageMagick).
- In-tree `groket-hud` binary: prefers **`target/release/groket-hud`**, then
  debug, then `groket hud` (document release for snappier walks).
- Control: `groket serve -d` (or the walkthrough starts it).
- Auto keys: **`xdotool`** (or `--manual-keys`).

## Isolation (default)

Matches icedtea `gallery-gif.sh` ideas:

1. **Xephyr** nested display  
2. **metacity** WM inside it  
3. **`GROKET_HUD_WINDOW=1`** — normal managed window (not overlay)  
4. **wmctrl place** + **`import -window root -crop`** of the client  

Does **not** `--restart` the host HUD. Overlay mode stays off unless
`--overlay`.

| Backend | Flag | Notes |
|---------|------|--------|
| Xephyr | `--backend xephyr` (default) | Isolated; window mode paints |
| Host | `--backend host` | Interferes; still works |
| Xvfb | `--backend xvfb` | Often black |

## Inputs

| Arg / env | Meaning |
|-----------|---------|
| Session | Substring or full `sessionId` (required) |
| Out dir | Default `tmp/hud-walk/<timestamp>/` under the repo |
| `--manual-keys` | Do not inject keys; wait for Enter between steps |
| `--backend` | `xephyr` (default), `host`, or `xvfb` |
| `--overlay` | Force overlay mode (skip `GROKET_HUD_WINDOW`) |
| `--display-num N` | Nested display number |
| `--settle-ms N` | Sleep after action before settled screenshot (default 450) |
| `GROKET_CONTROL_SOCKET` | Override control socket |

## Agent procedure

### 1. Run the harness

From the **groket** repo root:

```bash
# Prefer a release HUD for less UI thrash under nested X:
#   (cd groket-hud && cargo build --release)

python3 .grok/skills/hud-visual-walkthrough/scripts/hud_walkthrough.py \
  --session '<session-id-or-title-substring>' \
  --out tmp/hud-walk/latest
```

Read stdout. Note `out_dir`, `timings.json`, `display=`, `backend=`,
`window_mode=true`, key injection live vs manual, step errors.

If the script fails before any screenshots, fix environment (serve,
binary, X) and re-run. Do not invent screenshots.

### 2. Control timings

`timings.json` includes control-plane RPC samples (`session/list`,
`session/overview`, `session/timeline`). Report as printed — do not invent.

Optional `response_ms` on a step is **first pixel delta** from action delivery
(external observation). It is **not** product instrumentation. Settled
`ms` includes settle sleep and is wall-clock only.

### 2b. Interactive latency bar (HUD product)

**Every operator action on the hot path must feel under 100ms** when
measured externally (first meaningful pixel change after key/click
delivery — `response_ms` when the harness reports it, not settled `ms`
which includes settle sleep).

| In bar (must stay snappy) | Out of bar (RPC may be slower) |
|---------------------------|--------------------------------|
| Key nav (↑↓ Enter Esc pane digits) | First `session/overview` after pick |
| Spotlight typeahead re-rank / list step | Timeline page fill / open-event fetch |
| Tab switch chrome | Analysis / notes save when network-bound |
| Open/close event detail shell (local) | Cold catalog drain |

**QA rules**

1. Flag any step whose **`response_ms` ≥ 100** for in-bar actions as
   **broken** (or **ugly** if only borderline and rare). Cite the step
   name and ms in the report.
2. Control RPC samples stay reported honestly; a slow overview RPC is
   not an automatic fail if the shell painted the loading state in
   &lt;100ms — but a frozen input loop or blank freeze is a fail.
3. Prefer release binary for timing walks; note debug builds as
   non-binding for the 100ms bar.
4. When fixing product code after a failed bar, prefer moving work off
   the keystroke (defer RPC, virtualize lists, avoid full-catalog clone
   on each key) — do not raise the bar.

### 3. Visual review (mandatory — every shot)

For **each** `*.png` under `out_dir/shots/` in step order:

1. Open with the **read_file** tool (image path). You **must** inspect
   pixels with multimodal vision — **filename-only scoring fails**.
2. Score against `references/rubric.md` using **categories A–G**, not a
   single remembered regression:
   - **A Geometry** — clip, overlap, occlusion; **structured field columns**
     (Overview label gutter: all values share one vertical start)
   - **B Control honesty** — labels match load/empty behavior
   - **C Gate consistency** — tabs/actions vs “select a session” body
   - **D Identity** — type color, human labels, no a11y-id as caption
   - **E Density** — empty shells, spacing, contrast
   - **F Pane validity** — Overview / Turns / Events / Findings / Notes
   - **G Transitions** — cold start → select → All turns before turn pick
3. On **Overview** shots: label/value stack aligned? path not left-shifted
   vs session? Glance fields (not raw event-count dump)?
4. On Timeline shots especially, answer explicitly:
   - Is **Search all events** fully visible and not under picks/count?
   - Does **All turns** show events, loading, or a *honest* empty?
   - Is count/range real text (not the word `count` / a widget id)?
   - Do closed cards show human type labels with brand color?
5. Human-usefulness bar: correct pane; primary controls usable without
   guesswork; no empty wrong pane when data should show.
6. Write one short note per shot: **ok / ugly / broken** + **category
   letter(s)** + one sentence why.

Do **not** use `image_gen` to “fix” the UI. Prefer plain description + path.

Do **not** stop at “search is no longer clipped” if Category B/C still fail.

### 4. Report

Write `out_dir/VISUAL_REPORT.md` (or `REPORT.md`) with:

1. **Environment** — branch, serve, walk `DISPLAY`, backend, release vs debug.
2. **Session** — id and title.
3. **Timings** — control RPCs from `timings.json`; **interactive
   `response_ms`** vs the **100ms** bar (§2b) for in-bar steps.
4. **Shot review** — ordered table: step · file · verdict · categories · notes.
5. **Category rollup** — which of A–G failed (with one example shot each).
6. **Latency rollup** — any in-bar step with `response_ms` ≥ 100 (step + ms).
7. **Verdict** — `SHIPPABLE` / `POLISH` / `BROKEN` (latency bar failures
   count as **BROKEN** when in-bar).
8. **Top fixes** — concrete product/UI asks (grouped by category when useful).

Paste a short summary into chat. Keep the full report on disk.

### 5. Do not

- Commit screenshots, casts, or `tmp/hud-walk/**` unless the user asks.
- Push a demo binary or force-push.
- Touch the icedtea checkout unless the user explicitly says so.
- Claim pixel perfection without reading every shot.
- Depend on product env logs or harness-only symbols in the HUD binary.
- Reduce the rubric to a checklist of last-session bugs only.

## Step map

| Step | Action | Shot name | Category stress |
|------|--------|-----------|-----------------|
| 00 | Ensure serve + start HUD | `00-boot` | — |
| 01 | Show window (window mode) | `01-summon` | **C** cold: tabs vs select body |
| 02 | Search session substring | `02-search` | A dual search later |
| 03 | Select session (Enter) → Overview | `03-overview` | **A.6** field columns, F Overview |
| 04 | Ctrl+2 Turns | `04-turns` | F Turns |
| 05 | Expand first turn (click) | `05-turn-open` | E open density |
| 06 | Ctrl+3 Events (**All turns**, before `]`) | `06-events` | **A B D F** filter + All turns honesty |
| 06b | `]` first Events turn pick | `06b-events-turn-pick` | D type color/labels, G |
| 06c | `]` next turn (if ≥2 turns) | `06c-next-turn` | G list updates |
| 07 | Ctrl+4 Findings | `07-findings` | F |
| 08 | Ctrl+5 Notes | `08-notes` | F |
| 09 | Ctrl+1 Overview | `09-overview-return` | G |

Step **06** is the primary Timeline honesty gate: do not treat it as a
throwaway empty state. If the product claims All turns / search-all, the
frame must support that claim.

## Quality

- Script is plain Python 3.12+; keep **ruff** clean (`ruff check` + `ruff format`).
- Prefer release binary; document when debug is used.
- Measurement is external (pixels + control RPC) — no product file I/O hooks.
- Rubric is category-driven; extend categories when a new *class* of bug
  appears, not only a one-line regression for the last incident.

## Related

- Rubric: `references/rubric.md`
- Harness: `scripts/hud_walkthrough.py`
- Product keys: `groket-hud/README.md`
