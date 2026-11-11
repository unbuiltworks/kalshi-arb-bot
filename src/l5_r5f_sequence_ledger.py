"""R5F day-1 sequence ledger: every live decision on every R5F market as one
row -- what L5 sampled, what the engine did, the order it sent, what the
venue did with it, where the touch was and where it went next, the
inventory it left -- and, for every LOSING market, a verdict that puts the
loss into exactly four named categories with dollars.

OWNER (#167 5577286614 item 3): "Diagnose the -$8.8243 R5F day at the
sequence level. For every losing residual, show the exact L5
OPEN/EXTEND/COMPLETE path, what filled, what did not, touch movement, model
decisions after fill, and whether loss came from faithful L5 behavior,
execution/queue failure, market-selection transfer, or remaining shell
distortion. Do not respond by inventing a generic pairing rule."
Item 5: "quantify how many actual L5 decisions/fills are being refused by
our 0.20-0.80 / 0.35-0.65-style bounds and the realized/counterfactual
effect in R5F."

THE QUESTION ON EVERY ROW. "At this exact state, what would L5 do next, and
did we do it? If not, name the reason explicitly." `l5` is the stochastic
draw from the frozen distribution (shadow.stochastic.sampled), `v2` the
recommended relation, `did_l5_action` whether the executed relation equals
the draw, `why_not` the engine's own refusal string when it does not.

SOURCE. The Railway log dumps of the R5F probe ({"deploy": [{"timestamp",
"message"}]}); parsing and de-duplication are IMPORTED from
src/l5_r4_action_comparison.harvest (shell prefix "R5F/"), the refusal /
run / paying-side arithmetic from src/l5_r4_fidelity_decomposition. Nothing
here touches the engine.

TOUCH. An acted decision's touch is the ORDER it produced (touch_join
orders rest at the touch; a COMPLETE rests at L5's deficit-side touch,
l5_touch_px). A hold's touch is counterfactual_l5_price.l5_touch_px (a
COMPLETE that had no legal price), the bid quoted in an entry_band_left
refusal, or open_side. Every touch is also expressed as a YES-equivalent
(a YES touch is the yes bid; a NO touch p is 1 - p, i.e. the yes ask) so
that movement between consecutive decisions is one number, in cents,
labelled with its basis. Movement DURING a rest comes from the order's
rest_book (max_touch / min_opp_ask over its polls).

THE TWO BANDS (item 5).
  0.20-0.80  q1 MIN_PRICE..MAX_PRICE: the legal maker price range the
             executor applies to every order price, and the bid range of
             q1.entry_ok. Under R5F a COMPLETE priced at L5's touch outside
             it is refused as complement_not_priceable:no_legal_maker_price;
             an EXTEND whose bid is outside it is refused as
             entry_band_left (q1_entry_ok=False).
  0.35-0.65  the spring observation band; OPEN admission only, and the
             EXTEND check when inventory is zero.
Both are shell restrictions, not venue limits (the venue quoted 0.81 and
0.05 touches on these markets), so a refusal by either is "remaining shell
distortion" in the verdict.

LOSS PARTITION (losing markets only). The venue's realized number is the
only realized figure. Its DERIVED split is the ledger's: matched-pair
contribution + directional-residual contribution. The residual loss is
partitioned over the residual CONTRACTS by the first opportunity that
could have removed each contract, walked in decision order (FIFO):
  execution_queue_failure    a COMPLETE was quoted for them and did not
                             fill (deadline / post-only reject / repriced)
  remaining_shell_distortion a COMPLETE (or the EXTEND L5 drew) was refused
                             by no_legal_maker_price / entry_band_left /
                             spend cap / residual units / size cap
  market_selection_transfer  no legal opportunity was left: the price had
                             run outside the 0.20-0.80 range or the state
                             went off-distribution before any complete
                             could be quoted (sports, vs L5's windows)
  faithful_l5_behavior       L5 drew hold / extend for them at every
                             decision while they were held; also the
                             above-par matched-pair contribution the owner
                             knowingly approved, and the cost of EXTEND
                             fills L5 asked for
Partition dollars are the residual contracts' own cost (qty x avg cost of
the residual side): they sum exactly to the derived residual contribution.
Alongside each execution / shell opportunity the COUNTERFACTUAL saving is
reported -- had the complete filled at its price p, each matched contract
would have paid 1.00 instead of losing its cost: qty x (1 - p); labelled
counterfactual, fill-assumed, queue and depth not modelled.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.l5_r4_action_comparison import (_f, _sampled, _stage_no, harvest,
                                         pair_orders_with_decisions)
from src.l5_r4_fidelity_decomposition import (_d, _q, coverage, expand_logs,
                                              idx_gaps, paying_side,
                                              refusal_name, session_time)

OUT_DIR = os.path.join("analysis", "l5")
SHELL_PREFIX = "R5F/"
LEGAL_LO, LEGAL_HI = D("0.20"), D("0.80")      # q1 MIN_PRICE / MAX_PRICE
BAND_LO, BAND_HI = D("0.35"), D("0.65")        # spring observation band
ONE = D(1)
CENT = D("0.01")
BAND_NAMES = ("no_legal_maker_price", "entry_band_left")
SHELL_REFUSALS = BAND_NAMES + ("spend_cap_reached", "residual_cap",
                               "unmatched_usd_cap", "size_cap")
CATEGORIES = ("faithful_l5_behavior", "execution_queue_failure",
              "market_selection_transfer", "remaining_shell_distortion")
LABELS = {
    "realized": "the venue's own number (settlement rows only)",
    "derived": "arithmetic on ledger fills / venue rows",
    "counterfactual": "did not happen; fill-assumed at the quoted or touch price, "
                      "queue position and depth not modelled",
    "partition": "residual contracts x their own avg cost, attributed to the first "
                 "opportunity that could have removed them (FIFO in decision order); "
                 "sums to the derived residual contribution",
}


# ------------------------------ helpers ------------------------------

def _ts(s: Optional[str]) -> Optional[float]:
    """ISO -> epoch; tolerates the log tool's 9-digit fractions and 'Z'."""
    if not s:
        return None
    s = str(s).replace("Z", "+00:00")
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", s)
    if m:
        frac = (m.group(2) or "")[:7]
        s = m.group(1) + frac + m.group(3)
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _iso(t: Optional[float]) -> Optional[str]:
    if t is None:
        return None
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _hhmmss(s: Optional[str]) -> str:
    t = _ts(s)
    return datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M:%S") if t else "?"


def band_refusal(d: Mapping[str, Any]) -> Optional[str]:
    r = str(d.get("refusal") or "")
    if r.startswith("complement_not_priceable:no_legal_maker_price"):
        return "no_legal_maker_price"
    if r.startswith("entry_band_left"):
        return "entry_band_left"
    return None


