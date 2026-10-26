"""Run the joined BTC15m collector continuously. Read-only, self-recovering.

One window at a time: subscribe before it opens, watch it through, write a
self-contained record to the volume, move to the next. Never places, previews
or cancels anything -- there is no object in this process that could.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import traceback
from typing import Any, Dict, List, Optional, Set

from src import l5_window_actions as WA
from src import l5_window_collector as C

ROOT_ENV = "L5_COLLECTOR_ROOT"
LEAD_ENV = "L5_COLLECTOR_LEAD_S"
POLL_ENV = "L5_COLLECTOR_POLL_S"
ONCE_ENV = "L5_COLLECTOR_ONCE"

DEFAULT_ROOT = "/state/l5-collector"
DEFAULT_LEAD_S = 75.0
DEFAULT_POLL_S = 3.0


def _f(env: str, default: float) -> float:
    try:
        return float(str(os.getenv(env, "")).strip() or default)
    except ValueError:
        return default


def root() -> str:
    return os.getenv(ROOT_ENV, "").strip() or DEFAULT_ROOT


def say(kind: str, payload: Dict[str, Any]) -> None:
    print("%s %s" % (kind, json.dumps(payload, default=str)), flush=True)


def next_window(now: float, lead_s: float):
    """The window we should be subscribing to, and when it opens."""
    slug = WA.current_slug(now)
    start = WA.window_start(slug)
    if start is None:
        return None, None
    # if the current window is already too far gone to observe properly,
    # aim at the next one instead of recording a stub
    if now > start + (C.WINDOW_S - lead_s):
        slug = WA.current_slug(start + C.WINDOW_S + 1.0)
        start = WA.window_start(slug)
    return slug, start


def collect_one(slug: str, start: float, *, clock=time.time,
                sleep=time.sleep, activity=None,
                feed_factory=None) -> Dict[str, Any]:
    end = start + C.WINDOW_S
    rec = C.WindowRecord(root(), slug, start)
    cov = C.Coverage(start, end)
    feed = (feed_factory(slug, rec.open_raw()) if feed_factory
            else C.RawTapeFeed(slug, rec.open_raw(), clock=clock))
    health = WA.FeedHealth()
    seen: Set[str] = set()
    actions: List[dict] = []
    polls = 0
    polls_ok = 0
    feed_ok_last = False

    say("L5_COLLECTOR_WINDOW_OPEN", {
        "market": slug, "window_start_utc":
            dt.datetime.utcfromtimestamp(start).isoformat() + "Z",
        "lead_s": round(start - clock(), 1), "root": root(),
        # LOUD, AND AT THE TOP OF THE WINDOW. A full volume used to be a
        # five-second traceback with no window line at all; it is now a
        # window that opens, says it cannot keep a tape, and still polls.
        "storage": rec.storage()})
    feed.start(cov)
    try:
        while clock() < end:
            if clock() >= start:
                polls += 1
                try:
                    new, ok = WA.new_l5_actions(slug, seen, activity,
                                                health=health)
                except Exception as exc:                   # noqa: BLE001
                    new, ok = [], False
                    health.last_error = "%s: %s" % (type(exc).__name__,
                                                    str(exc)[:120])
                feed_ok_last = bool(ok)
                if ok:
                    polls_ok += 1
                for a in new or []:
                    s = feed.market_state()
                    a = dict(a)
                    a["collector"] = {
                        "receipt_t": clock(),
                        "receipt_utc": dt.datetime.now(
                            dt.timezone.utc).isoformat(),
                        "bbo_at_receipt": {
                            "bid": s.get("bid"), "ask": s.get("ask"),
                            "bid_qty": s.get("bid_qty"),
                            "ask_qty": s.get("ask_qty"),
                            "state": s.get("state")},
                        "tte_s_at_receipt": round(end - clock(), 3),
                        "feed_connected": bool(feed.connected),
                        "prints_so_far": len(feed.prints()),
                    }
                    actions.append(a)
            sleep(_f(POLL_ENV, DEFAULT_POLL_S))
    finally:
        feed.stop()
        prints = feed.prints()
        books = list(feed.book_states)
        rec.close_raw()

    coverage = cov.as_dict()
    elig = C.primary_eligible(coverage, len(prints), feed_ok_last or polls_ok > 0)
    doc = {
        "schema": "l5_window_collector/v1",
        "market": slug,
        "window_start_utc": dt.datetime.utcfromtimestamp(start).isoformat() + "Z",
        "window_start_epoch": start,
        "window_end_epoch": end,
        "written_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "coverage": coverage,
        "eligibility": elig,
        "l5": {"polls": polls, "polls_ok": polls_ok,
               "actions": len(actions),
               "health": health.as_dict() if hasattr(health, "as_dict") else {},
               "feed_ok_last": feed_ok_last},
        "pmus": {"prints": len(prints), "book_states": len(books),
                 "raw_frames": rec.raw_frames,
                 "feed_errors": feed.errors[-3:],
                 "stop_reason": feed.stop_reason},
        # WHETHER THE VOLUME KEPT ANY OF THIS. A degraded window polled the
        # L5 feed exactly as a healthy one does and kept no tape, so the
        # two must never be told apart by the absence of frames alone.
        "storage": rec.storage(),
        "actions": actions,
        "prints": prints,
        "book_states": books,
        "provenance": {
            "source": "authenticated websocket, recorder primitives "
                      "(WS_URL, subscribe_frames, guard_outbound, "
                      "apply_events); L5 actions via l5_window_actions."
                      "new_l5_actions with its market-identity gate",
            "raw_frames_file": str(rec.raw_path),
            "read_only": True,
        },
        "labels": ("an observed trade is not a fill. Coverage is reported "
                   "before anything else so missing tape can never be read "
                   "as an absence of trading."),
    }
    path = rec.write(doc)
    say("L5_COLLECTOR_WINDOW", {
        "market": slug, "path": path,
        "observed_fraction": coverage["observed_fraction"],
        "max_gap_s": coverage["max_gap_s"],
        "prints": len(prints), "book_states": len(books),
        "raw_frames": rec.raw_frames,
        "storage_degraded": rec.degraded,
        "storage_degraded_reason": rec.degraded_reason,
        "l5_actions": len(actions), "l5_polls_ok": polls_ok,
        "l5_polls": polls,
        "l5_feed_health": (health.as_dict()
                           if hasattr(health, "as_dict") else {}),
        "feed_connected_ever": coverage["frames"] > 0,
        "feed_errors": feed.errors[-2:],
        "primary": elig["primary"], "excluded_for": elig["excluded_for"]})
    return doc


def main(argv: Optional[List[str]] = None) -> int:
    say("L5_COLLECTOR_MODE", {
        "role": "READ_ONLY_JOINED_WINDOW_COLLECTOR",
        "root": root(),
        "lead_s": _f(LEAD_ENV, DEFAULT_LEAD_S),
        "poll_s": _f(POLL_ENV, DEFAULT_POLL_S),
        "once": bool(os.getenv(ONCE_ENV, "").strip()),
        "capability": "no order, no preview, no cancel, no account mutation",
    })
    # the boundary first, before this process creates anything of its own
    try:
        prior = os.getenv("L5_PRIOR_ACTION_COUNT", "").strip()
        snap = C.snapshot_pre_cutover(
            root(), note=os.getenv("L5_CUTOVER_NOTE", "").strip(),
            action_count=int(prior) if prior.isdigit() else None)
        say("L5_COLLECTOR_PRE_CUTOVER", {
            "file_count": snap.get("file_count"),
            "total_bytes": snap.get("total_bytes"),
            "taken_utc": snap.get("taken_utc"),
            "prior_collector_action_count":
                snap.get("prior_collector_action_count"),
            "reused_existing_manifest": snap.get("reused_existing_manifest")})
        say("L5_COLLECTOR_EVIDENCE_INTACT", C.verify_pre_cutover(root()))
    except BaseException as exc:                           # noqa: BLE001
        say("L5_COLLECTOR_SNAPSHOT_FAILED", {
            "error": type(exc).__name__, "detail": str(exc)[:200]})

    missing = C.missing_feed_credentials()
    if missing:
        say("L5_COLLECTOR_CREDENTIALS_MISSING", {
            "missing": missing,
            "effect": "the authenticated feed cannot connect; L5 actions "
                      "will still be collected and every window will be "
                      "excluded FEED_NEVER_CONNECTED rather than look quiet",
            "labels": "a half-collecting collector says so on every boot"})

    once = bool(os.getenv(ONCE_ENV, "").strip())
    while True:
        try:
            lead = _f(LEAD_ENV, DEFAULT_LEAD_S)
            slug, start = next_window(time.time(), lead)
            if slug is None or start is None:
                say("L5_COLLECTOR_IDLE", {"reason": "WINDOW_NOT_ESTABLISHED"})
                time.sleep(15.0)
                continue
            wait = (start - lead) - time.time()
            if wait > 0:
                time.sleep(min(wait, 600.0))
                continue
            # BOUND THE VOLUME BEFORE OPENING THE NEXT WINDOW.
            #
            # There was no retention at all: one directory per 15-minute
            # window, written forever, and when the 50 GB volume filled on
            # 2026-10-08 the collector raised ENOSPC out of the first
            # statement of `collect_one` every five seconds for 17 days
            # without ever polling the L5 feed. Pruning here -- before the
            # record is created, never during -- is what stops that
            # recurring. The live window is protected by name so the tape
            # it is about to open cannot be deleted underneath it.
            try:
                pr = C.prune_windows(root(), keep_slugs=[slug])
                if (pr.get("pruned_count") or pr.get("failed_slugs")
                        or pr.get("floor_unreachable") or pr.get("error")):
                    say("L5_COLLECTOR_RETENTION", pr)
            except BaseException as exc:                   # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                # retention is housekeeping. It must never be the reason a
                # window goes uncollected -- that is the failure mode this
                # whole change exists to remove.
                say("L5_COLLECTOR_RETENTION", {
                    "error": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                    "effect": "not pruned; collection continues"})
            collect_one(slug, start)
        except KeyboardInterrupt:
            return 0
        except BaseException as exc:                       # noqa: BLE001
            say("L5_COLLECTOR_ERROR", {
                "error": type(exc).__name__, "detail": str(exc)[:300],
                "trace": traceback.format_exc()[-800:]})
            time.sleep(5.0)                # self-recovering, never exits
        if once:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
