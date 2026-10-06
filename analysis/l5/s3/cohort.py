"""S3 cohort runner: 25 BTC15m markets, frozen gates, zero capital.

Feeds the instrument from either a RECORDED tape (testing) or a LIVE
read-only public feed (prospective). Neither path can submit or sign.

The catastrophic-mismatch gate is evaluated every market. Per the brief, a
first-five-market no-evidenced-fill breach is preserved as a SUBSTANTIVE
RESULT as well as halting: zero evidenced fills is itself the answer to
S3's question, provided the instrument is working. The runner therefore
separates INSTRUMENTATION_FAILURE from VALID_ZERO_FILL_EVIDENCE, and does
not restart or extend the cohort either way.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

from analysis.l5.s3.instrument import (MARKOUT_HORIZONS, S3Market, Snap,
                                       assert_no_execution_surface)
from analysis.l5.s3.spec import SPEC, S3_FINGERPRINT, assert_frozen

GATE = SPEC["catastrophic_mismatch_gate"]["abort_if_any"]
COHORT_N = SPEC["cohort"]["n_markets_required"]

#: "COMPLETE market" is NOT a frozen-spec parameter -- the spec fixes the
#: cohort SIZE (25) but not what counts as complete. Declared here at
#: runner level and disclosed: a market is COMPLETE when its observations
#: span at least 840 s of the 900 s window with no observation gap longer
#: than 60 s. A partially observed market still appears in the output but
#: does NOT count toward the 25.
MARKET_COMPLETE_MIN_SPAN_S = 840.0
MARKET_COMPLETE_MAX_GAP_S = 60.0
MARKET_COMPLETE_DOC = (
    "RUNNER-LEVEL criterion, not a frozen-spec parameter: span >= %ds of "
    "the 900s window AND no observation gap > %ds. Partially observed "
    "markets are reported but do not count toward the %d."
    % (MARKET_COMPLETE_MIN_SPAN_S, MARKET_COMPLETE_MAX_GAP_S, 25))


def load_tape(path: str) -> Dict[str, List[dict]]:
    by: Dict[str, List[dict]] = defaultdict(list)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("kind") == "book" and r.get("slug"):
                by[r["slug"]].append(r)
    return by


def _ts(v) -> Optional[float]:
    from datetime import datetime
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def run_market(slug: str, rows: List[dict]) -> Dict[str, Any]:
    """One market from recorded book rows.

    The tape carries ONE outcome's book per row. A two-sided instrument
    needs both, so a tape that never supplies the complement yields
    `complement_never_observed` and the reservation cannot be evaluated --
    reported, never guessed.
    """
    obs = []
    for r in rows:
        t = _ts(r.get("t"))
        if t is None:
            continue
        obs.append((t, r))
    obs.sort(key=lambda x: x[0])
    if not obs:
        return {"market_slug": slug, "VERDICT": "NO_USABLE_ROWS"}

    start = obs[0][0]
    m = S3Market(slug, start)
    series: Dict[str, List[Snap]] = defaultdict(list)
    comp_seen = False
    # A feed delivers the two outcomes asynchronously, so the instrument
    # must act on the LATEST KNOWN state of BOTH. Passing one outcome per
    # step would leave the complement unknown, which silently disables the
    # reservation -- S3's core mechanism. Rows are therefore grouped by
    # timestamp and carried forward per outcome.
    latest: Dict[str, Snap] = {}
    i = 0
    while i < len(obs):
        t = obs[i][0]
        while i < len(obs) and obs[i][0] == t:
            r = obs[i][1]
            oc = str(r.get("outcome") or r.get("side") or "up").lower()
            if oc not in ("up", "down"):
                oc = "up"
            s = Snap(t, r.get("bids"), r.get("offers"), r.get("state"))
            series[oc].append(s)
            latest[oc] = s
            if oc == "down":
                comp_seen = True
            i += 1
        m.step(t, dict(latest))
    m.finish(obs[-1][0], series)
    out = m.metrics()
    out["complement_never_observed"] = not comp_seen
    out["n_snapshots"] = len(obs)
    span = obs[-1][0] - obs[0][0]
    gaps = [b[0] - a[0] for a, b in zip(obs, obs[1:])]
    out["span_s"] = round(span, 1)
    out["max_observation_gap_s"] = (round(max(gaps), 1) if gaps else None)
    out["market_COMPLETE"] = bool(
        span >= MARKET_COMPLETE_MIN_SPAN_S
        and (not gaps or max(gaps) <= MARKET_COMPLETE_MAX_GAP_S))
    out["orders"] = [o.record(m.inv) for o in m.closed]
    out["stand_downs"] = m.stand_downs
    return out


def gate_check(done: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Frozen gate, evaluated every market. Thresholds read from the spec."""
    n = len(done)
    res: Dict[str, Any] = {"n_markets_done": n, "breaches": [],
                           "halt": False}
    ok = [d for d in done if "VERDICT" not in d]

    # orders never evidenced in the first N markets
    first_n = GATE["orders_never_evidenced_in_first_n_markets"]
    if n >= first_n:
        head = ok[:first_n]
        if head and all(d.get("M1_evidenced_fills", 0) == 0 for d in head):
            res["breaches"].append({
                "condition": "orders_never_evidenced_in_first_%d_markets"
                             % first_n,
                "observed": "0 evidenced fills in each of the first %d"
                            % first_n,
                # the substantive/instrumentation split the brief requires
                "classification": classify_zero_fill(head),
            })
    if n >= first_n:
        share = (sum(1 for d in ok if d.get("M2_two_sided_evidenced_acquisition"))
                 / max(1, len(ok)))
        if share < GATE["two_sided_evidenced_acquisition_markets_below"]:
            res["breaches"].append({
                "condition": "two_sided_evidenced_acquisition_below",
                "threshold": GATE["two_sided_evidenced_acquisition_markets_below"],
                "observed": round(share, 4)})
        unobs = [1 - (d.get("M6_queue_ahead", {}).get("n", 0)
                      / max(1, d.get("n_orders", 1))) for d in ok]
        u = sum(unobs) / len(unobs) if unobs else 0.0
        if u > GATE["queue_ahead_unobservable_share_above"]:
            res["breaches"].append({
                "condition": "queue_ahead_unobservable_share_above",
                "threshold": GATE["queue_ahead_unobservable_share_above"],
                "observed": round(u, 4)})
        st = [d.get("bbo_staleness_breach_share") for d in ok]
        st = [x for x in st if x is not None]
        if st:
            sm = sum(st) / len(st)
            if sm > GATE["bbo_staleness_breaches_share_above"]:
                res["breaches"].append({
                    "condition": "bbo_staleness_breaches_share_above",
                    "threshold": GATE["bbo_staleness_breaches_share_above"],
                    "observed": round(sm, 4)})
    if any(d.get("reservation_stand_downs_violations", 0) > 0 for d in ok):
        res["breaches"].append({"condition": "reservation_violations"})
    if any(d.get("orders_submitted", 0) or d.get("orders_signed", 0)
           for d in ok):
        res["breaches"].append({"condition": "any_order_submitted_or_signed",
                                "SEVERITY": "ABSOLUTE"})
    res["halt"] = bool(res["breaches"])
    res["NOTE"] = ("a breach halts collection and is reported as a "
                   "measurement result. It is NEVER grounds for retuning a "
                   "placement parameter and re-running.")
    return res


