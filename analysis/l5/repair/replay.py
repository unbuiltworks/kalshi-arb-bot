"""L5 MATCHED-EDGE / RESIDUAL-REPAIR REPLAY.

THE QUESTION. L5's matched leg earned +$162k on BTC15m and his residual leg
surrendered -$144k of it. Can a bounded policy keep a meaningful share of the
matched economics while shedding less residual -- WITHOUT lookahead?

NO LOOKAHEAD, AND THE FILL STANDARD IS WHAT ENFORCES IT. The clone walks L5's
own fill sequence in order and may only ever REFUSE a fill. It can never take a
fill L5 did not take, never reorder, never assume a complement arrives, and
never price a leg at anything but the price L5 actually paid at that instant.
Refusing is free; completing is not. So a policy that "waits for a cheap pair"
only gets one if L5's own tape later supplies a complement fill at that price.

That asymmetry is the whole point: identifying a cheap pair after it is cheap is
cheating, and this construction makes it impossible.

REPAIR POLICIES, NOT ENTRY PREDICTORS. Nothing here forecasts direction or
selects windows. Every policy is a rule about what to do with inventory once it
exists.

FOUR FEE SCENARIOS, applied to the SAME accepted fills so only the fee varies:
  A  observed        cost = usdcSize                       (what L5 actually paid)
  B  PMUS maker      cost = px*sz - 0.0125*px*(1-px)*sz    (rebate credited)
  C  fee-free        cost = px*sz
  D  all fee-bearing cost = px*sz * (1 + 0.07*(1-px))
B is an ECONOMIC TRANSFER TEST ONLY. It does not assert that these fills are
executable on PMUS, and nothing here claims they are.
"""
from __future__ import annotations

import collections
import statistics
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from analysis.l5.redemption import audit as A

L5_FEE = 0.07
PMUS_MAKER_REBATE = 0.0125
TRUNCATION_S = 86400
SCENARIOS = ("A_observed", "B_pmus_maker", "C_fee_free", "D_all_fee")


def cost_of(px: float, sz: float, usd: float, scenario: str) -> float:
    if scenario == "A_observed":
        return usd
    if scenario == "C_fee_free":
        return px * sz
    if scenario == "B_pmus_maker":
        return px * sz - PMUS_MAKER_REBATE * px * (1.0 - px) * sz
    if scenario == "D_all_fee":
        return px * sz * (1.0 + L5_FEE * (1.0 - px))
    raise ValueError(scenario)


def load(cohort: str) -> List[dict]:
    """One record per window: L5's ordered fills and the settled outcome."""
    buys: Dict[str, list] = collections.defaultdict(list)
    meta: Dict[str, tuple] = {}
    redeem: Dict[str, Optional[str]] = {}
    for r in A.iter_activity():
        t = str(r.get("type") or "").upper()
        cid = str(r.get("conditionId") or "")
        w = A.window_of(r)
        if not w or not cid:
            continue
        meta.setdefault(cid, w)
        if t == "TRADE" and str(r.get("side") or "").upper() == "BUY":
            px = float(r.get("price") or 0)
            sz = float(r.get("size") or 0)
            usd = float(r.get("usdcSize") or 0)
            if px > 0 and sz > 0:
                buys[cid].append((int(r.get("timestamp") or 0), px,
                                  str(r.get("outcome")), sz, usd))
        elif t == "REDEEM":
            redeem.setdefault(cid, str(r.get("outcome")))
    capture_end = max((m[2] for m in meta.values()), default=0)
    out: List[dict] = []
    for cid, s in buys.items():
        asset, horizon, end = meta[cid]
        if f"{asset.upper()}{horizon}" != cohort:
            continue
        if capture_end - end < TRUNCATION_S:
            continue                       # indeterminate tail, excluded
        s.sort(key=lambda x: x[0])
        up = sum(x[3] for x in s if x[2] == "Up")
        dn = sum(x[3] for x in s if x[2] != "Up")
        o = redeem.get(cid)
        # no redeem on a long-settled window: the held side lost
        won_up = (o == "Up") if o else (dn > up)
        out.append({"cid": cid, "t": s[0][0], "end": end, "s": s,
                    "won_up": won_up,
                    "outcome_source": "redeem_row" if o else "no_redeem_implies_loss"})
    out.sort(key=lambda r: r["t"])
    return out


