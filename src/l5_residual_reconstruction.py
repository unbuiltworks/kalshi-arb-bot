"""Post-imbalance decision states, reconstructed without settlement hindsight.

OWNER 2026-09-11: "reconstruct them without settlement hindsight, beginning at
the first point each market became imbalanced. For each subsequent decision
state, evaluate what was actually knowable and executable then: COMPLETE,
economically reparative EXTEND, WAIT/requote, or no safe action."

Governed by analysis/l5/RESIDUAL_REPAIR_PREREGISTRATION.md, committed at
dd2e2637 BEFORE this module ran. Every field this reads is listed there as
knowable; the settlement outcome, resolution side and realized_usd are named
there as forbidden inputs and are not read here at all -- not to choose and
not to score. This module answers "what was available", which is a question
about the past that the future cannot inform.

WHAT MAKES THE BOOK RECOVERABLE. An OPEN or EXTEND price is computed from the
BBO touch by construction:

    px = bbo["bid"] if side_up else (ONE - bbo["ask"])

so the submitted price on those orders IS the touch at that instant, and the
order rows give us the book without a separate tape. COMPLETE carries its own
book directly -- `complement_preflight.opp_ask` and
`counterfactual_l5_price.l5_touch_px`.

THE FLOOR IS THE MEASURE, NOT THE RESOLUTION. A position's guaranteed
settlement floor is min(up, dn) - spent: what it is worth if the side you
hold least wins. Completing a residual at price p moves that floor in a way
you can compute at the time. Judging a past choice by which side actually
won would be hindsight wearing a metric's clothes, so nothing here does it.
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

ONE = D("1")
ACTING = ("open", "extend", "complete")
# the standing ceiling these markets were traded under and are judged by
PAIR_CEILING = D("1.00")


def _d(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                  # noqa: BLE001
        return None


# ------------------------------------------------------------- economics
def floor_now(inv: Mapping[str, Any]) -> Optional[D]:
    """min(up, dn) - spent: the position's value if the side we hold least
    is the side that wins. Knowable at the time, by definition."""
    up, dn, spent = _d(inv.get("up")), _d(inv.get("dn")), _d(inv.get("spent"))
    if up is None or dn is None or spent is None:
        return None
    return min(up, dn) - spent


def complete_at(inv: Mapping[str, Any], px: D) -> Optional[dict]:
    """What a full COMPLETE of the residual at `px` would do, priced with
    only what was on the screen: the cash it costs, the floor before and
    after, and the matched pair cost it would leave.

    Returns None when there is no residual to complete -- a flat position
    has no completion, and reporting one as a zero-cost opportunity would
    invent an option that did not exist."""
    up, dn, spent = _d(inv.get("up")), _d(inv.get("dn")), _d(inv.get("spent"))
    if up is None or dn is None or spent is None or px is None:
        return None
    resid = up - dn
    if resid == 0:
        return None
    q = abs(resid)
    cost = px * q
    up2, dn2 = (up, dn + q) if resid > 0 else (up + q, dn)
    spent2 = spent + cost
    before, after = min(up, dn) - spent, min(up2, dn2) - spent2
    # the pair cost this would leave, on the same averages the live ceiling
    # reads: cost of each side over its quantity
    cu, cd = _d(inv.get("avg_up")), _d(inv.get("avg_dn"))
    cost_up = (cu * up) if (cu is not None and up) else D("0")
    cost_dn = (cd * dn) if (cd is not None and dn) else D("0")
    if resid > 0:
        cost_dn += cost
    else:
        cost_up += cost
    pair_after = ((cost_up / up2) + (cost_dn / dn2)) if up2 and dn2 else None
    return {
        "residual_units": str(resid),
        "complete_px": str(px),
        "cash_cost_usd": str(cost.quantize(D("0.0001"))),
        "floor_before_usd": str(before.quantize(D("0.0001"))),
        "floor_after_usd": str(after.quantize(D("0.0001"))),
        "floor_delta_usd": str((after - before).quantize(D("0.0001"))),
        "pair_cost_after": (str(pair_after.quantize(D("0.000001")))
                            if pair_after is not None else None),
        "within_pair_ceiling": (pair_after is not None
                                and pair_after <= PAIR_CEILING),
        "improves_floor": after > before,
    }


def extend_is_reparative(inv: Mapping[str, Any], px: D) -> Optional[bool]:
    """Would buying MORE of the surplus side at `px` LOWER that side's
    average cost?

    This class exists because the measured nikizq reconstruction showed the
    opposite of the intuition: NO extends near 0.38 pulled avg_dn from 0.49
    to 0.4226 and dug the pair out from 1.15 to 1.0732. An extend below the
    surplus side's own average is repair; above it, it is the loophole the
    2026-09-10 guard was written to refuse."""
    up, dn = _d(inv.get("up")), _d(inv.get("dn"))
    if up is None or dn is None or px is None:
        return None
    resid = up - dn
    if resid == 0:
        return None
    avg = _d(inv.get("avg_up")) if resid > 0 else _d(inv.get("avg_dn"))
    return None if avg is None else px < avg


# --------------------------------------------------------------- parsing
def iter_session_ends(paths: Iterable[str], slugs: set):
    """(timestamp, session_end object) for the markets of interest."""
    seen = set()
    for path in paths:
        try:
            doc = json.loads(Path(path).read_text())
        except Exception:                              # noqa: BLE001
            continue
        rows = doc.get("deploy") if isinstance(doc, Mapping) else doc
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            msg = row.get("message") or ""
            # cheap pre-filter, then the PARSED object decides. The first
            # version matched the literal '"kind":"session_end"', which is
            # compact-JSON spacing: a dump serialised with spaces after the
            # colons was silently skipped and produced an empty study rather
            # than an error. l5_r5f_cohort carries the same warning in its
            # own parser and I reproduced the bug anyway.
            if not isinstance(msg, str) or "session_end" not in msg \
                    or "{" not in msg:
                continue
            try:
                obj = json.loads(msg[msg.index("{"):])
            except Exception:                          # noqa: BLE001
                continue
            if not isinstance(obj, Mapping) or obj.get("kind") != "session_end":
                continue
            if obj.get("slug") not in slugs:
                continue
            # the same session is logged more than once across dumps
            key = (obj.get("slug"), json.dumps(obj.get("inventory"),
                                               sort_keys=True),
                   len(obj.get("orders") or []),
                   len(obj.get("decisions") or []))
            if key in seen:
                continue
            seen.add(key)
            yield row.get("timestamp") or "", obj


def pair_decisions_to_orders(session: Mapping[str, Any]) -> list:
    """Decisions with the order each one produced.

    Matched by position among ACTING decisions: a decision that held or was
    refused submitted nothing, so the order pointer only advances when the
    decision actually acted. Zipping decisions to orders directly would slide
    every later pairing by one on the first hold -- and this file's whole
    purpose is knowing which price was on the screen at which state."""
    orders = [o for o in (session.get("orders") or []) if isinstance(o, Mapping)]
    out, i = [], 0
    for d in (session.get("decisions") or []):
        if not isinstance(d, Mapping):
            continue
        acted = str(d.get("acted") or "")
        order = None
        if acted in ACTING and i < len(orders):
            order = orders[i]
            i += 1
        out.append((d, order))
    return out


# -------------------------------------------------------- reconstruction
def states_for_market(sessions: list) -> list:
    """Every decision state from the FIRST imbalance onward."""
    rows: list = []
    started = False
    for ts, s in sessions:
        for d, order in pair_decisions_to_orders(s):
            inv = d.get("inventory_before") or {}
            resid = _d(inv.get("residual"))
            if not started:
                if resid is None or resid == 0:
                    continue                  # still flat: not yet in scope
                started = True
            px = _d((order or {}).get("px"))
            cf = d.get("counterfactual_l5_price") or {}
            pf = d.get("complement_preflight") or {}
            # the deficit-side price that was on the screen, in order of
            # directness: L5's own touch, the preflight's chosen price, then
            # the submitted price when the action WAS a complete
            complete_px = (_d(cf.get("l5_touch_px"))
                           or _d(pf.get("chosen_px"))
                           or (px if str(d.get("acted")) == "complete" else None))
            avail = complete_at(inv, complete_px) if complete_px else None
            rows.append({
                "t_utc": d.get("t_utc"),
                "session_end_ts": ts,
                "state": d.get("state"),
                "state_n": d.get("state_n"),
                "acted": d.get("acted"),
                "v2_recommended": d.get("v2_recommended"),
                "refusal": d.get("refusal"),
                "cap_binding": d.get("cap_binding"),
                "inventory_before": inv,
                "floor_before_usd": (str(floor_now(inv))
                                     if floor_now(inv) is not None else None),
                "submitted_px": str(px) if px is not None else None,
                "order_outcome": (order or {}).get("outcome"),
                "order_filled_qty": (order or {}).get("filled_qty"),
                "complete_available": avail,
                "extend_reparative": (extend_is_reparative(inv, px)
                                      if px is not None else None),
                # L5's own distribution at this state, carried for the
                # comparison the owner asked for -- never used to score
                "l5": {"persist_extend_p": d.get("persist_extend_p"),
                       "switch_complete_p": d.get("switch_complete_p"),
                       "state_n": d.get("state_n"),
                       "target_size": (order or {}).get("l5_target_size")},
            })
    return rows


def classify(row: Mapping[str, Any]) -> str:
    """The four pre-registered classes, by what was available at the state."""
    avail = row.get("complete_available") or {}
    if avail.get("within_pair_ceiling") and avail.get("improves_floor"):
        return "COMPLETE"
    if row.get("extend_reparative") is True:
        return "EXTEND_REPARATIVE"
    if avail.get("complete_px") is not None or row.get("submitted_px"):
        return "WAIT_REQUOTE"
    return "NO_SAFE_ACTION"


def reconstruct(paths: Iterable[str], slugs: Iterable[str]) -> dict:
    slugs = set(slugs)
    by_slug: dict = {}
    for ts, s in iter_session_ends(paths, slugs):
        by_slug.setdefault(s["slug"], []).append((ts, s))
    markets: list = []
    for slug, sessions in by_slug.items():
        sessions.sort(key=lambda x: x[0])
        rows = states_for_market(sessions)
        for r in rows:
            r["class"] = classify(r)
        counts: dict = {}
        for r in rows:
            counts[r["class"]] = counts.get(r["class"], 0) + 1
        # the headline question: at how many states was a COMPLETE available
        # inside the ceiling AND floor-improving, while the engine did not
        # complete?
        missed = [r for r in rows
                  if r["class"] == "COMPLETE" and r.get("acted") != "complete"]
        markets.append({
            "slug": slug,
            "sessions": len(sessions),
            "post_imbalance_states": len(rows),
            "class_counts": counts,
            "missed_complete_states": len(missed),
            "best_missed": max(
                (r for r in missed),
                key=lambda r: D(r["complete_available"]["floor_delta_usd"]),
                default=None),
            "states": rows,
        })
    markets.sort(key=lambda m: -m["missed_complete_states"])
    return {"markets": markets, "totals": _totals(markets),
            "governed_by": "analysis/l5/RESIDUAL_REPAIR_PREREGISTRATION.md",
            "labels": LABELS}


def _totals(markets: list) -> dict:
    states = sum(m["post_imbalance_states"] for m in markets)
    missed = sum(m["missed_complete_states"] for m in markets)
    cc: dict = {}
    for m in markets:
        for k, v in m["class_counts"].items():
            cc[k] = cc.get(k, 0) + v
    return {"markets": len(markets), "post_imbalance_states": states,
            "class_counts": cc, "missed_complete_states": missed,
            "missed_per_market": (round(missed / len(markets), 3)
                                  if markets else None)}


LABELS = {
    "no_hindsight": ("every field read here is listed as knowable in the "
                     "pre-registration; settlement outcome, resolution side "
                     "and realized_usd are not read at all"),
    "floor": ("min(up,dn) - spent: what the position is worth if the side "
              "held least wins. A past choice is judged on the floor it "
              "could have secured, never on which side actually won"),
    "book": ("an OPEN/EXTEND price IS the BBO touch by construction, so the "
             "order rows recover the book; COMPLETE carries opp_ask and "
             "l5_touch_px directly"),
}


def render_markdown(res: Mapping[str, Any]) -> str:
    t = res["totals"]
    out = ["# Residual markets: post-imbalance decision states", "",
           f"- markets: **{t['markets']}**",
           f"- post-imbalance decision states: **{t['post_imbalance_states']}**",
           f"- class counts: {t['class_counts']}",
           f"- states where a floor-improving COMPLETE was available inside "
           f"the ceiling and we did not complete: "
           f"**{t['missed_complete_states']}** "
           f"({t['missed_per_market']} per market)", "",
           "| market | sessions | states | missed COMPLETE | best missed floor gain |",
           "|---|---:|---:|---:|---:|"]
    for m in res["markets"]:
        best = m.get("best_missed")
        gain = (best["complete_available"]["floor_delta_usd"]
                if best else "—")
        out.append(f"| `{m['slug']}` | {m['sessions']} | "
                   f"{m['post_imbalance_states']} | "
                   f"{m['missed_complete_states']} | {gain} |")
    out += ["", "Judged on the guaranteed floor min(up,dn) − spent, never on "
            "which side won. No settlement field is read."]
    return "\n".join(out) + "\n"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--slugs", required=True,
                    help="JSON file: list of residual market slugs")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    paths: list = []
    for pat in args.logs:
        paths.extend(sorted(_glob.glob(pat)) or [pat])
    slugs = json.loads(Path(args.slugs).read_text())
    res = reconstruct(paths, slugs)
    md = render_markdown(res)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "l5_residual_reconstruction.json").write_text(
            json.dumps(res, indent=2, default=str) + "\n")
        (out / "L5_RESIDUAL_RECONSTRUCTION.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":                             # pragma: no cover
    raise SystemExit(main())