def classify_zero_fill(head: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Distinguish a broken instrument from a real zero-fill finding.

    The cohort is NOT restarted or changed either way.
    """
    placed = sum(d.get("n_orders", 0) for d in head)
    snaps = sum(d.get("n_snapshots", 0) for d in head)
    qa = sum(d.get("M6_queue_ahead", {}).get("n", 0) for d in head)
    uncertain = sum(d.get("ledger_counts", {}).get("UNCERTAIN", 0)
                    for d in head)
    rest = [d.get("M6_resting_time_s", {}).get("mean") for d in head]
    rest = [r for r in rest if r is not None]
    no_comp = sum(1 for d in head if d.get("complement_never_observed"))

    resv = sum(d.get("reservation_stand_downs", 0) for d in head)
    reasons = []
    if placed == 0 and resv == 0:
        reasons.append("NO_ORDERS_EVER_PLACED_AND_NO_RESERVATION_BLOCK")
    # Zero orders BECAUSE the frozen 0.96 reservation blocked every attempt
    # is NOT an instrument fault: it is the venue never offering a pair
    # cheap enough. On PMUS, resting at both touches of one slug costs
    # 1 - spread, so 0.96 requires a spread >= 0.04 (8 crypto ticks). That
    # is a substantive economic finding about available execution, and the
    # reservation is NOT to be altered in response to it.
    if snaps == 0:
        reasons.append("NO_BOOK_SNAPSHOTS")
    if placed and qa == 0:
        reasons.append("QUEUE_STATE_NEVER_OBSERVABLE")
    if no_comp == len(head):
        reasons.append("COMPLEMENT_NEVER_OBSERVED_reservation_untestable")
    if rest and max(rest) < 1.0:
        reasons.append("ORDERS_NEVER_RESTED")
    failure = bool(reasons)
    blocked_by_reservation = bool(placed == 0 and resv > 0)
    return {
        "classification": ("INSTRUMENTATION_FAILURE" if failure
                           else "VALID_ZERO_FILL_EVIDENCE"),
        "zero_orders_because_reservation_blocked": blocked_by_reservation,
        "reservation_stand_downs": resv,
        "reservation_note": (
            "pair cost at both PMUS touches is 1 - spread, so the frozen "
            "0.96 reservation requires a spread >= 0.04. If this is why no "
            "order was placed, the finding is that the venue did not offer "
            "a qualifying pair -- NOT that the instrument failed, and NOT a "
            "reason to move the reservation."),
        "reasons": reasons or [
            "orders were placed, rested, and queue state was observable; "
            "zero evidenced fills is then a SUBSTANTIVE result about "
            "available maker execution, not an instrument fault"],
        "orders_placed": placed,
        "snapshots": snaps,
        "orders_with_observable_queue": qa,
        "uncertain_ledger_entries": uncertain,
        "mean_resting_time_s": (round(sum(rest) / len(rest), 3) if rest
                                else None),
        "cohort_restarted_or_extended": False,
        "substantive_reading": (
            "if VALID_ZERO_FILL_EVIDENCE: resting at the touch on PMUS "
            "BTC15m did not obtain evidenced maker execution under the "
            "frozen bounds. That answers S3's question in the negative and "
            "is preserved as a result."),
    }


def main(argv=None) -> int:
    assert_no_execution_surface()
    assert_frozen()
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", nargs="+",
                    help="one or more recorded book tapes (jsonl). Several "
                         "are accepted so repeated collection runs "
                         "accumulate toward the frozen 25-market cohort; "
                         "the spec's cohort size is unchanged.")
    ap.add_argument("--max-markets", type=int, default=COHORT_N)
    ap.add_argument("--outdir", default="analysis/l5/s3")
    ap.add_argument("--out-name", default="S3_COHORT.json")
    a = ap.parse_args(argv)

    if not a.tape:
        print("no --tape given; S3 has no live feed in this environment")
        return 2
    by: Dict[str, List[dict]] = defaultdict(list)
    for path in a.tape:
        for slug, rows in load_tape(path).items():
            by[slug].extend(rows)
    for rows in by.values():
        rows.sort(key=lambda r: str(r.get("t")))
    done: List[Dict[str, Any]] = []
    gate: Dict[str, Any] = {"n_markets_done": 0, "breaches": [], "halt": False}
    for slug in sorted(by):
        if len(done) >= a.max_markets:
            break
        done.append(run_market(slug, by[slug]))
        gate = gate_check(done)
        if gate["halt"]:
            break

    complete = [d for d in done if d.get("market_COMPLETE")]
    out = {
        "spec_fingerprint": S3_FINGERPRINT,
        "markets_COMPLETE": len(complete),
        "cohort_ready_to_evaluate": len(complete) >= COHORT_N,
        "EVALUATION_HELD": (
            "per the frozen protocol the cohort is NOT evaluated before %d "
            "COMPLETE markets unless a catastrophic gate fires" % COHORT_N),
        "spec_commit": "28c619df",
        "is_predictor": SPEC["is_predictor"],
        "zero_capital": True, "orders_submitted": 0, "orders_signed": 0,
        "source": {"kind": "RECORDED_TAPE", "paths": list(a.tape)},
        "market_completeness_criterion": MARKET_COMPLETE_DOC,
        "cohort_required": COHORT_N,
        "cohort_completed": len(done),
        "gate": gate,
        "markout_horizons": list(MARKOUT_HORIZONS),
        "NOT_A_CLAIM_ABOUT_L5": (
            "the 0.96 reservation is OUR pair-economics constraint, not a "
            "reproduction of L5. Running pair-cost control was REFUTED."),
        "markets": done,
    }
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, a.out_name), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print("written %s: %d markets, halt=%s"
          % (a.out_name, len(done), gate["halt"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