class Policy:
    """A repair rule. Sees only the state BEFORE the fill it is judging."""

    def __init__(self, name: str, resid_cap: Optional[float] = None,
                 pair_ceiling: Optional[float] = None,
                 complement_only: bool = False,
                 tte_no_open: Optional[float] = None,
                 tte_relax: Optional[Sequence[Tuple[float, float]]] = None):
        self.name = name
        self.resid_cap = resid_cap
        self.pair_ceiling = pair_ceiling
        self.complement_only = complement_only
        self.tte_no_open = tte_no_open
        # [(tte_below, ceiling)] -- repair gets MORE permissive as expiry nears
        self.tte_relax = tuple(tte_relax or ())

    def ceiling_at(self, tte: float) -> Optional[float]:
        c = self.pair_ceiling
        for below, relaxed in self.tte_relax:
            if tte <= below:
                c = relaxed if c is None else max(c, relaxed)
        return c

    def accept(self, *, up: float, dn: float, cu: float, cd: float,
               px: float, sz: float, cost: float, is_up: bool,
               tte: float) -> bool:
        held = up + dn
        resid = up - dn
        # does this fill reduce the absolute residual?
        new_resid = (up + sz) - dn if is_up else up - (dn + sz)
        reduces = abs(new_resid) < abs(resid)

        if held == 0:                                   # the first leg
            if self.tte_no_open is not None and tte < self.tte_no_open:
                return False
            return True

        if not reduces:                                 # adding to the leading side
            if self.complement_only:
                return False
            if self.tte_no_open is not None and tte < self.tte_no_open:
                return False
            if self.resid_cap is not None and abs(new_resid) > self.resid_cap:
                return False
            return True

        # a complement fill: gate it on the pair it would actually achieve
        ceil = self.ceiling_at(tte)
        if ceil is None:
            return True
        nu, nd = (up + sz, dn) if is_up else (up, dn + sz)
        ncu, ncd = (cu + cost, cd) if is_up else (cu, cd + cost)
        if nu <= 0 or nd <= 0:
            return True
        return (ncu / nu) + (ncd / nd) <= ceil


def run_window(w: dict, pol: Policy, scenario: str) -> dict:
    up = dn = cu = cd = 0.0
    peak_capital = 0.0
    worst_resid = 0.0
    taken = refused = 0
    fee = 0.0
    first_resid_at_repair: List[float] = []
    for (t, px, outcome, sz, usd) in w["s"]:
        is_up = outcome == "Up"
        cost = cost_of(px, sz, usd, scenario)
        tte = w["end"] - t
        if not pol.accept(up=up, dn=dn, cu=cu, cd=cd, px=px, sz=sz,
                          cost=cost, is_up=is_up, tte=tte):
            refused += 1
            continue
        resid_before = up - dn
        new_resid = (up + sz) - dn if is_up else up - (dn + sz)
        if abs(new_resid) < abs(resid_before) and resid_before != 0:
            first_resid_at_repair.append(abs(resid_before))
        if is_up:
            up += sz; cu += cost
        else:
            dn += sz; cd += cost
        fee += cost - px * sz
        taken += 1
        peak_capital = max(peak_capital, cu + cd)
        worst_resid = max(worst_resid, abs(up - dn))

    matched = min(up, dn)
    resid = up - dn
    avg_up = (cu / up) if up else None
    avg_dn = (cd / dn) if dn else None
    won_up = w["won_up"]
    if up == 0 and dn == 0:
        return {"cid": w["cid"], "t": w["t"], "taken": 0, "refused": refused,
                "matched_pnl": 0.0, "residual_pnl": 0.0, "net": 0.0,
                "cost": 0.0, "fee": 0.0, "capital": 0.0, "matched": 0.0,
                "stranded_residual": 0.0, "worst_residual": 0.0,
                "completed": False, "repaired_from": first_resid_at_repair,
                "effective_pair": None}
    matched_pnl = (matched * (1.0 - (avg_up + avg_dn))
                   if (matched > 0 and avg_up is not None and avg_dn is not None) else 0.0)
    if resid == 0:
        residual_pnl = 0.0
    else:
        r_up = resid > 0
        r_avg = avg_up if r_up else avg_dn
        residual_pnl = (abs(resid) if r_up == won_up else 0.0) - abs(resid) * r_avg
    payout = up if won_up else dn
    return {"cid": w["cid"], "t": w["t"], "taken": taken, "refused": refused,
            "matched_pnl": matched_pnl, "residual_pnl": residual_pnl,
            "net": payout - (cu + cd), "cost": cu + cd, "fee": fee,
            "capital": peak_capital, "matched": matched,
            "stranded_residual": abs(resid), "worst_residual": worst_resid,
            "completed": matched > 0 and abs(resid) == 0,
            "repaired_from": first_resid_at_repair,
            "effective_pair": ((avg_up + avg_dn)
                               if (avg_up is not None and avg_dn is not None) else None)}


def summarise(runs: List[dict]) -> Dict[str, Any]:
    r = [x for x in runs if x["cost"] > 0]
    if not r:
        return {"n": 0}
    cost = sum(x["cost"] for x in r)
    net = sum(x["net"] for x in r)
    bps = sorted(1e4 * x["net"] / x["cost"] for x in r)
    cum = peak = dd = 0.0
    for x in sorted(r, key=lambda y: y["t"]):
        cum += x["net"]; peak = max(peak, cum); dd = min(dd, cum - peak)
    return {
        "n": len(r),
        "matched_pnl": sum(x["matched_pnl"] for x in r),
        "residual_pnl": sum(x["residual_pnl"] for x in r),
        "fee": sum(x["fee"] for x in r),
        "net": net,
        "cost": cost,
        "capital": sum(x["capital"] for x in r),
        "bp_per_dollar": 1e4 * net / cost,
        "completion_rate": sum(1 for x in r if x["completed"]) / len(r),
        "stranded_residual": sum(x["stranded_residual"] for x in r),
        "worst_window": min(x["net"] for x in r),
        "bp_p05": bps[int(0.05 * len(bps))],
        "max_drawdown": dd,
        "incentives_usd": None,
        "incentives_reason": ("L5's credits are not published per window for "
                              "another account; never imputed, never netted in"),
    }
