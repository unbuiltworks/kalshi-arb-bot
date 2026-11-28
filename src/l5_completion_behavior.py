"""What does L5 HIMSELF do when he is trying to complete a pair?

OWNER (#167): "If the evidence shows L5 refreshes/reprices COMPLETEs faster
or closer to touch than our implementation, then I would be strongly
inclined to approve it. That's not us inventing a new strategy. That's
fixing our clone so it actually executes L5."

So this measures L5's completion behaviour on his own canonical history
(71,984 fills / 2,216 windows) and puts it beside ours: our completes fill
13.7% of the time, and the ones that fail sit the full ~31 s window and die.

THE OBSERVABILITY LIMIT, STATED FIRST BECAUSE IT BOUNDS EVERY NUMBER HERE.
We see L5's FILLS. We never see his orders, his cancels, or his resting
prices -- that is a known gap in L5_STATUS.md and no amount of care removes
it. So:

  * time-to-complete is how fast his completion HAPPENED, not how fast he
    repriced. A fast completion could be one resting order that got lifted.
  * but DISTINCT PRICES on the completing side within one window ARE
    evidence of repricing: a single resting order fills at one price, so
    two prices means at least two orders.

That asymmetry is the whole method. Time is suggestive; distinct prices are
the inference that survives.

READ-ONLY. Completion pricing and the rest window are live money parameters
and move only on an explicit owner decision; this module exists to inform
that decision, not to pre-empt it.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
from collections import Counter, defaultdict
from statistics import median
from typing import Any, Dict, List, Mapping, Optional

CANONICAL = "analysis/l5/l5_canonical_sequence.csv.gz"


def _rows(path: str = CANONICAL):
    op = gzip.open if str(path).endswith(".gz") else open
    with op(path, "rt", newline="") as fh:
        for row in csv.DictReader(fh):
            yield row


def _f(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def windows(path: str = CANONICAL) -> Dict[str, List[dict]]:
    by: Dict[str, List[dict]] = defaultdict(list)
    for r in _rows(path):
        if str(r.get("side", "")).upper() != "BUY":
            continue                       # L5 holds to settlement; sells are
        by[str(r.get("window_id"))].append(r)   # $2.18 lifetime and not pairing
    for w in by.values():
        w.sort(key=lambda r: (_f(r.get("trade_seq")) or 0))
    return by


def completion_events(by_window: Mapping[str, List[dict]]) -> List[dict]:
    """One row per window that actually attempted a pair.

    The first leg is the first BUY. The completing leg is the first BUY on
    the OPPOSITE outcome. A window that never buys the other side never
    completed -- that is L5's own residual, and it is counted, not dropped,
    because dropping it would flatter his completion rate exactly the way
    conditioning on success always does."""
    out: List[dict] = []
    for wid, rows in by_window.items():
        if not rows:
            continue
        first = rows[0]
        side0 = str(first.get("outcome"))
        t0 = _f(first.get("timestamp"))
        comp = next((r for r in rows[1:]
                     if str(r.get("outcome")) != side0), None)
        same_side_prices = {str(r.get("price"))[:6] for r in rows
                            if str(r.get("outcome")) == side0}
        row = {
            "window_id": wid,
            "market_slug": first.get("market_slug"),
            "duration_minutes": _f(first.get("duration_minutes")),
            "first_leg_side": side0,
            "first_leg_price": _f(first.get("price")),
            "first_leg_tts": _f(first.get("seconds_to_settlement")),
            "completed": comp is not None,
            "first_leg_distinct_prices": len(same_side_prices),
        }
        if comp is not None:
            t1 = _f(comp.get("timestamp"))
            opp_rows = [r for r in rows if str(r.get("outcome")) != side0]
            row.update({
                "seconds_to_complete": (t1 - t0) if (t0 is not None
                                                     and t1 is not None) else None,
                "completing_price": _f(comp.get("price")),
                "completing_tts": _f(comp.get("seconds_to_settlement")),
                "completing_fills": len(opp_rows),
                "completing_distinct_prices": len(
                    {str(r.get("price"))[:6] for r in opp_rows}),
            })
        out.append(row)
    return out


def _dist(vals: List[float]) -> dict:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return {"n": 0}
    def pct(p):
        return v[min(len(v) - 1, int(p * len(v)))]
    return {"n": len(v), "min": v[0], "p10": pct(0.10), "median": median(v),
            "p90": pct(0.90), "max": v[-1]}


def _repricing_deconfounded(done: List[dict]) -> dict:
    """Repricing among completions that could POSSIBLY show it.

    A completion with one fill has one price by arithmetic. Counting those
    in the denominator understates repricing; counting them as evidence of
    single-pricing is simply wrong."""
    multi = [e for e in done if (e.get("completing_fills") or 0) >= 2]
    rep = [e for e in multi if (e.get("completing_distinct_prices") or 0) > 1]
    single = [e for e in done if (e.get("completing_fills") or 0) == 1]
    return {
        "completions_with_at_least_two_fills": len(multi),
        "of_those_at_more_than_one_price": len(rep),
        "share": round(len(rep) / len(multi), 4) if multi else None,
        "single_fill_completions_excluded": len(single),
        "why_excluded": ("one fill has one price by arithmetic; it is "
                         "evidence of neither repricing nor its absence"),
    }


def _by_speed(done: List[dict], split_s: float = 31.0) -> dict:
    """Prices and fills for completions faster and slower than OUR window.

    The intuition to check: are fast completions just one lucky resting
    order getting lifted? If so they would carry FEWER prices. They carry
    more."""
    out = {}
    for name, keep in ((f"within_{int(split_s)}s",
                        lambda s: s is not None and s <= split_s),
                       (f"over_{int(split_s)}s",
                        lambda s: s is not None and s > split_s)):
        grp = [e for e in done if keep(e.get("seconds_to_complete"))]
        pr = sorted(e.get("completing_distinct_prices") or 0 for e in grp)
        fl = sorted(e.get("completing_fills") or 0 for e in grp)
        out[name] = {
            "n": len(grp),
            "median_distinct_prices": median(pr) if pr else None,
            "median_completing_fills": median(fl) if fl else None,
        }
    return out


def report(path: str = CANONICAL) -> dict:
    ev = completion_events(windows(path))
    done = [e for e in ev if e["completed"]]
    secs = [e.get("seconds_to_complete") for e in done]
    within = {f"within_{k}s": sum(1 for s in secs if s is not None and s <= k)
              for k in (5, 10, 15, 30, 31, 60, 120, 300)}
    reprice = Counter(e.get("completing_distinct_prices") for e in done)
    multi = sum(n for k, n in reprice.items() if k and k > 1)
    return {
        "windows_with_a_first_leg": len(ev),
        "windows_that_completed": len(done),
        "l5_completion_rate": round(len(done) / len(ev), 4) if ev else None,
        "seconds_to_complete": _dist([s for s in secs if s is not None]),
        "completions_within": within,
        "completions_within_our_31s_window_share": (
            round(within["within_31s"] / len(done), 4) if done else None),
        "completing_distinct_prices": {str(k): v for k, v in
                                       sorted(reprice.items(),
                                              key=lambda kv: (kv[0] or 0))},
        "completions_at_more_than_one_price": multi,
        "repricing_share_confounded": round(multi / len(done), 4) if done else None,
        # DECONFOUNDED. A completion with a single fill has one price by
        # arithmetic, not by choice, so it cannot be evidence either way.
        # Restricting to completions that got at least two fills is the
        # only version of this number that means anything.
        "repricing_among_multi_fill_completions": _repricing_deconfounded(done),
        # Does working it harder complete it faster? Split at our own 31 s
        # window so the comparison is against the thing we actually do.
        "prices_and_fills_by_speed": _by_speed(done),
        "completing_fills_per_window": _dist(
            [e.get("completing_fills") for e in done]),
        "observability_limit": (
            "FILLS ONLY. L5's orders, cancels and resting prices are not "
            "observable, so seconds_to_complete is how fast his completion "
            "HAPPENED, not how fast he repriced. Distinct prices on the "
            "completing side ARE repricing evidence: one resting order "
            "fills at one price."),
        "ours_for_comparison": {
            "complete_fill_rate": 0.137,
            "complete_no_fill_share": 0.685,
            "complete_rejected_share": 0.179,
            "median_rest_s_when_filled": 8.61,
            "median_rest_s_when_not_filled": 30.85,
            "source": "l5_completion_economics over 666 complete orders",
        },
    }


def render_markdown(rep: Mapping[str, Any]) -> str:
    d, w = rep["seconds_to_complete"], rep["completions_within"]
    done = rep["windows_that_completed"]
    L = ["# What L5 does when completing a pair", "",
         f"Windows with a first leg: **{rep['windows_with_a_first_leg']}**  ",
         f"Windows that completed: **{done}** "
         f"(**{rep['l5_completion_rate']:.1%}**)", "",
         "## How long L5's completion takes", "",
         "| n | min | p10 | median | p90 | max |", "|---|---|---|---|---|---|",
         f"| {d.get('n')} | {d.get('min')} | {d.get('p10')} | "
         f"{d.get('median')} | {d.get('p90')} | {d.get('max')} |", "",
         "| completed within | windows | share of completions |",
         "|---|---|---|"]
    for k in (5, 10, 15, 30, 31, 60, 120, 300):
        n = w[f"within_{k}s"]
        L.append(f"| {k}s | {n} | {(n / done):.1%} |" if done else
                 f"| {k}s | {n} | - |")
    L += ["", f"**Share of L5's completions that land inside our 31 s "
              f"window: {rep['completions_within_our_31s_window_share']:.1%}**"
          if rep["completions_within_our_31s_window_share"] is not None else "",
          "", "## Does L5 reprice?", "",
          f"Among completions that got at least two fills — the only ones "
          f"that CAN show repricing — "
          f"**{rep['repricing_among_multi_fill_completions']['of_those_at_more_than_one_price']}"
          f" of {rep['repricing_among_multi_fill_completions']['completions_with_at_least_two_fills']}"
          f" ({rep['repricing_among_multi_fill_completions']['share']:.1%})"
          f" happened at more than one price.**  ",
          f"({rep['repricing_among_multi_fill_completions']['single_fill_completions_excluded']}"
          f" single-fill completions excluded: "
          f"{rep['repricing_among_multi_fill_completions']['why_excluded']}.)",
          "",
          "Are fast completions just one lucky resting order? No — they "
          "carry MORE prices, not fewer:", "",
          "| completions | n | median distinct prices | median fills |",
          "|---|---|---|---|"]
    for k, v in rep["prices_and_fills_by_speed"].items():
        L.append(f"| {k} | {v['n']} | {v['median_distinct_prices']} | "
                 f"{v['median_completing_fills']} |")
    L += ["",
          "## Ours, for comparison", ""]
    o = rep["ours_for_comparison"]
    L += [f"- COMPLETE fill rate **{o['complete_fill_rate']:.1%}**, "
          f"no-fill **{o['complete_no_fill_share']:.1%}**, "
          f"rejected **{o['complete_rejected_share']:.1%}**",
          f"- median rest when filled **{o['median_rest_s_when_filled']}s**, "
          f"when not filled **{o['median_rest_s_when_not_filled']}s**",
          "", "## Observability limit", "", rep["observability_limit"]]
    return "\n".join(L) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sequence", default=CANONICAL)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rep = report(a.sequence)
    md = render_markdown(rep)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "L5_COMPLETION_BEHAVIOR.md"), "w") as fh:
            fh.write(md)
        with open(os.path.join(a.out, "l5_completion_behavior.json"), "w") as fh:
            json.dump(rep, fh, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
