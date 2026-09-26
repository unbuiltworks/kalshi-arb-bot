"""Turning the proven maker mechanism into expectancy -- the measurement half.

Probe 07 proved we can rest and get filled. It did not prove we can earn. This
module exists to ask, on evidence, WHERE the money went and which single
change would have kept it.

THE UNIT OF ACCOUNT
-------------------
Every acquisition is expressed the same way, whatever venue produced it:

    buy outcome X at X's own price, for q shares

PMUS wires a BUY_SHORT as a SELL of YES at wire price p; that is the same
thing as buying NO at (1 - p), and stating it that way is what lets a Probe
07 order and an L5 Polymarket trade run through one ledger. The identity is
exact, not an approximation: p*q credited now minus 1*q owed at settlement
equals -(1-p)*q, which is what the NO share cost.

MATCHED AND RESIDUAL
--------------------
One UP share plus one DOWN share redeems exactly $1 whatever happens, so a
matched pair's P&L is locked the moment the second leg fills and is known
without knowing the outcome: 1 - (p_up + p_down). Everything left over is
residual: pure direction, worth 1 or 0.

Pairing is FIFO in fill-time order, because that is what an inventory ledger
does, and because a pairing convention that is chosen after seeing the
outcome is not a measurement. `pooled_split` computes the average-cost
alternative so the convention's influence can be reported rather than
assumed.

THE BLIND BARRIER
-----------------
Policies decide on `l5_adapted_v1.assert_blind`-checked state only. The
winner is used to SCORE a replay and never to make one. That asymmetry is
the whole validity of the exercise.
"""
from __future__ import annotations

import collections
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.l5_adapted_v1 import assert_blind

UP, DOWN = "UP", "DOWN"


def _d(v: Any) -> D:
    return D(str(v if v is not None else 0))


def other(side: str) -> str:
    return DOWN if side == UP else UP


class Fill:
    """One acquisition, in the unit of account."""

    __slots__ = ("t", "side", "price", "qty", "rebate", "ref", "insert_t")

    def __init__(self, t: float, side: str, price: D, qty: D,
                 rebate: D = D(0), ref: Any = None) -> None:
        if side not in (UP, DOWN):
            raise ValueError("side must be UP or DOWN, got %r" % (side,))
        if qty < 0:
            raise ValueError("qty must not be negative")
        self.t, self.side, self.price, self.qty = float(t), side, price, qty
        self.rebate, self.ref = rebate, ref
        self.insert_t = float(t)

    def cost(self) -> D:
        return self.price * self.qty

    def as_dict(self) -> Dict[str, Any]:
        return {"t": self.t, "side": self.side, "price": str(self.price),
                "qty": str(self.qty), "rebate": str(self.rebate),
                "ref": self.ref}


