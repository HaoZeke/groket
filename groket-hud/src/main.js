import { fuzzyFilter } from "./fuzzy.js";
import {
  escapeHtml,
  renderBodyHtml,
  renderRawInputHtml,
} from "./content-render.js";
import {
  IDLE_POLL_MS,
  LIVE_POLL_MS,
  centeredScrollTop,
  isLiveStatus,
  mergeTimelineByIndex,
  overviewPaintFingerprint,
  patchListRowFromMeta,
  selectedSessionIndex,
  sessionNeedsLivePoll,
  shouldAutoFollowTimeline,
  timelineSeekOffset,
  turnEventIndexForPrompt,
} from "./live.js";

const { invoke } = window.__TAURI__.core;
const { getCurrentWindow } = window.__TAURI__.window;
const { listen } = window.__TAURI__.event;

const q = document.getElementById("q");
const resultsEl = document.getElementById("results");
const detailEl = document.getElementById("detail");
const statusEl = document.getElementById("status");
const hotkeyHint = document.getElementById("hotkey-hint");
const tabsEl = document.getElementById("tabs");
const tlSearchBar = document.getElementById("tl-search-bar");
const tlQ = document.getElementById("tl-q");
const tlSearchMeta = document.getElementById("tl-search-meta");

/** @type {Array<Record<string, unknown>>} */
let allSessions = [];
/** @type {Array<Record<string, unknown>>} */
let sessions = [];
let active = 0;
let loadGen = 0;
/** @type {"overview"|"turns"|"timeline"|"findings"|"notes"} */
let tab = "overview";
/** @type {Record<string, unknown>|null} */
let overviewCache = null;
let overviewSid = "";
let listLoading = false;
/** Debounce overview fetches while arrowing through the list. */
let overviewDebounce = 0;
let overviewPendingSid = "";
/** Accumulated timeline for the selected session (via session/timeline chunks). */
let timelineSid = "";
/** @type {Array<Record<string, unknown>>} */
let timelineEvents = [];
let timelineTotal = 0;
/** Next offset to fetch when scrolling for more. */
let timelineNextOffset = 0;
/** Chunk size per control request (server max 2000). */
const TIMELINE_CHUNK = 200;
let timelineLoading = false;
/** True while a background "load more" or full-fill is in flight. */
let timelineLoadingMore = false;
let timelineGen = 0;
/** Scroll target after timeline render (event index). */
let timelineFocusIndex = null;
/**
 * In-flight ensureTimeline for a session (join instead of cancelling).
 * @type {Promise<void>|null}
 */
let timelineEnsurePromise = null;
/** Session id for {@link timelineEnsurePromise}. */
let timelineEnsureSid = "";
/** Sub-search over loaded events (client-side fzf); triggers fill when needed. */
let timelineQuery = "";
let tlSearchDebounce = 0;
/**
 * When true, live timeline updates may stick the scroller to the bottom.
 * Cleared as soon as the operator scrolls away from the tail.
 */
let timelinePinnedToBottom = true;
/** Ignore programmatic scrollTop writes when measuring pin state. */
let timelineScrollProgrammatic = false;
/**
 * After a turn/finding jump, re-center on the focus row until this time
 * (ms, ``performance.now()``) so live paints cannot pin the scroller to 0.
 */
let timelineFocusScrollUntil = 0;

/** Palette is on-screen (poll only while visible / focused). */
let paletteLive = true;
/** @type {ReturnType<typeof setTimeout> | 0} */
let livePollTimer = 0;
let livePollBusy = false;
/** Quiet list refresh generation (ignore stale responses). */
let listLiveGen = 0;
/** Throttle full catalog re-list during live poll (every N live ticks). */
let listLiveTick = 0;
/** Live overview poll generation — must not cancel user-driven loadOverview. */
let liveOverviewGen = 0;
/** Last overview fingerprint painted for the selected session (skip no-op paints). */
let overviewPaintFp = "";

// Default chrome; overwritten from Rust after resolve (config / env override).
if (!/Mac|iPhone|iPod|iPad/i.test(navigator.platform)) {
  hotkeyHint.textContent = "Ctrl⇧G";
}
void invoke("hud_summon_shortcut")
  .then((label) => {
    if (label && hotkeyHint) hotkeyHint.textContent = String(label);
  })
  .catch(() => {});

function setStatus(text, isErr = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("err", isErr);
}

function sessionHay(row) {
  return [
    row.sessionId,
    row.title,
    row.label,
    row.model,
    row.status,
    row.origin,
    row.path,
    row.outcome,
  ]
    .map((x) => String(x || ""))
    .join(" ");
}

function queryText() {
  return q.value.trim();
}

/**
 * Normalize invoke / JSON-RPC payloads.
 * Only unwrap a true JSON-RPC envelope — never strip a list body that happens
 * to contain a nested ``result`` field for another reason.
 * @param {unknown} raw
 */
function rpcPayload(raw) {
  let v = raw;
  if (typeof v === "string") {
    try {
      v = JSON.parse(v);
    } catch {
      return raw;
    }
  }
  if (
    v &&
    typeof v === "object" &&
    !Array.isArray(v) &&
    "result" in v &&
    (Object.prototype.hasOwnProperty.call(v, "jsonrpc") ||
      Object.prototype.hasOwnProperty.call(v, "id")) &&
    !Object.prototype.hasOwnProperty.call(v, "sessions")
  ) {
    return /** @type {{ result: unknown }} */ (v).result;
  }
  return v;
}

/**
 * Extract session rows from a session/list payload (tolerant of shape drift).
 * @param {unknown} listed
 * @returns {Array<Record<string, unknown>>}
 */
function sessionRowsFromList(listed) {
  if (!listed || typeof listed !== "object") return [];
  const obj = /** @type {Record<string, unknown>} */ (listed);
  const direct = obj.sessions;
  if (Array.isArray(direct)) return direct;
  const nested = obj.result;
  if (nested && typeof nested === "object" && !Array.isArray(nested)) {
    const inner = /** @type {Record<string, unknown>} */ (nested).sessions;
    if (Array.isArray(inner)) return inner;
  }
  if (Array.isArray(listed)) return listed;
  return [];
}

function sessionSortEpoch(row) {
  const se = row?.sortEpoch;
  if (typeof se === "number" && Number.isFinite(se) && se > 0) return se;
  for (const key of ["updatedAt", "createdAt"]) {
    const raw = row?.[key];
    if (!raw) continue;
    const t = Date.parse(String(raw));
    if (Number.isFinite(t) && t > 0) return t / 1000;
  }
  return 0;
}

/**
 * Rank/refine the page already returned by session/list.
 * Authoritative catalog discovery is the server ``query`` param; fuzzy is
 * presentation-only on that page.
 * @param {{ render?: boolean, listOpts?: { scrollActive?: boolean } }} [opts]
 *   render — when false, skip list paint (live tick measures identity change).
 * @returns {boolean} true when the visible session-id order changed
 */
function applySessionFilter(opts = {}) {
  const doRender = opts.render !== false;
  const keepId =
    (sessions[active] && sessions[active].sessionId) || overviewSid || "";
  const needle = queryText();
  const prev = sessions.map((r) => String(r.sessionId || "")).join("\n");
  const ranked = needle ? fuzzyFilter(needle, allSessions, sessionHay) : allSessions.slice();
  sessions = ranked.sort((a, b) => {
    const db = sessionSortEpoch(b) - sessionSortEpoch(a);
    if (db !== 0) return db;
    return String(a.sessionId || "").localeCompare(String(b.sessionId || ""));
  });
  if (keepId) {
    const idx = sessions.findIndex((r) => String(r.sessionId || "") === String(keepId));
    active = idx >= 0 ? idx : 0;
  } else if (active >= sessions.length) {
    active = Math.max(0, sessions.length - 1);
  }
  const changed = prev !== sessions.map((r) => String(r.sessionId || "")).join("\n");
  if (doRender) renderList(opts.listOpts || {});
  return changed;
}

/** Catalog identity fingerprint (id + status + title) for skip-paint on live list. */
function catalogSig(list) {
  return list
    .map((r) => `${r.sessionId}\u0001${r.status}\u0001${r.title}`)
    .join("\n");
}

function statusClass(status) {
  const s = String(status || "").toLowerCase();
  if (s === "awaiting") return "awaiting";
  if (s.includes("run")) return "running";
  if (s.includes("complete") || s === "ok") return "complete";
  if (s.includes("end")) return "ending";
  return "";
}

/**
 * Rebuild the session list.
 * @param {{ scrollActive?: boolean }} [opts]
 *   scrollActive — when false, skip scrollIntoView (live poll must not thrash).
 */
