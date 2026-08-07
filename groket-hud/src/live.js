/**
 * Live-refresh helpers for the HUD (pure — no DOM / Tauri).
 *
 * Polling re-reads control ``session/overview`` + timeline tail so a selected
 * running turn updates without FS watch (the control owner may be headless).
 */

/** Status labels that mean the turn/session is still moving. */
const LIVE_STATUS = new Set([
  "running",
  "ending",
  "in_progress",
  "pending",
  "awaiting",
  "awaiting_follow_up",
]);

/**
 * Normalize a control/list status string and test for live work.
 * @param {unknown} status
 * @returns {boolean}
 */
export function isLiveStatus(status) {
  const x = String(status ?? "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_");
  if (!x || x === "—" || x === "-") return false;
  if (LIVE_STATUS.has(x)) return true;
  // Compact labels sometimes omit underscores (list chips).
  if (x.includes("await") || x === "run" || x.startsWith("runn")) return true;
  return false;
}

/**
 * True when overview turn rows include an unfinished segment.
 * @param {unknown} turnsBlock — ``overview.turns`` payload
 * @returns {boolean}
 */
export function hasOpenTurn(turnsBlock) {
  if (!turnsBlock || typeof turnsBlock !== "object") return false;
  const turns = /** @type {{ turns?: unknown }} */ (turnsBlock).turns;
  if (!Array.isArray(turns)) return false;
  return turns.some((t) => t && typeof t === "object" && Boolean(/** @type {{ open?: unknown }} */ (t).open));
}

/**
 * Whether the selected session should poll at the fast interval.
 * @param {unknown} status
 * @param {unknown} turnsBlock
 * @returns {boolean}
 */
export function sessionNeedsLivePoll(status, turnsBlock) {
  return isLiveStatus(status) || hasOpenTurn(turnsBlock);
}

/** Fast interval while a selected session is live (ms). Match TUI active poll. */
export const LIVE_POLL_MS = 3000;
/** Slow interval for idle selected sessions / list status (ms). */
export const IDLE_POLL_MS = 15000;

/**
 * List offset for a seek window around a timeline event index.
 * Unfiltered ``session/timeline`` offsets match sequential ``event.index``.
 * @param {number} focusIndex
 * @param {number} [pad]
 * @returns {number}
 */
export function timelineSeekOffset(focusIndex, pad = 20) {
  const n = Number(focusIndex);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.max(0, Math.floor(n) - Math.max(0, pad));
}

/** Return the exact row index for a daemon-selected session. */
export function selectedSessionIndex(rows, sessionId) {
  if (!Array.isArray(rows) || !sessionId) return -1;
  const sid = String(sessionId);
  return rows.findIndex((row) => row && String(row.sessionId || "") === sid);
}

/** Resolve a prompt selection to its user event for timeline focus. */
export function turnEventIndexForPrompt(overview, promptIndex) {
  if (promptIndex == null || !overview || typeof overview !== "object") return null;
  const turns = overview.turns && typeof overview.turns === "object" ? overview.turns : {};
  const rows = Array.isArray(turns.turns) ? turns.turns : [];
  const prompt = Number(promptIndex);
  const row = rows.find((item) => item && Number(item.promptIndex) === prompt);
  if (!row) return null;
  const index = row.userEventIndex ?? row.firstIndex;
  if (index == null || index === "") return null;
  const value = Number(index);
  return Number.isFinite(value) ? value : null;
}

/**
 * Scroll offset that centers a child row inside an overflow scroller.
 *
 * Prefer this over ``scrollIntoView`` for nested overflow panes (WebKit/Tauri
 * often leaves the focused row off-screen after a full list rebuild).
 *
 * @param {number} scrollTop — current ``scroller.scrollTop``
 * @param {number} scrollerTop — ``scroller.getBoundingClientRect().top``
 * @param {number} scrollerClientHeight — ``scroller.clientHeight``
 * @param {number} childTop — ``child.getBoundingClientRect().top``
 * @param {number} childHeight — ``child.getBoundingClientRect().height`` (or ``offsetHeight``)
 * @returns {number}
 */
export function centeredScrollTop(
  scrollTop,
  scrollerTop,
  scrollerClientHeight,
  childTop,
  childHeight,
) {
  const st = Number(scrollTop);
  const sh = Number(scrollerClientHeight);
  const y = st + (Number(childTop) - Number(scrollerTop));
  const h = Number(childHeight);
  if (!Number.isFinite(st) || !Number.isFinite(sh) || !Number.isFinite(y)) return 0;
  const mid = Number.isFinite(h) ? h / 2 : 0;
  return Math.max(0, y - sh / 2 + mid);
}

/**
 * Stable fingerprint for a timeline event (skip no-op merges).
 * @param {Record<string, unknown>} ev
 * @returns {string}
 */
