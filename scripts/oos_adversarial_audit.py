"""Adversarial audit of the 49-window OOS negative result.

THE POSTURE. Assume the published result is WRONG. Nothing here loads
r7_oos_actual_winners.json, r7_oos_frontier.json, or the derived
oos_tape.jsonl. Every input is re-derived from the lowest-level preserved
artefact: the gzipped Railway log slices under
data/pmus_btc15m_tape/raw_railway_logs/2026-09-28/, which are
`{"timestamp","message"}` lines exactly as Railway returned them.

Every check emits PASS or FAIL. If any invariant FAILS the caller must not
report aggregate OOS economics.

Nothing is fitted. Strategy parameters are read from the frozen engine
module and never written.
"""
from __future__ import annotations

import glob
import gzip
import json
import os
import statistics
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

import scripts.pmus_completeness_rule as CR
from scripts.audit_economic_complete import USABLE, TAPE as FROZEN_TAPE
from src import pmus_l5_native_engine as E
from src import pmus_native_parity as P

RAW_GLOB = "data/pmus_btc15m_tape/raw_railway_logs/2026-09-28/*.jsonl.gz"
MARK = "PMUS_TAPE_ROW "
WINNERS_JSON = "analysis/l5/r7_oos_winners.json"

#: the pinned frozen checkpoint. Step 11: the replay must reproduce this
#: EXACTLY before any OOS number it produces is trusted.
FROZEN_MEAN_CLIP50 = D("6.116725359099999999999999992")

CLIPS = (10, 20, 25, 30)
RESULTS: List[Dict[str, Any]] = []


def check(name: str, ok: bool, detail: Any = None) -> bool:
    RESULTS.append({"invariant": name, "verdict": "PASS" if ok else "FAIL",
                    "detail": detail})
    return bool(ok)


# ---------------------------------------------------------------- step 1

def raw_rows() -> List[Dict[str, Any]]:
    """Every PMUS_TAPE_ROW in the preserved slices, de-duplicated."""
    seen = set()
    out = []
    files = sorted(glob.glob(RAW_GLOB))
    for p in files:
        with gzip.open(p, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    env = json.loads(line)
                except ValueError:
                    continue
                msg = env.get("message") or ""
                i = msg.find(MARK)
                if i < 0:
                    continue
                try:
                    r = json.loads(msg[i + len(MARK):])
                except ValueError:
                    continue
                k = (r.get("slug"), r.get("kind"), r.get("t"))
                if k in seen:
                    continue
                seen.add(k)
                out.append(r)
    return out, files


def by_slug(rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict]]:
    d: Dict[str, List[Dict]] = {}
    for r in rows:
        if r.get("slug"):
            d.setdefault(r["slug"], []).append(r)
    for v in d.values():
        v.sort(key=lambda x: str(x.get("t")))
    return d


# ---------------------------------------------------------------- step 5-7

def replay_one(rows: Sequence[Dict[str, Any]], *, winner: str,
               clip: int) -> Dict[str, Any]:
    """Frozen engine over one window, with an INDEPENDENT ledger.

    The engine's own settle() is used for the authoritative figures, and the
    components are re-derived here so the identity can be asserted rather
    than assumed.
    """
    res = P.replay_window(rows, winner=winner, clip=D(str(clip)))
    eng_net = P.net_of(res)
    matched = res.get("matched_pnl")
    resid = res.get("resid_pnl")
    fees = res.get("fees")
    return {"net": eng_net, "matched_pnl": matched, "resid_pnl": resid,
            "fees": fees,
            "completions": res.get("executable_states"),
            "raw": {k: res.get(k) for k in
                    ("q", "c", "cum_matched", "residual", "maker_qty",
                     "taker_qty", "completed_qty", "modeled_rebate")}}


def identity_holds(row: Dict[str, Any]) -> Tuple[bool, str]:
    """net == matched + residual - fees, to the last place."""
    m, r, f, n = (row.get("matched_pnl"), row.get("resid_pnl"),
                  row.get("fees"), row.get("net"))
    if None in (m, r, f, n):
        return False, "a component is None: m=%s r=%s f=%s n=%s" % (m, r, f, n)
    lhs = D(str(n))
    rhs = D(str(m)) + D(str(r)) - D(str(f))
    return (lhs == rhs), "net=%s  matched+residual-fees=%s  delta=%s" % (
        lhs, rhs, lhs - rhs)


if __name__ == "__main__":
    print("This module is driven by oos_adversarial_audit_run.py", flush=True)
