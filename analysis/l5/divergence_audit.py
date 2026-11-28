"""Decision-by-decision behavioural divergence audit.

For every L5 action in the canonical BTC15m sequence, walk it through OUR
engine's gates in the engine's own order and record the FIRST gate that would
have refused it. Attach that action's own settled economics, so the ranking is
by money and not by count.

SETTLED VALUE OF ONE L5 ACTION. An action buys `qty` of outcome `o` at own
price `p`. At settlement it is worth 1 if `o` resolved, else 0. So

    action_pnl = qty * ((1 if o == settlement else 0) - p)

That is the realized economics of that exact decision, with no modelling.

WHAT "REACHABLE" MEANS. Every behavioural knob is env-configurable, but several
accessors clamp in the RESTRICTIVE direction only, so a knob cannot express
L5 at all:

    entry_cutoff_s       _earlier_only(..., 600.0)   -> never later than 600s
    complete_cutoff_s    _earlier_only(..., 720.0)   -> never later than 720s
    opening_leg_max      downward only, ceiling 3    -> never more than 3
    order_ttl_s          min(120.0, v)               -> never longer than 120s
    completion_pair_ceiling  clamped to <= 1.00      -> never above redemption
    ABS_RESIDUAL_CAP_CONTRACTS = 5 (code constant)   -> never more than 5

Those are marked HARD. The rest are reachable by configuration today.
"""
import gzip, csv, collections
from decimal import Decimal as D

CSV = "analysis/l5/l5_canonical_sequence.csv.gz"

# the DEPLOYED configuration, read from the same constants the engine uses
CFG = dict(
    entry_cutoff_s=D(480),        # env-tightened from the 600 code ceiling
    complete_cutoff_s=D(600),     # env-tightened from the 720 code ceiling
    band_lo=D("0.30"), band_hi=D("0.60"),
    clip=D(1),                    # code ceiling 3
    k=D(1),                       # applied cap min(k*clip, 5)
    abs_cap=D(5),
    pair_ceiling=D("0.96"),
    completion_ceiling=D("1.00"),
    round_allocations=4,
    one_order_slot=True,
)

# classification of each gate
KIND = {
    "SCHEDULING_ONE_ORDER_SLOT":   ("STRATEGY", "reachable"),
    "ROUND_ALLOCATIONS_SPENT":     ("STRATEGY", "reachable"),
    "PAST_ENTRY_CUTOFF":           ("STRATEGY", "HARD>600s"),
    "PAST_COMPLETE_CUTOFF":        ("STRATEGY", "HARD>720s"),
    "OPENING_BAND":                ("STRATEGY", "reachable"),
    "PAIR_COST_ABOVE_CEILING":     ("STRATEGY", "reachable to 1.00"),
    "PAIR_ABOVE_REDEMPTION":       ("ECONOMIC", "HARD>1.00"),
    "RESIDUAL_ABOVE_CAP":          ("CAPITAL",  "HARD>5"),
    "CLIP_TRUNCATED":              ("CAPITAL",  "HARD>3"),
    "ATTEMPTED":                   ("-", "-"),
}


def load():
    by = collections.defaultdict(list)
    with gzip.open(CSV, "rt") as f:
        for r in csv.DictReader(f):
            if r.get("duration_minutes") == "15":
                by[r["window_id"]].append(r)
    out = []
    for w, rs in by.items():
        rs.sort(key=lambda r: int(r["trade_seq"]))
        so = (rs[-1].get("settlement_outcome") or "").strip().upper()
        if so not in ("UP", "DOWN"):
            continue
        acts = []
        for r in rs:
            try:
                acts.append(dict(
                    t=D(str(int(float(r["seconds_from_window_start"])))),
                    o=("UP" if r["outcome"].upper().startswith("U") else "DOWN"),
                    p=D(r["price"]).quantize(D("0.0001")),
                    q=D(r["quantity"]),
                    mt=r["maker_taker"]))
            except Exception:
                continue
        if not acts:
            continue
        out.append(dict(day=rs[-1]["datetime_utc"][:10], win=so, acts=acts))
    out.sort(key=lambda x: x["day"])
    return out


def audit(W, cfg=CFG, slot_hold_s=D(0)):
    """First-refusing gate per L5 action, with that action's settled P&L.

    `slot_hold_s` models the single-order slot. A mirrored L5 action IS a trade
    at that price, so our order fills and the slot frees immediately: that is
    slot_hold_s 0, and it ISOLATES the other gates rather than letting one
    unobservable latency dominate the audit. Fill latency cannot be measured
    from our own records -- `venue_event_t` is null on every fill we have -- so
    the slot's cost is reported separately at a few assumed hold times instead
    of being baked in.
    """
    tally = collections.defaultdict(lambda: dict(n=0, qty=D(0), pnl=D(0),
                                                 notional=D(0)))
    forgone_by_clip = dict(n=0, qty=D(0), pnl=D(0))
    for w in W:
        qty = {"UP": D(0), "DOWN": D(0)}
        cost = {"UP": D(0), "DOWN": D(0)}
        opens = 0
        free_at = D(-1)
        for a in w["acts"]:
            o, p, q, t = a["o"], a["p"], a["q"], a["t"]
            val = (D(1) if o == w["win"] else D(0)) - p     # per contract
            pnl = q * val
            resid = qty["UP"] - qty["DOWN"]
            reduces = (resid > 0 and o == "DOWN") or (resid < 0 and o == "UP")

            def rec(gate, n=1):
                g = tally[gate]
                g["n"] += n; g["qty"] += q; g["pnl"] += pnl
                g["notional"] += q * p

            # --- gate order as the engine evaluates it -------------------
            if cfg["one_order_slot"] and t < free_at:
                rec("SCHEDULING_ONE_ORDER_SLOT"); continue
            if not reduces and opens >= cfg["round_allocations"]:
                rec("ROUND_ALLOCATIONS_SPENT"); continue
            if reduces:
                if t > cfg["complete_cutoff_s"]:
                    rec("PAST_COMPLETE_CUTOFF"); continue
            else:
                if t > cfg["entry_cutoff_s"]:
                    rec("PAST_ENTRY_CUTOFF"); continue
                if not (cfg["band_lo"] <= p <= cfg["band_hi"]):
                    rec("OPENING_BAND"); continue
            if reduces:
                held = "UP" if resid > 0 else "DOWN"
                a_held = (cost[held] / qty[held]) if qty[held] > 0 else None
                if a_held is not None:
                    pair = a_held + p
                    if pair > cfg["completion_ceiling"]:
                        rec("PAIR_ABOVE_REDEMPTION"); continue
                    if pair > cfg["pair_ceiling"]:
                        rec("PAIR_COST_ABOVE_CEILING"); continue
            applied = min(cfg["k"] * max(cfg["clip"], D(1)), cfg["abs_cap"])
            after = resid + (cfg["clip"] if o == "UP" else -cfg["clip"])
            if abs(after) > applied:
                rec("RESIDUAL_ABOVE_CAP"); continue

            # --- we would have attempted it, at OUR clip -----------------
            took = min(q, cfg["clip"])
            g = tally["ATTEMPTED"]
            g["n"] += 1; g["qty"] += took; g["pnl"] += took * val
            g["notional"] += took * p
            if q > took:
                forgone_by_clip["n"] += 1
                forgone_by_clip["qty"] += (q - took)
                forgone_by_clip["pnl"] += (q - took) * val
            qty[o] += took; cost[o] += p * took
            if not reduces:
                opens += 1
            free_at = t + slot_hold_s    # the single slot, see docstring
    return tally, forgone_by_clip