def l5_action(d: Mapping[str, Any]) -> Optional[str]:
    """The relation L5 drew at this state (the executed distribution's
    stochastic draw); falls back to the attempted / uncapped relation."""
    return _sampled(d)


def refusal_class(d: Mapping[str, Any]) -> Optional[str]:
    b = band_refusal(d)
    if b:
        return b
    return refusal_name(d)


def outcome_of(o: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not o:
        return None
    filled, qty = _d(o.get("filled_qty"), D(0)), _d(o.get("qty"), D(0))
    er = str(o.get("end_reason") or "")
    if filled and filled > 0:
        return "filled" if filled >= qty else f"partial:{filled}/{qty}"
    head = er.split(":")[0]
    if head == "rejected":
        rc = str(o.get("rejection_class") or "unclassified")
        return "rejected:" + rc.split(":")[-1]
    if head == "repriced":
        return er
    return head or "unknown"


def order_touch(o: Mapping[str, Any]) -> dict:
    side, px = str(o.get("side") or "").upper(), _d(o.get("px"))
    if px is None:
        return {}
    ye = px if side == "YES" else ONE - px
    return {"source": "order", "side": side, "px": _q(px, 2), "yes_equiv": _q(ye, 2),
            "basis": "yes_bid" if side == "YES" else "yes_ask"}


def decision_touch(d: Mapping[str, Any], o: Optional[Mapping[str, Any]]) -> dict:
    if o and o.get("px") is not None:
        return order_touch(o)
    cf = d.get("counterfactual_l5_price") or {}
    inv = d.get("inventory_before") or {}
    up, dn = _d(inv.get("up"), D(0)), _d(inv.get("dn"), D(0))
    if cf.get("l5_touch_px") is not None:
        deficit = "YES" if dn > up else "NO"
        px = _d(cf.get("l5_touch_px"))
        ye = px if deficit == "YES" else ONE - px
        return {"source": "counterfactual_l5_price", "side": deficit, "px": _q(px, 2),
                "yes_equiv": _q(ye, 2), "basis": "yes_bid" if deficit == "YES" else "yes_ask",
                "pair_at_touch": _q(_d(cf.get("pair_at_touch")))}
    m = re.search(r"bid=([0-9.]+)", str(d.get("refusal") or ""))
    if m:
        px = D(m.group(1))
        return {"source": "refusal_bid", "side": "YES", "px": _q(px, 4), "yes_equiv": _q(px, 4),
                "basis": "yes_bid"}
    os_ = d.get("open_side") or {}
    if os_.get("yes_bid") is not None:
        side = os_.get("executed_side") or os_.get("convention_side")
        px = _d(os_.get("no_touch")) if side == "NO" else _d(os_.get("yes_touch"))
        return {"source": "open_side", "side": side, "px": _q(px, 2),
                "yes_equiv": _q(_d(os_.get("yes_bid")), 2), "basis": "yes_bid"}
    return {}


def _pair_econ(d: Mapping[str, Any]) -> Optional[dict]:
    cf = d.get("counterfactual_l5_price")
    if not cf:
        return None
    return {"regime": cf.get("complement_pricing"),
            "pair_at_touch": _q(_d(cf.get("pair_at_touch"))),
            "above_par": cf.get("l5_consistent_above_par"),
            "fee_safe": cf.get("fee_safe_at_touch"),
            "executed_px": None if cf.get("executed_px") in (None, "None") else cf.get("executed_px"),
            "class": cf.get("class")}


def _inv(inv: Optional[Mapping[str, Any]]) -> Optional[dict]:
    if not inv:
        return None
    return {k: inv.get(k) for k in ("up", "dn", "matched", "residual", "avg_up", "avg_dn",
                                    "spent", "unmatched_usd")}


# ------------------------------ ledger rows ------------------------------

def ledger_rows(slug: str, h: Mapping[str, Any]) -> List[dict]:
    decs = sorted([d for d in h["decisions"] if d.get("slug") == slug],
                  key=lambda x: int(_f(x.get("decision_idx"), -1)))
    orders = [o for o in h["orders"] if o.get("slug") == slug]
    pairing = pair_orders_with_decisions(orders, decs)
    order_by_dec: Dict[str, dict] = {}
    for o in orders:
        d = pairing.get(o.get("order_id"))
        if d is not None:
            order_by_dec[d.get("decision_id")] = o
    sess_inv = {s["session_id"]: s.get("inventory") for s in h["sessions"] if s.get("slug") == slug}
    rows: List[dict] = []
    for i, d in enumerate(decs):
        o = order_by_dec.get(d.get("decision_id"))
        nxt = decs[i + 1] if i + 1 < len(decs) else None
        touch = decision_touch(d, o)
        l5 = l5_action(d)
        acted = d.get("acted")
        did = bool(acted and acted != "hold" and acted == l5)
        why_not = None
        if not did:
            why_not = (d.get("refusal") or
                       ("engine executed %s on L5 draw %s: %s" % (acted, l5, d.get("divergence"))
                        if acted != "hold" else d.get("divergence") or "unnamed hold"))
        inv_after = (nxt.get("inventory_before") if nxt else sess_inv.get(d.get("session_id"))) or None
        row = {
            "idx": int(_f(d.get("decision_idx"), -1)),
            "t_utc": d.get("t_utc"),
            "session_id": d.get("session_id"),
            "state": d.get("state"),
            "state_n": d.get("state_n"),
            "off_distribution": bool(d.get("off_distribution")),
            "l5_sampled": l5,
            "v2_recommended": d.get("v2_recommended"),
            "would_act_without_safety_cap": d.get("would_act_without_safety_cap"),
            "acted": acted,
            "refusal": d.get("refusal"),
            "refusal_class": refusal_class(d) if acted == "hold" else None,
            "did_l5_action": did,
            "why_not": why_not,
            "order": ({"order_id": o.get("order_id"), "stage": o.get("stage"),
                       "side": o.get("side"), "px": o.get("px"), "qty": o.get("qty"),
                       "filled_qty": o.get("filled_qty"), "relation": o.get("relation"),
                       "rest_s": o.get("rest_s"), "end_reason": o.get("end_reason"),
                       "rejection_class": o.get("rejection_class"),
                       "fresh_opp_ask": o.get("fresh_opp_ask"),
                       "rest_book": o.get("rest_book")} if o else None),
            "outcome": outcome_of(o) if o else ("hold" if acted == "hold" else "acted:no_order_row"),
            "rest_s": o.get("rest_s") if o else None,
            "next_reevaluation_s": d.get("next_reevaluation_s"),
            "touch": touch,
            "touch_move": None,
            "inventory_before": _inv(d.get("inventory_before")),
            "inventory_after": _inv(inv_after),
            "planned_safe_size": d.get("planned_safe_size"),
            "pair_economics": _pair_econ(d),
            "cap_binding": d.get("cap_binding") or [],
            "divergence": d.get("divergence"),
        }
        rows.append(row)
    for a, b in zip(rows, rows[1:]):
        ya, yb = _d((a["touch"] or {}).get("yes_equiv")), _d((b["touch"] or {}).get("yes_equiv"))
        ta, tb = _ts(a["t_utc"]), _ts(b["t_utc"])
        mv = {"to_idx": b["idx"], "minutes": round((tb - ta) / 60, 2) if (ta and tb) else None}
        if ya is not None and yb is not None:
            mv["yes_equiv_from"] = _q(ya, 2)
            mv["yes_equiv_to"] = _q(yb, 2)
            mv["cents"] = _q((yb - ya) / CENT, 1)
            mv["basis"] = f"{(a['touch'] or {}).get('basis')}->{(b['touch'] or {}).get('basis')}"
        rb = ((a.get("order") or {}).get("rest_book")) or {}
        if rb.get("polls"):
            mv["during_rest"] = {"max_touch": rb.get("max_touch"), "min_opp_ask": rb.get("min_opp_ask"),
                                 "polls": rb.get("polls")}
        a["touch_move"] = mv
    return rows


# ------------------------------ economics ------------------------------

def market_economics(slug: str, h: Mapping[str, Any]) -> dict:
    settle = h["settlements"].get(slug) or {}
    who = paying_side(settle)
    bd = settle.get("breakdown") or {}
    trades = ((settle.get("trades") or {}).get("rows")) or []
    blob = json.dumps(settle, default=str).lower()
    rebate_keys = sorted({k for k in re.findall(r'"([a-z_]*(?:rebate|incentive)[a-z_]*)"', blob)})
    return {
        "settled": bool(settle.get("settled")),
        "status": settle.get("status"),
        "settled_by": settle.get("settled_by"),
        "realized_usd_venue": settle.get("realized_usd"),
        "realized_confirmed": settle.get("realized_confirmed"),
        "resolution_side_field": (settle.get("resolution") or {}).get("side"),
        "paying_side": who.get("side"),
        "paying_side_basis": who.get("basis"),
        "paying_side_unconfirmed": bool(who.get("unconfirmed")),
        "venue_trades": [{"t": r.get("t"), "px": r.get("px"), "qty": r.get("qty"),
                          "aggressor": r.get("aggr"), "state": r.get("state")} for r in trades],
        "venue_trades_n": len(trades),
        "derived": {k: bd.get(k) for k in (
            "fills", "bought_yes", "bought_no", "avg_yes", "avg_no", "cost_basis_usd",
            "matched_pairs", "matched_pair_cost", "matched_contribution_usd",
            "directional_residual", "residual_contribution_usd", "maker_fills",
            "taker_fills", "fees_usd")} if bd else None,
        "credited_rebates": ("not present in the settlement rows (fees_usd=%s); "
                             "rebates are only ever read from the incentive audit"
                             % (bd.get("fees_usd") if bd else None)) if not rebate_keys
                            else {"keys": rebate_keys},
        "labels": LABELS,
    }


# ------------------------------ loss verdict ------------------------------

def _residual_side(bd: Mapping[str, Any]) -> Optional[str]:
    by, bn = _d(bd.get("bought_yes"), D(0)), _d(bd.get("bought_no"), D(0))
    return "YES" if by > bn else ("NO" if bn > by else None)


def loss_partition(slug: str, rows: Sequence[dict], econ: Mapping[str, Any]) -> Optional[dict]:
    """The four-category partition of a settled market's residual loss
    (module docstring, LOSS PARTITION). None when the market is not a
    settled loss."""
    realized = _d(econ.get("realized_usd_venue"))
    bd = econ.get("derived") or {}
    if realized is None or realized >= 0 or not bd:
        return None
    side = _residual_side(bd)
    res_qty = _d(bd.get("directional_residual"), D(0))
    avg = _d(bd.get("avg_yes") if side == "YES" else bd.get("avg_no"), D(0))
    res_loss = _d(bd.get("residual_contribution_usd"), D(0))
    matched = _d(bd.get("matched_contribution_usd"), D(0))
    # contracts become attributable when their fill lands (accrual), a filled
    # COMPLETE removes what it matched, so an opportunity can only take what
    # was actually held at that moment
    remaining = D(0)
    parts = {c: {"contracts": D(0), "usd": D(0), "events": []} for c in CATEGORIES}
    cf_saving = {"execution_queue_failure": D(0), "remaining_shell_distortion": D(0),
                 "market_selection_transfer": D(0)}
    first_fill_idx = next((r["idx"] for r in rows if r.get("outcome", "").startswith(("filled", "partial"))), None)
    extends_added = D(0)
    rejected_extends = []
    complete_attempts = []
    last_opportunity_idx = None
    last_fill_idx = None
    for r in rows:
        if first_fill_idx is None or r["idx"] < first_fill_idx:
            continue
        o = r.get("order") or {}
        acted, l5 = r.get("acted"), r.get("l5_sampled")
        filled = _d(o.get("filled_qty"), D(0)) if o else D(0)
        if filled > 0 and str(o.get("side")).upper() == side:
            remaining += filled                     # bought on the residual side
            last_fill_idx = r["idx"]
        elif filled > 0:
            remaining = max(D(0), remaining - filled)   # a filled COMPLETE matched them
        # extend fills L5 asked for on the residual side: faithful money at risk
        if acted == "extend" and filled > 0 and str(o.get("side")).upper() == side:
            extends_added += filled * _d(o.get("px"), D(0))
        # an EXTEND L5 drew that the venue rejected: an execution failure that,
        # on a losing side, avoided loss by accident
        if acted == "extend" and o and filled <= 0 and str(r.get("outcome", "")).startswith("rejected") \
                and str(o.get("side")).upper() == side:
            rejected_extends.append({"idx": r["idx"], "t": r["t_utc"], "px": o.get("px"), "qty": o.get("qty"),
                                     "outcome": r["outcome"],
                                     "loss_avoided_usd": _q(_d(o.get("qty"), D(0)) * _d(o.get("px"), D(0)))})
        if acted == "complete" and o:
            rb = o.get("rest_book") or {}
            complete_attempts.append({"idx": r["idx"], "t": r["t_utc"], "side": o.get("side"), "px": o.get("px"),
                                      "qty": o.get("qty"), "filled_qty": o.get("filled_qty"), "outcome": r["outcome"],
                                      "rest_s": o.get("rest_s"), "max_touch_during_rest": rb.get("max_touch"),
                                      "min_opp_ask_during_rest": rb.get("min_opp_ask"), "polls": rb.get("polls"),
                                      "pair_at_touch": (r.get("pair_economics") or {}).get("pair_at_touch")})
        elif acted == "hold" and r.get("refusal_class") in BAND_NAMES and (l5 == "complete" or r["refusal_class"] == "no_legal_maker_price"):
            complete_attempts.append({"idx": r["idx"], "t": r["t_utc"], "refused": r.get("refusal"),
                                      "l5_touch_px": (r.get("touch") or {}).get("px"),
                                      "pair_at_touch": (r.get("pair_economics") or {}).get("pair_at_touch")})
        # a refused EXTEND on the losing side did not remove residual: it stopped
        # more from being added, recorded whether or not residual is left to attribute
        if acted == "hold" and r.get("refusal_class") in SHELL_REFUSALS and \
                not (l5 == "complete" or r["refusal_class"] == "no_legal_maker_price"):
            touch = _d((r.get("touch") or {}).get("px"))
            ps = r.get("planned_safe_size") or {}
            q = _d(ps.get("contracts"), D(0))
            parts["remaining_shell_distortion"]["events"].append({
                "idx": r["idx"], "t": r["t_utc"], "refusal": r.get("refusal"),
                "l5_sampled": l5, "bid": _q(touch, 4) if touch is not None else None,
                "qty": _q(q, 2), "removed_residual": False,
                "loss_avoided_by_not_extending_usd": _q(q * touch) if touch is not None else None,
                "note": "refused EXTEND on the losing side: the band stopped a further loss, "
                        "counted as avoided, not as residual loss"})
            continue
        if remaining <= 0:
            continue
        # (1) a COMPLETE quoted for the residual and not filled. rest_book says
        # whether we sat at the touch unfilled (queue) or the touch ran away from
        # the resting price during the rest (the price ran through the complement)
        if acted == "complete" and o and filled < _d(o.get("qty"), D(0)):
            q = min(remaining, _d(o.get("qty"), D(0)) - filled)
            p = _d(o.get("px"), D(0))
            rb = o.get("rest_book") or {}
            max_touch = _d(rb.get("max_touch"))
            ran = bool(max_touch is not None and max_touch > p and rb.get("polls"))
            cat = "market_selection_transfer" if ran else "execution_queue_failure"
            sub = ("touch_ran_away_during_rest" if ran else
                   ("rejected" if str(r["outcome"]).startswith("rejected") else "sat_at_touch_unfilled"))
            parts[cat]["contracts"] += q
            parts[cat]["usd"] += q * avg
            cf_saving[cat] += q * (ONE - p)
            parts[cat]["events"].append({
                "idx": r["idx"], "t": r["t_utc"], "px": _q(p, 2), "qty": _q(q, 2),
                "outcome": r["outcome"], "subclass": sub, "rest_s": o.get("rest_s"),
                "max_touch_during_rest": rb.get("max_touch"), "min_opp_ask_during_rest": rb.get("min_opp_ask"),
                "polls": rb.get("polls"), "next_reevaluation_s": r.get("next_reevaluation_s"),
                "loss_on_these_contracts_usd": _q(q * avg),
                "counterfactual_saving_if_filled_usd": _q(q * (ONE - p))})
            remaining -= q
            last_opportunity_idx = r["idx"]
        # (2) a COMPLETE (or the EXTEND L5 drew) refused by the shell
        elif acted == "hold" and r.get("refusal_class") in SHELL_REFUSALS:
            cls = r["refusal_class"]
            if l5 == "complete" or cls == "no_legal_maker_price":
                touch = _d((r.get("touch") or {}).get("px"))
                ps = r.get("planned_safe_size") or {}
                q = min(remaining, _d(ps.get("contracts"), remaining) or remaining)
                parts["remaining_shell_distortion"]["contracts"] += q
                parts["remaining_shell_distortion"]["usd"] += q * avg
                saving = q * (ONE - touch) if touch is not None else None
                if saving is not None:
                    cf_saving["remaining_shell_distortion"] += saving
                parts["remaining_shell_distortion"]["events"].append({
                    "idx": r["idx"], "t": r["t_utc"], "refusal": r.get("refusal"),
                    "l5_sampled": l5, "l5_touch_px": _q(touch, 2) if touch is not None else None,
                    "qty": _q(q, 2), "loss_on_these_contracts_usd": _q(q * avg),
                    "counterfactual_saving_if_filled_at_touch_usd": _q(saving) if saving is not None else None,
                    "pair_at_touch": (r.get("pair_economics") or {}).get("pair_at_touch")})
                remaining -= q
                last_opportunity_idx = r["idx"]
    # price run after the first fill: how fast the market left L5's price windows
    post = [r for r in rows if first_fill_idx is not None and r["idx"] >= first_fill_idx]
    ye_path = [(r["idx"], r["t_utc"], _d((r.get("touch") or {}).get("yes_equiv"))) for r in post]
    ye_path = [(i, t, y) for i, t, y in ye_path if y is not None]
    left = next(((i, t, y) for i, t, y in ye_path if not (LEGAL_LO <= y <= LEGAL_HI)), None)
    t_first = _ts(ye_path[0][1]) if ye_path else None
    price_run = {
        "first_fill_idx": first_fill_idx,
        "yes_equiv_at_first_fill": _q(ye_path[0][2], 2) if ye_path else None,
        "yes_equiv_min": _q(min(y for _, _, y in ye_path), 2) if ye_path else None,
        "yes_equiv_max": _q(max(y for _, _, y in ye_path), 2) if ye_path else None,
        "yes_equiv_last": _q(ye_path[-1][2], 2) if ye_path else None,
        "minutes_first_fill_to_last_decision": round((_ts(ye_path[-1][1]) - t_first) / 60, 1) if ye_path and t_first else None,
        "left_legal_range_0_20_0_80_at_idx": left[0] if left else None,
        "minutes_first_fill_to_leaving_legal_range": round((_ts(left[1]) - t_first) / 60, 1) if (left and t_first) else None,
        "decisions_outside_legal_range": sum(1 for _, _, y in ye_path if not (LEGAL_LO <= y <= LEGAL_HI)),
        "decisions_off_distribution": sum(1 for r in post if r.get("off_distribution")),
    }
    # leftover: no opportunity ever removed these contracts
    if remaining > 0:
        after = max(x for x in (last_opportunity_idx, last_fill_idx) if x is not None) \
            if (last_opportunity_idx is not None or last_fill_idx is not None) else None
        tail = [r for r in rows if after is None or r["idx"] > after]
        band_tail = any(r.get("refusal_class") in BAND_NAMES for r in tail)
        off = any(r.get("off_distribution") for r in tail)
        ye = [_d((r.get("touch") or {}).get("yes_equiv")) for r in tail]
        ye = [y for y in ye if y is not None]
        outside = any(not (LEGAL_LO <= y <= LEGAL_HI) for y in ye)
        drew_complete = any(r.get("l5_sampled") == "complete" for r in tail)
        if (band_tail or outside or off) and drew_complete:
            cat, why = "market_selection_transfer", (
                "the price ran outside the 0.20-0.80 range / the state left the model's "
                "windows before a legal complete could be quoted for these contracts")
        elif band_tail or outside or off:
            cat, why = "market_selection_transfer", (
                "the market left the legal price range / distribution while L5 was still "
                "drawing hold or extend; no complete was ever drawn for these contracts")
        else:
            cat, why = "faithful_l5_behavior", (
                "L5 drew hold / extend at every decision while these contracts were held; "
                "they went to settlement as L5 would have carried them")
        parts[cat]["contracts"] += remaining
        parts[cat]["usd"] += remaining * avg
        parts[cat]["events"].append({"after_idx": after, "qty": _q(remaining, 2),
                                     "loss_on_these_contracts_usd": _q(remaining * avg), "why": why,
                                     "tail_decisions": [r["idx"] for r in tail],
                                     "tail_yes_equiv": [_q(y, 2) for y in ye]})
    total_part = sum(p["usd"] for p in parts.values())
    return {
        "slug": slug,
        "realized_usd_venue": _q(realized),
        "derived_matched_contribution_usd": _q(matched),
        "derived_residual_contribution_usd": _q(res_loss),
        "residual_side": side, "residual_contracts": _q(res_qty, 2), "residual_avg_cost": _q(avg),
        "partition_of_residual_loss": {
            c: {"contracts": _q(p["contracts"], 2), "usd": _q(-p["usd"]), "events": p["events"]}
            for c, p in parts.items()},
        "partition_check": {"sum_usd": _q(-total_part), "residual_contribution_usd": _q(res_loss),
                            "reconciles": abs(-total_part - res_loss) < D("0.02")},
        "faithful_l5_behavior_also": {
            "matched_pair_contribution_usd": _q(matched),
            "matched_pair_cost": bd.get("matched_pair_cost"),
            "note": "above-par completions are L5's own target under the approved R5F pricing; "
                    "their pair contribution is faithful L5 behavior by construction",
            "extend_fills_on_residual_side_usd": _q(extends_added),
        },
        "counterfactual_saving_fill_assumed": {k: _q(v) for k, v in cf_saving.items()},
        "complete_attempts_after_residual": complete_attempts,
        "rejected_extends_on_losing_side": rejected_extends,
        "price_run": price_run,
        "labels": LABELS,
    }


def _path(rows: Sequence[dict]) -> str:
    """Compact action path: relation(order outcome), holds collapsed by reason."""
    out: List[str] = []
    run_key, run_n = None, 0

    def flush() -> None:
        if run_key:
            out.append(f"{run_key} x{run_n}" if run_n > 1 else run_key)

    for r in rows:
        if r.get("acted") == "hold":
            key = f"hold:{r.get('refusal_class')}"
            if key == run_key:
                run_n += 1
            else:
                flush()
                run_key, run_n = key, 1
            continue
        flush()
        run_key, run_n = None, 0
        o = r.get("order") or {}
        star = "" if r["did_l5_action"] else "*"
        out.append(f"{r['acted']}{star}({o.get('side')} {o.get('qty')}@{o.get('px')} {r.get('outcome')})"
                   if o else f"{r['acted']}{star}")
    flush()
    return " -> ".join(out) or "(no action)"


def verdict_text(slug: str, rows: Sequence[dict], econ: Mapping[str, Any], part: Mapping[str, Any]) -> str:
    p = part["partition_of_residual_loss"]
    pr = part["price_run"]
    path = _path(rows)
    run = (f"yes-equivalent touch {pr['yes_equiv_at_first_fill']} at the first fill -> min {pr['yes_equiv_min']} / "
           f"last {pr['yes_equiv_last']} over {pr['minutes_first_fill_to_last_decision']} min; left the 0.20-0.80 range "
           f"after {pr['minutes_first_fill_to_leaving_legal_range']} min (idx {pr['left_legal_range_0_20_0_80_at_idx']}), "
           f"{pr['decisions_outside_legal_range']} decisions outside it, {pr['decisions_off_distribution']} off-distribution")
    ex = p["execution_queue_failure"]
    sh = p["remaining_shell_distortion"]
    tr = p["market_selection_transfer"]
    fa = p["faithful_l5_behavior"]
    ex_out = Counter(f"{e.get('subclass')}:{e['outcome']}" for e in ex["events"])
    tr_ev = [e for e in tr["events"] if e.get("subclass")]
    rej = part["rejected_extends_on_losing_side"]
    rej_usd = sum(((_d(e["loss_avoided_usd"]) or D(0)) for e in rej), D(0))
    sh_ref = Counter(str(e.get("refusal") or "").split(":")[0] + (":" + str(e.get("refusal")).split(":")[1] if "no_legal" in str(e.get("refusal")) else "")
                     for e in sh["events"] if e.get("removed_residual", True))
    sh_avoided = sum(((_d(e.get("loss_avoided_by_not_extending_usd")) or D(0)) for e in sh["events"]
                      if not e.get("removed_residual", True)), D(0))
    cf = part["counterfactual_saving_fill_assumed"]
    lines = [
        f"VERDICT {slug}: venue realized {part['realized_usd_venue']} = matched pairs "
        f"{part['derived_matched_contribution_usd']} (pair cost {part['faithful_l5_behavior_also']['matched_pair_cost']}) "
        f"+ residual {part['derived_residual_contribution_usd']} ({part['residual_contracts']} {part['residual_side']} "
        f"at avg {part['residual_avg_cost']}, paying side {econ.get('paying_side')}). "
        f"Path (* = not L5's draw): {path}.",
        f"- faithful L5 behavior: {fa['usd']} of residual loss on {fa['contracts']} contracts"
        + (f" ({fa['events'][0]['why']})" if fa["events"] else "")
        + f"; plus the matched-pair contribution {part['derived_matched_contribution_usd']} at L5's own above-par "
          f"target where applicable, and {part['faithful_l5_behavior_also']['extend_fills_on_residual_side_usd']} of "
          f"EXTEND fills L5 asked for on the residual side.",
        f"- execution/queue failure: {ex['usd']} on {ex['contracts']} contracts; {len(ex['events'])} quoted COMPLETE(s) "
        f"did not fill ({dict(ex_out)}: " + "; ".join(
            f"idx {e['idx']} {e['qty']}@{e['px']} rested {e['rest_s']}s, touch during rest {e['max_touch_during_rest']}, "
            f"opp ask {e['min_opp_ask_during_rest']}, {e['polls']} polls" for e in ex["events"]) +
        f"); counterfactual saving had they filled at their price: {cf['execution_queue_failure']} (fill-assumed). "
        f"Also {len(rej)} EXTEND(s) L5 drew on the losing side were REJECTED by the venue "
        f"({dict(Counter(e['outcome'] for e in rej))}), which avoided {_q(rej_usd)} of further loss by accident.",
        f"- market-selection transfer: {tr['usd']} on {tr['contracts']} contracts"
        + ("; " + "; ".join(
            f"idx {e['idx']} COMPLETE {e['qty']}@{e['px']} rested {e['rest_s']}s while the touch ran to "
            f"{e['max_touch_during_rest']} (opp ask {e['min_opp_ask_during_rest']}): the price ran through the complement "
            f"during the rest, cf saving {e['counterfactual_saving_if_filled_usd']}" for e in tr_ev) if tr_ev else "")
        + (f" ({[e for e in tr['events'] if not e.get('subclass')][0]['why']}; tail yes-equiv "
           f"{[e for e in tr['events'] if not e.get('subclass')][0]['tail_yes_equiv']})"
           if any(not e.get("subclass") for e in tr["events"]) else "")
        + f". Price run: {run}.",
        f"- remaining shell distortion: {sh['usd']} on {sh['contracts']} contracts refused by {dict(sh_ref)}; "
        f"counterfactual saving had the refused COMPLETE(s) filled at L5's touch: {cf['remaining_shell_distortion']} "
        f"(fill-assumed); the band also refused EXTEND draws on the losing side worth {_q(sh_avoided)} of avoided loss.",
        f"- partition check: {part['partition_check']['sum_usd']} vs derived residual "
        f"{part['partition_check']['residual_contribution_usd']} (reconciles={part['partition_check']['reconciles']}).",
    ]
    return "\n".join(lines)


# ------------------------------ band quantification ------------------------------

def band_quantification(per: Mapping[str, Mapping[str, Any]]) -> dict:
    """Item 5: every decision refused by no_legal_maker_price / entry_band_left
    across every R5F market: the L5 action refused, the dollars of the
    L5-intended order, and the counterfactual (fill-assumed) effect given
    the venue's actual resolution."""
    rows = []
    for slug, m in per.items():
        econ = m["economics"]
        winner = econ.get("paying_side")
        settled = bool(econ.get("settled"))
        unconfirmed = bool(econ.get("paying_side_unconfirmed"))
        for r in m["rows"]:
            b = r.get("refusal_class")
            if b not in BAND_NAMES:
                continue
            l5 = r.get("l5_sampled")
            inv = r.get("inventory_before") or {}
            up, dn = _d(inv.get("up"), D(0)), _d(inv.get("dn"), D(0))
            touch = _d((r.get("touch") or {}).get("px"))
            ps = r.get("planned_safe_size") or {}
            residual = (up - dn).copy_abs()
            if b == "no_legal_maker_price" or l5 == "complete":
                side = "YES" if dn > up else "NO"            # deficit side
                surplus = "NO" if side == "YES" else "YES"
                q = min(residual, _d(ps.get("contracts"), residual) or residual)
                surplus_avg = _d(inv.get("avg_dn") if surplus == "NO" else inv.get("avg_up"))
                pair = (surplus_avg + touch) if (surplus_avg is not None and touch is not None) else None
                usd = q * touch if touch is not None else None
                # a filled pair pays 1.00 whatever settles; vs what actually happened:
                # the surplus lost -> +q(1 - touch); the surplus won -> -q x touch
                if settled and winner and touch is not None:
                    delta = q * (ONE - touch) if winner != surplus else -(q * touch)
                else:
                    delta = None
                rows.append({"slug": slug, "idx": r["idx"], "t_utc": r["t_utc"], "band": b,
                             "l5_action": l5, "v2": r.get("v2_recommended"),
                             "refusal": r.get("refusal"), "state": r.get("state"),
                             "order_refused": {"relation": "complete", "side": side, "px": _q(touch, 2) if touch is not None else None,
                                               "qty": _q(q, 2), "usd": _q(usd)},
                             "pair_cost_if_filled": _q(pair), "above_par": (pair > ONE) if pair is not None else None,
                             "paying_side": winner, "settled": settled,
                             "paying_side_unconfirmed": unconfirmed,
                             "counterfactual_delta_vs_actual_usd": _q(delta),
                             "counterfactual_pair_contribution_usd": _q(q * (ONE - pair)) if pair is not None else None})
            else:
                side = "YES" if up > dn else "NO"            # the residual side L5 would extend
                q = _d(ps.get("contracts"), D(0))
                usd = q * touch if touch is not None else None
                if settled and winner and touch is not None:
                    delta = q * (ONE - touch) if winner == side else -(q * touch)
                else:
                    delta = None
                rows.append({"slug": slug, "idx": r["idx"], "t_utc": r["t_utc"], "band": b,
                             "l5_action": l5, "v2": r.get("v2_recommended"),
                             "refusal": r.get("refusal"), "state": r.get("state"),
                             "order_refused": {"relation": l5 or "extend", "side": side, "px": _q(touch, 4) if touch is not None else None,
                                               "qty": _q(q, 2), "usd": _q(usd)},
                             "pair_cost_if_filled": None, "above_par": None,
                             "paying_side": winner, "settled": settled,
                             "paying_side_unconfirmed": unconfirmed,
                             "counterfactual_delta_vs_actual_usd": _q(delta),
                             "counterfactual_pair_contribution_usd": None})

    def agg(xs: Sequence[dict]) -> dict:
        d_sum = sum(((_d(x["counterfactual_delta_vs_actual_usd"]) or D(0)) for x in xs), D(0))
        return {"decisions": len(xs),
                "by_l5_action": dict(Counter(x["l5_action"] for x in xs).most_common()),
                "by_market": dict(Counter(x["slug"] for x in xs).most_common()),
                "usd_of_l5_intended_orders": _q(sum(((_d(x["order_refused"]["usd"]) or D(0)) for x in xs), D(0))),
                "contracts": _q(sum(((_d(x["order_refused"]["qty"]) or D(0)) for x in xs), D(0)), 2),
                "counterfactual_delta_vs_actual_usd": _q(d_sum),
                "settled_rows": sum(1 for x in xs if x["settled"]),
                "paying_side_unconfirmed_rows": sum(1 for x in xs if x.get("paying_side_unconfirmed")),
                "above_par_pairs": sum(1 for x in xs if x.get("above_par"))}

    by_band = {b: agg([x for x in rows if x["band"] == b]) for b in BAND_NAMES}
    completes = [x for x in rows if x["order_refused"]["relation"] == "complete"]
    extends = [x for x in rows if x["order_refused"]["relation"] != "complete"]
    a_c, a_e = agg(completes), agg(extends)
    dc, de = _d(a_c["counterfactual_delta_vs_actual_usd"]) or D(0), _d(a_e["counterfactual_delta_vs_actual_usd"]) or D(0)
    total = dc + de
    rec = ("REMOVE the 0.20-0.80 legal-price refusal for COMPLETE (price L5's touch wherever the venue "
           "accepts it) and keep entry_band_left only as the OPEN/zero-inventory band"
           if dc > 0 else
           "KEEP both bounds: the fill-assumed counterfactual does not show a positive dollar effect")
    return {
        "bounds": {"legal_maker_price": f"{LEGAL_LO}-{LEGAL_HI} (q1 MIN_PRICE/MAX_PRICE, entry_ok)",
                   "observation_band": f"{BAND_LO}-{BAND_HI} (OPEN admission; EXTEND with zero inventory)"},
        "by_band": by_band,
        "refused_completes": a_c,
        "refused_extends": a_e,
        "counterfactual_total_delta_vs_actual_usd": _q(total),
        "recommendation": (
            f"{rec}. Numbers: {len(completes)} COMPLETE decisions L5 drew were refused by the legal-price "
            f"range ({a_c['usd_of_l5_intended_orders']} of intended orders, {a_c['above_par_pairs']} above-par pairs); "
            f"had every one filled at L5's touch the realized outcome would have moved by {_q(dc)} "
            f"(fill-assumed, given the venue's actual resolutions). {len(extends)} EXTEND draws were refused by "
            f"entry_band_left ({a_e['usd_of_l5_intended_orders']} intended); had they filled and been held, "
            f"the outcome would have moved by {_q(de)}. Net {_q(total)}. The COMPLETE refusals are pure shell "
            f"distortion of an approved L5 action; the EXTEND refusals mostly stopped buying a side the venue "
            f"then settled against, so they are the part of the band that protected the account -- which is a "
            f"selection fact (the price ran through the range in minutes), not an L5 fidelity gain."),
        "rows": rows,
        "labels": {"counterfactual": LABELS["counterfactual"],
                   "delta": "per refused COMPLETE at touch p on the deficit side: the pair pays 1.00 whatever "
                            "settles, so vs the actual outcome the delta is +q(1-p) when the surplus side lost and "
                            "-q*p when it won; per refused EXTEND at bid p: +q(1-p) if that side paid, else -q*p"},
    }


# ------------------------------ campaign ------------------------------

def market_report(slug: str, h: Mapping[str, Any]) -> dict:
    rows = ledger_rows(slug, h)
    econ = market_economics(slug, h)
    part = loss_partition(slug, rows, econ)
    decs = [d for d in h["decisions"] if d.get("slug") == slug]
    orders = [o for o in h["orders"] if o.get("slug") == slug]
    return {
        "slug": slug,
        "decisions": len(rows),
        "coverage": idx_gaps(decs),
        "session_time": session_time(slug, h),
        "orders_submitted": len(orders),
        "orders_filled": sum(1 for o in orders if _f(o.get("filled_qty")) > 0),
        "did_l5_action_share": round(sum(1 for r in rows if r["did_l5_action"]) / len(rows), 4) if rows else None,
        "holds_by_refusal": dict(Counter(r["refusal_class"] for r in rows if r["acted"] == "hold").most_common()),
        "outcomes": dict(Counter(r["outcome"] for r in rows).most_common()),
        "rows": rows,
        "economics": econ,
        "loss_partition": part,
        "verdict": verdict_text(slug, rows, econ, part) if part else None,
    }


def campaign_report(h: Mapping[str, Any], cov: Optional[Mapping[str, Any]] = None) -> dict:
    per = OrderedDict((slug, market_report(slug, h)) for slug in h["markets"])
    losing = {s: m for s, m in per.items() if m["loss_partition"]}
    realized = [(_d(m["economics"]["realized_usd_venue"]) or D(0)) for m in per.values()
                if m["economics"].get("realized_usd_venue") is not None]
    totals = {c: sum(((_d(m["loss_partition"]["partition_of_residual_loss"][c]["usd"]) or D(0))
                      for m in losing.values()), D(0)) for c in CATEGORIES}
    return {
        "campaign": "R5F-RUN-2026-09-07",
        "shell_prefix": SHELL_PREFIX,
        "markets": list(per),
        "markets_with_fills": [s for s, m in per.items() if m["orders_filled"] > 0],
        "markets_settled": sum(1 for m in per.values() if m["economics"]["settled"]),
        "decisions": sum(m["decisions"] for m in per.values()),
        "orders_submitted": sum(m["orders_submitted"] for m in per.values()),
        "orders_filled": sum(m["orders_filled"] for m in per.values()),
        "did_l5_action_share": round(sum(1 for m in per.values() for r in m["rows"] if r["did_l5_action"])
                                     / max(1, sum(m["decisions"] for m in per.values())), 4),
        "holds_by_refusal": dict(sum((Counter(m["holds_by_refusal"]) for m in per.values()), Counter()).most_common()),
        "realized_usd_venue_sum": _q(sum(realized)) if realized else None,
        "losing_markets": list(losing),
        "loss_partition_totals_usd": {c: _q(v) for c, v in totals.items()},
        "counterfactual_saving_fill_assumed_totals": {
            k: _q(sum(((_d(m["loss_partition"]["counterfactual_saving_fill_assumed"][k]) or D(0))
                       for m in losing.values()), D(0)))
            for k in ("execution_queue_failure", "remaining_shell_distortion", "market_selection_transfer")},
        "band": band_quantification(per),
        "coverage": cov,
        "per_market": per,
        "labels": LABELS,
    }


# ------------------------------ render ------------------------------

def _t(x: Any) -> str:
    return "" if x is None else str(x)


def _row_md(r: Mapping[str, Any]) -> str:
    o = r.get("order") or {}
    tch = r.get("touch") or {}
    mv = r.get("touch_move") or {}
    ia = r.get("inventory_after") or {}
    pe = r.get("pair_economics") or {}
    order = f"{o.get('side')} {o.get('qty')}@{o.get('px')}" if o else ""
    outcome = r.get("outcome") or ""
    rest = f"{_t(o.get('rest_s'))}/{_t(r.get('next_reevaluation_s'))}"
    touch = f"{tch.get('side')} {tch.get('px')} (ye {tch.get('yes_equiv')})" if tch else ""
    move = ""
    if mv.get("cents") is not None:
        move = f"{mv['cents']}c in {mv.get('minutes')}m"
    if mv.get("during_rest"):
        dr = mv["during_rest"]
        move += f"; rest max_touch {dr.get('max_touch')} min_opp_ask {dr.get('min_opp_ask')}"
    inv = f"{ia.get('up')}/{ia.get('dn')} res {ia.get('residual')} unm {ia.get('unmatched_usd')}" if ia else ""
    pair = (f"{pe.get('regime')} pair {pe.get('pair_at_touch')} par>{'Y' if pe.get('above_par') else 'N'} "
            f"fee_safe {'Y' if pe.get('fee_safe') else 'N'}") if pe else ""
    did = "did L5" if r["did_l5_action"] else f"NOT: {r.get('why_not')}"
    act = r["acted"] if r["acted"] != "hold" else f"hold:{r.get('refusal_class')}"
    return (f"| {r['idx']} | {_hhmmss(r['t_utc'])} | {r['state']} | {r['l5_sampled']}/{r['v2_recommended']} | "
            f"{act} | {did} | {order} | {outcome} | {rest} | {touch} | {move} | {inv} | {pair} |")


def render_md(rep: Mapping[str, Any]) -> str:
    b = rep["band"]
    L = ["# R5F day-1 sequence ledger (R5F-RUN-2026-09-07)", "",
         f"Markets {len(rep['markets'])} ({len(rep['markets_with_fills'])} with fills, {rep['markets_settled']} settled) | "
         f"decisions {rep['decisions']} | orders {rep['orders_submitted']} submitted / {rep['orders_filled']} filled | "
         f"executed L5's draw on {rep['did_l5_action_share']} of decisions | holds by refusal {rep['holds_by_refusal']}",
         f"Venue realized sum over settled markets with a venue number: {rep['realized_usd_venue_sum']} "
         f"(REALIZED markets only; SETTLED_REALIZED_UNCONFIRMED rows carry no venue number).", "",
         "The question on every row: at this exact state, what would L5 do next (column L5 sampled/v2), and did we do "
         "it (column did L5)? If not, the engine's own reason is on the row.", "",
         "## Loss partition totals (losing markets, residual loss by category)", ""]
    for c in CATEGORIES:
        L.append(f"- {c}: {rep['loss_partition_totals_usd'][c]}")
    L += [f"- counterfactual saving, fill-assumed: execution {rep['counterfactual_saving_fill_assumed_totals']['execution_queue_failure']}, "
          f"shell {rep['counterfactual_saving_fill_assumed_totals']['remaining_shell_distortion']}, "
          f"transfer {rep['counterfactual_saving_fill_assumed_totals']['market_selection_transfer']}", "",
          "## Band quantification (item 5)", "",
          f"- bounds: {b['bounds']}"]
    for name in BAND_NAMES:
        x = b["by_band"][name]
        L.append(f"- {name}: {x['decisions']} decisions refused; L5 action refused {x['by_l5_action']}; "
                 f"{x['contracts']} contracts / {x['usd_of_l5_intended_orders']} of L5-intended orders; "
                 f"counterfactual delta vs actual {x['counterfactual_delta_vs_actual_usd']} "
                 f"({x['settled_rows']} settled rows, {x['paying_side_unconfirmed_rows']} with the paying side read from "
                 f"the venue's public settlement price, UNCONFIRMED); by market {x['by_market']}")
    L += [f"- refused COMPLETEs: {b['refused_completes']['decisions']} ({b['refused_completes']['above_par_pairs']} above-par pairs), "
          f"delta {b['refused_completes']['counterfactual_delta_vs_actual_usd']}; refused EXTENDs: "
          f"{b['refused_extends']['decisions']}, delta {b['refused_extends']['counterfactual_delta_vs_actual_usd']}; "
          f"net {b['counterfactual_total_delta_vs_actual_usd']}",
          f"- recommendation: {b['recommendation']}", "",
          "| market | idx | t | band | L5 | refused order | pair if filled | paying | delta vs actual (cf) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for x in b["rows"]:
        o = x["order_refused"]
        L.append(f"| {x['slug']} | {x['idx']} | {_hhmmss(x['t_utc'])} | {x['band']} | {x['l5_action']}/{x['v2']} | "
                 f"{o['relation']} {o['side']} {o['qty']}@{o['px']} ({o['usd']}) | {_t(x['pair_cost_if_filled'])} | "
                 f"{_t(x['paying_side'])}{' (unconfirmed)' if x.get('paying_side_unconfirmed') else ''} | "
                 f"{_t(x['counterfactual_delta_vs_actual_usd'])} |")
    L.append("")
    for slug, m in rep["per_market"].items():
        e = m["economics"]
        L += [f"## {slug}", "",
              f"- decisions {m['decisions']} (coverage {m['coverage']['range']}, missing {m['coverage']['missing_n']}); "
              f"orders {m['orders_submitted']} / filled {m['orders_filled']}; did L5's draw {m['did_l5_action_share']}; "
              f"holds {m['holds_by_refusal']}; outcomes {m['outcomes']}; session time {m['session_time']}", "",
              "| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        L += [_row_md(r) for r in m["rows"]]
        d = e.get("derived") or {}
        L += ["", f"- venue trades ({e['venue_trades_n']}): " + "; ".join(
                  f"{_hhmmss(t['t'])} {t['qty']}@{t['px']}{' aggr' if t.get('aggressor') else ''}" for t in e["venue_trades"]),
              f"- settlement: settled {e['settled']} ({e['status']}, {e['settled_by']}); resolution field {e['resolution_side_field']}; "
              f"paying side {e['paying_side']} [{e['paying_side_basis']}]; venue realized {e['realized_usd_venue']}",
              f"- derived: bought YES {d.get('bought_yes')} @ {d.get('avg_yes')}, NO {d.get('bought_no')} @ {d.get('avg_no')}; "
              f"matched pairs {d.get('matched_pairs')} at pair cost {d.get('matched_pair_cost')} -> {d.get('matched_contribution_usd')}; "
              f"residual {d.get('directional_residual')} -> {d.get('residual_contribution_usd')}; fees {d.get('fees_usd')}",
              f"- credited rebates: {e['credited_rebates']}", ""]
        if m["verdict"]:
            L += [m["verdict"], ""]
    L += ["Labels: " + "; ".join(f"{k} = {v}" for k, v in LABELS.items()), ""]
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", required=True, help="Railway log dump files or directories")
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--shell-prefix", default=SHELL_PREFIX)
    a = ap.parse_args(argv)
    files = expand_logs(a.logs)
    h = harvest(files, a.shell_prefix)
    cov = coverage(files, [d.get("decision_id") for d in h["decisions"]])
    cov = {k: v for k, v in cov.items() if k != "truncated"} | {"truncated_rows_detail": cov["truncated"][:20]}
    rep = campaign_report(h, cov)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "r5f_day1_sequence.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, sort_keys=True, default=str)
    with open(os.path.join(a.out, "R5F_DAY1_SEQUENCE.md"), "w", encoding="utf-8") as f:
        f.write(render_md(rep))
    print(json.dumps({"markets": rep["markets"], "decisions": rep["decisions"],
                      "orders": rep["orders_submitted"], "filled": rep["orders_filled"],
                      "losing": rep["losing_markets"], "totals": rep["loss_partition_totals_usd"],
                      "band": {k: rep["band"]["by_band"][k]["decisions"] for k in BAND_NAMES},
                      "coverage": {k: cov[k] for k in ("campaign_rows", "parsed_rows", "truncated_rows")}}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