function renderList(opts = {}) {
  const scrollActive = opts.scrollActive !== false;
  if (!sessions.length) {
    resultsEl.innerHTML = `<li class="muted">${
      listLoading ? "Loading…" : allSessions.length ? "No matches" : "No sessions"
    }</li>`;
    return;
  }
  const frag = document.createDocumentFragment();
  sessions.forEach((row, i) => {
    const li = document.createElement("li");
    li.role = "option";
    if (i === active) li.className = "active";
    const origin = row.origin || "work";
    const status = row.status || "—";
    const title = row.title || row.sessionId || "?";
    const sid = row.sessionId || "";
    const model = row.model || "";
    const originClass = String(origin).toLowerCase() === "host" ? " host" : "";
    li.innerHTML = `
      <span class="badge${originClass}">${escapeHtml(origin)}</span>
      <span class="title">${escapeHtml(title)}</span>
      <span class="status-pill ${statusClass(status)}">${escapeHtml(status)}</span>
      <span class="sub">${escapeHtml(sid)} · ${escapeHtml(model)}</span>
    `;
    li.addEventListener("click", () => {
      if (active === i && overviewSid === String(sid) && overviewCache) {
        renderDetail();
        return;
      }
      active = i;
      renderList();
      scheduleOverview(true, 0);
    });
    frag.appendChild(li);
  });
  resultsEl.innerHTML = "";
  resultsEl.appendChild(frag);
  if (scrollActive) {
    const activeEl = resultsEl.querySelector("li.active");
    if (activeEl) activeEl.scrollIntoView({ block: "nearest" });
  }
}

function setTab(next) {
  tab = next;
  for (const btn of tabsEl.querySelectorAll(".tab")) {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  }
  syncTimelineSearchBar();
  if (tab === "timeline") {
    // Fresh open of Timeline: pin to tail unless we are jumping to a focus event.
    if (timelineFocusIndex == null) timelinePinnedToBottom = true;
  }
  if (tab === "timeline" && overviewSid) {
    void ensureTimeline(overviewSid);
  }
  renderDetail();
  if (tab === "timeline") {
    requestAnimationFrame(() => scrollTimelineFocus());
  }
}

function syncTimelineSearchBar() {
  if (!tlSearchBar) return;
  const show = tab === "timeline" && Boolean(overviewSid);
  tlSearchBar.hidden = !show;
  if (!show) return;
  if (tlQ && document.activeElement !== tlQ) {
    tlQ.value = timelineQuery;
  }
}

function eventHay(ev) {
  const raw =
    ev.rawInput && typeof ev.rawInput === "object"
      ? JSON.stringify(ev.rawInput)
      : String(ev.rawInput || "");
  return [
    ev.kind,
    ev.type,
    ev.typeLabel,
    ev.heading,
    ev.toolName,
    ev.toolFamily,
    ev.preview,
    ev.content,
    ev.time,
    String(ev.index),
    raw,
  ]
    .map((x) => String(x || ""))
    .join(" ");
}

function filteredTimelineEvents() {
  const all = timelineSid === overviewSid ? timelineEvents : [];
  const needle = (timelineQuery || "").trim();
  if (!needle) return all.slice();
  return fuzzyFilter(needle, all, eventHay);
}

function timelineIsComplete() {
  // After the first successful fetch, total is authoritative (may be 0).
  return Boolean(timelineSid) && timelineNextOffset >= timelineTotal;
}

/**
 * Center the focus event row inside ``#detail`` (nested overflow scroller).
 * Uses explicit scrollTop — ``scrollIntoView`` is unreliable here after large
 * list rebuilds (especially once the operator has already scrolled the pane).
 */
function scrollTimelineFocus() {
  if (timelineFocusIndex == null) return;
  const apply = () => {
    const el = detailEl.querySelector(`[data-index="${String(timelineFocusIndex)}"]`);
    if (!el) return false;
    timelineScrollProgrammatic = true;
    timelinePinnedToBottom = false;
    const parent = detailEl.getBoundingClientRect();
    const rect = el.getBoundingClientRect();
    detailEl.scrollTop = centeredScrollTop(
      detailEl.scrollTop,
      parent.top,
      detailEl.clientHeight,
      rect.top,
      rect.height,
    );
    return true;
  };
  if (!apply()) return;
  // Large list rebuilds need a second frame before geometry is stable.
  requestAnimationFrame(() => {
    apply();
    requestAnimationFrame(() => {
      timelineScrollProgrammatic = false;
    });
  });
}

/**
 * Measure whether the detail pane is pinned to the timeline tail.
 * @returns {boolean}
 */
function detailNearBottom() {
  return shouldAutoFollowTimeline(
    detailEl.scrollTop,
    detailEl.scrollHeight,
    detailEl.clientHeight,
  );
}

/**
 * Replace or append one event row while preserving scroll position.
 * @param {Element} list
 * @param {number} ix
 * @param {string} html
 * @param {boolean} follow
 */
function upsertTimelineRow(list, ix, html, follow) {
  const existing = list.querySelector(`[data-index="${String(ix)}"]`);
  if (!existing) {
    list.insertAdjacentHTML("beforeend", html);
    return;
  }
  if (follow) {
    existing.outerHTML = html;
    return;
  }
  // Anchor: keep the top edge of this row (or viewport) stable when body grows.
  const beforeTop = existing.getBoundingClientRect().top;
  existing.outerHTML = html;
  const next = list.querySelector(`[data-index="${String(ix)}"]`);
  if (!next) return;
  const delta = next.getBoundingClientRect().top - beforeTop;
  if (Math.abs(delta) > 0.5) {
    timelineScrollProgrammatic = true;
    detailEl.scrollTop += delta;
    requestAnimationFrame(() => {
      timelineScrollProgrammatic = false;
    });
  }
}

function resetTimelineState() {
  timelineSid = "";
  timelineEvents = [];
  timelineTotal = 0;
  timelineNextOffset = 0;
  timelineLoading = false;
  timelineLoadingMore = false;
  timelineFocusIndex = null;
  // Invalidate any in-flight ensure (session switch / hard reset).
  timelineGen += 1;
  timelineEnsurePromise = null;
  timelineEnsureSid = "";
}

/**
 * Status string for the selected session (overview meta preferred over list).
 * @returns {string}
 */
function selectedStatus() {
  const meta = overviewCache && typeof overviewCache === "object" ? overviewCache.meta : null;
  if (meta && typeof meta === "object" && meta.status != null && String(meta.status).trim()) {
    return String(meta.status);
  }
  const row = sessions[active];
  return row?.status != null ? String(row.status) : "";
}

/**
 * True when the selected session should re-fetch overview/timeline on the fast path.
 * @returns {boolean}
 */
function selectedNeedsLivePoll() {
  const turns = overviewCache && typeof overviewCache === "object" ? overviewCache.turns : null;
  return sessionNeedsLivePoll(selectedStatus(), turns);
}

/**
 * Re-arm the live poll timer (fast while selected session is live).
 * @param {number} [ms]
 */
function armLivePoll(ms) {
  window.clearTimeout(livePollTimer);
  livePollTimer = 0;
  if (!paletteLive) return;
  const delay =
    ms != null && Number.isFinite(ms)
      ? Math.max(500, ms)
      : selectedNeedsLivePoll()
        ? LIVE_POLL_MS
        : IDLE_POLL_MS;
  livePollTimer = window.setTimeout(() => {
    livePollTimer = 0;
    void liveTick();
  }, delay);
}

function stopLivePoll() {
  window.clearTimeout(livePollTimer);
  livePollTimer = 0;
  livePollBusy = false;
}

/**
 * HTML for one timeline event row (shared by full paint + surgical live updates).
 * @param {Record<string, unknown>} ev
 * @returns {string}
 */
/**
 * Compact turn chip for meta (``T3``) or empty when the event is session-level.
 * @param {Record<string, unknown>} ev
 * @returns {string}
 */
function timelineTurnChip(ev) {
  if (ev.turnIndex == null || ev.turnIndex === "") return "";
  const n = Number(ev.turnIndex);
  if (!Number.isFinite(n)) return "";
  return `<span class="turn-chip" title="Turn ${escapeHtml(n)}">T${escapeHtml(n)}</span>`;
}

/**
 * Section marker when the operator turn changes between consecutive rows.
 * @param {unknown} turnIndex
 * @returns {string}
 */
function timelineTurnMarkHtml(turnIndex) {
  const n = Number(turnIndex);
  if (!Number.isFinite(n)) return "";
  return `<li class="tl-turn-mark" data-turn="${escapeHtml(n)}" aria-hidden="true"><span>Turn ${escapeHtml(n)}</span></li>`;
}

/**
 * HTML for one timeline event row (shared by full paint + surgical live updates).
 * @param {Record<string, unknown>} ev
 * @returns {string}
 */
function timelineEventLiHtml(ev) {
  const kind = ev.kind || "other";
  const family = ev.toolFamily || "";
  const err = ev.isError ? " is-error" : "";
  const body = String(ev.content || ev.preview || "").trim();
  const rawHtml =
    kind === "tool" || kind === "tool_result" ? renderRawInputHtml(ev.rawInput) : "";
  const toolBits =
    ev.toolName && (kind === "tool" || kind === "tool_result")
      ? `<span class="tool-tag fam-${escapeHtml(family)}">${escapeHtml(family || "tool")} · ${escapeHtml(ev.toolName)}</span>`
      : "";
  const focus =
    timelineFocusIndex != null && Number(ev.index) === Number(timelineFocusIndex)
      ? " ev-focus"
      : "";
  const bodyHtml = renderBodyHtml(body || "—", {
    maxLen: 10000,
    className: "in-event",
    showKind: false,
  });
  const trunc = ev.contentTruncated
    ? `<div class="content-more muted">…truncated on wire (${escapeHtml(ev.contentLength || "")} chars)</div>`
    : "";
  const turnChip = timelineTurnChip(ev);
  const metaBits = [turnChip, `#${escapeHtml(ev.index)}`, escapeHtml(ev.time || "")]
    .filter(Boolean)
    .join(" · ");
  return `<li class="ev kind-${escapeHtml(kind)}${err}${focus}" data-index="${escapeHtml(ev.index)}" data-turn="${escapeHtml(ev.turnIndex ?? "")}" id="ev-${escapeHtml(ev.index)}">
          <span class="ev-rail" aria-hidden="true"></span>
          <div class="ev-main">
            <div class="et">
              <span><span class="kind-label">${escapeHtml(ev.heading || ev.typeLabel || kind)}</span> ${toolBits}</span>
              <span class="ev-meta">${metaBits}</span>
            </div>
            <div class="ep">${bodyHtml}${trunc}</div>
            ${rawHtml ? `<div class="raw">${rawHtml}</div>` : ""}
          </div>
        </li>`;
}

