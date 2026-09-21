"""H-QUEUE-MISMEASURED: can public data account for L5's queue clearing?

THE TEST, stated once. L5 rests a BUY at price `px`. At the last moment we
publicly looked at the book, `AL` shares were still resting at or ahead of that
price. He then filled. Either

  (a) that queue was TRADED away, and the public tape shows the volume; or
  (b) it was CANCELLED, and the book shows depth leaving with no matching
      print; or
  (c) neither, and the queue we measured is not the queue that governed his
      fill -- which is H-QUEUE-MISMEASURED.

Everything here operates on IMMUTABLE event records produced by
`l5_prospective_recorder`. Nothing is re-fetched, nothing is written back into
a raw stream, and no quantity is fitted. Raw rows travel with their derivations
so any verdict can be recomputed from the same record.

WHAT THIS MODULE REFUSES TO DO
  * pool markets before within-market effects are established (`pooled()`
      raises below `MIN_N_PER_MARKET`);
  * call a fill unexplained when the unobserved interval is long enough that
      ordinary trading could have cleared the queue unseen (INCONCLUSIVE_GAP);
  * treat a fill with no queue ahead of it as evidence either way (NO_QUEUE);
  * emit a rate without its denominator.
"""
from __future__ import annotations

import collections
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from src.l5_prospective_recorder import bid_consuming

# Preregistered. Fixed BEFORE any prospective observation was scored, and not
# to be moved to make a verdict come out.
MIN_N_PER_MARKET = 200

# A fill is only informative about queueing if something was actually ahead of
# him. One share is the venue minimum, so "ahead" has to mean more than the
# rounding of a single lot.
MIN_QUEUE_SHARES = 1.0

# Tolerance on the accounting identity: the larger of an absolute share floor
# and a fraction of the queue being explained. Both are stated, neither is
# searched.
TOL_ABS_SHARES = 1.0
TOL_REL = 0.05

VERDICTS = ("NO_COVERAGE", "NO_QUEUE", "ACCOUNTED_BY_TRADES",
            "ACCOUNTED_WITH_CANCELS", "INCONCLUSIVE_GAP", "UNEXPLAINED")


def _f(v) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def tolerance(queue: float) -> float:
    return max(TOL_ABS_SHARES, TOL_REL * abs(queue))


def split_window(ev: Mapping[str, Any]) -> tuple:
    """Prints before and after the last book look, and the last-look time.

    The split matters: volume BEFORE the last look is already reflected in the
    depth we measured there, so counting it again against the residual would
    explain the same shares twice.
    """
    prints = list(ev.get("window_prints") or [])
    ts = _f(ev.get("timestamp"))
    # The last look IS the pre-fill book: the trail ends there.
    last_t = _f(ev.get("queue_trail_t1"))
    if last_t is None:
        last_t = _f((ev.get("pre_fill_book") or {}).get("t"))
    if last_t is None or ts is None:
        return prints, [], last_t
    before = [p for p in prints if _f(p.get("t")) is not None
              and _f(p.get("t")) <= last_t]
    after = [p for p in prints if _f(p.get("t")) is not None
             and last_t < _f(p.get("t")) <= ts]
    return before, after, last_t