class InventoryWalk:
    """FIFO two-sided inventory, matched/residual split, settlement-blind.

    Nothing here knows the outcome until `settle` is called, and `settle` is
    scoring, not deciding.
    """

    def __init__(self, market: str = "") -> None:
        self.market = market
        self.q: Dict[str, List[List[D]]] = {UP: [], DOWN: []}   # [[qty, px]]
        self.spend = D(0)
        self.rebates = D(0)
        self.matched_pairs = D(0)
        self.matched_cost = D(0)
        self.matched_locked_pnl = D(0)
        self.pair_costs: List[Tuple[D, D]] = []        # (pairs, pair_cost)
        self.max_clip = D(0)
        self.peak_abs_residual = D(0)
        self.n_fills = 0
        self.legs: List[Dict[str, Any]] = []
        self._residual_since: Optional[float] = None
        self._last_t: float = 0.0

    # ------------------------------------------------------------- state

    def side_qty(self, side: str) -> D:
        return sum((x[0] for x in self.q[side]), D(0))

    def residual(self) -> D:
        """Signed imbalance: positive is long UP."""
        return self.side_qty(UP) - self.side_qty(DOWN)

    def residual_age_s(self, t: Optional[float] = None) -> Optional[float]:
        if self._residual_since is None:
            return None
        return float((self._last_t if t is None else t) - self._residual_since)

    def blind_state(self, t: float, *, window_end: Optional[float] = None,
                    cand: Optional[Fill] = None) -> Dict[str, Any]:
        r = self.residual()
        st: Dict[str, Any] = {
            "residual": str(r),
            "abs_residual": str(-r if r < 0 else r),
            "residual_age_s": self.residual_age_s(t),
            "up_qty": str(self.side_qty(UP)),
            "down_qty": str(self.side_qty(DOWN)),
            "max_clip_seen": str(self.max_clip),
            "matched_pairs": str(self.matched_pairs),
            "time_remaining_s": (None if window_end is None
                                 else float(window_end - t)),
        }
        if cand is not None:
            st["cand"] = {"side": cand.side, "price": str(cand.price),
                          "qty": str(cand.qty)}
        assert_blind(st, where="inventory_walk")
        return st

    # ------------------------------------------------------------ mutate

    def accept(self, f: Fill) -> None:
        """Book a fill. FIFO-match it against the opposite side."""
        self._last_t = f.t
        self.n_fills += 1
        self.spend += f.cost()
        self.rebates += f.rebate
        if f.qty > self.max_clip:
            self.max_clip = f.qty
        left = f.qty
        opp = self.q[other(f.side)]
        while left > 0 and opp:
            lot = opp[0]
            take = lot[0] if lot[0] <= left else left
            pair_cost = f.price + lot[1]
            self.matched_pairs += take
            self.matched_cost += pair_cost * take
            self.matched_locked_pnl += (D(1) - pair_cost) * take
            self.pair_costs.append((take, pair_cost))
            self.legs.append({"t": f.t, "pairs": str(take),
                              "pair_cost": str(pair_cost),
                              "locked": str((D(1) - pair_cost) * take),
                              "taker_side": f.side})
            lot[0] -= take
            left -= take
            if lot[0] == 0:
                opp.pop(0)
        if left > 0:
            self.q[f.side].append([left, f.price])
        r = self.residual()
        a = -r if r < 0 else r
        if a > self.peak_abs_residual:
            self.peak_abs_residual = a
        if a == 0:
            self._residual_since = None
        elif self._residual_since is None:
            self._residual_since = f.t

    # ------------------------------------------------------------- score

    def residual_side(self) -> Optional[str]:
        r = self.residual()
        if r == 0:
            return None
        return UP if r > 0 else DOWN

    def residual_cost(self) -> D:
        s = self.residual_side()
        if s is None:
            return D(0)
        return sum((x[0] * x[1] for x in self.q[s]), D(0))

    def settle(self, winner: Optional[str]) -> Dict[str, Any]:
        """Score. `winner` is settlement information and is used ONLY here."""
        r = self.residual()
        size = -r if r < 0 else r
        rs = self.residual_side()
        rcost = self.residual_cost()
        if size == 0:
            rpnl = D(0)
        elif winner is None:
            rpnl = None                      # unscorable, and said so
        else:
            rpnl = (size if rs == winner else D(0)) - rcost
        total = (None if rpnl is None
                 else self.matched_locked_pnl + rpnl + self.rebates)
        redemption = self.matched_pairs + (
            D(0) if (rpnl is None or rs != winner) else size)
        return {
            "market": self.market,
            "fills": self.n_fills,
            "spend": self.spend,
            "matched_qty": self.matched_pairs,
            "matched_cost": self.matched_cost,
            "matched_redemption": self.matched_pairs,
            "matched_pnl": self.matched_locked_pnl,
            "avg_pair_cost": (self.matched_cost / self.matched_pairs
                              if self.matched_pairs else None),
            "maker_rebates": self.rebates,
            "residual_qty": size,
            "residual_side": rs,
            "residual_cost": rcost,
            "residual_pnl": rpnl,
            "redemption": redemption,
            "total": total,
            "peak_abs_residual": self.peak_abs_residual,
            "capital_deployed": self.spend,
        }


