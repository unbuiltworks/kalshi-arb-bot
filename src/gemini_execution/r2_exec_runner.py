"""The continuously running R2 execution-validation runner.

WHAT IT DOES, and nothing more:

  discover -> wait for strike -> observe frozen R2 -> FIRST qualifying
  opportunity -> freeze price/depth -> immediate re-read -> durable slot
  -> q=1 submission -> reconcile -> finish market -> next DISTINCT market

IT IS NOT A TRADING ENGINE. It owns no signal logic, no budget arithmetic, no
HTTP call, no signing and no payload construction. Everything is delegated:

  qualification      analysis/gemini/info_edge/r2_frozen.py  (IMPORTED by
                     r2_exec_probe; semantic reference 6bc5c1f7, executable
                     fingerprint 69adbd18bd12...)
  one probe          r2_exec_probe.probe()        -- the only venue-order path
  budget and slots   r2_exec_budget               -- 10 slots, $8 stop, expiry
  HALT               semantics_probe.halted()/halt()
  authentication     gemini_prediction_auth, via transport
  discovery / book   metadata (public REST) and the proven Coinbase feed from
                     analysis/gemini/info_edge/ie_collector.py

FIRST QUALIFYING OPPORTUNITY WINS, AND THE MARKET IS THEN DONE. A market may
produce at most one SUBMITTED or one MISSED. The runner never waits for a
"better" R2 state later in the same window -- that would be selecting on the
very thing being measured.

DUPLICATE SUBMISSION IS STRUCTURALLY IMPOSSIBLE ACROSS A KILL. The slot is
consumed and fsynced, and the market marked SUBMITTED, BEFORE the venue is
touched. A process killed between that fsync and the venue's response restarts
to find the slot spent and the market finished, so `may_probe` refuses it.
There is no release path. The cost of a kill is one wasted slot, never a
second order.
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.gemini_execution import (arming, metadata, payload,
                                  r2_exec_budget as BUDGET,
                                  r2_exec_probe as PROBE, transport)
from src.gemini_execution.semantics_probe import halted

#: the proven Coinbase feed, reused rather than rewritten.
_IEC = Path("analysis/gemini/info_edge/ie_collector.py")
IEC: Dict[str, Any] = {"__file__": str(_IEC), "__name__": "ie_collector"}

BTC5M = re.compile(r"^BTC05M\d{10}$")

#: how often to re-read the slate and the qualifying state. The frozen rule's
#: own observation grid is 5..240 s, so a 5 s cadence cannot skip a window.
POLL_S = 5.0

#: Coinbase ticks kept in memory. 300 s of volatility lookback at ~5 trades/s
#: needs far fewer than this; the margin is for bursts.
TICK_CAP = 20000


def _load_iec() -> None:
    if "coinbase_task" in IEC:
        return
    src = _IEC.read_text()
    exec(src.split("async def main_async")[0], IEC)


class Ticks:
    """A bounded in-memory Coinbase tape, shaped exactly as the frozen rule's
    `sigma_1s_from_ticks` expects: a time-sorted list of (seconds, price)."""

    def __init__(self) -> None:
        self.rows: List[Tuple[float, float]] = []

    def write(self, row: Dict[str, Any]) -> None:       # Sink-compatible
        if row.get("kind") != "cb_match":
            return
        try:
            t = row["t_ns"] / 1e9
            px = float(row["price"])
        except Exception:                                      # noqa: BLE001
            return
        self.rows.append((t, px))
        if len(self.rows) > TICK_CAP:
            del self.rows[: len(self.rows) - TICK_CAP]

    def close(self) -> None:
        pass

    def snapshot(self) -> List[Tuple[float, float]]:
        return list(self.rows)

    def fresh(self, now: float, within: float = 10.0) -> bool:
        return bool(self.rows) and (now - self.rows[-1][0]) <= within


def git_slot_hook(branch: str):
    """Make a consumed slot durable in GIT, which outlives the host.

    Called by `r2_exec_probe.probe` AFTER the slot is spent and BEFORE the
    venue is touched -- the ordering `run_canary` established. A failure here
    stops the probe with the slot already spent, which costs one probe and
    never risks an order nobody recorded.
    """
    import subprocess

    def hook(cohort_id: str, ticker: str, slot: Dict[str, Any]
             ) -> Dict[str, Any]:
        d = BUDGET.cohort.directory(cohort_id)
        msg = ("latch(r2-exec): slot %s consumed for %s [skip ci]"
               % (slot.get("slot"), ticker))
        steps = [
            ["git", "add", "-f", d, "analysis/gemini/info_edge/probe_windows.json"],
            ["git", "-c", "user.name=gemini-r2-exec[bot]",
             "-c", "user.email=gemini-r2-exec@users.noreply.github.com",
             "commit", "-m", msg],
            ["git", "push", "origin", "HEAD:%s" % branch],
        ]
        log = []
        for cmd in steps:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=60)
            log.append({"cmd": cmd[0] + " " + cmd[1],
                        "rc": p.returncode,
                        "err": (p.stderr or "")[-200:]})
            if p.returncode != 0 and cmd[1] != "commit":
                return {"ok": False, "where": cmd[1], "log": log}
        return {"ok": True, "branch": branch, "log": log}

    return hook


def discover(now: float) -> List[Dict[str, Any]]:
    """Active BTC5m contracts whose STRIKE HAS ALREADY PUBLISHED, soonest
    first. A contract without a published strike is not yet observable under
    frozen R2 and is skipped rather than guessed at."""
    out = []
    try:
        evs = metadata.active_events() if hasattr(metadata, "active_events") \
            else None
    except Exception:                                          # noqa: BLE001
        evs = None
    if evs is None:
        evs = _active_events_public()
    for e in evs:
        tk = e.get("ticker") or ""
        if not BTC5M.match(tk):
            continue
        sym = "GEMI-%s-UP" % tk
        c = (e.get("contracts") or [{}])[0]
        st = c.get("strike") if isinstance(c.get("strike"), dict) else {}
        exp = c.get("expiryDate")
        if not exp:
            continue
        from datetime import datetime
        E = datetime.fromisoformat(str(exp).replace("Z", "+00:00")).timestamp()
        tte = E - now
        if not (0 < tte <= 300):
            continue                      # post-strike window only
        if not st.get("value"):
            continue                      # strike not published yet
        sa = st.get("availableAt")
        if sa:
            A = datetime.fromisoformat(str(sa).replace("Z", "+00:00")).timestamp()
            if now < A:
                continue                  # availableAt has NOT passed
        out.append({"ticker": tk, "symbol": sym, "tte": tte, "expiry": E})
    out.sort(key=lambda r: r["tte"])
    return out


def _active_events_public() -> List[Dict[str, Any]]:
    """Public events listing, via the same transport `metadata` uses."""
    import urllib.request
    url = (metadata.EVENTS_PATH if str(metadata.EVENTS_PATH).startswith("http")
           else "https://api.gemini.com/v1/prediction-markets/events")
    q = "?status=active&category=Crypto&limit=500"
    px = IEC.get("proxy_url", lambda: None)()
    op = (urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": px, "http": px}))
        if px else urllib.request.build_opener())
    with op.open(url + q, timeout=25) as r:
        d = json.loads(r.read())
    return d.get("data") or d.get("events") or (d if isinstance(d, list) else [])


async def run(*, cohort_id: str, outdir: str, max_runtime_s: float,
              dry_run: bool = True,
              stat_gate_fn=None, slot_hook=None) -> Dict[str, Any]:
    """Stay alive across windows. Probe the first qualifying opportunity in
    each distinct market, within every cohort bound."""
    _load_iec()
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    summary: Dict[str, Any] = {
        "label": "GEMINI_R2_EXEC_RUNNER_V1",
        "cohort_id": cohort_id,
        "auth_id": arming.EXPECTED_AUTH_ID,
        "taker_auth_id": payload.SEMANTICS_PROBE_AUTH_ID,
        "rule_semantic_sha": PROBE.RULE_SEMANTIC_SHA,
        "rule_fingerprint": PROBE.RULE["rule_fingerprint"](),
        "dry_run": bool(dry_run),
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "probes": [], "markets_seen": 0, "stopped": None,
    }

    ticks = Ticks()
    stop = asyncio.Event()
    cb = asyncio.create_task(IEC["coinbase_task"](ticks, stop))
    try:
        while True:
            now = time.time()
            if now - started >= max_runtime_s:
                summary["stopped"] = "MAX_RUNTIME"
                break
            h = halted()
            if h:
                summary["stopped"] = "HALTED: %s" % h
                break
            gate = stat_gate_fn() if stat_gate_fn else None
            why = BUDGET.expired(cohort_id, stat_gate_windows=gate)
            if why:
                summary["stopped"] = why
                break
            if not ticks.fresh(now):
                await asyncio.sleep(POLL_S)
                continue

            cands = discover(now)
            fin = BUDGET.finished_markets(cohort_id)
            for c in cands:
                if c["ticker"] in fin:
                    continue
                summary["markets_seen"] += 1
                stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                dest = out / ("r2_probe_%s_%s.json" % (c["ticker"], stamp))
                if dry_run:
                    q = PROBE.qualify(c["symbol"], c["ticker"],
                                      ticks.snapshot(), time.time())
                    rec = {"dry_run": True, "qualification": q,
                           "state": q["state"], "why": q.get("why")}
                    dest.write_text(json.dumps(rec, indent=2, default=str))
                else:
                    rec = await PROBE.probe(symbol=c["symbol"],
                                            cohort_id=cohort_id,
                                            cb_ticks=ticks.snapshot(),
                                            stat_gate_windows=gate,
                                            out=str(dest),
                                            slot_hook=slot_hook)
                summary["probes"].append(
                    {"ticker": c["ticker"], "state": rec.get("state"),
                     "why": rec.get("why"), "record": str(dest)})
                # FIRST qualifying opportunity wins. A market that merely has
                # not triggered yet is left alone and may trigger later in the
                # SAME window -- that is the rule's own grid, not selection.
                # A market that SUBMITTED or MISSED is finished by the budget
                # and will be filtered out above on the next pass.
                if rec.get("state") in ("FILLED", "ACCEPTED_UNFILLED",
                                        "SUBMISSION_UNKNOWN", "REJECTED",
                                        "MISSED_NOT_EXECUTABLE",
                                        "NOT_EXECUTABLE",
                                        "REFUSED_SLOT_NOT_DURABLE"):
                    break
            await asyncio.sleep(POLL_S)
    finally:
        stop.set()
        cb.cancel()
        try:
            await cb
        except BaseException:                                  # noqa: BLE001
            pass
    summary["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary["budget"] = BUDGET.state(cohort_id)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    (out / ("runner_summary_%s.json" % stamp)).write_text(
        json.dumps(summary, indent=2, default=str))
    return summary


async def recover(cohort_id: str) -> Dict[str, Any]:
    """Restart recovery, run BEFORE anything else.

    Nothing is initialised. The consumed count, the finished markets and the
    confirmed loss are all RE-READ from disk, and any order the venue still
    holds is surfaced rather than assumed away.
    """
    st = BUDGET.state(cohort_id)
    rec: Dict[str, Any] = {
        "label": "GEMINI_R2_EXEC_RECOVERY_V1",
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "consumed_recovered": st["consumed"],
        "remaining": st["remaining"],
        "confirmed_realized_principal_loss": st[
            "confirmed_realized_principal_loss"],
        "markets_finished": st["markets_finished"],
        "initialized_rather_than_recovered": False,
        "halt": halted(),
        "expired": st["expired"],
    }
    try:
        live = await transport.active_orders()
        rec["venue_active_orders"] = {
            "ok": live.get("ok"),
            "n": len(transport._orders_of(live)) if live.get("ok") else None}
    except Exception as exc:                                   # noqa: BLE001
        rec["venue_active_orders"] = {"ok": False,
                                      "error": "%s" % type(exc).__name__}
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cohort", required=True)
    ap.add_argument("--outdir", default="analysis/gemini/info_edge/r2_probes")
    ap.add_argument("--max-runtime-s", type=float, default=3600.0)
    ap.add_argument("--live", action="store_true",
                    help="ARM the venue-order path. Without it the runner "
                         "observes and records qualification only and sends "
                         "NOTHING.")
    ap.add_argument("--recover-only", action="store_true")
    ap.add_argument("--durable-branch", default=None,
                    help="push each consumed slot to this branch BEFORE the "
                         "venue is touched. REQUIRED with --live on an "
                         "ephemeral host, where an fsync alone does not "
                         "survive the disk being discarded.")
    a = ap.parse_args(argv)
    if a.recover_only:
        print(json.dumps(asyncio.run(recover(a.cohort)), indent=2,
                         default=str))
        return 0
    hook = git_slot_hook(a.durable_branch) if a.durable_branch else None
    if a.live and hook is None:
        raise SystemExit("--live requires --durable-branch: on an ephemeral "
                         "host an fsynced slot dies with the disk and the "
                         "ten-slot budget would silently reset.")
    s = asyncio.run(run(cohort_id=a.cohort, outdir=a.outdir,
                        max_runtime_s=a.max_runtime_s,
                        dry_run=not a.live, slot_hook=hook))
    print(json.dumps({k: s.get(k) for k in
                      ("dry_run", "markets_seen", "probes", "stopped",
                       "rule_fingerprint")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