def reconcile_fill(ev: Mapping[str, Any]) -> dict:
    """One fill's accounting. Returns a verdict plus every term behind it."""
    oi = ev.get("outcome_index")
    px = _f(ev.get("price"))
    # a0 is the EARLIEST look in the trail, al the last look before the fill.
    # Falling back to depth_ahead_at_pre keeps records written before the trail
    # lookback existed readable rather than silently NO_COVERAGE.
    a0 = _f(ev.get("depth_ahead_at_first_look"))
    if a0 is None:
        a0 = _f(ev.get("depth_ahead_at_pre"))
    al = _f(ev.get("depth_ahead_at_last_look"))
    gap_s = _f(ev.get("unobserved_interval_s"))
    unc = _f(ev.get("fill_time_uncertainty_s")) or 1.0
    base = {"transactionHash": ev.get("transactionHash"),
            "slug": ev.get("slug"), "conditionId": ev.get("conditionId"),
            "timestamp": ev.get("timestamp"), "price": px,
            "size": _f(ev.get("size")), "is_taker": ev.get("is_taker"),
            "depth_ahead_at_first_look": a0, "depth_ahead_at_last_look": al,
            "unobserved_interval_s": gap_s,
            "queue_trail_snapshots": ev.get("queue_trail_snapshots")}

    if ev.get("pre_fill_book") is None or oi is None or px is None or a0 is None:
        return {**base, "verdict": "NO_COVERAGE",
                "reason": ev.get("pre_fill_book_reason")
                or "no pre-fill book, price or outcome index"}
    bf = (ev.get("flow_backfill") or {}).get("status")
    if bf in ("NO_COVERAGE", "TAPE_STILL_BEHIND"):
        return {**base, "verdict": "NO_COVERAGE",
                "reason": "trade tape has not published this window: %s" % bf,
                "flow_backfill": ev.get("flow_backfill")}
    if ev.get("window_prints_truncated"):
        return {**base, "verdict": "NO_COVERAGE",
                "reason": "the print window was truncated at the capture cap "
                          "(%s of %s rows kept), so consuming volume would be "
                          "undercounted here -- a capped list must not become "
                          "an UNEXPLAINED verdict"
                          % (len(ev.get("window_prints") or []),
                             ev.get("window_prints_n_total")),
                "window_prints_n_total": ev.get("window_prints_n_total")}
    if a0 < MIN_QUEUE_SHARES:
        return {**base, "verdict": "NO_QUEUE",
                "reason": "nothing material was ahead of him; this fill "
                          "constrains no queue model"}

    before, after, last_t = split_window(ev)
    c_obs = sum(_f(p.get("size")) or 0.0 for p in before
                if bid_consuming(p, int(oi), px))
    c_gap = sum(_f(p.get("size")) or 0.0 for p in after
                if bid_consuming(p, int(oi), px))
    al_eff = a0 if al is None else al

    # (a) over the interval we watched
    observed_drop = round(a0 - al_eff, 6)
    cancelled_implied = round(observed_drop - c_obs, 6)

    # (b) at the fill itself
    shortfall = round(al_eff - c_gap, 6)
    tol = tolerance(a0)

    # Could ordinary trading have cleared `al_eff` inside the unobserved
    # interval without us seeing it? Rate comes from this market's OWN observed
    # window, not from a constant.
    span = None
    t0 = _f(ev.get("window_prints_t0"))
    ts = _f(ev.get("timestamp"))
    if t0 is not None and last_t is not None and last_t > t0:
        span = last_t - t0
    obs_rate = (None if not span else
                sum(_f(p.get("size")) or 0.0 for p in before) / span)
    plausible = (None if (obs_rate is None or gap_s is None)
                 else obs_rate * max(gap_s, unc))

    out = {**base,
           "consuming_volume_observed_interval": c_obs,
           "consuming_volume_unobserved_interval": c_gap,
           "observed_depth_drop": observed_drop,
           "cancellation_implied_shares": cancelled_implied,
           "residual_queue_at_last_look": al_eff,
           "shortfall_shares": shortfall,
           "tolerance_shares": tol,
           "market_observed_rate_shares_per_s": obs_rate,
           "plausible_unseen_volume": plausible,
           "complement_arrival_volume": _f(ev.get("complement_arrival_volume")),
           "prints_observed_interval": len(before),
           "prints_unobserved_interval": len(after)}

    if shortfall <= tol:
        out["verdict"] = ("ACCOUNTED_WITH_CANCELS" if cancelled_implied > tol
                          else "ACCOUNTED_BY_TRADES")
        out["reason"] = ("public trades cover the queue that was ahead of him"
                         if cancelled_implied <= tol else
                         "public trades cover the residual, but %.1f shares "
                         "left the book earlier with no matching print"
                         % cancelled_implied)
        return out
    if obs_rate is None:
        # NO OBSERVED SPAN, SO NO BASIS TO RULE THE GAP OUT. This happens when
        # the trail has a single look (or none): `span` collapses to zero and
        # the market's own rate is unmeasurable. Falling through to UNEXPLAINED
        # here would have made every such fill "unexplained" BY CONSTRUCTION --
        # and it does, on records written before the trail lookback existed,
        # where 4 of 4 scorable fills came out UNEXPLAINED with no possible
        # INCONCLUSIVE_GAP to compete. Claiming the queue could not have
        # cleared requires a rate we do not have.
        out["verdict"] = "INCONCLUSIVE_GAP"
        out["reason"] = ("only one book look is available, so this market's "
                         "own trade rate is unmeasurable and the %.1fs before "
                         "the fill cannot be ruled out as enough to clear the "
                         "%.1f shares ahead of him"
                         % (gap_s or 0.0, al_eff))
        out["degenerate_observed_interval"] = True
        return out
    if plausible is not None and al_eff <= plausible:
        out["verdict"] = "INCONCLUSIVE_GAP"
        out["reason"] = ("the book was last read %.1fs before the fill; at "
                         "this market's own observed rate (%.2f sh/s) that "
                         "interval could have cleared %.1f shares unseen, "
                         "which covers the %.1f still ahead of him"
                         % (gap_s or 0.0, obs_rate or 0.0, plausible, al_eff))
        return out
    out["verdict"] = "UNEXPLAINED"
    out["reason"] = ("%.1f shares were still resting at or ahead of his price "
                     "at the last public look %.1fs before the fill, and only "
                     "%.1f shares of consuming volume were published in that "
                     "interval. Public book mutations plus public trades do "
                     "not account for his queue clearing."
                     % (al_eff, gap_s or 0.0, c_gap))
    return out


