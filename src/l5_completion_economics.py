"""Why COMPLETE legs fail, on the right denominator.

THE ERROR THIS MODULE EXISTS TO PREVENT. 43 of 70 book-carrying rejections
are COMPLETE legs, and I read that as "rejection is why completions fail".
It is not: it is a fact about the composition of REJECTIONS. On the right
denominator -- all 666 complete orders -- 68.5% die unfilled and only 17.9%
are rejected. Conditioning on failure and then reasoning about causes is
the same mistake twice in one afternoon, so the outcome table here is
always computed over ALL orders of a relation, never over a failure subset.

WHY IT MATTERS MORE THAN ANY OTHER METRIC RIGHT NOW. The settled cohort
splits cleanly: markets that paired to zero returned +$6.3639 across 12
markets; the 4 that stranded residual returned -$9.2384 with no winners and
a confidence interval excluding zero. Residual is the only thing losing
money, and a COMPLETE that does not fill is how residual is created. So
"why don't completes fill" IS the profitability question.

WHAT IT REFUSES TO ANSWER WITHOUT EVIDENCE. Whether our resting completes
are priced too far from the touch needs the opposite ask AT ORDER TIME.
That was recorded only on rejections until _control_book shipped, so
`distance_from_touch` returns UNAVAILABLE with the reason rather than
substituting the rejection-only sample -- which is precisely the biased
sample this module was written to stop using.

READ-ONLY. No pricing rule, no rest window, no gate. Completion pricing and
the rest window are live money parameters and move only on an owner
decision.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from statistics import median
from typing import Any, Iterable, List, Mapping, Optional

RELATIONS = ("open", "extend", "complete")


def _messages(path: Any) -> List[str]:
    try:
        blob = open(path, "rt", encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    try:
        doc = json.loads(blob)
    except ValueError:
        return [blob]
    rows = doc.get("deploy") if isinstance(doc, Mapping) else None
    if not isinstance(rows, list):
        return [blob]
    return [str(r.get("message") or "") for r in rows if isinstance(r, Mapping)]


def _objects(blob: str, marker: str) -> List[dict]:
    out: List[dict] = []
    for chunk in blob.split(marker)[1:]:
        start = chunk.find("{")
        if start < 0:
            continue
        depth, end, in_str, esc = 0, None, False, False
        for i in range(start, len(chunk)):
            ch = chunk[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            continue
        try:
            obj = json.loads(chunk[start:end])
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def all_orders(paths: Iterable[Any]) -> List[dict]:
    """EVERY submitted order, deduplicated. The denominator."""
    seen, out = set(), []
    for p in paths:
        for msg in _messages(p):
            if "session_end" not in msg:
                continue
            for rec in _objects(msg, "PMUS_L5_CAMPAIGN "):
                for o in (rec.get("orders") or []):
                    if not isinstance(o, Mapping):
                        continue
                    key = (o.get("order_id"), o.get("stage"))
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({**o, "slug": rec.get("slug")})
    return out


def outcome_of(order: Mapping[str, Any]) -> str:
    if str(order.get("end_reason") or "").startswith("rejected"):
        return "rejected"
    got = order.get("filled_qty")
    if got not in (None, "0", "0.00", "0.0000"):
        return "filled"
    return "no_fill_cancelled"


def outcomes_by_relation(orders: Iterable[Mapping[str, Any]]) -> dict:
    by: dict = defaultdict(Counter)
    for o in orders:
        by[str(o.get("relation") or "?")][outcome_of(o)] += 1
    out = {}
    for rel, c in by.items():
        n = sum(c.values())
        out[rel] = {"orders": n, **{k: c.get(k, 0) for k in
                                    ("filled", "no_fill_cancelled",
                                     "rejected")},
                    "fill_rate": round(c.get("filled", 0) / n, 4) if n else None,
                    "no_fill_share": round(c.get("no_fill_cancelled", 0) / n, 4)
                    if n else None,
                    "rejected_share": round(c.get("rejected", 0) / n, 4)
                    if n else None}
    return out


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def fill_profile(orders: Iterable[Mapping[str, Any]],
                 relation: str = "complete") -> dict:
    """Resting time and price of the orders that filled vs those that did
    not, for one relation. Rejections are excluded -- they never rested."""
    rows = [o for o in orders if o.get("relation") == relation
            and outcome_of(o) != "rejected"]
    out: dict = {"relation": relation, "orders": len(rows)}
    for bucket in ("filled", "no_fill_cancelled"):
        sel = [o for o in rows if outcome_of(o) == bucket]
        rest = [x for x in (_f(o.get("rest_s")) for o in sel) if x is not None]
        px = [x for x in (_f(o.get("px")) for o in sel) if x is not None]
        out[bucket] = {
            "n": len(sel),
            "median_rest_s": round(median(rest), 2) if rest else None,
            "median_px": round(median(px), 4) if px else None,
        }
    return out


def distance_from_touch(orders: Iterable[Mapping[str, Any]],
                        relation: str = "complete") -> dict:
    """Fill rate as a function of how far our resting price sat from the
    opposite ask AT ORDER TIME.

    Needs `fresh_opp_ask` on orders the venue ACCEPTED. Before
    _control_book that field existed only on rejections, and answering
    from that sample would reintroduce the selection bias this module
    exists to avoid -- so with no control rows it returns UNAVAILABLE and
    says why."""
    rows = [o for o in orders if o.get("relation") == relation
            and outcome_of(o) != "rejected"
            and o.get("fresh_opp_ask") is not None]
    if not rows:
        return {"available": False,
                "reason": ("no accepted order carries fresh_opp_ask yet; "
                           "the control snapshot has not accumulated. "
                           "Answering from rejection rows would be the "
                           "selection bias this module refuses."),
                "orders_with_a_touch": 0}
    buckets: dict = defaultdict(Counter)
    for o in rows:
        px, opp = _f(o.get("px")), _f(o.get("fresh_opp_ask"))
        if px is None or opp is None:
            continue
        ticks = int(round((opp - px) * 100))
        buckets[max(-1, min(ticks, 10))][outcome_of(o)] += 1
    table = {}
    for t in sorted(buckets):
        c = buckets[t]
        n = sum(c.values())
        table[t] = {"orders": n, "filled": c.get("filled", 0),
                    "fill_rate": round(c.get("filled", 0) / n, 4)}
    return {"available": True, "orders_with_a_touch": len(rows),
            "fill_rate_by_ticks_inside_the_opposite_ask": table,
            "note": ("ticks = opposite_ask - our_price, clipped to [-1, 10]. "
                     "A fill rate that collapses with distance means the "
                     "completion bound, not the venue, is costing us pairs.")}


def report(paths: Iterable[Any]) -> dict:
    orders = all_orders(paths)
    return {
        "orders_total": len(orders),
        "outcomes_by_relation": outcomes_by_relation(orders),
        "complete_fill_profile": fill_profile(orders, "complete"),
        "complete_distance_from_touch": distance_from_touch(orders, "complete"),
        "why_this_denominator": (
            "Computed over ALL orders of a relation. Reading the "
            "composition of rejections as the composition of completion "
            "failures said rejection was the problem; on this denominator "
            "68.5% of completes die unfilled and 17.9% are rejected."),
    }


def render_markdown(rep: Mapping[str, Any]) -> str:
    L = ["# Why COMPLETE legs fail", "",
         f"Orders in sample: **{rep['orders_total']}**", "",
         "| relation | orders | filled | no-fill | rejected |",
         "|---|---|---|---|---|"]
    for rel in RELATIONS:
        r = rep["outcomes_by_relation"].get(rel)
        if not r:
            continue
        L.append(f"| {rel} | {r['orders']} | {r['filled']} "
                 f"({r['fill_rate']:.1%}) | {r['no_fill_cancelled']} "
                 f"({r['no_fill_share']:.1%}) | {r['rejected']} "
                 f"({r['rejected_share']:.1%}) |")
    p = rep["complete_fill_profile"]
    L += ["", "## Completes that filled vs completes that did not", "",
          "| bucket | n | median rest_s | median px |", "|---|---|---|---|"]
    for b in ("filled", "no_fill_cancelled"):
        v = p[b]
        L.append(f"| {b} | {v['n']} | {v['median_rest_s']} | {v['median_px']} |")
    d = rep["complete_distance_from_touch"]
    L += ["", "## Fill rate by distance from the opposite ask", ""]
    if not d["available"]:
        L.append(f"**UNAVAILABLE** — {d['reason']}")
    else:
        L += [f"Orders with a touch recorded: {d['orders_with_a_touch']}", "",
              "| ticks inside | orders | filled | fill rate |",
              "|---|---|---|---|"]
        for t, v in d["fill_rate_by_ticks_inside_the_opposite_ask"].items():
            L.append(f"| {t} | {v['orders']} | {v['filled']} | "
                     f"{v['fill_rate']:.1%} |")
        L += ["", d["note"]]
    L += ["", rep["why_this_denominator"]]
    return "\n".join(L) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    paths = [os.path.join(a.logs, f) for f in sorted(os.listdir(a.logs))] \
        if os.path.isdir(a.logs) else [a.logs]
    rep = report(paths)
    md = render_markdown(rep)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "COMPLETION_ECONOMICS.md"), "w") as fh:
            fh.write(md)
        with open(os.path.join(a.out, "completion_economics.json"), "w") as fh:
            json.dump(rep, fh, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
