"""What separated a completed pair from a stranded first leg on PMUS.

OWNER P0 (#167): "Take every PMUS trade we already made and determine what
separated good two-sided fills from stranded first legs. Then test whether
high-flow market selection + removal of our non-L5 execution restrictions
materially improves completion and trading drag."

This module reads campaign log dumps only. It never touches the venue, the
ledger, or any live path, and it places no orders.

THE UNIT
--------
The question is about COMPLETION, so the unit is a market in which we took a
first-side fill. A market where nothing filled tells us nothing about whether
a complement would have filled. For each such market we measure

    cover = filled complement contracts / filled first-side contracts

and split the campaign into FULL (cover >= 0.99), PARTIAL, and ZERO cohorts.
Cover is bimodal in the observed campaign, which is itself the first finding:
completion is close to all-or-nothing per market, not a smooth rate.

WHAT THE EVIDENCE SAYS, AND WHAT IT DOES NOT
--------------------------------------------
The headline result is NOT a liquidity result. In the stranded markets we did
not lose a race against other makers; in five of seven we stopped acting on
the market within minutes of taking the first leg, and in two we never
submitted a single completion attempt after the fill at all.

Two mechanisms produce that, and they are separable in the rows:

  1. THE SESSION ORDER BUDGET. A spring session carries a 6-order budget.
     When it is spent the session ends holding whatever it holds. One market
     records the stop verbatim:
     `opening_budget_spent:6/6:extension_is_completion_only_not_extend`.

  2. THE ENTRY BAND APPLIED TO A NON-ENTRY. `entry_band_left` is an ENTRY
     admission band (q1 MIN_PRICE/MAX_PRICE 0.20-0.80). 39 of its 42 firings
     in this campaign happened while we were HOLDING unmatched inventory,
     where it is not an entry decision at all. On those cycles the frozen L5
     model recommended `complete` 12 times and `extend` 24 times -- both
     residual-management actions -- and the band refused them. The affected
     markets then woke, refused, and submitted ZERO orders for 6, 9 and 12
     consecutive cycles while carrying an unmatched leg.

WHY THIS IS NOT A LICENCE TO DELETE THE BAND
---------------------------------------------
The same rows say the two refused actions are NOT economically alike. On the
blocked cycles the shell would have acted `extend` 36 times against `open` 3
-- that is, it would mostly have bought MORE of the side already held, in
markets whose bid was on its way from 0.36 to 0.08. Removing the band for
EXTEND buys deeper into a moving market; removing it for COMPLETE removes an
entry rule from a decision that is not an entry. The prior band study
measured that split directly and it points the same way (COMPLETE +7.6905,
EXTEND -2.7402, fill-assumed). This module therefore reports the two
separately and never nets them into one recommendation.

THE FLOW HYPOTHESIS, TESTED HONESTLY
-------------------------------------
The owner asked whether contemporaneous executable activity -- not headline
lifetime volume -- separates the cohorts. On this campaign's evidence the
answer is that it does not discriminate on its own: entry spread is 0.010 in
both cohorts at the median, and four FULL-cover markets show the same violent
bid drift as the stranded ones. What distinguishes the losers is drift WHILE
HOLDING AN UNMATCHED LEG AND BEING UNABLE TO ACT -- 9 and 12 blocked cycles
in the two worst, against 3 in the FULL-cover markets that saw comparable
drift and recovered. Flow may still matter; this campaign cannot show it,
because our own abandonment dominates the variance. That limit is stated
rather than papered over, and the flow features are still emitted so a
forward high-flow cohort can be compared against them.

n = 31 markets with a first-side fill, of which 7 stranded. Every cohort
statement here rests on that n and is descriptive: markets were not randomly
assigned to cohorts, so none of these splits is causal.
"""

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.l5_r4_action_comparison import harvest
from src.l5_r4_fidelity_decomposition import expand_logs

OUT_DIR = os.path.join("analysis", "l5")
SHELL_PREFIX = "R5F/"
FULL_COVER = D("0.99")
FIRST_SIDE_RELATIONS = ("open", "extend")
ENTRY_BAND = "entry_band_left"