def pooled_split(fills: Sequence[Fill], winner: Optional[str]) -> Dict[str, Any]:
    """The average-cost alternative to FIFO, to show the convention's weight."""
    qty = {UP: D(0), DOWN: D(0)}
    cost = {UP: D(0), DOWN: D(0)}
    reb = D(0)
    for f in fills:
        qty[f.side] += f.qty
        cost[f.side] += f.cost()
        reb += f.rebate
    avg = {s: (cost[s] / qty[s] if qty[s] else D(0)) for s in (UP, DOWN)}
    matched = min(qty[UP], qty[DOWN])
    mcost = (avg[UP] + avg[DOWN]) * matched
    rs = UP if qty[UP] > qty[DOWN] else (DOWN if qty[DOWN] > qty[UP] else None)
    size = (qty[rs] - matched) if rs else D(0)
    rcost = avg[rs] * size if rs else D(0)
    rpnl = (None if (size > 0 and winner is None)
            else (size if (rs == winner) else D(0)) - rcost)
    return {"matched_qty": matched, "matched_cost": mcost,
            "matched_pnl": matched - mcost, "residual_qty": size,
            "residual_side": rs, "residual_cost": rcost,
            "residual_pnl": rpnl, "maker_rebates": reb,
            "total": (None if rpnl is None
                      else (matched - mcost) + rpnl + reb)}


# ------------------------------------------------------------- policies

