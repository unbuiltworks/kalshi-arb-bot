"""Residual-efficiency frontier: the fill-level suppression replay.

Protocol RESIDUAL-EFFICIENCY-FRONTIER-LANE-B, frozen at
`spec.FRONTIER_FINGERPRINT`. Preregistration:
`analysis/l5/residual_frontier/PREREGISTRATION.md`.

HISTORICAL REPLAY ONLY. Every dollar here is a counterfactual modification of
another trader's public Polymarket activity. Not shadow, not a probe, not
submitted, not filled, not settled, never realized. No network, no venue
write, no order path. Rebates and rewards are exactly $0.

THE ONE THING TO UNDERSTAND. `DecisionState` is the ONLY object a rule sees,
and its field set is exactly `spec.ALLOWED_STATE_FIELDS`. Settlement lives in
`_Event.winner_index`, which the replay reads AFTER the full fill sequence has
been decided and only to score. A rule cannot reach it: it is not passed in.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.norebate.q1 import day, era, load
from analysis.l5.residual_frontier import spec

OBSERVED_DAYS = 22
#: Primary regime partition, detected in analysis/l5/CANON_REGIMES.md. Not
#: invented here.
CANON_CHANGE_POINTS = ("2026-04-25", "2026-06-23", "2026-08-19")


# --------------------------------------------------------------- the state

@dataclass(frozen=True)
class DecisionState:
    """Contemporaneous state at one candidate fill. NOTHING ELSE EXISTS.

    Every field is observable at the candidate fill's own timestamp.
    `shares_up` / `shares_down` are built from fills THIS CELL already took.
    """
    __slots__ = tuple(spec.ALLOWED_STATE_FIELDS)

    shares_up: float
    shares_down: float
    cand_is_up: bool
    cand_size: float
    cand_price: float
    tte_s: float
    duration_s: int
    taken_count: int

    # -- derived views, all functions of the fields above only --
    @property
    def residual(self) -> float:
        return self.shares_up - self.shares_down

    @property
    def gross(self) -> float:
        return self.shares_up + self.shares_down

    @property
    def is_complete(self) -> bool:
        """True iff the candidate lands on the strictly lighter side.

        spec.ACTION_RULE. With an empty or balanced book nothing is lighter,
        so the fill is an EXTEND.
        """
        own = self.shares_up if self.cand_is_up else self.shares_down
        other = self.shares_down if self.cand_is_up else self.shares_up
        return own < other

    @property
    def post_residual(self) -> float:
        d = self.cand_size if self.cand_is_up else -self.cand_size
        return self.residual + d

    @property
    def post_gross(self) -> float:
        return self.gross + self.cand_size


def allow(st: DecisionState, cell: Dict[str, Any]) -> bool:
    """The rule. A pure function of a DecisionState and frozen parameters.

    Returns True to TAKE the candidate fill. Reads nothing but `st` and
    `cell`; `cell` holds only the frozen numeric parameters of the grid.
    """
    if cell["family"] == "BASELINE":
        return True
    if cell["semantics"] == "EXTEND_ONLY" and st.is_complete:
        # the cap gates ADDITIONAL SAME-SIDE exposure; a COMPLETE, which
        # reduces |residual|, is always allowed.
        return True

    post = abs(st.post_residual)

    cap = cell["abs_cap"]
    if cap is not None:
        if cell["tte_s"] is not None and st.tte_s < cell["tte_s"]:
            cap = cap * cell["tte_tighten"]
        if post > cap:
            return False

    frac = cell["frac_cap"]
    if frac is not None:
        pg = st.post_gross
        if pg > 0 and post > frac * pg:
            return False

    return True


# --------------------------------------------------------------- the corpus

@dataclass
class _Fill:
    ts: int
    is_up: bool
    size: float
    price: float
    usdc: float
    tte_s: float


@dataclass
class _Event:
    slug: str
    ts: int
    day: str
    era: str
    duration_s: int
    fills: List[_Fill]
    #: SCORING ONLY. 0 = Up won, 1 = Down won, None = no REDEEM record.
    winner_index: Optional[int]


def _duration_and_epoch(slug: str) -> Tuple[int, int]:
    # btc-updown-5m-<open epoch> / eth-updown-15m-<open epoch>. The trailing
    # integer is the window OPEN epoch; TTE = epoch + duration - t, verified
    # in analysis/l5/EXTEND_AND_SWITCH.md on 3,528 conditions, 0 mismatches.
    tail = slug.rsplit("-", 1)[-1]
    dur = 900 if "-15m-" in slug else 300
    return dur, int(tail)


def build_events(activity: str = "data/l5/raw/activity.jsonl",
                 q1_events: str = "analysis/l5/norebate/Q1_EVENTS.jsonl",
                 ) -> List[_Event]:
    """The frozen Q1 population, rebuilt at fill granularity.

    The event key and the admitted set are taken from the frozen audit's own
    output so the population cannot drift: an event is admitted iff its slug
    appears in Q1_EVENTS.jsonl.
    """
    keep = {json.loads(l)["slug"] for l in open(q1_events, encoding="utf-8")
            if l.strip()}
    rows = load(activity)
    raw: Dict[str, Dict[str, Any]] = collections.defaultdict(
        lambda: {"fills": [], "ts": None, "redeem_idx": set()})
    for r in rows:
        slug = r.get("eventSlug") or r.get("conditionId") or ""
        if slug not in keep:
            continue
        d = raw[slug]
        if r["type"] in ("TRADE", "REDEEM"):
            d["ts"] = (r["timestamp"] if d["ts"] is None
                       else min(d["ts"], r["timestamp"]))
        if r["type"] == "TRADE":
            if r.get("side") != "BUY":
                # the corpus contains 2 SELL records in total; L5 is
                # effectively buy-only. A SELL is not a suppressible
                # acquisition and is excluded, which is recorded, not hidden.
                continue
            d["fills"].append(r)
        elif r["type"] == "REDEEM":
            d["redeem_idx"].add(r.get("outcomeIndex"))

    out: List[_Event] = []
    for slug, d in raw.items():
        if d["ts"] is None or not d["fills"]:
            continue
        dur, epoch = _duration_and_epoch(slug)
        fills = sorted(d["fills"], key=lambda r: r["timestamp"])
        out.append(_Event(
            slug=slug, ts=d["ts"], day=day(d["ts"]), era=era(d["ts"]),
            duration_s=dur,
            fills=[_Fill(ts=r["timestamp"],
                         is_up=(r.get("outcomeIndex") == 0),
                         size=float(r["size"]), price=float(r["price"]),
                         usdc=float(r["usdcSize"]),
                         tte_s=float(epoch + dur - r["timestamp"]))
                   for r in fills],
            winner_index=(list(d["redeem_idx"])[0]
                          if len(d["redeem_idx"]) == 1 else None),
        ))
    out.sort(key=lambda e: e.ts)
    return out


# --------------------------------------------------------------- the scorer

def _score_event(ev: _Event, cell: Dict[str, Any]) -> Dict[str, Any]:
    """Replay one event under one cell. Settlement is read only at the end."""
    up = dn = 0.0
    cost_up = cost_dn = 0.0
    fees = turn = 0.0
    taken = 0
    n_ext = n_cmp = taken_ext = taken_cmp = 0

    for f in ev.fills:
        st = DecisionState(
            shares_up=up, shares_down=dn, cand_is_up=f.is_up,
            cand_size=f.size, cand_price=f.price, tte_s=f.tte_s,
            duration_s=ev.duration_s, taken_count=taken)
        is_cmp = st.is_complete
        if is_cmp:
            n_cmp += 1
        else:
            n_ext += 1
        if not allow(st, cell):
            continue
        taken += 1
        if is_cmp:
            taken_cmp += 1
        else:
            taken_ext += 1
        if f.is_up:
            up += f.size
            cost_up += f.price * f.size
        else:
            dn += f.size
            cost_dn += f.price * f.size
        fees += max(0.0, f.usdc - f.price * f.size)
        turn += f.usdc

    matched = min(up, dn)
    pair_cost = ((cost_up / up) if up > 0 else 0.0) + \
                ((cost_dn / dn) if dn > 0 else 0.0)
    matched_usd = matched * (1.0 - pair_cost) if matched > 0 else 0.0

    resid = up - dn
    r_side = 0 if resid > 0 else 1
    vwap = (cost_up / up) if (r_side == 0 and up > 0) else \
           ((cost_dn / dn) if (r_side == 1 and dn > 0) else 0.0)
    r_cost = vwap * abs(resid)
    # SETTLEMENT IS READ HERE AND ONLY HERE.
    r_payout = (abs(resid) if (ev.winner_index is not None
                               and r_side == ev.winner_index) else 0.0)
    resid_usd = r_payout - r_cost

    return {
        "matched_usd": matched_usd, "residual_usd": resid_usd,
        "fees_usd": fees, "turnover_usd": turn,
        "net_usd": matched_usd + resid_usd - fees,
        "taken": taken, "available": len(ev.fills),
        "n_extend": n_ext, "n_complete": n_cmp,
        "taken_extend": taken_ext, "taken_complete": taken_cmp,
        "touched": 1 if taken else 0,
        "matched_shares": matched,
    }


def _regime(d: str) -> str:
    if d < CANON_CHANGE_POINTS[0]:
        return "REGIME_PRE_2026-04-25"
    if d < CANON_CHANGE_POINTS[1]:
        return "REGIME_2026-04-25_TO_2026-06-22"
    if d < CANON_CHANGE_POINTS[2]:
        return "REGIME_2026-06-23_TO_2026-08-18"
    return "REGIME_2026-08-19_ON"


def _strata_of(ev: _Event) -> List[str]:
    return ["FULL_CORPUS", ev.era, _regime(ev.day),
            ("SETTLEMENT_RESOLVED" if ev.winner_index is not None
             else "SETTLEMENT_UNRESOLVED")]


def _drawdown(vals: List[float]) -> float:
    peak = cum = 0.0
    dd = 0.0
    for v in vals:
        cum += v
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return dd


def _metrics(acc: Dict[str, Any], base: Optional[Dict[str, Any]]
             ) -> Dict[str, Any]:
    """The metric block. IDENTICAL for every cell -- no cell-specific path."""
    turn = acc["turnover_usd"]
    avail = acc["available"]
    net = acc["matched_usd"] + acc["residual_usd"] - acc["fees_usd"]
    base_turn = base["turnover_usd"] if base else turn
    base_resid = base["residual_usd"] if base else acc["residual_usd"]
    m = {
        "matched_retained_usd": round(acc["matched_usd"], 2),
        "residual_pnl_usd": round(acc["residual_usd"], 2),
        "residual_loss_avoided_usd": round(acc["residual_usd"] - base_resid, 2),
        "fees_usd": round(acc["fees_usd"], 2),
        "net_cash_ex_incentives_usd": round(net, 2),
        "turnover_retained_usd": round(turn, 2),
        "turnover_retained_frac": (round(turn / base_turn, 4)
                                   if base_turn else None),
        "events_touched": acc["touched"],
        "events_per_observed_day": round(acc["touched"] / OBSERVED_DAYS, 2),
        "worst_drawdown_usd": round(_drawdown(acc["nets"]), 2),
        "suppressed_action_frac": (round(1.0 - acc["taken"] / avail, 4)
                                   if avail else None),
        "suppressed_extend_frac": (
            round(1.0 - acc["taken_extend"] / acc["n_extend"], 4)
            if acc["n_extend"] else None),
        "suppressed_complete_frac": (
            round(1.0 - acc["taken_complete"] / acc["n_complete"], 4)
            if acc["n_complete"] else None),
        "net_bp_of_turnover": (round(10000.0 * net / turn, 1)
                               if turn else None),
    }
    assert set(m) == set(spec.METRICS), "metric key set drifted"
    return m


def _new_acc() -> Dict[str, Any]:
    a = {k: 0.0 for k in ("matched_usd", "residual_usd", "fees_usd",
                          "turnover_usd")}
    a.update({k: 0 for k in ("taken", "available", "n_extend", "n_complete",
                             "taken_extend", "taken_complete", "touched")})
    a["nets"] = []
    return a


def run(events: Optional[List[_Event]] = None) -> Dict[str, Any]:
    spec.assert_frozen()
    if events is None:
        events = build_events()

    # cell -> stratum -> accumulator
    acc: Dict[str, Dict[str, Dict[str, Any]]] = {
        c["cell_id"]: collections.defaultdict(_new_acc) for c in spec.GRID}
    for ev in events:
        strata = _strata_of(ev)
        for c in spec.GRID:
            r = _score_event(ev, c)
            for s in strata:
                a = acc[c["cell_id"]][s]
                for k in ("matched_usd", "residual_usd", "fees_usd",
                          "turnover_usd", "taken", "available", "n_extend",
                          "n_complete", "taken_extend", "taken_complete",
                          "touched"):
                    a[k] += r[k]
                a["nets"].append(r["net_usd"])

    base = acc["BASELINE"]
    cells: Dict[str, Any] = {}
    for c in spec.GRID:
        cid = c["cell_id"]
        blocks = {}
        for s in sorted(acc[cid]):
            blocks[s] = _metrics(acc[cid][s], base.get(s))
        full = blocks["FULL_CORPUS"]
        bfull = _metrics(base["FULL_CORPUS"], base["FULL_CORPUS"])
        gutted = []
        if full["net_cash_ex_incentives_usd"] > 0:
            if (full["turnover_retained_frac"] or 0) < 0.50:
                gutted.append("turnover_retained_frac<0.50")
            if (full["suppressed_action_frac"] or 0) > 0.50:
                gutted.append("suppressed_action_frac>0.50")
            if bfull["matched_retained_usd"] and (
                    full["matched_retained_usd"]
                    / bfull["matched_retained_usd"]) < 0.50:
                gutted.append("matched_retained<50%_of_baseline")
        matched_sacrificed = (bfull["matched_retained_usd"]
                              - full["matched_retained_usd"])
        cells[cid] = {
            "params": c,
            # DERIVED, not a preregistered metric: the exchange rate at which
            # a cell buys avoided residual loss with sacrificed matched
            # economics. > 1.0 means the trade is favourable before fees.
            "DERIVED_avoided_per_matched_sacrificed": (
                round(full["residual_loss_avoided_usd"] / matched_sacrificed,
                      4) if matched_sacrificed > 1e-9 else None),
            "DERIVED_matched_sacrificed_usd": round(matched_sacrificed, 2),
            "DERIVED_net_improvement_vs_baseline_usd": round(
                full["net_cash_ex_incentives_usd"]
                - bfull["net_cash_ex_incentives_usd"], 2),
            "matched_retained_frac_of_baseline": (
                round(full["matched_retained_usd"]
                      / bfull["matched_retained_usd"], 4)
                if bfull["matched_retained_usd"] else None),
            "POSITIVE_FULL_CORPUS": full["net_cash_ex_incentives_usd"] > 0,
            "GUTTED_ENGINE": bool(gutted),
            "GUTTED_REASONS": gutted,
            "strata": blocks,
        }

    return {
        "protocol": spec.PROTOCOL,
        "fingerprint": spec.FRONTIER_FINGERPRINT,
        "parent_protocol": spec.PARENT_PROTOCOL,
        "population": spec.POPULATION,
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "profit_label": "historical replay",
        "NOT_OUR_REALIZED_PROFIT": (
            "a counterfactual modification of another trader's public "
            "activity. Not shadow, not submitted, not filled, not settled, "
            "never realized."),
        "rebates_rewards_usd": 0.0,
        "n_events": len(events),
        "n_fills": sum(len(e.fills) for e in events),
        "n_cells": spec.N_CELLS,
        "observed_days": OBSERVED_DAYS,
        "metric_definitions": spec.METRICS,
        "strata_definitions": spec.STRATA,
        "no_hindsight_clause": spec.NO_HINDSIGHT_CLAUSE,
        "oos_hold_clause": spec.OOS_HOLD_CLAUSE,
        "cells": cells,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity", default="data/l5/raw/activity.jsonl")
    ap.add_argument("--out", default="analysis/l5/residual_frontier/"
                                     "FRONTIER.json")
    a = ap.parse_args(argv)
    out = run(build_events(a.activity))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=str)
    print("written %s: %d cells, %d events, %d fills"
          % (a.out, out["n_cells"], out["n_events"], out["n_fills"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