/**
 * Stick scroller to bottom only when the operator is pinned there.
 * @param {boolean} follow
 */
function applyTimelineFollowScroll(follow) {
  if (!follow || !timelinePinnedToBottom) return;
  timelineScrollProgrammatic = true;
  detailEl.scrollTop = detailEl.scrollHeight;
  requestAnimationFrame(() => {
    timelineScrollProgrammatic = false;
  });
}

/**
 * Paint timeline after a live merge without wiping the whole pane when possible.
 * @param {{ added: number, updated: number, changedIndices: number[] }} merged
 * @param {{ follow: boolean, top: number }} scroll
 */
function paintTimelineLive(merged, scroll) {
  if (tab !== "timeline" || overviewSid !== timelineSid) return;
  const needle = (timelineQuery || "").trim();
  const follow = Boolean(scroll.follow) && timelinePinnedToBottom;
  const holdFocus =
    !follow &&
    timelineFocusIndex != null &&
    performance.now() < timelineFocusScrollUntil;
  // Search filter: full re-render is simpler and rare during live watch.
  if (needle || !detailEl.querySelector(".event-list")) {
    const top = detailEl.scrollTop;
    renderDetail();
    if (follow) applyTimelineFollowScroll(true);
    else if (holdFocus) requestAnimationFrame(() => scrollTimelineFocus());
    else detailEl.scrollTop = top;
    return;
  }
  const list = detailEl.querySelector(".event-list");
  if (!list || merged.changedIndices.length > 12) {
    const top = detailEl.scrollTop;
    renderDetail();
    if (follow) applyTimelineFollowScroll(true);
    else if (holdFocus) requestAnimationFrame(() => scrollTimelineFocus());
    else detailEl.scrollTop = top;
    return;
  }
  const byIndex = new Map(timelineEvents.map((e) => [Number(e.index), e]));
  // Update existing rows first (stable anchors), then append new indices in order.
  const sorted = [...merged.changedIndices].sort((a, b) => a - b);
  for (const ix of sorted) {
    const ev = byIndex.get(ix);
    if (!ev) continue;
    upsertTimelineRow(list, ix, timelineEventLiHtml(ev), follow);
  }
  // Keep search meta count honest without a full rebuild.
  if (tlSearchMeta && !needle) {
    tlSearchMeta.textContent = timelineIsComplete()
      ? `${timelineEvents.length}`
      : `${timelineEvents.length}+`;
  }
  applyTimelineFollowScroll(follow);
  if (holdFocus) requestAnimationFrame(() => scrollTimelineFocus());
}

/**
 * Pull new/updated timeline events near the tail; merge by index.
 * Preserves scroll; auto-follows when the operator was near the bottom.
 * @param {string} sid
 * @returns {Promise<boolean>} true when the buffer changed
 */
/**
 * Live-tick tail refresh: one chunk near the end via {@link fetchTimelineChunk}.
 * Soft-fails (status only) — does not blank the detail pane.
 * @param {string} sid
 * @returns {Promise<boolean>} true when the buffer changed
 */
async function refreshTimelineTail(sid) {
  if (!sid) return false;
  if (timelineLoading || timelineLoadingMore) return false;
  if (timelineSid && timelineSid !== sid) return false;
  if (!timelineSid || timelineSid !== sid) {
    if (tab === "timeline") {
      await ensureTimeline(sid, { force: false });
      return true;
    }
    return false;
  }

  const gen = timelineGen;
  if (tab === "timeline") {
    timelinePinnedToBottom = detailNearBottom();
  }
  const follow = tab === "timeline" && timelinePinnedToBottom;
  const top = detailEl.scrollTop;
  // Re-read a small tail window so the open/streaming last event updates.
  const tailBack = follow ? 4 : 0;
  const offset = Math.max(0, timelineNextOffset - tailBack);
  try {
    const result = await fetchTimelineChunk(sid, offset, {
      append: true,
      gen,
      limit: TIMELINE_CHUNK,
    });
    if (gen !== timelineGen || timelineSid !== sid) return false;
    // When lagging total with no new indices, nudge cursor so the next tick advances.
    if (
      result.added === 0 &&
      Number.isFinite(timelineTotal) &&
      timelineNextOffset < timelineTotal
    ) {
      timelineNextOffset = Math.max(
        timelineNextOffset,
        Math.min(timelineTotal, offset + result.batch.length),
      );
    }
    const changed = result.added > 0 || result.updated > 0;
    if (changed && tab === "timeline" && overviewSid === sid) {
      paintTimelineLive(result, { follow, top });
    }
    return changed;
  } catch (e) {
    setStatus(String(e), true);
    return false;
  }
}

/**
 * One live-refresh cycle: overview + timeline tail while selected session is live.
 */
async function liveTick() {
  if (!paletteLive || livePollBusy) {
    armLivePoll();
    return;
  }
  try {
    const focused = await getCurrentWindow().isFocused();
    if (!focused) {
      // Hidden/blurred palette: pause until shown again (onPaletteShown restarts).
      armLivePoll(IDLE_POLL_MS);
      return;
    }
  } catch {
    /* keep polling if focus probe fails */
  }
  livePollBusy = true;
  try {
    const row = sessions[active];
    const sid = row?.sessionId ? String(row.sessionId) : "";
    const live = selectedNeedsLivePoll();
    if (sid && live) {
      // Timeline-focused live watch: prefer tail RPC only (lighter, less paint).
      // Overview every other tick for status / turns / list pill.
      listLiveTick += 1;
      const wantOverview = tab !== "timeline" || listLiveTick % 2 === 0;
      if (wantOverview) {
        await loadOverview(false, { quiet: true, session: sid });
      }
      if (timelineSid === sid || tab === "timeline") {
        await refreshTimelineTail(sid);
      } else if (tab === "overview" || tab === "turns") {
        // keep warm for a quick switch to Timeline
        void ensureTimeline(sid, { force: false });
      }
      // Full catalog rarely — selected row is patched from overview.
      if (listLiveTick % 5 === 0) void refreshListFromServer({ quiet: true });
    } else if (sid && tab === "timeline" && isLiveStatus(selectedStatus())) {
      await refreshTimelineTail(sid);
    } else {
      // Idle interval is already slow; refresh catalog for new/finished runs.
      void refreshListFromServer({ quiet: true });
    }
  } finally {
    livePollBusy = false;
    armLivePoll();
  }
}

/**
 * Fetch one chunk and apply into *timelineEvents* (sole timeline RPC apply path).
 * @param {string} sid
 * @param {number} offset
 * @param {{ append?: boolean, gen?: number, limit?: number }} [opts]
 * @returns {Promise<{ batch: Array<Record<string, unknown>>, added: number, updated: number, changedIndices: number[], total: number }>}
 */
async function fetchTimelineChunk(sid, offset, opts = {}) {
  const append = Boolean(opts.append);
  const gen = opts.gen;
  const limit = Number.isFinite(opts.limit) ? Number(opts.limit) : TIMELINE_CHUNK;
  const data = rpcPayload(
    await invoke("control_session_timeline", {
      session: sid,
      offset,
      limit,
      contentChars: 12000,
    }),
  );
  if (gen != null && gen !== timelineGen) {
    return { batch: [], added: 0, updated: 0, changedIndices: [], total: timelineTotal };
  }
  const batch = Array.isArray(data?.events) ? data.events : [];
  const total = Number(data?.total);
  const off = Number(data?.offset);
  const base = Number.isFinite(off) ? off : offset;
  const resolvedTotal = Number.isFinite(total) && total >= 0 ? total : timelineTotal;
  /** @type {{ events: Array<Record<string, unknown>>, added: number, updated: number, changedIndices: number[] }} */
  let merged = { events: timelineEvents, added: 0, updated: 0, changedIndices: [] };
  if (append && timelineSid === sid && timelineEvents.length) {
    merged = mergeTimelineByIndex(timelineEvents, batch);
    timelineEvents = merged.events;
  } else {
    timelineEvents = batch;
    merged = {
      events: batch,
      added: batch.length,
      updated: 0,
      changedIndices: batch.map((e) => Number(e.index)).filter((n) => Number.isFinite(n)),
    };
  }
  timelineSid = sid;
  if (Number.isFinite(resolvedTotal) && resolvedTotal >= 0) {
    timelineTotal = resolvedTotal;
  } else {
    timelineTotal = timelineEvents.length;
  }
  // Never rewind the high-water mark when a seek/fill loads an earlier window.
  timelineNextOffset = Math.max(timelineNextOffset, base + batch.length);
  if (Number.isFinite(timelineTotal) && timelineTotal > 0) {
    timelineNextOffset = Math.min(timelineNextOffset, timelineTotal);
  }
  if (batch.length === 0 && Number.isFinite(timelineTotal)) {
    if (base >= timelineTotal) timelineNextOffset = Math.max(timelineNextOffset, timelineTotal);
  }
  return {
    batch,
    added: merged.added,
    updated: merged.updated,
    changedIndices: merged.changedIndices,
    total: timelineTotal,
  };
}