class Native:
    """The frozen control. Takes every action the engine produced."""

    name = "NATIVE"
    params: Dict[str, Any] = {}

    def allows(self, walk: InventoryWalk, cand: Fill,
               state: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
        return True, None


def _increases_imbalance(walk: InventoryWalk, cand: Fill) -> bool:
    r = walk.residual()
    if r == 0:
        return True                      # from flat, any side opens imbalance
    return (r > 0) == (cand.side == UP)


class ResidualCap:
    """Refuse an acquisition that would carry |residual| past k*max_clip.

    The cap is set from inventory geometry -- the largest clip the stream has
    ALREADY shown -- never from an outcome, and never from a sweep over
    settled results.
    """

    def __init__(self, k: D, floor_clip: D = D("0.01")) -> None:
        self.k = D(str(k))
        self.floor_clip = floor_clip
        self.name = "RESIDUAL_CAP_k%s" % self.k
        self.params = {"k": str(self.k), "cap": "k * running max clip"}

    def cap(self, walk: InventoryWalk, cand: Optional[Fill] = None) -> D:
        # the candidate's own size is known at decision time and is part of
        # the geometry, so a first fill is never blocked by a cap derived
        # from an empty history
        clip = walk.max_clip
        if cand is not None and cand.qty > clip:
            clip = cand.qty
        if clip <= 0:
            clip = self.floor_clip
        return self.k * clip

    def allows(self, walk: InventoryWalk, cand: Fill,
               state: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
        if not _increases_imbalance(walk, cand):
            return True, None
        r = walk.residual()
        after = r + (cand.qty if cand.side == UP else -cand.qty)
        a = -after if after < 0 else after
        if a > self.cap(walk, cand):
            return False, "RESIDUAL_ABOVE_CAP"
        return True, None


class ResidualAge:
    """Refuse to DEEPEN an imbalance that has already sat longer than T.

    Repairing is always allowed. The policy never invents a trade; it only
    declines to add to a position it has failed to balance in time.
    """

    def __init__(self, seconds: float) -> None:
        self.seconds = float(seconds)
        self.name = "RESIDUAL_AGE_%gs" % self.seconds
        self.params = {"max_residual_age_s": self.seconds}

    def allows(self, walk: InventoryWalk, cand: Fill,
               state: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
        if not _increases_imbalance(walk, cand):
            return True, None
        age = walk.residual_age_s(cand.t)
        if age is not None and age > self.seconds and walk.residual() != 0:
            return False, "RESIDUAL_TOO_OLD"
        return True, None


class ComplementRepair:
    """While imbalanced, take only the side that reduces the imbalance.

    This is the strict form of the question 'does suppressing same-side maker
    acquisition while already imbalanced reduce residual losses'.
    """

    name = "COMPLEMENT_REPAIR"
    params: Dict[str, Any] = {"rule": "no same-side acquisition while residual != 0"}

    def allows(self, walk: InventoryWalk, cand: Fill,
               state: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
        if walk.residual() == 0:
            return True, None
        if _increases_imbalance(walk, cand):
            return False, "IMBALANCE_INCREASE_SUPPRESSED"
        return True, None


# --------------------------------------------------------------- replay

def replay(fills: Sequence[Fill], policy: Any, *, winner: Optional[str] = None,
           market: str = "", window_end: Optional[float] = None
           ) -> Dict[str, Any]:
    """Walk one market's fills under one policy. Fill-for-fill counterfactual.

    ASSUMPTION, stated because it is load-bearing: an action the policy takes
    fills exactly as it did in the record, and an action it suppresses simply
    does not happen. At these clip sizes that is reasonable; it is still an
    assumption and no result here is stronger than it.
    """
    walk = InventoryWalk(market)
    taken: List[Fill] = []
    suppressed: List[Dict[str, Any]] = []
    for f in sorted(fills, key=lambda x: x.t):
        st = walk.blind_state(f.t, window_end=window_end, cand=f)
        ok, why = policy.allows(walk, f, st)
        if ok:
            walk.accept(f)
            taken.append(f)
        else:
            suppressed.append({"t": f.t, "side": f.side, "qty": str(f.qty),
                               "price": str(f.price), "reason": why,
                               "ref": f.ref})
    out = walk.settle(winner)
    out["policy"] = policy.name
    out["params"] = policy.params
    out["actions_offered"] = len(fills)
    out["actions_taken"] = len(taken)
    out["actions_suppressed"] = len(suppressed)
    out["suppressed"] = suppressed
    out["qty_offered"] = sum((f.qty for f in fills), D(0))
    out["qty_taken"] = sum((f.qty for f in taken), D(0))
    out["rebate_forgone"] = sum(
        (f.rebate for f in fills if f.ref is not None
         and f.ref in {s["ref"] for s in suppressed}), D(0))
    return out


def drawdown(series: Sequence[D]) -> D:
    """Max peak-to-trough of the cumulative series. Zero if it only rises."""
    peak = D(0)
    cum = D(0)
    worst = D(0)
    for x in series:
        cum += x
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > worst:
            worst = dd
    return worst


class RandomSuppress:
    """The control that decides whether a treatment has any content.

    Every treatment here reduces volume, and less volume is less exposure. If
    dropping the SAME fraction of actions at random did as well, the rule is
    decoration and the only active ingredient is trading less. This arm is
    state-blind on purpose: it sees nothing, so whatever it earns is what
    volume reduction alone is worth.
    """

    def __init__(self, take_rate: float, seed: int = 0) -> None:
        import random
        self.take_rate = float(take_rate)
        self.seed = seed
        self._rng = random.Random(seed)
        self.name = "RANDOM_take%.2f_s%d" % (self.take_rate, seed)
        self.params = {"take_rate": self.take_rate, "seed": seed,
                       "rule": "state-blind volume control"}

    def allows(self, walk: "InventoryWalk", cand: Fill,
               state: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
        if self._rng.random() <= self.take_rate:
            return True, None
        return False, "RANDOM_CONTROL"


class PairCostCap:
    """Refuse a completing leg whose pair would cost more than `c`.

    This is not a new idea: the operating model already says spring
    discipline prefers materially cheaper pair completion, roughly <= 0.96.
    Probe 07 paid 1.0299 a pair and nothing in the stack noticed, because no
    gate was ever wired to the number. This arm asks what the written rule
    would have been worth.

    An opening leg -- one with no opposite inventory to match against -- is
    never refused here: it forms no pair yet, and refusing it would make this
    a different policy than the one named.
    """

    def __init__(self, c: D) -> None:
        self.c = D(str(c))
        self.name = "PAIR_COST_CAP_%s" % self.c
        self.params = {"max_pair_cost": str(self.c)}

    def allows(self, walk: "InventoryWalk", cand: Fill,
               state: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
        opp = walk.q[other(cand.side)]
        if not opp:
            return True, None
        left = cand.qty
        qty = D(0)
        cost = D(0)
        for lot in opp:
            if left <= 0:
                break
            take = lot[0] if lot[0] <= left else left
            qty += take
            cost += (cand.price + lot[1]) * take
            left -= take
        if qty <= 0:
            return True, None
        if cost / qty > self.c:
            return False, "PAIR_COST_ABOVE_CAP"
        return True, None