def reconcile(events: Iterable[Mapping[str, Any]], *, maker_only: bool = True
              ) -> List[dict]:
    """Reconcile a stream of events. Maker fills only, by default: a taker fill
    crosses the spread and has no queue question to answer."""
    out = []
    for ev in events:
        if maker_only and ev.get("is_taker") is not False:
            continue
        out.append(reconcile_fill(ev))
    return out


def atomic_groups(events: Sequence[Mapping[str, Any]]) -> Dict[str, dict]:
    """(10) Do several L5 fills land in ONE transaction?

    Atomic multi-fills are a matching-context fact, not a priority mechanism.
    Counted and reported; never used to infer a separate queue.
    """
    by_tx: Dict[str, List[Mapping[str, Any]]] = collections.defaultdict(list)
    for ev in events:
        h = ev.get("transactionHash")
        if h:
            by_tx[str(h)].append(ev)
    out = {}
    for h, rows in by_tx.items():
        sides = {r.get("outcome_index") for r in rows}
        out[h] = {"n_fills": len(rows), "atomic": len(rows) > 1,
                  "outcomes": sorted(str(s) for s in sides),
                  "both_outcomes": len(sides) > 1,
                  "total_size": sum(_f(r.get("size")) or 0.0 for r in rows),
                  "prices": sorted({_f(r.get("price")) for r in rows} - {None}),
                  "slugs": sorted({str(r.get("slug")) for r in rows})}
    return out


def annotate_atomic(events: Sequence[Mapping[str, Any]]) -> List[dict]:
    """Fill in `atomic_group_size` without mutating the raw records."""
    g = atomic_groups(events)
    out = []
    for ev in events:
        r = dict(ev)
        h = ev.get("transactionHash")
        info = g.get(str(h)) if h else None
        r["atomic_group_size"] = (info or {}).get("n_fills")
        r["atomic_group_both_outcomes"] = (info or {}).get("both_outcomes")
        r["atomic_group_reason"] = (None if info else "no transactionHash on the fill")
        out.append(r)
    return out


def by_market(rows: Sequence[Mapping[str, Any]]) -> Dict[str, List[dict]]:
    key = lambda r: str(r.get("conditionId") or r.get("slug") or "UNKNOWN")
    out: Dict[str, List[dict]] = collections.defaultdict(list)
    for r in rows:
        out[key(r)].append(dict(r))
    return dict(out)