/**
 * True when *timelineEvents* already contains the focus event index.
 * @param {number|null|undefined} focusIndex
 */
function timelineHasFocus(focusIndex) {
  if (focusIndex == null || Number.isNaN(Number(focusIndex))) return true;
  const target = Number(focusIndex);
  return timelineEvents.some((e) => Number(e.index) === target);
}

/**
 * Append chunks at *timelineNextOffset* until *done(events)* or complete / cancelled.
 * @param {string} sid
 * @param {(evs: Array<Record<string, unknown>>) => boolean} done — stop when true
 * @param {number} [gen]
 */
async function fillTimelineUntil(sid, done, gen = timelineGen) {
  while (
    gen === timelineGen &&
    timelineSid === sid &&
    !done(timelineEvents) &&
    !timelineIsComplete()
  ) {
    timelineLoadingMore = true;
    await fetchTimelineChunk(sid, timelineNextOffset, { append: true, gen });
    if (gen !== timelineGen) return;
  }
  if (gen === timelineGen) timelineLoadingMore = false;
}

/**
 * Ensure *focusIndex* is in the buffer: one seek window, then sequential fill if needed.
 * Unfiltered session/timeline offsets match sequential event.index.
 * @param {string} sid
 * @param {number|null|undefined} focusIndex
 * @param {number} [gen]
 */
async function ensureTimelineFocus(sid, focusIndex, gen = timelineGen) {
  if (focusIndex == null || Number.isNaN(Number(focusIndex))) return;
  const target = Number(focusIndex);
  if (timelineHasFocus(target)) return;
  const windowStart = timelineSeekOffset(target, 20);
  timelineLoadingMore = true;
  try {
    // One window around the target (merge). Never walk only from a late nextOffset.
    await fetchTimelineChunk(sid, windowStart, {
      append: timelineEvents.length > 0 && timelineSid === sid,
      gen,
      limit: Math.min(TIMELINE_CHUNK, 120),
    });
    if (gen !== timelineGen || timelineHasFocus(target)) return;
    // Still missing: fill from the head (restore high-water mark after).
    const savedNext = timelineNextOffset;
    timelineNextOffset = 0;
    try {
      await fillTimelineUntil(sid, () => timelineHasFocus(target), gen);
    } finally {
      if (gen === timelineGen) {
        timelineNextOffset = Math.max(timelineNextOffset, savedNext);
      }
    }
  } finally {
    if (gen === timelineGen) timelineLoadingMore = false;
  }
}

/**
 * Paint timeline tab after load/focus work (idempotent).
 * @param {string} sid
 */
function paintTimelineIfActive(sid) {
  if (tab !== "timeline") return;
  if (overviewSid && overviewSid !== sid) return;
  renderDetail();
  requestAnimationFrame(() => scrollTimelineFocus());
}

/**
 * Ensure an initial chunk (or more until *focusIndex* is present).
 *
 * Same-session calls **join** an in-flight load (turn jump while prefetching)
 * instead of cancelling it. *force* only when the session identity changes.
 *
 * @param {string} sid
 * @param {{ force?: boolean, focusIndex?: number|null }} [opts]
 * @returns {Promise<void>}
 */
async function ensureTimeline(sid, opts = {}) {
  const force = Boolean(opts.force);
  if (opts.focusIndex != null && opts.focusIndex !== "") {
    const n = Number(opts.focusIndex);
    if (!Number.isNaN(n)) timelineFocusIndex = n;
  }
  if (!sid) return;

  // Join in-flight ensure for this session — do not ++timelineGen (that cancels).
  if (!force && timelineEnsurePromise && timelineEnsureSid === sid) {
    try {
      await timelineEnsurePromise;
    } catch {
      /* primary path reports errors */
    }
    if (timelineFocusIndex != null && timelineSid === sid) {
      await ensureTimelineFocus(sid, timelineFocusIndex, timelineGen);
    }
    paintTimelineIfActive(sid);
    return;
  }

  // Already warm: only pull focus if missing.
  if (!force && timelineSid === sid && timelineEvents.length && !timelineLoading) {
    if (timelineFocusIndex != null && !timelineHasFocus(timelineFocusIndex)) {
      await ensureTimelineFocus(sid, timelineFocusIndex, timelineGen);
    }
    paintTimelineIfActive(sid);
    return;
  }

  const gen = ++timelineGen;
  timelineLoading = true;
  timelineLoadingMore = false;
  if (force || timelineSid !== sid) {
    timelineEvents = [];
    timelineTotal = 0;
    timelineNextOffset = 0;
    timelineSid = "";
  }
  if (tab === "timeline") renderDetail();

  const run = (async () => {
    try {
      // Seed one chunk: window around focus when jumping, else head.
      if (!timelineEvents.length || timelineSid !== sid) {
        const seed =
          timelineFocusIndex != null && Number.isFinite(Number(timelineFocusIndex))
            ? timelineSeekOffset(Number(timelineFocusIndex), 20)
            : 0;
        await fetchTimelineChunk(sid, seed, {
          append: false,
          gen,
          limit: Math.min(TIMELINE_CHUNK, 120),
        });
        if (gen !== timelineGen) return;
      }
      // Single focus policy (seek window + fill if still missing).
      if (timelineFocusIndex != null) {
        await ensureTimelineFocus(sid, timelineFocusIndex, gen);
        if (gen !== timelineGen) return;
      }
      timelineLoading = false;
      paintTimelineIfActive(sid);
    } catch (e) {
      if (gen !== timelineGen) return;
      timelineLoading = false;
      timelineEvents = [];
      timelineTotal = 0;
      timelineNextOffset = 0;
      timelineSid = "";
      if (tab === "timeline") {
        detailEl.innerHTML = `<p class="err">${escapeHtml(e)}</p>`;
        setStatus(String(e), true);
      }
    }
  })();

  timelineEnsureSid = sid;
  timelineEnsurePromise = run;
  try {
    await run;
  } finally {
    if (timelineEnsureSid === sid) {
      timelineEnsurePromise = null;
      timelineEnsureSid = "";
    }
  }
}

/** Load the next chunk when the operator scrolls near the bottom. */
async function loadMoreTimeline() {
  if (!overviewSid || tab !== "timeline") return;
  if (timelineLoading || timelineLoadingMore) return;
  if (timelineSid !== overviewSid) return;
  if (timelineIsComplete()) return;
  const gen = timelineGen;
  const beforeLen = timelineEvents.length;
  timelineLoadingMore = true;
  if (tab === "timeline") {
    // Soft footer only — avoid full re-render jank until chunk arrives.
    const foot = detailEl.querySelector(".tl-load-more");
    if (foot) foot.textContent = "Loading more…";
  }
  try {
    await fetchTimelineChunk(overviewSid, timelineNextOffset, { append: true });
    if (gen !== timelineGen) return;
    timelineLoadingMore = false;
    if (tab !== "timeline" || overviewSid !== timelineSid) return;
    const list = detailEl.querySelector(".event-list");
    const needle = (timelineQuery || "").trim();
    if (!list || needle) {
      const top = detailEl.scrollTop;
      renderDetail();
      detailEl.scrollTop = top;
      return;
    }
    // Append only new rows — full innerHTML rebuild jumps the scroller.
    let lastTurn = null;
    const lastEv = list.querySelector("li.ev:last-of-type");
    if (lastEv?.dataset.turn != null && lastEv.dataset.turn !== "") {
      const n = Number(lastEv.dataset.turn);
      if (Number.isFinite(n)) lastTurn = n;
    }
    for (let i = beforeLen; i < timelineEvents.length; i++) {
      const ev = timelineEvents[i];
      const ix = Number(ev.index);
      if (list.querySelector(`[data-index="${String(ix)}"]`)) continue;
      const ti =
        ev.turnIndex != null && ev.turnIndex !== "" && Number.isFinite(Number(ev.turnIndex))
          ? Number(ev.turnIndex)
          : null;
      if (ti != null && ti !== lastTurn) {
        list.insertAdjacentHTML("beforeend", timelineTurnMarkHtml(ti));
        lastTurn = ti;
      }
      list.insertAdjacentHTML("beforeend", timelineEventLiHtml(ev));
    }
    if (tlSearchMeta) {
      tlSearchMeta.textContent = timelineIsComplete()
        ? `${timelineEvents.length}`
        : `${timelineEvents.length}+`;
    }
    const foot = detailEl.querySelector(".tl-load-more");
    if (timelineIsComplete()) {
      if (foot) foot.remove();
    } else if (foot) {
      foot.textContent = "Scroll for more";
    } else {
      list.insertAdjacentHTML(
        "afterend",
        `<p class="tl-load-more muted">Scroll for more</p>`,
      );
    }
  } catch (e) {
    if (gen !== timelineGen) return;
    timelineLoadingMore = false;
    setStatus(String(e), true);
  }
}

