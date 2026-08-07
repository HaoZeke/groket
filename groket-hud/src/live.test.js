/**
 * Node test runner: ``node --test src/live.test.js`` (from groket-hud/).
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  centeredScrollTop,
  eventFingerprint,
  hasOpenTurn,
  isLiveStatus,
  latestSingleFlight,
  mergeTimelineByIndex,
  overviewPaintFingerprint,
  patchListRowFromMeta,
  selectedSessionIndex,
  sessionNeedsLivePoll,
  shouldAutoFollowTimeline,
  timelineSeekOffset,
  turnEventIndexForPrompt,
} from "./live.js";

describe("latestSingleFlight", () => {
  it("serializes work and keeps the newest queued arguments", async () => {
    const releases = [];
    const calls = [];
    let active = 0;
    let maxActive = 0;
    const run = latestSingleFlight(async (value) => {
      calls.push(value);
      active += 1;
      maxActive = Math.max(maxActive, active);
      await new Promise((resolve) => releases.push(resolve));
      active -= 1;
      return value;
    });

    const first = run("first");
    const second = run("second");
    const third = run("third");
    assert.equal(first, second);
    assert.equal(second, third);
    assert.deepEqual(calls, ["first"]);

    releases.shift()();
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(calls, ["first", "third"]);
    releases.shift()();
    assert.equal(await first, "third");
    assert.equal(maxActive, 1);
  });
});

describe("isLiveStatus", () => {
  it("accepts control list labels", () => {
    assert.equal(isLiveStatus("running"), true);
    assert.equal(isLiveStatus("awaiting"), true);
    assert.equal(isLiveStatus("awaiting_follow_up"), true);
    assert.equal(isLiveStatus("ending"), true);
    assert.equal(isLiveStatus("complete"), false);
    assert.equal(isLiveStatus("cancelled"), false);
    assert.equal(isLiveStatus("—"), false);
    assert.equal(isLiveStatus(""), false);
  });
});

describe("sessionNeedsLivePoll", () => {
  it("polls when open turn even if status is complete-ish", () => {
    assert.equal(sessionNeedsLivePoll("complete", { turns: [{ open: true }] }), true);
    assert.equal(sessionNeedsLivePoll("complete", { turns: [{ open: false }] }), false);
    assert.equal(hasOpenTurn({ turns: [{ open: true }] }), true);
  });
});

describe("mergeTimelineByIndex", () => {
  it("appends new indices and skips identical updates", () => {
    const a = [
      { index: 0, content: "a" },
      { index: 1, content: "b" },
    ];
    const noop = mergeTimelineByIndex(a, [
      { index: 0, content: "a" },
      { index: 1, content: "b" },
    ]);
    assert.equal(noop.added, 0);
    assert.equal(noop.updated, 0);
    assert.equal(noop.changedIndices.length, 0);

    const r = mergeTimelineByIndex(a, [
      { index: 1, content: "b2" },
      { index: 2, content: "c" },
    ]);
    assert.equal(r.added, 1);
    assert.equal(r.updated, 1);
    assert.equal(r.events.length, 3);
    assert.equal(r.events[1].content, "b2");
    assert.equal(r.events[2].content, "c");
    assert.deepEqual(r.changedIndices, [1, 2]);
  });
});

describe("eventFingerprint", () => {
  it("changes when content grows", () => {
    const a = eventFingerprint({ index: 1, content: "hi" });
    const b = eventFingerprint({ index: 1, content: "hi there" });
    assert.notEqual(a, b);
  });
});

describe("shouldAutoFollowTimeline", () => {
  it("follows when near bottom", () => {
    assert.equal(shouldAutoFollowTimeline(900, 1000, 100, 120), true);
    assert.equal(shouldAutoFollowTimeline(0, 1000, 100, 120), false);
  });
});

describe("centeredScrollTop", () => {
  it("centers a child already partially scrolled into view", () => {
    // scroller viewport at y=100 height 400; child on screen at y=300 height 40
    // content offset of child = 200 + (300-100) = 400; center → 400 - 200 + 20 = 220
    assert.equal(centeredScrollTop(200, 100, 400, 300, 40), 220);
  });

  it("floors at zero for a child above the viewport", () => {
    assert.equal(centeredScrollTop(0, 100, 400, 50, 20), 0);
  });

  it("matches seek pad math for early indices", () => {
    assert.equal(timelineSeekOffset(10, 20), 0);
    assert.equal(timelineSeekOffset(100, 20), 80);
  });
});

describe("patchListRowFromMeta", () => {
  it("listPaint only for chrome fields", () => {
    const rows = [{ sessionId: "s1", status: "running", numEvents: 1 }];
    const silent = patchListRowFromMeta(rows, "s1", { numEvents: 2 });
    assert.equal(silent.changed, true);
    assert.equal(silent.listPaint, false);
    const paint = patchListRowFromMeta(rows, "s1", { status: "complete", title: "T" });
    assert.equal(paint.listPaint, true);
    assert.equal(rows[0].status, "complete");
    assert.equal(rows[0].title, "T");
  });
});

describe("timelineSeekOffset", () => {
  it("pads before focus and floors at zero", () => {
    assert.equal(timelineSeekOffset(100, 20), 80);
    assert.equal(timelineSeekOffset(5, 20), 0);
    assert.equal(timelineSeekOffset(-1, 20), 0);
  });
});

describe("external session selection", () => {
  it("finds the selected row and prompt event", () => {
    const rows = [{ sessionId: "a" }, { sessionId: "target" }];
    const overview = {
      turns: {
        turns: [
          { promptIndex: 3, userEventIndex: 40 },
          { promptIndex: 9, userEventIndex: 120 },
        ],
      },
    };
    assert.equal(selectedSessionIndex(rows, "target"), 1);
    assert.equal(turnEventIndexForPrompt(overview, 9), 120);
    assert.equal(turnEventIndexForPrompt(overview, 8), null);
  });
});

describe("overviewPaintFingerprint", () => {
  it("stable when only duration would tick", () => {
    const a = overviewPaintFingerprint({
      sessionId: "s",
      meta: { status: "running", numEvents: 3, duration: "1s" },
      turns: { total: 1, turns: [{ open: true, summary: "x" }] },
      notes: { count: 0 },
      findings: { total: 0 },
      summary: "hi",
    });
    const b = overviewPaintFingerprint({
      sessionId: "s",
      meta: { status: "running", numEvents: 3, duration: "2s" },
      turns: { total: 1, turns: [{ open: true, summary: "x" }] },
      notes: { count: 0 },
      findings: { total: 0 },
      summary: "hi",
    });
    assert.equal(a, b);
    const c = overviewPaintFingerprint({
      sessionId: "s",
      meta: { status: "running", numEvents: 4, duration: "2s" },
      turns: { total: 1, turns: [{ open: true, summary: "x" }] },
      notes: { count: 0 },
      findings: { total: 0 },
      summary: "hi",
    });
    assert.notEqual(a, c);
  });
});
