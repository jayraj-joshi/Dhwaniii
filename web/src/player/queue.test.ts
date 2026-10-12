import { describe, expect, it } from "vitest";
import type { Ctx, PlaybackState } from "../types";
import * as q from "./queue";

const ctx: Ctx = { type: "playlist", id: "1", name: "Road trip" };
const artistOf = (id: number) => (id < 100 ? "a" : "b");
const ids = (n: number, from = 1) => Array.from({ length: n }, (_, i) => from + i);
const start = (list = ids(4), at = 0, base = q.EMPTY) => q.playContext(base, ctx, list, at, artistOf);
const cur = (s: ReturnType<typeof start>) => s.current?.track_id;

describe("queue", () => {
  it("plays a context in order and stops at the end", () => {
    let s = start();
    expect(cur(s)).toBe(1);
    for (const want of [2, 3, 4]) { s = q.next(s, {}, artistOf)!; expect(cur(s)).toBe(want); }
    expect(q.next(s, {}, artistOf)).toBeNull();
  });

  it("plays the user's queue first and then resumes the context where it was", () => {
    let s = q.addToQueue(q.playNext(start(), [50]), [60]);
    expect(s.user_queue).toEqual([50, 60]);
    s = q.next(s, {}, artistOf)!;
    expect([cur(s), s.current?.from, s.context_pos]).toEqual([50, "user", 0]);
    s = q.next(q.next(s, {}, artistOf)!, {}, artistOf)!;
    expect([cur(s), s.current?.from]).toEqual([2, "context"]);
  });

  it("keeps the user's queue when a new context starts", () => {
    const s = start(ids(3, 10), 1, q.playNext(start(), [50]));
    expect([cur(s), s.user_queue, s.context_pos]).toEqual([11, [50], 1]);
  });

  it("repeat one restarts on auto-advance only; repeat all wraps", () => {
    const one = { ...start(), repeat: "one" as const };
    expect(cur(q.next(one, { auto: true }, artistOf)!)).toBe(1);
    expect(cur(q.next(one, {}, artistOf)!)).toBe(2);
    let all: PlaybackState = { ...start(ids(2)), repeat: "all" };
    all = q.next(all, {}, artistOf)!;
    expect(cur(q.next(all, {}, artistOf)!)).toBe(1);
  });

  it("previous goes back one track, or restarts at the first", () => {
    let s = q.next(start(), {}, artistOf)!;
    expect(cur(q.previous(s))).toBe(1);
    s = q.previous(q.previous(s));
    expect([cur(s), s.context_pos]).toEqual([1, 0]);
  });

  it("jumpTo drops earlier user items; remove only touches upcoming items", () => {
    const s = q.addToQueue(start(), [50, 60, 70]);
    const j = q.jumpTo(s, "user", 1);
    expect([cur(j), j.user_queue]).toEqual([60, [70]]);
    expect(cur(q.jumpTo(s, "context", 3))).toBe(4);
    expect(q.remove(s, "context", 0)).toBe(s);
    expect(q.view(q.remove(s, "context", 2)).context.map((c) => c.id)).toEqual([2, 4]);
  });

  it("shuffle puts the current track first and toggling off restores its place", () => {
    let s = q.next(start(ids(8)), {}, artistOf)!; // playing track 2
    s = q.toggleShuffle(s, artistOf);
    expect([cur(s), s.context_pos, s.order[0]]).toEqual([2, 0, 1]);
    expect([...s.order].sort((a, b) => a - b)).toEqual(ids(8, 0));
    s = q.toggleShuffle(s, artistOf);
    expect([s.order, s.context_pos]).toEqual([ids(8, 0), 1]);
  });

  it("smart shuffle is a permutation and avoids neighbours by the same artist when it can", () => {
    const items = [...ids(5), ...ids(5, 100)];
    for (let i = 0; i < 50; i++) {
      const out = q.smartShuffle(items, artistOf);
      expect([...out].sort((a, b) => a - b)).toEqual(items);
      expect(out.some((x, k) => k > 0 && artistOf(x) === artistOf(out[k - 1]))).toBe(false);
    }
    const same = ids(6);
    expect([...q.smartShuffle(same, artistOf)].sort((a, b) => a - b)).toEqual(same);
  });

  it("extendContext appends unseen ids without reordering, even with shuffle on", () => {
    let s = q.toggleShuffle(start(ids(3)), artistOf);
    const before = s.order;
    s = q.extendContext(s, [3, 7, 8]);
    expect(s.context_tracks).toEqual([1, 2, 3, 7, 8]);
    expect(s.order).toEqual([...before, 3, 4]);
    expect(q.isLastInContext(s)).toBe(false);
  });

  it("peekNext lists the user's queue, then the context", () => {
    expect(q.peekNext(q.playNext(start(), [50]), 2)).toEqual([50, 2]);
  });
});