/**
 * For search: pull remaining chunks so matches are not limited to the first page.
 * @param {string} sid
 */
async function ensureTimelineFilledForSearch(sid) {
  if (!sid || timelineSid !== sid) return;
  if (timelineIsComplete() || timelineLoading || timelineLoadingMore) return;
  const gen = timelineGen;
  timelineLoadingMore = true;
  try {
    // done() never true → fillTimelineUntil stops only when complete / cancelled.
    await fillTimelineUntil(sid, () => false, gen);
  } finally {
    if (gen === timelineGen) timelineLoadingMore = false;
  }
  if (gen === timelineGen && tab === "timeline" && overviewSid === sid) {
    renderDetail();
  }
}

function onDetailScroll() {
  if (tab !== "timeline") return;
  if (!timelineScrollProgrammatic) {
    timelinePinnedToBottom = detailNearBottom();
  }
  const room = detailEl.scrollHeight - detailEl.scrollTop - detailEl.clientHeight;
  if (room < 160) void loadMoreTimeline();
}

/**
 * Jump from a turn/finding card to the timeline event (user prompt or first event).
 * Switches tab and shows loading immediately; does not cancel an in-flight
 * prefetch for the same session.
 * @param {unknown} index
 */
function jumpToTimelineEvent(index) {
  if (index == null || index === "") return;
  const target = Number(index);
  if (Number.isNaN(target)) return;
  timelineFocusIndex = target;
  timelinePinnedToBottom = false;
  // Keep re-centering through the next paint/live tick after a jump.
  timelineFocusScrollUntil = performance.now() + 2000;
  // Filter can hide the target row from the DOM even when it is in the buffer.
  if (timelineQuery) {
    timelineQuery = "";
    if (tlQ) tlQ.value = "";
  }
  tab = "timeline";
  for (const btn of tabsEl.querySelectorAll(".tab")) {
    btn.classList.toggle("active", btn.dataset.tab === "timeline");
  }
  syncTimelineSearchBar();
  if (!overviewSid) {
    renderDetail();
    return;
  }
  const sid = overviewSid;
  const haveTarget =
    timelineSid === sid && timelineHasFocus(target) && timelineEvents.length > 0;
  // Immediate feedback: never leave the Turns list painted under a Timeline tab.
  if (!haveTarget) {
    if (timelineSid !== sid) {
      timelineLoading = true;
    } else if (!timelineLoading) {
      timelineLoadingMore = true;
    }
    renderDetail();
  } else {
    // Buffer already has the row (common after scrolling the timeline). Still
    // force a full paint + centered scroll — do not rely on leftover scrollTop.
    renderDetail();
    requestAnimationFrame(() => scrollTimelineFocus());
    return;
  }
  const needForce = timelineSid !== "" && timelineSid !== sid;
  void ensureTimeline(sid, {
    force: needForce,
    focusIndex: target,
  }).then(() => {
    timelineLoadingMore = false;
    if (tab === "timeline" && overviewSid === sid) {
      paintTimelineIfActive(sid);
    }
  });
}

function renderOverviewTab(o, meta, summary) {
  const chips = `
    <div class="hero-meta">
      <span class="chip ${statusClass(meta.status)}">${escapeHtml(meta.status || "—")}</span>
      <span class="chip muted">${escapeHtml(meta.model || "")}</span>
      <span class="chip muted">${escapeHtml(meta.origin || "")}</span>
      <span class="chip muted">${escapeHtml(meta.duration || "")}</span>
      <span class="chip muted">${escapeHtml(meta.contextUsageCompact || meta.contextUsage || "ctx —")}</span>
    </div>`;
  const sum = summary
    ? `<div class="summary-box">${renderBodyHtml(summary, { maxLen: 4000, className: "in-summary" })}</div>`
    : `<p class="empty">No summary text for this session.</p>`;
  return `
    <h2>${escapeHtml(meta.title || overviewSid)}</h2>
    ${chips}
    ${sum}
    <dl class="kv">
      <dt>id</dt><dd>${escapeHtml(meta.sessionId || overviewSid)}</dd>
      <dt>events</dt><dd>${escapeHtml(meta.numEvents ?? o.timeline?.total ?? "—")}</dd>
      <dt>tools</dt><dd>${escapeHtml(meta.toolCallCount ?? "—")} (${escapeHtml(meta.errorCount ?? 0)} err)</dd>
      <dt>turns</dt><dd>${escapeHtml(o.turns?.total ?? "—")}</dd>
      <dt>findings</dt><dd>${escapeHtml(o.findings?.total ?? o.findings?.count ?? 0)}</dd>
      <dt>notes</dt><dd>${escapeHtml(o.notes?.count ?? 0)}</dd>
      <dt>git</dt><dd>${escapeHtml([meta.gitRepo, meta.gitBranch].filter(Boolean).join(" · ") || "—")}</dd>
      <dt>path</dt><dd>${escapeHtml(meta.path || "")}</dd>
    </dl>`;
}

function renderTurnsTab(o) {
  // Search box only filters the session list — not detail tabs.
  const turns = Array.isArray(o.turns?.turns) ? o.turns.turns : [];
  if (!turns.length) {
    return `<p class="empty">No turns segmented.</p>`;
  }
  return `<ul class="turn-list">${turns
    .map((t) => {
      const open = t.open ? " · open" : "";
      const outcome = t.outcome ? ` · ${t.outcome}` : "";
      const summary = String(t.summary || "").trim();
      const jumpIdx =
        t.userEventIndex != null
          ? t.userEventIndex
          : t.firstIndex != null
            ? t.firstIndex
            : "";
      return `<li class="kind-session turn-row" data-jump-index="${escapeHtml(jumpIdx)}" role="button" tabindex="0">
        <div class="tl">${escapeHtml(t.label || `turn ${t.turnIndex}`)}</div>
        ${
          summary
            ? `<div class="turn-summary">${renderBodyHtml(summary, { maxLen: 1200, className: "in-turn" })}</div>`
            : `<div class="turn-summary muted">No user prompt in this turn</div>`
        }
        <div class="tm">prompt ${escapeHtml(t.promptIndex ?? "—")} · events ${escapeHtml(t.eventCount)} · tools ${escapeHtml(t.toolCallCount)} (${escapeHtml(t.toolErrorCount)} err)${escapeHtml(outcome)}${escapeHtml(open)} · idx ${escapeHtml(t.firstIndex)}–${escapeHtml(t.lastIndex)}</div>
      </li>`;
    })
    .join("")}</ul>`;
}

function renderTimelineTab() {
  if (
    timelineLoading &&
    !timelineEvents.length &&
    (timelineSid === overviewSid || !timelineSid)
  ) {
    if (tlSearchMeta) tlSearchMeta.textContent = "";
    const jumping =
      timelineFocusIndex != null
        ? `Jumping to event #${escapeHtml(timelineFocusIndex)}…`
        : "Loading timeline…";
    return `<p class="loading">${jumping}</p>`;
  }
  const all = timelineSid === overviewSid ? timelineEvents : [];
  const total =
    timelineSid === overviewSid
      ? timelineTotal
      : overviewCache?.timeline?.total ?? 0;
  if (!all.length) {
    if (tlSearchMeta) tlSearchMeta.textContent = "";
    if (
      timelineLoading ||
      timelineLoadingMore ||
      total > 0 ||
      (overviewCache?.timeline?.lazy && (overviewCache?.timeline?.total || 0) > 0)
    ) {
      return `<p class="loading">Loading timeline…</p>`;
    }
    return `<p class="empty">No timeline events.</p>`;
  }
  const events = filteredTimelineEvents();
  const needle = (timelineQuery || "").trim();
  if (tlSearchMeta) {
    tlSearchMeta.textContent = needle
      ? `${events.length} match`
      : timelineIsComplete()
        ? `${all.length}`
        : `${all.length}+`;
  }
  if (!events.length) {
    const filling = needle && !timelineIsComplete() && (timelineLoadingMore || timelineLoading);
    if (filling) {
      return `<p class="loading">Searching timeline…</p>`;
    }
    return `<p class="empty">No events match “${escapeHtml(needle)}”.</p>`;
  }
  const foot =
    !needle && !timelineIsComplete()
      ? `<p class="tl-load-more muted">${timelineLoadingMore ? "Loading more…" : "Scroll for more"}</p>`
      : needle && !timelineIsComplete() && timelineLoadingMore
        ? `<p class="tl-load-more muted">Loading more for search…</p>`
        : "";
  // Turn section marks when the sequential operator turn changes between rows.
  let lastTurn = null;
  const rows = [];
  for (const ev of events) {
    const ti =
      ev.turnIndex != null && ev.turnIndex !== "" && Number.isFinite(Number(ev.turnIndex))
        ? Number(ev.turnIndex)
        : null;
    if (ti != null && ti !== lastTurn) {
      rows.push(timelineTurnMarkHtml(ti));
      lastTurn = ti;
    }
    rows.push(timelineEventLiHtml(ev));
  }
  return `<ul class="event-list">${rows.join("")}</ul>${foot}`;
}

/**
 * Short local time for note meta (not raw ISO).
 * @param {unknown} iso
 */