def counts(rows: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    c = {v: 0 for v in VERDICTS}
    for r in rows:
        c[r.get("verdict", "NO_COVERAGE")] = c.get(r.get("verdict"), 0) + 1
    return c


def scorable(rows: Sequence[Mapping[str, Any]]) -> List[dict]:
    """Fills that can discriminate: a real queue, and coverage to judge it."""
    return [r for r in rows
            if r.get("verdict") in ("ACCOUNTED_BY_TRADES",
                                    "ACCOUNTED_WITH_CANCELS",
                                    "INCONCLUSIVE_GAP", "UNEXPLAINED")]


class InsufficientEvidence(RuntimeError):
    """Raised instead of returning a pooled number that is not yet earned."""


def market_report(rows: Sequence[Mapping[str, Any]]) -> List[dict]:
    """One row per market. Always available -- partitioning is the default."""
    out = []
    for mk, rs in sorted(by_market(rows).items()):
        sc = scorable(rs)
        n = len(sc)
        unex = sum(1 for r in sc if r["verdict"] == "UNEXPLAINED")
        out.append({
            "market": mk, "n_fills": len(rs), "n_scorable": n,
            "counts": counts(rs),
            "unexplained_rate": (None if n == 0 else unex / n),
            "unexplained_rate_denominator": n,
            "meets_preregistered_n": n >= MIN_N_PER_MARKET,
            "note": ("below the preregistered n=%d: descriptive only, no "
                     "inference" % MIN_N_PER_MARKET) if n < MIN_N_PER_MARKET else None,
        })
    return sorted(out, key=lambda r: -r["n_scorable"])


def pooled(rows: Sequence[Mapping[str, Any]], *,
           min_n: int = MIN_N_PER_MARKET) -> dict:
    """Pooled rate -- ONLY once within-market effects can be established.

    Refuses rather than returning a number, because the failure mode this
    guards against is reading a pooled rate off n=16 and believing it.
    """
    per = market_report(rows)
    ok = [m for m in per if m["meets_preregistered_n"]]
    total = sum(m["n_scorable"] for m in per)
    if not ok:
        raise InsufficientEvidence(
            "no market reaches the preregistered n=%d (best market has %d "
            "scorable fills; %d in total across %d markets). Accumulate first; "
            "do not fit."
            % (min_n, max([m["n_scorable"] for m in per] or [0]), total, len(per)))
    sc = [r for r in scorable(rows)
          if str(r.get("conditionId") or r.get("slug") or "UNKNOWN")
          in {m["market"] for m in ok}]
    unex = sum(1 for r in sc if r["verdict"] == "UNEXPLAINED")
    return {"markets_pooled": [m["market"] for m in ok],
            "n_scorable": len(sc),
            "unexplained_rate": (unex / len(sc)) if sc else None,
            "unexplained": unex,
            "excluded_markets_below_n": [m["market"] for m in per
                                         if not m["meets_preregistered_n"]]}


def table(rows: Sequence[Mapping[str, Any]]) -> str:
    per = market_report(rows)
    w = max([len(m["market"]) for m in per] + [6])
    head = ("%-*s  %8s %9s  %9s %9s %9s %9s %9s %9s  %s"
            % (w, "market", "n_fills", "scorable", "NOCOV", "NOQUEUE",
               "TRADES", "CANCELS", "INCONCL", "UNEXPL", "unexpl_rate"))
    lines = [head, "-" * len(head)]
    for m in per:
        c = m["counts"]
        rate = ("n/a" if m["unexplained_rate"] is None
                else "%.3f (n=%d)%s" % (m["unexplained_rate"],
                                        m["unexplained_rate_denominator"],
                                        "" if m["meets_preregistered_n"] else " *"))
        lines.append("%-*s  %8d %9d  %9d %9d %9d %9d %9d %9d  %s"
                     % (w, m["market"], m["n_fills"], m["n_scorable"],
                        c["NO_COVERAGE"], c["NO_QUEUE"],
                        c["ACCOUNTED_BY_TRADES"], c["ACCOUNTED_WITH_CANCELS"],
                        c["INCONCLUSIVE_GAP"], c["UNEXPLAINED"], rate))
    lines.append("")
    lines.append("* below the preregistered n=%d -- descriptive only."
                 % MIN_N_PER_MARKET)
    lines.append("Markets are NOT pooled. pooled() raises until a market "
                 "reaches n=%d." % MIN_N_PER_MARKET)
    return "\n".join(lines)


# ------------------------------------------------- (9) PositionSplit context
def annotate_chain(events: Sequence[Mapping[str, Any]],
                   facts: Mapping[str, Mapping[str, Any]]) -> List[dict]:
    """Attach on-chain transaction context to each event, without mutating raw.

    `facts` is `mint_onchain.fetch(txs)` output: PositionSplit / PositionsMerge
    / exchange-fill log counts per transaction hash.

    SCOPE, restated because it has been over-read before: this is evidence of
    the TRANSACTION AND MATCHING CONTEXT a fill occurred in. It is NOT evidence
    of a separate queue or of special priority, and the earlier finding stands
    -- minting is the venue's ordinary settlement path and does not distinguish
    maker from taker (86.5% vs 90.0%, CI includes zero).
    """
    out = []
    for ev in events:
        r = dict(ev)
        h = ev.get("transactionHash")
        f = facts.get(str(h)) if h else None
        r["position_split_context"] = (dict(f) if f else None)
        r["position_split_context_reason"] = (
            None if f else ("no transactionHash on the fill" if not h
                            else "receipt not fetched for this transaction"))
        r["position_split_scope"] = (
            "transaction/matching context only; not a priority mechanism")
        out.append(r)
    return out


def chain_summary(rows: Sequence[Mapping[str, Any]]) -> dict:
    """Counts only. No rate is computed against a fill class here, because the
    maker/taker mint comparison is already settled and is not re-opened by a
    prospective sample this small."""
    have = [r for r in rows if r.get("position_split_context")]
    if not have:
        return {"n_with_receipt": 0, "note": "no receipts joined"}
    ps = sum(1 for r in have if r["position_split_context"].get("position_splits"))
    mg = sum(1 for r in have if r["position_split_context"].get("positions_merges"))
    multi = sum(1 for r in have
                if (r["position_split_context"].get("exchange_fills") or 0) > 1)
    return {"n_with_receipt": len(have),
            "with_position_split": ps,
            "with_positions_merge": mg,
            "multi_fill_transactions": multi,
            "scope": "context only; not evidence of a priority mechanism"}