export function eventFingerprint(ev) {
  if (!ev || typeof ev !== "object") return "";
  return [
    ev.index,
    ev.turnIndex,
    ev.typeLabel,
    ev.heading,
    ev.kind,
    ev.toolName,
    ev.isError ? 1 : 0,
    ev.contentTruncated ? 1 : 0,
    ev.contentLength,
    ev.preview,
    // content is the streaming body — must participate
    typeof ev.content === "string" ? ev.content.length : 0,
    typeof ev.content === "string" ? ev.content.slice(0, 64) : "",
    typeof ev.content === "string" ? ev.content.slice(-64) : "",
    ev.time,
  ].join("\u0001");
}

/**
 * Merge timeline event batches by ``index`` (append/update, stable order).
 * Only counts *updated* when content fingerprint changes (avoids paint thrash).
 * @param {Array<Record<string, unknown>>} existing
 * @param {Array<Record<string, unknown>>} batch
 * @returns {{ events: Array<Record<string, unknown>>, added: number, updated: number, changedIndices: number[] }}
 */
export function mergeTimelineByIndex(existing, batch) {
  const byIndex = new Map();
  for (const ev of existing) {
    if (!ev || typeof ev !== "object") continue;
    const ix = Number(ev.index);
    if (Number.isFinite(ix)) byIndex.set(ix, ev);
  }
  let added = 0;
  let updated = 0;
  /** @type {number[]} */
  const changedIndices = [];
  for (const ev of batch) {
    if (!ev || typeof ev !== "object") continue;
    const ix = Number(ev.index);
    if (!Number.isFinite(ix)) continue;
    const prev = byIndex.get(ix);
    if (prev) {
      if (eventFingerprint(prev) !== eventFingerprint(ev)) {
        byIndex.set(ix, ev);
        updated += 1;
        changedIndices.push(ix);
      }
    } else {
      byIndex.set(ix, ev);
      added += 1;
      changedIndices.push(ix);
    }
  }
  const events = [...byIndex.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([, ev]) => ev);
  return { events, added, updated, changedIndices };
}

/**
 * True when the detail scroller is near the bottom (auto-follow new events).
 * @param {number} scrollTop
 * @param {number} scrollHeight
 * @param {number} clientHeight
 * @param {number} [threshold]
 * @returns {boolean}
 */
export function shouldAutoFollowTimeline(
  scrollTop,
  scrollHeight,
  clientHeight,
  threshold = 120,
) {
  const room = Number(scrollHeight) - Number(scrollTop) - Number(clientHeight);
  if (!Number.isFinite(room)) return true;
  return room <= threshold;
}

/**
 * Fingerprint of overview fields that justify a detail re-paint.
 * Omits rapidly ticking duration alone (still covered when events/status move).
 * @param {Record<string, unknown>|null|undefined} overview
 * @returns {string}
 */
export function overviewPaintFingerprint(overview) {
  if (!overview || typeof overview !== "object") return "";
  const meta = overview.meta && typeof overview.meta === "object" ? overview.meta : {};
  const turns = overview.turns && typeof overview.turns === "object" ? overview.turns : {};
  const notes = overview.notes && typeof overview.notes === "object" ? overview.notes : {};
  const findings =
    overview.findings && typeof overview.findings === "object" ? overview.findings : {};
  const turnRows = Array.isArray(turns.turns) ? turns.turns : [];
  const openFlags = turnRows.map((t) => (t && t.open ? "1" : "0")).join("");
  const lastSummary =
    turnRows.length && turnRows[turnRows.length - 1]
      ? String(turnRows[turnRows.length - 1].summary || "").slice(0, 120)
      : "";
  return [
    overview.sessionId,
    meta.status,
    meta.title,
    meta.numEvents,
    meta.toolCallCount,
    meta.errorCount,
    meta.contextUsageCompact,
    turns.total,
    openFlags,
    lastSummary,
    notes.count,
    notes.revision,
    findings.total,
    findings.count,
    String(overview.summary || "").slice(0, 80),
  ].join("\u0001");
}

/**
 * Patch list-row fields that appear in the session list chrome.
 * @param {Array<Record<string, unknown>>} rows
 * @param {string} sessionId
 * @param {Record<string, unknown>|null|undefined} meta
 * @returns {{ changed: boolean, listPaint: boolean }}
 */
export function patchListRowFromMeta(rows, sessionId, meta) {
  if (!Array.isArray(rows) || !sessionId || !meta || typeof meta !== "object") {
    return { changed: false, listPaint: false };
  }
  const sid = String(sessionId);
  const row = rows.find((r) => r && String(r.sessionId || "") === sid);
  if (!row) return { changed: false, listPaint: false };
  let changed = false;
  let listPaint = false;
  // Fields painted in the list row (status pill + title + sub).
  for (const [src, dest, paintsList] of [
    ["status", "status", true],
    ["title", "title", true],
    ["label", "label", true],
    ["model", "model", true],
    ["outcome", "outcome", false],
    ["numEvents", "numEvents", false],
    ["contextUsageCompact", "contextUsageCompact", false],
  ]) {
    if (meta[src] == null || meta[src] === "") continue;
    const next = meta[src];
    if (row[dest] !== next) {
      row[dest] = next;
      changed = true;
      if (paintsList) listPaint = true;
    }
  }
  return { changed, listPaint };
}