function formatNoteTime(iso) {
  const s = String(iso || "").trim();
  if (!s) return "";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  try {
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return s;
  }
}

/**
 * Prefer summary/detail as title/body; everything else is an extra field chip
 * (including severity/priority/level — whatever the notes schema defines).
 * @param {Record<string, unknown>} fields
 * @returns {{ title: string, body: string, extras: Array<[string, string]> }}
 */
function noteFieldsView(fields) {
  const f = fields && typeof fields === "object" ? fields : {};
  const get = (k) => String(f[k] ?? "").trim();
  const title = get("summary") || get("title") || get("issue") || "";
  const body = get("detail") || get("body") || get("notes") || get("description") || "";
  // Structural body fields only — do not special-case severity vocabularies.
  const skip = new Set([
    "summary",
    "title",
    "issue",
    "detail",
    "body",
    "notes",
    "description",
  ]);
  /** @type {Array<[string, string]>} */
  const extras = [];
  for (const [k, v] of Object.entries(f)) {
    if (skip.has(k)) continue;
    const val = String(v ?? "").trim();
    if (!val) continue;
    extras.push([k, val]);
  }
  // Prefer a real title; if only extras, use first extra as title.
  let t = title;
  let b = body;
  if (!t && !b && extras.length) {
    t = extras[0][1];
    extras.shift();
  }
  if (!t && b) {
    // First line as title when summary empty.
    const line = b.split("\n").find((x) => x.trim());
    t = line ? line.trim().slice(0, 120) : "";
    if (line && b.trim() === line.trim()) b = "";
  }
  return { title: t, body: b, extras };
}

