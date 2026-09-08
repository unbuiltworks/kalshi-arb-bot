"""PMUS SPORTS EVENT-PROGRESS TAPE -- a continuous, shadow-only record of the
venue's own game clock, independent of whether the campaign is selecting.

WHY THIS EXISTS (OWNER #167 5583906276 item 4, measured 2026-09-08 12:55Z).
Mapping B -- the translated sports state -- can only key a decision if some
observation of that event's progress exists at or before it. Today the ONLY
such observation is the probe's `selection` row, and under concurrency 1 a
held market suppresses selection entirely: from 12:03:19Z to 12:46:52Z every
campaign cycle logged CAMPAIGN_HOLD:POSITION_HELD and emitted no admitted
list at all. The result measured on the first forward cohort was 147 of 165
decisions unkeyable for want of any progress row, 120 of them on the single
market the campaign was holding -- i.e. we stop watching the clock during
exactly the periods that generate the most decisions.

There is a second, quieter defect the coverage number hides: even where a
selection row exists, mapping B keys the whole session off that ONE
selection-time reading, held constant. A frozen clock is the exact thing
mapping B was built to cure, so scoring it that way is not a fair test of it
either.

This module fixes both by observing the clock on its own cadence rather than
borrowing the selector's. It is deliberately NOT in the probe:

  * a gateway read per decision would put network latency on the live order
    path, and
  * fetching progress during a hold would change live campaign behaviour.

Neither is a change to make without an explicit owner go, and neither is
needed: the progress table is public, keyless and identical whoever reads it.

WHAT IT WRITES. Transitions only. A step function is fully reconstructible
from its steps, so a row is appended when a market's (period, proximity,
ended) changes, plus a periodic keepalive so a long unchanged run still
proves the observer was alive. The reader returns the same
slug -> [(t, {...})] shape as l5_sports_clock_study.selection_progress, so
the A/B consumes it with no change to how mapping B is scored.

NOT CLAIMED: this changes no gate, no cap, no price, no size and no live
decision. It is an observation channel. The pre-registered promotion gate
(sha 6a474b81b397c2bb) stands exactly as frozen -- widening coverage is how
that gate becomes reachable honestly, not a reason to lower it.

    python3 -m src.pmus_progress_tape --once
    python3 -m src.pmus_progress_tape --read      # reconstruct the series
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from src import pmus_event_progress as prog

# --------------------------------------------------------------------------

TAPE_DIR = Path(os.environ.get("PMUS_PROGRESS_TAPE_DIR",
                               "/data/l5_shadow/progress"))
TAPE_BUDGET_BYTES = int(os.environ.get("PMUS_PROGRESS_TAPE_BUDGET",
                                       str(128 * 1024 * 1024)))
TICK_S = float(os.environ.get("PMUS_PROGRESS_TICK_S", "60"))
# a market whose clock has not moved still gets a row this often, so a long
# unchanged run is distinguishable from an observer that stopped looking
KEEPALIVE_S = float(os.environ.get("PMUS_PROGRESS_KEEPALIVE_S", "900"))
STALE_TABLE_S = 1800.0

LABELS = ("venue-served game position, observed continuously and "
          "independently of campaign selection; NOT a settlement time")


def code_sha16() -> str:
    """Hash of THIS module's own source, printed on every boot row.

    Found the hard way on 2026-09-08: l5-shadow-loop kept running the build
    from before the log emission was added, because Railway evaluates watch
    patterns against the HEAD commit's diff and the change had landed in an
    intermediate commit of the same push. The service looked healthy and was
    silently producing the old output. A build identity on the boot row makes
    "running older code than you think" visible instead of invisible."""
    try:
        return hashlib.sha256(
            open(__file__, "rb").read()).hexdigest()[:16]
    except OSError:
        return "unreadable"


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# tape
# --------------------------------------------------------------------------

def tape_dir() -> Path:
    return Path(os.environ.get("PMUS_PROGRESS_TAPE_DIR", str(TAPE_DIR)))


def tape_path(t: Optional[float] = None) -> Path:
    t = time.time() if t is None else t
    hour = time.strftime("%Y%m%dT%H", time.gmtime(t))
    return tape_dir() / f"progress-{hour}.jsonl"


def tape_parts() -> List[Path]:
    d = tape_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("progress-*.jsonl") if p.is_file())


def sweep_tape(budget: int = TAPE_BUDGET_BYTES,
               now: Optional[float] = None) -> dict:
    """Oldest-first, and NEVER the part currently being written. An unbounded
    tape filled the recorder's volume at 00:40Z; a sweep that can delete the
    live part loses the newest evidence instead of the oldest."""
    parts = tape_parts()
    current = tape_path(now).name
    sizes = {}
    for p in parts:
        try:
            sizes[p] = p.stat().st_size
        except OSError:
            sizes[p] = 0
    total = sum(sizes.values())
    deleted: List[str] = []
    for p in parts:
        if total <= budget:
            break
        if p.name == current:
            continue
        try:
            p.unlink()
        except OSError:
            continue
        total -= sizes.get(p, 0)
        deleted.append(p.name)
    return {"kind": "progress_tape_retention", "parts": len(parts),
            "bytes": total, "budget": budget, "deleted": deleted}


def append(rows: Sequence[Mapping[str, Any]],
           now: Optional[float] = None) -> dict:
    """Fail-soft. A tape write that raises would end the observer, and an
    observer that dies to save a disk write is worse than a gap."""
    if not rows:
        return {"written": 0, "bytes": 0}
    path = tape_path(now)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
        with open(path, "at", encoding="utf-8") as fh:
            fh.write(blob)
        return {"written": len(rows), "bytes": len(blob)}
    except OSError as exc:
        print("PMUS_PROGRESS_ALARM " + json.dumps(
            {"alarm": "tape_write_failed", "path": str(path),
             "err": f"{type(exc).__name__}:{str(exc)[:120]}"}), flush=True)
        return {"written": 0, "bytes": 0,
                "error": f"{type(exc).__name__}:{str(exc)[:120]}"}


# --------------------------------------------------------------------------
# observation
# --------------------------------------------------------------------------

def _fingerprint(entry: Mapping[str, Any]) -> tuple:
    return (entry.get("period"), _f(entry.get("game_proximity")),
            bool(entry.get("ended")))


def changed_rows(tbl: Mapping[str, Mapping[str, Any]],
                 last: Dict[str, tuple], now: float,
                 keepalive_s: float = KEEPALIVE_S) -> List[dict]:
    """A row per market whose clock moved, plus keepalives. `last` is mutated
    in place so the caller keeps state across ticks and across restarts."""
    out: List[dict] = []
    for slug, entry in sorted(tbl.items()):
        fp = _fingerprint(entry)
        prev = last.get(slug)
        moved = prev is None or prev[0] != fp
        stale = prev is not None and (now - prev[1]) >= keepalive_s
        if not (moved or stale):
            continue
        last[slug] = (fp, now)
        out.append({
            "kind": "progress", "venue": "polymarket_us", "t": round(now, 3),
            "slug": slug, "event_slug": entry.get("event_slug"),
            "sport_type": entry.get("sport_type"), "period": entry.get("period"),
            "clock": entry.get("clock"), "score": entry.get("score"),
            "ended": bool(entry.get("ended")),
            "game_proximity": _f(entry.get("game_proximity")),
            "proximity_basis": entry.get("proximity_basis"),
            "hours_since_event_start": entry.get("hours_since_event_start"),
            "reason": "moved" if moved else "keepalive",
            "labels": LABELS,
        })
    return out


def seed_last_from_tape(parts: Optional[Sequence[Path]] = None
                        ) -> Dict[str, tuple]:
    """Restore the newest fingerprint per market from the tape so a restart
    does not re-append rows the tape already holds. Collection has to be
    idempotent across restarts, not only within one process."""
    last: Dict[str, tuple] = {}
    for path in (tape_parts() if parts is None else parts):
        try:
            with open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    if '"progress"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("kind") != "progress":
                        continue
                    slug, t = e.get("slug"), _f(e.get("t"))
                    if not slug or t is None:
                        continue
                    prev = last.get(str(slug))
                    if prev is None or t > prev[1]:
                        last[str(slug)] = (_fingerprint(e), t)
        except OSError:
            continue
    return last


def tick(last: Dict[str, tuple], now: Optional[float] = None,
         get_json: Optional[Callable] = None) -> dict:
    """One observation. Never raises for a venue hiccup: an empty table is a
    visible outcome, not an exception."""
    now = time.time() if now is None else now
    # deliberately NOT prog.table(): that memoises behind a module-level TTL
    # cache shared with the live decision path. An observer whose whole job
    # is to see every change must not be served a remembered answer, and it
    # must not warm a cache the probe then reads.
    try:
        rows_in, diag = prog.fetch_live_events(get_json)
        tbl = prog.progress_table(rows_in, now)
    except Exception as exc:                         # noqa: BLE001
        tbl, diag = {}, {"error": f"{type(exc).__name__}:{str(exc)[:100]}"}
    rows = changed_rows(tbl, last, now)
    wrote = append(rows, now)
    return {"kind": "progress_tick", "markets": len(tbl),
            "with_proximity": sum(1 for v in tbl.values()
                                  if v.get("game_proximity") is not None),
            "rows": len(rows), **wrote,
            "moved": sum(1 for r in rows if r["reason"] == "moved"),
            "emit": emit_rows(rows),
            "diag_error": (diag or {}).get("error")}


EMIT_MAX = int(os.environ.get("PMUS_PROGRESS_EMIT_MAX", "40"))


def emit_rows(rows: Sequence[Mapping[str, Any]],
              cap: int = EMIT_MAX) -> List[list]:
    """The tape is durable on the service's own volume, which the analysis
    host cannot read. So the transitions that mapping B can actually use --
    a MOVE with a recognised proximity -- also go out on the log stream,
    where the existing harvest already reaches, as fixed-position tuples
    [t, slug, proximity, period]. Positional and capped on purpose: the
    probe's 06:30Z exit dropped 117,262 messages by printing a document.

    Keepalives are deliberately not emitted -- the tick summary already
    proves the observer was looking, and a keepalive carries no transition
    to reconstruct."""
    out: List[list] = []
    for r in rows:
        if len(out) >= cap:
            break
        if r.get("reason") != "moved" or r.get("game_proximity") is None:
            continue
        out.append([round(float(r["t"]), 1), r["slug"],
                    r["game_proximity"], r.get("period")])
    return out


def progress_from_logs(paths: Sequence[Any]) -> Dict[str, list]:
    """Reconstruct the series from harvested PMUS_PROGRESS log rows -- the
    same shape load_progress and selection_progress return, so the A/B
    cannot tell which witness a row came from."""
    out: Dict[str, list] = collections.defaultdict(list)
    for path in paths:
        try:
            blob = open(path, "rt", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if "PMUS_PROGRESS " not in blob:
            continue
        for chunk in blob.split("PMUS_PROGRESS ")[1:]:
            depth, end = 0, None
            for i, ch in enumerate(chunk):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end is None:
                continue
            try:
                row = json.loads(chunk[:end].replace('\\"', '"'))
            except ValueError:
                continue
            for e in (row.get("emit") or []):
                if not isinstance(e, list) or len(e) < 3:
                    continue
                t, slug, prox = _f(e[0]), e[1], _f(e[2])
                if t is None or not slug:
                    continue
                out[str(slug)].append((t, {
                    "period": e[3] if len(e) > 3 else None,
                    "proximity": prox, "basis": "progress_tape_log",
                    "clock": None, "source": "progress_tape_log"}))
    return {k: sorted(v, key=lambda x: x[0]) for k, v in out.items()}


# --------------------------------------------------------------------------
# reader -- the shape l5_sports_clock_study.selection_progress returns
# --------------------------------------------------------------------------

def load_progress(parts: Optional[Sequence[Path]] = None
                  ) -> Dict[str, list]:
    """slug -> [(t_epoch, {period, proximity, basis, clock})], ascending.

    Deliberately the SAME shape selection_progress returns, so the A/B keys
    mapping B off a real time series without changing how it is scored."""
    out: Dict[str, list] = collections.defaultdict(list)
    for path in (tape_parts() if parts is None else parts):
        try:
            with open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    if '"progress"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("kind") != "progress":
                        continue
                    slug, t = e.get("slug"), _f(e.get("t"))
                    if not slug or t is None:
                        continue
                    out[str(slug)].append((t, {
                        "period": e.get("period"),
                        "proximity": _f(e.get("game_proximity")),
                        "basis": e.get("proximity_basis"),
                        "clock": e.get("clock"),
                        "source": "progress_tape",
                    }))
        except OSError:
            continue
    return {k: sorted(v, key=lambda x: x[0]) for k, v in out.items()}


def merge_progress(*series: Mapping[str, list]) -> Dict[str, list]:
    """Union of progress series by slug, time-ordered. Used to score mapping
    B off the tape AND the historical selection rows without discarding the
    older evidence the selector already gave us."""
    out: Dict[str, list] = collections.defaultdict(list)
    for s in series:
        for slug, rows in (s or {}).items():
            out[str(slug)].extend(rows)
    return {k: sorted(v, key=lambda x: x[0]) for k, v in out.items()}


# --------------------------------------------------------------------------
# loop hook + cli
# --------------------------------------------------------------------------

class Observer:
    """Owns the cross-tick state and its own cadence, so a host loop on a
    different interval can call `maybe_tick` as often as it likes."""

    def __init__(self, tick_s: float = TICK_S,
                 clock: Callable[[], float] = time.time,
                 get_json: Optional[Callable] = None) -> None:
        self.tick_s = float(tick_s)
        self.clock = clock
        self.get_json = get_json
        self.last: Dict[str, tuple] = seed_last_from_tape()
        self.next_at = 0.0
        self.ticks = 0
        self.last_seen_t: Optional[float] = None
        self.last_alarm_t = 0.0

    def maybe_tick(self, now: Optional[float] = None) -> Optional[dict]:
        now = self.clock() if now is None else now
        if now < self.next_at:
            return None
        self.next_at = now + self.tick_s
        self.ticks += 1
        row = tick(self.last, now=now, get_json=self.get_json)
        if row["markets"]:
            self.last_seen_t = now
        elif (self.last_seen_t is not None
              and now - self.last_seen_t >= STALE_TABLE_S
              and now - self.last_alarm_t >= STALE_TABLE_S):
            self.last_alarm_t = now
            print("PMUS_PROGRESS_ALARM " + json.dumps(
                {"alarm": "no_live_events", "since_s": round(now - self.last_seen_t, 1),
                 "evidence": "an observer seeing nothing is not the same as "
                             "a quiet board; say so rather than sit silent"}),
                  flush=True)
        if self.ticks % 15 == 1:
            sweep_tape(now=now)
        return row


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--read", action="store_true",
                    help="reconstruct and summarise the series on the tape")
    ap.add_argument("--tick-s", type=float, default=TICK_S)
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.read:
        series = load_progress()
        print(json.dumps({"kind": "progress_tape_read", "slugs": len(series),
                          "rows": sum(len(v) for v in series.values()),
                          "with_proximity": sum(
                              1 for v in series.values()
                              if any(r[1]["proximity"] is not None for r in v)),
                          "parts": [p.name for p in tape_parts()]},
                         sort_keys=True))
        return 0

    obs = Observer(tick_s=args.tick_s)
    print("PMUS_PROGRESS_BOOT " + json.dumps(
        {"tape_dir": str(tape_dir()), "tick_s": obs.tick_s,
         "keepalive_s": KEEPALIVE_S, "budget": TAPE_BUDGET_BYTES,
         "seeded_slugs": len(obs.last), "read_only": True,
         "code_sha": code_sha16(), "emits_rows": True,
         "labels": LABELS}, sort_keys=True), flush=True)
    while True:
        row = obs.maybe_tick()
        if row is not None:
            print("PMUS_PROGRESS " + json.dumps(row, sort_keys=True), flush=True)
        if args.once:
            return 0
        time.sleep(1.0)


if __name__ == "__main__":                       # pragma: no cover
    sys.exit(main())