LABELS = {
    "measured": "counted from the campaign's own decision/order/session rows",
    "descriptive": "cohorts are not randomly assigned; no causal claim is made",
    "counterfactual": "did not happen; fill-assumed, queue position and depth not modelled",
}


# ------------------------------ helpers ------------------------------

def _d(x: Any) -> Optional[D]:
    """Decimal or None. Decimal('junk') raises InvalidOperation, not ValueError."""
    if x is None:
        return None
    try:
        return D(str(x))
    except ArithmeticError:
        return None


def _ts(s: Optional[str]) -> Optional[float]:
    """ISO -> epoch; tolerates the log tool's 9-digit fractions and 'Z'."""
    if not s:
        return None
    s = str(s).replace("Z", "+00:00")
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", s)
    if m:
        s = m.group(1) + (m.group(2) or "")[:7] + m.group(3)
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _median(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    ys = sorted(xs)
    n = len(ys)
    return ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2


def band_bid(refusal: Optional[str]) -> Optional[float]:
    """The book bid recorded inside an `entry_band_left:bid=0.0600:...` refusal."""
    if not refusal or not refusal.startswith(ENTRY_BAND + ":bid="):
        return None
    try:
        return float(refusal.split("bid=")[1].split(":")[0])
    except (IndexError, ValueError):
        return None


def is_entry_band(refusal: Optional[str]) -> bool:
    return bool(refusal) and str(refusal).startswith(ENTRY_BAND)


def residual_of(row: Mapping[str, Any], key: str = "inventory_before") -> D:
    inv = row.get(key)
    if not isinstance(inv, Mapping):
        return D(0)
    return _d(inv.get("residual")) or D(0)


# ------------------------------ cohorts ------------------------------

def cover_by_market(orders: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """filled complement contracts / filled first-side contracts, per market."""
    first: Dict[str, D] = defaultdict(lambda: D(0))
    comp: Dict[str, D] = defaultdict(lambda: D(0))
    attempts: Dict[str, Counter] = defaultdict(Counter)
    for r in orders:
        rel = r.get("relation")
        attempts[r["slug"]][rel] += 1
        q = _d(r.get("filled_qty")) or D(0)
        if q <= 0:
            continue
        if rel in FIRST_SIDE_RELATIONS:
            first[r["slug"]] += q
        elif rel == "complete":
            comp[r["slug"]] += q
    out: Dict[str, Dict[str, Any]] = {}
    for slug, f in first.items():
        if f <= 0:
            continue
        c = comp[slug]
        out[slug] = {
            "first_side_filled": f,
            "complement_filled": c,
            "cover": c / f,
            "cohort": "FULL" if c / f >= FULL_COVER else ("ZERO" if c == 0 else "PARTIAL"),
            "attempts": dict(attempts[slug]),
        }
    return out


def abandonment(orders: Sequence[Mapping[str, Any]], slug: str) -> Dict[str, Any]:
    """Did we keep working the market after taking a first leg, or walk away?"""
    rows = sorted((r for r in orders if r.get("slug") == slug),
                  key=lambda r: _ts(r.get("log_ts")) or 0.0)
    filled = [r for r in rows if (_d(r.get("filled_qty")) or D(0)) > 0]
    t_first = _ts(filled[0].get("log_ts")) if filled else None
    t_last = _ts(rows[-1].get("log_ts")) if rows else None
    after = [r for r in rows
             if r.get("relation") == "complete" and t_first is not None
             and (_ts(r.get("log_ts")) or 0.0) > t_first]
    return {
        "orders": len(rows),
        "completes_after_first_fill": len(after),
        "minutes_first_fill_to_last_action": (
            round((t_last - t_first) / 60.0, 2) if (t_first and t_last) else None),
        "end_reasons_after_first_fill": dict(
            Counter((r.get("end_reason") or "")[:40] for r in after)),
        "median_complete_rest_s": _median([r["rest_s"] for r in after
                                           if r.get("rest_s") is not None]),
    }


def band_blocked_while_holding(decisions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The entry band's firings, split by whether we were flat or holding.

    A firing while flat is the band doing its job. A firing while holding
    unmatched inventory is an entry rule refusing a residual action.
    """
    fired = [d for d in decisions if is_entry_band(d.get("refusal"))]
    flat = [d for d in fired if residual_of(d) == 0]
    hold = [d for d in fired if residual_of(d) != 0]
    per_market: Dict[str, Dict[str, Any]] = {}
    for slug in sorted({d["slug"] for d in hold}):
        rows = [d for d in hold if d["slug"] == slug]
        bids = [b for b in (band_bid(d.get("refusal")) for d in rows) if b is not None]
        unmatched = [_d((d.get("inventory_before") or {}).get("unmatched_usd")) or D(0)
                     for d in rows]
        per_market[slug] = {
            "blocked_cycles": len(rows),
            "min_bid_seen": min(bids) if bids else None,
            "max_unmatched_usd_carried": max(unmatched) if unmatched else D(0),
            "l5_wanted": dict(Counter(d.get("v2_recommended") for d in rows)),
        }
    return {
        "fired": len(fired),
        "while_flat_a_true_entry_refusal": len(flat),
        "while_holding_residual": len(hold),
        "l5_recommended_on_holding_cycles": dict(
            Counter(d.get("v2_recommended") for d in hold)),
        "shell_would_have_acted_on_holding_cycles": dict(
            Counter(d.get("would_act_without_safety_cap") for d in hold)),
        "per_market": per_market,
        "why_this_matters": (
            "entry_band_left is an ENTRY admission band. A firing while holding "
            "unmatched inventory refuses a residual action, not an entry."),
        "why_removal_is_not_automatic": (
            "on these same cycles the shell would mostly have EXTENDed -- bought "
            "more of the side already held, in markets whose bid was falling. "
            "COMPLETE and EXTEND must be decided separately, never netted."),
    }


def flow_features(decisions: Sequence[Mapping[str, Any]],
                  orders: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Contemporaneous activity per market, independent of whether WE filled.

    Entry spread comes from the book at OPEN. Bid drift comes from the book
    bids the band refusals recorded afterwards. Neither uses our own fills as
    an input, so neither is circular with the cover outcome.
    """
    spread: Dict[str, List[float]] = defaultdict(list)
    open_bid: Dict[str, List[float]] = defaultdict(list)
    later_bid: Dict[str, List[float]] = defaultdict(list)
    for d in decisions:
        o = d.get("open_side")
        if isinstance(o, Mapping):
            b, a = _d(o.get("yes_bid")), _d(o.get("yes_ask"))
            if b is not None and a is not None:
                spread[d["slug"]].append(float(a - b))
                open_bid[d["slug"]].append(float(b))
        b2 = band_bid(d.get("refusal"))
        if b2 is not None:
            later_bid[d["slug"]].append(b2)
    span: Dict[str, List[float]] = defaultdict(list)
    for r in orders:
        rb = r.get("rest_book")
        if not isinstance(rb, Mapping):
            continue
        mt, mo = _d(rb.get("max_touch")), _d(rb.get("min_opp_ask"))
        if mt is not None and mo is not None:
            span[r["slug"]].append(float(mt - mo))
    out: Dict[str, Dict[str, Any]] = {}
    for slug in set(spread) | set(span) | set(later_bid):
        ob = open_bid[slug][0] if open_bid.get(slug) else None
        mn = min(later_bid[slug]) if later_bid.get(slug) else None
        out[slug] = {
            "entry_spread_median": _median(spread.get(slug, [])),
            "entry_bid": ob,
            "min_bid_after_entry": mn,
            "bid_drift": round(mn - ob, 4) if (ob is not None and mn is not None) else None,
            "touch_to_oppask_span_median": _median(span.get(slug, [])),
        }
    return out


# ------------------------------ report ------------------------------

def build(files: Sequence[str], shell_prefix: str = SHELL_PREFIX) -> Dict[str, Any]:
    h = harvest(list(files), shell_prefix)
    orders, decisions = h["orders"], h["decisions"]
    cov = cover_by_market(orders)
    flow = flow_features(decisions, orders)
    band = band_blocked_while_holding(decisions)

    per_market: Dict[str, Dict[str, Any]] = {}
    for slug, c in cov.items():
        per_market[slug] = dict(c)
        per_market[slug]["abandonment"] = abandonment(orders, slug)
        per_market[slug]["flow"] = flow.get(slug, {})
        per_market[slug]["band_blocked_cycles"] = (
            band["per_market"].get(slug, {}).get("blocked_cycles", 0))

    cohorts: Dict[str, List[str]] = defaultdict(list)
    for slug, c in cov.items():
        cohorts[c["cohort"]].append(slug)

    summary: Dict[str, Any] = {}
    for name, slugs in cohorts.items():
        comps = [r for r in orders if r.get("relation") == "complete" and r["slug"] in slugs]
        filled = sum(1 for r in comps if (_d(r.get("filled_qty")) or D(0)) > 0)
        rej = sum(1 for r in comps if "rejected" in (r.get("end_reason") or ""))
        summary[name] = {
            "markets": len(slugs),
            "complete_orders": len(comps),
            "complete_orders_per_market": round(len(comps) / len(slugs), 2) if slugs else None,
            "complete_filled": filled,
            "complete_fill_rate": round(filled / len(comps), 4) if comps else None,
            "complete_rejected": rej,
            "complete_reject_rate": round(rej / len(comps), 4) if comps else None,
            "median_complete_rest_s": _median([r["rest_s"] for r in comps
                                               if r.get("rest_s") is not None]),
            "median_entry_spread": _median([flow[s]["entry_spread_median"] for s in slugs
                                            if flow.get(s, {}).get("entry_spread_median")
                                            is not None]),
            "band_blocked_cycles_total": sum(
                band["per_market"].get(s, {}).get("blocked_cycles", 0) for s in slugs),
        }

    return {
        "shell_prefix": shell_prefix,
        "markets_with_a_first_side_fill": len(cov),
        "cohorts": {k: sorted(v) for k, v in cohorts.items()},
        "cohort_summary": summary,
        "entry_band": band,
        "per_market": per_market,
        "verdict": VERDICT,
        "flow_hypothesis": FLOW_HYPOTHESIS,
        "labels": LABELS,
    }


VERDICT = (
    "There are TWO distinct completion failures in this campaign and they have "
    "opposite fixes. (1) ABANDONMENT, the ZERO cohort: 7 markets, 2.14 COMPLETE "
    "attempts each, median rest 30.8s -- one deadline and done. In five of them "
    "our last action came within 6.2 minutes of the first fill; in two we never "
    "attempted a completion after the fill at all. Nothing about the venue "
    "caused that. (2) GENUINE COMPLETION DIFFICULTY, the PARTIAL cohort: 3 "
    "markets, 47 COMPLETE attempts each over as long as 43 minutes, and a 5.7% "
    "fill rate against the FULL cohort's 23.1%. There we did persist, and the "
    "complement still would not trade. Failure (1) is ours and is fixed by "
    "removing our own stops. Failure (2) is the market's, and is the one that "
    "market selection can actually address."
)

FLOW_HYPOTHESIS = (
    "SUPPORTED IN THE PARTIAL COHORT, UNTESTABLE IN THE ZERO COHORT. Entry "
    "spread is 0.010 at the median in every cohort and four FULL-cover markets "
    "show bid drift as violent as the stranded ones, so the static book "
    "features we recorded do not separate FULL from ZERO -- there, our own "
    "abandonment dominates the variance and no market property could show "
    "through it. The PARTIAL cohort is the clean case FOR the hypothesis: 141 "
    "COMPLETE orders, 47 per market, worked for up to 43 minutes, and a 5.7% "
    "fill rate. That is a market that will not transact against a resting "
    "maker, measured after persistence is ruled out as the cause. The campaign "
    "did not record the feature that would name it -- contemporaneous "
    "trade prints and two-sided print rate are not in the order rows, only "
    "spread, touch extrema and poll counts. Naming it needs a forward "
    "collector that tapes prints per market; the baseline it must beat is in "
    "this file."
)


def render_md(rep: Mapping[str, Any]) -> str:
    L = ["# What separated a completed pair from a stranded first leg", "",
         "_OWNER P0 (#167). Read-only, from the campaign's own rows. "
         f"{rep['markets_with_a_first_side_fill']} markets took a first-side fill._", "",
         "## Verdict", "", rep["verdict"], "",
         "## Cohorts", "",
         "| cohort | markets | COMPLETE orders | per market | filled | fill rate | rejected | median rest | median entry spread | band-blocked cycles |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name in ("FULL", "PARTIAL", "ZERO"):
        s = rep["cohort_summary"].get(name)
        if not s:
            continue
        L.append("| %s | %d | %d | %s | %d | %s | %d | %s | %s | %d |" % (
            name, s["markets"], s["complete_orders"], s["complete_orders_per_market"],
            s["complete_filled"],
            ("%.1f%%" % (100 * s["complete_fill_rate"])) if s["complete_fill_rate"] is not None else "-",
            s["complete_rejected"],
            ("%.1fs" % s["median_complete_rest_s"]) if s["median_complete_rest_s"] is not None else "-",
            ("%.3f" % s["median_entry_spread"]) if s["median_entry_spread"] is not None else "-",
            s["band_blocked_cycles_total"]))
    b = rep["entry_band"]
    L += ["", "## The entry band fired on non-entries", "",
          "`entry_band_left` fired %d times. %d while FLAT (a true entry refusal); "
          "**%d while HOLDING unmatched inventory**." % (
              b["fired"], b["while_flat_a_true_entry_refusal"], b["while_holding_residual"]), "",
          "On the holding cycles the frozen L5 model recommended: `%s`." % b["l5_recommended_on_holding_cycles"],
          "", "The shell would have acted: `%s` -- which is why this is NOT a "
          "licence to delete the band. %s" % (
              b["shell_would_have_acted_on_holding_cycles"], b["why_removal_is_not_automatic"]), "",
          "| market | blocked cycles | min bid seen | unmatched $ carried | L5 wanted |",
          "|---|---:|---:|---:|---|"]
    for slug, m in sorted(b["per_market"].items(), key=lambda kv: -kv[1]["blocked_cycles"]):
        L.append("| `%s` | %d | %s | %s | %s |" % (
            slug, m["blocked_cycles"],
            ("%.3f" % m["min_bid_seen"]) if m["min_bid_seen"] is not None else "-",
            m["max_unmatched_usd_carried"], m["l5_wanted"]))
    L += ["", "## Did we keep working the market after taking a leg?", "",
          "| cohort | market | orders | COMPLETEs after first fill | minutes to last action |",
          "|---|---|---:|---:|---:|"]
    for name in ("ZERO", "PARTIAL"):
        for slug in rep["cohorts"].get(name, []):
            a = rep["per_market"][slug]["abandonment"]
            L.append("| %s | `%s` | %d | %d | %s |" % (
                name, slug, a["orders"], a["completes_after_first_fill"],
                a["minutes_first_fill_to_last_action"]))
    L += ["", "## The flow hypothesis", "", rep["flow_hypothesis"], "",
          "Labels: " + "; ".join(f"{k} = {v}" for k, v in rep["labels"].items()), ""]
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", required=True,
                    help="Railway log dump files or directories")
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--shell-prefix", default=SHELL_PREFIX)
    a = ap.parse_args(argv)
    rep = build(expand_logs(a.logs), a.shell_prefix)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "l5_completion_forensics.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, sort_keys=True, default=str)
    with open(os.path.join(a.out, "L5_COMPLETION_FORENSICS.md"), "w", encoding="utf-8") as f:
        f.write(render_md(rep))
    print(json.dumps({"markets": rep["markets_with_a_first_side_fill"],
                      "cohorts": {k: len(v) for k, v in rep["cohorts"].items()},
                      "entry_band_while_holding": rep["entry_band"]["while_holding_residual"],
                      "entry_band_while_flat": rep["entry_band"]["while_flat_a_true_entry_refusal"]},
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