function renderFindingsTab(o) {
  const block = o.findings && typeof o.findings === "object" ? o.findings : {};
  const findings = Array.isArray(block.findings) ? block.findings : [];
  const total = Number(block.total) || findings.length;
  const plugins = Array.isArray(block.plugins) ? block.plugins : [];
  if (!findings.length) {
    return `<p class="empty">No analysis findings in cache for this session.</p>
      <p class="list-meta">Run analysis in the TUI (or plugins) so results land under ~/.groket/cache/analysis.</p>`;
  }
  const head = `<p class="list-meta">${findings.length}${
    total > findings.length ? ` of ${total}` : ""
  } finding${findings.length === 1 ? "" : "s"}${
    plugins.length ? ` · ${escapeHtml(plugins.join(", "))}` : ""
  }${block.truncated ? " · truncated" : ""}</p>`;
  return (
    head +
    `<ul class="finding-list">${findings
      .map((f) => {
        // Severity is opaque schema text from the analyzer — display as-is.
        const sev = String(f.severity || "").trim();
        const turns = Array.isArray(f.turnIndices) ? f.turnIndices : [];
        const events = Array.isArray(f.eventIndices) ? f.eventIndices : [];
        const primaryEv =
          f.primaryEventIndex != null && f.primaryEventIndex !== ""
            ? Number(f.primaryEventIndex)
            : events.length
              ? Number(events[0])
              : null;
        const turnChips = turns.length
          ? turns
              .slice(0, 6)
              .map((t) => `<span class="finding-chip">Turn ${escapeHtml(t)}</span>`)
              .join("")
          : `<span class="finding-chip muted">No turn link</span>`;
        const eventChips = events.length
          ? events
              .slice(0, 8)
              .map(
                (ei) =>
                  `<button type="button" class="finding-event" data-jump-index="${escapeHtml(ei)}">#${escapeHtml(ei)}</button>`,
              )
              .join("")
          : "";
        const cat = String(f.category || "").trim();
        const detail = String(f.detail || "").trim();
        const plug = String(f.pluginId || "").trim();
        const extras = f.extras && typeof f.extras === "object" ? f.extras : {};
        // Show whatever extras keys the analyzer stored (not a fixed MF set).
        const extraBits = Object.entries(extras)
          .filter(([, v]) => String(v ?? "").trim())
          .slice(0, 6)
          .map(([k, v]) => {
            const text = String(v).trim();
            return `<div class="finding-extra"><span class="finding-extra-k">${escapeHtml(String(k).replaceAll("_", " "))}</span>${escapeHtml(text.slice(0, 400))}${text.length > 400 ? "…" : ""}</div>`;
          })
          .join("");
        const jumpAttr =
          primaryEv != null && !Number.isNaN(primaryEv)
            ? ` data-jump-index="${escapeHtml(primaryEv)}" role="button" tabindex="0"`
            : "";
        return `<li class="finding-card"${jumpAttr}>
          <div class="finding-head">
            ${sev ? `<span class="field-badge">${escapeHtml(sev)}</span>` : ""}
            ${plug ? `<span class="finding-plugin">${escapeHtml(plug)}</span>` : ""}
            <span class="finding-turns">${turnChips}</span>
          </div>
          <div class="finding-title">${escapeHtml(f.title || "Finding")}</div>
          ${cat ? `<div class="finding-cat">${escapeHtml(cat)}</div>` : ""}
          ${
            detail
              ? `<div class="finding-detail">${renderBodyHtml(detail, {
                  maxLen: 2500,
                  className: "in-finding",
                  showKind: false,
                })}</div>`
              : ""
          }
          ${extraBits}
          ${
            eventChips
              ? `<div class="finding-events"><span class="finding-events-label">Events</span> ${eventChips}</div>`
              : ""
          }
        </li>`;
      })
      .join("")}</ul>`
  );
}

function wireFindingClicks() {
  for (const el of detailEl.querySelectorAll("[data-jump-index]")) {
    const go = () => {
      const raw = el.getAttribute("data-jump-index");
      if (raw === "" || raw == null) return;
      jumpToTimelineEvent(Number(raw));
    };
    el.addEventListener("click", (ev) => {
      ev.preventDefault();
      go();
    });
    el.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        go();
      }
    });
  }
}

function renderNotesTab(o) {
  const rawNotes = Array.isArray(o.notes?.notes) ? o.notes.notes.slice() : [];
  const rev = o.notes?.revision ? String(o.notes.revision) : "";
  if (!rawNotes.length) {
    return `<p class="empty">No operator notes on this session.</p>`;
  }
  // Newest update first.
  const notes = rawNotes.sort((a, b) => {
    const ta = Date.parse(String(a.updatedAt || a.createdAt || "")) || 0;
    const tb = Date.parse(String(b.updatedAt || b.createdAt || "")) || 0;
    return tb - ta;
  });
  const head = `<p class="list-meta">${notes.length} note${notes.length === 1 ? "" : "s"}${
    rev ? ` · rev ${escapeHtml(rev.slice(0, 12))}` : ""
  }</p>`;
  return (
    head +
    `<ul class="note-list">${notes
      .map((n) => {
        const fields = n.fields && typeof n.fields === "object" ? n.fields : {};
        const view = noteFieldsView(fields);
        const turn =
          n.turnIndex != null && n.turnIndex !== ""
            ? `Turn ${escapeHtml(n.turnIndex)}`
            : "Session";
        const when = formatNoteTime(n.updatedAt || n.createdAt);
        const indices = Array.isArray(n.eventIndices)
          ? n.eventIndices.filter((x) => x != null && x !== "").slice(0, 8)
          : [];
        const empty = !view.title && !view.body && !view.extras.length;
        const titleHtml = view.title
          ? `<div class="note-title">${escapeHtml(view.title)}</div>`
          : empty
            ? `<div class="note-title muted">Empty note</div>`
            : "";
        const bodyHtml = view.body
          ? `<div class="note-body">${renderBodyHtml(view.body, {
              maxLen: 4000,
              className: "in-note",
              showKind: false,
            })}</div>`
          : "";
        const chips = view.extras.length
          ? `<div class="note-chips">${view.extras
              .slice(0, 8)
              .map(
                ([k, v]) =>
                  `<span class="note-chip"><span class="note-chip-k">${escapeHtml(k)}</span>${escapeHtml(v)}</span>`,
              )
              .join("")}</div>`
          : "";
        const idShort = String(n.id || "").replace(/^n-/, "");
        const footBits = [
          idShort ? `#${idShort.slice(0, 10)}` : "",
          indices.length ? `events ${indices.join(", ")}` : "",
        ].filter(Boolean);
        return `<li class="note-card${empty ? " note-empty" : ""}">
          <div class="note-head">
            <span class="note-turn">${turn}</span>
            ${when ? `<span class="note-time">${escapeHtml(when)}</span>` : ""}
          </div>
          ${titleHtml}
          ${bodyHtml}
          ${chips}
          ${footBits.length ? `<div class="note-foot">${escapeHtml(footBits.join(" · "))}</div>` : ""}
        </li>`;
      })
      .join("")}</ul>`
  );
}

function renderDetailSkeleton(sid) {
  detailEl.innerHTML = `
    <div class="skeleton" aria-busy="true" aria-label="Loading ${escapeHtml(sid)}">
      <div class="skeleton-line h18 w70"></div>
      <div class="skeleton-line w40"></div>
      <div class="skeleton-line h48 w90"></div>
      <div class="skeleton-line w90"></div>
      <div class="skeleton-line w70"></div>
    </div>`;
}

function renderDetail() {
  if (!overviewCache || !overviewSid) {
    if (overviewPendingSid) {
      renderDetailSkeleton(overviewPendingSid);
      return;
    }
    detailEl.innerHTML = `<p class="empty-state">Select a session</p>`;
    return;
  }
  const o = overviewCache;
  const meta = o.meta && typeof o.meta === "object" ? o.meta : {};
  const summary = String(o.summary || meta.summary || "").trim();

  syncTimelineSearchBar();
  if (tab === "overview") {
    detailEl.innerHTML = renderOverviewTab(o, meta, summary);
  } else if (tab === "turns") {
    detailEl.innerHTML = renderTurnsTab(o);
    wireTurnClicks();
  } else if (tab === "timeline") {
    detailEl.innerHTML = renderTimelineTab();
    requestAnimationFrame(() => scrollTimelineFocus());
  } else if (tab === "findings") {
    detailEl.innerHTML = renderFindingsTab(o);
    wireFindingClicks();
  } else {
    detailEl.innerHTML = renderNotesTab(o);
  }
}

function wireTurnClicks() {
  for (const li of detailEl.querySelectorAll(".turn-row")) {
    const go = () => {
      const raw = li.getAttribute("data-jump-index");
      if (raw === "" || raw == null) return;
      jumpToTimelineEvent(Number(raw));
    };
    li.addEventListener("click", go);
    li.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        go();
      }
    });
  }
}

/**
 * Schedule overview load. Keyboard nav uses a short debounce so holding arrows
 * does not stampede the control owner; clicks use delay 0.
 * @param {boolean} force
 * @param {number} delayMs
 */
function scheduleOverview(force, delayMs = 90) {
  const row = sessions[active];
  const sid = row?.sessionId ? String(row.sessionId) : "";
  overviewPendingSid = sid;
  window.clearTimeout(overviewDebounce);
  if (!sid) {
    overviewCache = null;
    overviewSid = "";
    overviewPendingSid = "";
    resetTimelineState();
    renderDetail();
    return;
  }
  if (!force && overviewSid === sid && overviewCache) {
    overviewPendingSid = "";
    renderDetail();
    return;
  }
  // Immediate skeleton so the list click feels instant (RPC is off-main-thread).
  if (overviewSid !== sid) {
    overviewCache = null;
    overviewPaintFp = "";
    liveOverviewGen += 1; // drop in-flight live overview for previous sid
    resetTimelineState();
    timelineQuery = "";
    if (tlQ) tlQ.value = "";
    renderDetailSkeleton(sid);
  }
  overviewDebounce = window.setTimeout(() => {
    void loadOverview(force);
  }, Math.max(0, delayMs));
}

/**
 * Load session overview for the selection (or *opts.session* on live ticks).
 * @param {boolean} [force]
 * @param {{ quiet?: boolean, session?: string }} [opts]
 *   quiet — live poll: no skeleton, soft status on error, fingerprint skip-paint.
 * @returns {Promise<boolean|void>} quiet path returns success bool
 */
async function loadOverview(force = false, opts = {}) {
  const quiet = Boolean(opts.quiet);
  if (quiet) {
    const sid = opts.session ? String(opts.session) : "";
    if (!sid) return false;
    const gen = ++liveOverviewGen;
    const top = detailEl.scrollTop;
    try {
      const data = rpcPayload(await invoke("control_session_overview", { session: sid }));
      if (gen !== liveOverviewGen) return false;
      if (!data || typeof data !== "object") return false;
      const row = sessions[active];
      if (!row?.sessionId || String(row.sessionId) !== sid) return false;
      if (overviewPendingSid && overviewPendingSid !== sid) return false;
      overviewCache = data;
      overviewSid = sid;
      const meta = data.meta && typeof data.meta === "object" ? data.meta : null;
      if (meta) {
        const a = patchListRowFromMeta(allSessions, sid, meta);
        const b = patchListRowFromMeta(sessions, sid, meta);
        if (a.listPaint || b.listPaint) {
          renderList({ scrollActive: false });
        }
      }
      const fp = overviewPaintFingerprint(data);
      const paint = fp !== overviewPaintFp;
      overviewPaintFp = fp;
      // Timeline body is owned by refreshTimelineTail (avoid scroll jank here).
      if (paint && tab !== "timeline") {
        renderDetail();
        detailEl.scrollTop = top;
      }
      return true;
    } catch (e) {
      setStatus(String(e), true);
      return false;
    }
  }

  const row = sessions[active];
  if (!row?.sessionId) {
    overviewCache = null;
    overviewSid = "";
    overviewPendingSid = "";
    overviewPaintFp = "";
    renderDetail();
    return;
  }
  const sid = String(row.sessionId);
  if (!force && overviewSid === sid && overviewCache) {
    overviewPendingSid = "";
    renderDetail();
    return;
  }
  const gen = ++loadGen;
  liveOverviewGen += 1; // user load wins over live poll
  overviewPendingSid = sid;
  if (overviewSid !== sid || !overviewCache) {
    renderDetailSkeleton(sid);
  }
  const t0 = performance.now();
  try {
    const data = rpcPayload(await invoke("control_session_overview", { session: sid }));
    if (gen !== loadGen) return;
    if (!data || typeof data !== "object") {
      throw new Error("empty session/overview response");
    }
    overviewCache = data;
    overviewSid = sid;
    overviewPendingSid = "";
    overviewPaintFp = overviewPaintFingerprint(data);
    renderDetail();
    const ms = Math.round(performance.now() - t0);
    const status = data.meta && typeof data.meta === "object" ? data.meta.status : "";
    setStatus(`${sid} · ${status || ""} · ${ms}ms`);
    // Prefetch timeline in background so Turns→jump and Timeline tab are warm.
    void ensureTimeline(sid);
  } catch (e) {
    if (gen !== loadGen) return;
    overviewCache = null;
    overviewSid = "";
    overviewPendingSid = "";
    overviewPaintFp = "";
    detailEl.innerHTML = `<p class="err">${escapeHtml(e)}</p>`;
    setStatus(String(e), true);
  }
}

/**
 * Re-list sessions from the control owner.
 * @param {{ quiet?: boolean }} [opts]
 *   quiet — live poll: soft-fail, skip paint when catalog identity unchanged.
 */
async function refreshListFromServer(opts = {}) {
  const quiet = Boolean(opts.quiet);
  if (quiet) {
    const gen = ++listLiveGen;
    try {
      const needle = queryText();
      const listed = rpcPayload(
        await invoke("control_session_list", {
          ...(needle ? { query: needle } : {}),
          limit: needle ? 200 : 300,
        }),
      );
      if (gen !== listLiveGen) return;
      const rows = sessionRowsFromList(listed);
      if (!rows.length && allSessions.length) return;
      const prevSid = sessions[active]?.sessionId
        ? String(sessions[active].sessionId)
        : overviewSid;
      const prevSig = catalogSig(allSessions);
      allSessions = rows;
      const filterChanged = applySessionFilter({ render: false });
      if (prevSid) {
        const idx = sessions.findIndex((r) => String(r.sessionId || "") === prevSid);
        if (idx >= 0) active = idx;
      }
      if (catalogSig(allSessions) !== prevSig || filterChanged) {
        renderList({ scrollActive: false });
      }
    } catch {
      /* soft-fail live poll — do not blank the list */
    }
    return;
  }

  listLoading = true;
  renderList();
  try {
    const needle = queryText();
    // Server substring filter is the control contract for list discovery.
    const listed = rpcPayload(
      await invoke("control_session_list", {
        // omit query when empty — some IPC paths mishandle explicit null for Option
        ...(needle ? { query: needle } : {}),
        limit: needle ? 200 : 300,
      }),
    );
    const rows = sessionRowsFromList(listed);
    allSessions = rows;
    listLoading = false;
    applySessionFilter();
    const matched =
      listed && typeof listed === "object" && !Array.isArray(listed) && listed.matched != null
        ? Number(listed.matched)
        : allSessions.length;
    const total =
      listed && typeof listed === "object" && !Array.isArray(listed) && listed.total != null
        ? Number(listed.total)
        : allSessions.length;
    setStatus(
      needle
        ? `${sessions.length} shown · ${matched} matched · server`
        : `${allSessions.length} sessions · ready` +
            (total > allSessions.length ? ` (${total} total)` : ""),
    );
    if (!allSessions.length) {
      setStatus(
        needle
          ? `No matches for “${needle}”`
          : total > 0
            ? `List empty but server total=${total} (payload shape?)`
            : "No sessions from control (is groket serve running?)",
        !needle && total === 0,
      );
    }
    if (sessions.length) {
      // Keep a warm overview; only fetch when selection has no cache.
      scheduleOverview(false, 0);
    } else {
      overviewCache = null;
      overviewSid = "";
      overviewPendingSid = "";
      renderDetail();
    }
  } catch (e) {
    listLoading = false;
    // Keep prior catalog on transient RPC failure so a glitch is not a blank HUD.
    if (!allSessions.length) {
      sessions = [];
      renderList();
    } else {
      renderList();
    }
    setStatus(String(e), true);
    if (!allSessions.length) {
      detailEl.innerHTML = `<p class="err">${escapeHtml(e)}</p>
      <p class="muted">Run <code>groket serve start -d</code> then <code>groket hud --restart --rebuild</code>.</p>`;
    }
  }
}

async function selectSessionFromControl(sessionId, promptIndex = null) {
  const sid = String(sessionId || "").trim();
  if (!sid) return;

  let index = selectedSessionIndex(allSessions, sid);
  if (index < 0) {
    const listed = rpcPayload(
      await invoke("control_session_list", { query: sid, limit: 20 }),
    );
    const exact = sessionRowsFromList(listed).find(
      (row) => String(row.sessionId || "") === sid,
    );
    if (exact) {
      allSessions = [exact, ...allSessions.filter((row) => String(row.sessionId || "") !== sid)];
    }
  }

  if (q.value) q.value = "";
  applySessionFilter();
  index = selectedSessionIndex(sessions, sid);
  if (index < 0) {
    setStatus(`Selected session not found: ${sid}`, true);
    return;
  }
  active = index;
  renderList();
  await loadOverview(true);
  const eventIndex = turnEventIndexForPrompt(overviewCache, promptIndex);
  if (eventIndex != null) jumpToTimelineEvent(eventIndex);
}

/** Ignore blur→hide during show/focus handoff (macOS can emit a false blur). */
let suppressBlurHide = false;
let suppressBlurTimer = 0;
/** Debounced hide so a focus flicker does not cancel in-flight overview loads. */
let blurHideTimer = 0;

function focusSearchField() {
  try {
    q.focus({ preventScroll: true });
    q.select();
  } catch {
    q.focus();
    q.select();
  }
}

async function hidePalette() {
  window.clearTimeout(blurHideTimer);
  blurHideTimer = 0;
  paletteLive = false;
  stopLivePoll();
  try {
    await getCurrentWindow().hide();
  } catch {
    /* ignore */
  }
}

function armBlurSuppress(ms = 400) {
  suppressBlurHide = true;
  window.clearTimeout(suppressBlurTimer);
  window.clearTimeout(blurHideTimer);
  blurHideTimer = 0;
  suppressBlurTimer = window.setTimeout(() => {
    suppressBlurHide = false;
  }, ms);
}

function scheduleHideOnBlur() {
  if (suppressBlurHide) return;
  window.clearTimeout(blurHideTimer);
  // Longer delay + re-check focus: accessory activation can blip focus on show.
  blurHideTimer = window.setTimeout(() => {
    blurHideTimer = 0;
    if (suppressBlurHide) return;
    void (async () => {
      try {
        const focused = await getCurrentWindow().isFocused();
        if (focused) return;
      } catch {
        /* hide anyway if focus probe fails */
      }
      if (suppressBlurHide) return;
      void hidePalette();
    })();
  }, 280);
}

function cancelHideOnBlur() {
  window.clearTimeout(blurHideTimer);
  blurHideTimer = 0;
}

async function onPaletteShown() {
  // Sync first so a focus blip cannot hide before the async path runs.
  suppressBlurHide = true;
  armBlurSuppress(800);
  cancelHideOnBlur();
  paletteLive = true;
  // Claim the field immediately, then once more after layout so typing works.
  // Do not select() if that would re-filter mid-type; only select when empty.
  try {
    q.focus({ preventScroll: true });
    if (!q.value) q.select();
  } catch {
    q.focus();
  }
  requestAnimationFrame(() => {
    try {
      q.focus({ preventScroll: true });
    } catch {
      q.focus();
    }
  });
  void refreshListFromServer();
  // Start/resume live overview+timeline poll for running turns.
  armLivePoll(LIVE_POLL_MS);
}

async function boot() {
  const win = getCurrentWindow();
  detailEl.addEventListener("scroll", onDetailScroll, { passive: true });
  try {
    await win.onFocusChanged(({ payload: focused }) => {
      if (focused) {
        cancelHideOnBlur();
        // Do not steal focus from list/detail clicks — only palette-shown forces #q.
        return;
      }
      scheduleHideOnBlur();
    });
  } catch {
    // Older runtimes without onFocusChanged — Esc still hides.
  }
  try {
    await invoke("control_initialize");
    const path = await invoke("control_socket_path");
    setStatus(`ready · ${path.split("/").pop()}`);
  } catch (e) {
    setStatus(String(e), true);
  }
  await refreshListFromServer();
  const initial = await invoke("hud_initial_selection").catch(() => null);
  if (initial?.session) {
    await invoke("control_session_open", {
      session: String(initial.session),
      promptIndex: initial.promptIndex ?? null,
    });
    await selectSessionFromControl(initial.sessionId, initial.promptIndex ?? null);
  }
  focusSearchField();
  paletteLive = true;
  armLivePoll(LIVE_POLL_MS);
}

let debounce = 0;
q.addEventListener("input", () => {
  window.clearTimeout(debounce);
  debounce = window.setTimeout(() => {
    // Re-query the control owner so list search is not limited to a stale page.
    void refreshListFromServer();
  }, 120);
});

if (tlQ) {
  tlQ.addEventListener("input", () => {
    window.clearTimeout(tlSearchDebounce);
    tlSearchDebounce = window.setTimeout(() => {
      timelineQuery = tlQ.value;
      if (tab === "timeline") {
        detailEl.innerHTML = renderTimelineTab();
        // Pull remaining chunks so search is not stuck on the first load window.
        if ((timelineQuery || "").trim() && overviewSid && !timelineIsComplete()) {
          void ensureTimelineFilledForSearch(overviewSid);
        }
      }
    }, 40);
  });
  tlQ.addEventListener("keydown", (ev) => {
    // Keep / focus; Escape clears sub-search first, then hides palette.
    if (ev.key === "Escape" && tlQ.value) {
      ev.preventDefault();
      ev.stopPropagation();
      tlQ.value = "";
      timelineQuery = "";
      if (tab === "timeline") {
        detailEl.innerHTML = renderTimelineTab();
      }
    }
  });
}

tabsEl.addEventListener("click", (ev) => {
  const btn = ev.target.closest(".tab");
  if (!btn?.dataset.tab) return;
  setTab(btn.dataset.tab);
});

window.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") {
    if (tab === "timeline" && timelineQuery) {
      ev.preventDefault();
      timelineQuery = "";
      if (tlQ) tlQ.value = "";
      detailEl.innerHTML = renderTimelineTab();
      return;
    }
    ev.preventDefault();
    void hidePalette();
    return;
  }
  // `/` focuses timeline sub-search when Timeline is active (not while typing in list search).
  if (
    ev.key === "/" &&
    tab === "timeline" &&
    document.activeElement !== q &&
    document.activeElement !== tlQ
  ) {
    ev.preventDefault();
    if (tlQ) {
      syncTimelineSearchBar();
      tlQ.focus();
      tlQ.select();
    }
    return;
  }
  if (ev.key === "Tab" && !ev.metaKey && !ev.ctrlKey) {
    // cycle tabs
    const order = ["overview", "turns", "timeline", "findings", "notes"];
    const i = order.indexOf(tab);
    if (ev.shiftKey) {
      ev.preventDefault();
      setTab(order[(i - 1 + order.length) % order.length]);
    } else if (document.activeElement === q) {
      // let tab leave search into tabs only with shift? keep default for accessibility
    }
  }
  if (ev.key === "1" && (ev.metaKey || ev.ctrlKey)) {
    ev.preventDefault();
    setTab("overview");
    return;
  }
  if (ev.key === "2" && (ev.metaKey || ev.ctrlKey)) {
    ev.preventDefault();
    setTab("turns");
    return;
  }
  if (ev.key === "3" && (ev.metaKey || ev.ctrlKey)) {
    ev.preventDefault();
    setTab("timeline");
    return;
  }
  if (ev.key === "4" && (ev.metaKey || ev.ctrlKey)) {
    ev.preventDefault();
    setTab("findings");
    return;
  }
  if (ev.key === "5" && (ev.metaKey || ev.ctrlKey)) {
    ev.preventDefault();
    setTab("notes");
    return;
  }
  if (ev.key === "ArrowDown") {
    ev.preventDefault();
    if (!sessions.length) return;
    active = (active + 1) % sessions.length;
    renderList();
    scheduleOverview(true, 100);
    return;
  }
  if (ev.key === "ArrowUp") {
    ev.preventDefault();
    if (!sessions.length) return;
    active = (active - 1 + sessions.length) % sessions.length;
    renderList();
    scheduleOverview(true, 100);
    return;
  }
  if (ev.key === "Enter") {
    ev.preventDefault();
    scheduleOverview(true, 0);
  }
});

listen("palette-shown", () => {
  void onPaletteShown();
});

// Persistent control notify connection (Rust listener → session/notes/analysis).
listen("control-notify", (event) => {
  const payload = event?.payload;
  if (!payload || typeof payload !== "object") return;
  const method = String(payload.method || "");
  const params = payload.params && typeof payload.params === "object" ? payload.params : {};
  if (method === "session/selected") {
    void selectSessionFromControl(params.sessionId, params.promptIndex ?? null);
    return;
  }
  if (method === "session/changed") {
    void refreshListFromServer();
    const sid = String(params.sessionId || "").trim();
    if (sid && sid === overviewSid) {
      void loadOverview(false, { quiet: true, session: sid });
    }
    return;
  }
  if (method === "notes/changed" || method === "analysis/changed") {
    const sid = String(params.sessionId || "").trim();
    if (sid && sid === overviewSid) {
      // Force paint even if fingerprint logic is sticky (notes must show).
      overviewPaintFp = "";
      void loadOverview(false, { quiet: true, session: sid });
    }
  }
});

void boot();
