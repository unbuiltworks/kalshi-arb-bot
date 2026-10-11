"""Economic-state trajectory: one sequential, no-lookahead walk for every venue.

OWNER P0: "align episodes at first fill and reconstruct sequentially without
lookahead: first fill -> EXTEND/HOLD/COMPLETE sequence -> side switches ->
terminal inventory -> settlement."

READ-ONLY. Nothing here submits, prices, cancels, reprices or deploys.

THE POINT OF ONE WALKER. L5 and the clone are compared on state
distributions, so the states must be built the SAME way from the SAME kind of
input. Each venue gets a thin adapter to a common fill record and then this
one walker; nothing venue-specific reaches the state maths. A comparison
where each side used its own reconstruction would measure the
reconstructions.

NO LOOKAHEAD. Every field on a state is computed from fills STRICTLY BEFORE
it, plus the action being taken at that instant (its side, price and size --
which the actor knows, because it is what they are doing). Nothing downstream
of the action is ever read.

THE IDENTITY THIS RESTS ON, established at 2.22e-16 over 21,699 L5 switches:

    pair = last_pair - gain_per_unit

and its accounting twin, which is what the decomposition below uses:

    pair               = held_avg_cost + complement_price_paid
    pair - 1.00        = (held_avg_cost - entry_price)
                       + (complement_price_paid - (1 - entry_price))
                       = C_own_fills + B_market_move

ENTRY PRICE CANCELS OUT OF THE LEVEL, and that is a result rather than a
modelling convenience: entering at ANY price gives a par pair if the
complement is available at one minus it. Entry price can only matter through
what it makes reachable, so it is carried as a stratifier (component A) and
never as a level contribution.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

UP, DN = "up", "dn"
PAR = 1.0

TTE_BUCKETS = ((30, "tte<30s"), (120, "tte30-120s"), (300, "tte2-5m"),
               (600, "tte5-10m"), (float("inf"), "tte>10m"))
PRICE_BANDS = ((0.10, "p<10c"), (0.25, "p10-25c"), (0.45, "p25-45c"),
               (0.55, "p45-55c"), (0.75, "p55-75c"), (0.90, "p75-90c"),
               (1.01, "p>90c"))
GAIN_BUCKETS = ((-0.20, "<-20c"), (-0.10, "-20..-10c"), (-0.05, "-10..-5c"),
                (-0.015, "-5..-1.5c"), (0.0, "-1.5..0c"), (0.015, "0..+1.5c"),
                (0.05, "+1.5..+5c"), (float("inf"), ">+5c"))


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def bucket(value: Optional[float], bands) -> Optional[str]:
    if value is None:
        return None
    for hi, name in bands:
        if value < hi:
            return name
    return bands[-1][1]


def walk(fills: Sequence[Mapping[str, Any]]) -> list[dict]:
    """One episode -> its state sequence, aligned at the FIRST FILL.

    `fills` must be chronological and each carry at least
    ``{t, side, price, qty}``; ``tte_s``, ``sampled`` and ``acted`` are
    carried through when present. The first fill defines the episode's
    entry and is itself a state (with a flat pre-state).
    """
    qty = {UP: 0.0, DN: 0.0}
    spend = {UP: 0.0, DN: 0.0}
    last_px: dict[str, Optional[float]] = {UP: None, DN: None}
    out: list[dict] = []
    entry_px = entry_side = None
    prev_t = prev_switch_t = None
    prev_side = None
    prev_any_px = None

    for i, f in enumerate(fills):
        side = f.get("side")
        px, q, t = _f(f.get("price")), _f(f.get("qty")), _f(f.get("t"))
        if side not in (UP, DN) or px is None or q is None or q <= 0:
            continue

        # ---- PRE-state: everything from fills strictly before this one
        held = None
        if qty[UP] > qty[DN]:
            held = UP
        elif qty[DN] > qty[UP]:
            held = DN
        held_qty = qty[held] if held else 0.0
        held_avg = (spend[held] / qty[held]) if held and qty[held] > 0 else None
        held_px = last_px[held] if held else None
        gain = (held_px - held_avg) if (held_px is not None
                                        and held_avg is not None) else None
        matched = min(qty[UP], qty[DN])
        residual = qty[UP] - qty[DN]
        is_switch = prev_side is not None and side != prev_side

        # the pair this action actually forms: it must be on the side
        # OPPOSITE the one already held, or it adds to the held side and
        # forms no pair at all
        held_opp = (DN if held == UP else UP) if held else None
        realised_pair = (held_avg + px) if (held_avg is not None
                                            and held is not None
                                            and side == held_opp) else None
        # the pair reachable RIGHT NOW if the complement were taken at the
        # last observed price on the side we do NOT hold
        opp_px = last_px[held_opp] if held_opp else None
        attainable_pair = (held_avg + opp_px) if (held_avg is not None
                                                  and opp_px is not None) \
            else None
        last_pair = (last_px[side] + px) if last_px[side] is not None else None

        if entry_px is None:
            entry_px, entry_side = px, side

        out.append({
            "seq": i,
            "t": t,
            "action_side": side,
            "action_price": px,
            "action_qty": q,
            "is_switch": is_switch,
            "is_first_fill": i == 0 or not out,
            # ---- pre-state
            "held_side": held,
            "held_qty": held_qty,
            "held_avg_cost": held_avg,
            "held_side_price": held_px,
            "gain_per_unit": gain,
            "gain_bucket": bucket(gain, GAIN_BUCKETS),
            "cross_side_sum": last_pair,
            "last_pair": last_pair,
            "attainable_pair_cost": attainable_pair,
            "realised_pair_cost": realised_pair,
            "matched_qty": matched,
            "residual_signed": residual,
            "matched_fraction": (matched / (qty[UP] + qty[DN])
                                 if (qty[UP] + qty[DN]) > 0 else None),
            # ---- movement, cadence, regime
            "entry_price": entry_px,
            "entry_side": entry_side,
            "move_since_entry": (held_px - entry_px)
            if (held_px is not None and entry_px is not None
                and held == entry_side) else None,
            "move_since_prev_fill": (px - prev_any_px)
            if prev_any_px is not None else None,
            "dt_since_prev_fill": (t - prev_t)
            if (t is not None and prev_t is not None) else None,
            "dt_since_prev_switch": (t - prev_switch_t)
            if (t is not None and prev_switch_t is not None) else None,
            "tte_s": _f(f.get("tte_s")),
            "tte_bucket": bucket(_f(f.get("tte_s")), TTE_BUCKETS),
            "price_band": bucket(px, PRICE_BANDS),
            # ---- shell
            "sampled": f.get("sampled"),
            "acted": f.get("acted"),
        })

        # ---- apply the fill
        qty[side] += q
        spend[side] += q * px
        last_px[side] = px
        prev_any_px = px
        if is_switch:
            prev_switch_t = t
        prev_side, prev_t = side, t

    return out


def degeneracy(states: Sequence[Mapping[str, Any]]) -> dict:
    """How much of a venue's state distribution is INFORMATIVE.

    `gain_per_unit` is the held side marked against its OWN running average,
    so a position built from a single fill has a gain of EXACTLY zero by
    arithmetic, not because the market sat still. A venue that rarely cycles
    inventory therefore reports a tight, healthy-looking gain distribution
    centred on zero while carrying almost no information.

    Any comparison of gain or pair levels across venues is meaningless
    without this number beside it.
    """
    held = [s for s in states if s.get("held_side")]
    gains = [s for s in states if s.get("gain_per_unit") is not None]
    zero = [s for s in gains if abs(s["gain_per_unit"]) < 1e-12]
    return {
        "states_with_a_held_side": len(held),
        "states_with_a_gain": len(gains),
        "gain_exactly_zero": len(zero),
        "gain_exactly_zero_share": (round(len(zero) / len(gains), 4)
                                    if gains else None),
        "meaning": ("a gain of exactly zero means the held side's last price "
                    "IS its average -- typically a position built from one "
                    "fill. A high share means the venue is not cycling "
                    "inventory, NOT that its positions are flat to market."),
    }


def terminal(states: Sequence[Mapping[str, Any]],
             fills: Sequence[Mapping[str, Any]]) -> dict:
    """Terminal inventory for an episode, from the fills alone."""
    qty = {UP: 0.0, DN: 0.0}
    spend = {UP: 0.0, DN: 0.0}
    for f in fills:
        s, px, q = f.get("side"), _f(f.get("price")), _f(f.get("qty"))
        if s in (UP, DN) and px is not None and q and q > 0:
            qty[s] += q
            spend[s] += q * px
    matched = min(qty[UP], qty[DN])
    total = qty[UP] + qty[DN]
    switches = sum(1 for s in states if s["is_switch"])
    return {"fills": len(states), "switches": switches,
            "up": qty[UP], "dn": qty[DN], "matched": matched,
            "residual_signed": qty[UP] - qty[DN],
            "matched_fraction": (matched / total) if total else None,
            "spent": spend[UP] + spend[DN],
            "terminal_pair_cost": ((spend[UP] / qty[UP] + spend[DN] / qty[DN])
                                   if qty[UP] > 0 and qty[DN] > 0 else None)}


def decompose_excess_pair(state: Mapping[str, Any]) -> Optional[dict]:
    """The OBSERVED decomposition, exact by the identity above.

        pair - 1.00 = (held_avg_cost - entry_price)          C: our own fills
                    + (complement_paid - (1 - entry_price))  B: market move

    Returns None where the state formed no pair. No causal claim is made:
    these are two halves of an accounting identity, and which of them a
    trader can control is a separate question this function does not answer.
    """
    pair = state.get("realised_pair_cost")
    avg = state.get("held_avg_cost")
    entry = state.get("entry_price")
    paid = state.get("action_price")
    if pair is None or avg is None or entry is None or paid is None:
        return None
    c = avg - entry
    b = paid - (PAR - entry)
    return {"excess_pair": pair - PAR, "C_own_fills": c, "B_market_move": b,
            "A_entry_price": entry,
            "identity_residual": (pair - PAR) - (c + b)}


def distribution(values: Iterable[Optional[float]], label: str = "") -> dict:
    v = sorted(x for x in values if x is not None)
    if not v:
        return {"n": 0, "label": label}

    def p(q):
        return round(v[min(len(v) - 1, int(q * (len(v) - 1)))], 4)
    return {"n": len(v), "p10": p(.10), "p25": p(.25), "p50": p(.50),
            "p75": p(.75), "p90": p(.90),
            "mean": round(sum(v) / len(v), 4),
            "min": round(v[0], 4), "max": round(v[-1], 4), "label": label}


def reweight(states: Sequence[Mapping[str, Any]], key: str,
             target_weights: Mapping[str, float], value: str
             ) -> Optional[dict]:
    """COUNTERFACTUAL, and associational only.

    "What would this venue's mean `value` be if its `key` strata occurred at
    the OTHER venue's frequencies?" This holds each stratum's own behaviour
    fixed and changes only how often the strata are visited, so it answers a
    composition question and NOT a causal one: it cannot say the venue would
    behave that way if placed in those states.

    Strata the source never visits carry no observation and are DROPPED from
    both the numerator and the denominator, with the lost target mass
    reported -- never imputed from a neighbouring stratum.
    """
    by: dict[str, list[float]] = {}
    for s in states:
        k, v = s.get(key), _f(s.get(value))
        if k is None or v is None:
            continue
        by.setdefault(str(k), []).append(v)
    if not by:
        return None
    num = den = 0.0
    covered = 0.0
    for k, w in target_weights.items():
        if k in by:
            num += w * (sum(by[k]) / len(by[k]))
            den += w
            covered += w
    if den <= 0:
        return None
    observed = [v for vs in by.values() for v in vs]
    return {"key": key, "value": value,
            "observed_mean": round(sum(observed) / len(observed), 4),
            "reweighted_mean": round(num / den, 4),
            "shift": round(num / den - sum(observed) / len(observed), 4),
            "target_mass_covered": round(covered, 4),
            "target_mass_dropped_unobserved": round(
                max(0.0, sum(target_weights.values()) - covered), 4),
            "strata_observed": len(by),
            "labels": ("COUNTERFACTUAL composition shift, associational "
                       "only; not a causal effect and not additive with "
                       "other reweightings")}


def weights(states: Sequence[Mapping[str, Any]], key: str) -> dict:
    c: dict[str, int] = {}
    for s in states:
        k = s.get(key)
        if k is not None:
            c[str(k)] = c.get(str(k), 0) + 1
    n = sum(c.values())
    return {k: v / n for k, v in c.items()} if n else {}
