"""Backtest of the sports quoting core with and without directional allocation.

READ-ONLY and offline. Replays RECORDED books (Polymarket US top-of-book,
with sizes where the recorder kept them, plus the international book for
the same game) through the PRODUCTION quoting function
`pmus_rn1_replica.quotes`, the production international guard rule
(an opening/extension bid must be at or under the international fair for
its side minus `guard_margin_ticks`), the production ladder rule (deeper
levels while the market's exact worst case stays inside the per-market
spend and unpaired caps), and -- for the directional policy --
`pmus_directional_alloc` on the same international fair band the engine
uses (mid and range over `fair_stable_s`, settled only when calm). There is
no second execution engine here: only the order book is simulated.

LABELS on every result:
  RECORDED   books, international quotes, settlement prices.
  MODELLED   fills: a resting maker bid at px fills when a later frame's
             ask for that side is at or below px ("touch", optimistic,
             queue-blind) or strictly below px ("through", strict: the level
             was traded through). Fill size is capped by the displayed ask
             size when the recording has sizes. New orders become active,
             and cancelled orders stop being fillable, only `latency_s`
             after the decision (the lane takes several round trips), so a
             price that runs over a stale bid fills it: that is the adverse
             selection the live markouts show.
  MODELLED   rewards: none (no reward programme is recorded for these
             markets). Reported as 0 and labelled; never confirmed income.
Every P&L here is THEORETICAL; none is realized.
"""
from __future__ import annotations

import gzip
import json
import os
from collections import defaultdict, deque
from decimal import Decimal as D
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional

from src import pmus_directional_alloc as DA
from src import pmus_rn1_replica as RN1

ONE, ZERO = D(1), D(0)

PRODUCTION_CFG: Dict[str, Any] = {
    "qty": "2", "depth_ticks": 1, "pair_cap": "0.97",
    "residual_cap_usd": "5", "per_market_usd": "12",
    "extend_max_up": "0.02", "refill_cooldown_s": 10.0,
    "px_min": "0.20", "px_max": "0.92", "fit_qty": True,
    "fit_pair_cap": True,
}

SIM_DEFAULTS: Dict[str, Any] = {
    "ladder_levels": 3, "ladder_step_ticks": 2,
    "guard_margin_ticks": 1, "intl_max_age_s": 20.0,
    "fair_stable_s": 10.0, "fair_max_range": "0.01",
    "latency_s": 3.0, "fill": "touch", "size_cap": True,
    "reprice_ticks": 1, "min_qty": "1",
    "fill_loss_touch": "0.089", "fill_loss_behind": "0.042",
}


# ----------------------------------------------------------------- data
def load_cross_venue(paths: Iterable[str]) -> Dict[str, List[dict]]:
    """Frames per US slug from the two-venue recorder
    (analysis/l5/sports_wallets/cross_venue_sampler.py): each row has the
    international book of the FIRST outcome (ib/ia/ibq/iaq at ti) and the
    US book (ub/ua, ubq/uaq or ub3/ua3, state us, at tu)."""
    out: Dict[str, List[dict]] = defaultdict(list)
    for p in paths:
        op = gzip.open if p.endswith(".gz") else open
        with op(p, "rt") as fh:
            for line in fh:
                r = json.loads(line)
                if "games" in r or "slug" not in r:
                    continue
                if r.get("ub") is None or r.get("ua") is None:
                    continue
                out["aec-" + r["slug"]].append({
                    "t": float(r.get("tu") or r.get("ti")),
                    "yes_bid": D(str(r["ub"])), "yes_ask": D(str(r["ua"])),
                    "bid_q": r.get("ubq"), "ask_q": r.get("uaq"),
                    "state": r.get("us"),
                    "ib": None if r.get("ib") is None else D(str(r["ib"])),
                    "ia": None if r.get("ia") is None else D(str(r["ia"])),
                    "ti": r.get("ti")})
    for s in out:
        out[s].sort(key=lambda f: f["t"])
    return dict(out)


def align_intl(frames: List[dict]) -> Optional[bool]:
    """True if the international first outcome is the US YES side, False
    if it is the other side (flip), None if neither matches (drop)."""
    d_same, d_flip, n = 0.0, 0.0, 0
    for f in frames:
        if f["ib"] is None or f["ia"] is None:
            continue
        um = float(f["yes_bid"] + f["yes_ask"]) / 2
        im = float(f["ib"] + f["ia"]) / 2
        d_same += abs(um - im)
        d_flip += abs(um - (1 - im))
        n += 1
    if n < 10:
        return None
    a, b = d_same / n, d_flip / n
    if min(a, b) > 0.05:
        return None
    return a <= b


# ------------------------------------------------------------ simulator
class Market:
    def __init__(self, slug: str, tick: D, outcome_yes: Optional[int],
                 policy: Mapping[str, Any], sim: Mapping[str, Any]):
        self.slug, self.tick, self.outcome = slug, tick, outcome_yes
        self.policy, self.sim = dict(policy), dict(sim)
        self.pos = {"YES": ZERO, "NO": ZERO}
        self.cost = {"YES": ZERO, "NO": ZERO}
        self.last_fill_t: Dict[str, float] = {}
        self.orders: Dict[tuple, dict] = {}   # (side, level) -> order
        self.dying: List[dict] = []           # cancelled, still fillable
        self.fills: List[dict] = []
        self.fair_hist: deque = deque()
        self.favoured: Optional[str] = None
        self.spend = ZERO
        self.decisions = defaultdict(int)

    # fair band exactly as Engine.intl_fair_band (on recorded samples)
    def band(self, t: float, mid: Optional[D]) -> Optional[dict]:
        if mid is None:
            self.fair_hist.clear()
            return None
        h = self.fair_hist
        h.append((t, mid))
        w = float(self.sim["fair_stable_s"])
        while len(h) > 1 and t - h[1][0] >= w:
            h.popleft()
        span = t - h[0][0]
        vals = [m for _t, m in h]
        lo, hi = min(vals), max(vals)
        settled = span >= w and (hi - lo) <= D(str(self.sim["fair_max_range"]))
        return {"mid": mid, "lo": lo, "hi": hi, "settled": settled}

    def _fill_one(self, o: dict, f: dict, t: float) -> None:
        side, px = o["side"], o["px"]
        through = self.sim["fill"] == "through"
        if side == "YES":
            ask, size = f["yes_ask"], f.get("ask_q")
        else:
            ask, size = ONE - f["yes_bid"], f.get("bid_q")
        hit = ask < px if through else ask <= px
        if not hit or o["left"] <= 0:
            return
        q = o["left"]
        if self.sim["size_cap"] and size is not None:
            q = min(q, D(str(size)))
        if q <= 0:
            return
        o["left"] -= q
        self.pos[side] += q
        self.cost[side] += px * q
        self.spend += px * q
        self.last_fill_t[side] = t
        self.fills.append({"t": t, "side": side, "px": px, "qty": q,
                           "role": o["role"], "level": o["level"]})

    def fills_at(self, f: dict) -> None:
        t = f["t"]
        for o in list(self.orders.values()) + self.dying:
            if o["active_t"] <= t and (o.get("dies_t") is None or
                                       t < o["dies_t"]):
                self._fill_one(o, f, t)
        self.dying = [o for o in self.dying if t < o["dies_t"]]
        for k in [k for k, o in self.orders.items() if o["left"] <= 0]:
            del self.orders[k]

    def _ladder(self, want: Mapping[str, Any]) -> list:
        lv = int(self.sim["ladder_levels"])
        if lv <= 1 and not any((getattr(self, "_extra", None) or {}
                                ).values()):
            return []
        t = self.tick
        step = int(self.sim["ladder_step_ticks"])
        planned = [(sd, tg["px"], tg["qty"], tg["role"] == "complete")
                   for sd, tg in want.items()
                   if sd in ("YES", "NO") and tg is not None]
        out: list = []
        cap_s = D(str(PRODUCTION_CFG["per_market_usd"]))
        cap_u = D(str(PRODUCTION_CFG["residual_cap_usd"]))
        mq = D(str(self.sim["min_qty"]))
        for side in ("YES", "NO"):
            tg = want.get(side)
            if tg is None:
                continue
            behind_c = tg["role"] == "complete"
            qty = D(str(PRODUCTION_CFG["qty"])) if behind_c else tg["qty"]
            role = "open" if behind_c else tg["role"]
            for k in range(1, lv + int((getattr(self, "_extra", None)
                                        or {}).get(side, 0))):
                px = tg["px"] - k * step * t
                if px < D(str(PRODUCTION_CFG["px_min"])) or px <= 0:
                    break

                def fits(qq: D) -> bool:
                    trial = planned + [(l_["side"], l_["px"], l_["qty"],
                                        False) for l_ in out] + \
                        [(side, px, qq, False)]
                    c = RN1.committed(self.pos, self.cost, trial,
                                      D(str(PRODUCTION_CFG["pair_cap"])))
                    return c["spend"] <= cap_s and c["unmatched"] <= cap_u
                lq = qty
                while lq - 1 >= mq and not fits(lq):
                    lq -= 1
                if lq < mq or not fits(lq):
                    continue
                out.append({"side": side, "px": px, "qty": lq,
                            "role": role, "level": k})
        return out

    def decide(self, f: dict, flip: bool) -> None:
        t = f["t"]
        tick = self.tick
        mid = None
        if f["ib"] is not None and f["ia"] is not None and \
                f["ia"] > f["ib"] and f.get("ti") is not None and \
                abs(t - float(f["ti"])) <= float(self.sim["intl_max_age_s"]):
            im = (f["ib"] + f["ia"]) / 2
            mid = (ONE - im) if flip else im
        band = self.band(t, mid)
        if f.get("state") not in (None, "MARKET_STATE_OPEN") or \
                f["yes_ask"] <= f["yes_bid"]:
            self._replace({}, t)
            self.decisions["not_open"] += 1
            return
        qc = dict(PRODUCTION_CFG)
        extra = {"YES": 0, "NO": 0}
        prob0 = None
        if self.policy.get("directional") and band and band["settled"]:
            prob0 = DA.probability(band["mid"], band["lo"], band["hi"],
                                   source="intl")
        if prob0 is not None:
            t0 = {"YES": f["yes_bid"], "NO": ONE - f["yes_ask"]}
            fl0 = {s_: D(str(self.policy.get("fill_loss_touch",
                                             self.sim["fill_loss_touch"])))
                   for s_ in ("YES", "NO")}
            qc["qty_side"] = DA.size_hint(
                prob=prob0, touch=t0, clip=qc["qty"], fill_loss=fl0,
                prev_favoured=self.favoured, cfg=self.policy.get("cfg"),
                max_qty=qc["qty"])
            extra = DA.ladder_hint(prob=prob0, touch=t0, fill_loss=fl0,
                                   prev_favoured=self.favoured,
                                   cfg=self.policy.get("cfg"))
            if self.policy.get("price_adds"):
                qc["adds_priced_externally"] = True
        self._extra = extra
        q = RN1.quotes(yes_bid=f["yes_bid"], yes_ask=f["yes_ask"], tick=tick,
                       pos=self.pos, cost=self.cost,
                       last_fill_t=self.last_fill_t, now=t,
                       min_qty=D(str(self.sim["min_qty"])),
                       c=qc)
        want = {s: q.get(s) for s in ("YES", "NO")}
        # production international guard (opening/extension only)
        margin = tick * int(self.sim["guard_margin_ticks"])
        for s in ("YES", "NO"):
            tg = want[s]
            if tg is None or tg["role"] == "complete":
                continue
            if mid is None:
                continue                 # guard not requiring: no change
            fair_s = mid if s == "YES" else ONE - mid
            if tg["px"] > fair_s - margin:
                want[s] = None
                self.decisions["guard"] += 1
        prob = None
        if self.policy.get("directional") and band and band["settled"]:
            prob = DA.probability(band["mid"], band["lo"], band["hi"],
                                  source="intl")
        touch = {"YES": f["yes_bid"], "NO": ONE - f["yes_ask"]}
        fl = {}
        for s in ("YES", "NO"):
            tg = want.get(s)
            at = tg is not None and tg["px"] >= touch[s]
            key = "fill_loss_touch" if at else "fill_loss_behind"
            fl[s] = D(str(self.policy.get(key, self.sim[key])))
        if self.policy.get("directional"):
            res = DA.allocate(prob=prob, want=want, pos=self.pos, tick=tick,
                              min_qty=D(str(self.sim["min_qty"])),
                              touch=touch, fill_loss=fl,
                              prev_favoured=self.favoured,
                              cfg=self.policy.get("cfg"))
            rec = res.get("directional") or {}
            if rec.get("favoured"):
                self.favoured = rec["favoured"]
            for s in ("YES", "NO"):
                if want.get(s) is not None and res.get(s) is None:
                    self.decisions["directional_refused"] += 1
                want[s] = res.get(s) if want.get(s) is not None else None
        ladder = self._ladder(want)
        if self.policy.get("directional") and prob is not None and ladder:
            fl_b = {s: D(str(self.policy.get("fill_loss_behind",
                                              self.sim["fill_loss_behind"])))
                    for s in ("YES", "NO")}
            ladder, _ref = DA.allocate_ladder(
                prob=prob, ladder=ladder, front=want, pos=self.pos,
                min_qty=D(str(self.sim["min_qty"])), fill_loss=fl_b,
                cfg=self.policy.get("cfg"))
        targets = {}
        for s in ("YES", "NO"):
            if want.get(s) is not None:
                targets[(s, 0)] = dict(want[s], side=s, level=0)
        for l_ in ladder:
            targets[(l_["side"], l_["level"])] = dict(l_)
        self._replace(targets, t)

    def _replace(self, targets: Mapping[tuple, dict], t: float) -> None:
        lat = float(self.sim["latency_s"])
        rt = self.tick * int(self.sim["reprice_ticks"])
        for k, o in list(self.orders.items()):
            tg = targets.get(k)
            if tg is not None and abs(tg["px"] - o["px"]) < rt and \
                    o["left"] <= D(str(tg["qty"])) and \
                    tg.get("role") == o["role"]:
                continue
            o["dies_t"] = t + lat
            self.dying.append(o)
            del self.orders[k]
        for k, tg in targets.items():
            if k in self.orders:
                continue
            self.orders[k] = {"side": tg["side"], "px": D(str(tg["px"])),
                              "left": D(str(tg["qty"])),
                              "role": tg.get("role"),
                              "level": tg.get("level", 0),
                              "active_t": t + lat, "dies_t": None}
            self.decisions["placed"] += 1

    def result(self) -> Dict[str, Any]:
        y, n = self.pos["YES"], self.pos["NO"]
        m = min(y, n)
        avg = {s: (self.cost[s] / self.pos[s]) if self.pos[s] else ZERO
               for s in ("YES", "NO")}
        matched_cost = m * (avg["YES"] + avg["NO"])
        matched_pnl = m - matched_cost
        rs = "YES" if y > n else "NO" if n > y else None
        rq = abs(y - n)
        rcost = rq * avg[rs] if rs else ZERO
        rval = None
        if self.outcome is not None and rs:
            win = (self.outcome == 1) == (rs == "YES")
            rval = rq * (ONE if win else ZERO)
        resid_pnl = (rval - rcost) if rval is not None else None
        settled = self.outcome is not None
        return {"slug": self.slug, "settled": settled,
                "fills": len(self.fills),
                "contracts": str(sum((f["qty"] for f in self.fills), ZERO)),
                "spend": str(self.spend), "pos": {k: str(v) for k, v in
                                                   self.pos.items()},
                "matched_qty": str(m), "matched_pnl": str(matched_pnl),
                "residual_side": rs, "residual_qty": str(rq),
                "residual_cost": str(rcost),
                "residual_pnl": None if resid_pnl is None else str(resid_pnl),
                "trading_pnl": None if resid_pnl is None and rs else
                str(matched_pnl + (resid_pnl or ZERO)),
                "decisions": dict(self.decisions)}


def run(frames_by_slug: Mapping[str, List[dict]],
        meta: Mapping[str, Mapping[str, Any]],
        policy: Mapping[str, Any],
        sim: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Replay every market in time order (so concurrent capital is real).
    `meta[slug]` = {"tick": "0.01", "outcome_yes": 0|1|None,
    "flip": bool}. Markets without alignment or settlement are skipped and
    listed."""
    s = dict(SIM_DEFAULTS)
    s.update(sim or {})
    mk: Dict[str, Market] = {}
    skipped = {}
    events = []
    for slug, fr in frames_by_slug.items():
        m = meta.get(slug) or {}
        if m.get("flip") is None:
            skipped[slug] = "intl_not_aligned"
            continue
        if m.get("outcome_yes") is None:
            skipped[slug] = "no_settlement"
            continue
        mk[slug] = Market(slug, D(str(m.get("tick") or "0.01")),
                          m["outcome_yes"], policy, s)
        events += [(f["t"], slug, f) for f in fr]
    events.sort(key=lambda e: e[0])
    peak, out_cost = ZERO, {}
    for t, slug, f in events:
        m = mk[slug]
        m.fills_at(f)
        m.decide(f, meta[slug]["flip"])
        out_cost[slug] = m.cost["YES"] + m.cost["NO"]
        tot = sum(out_cost.values(), ZERO)
        if tot > peak:
            peak = tot
    rows = [m.result() for m in mk.values()]
    return summarise(rows, peak, skipped, policy, s)


def summarise(rows: List[dict], peak: D, skipped: Mapping[str, str],
              policy: Mapping[str, Any], sim: Mapping[str, Any]
              ) -> Dict[str, Any]:
    tr = [r for r in rows if r["trading_pnl"] is not None]
    tot = sum((D(r["trading_pnl"]) for r in tr), ZERO)
    mat = sum((D(r["matched_pnl"]) for r in tr), ZERO)
    res = sum((D(r["residual_pnl"]) for r in tr
               if r["residual_pnl"] is not None), ZERO)
    spend = sum((D(r["spend"]) for r in rows), ZERO)
    per_m = [float(D(r["trading_pnl"])) for r in tr if int(r["fills"])]
    n = len(per_m)
    mean = sum(per_m) / n if n else 0.0
    se = (sum((x - mean) ** 2 for x in per_m) / (n - 1) / n) ** 0.5 \
        if n > 1 else None
    return {
        "label": "THEORETICAL: recorded books, modelled fills; not realized",
        "policy": {k: v for k, v in policy.items()},
        "sim": {k: v for k, v in sim.items()},
        "markets": len(rows), "markets_traded": n,
        "skipped": dict(skipped),
        "fills": sum(int(r["fills"]) for r in rows),
        "contracts": str(sum((D(r["contracts"]) for r in rows), ZERO)),
        "spend_usd": str(spend),
        "1_settled_trading_pnl_after_fees_usd": str(tot),
        "fees_usd": "0", "fee_basis": "maker-only, PMUS maker fee 0",
        "2_confirmed_maker_rebates_usd": "0",
        "3_confirmed_liquidity_rewards_usd": "0",
        "4_total_realized_net_usd": "0 (backtest: nothing is realized)",
        "5_modelled_incentives_usd": "0 (no reward programme recorded for "
                                     "these markets; not modelled)",
        "6_matched_pnl_usd": str(mat), "6_residual_pnl_usd": str(res),
        "7_peak_capital_usd": str(peak),
        "7_return_on_peak_capital": None if not peak else
        str((tot / peak).quantize(D("0.0001"))),
        "7_capital_turnover": None if not peak else
        str((spend / peak).quantize(D("0.01"))),
        "per_market_mean_usd": round(mean, 4),
        "per_market_se_usd": None if se is None else round(se, 4),
        "rows": rows,
    }


def fetch_meta(slugs: Iterable[str], cache_path: str,
               get: Any = None) -> Dict[str, Dict[str, Any]]:
    """Tick and settlement for each US slug from the PMUS public market
    record (`/v1/market/slug/{slug}`: the long side's `price` is 1 or 0
    once settled), cached to `cache_path`. Public GETs only."""
    cache: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(cache_path):
        cache = json.load(open(cache_path))
    if get is None:
        import urllib.request

        def get(path: str) -> Any:
            req = urllib.request.Request(
                "https://gateway.polymarket.us" + path,
                headers={"User-Agent": "research-client"})
            with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
                return json.load(r)
    for s in slugs:
        if s in cache and cache[s].get("outcome_yes") is not None:
            continue
        try:
            m = (get("/v1/market/slug/%s" % s) or {}).get("market") or {}
            out = None
            if m.get("closed"):
                for sd in m.get("marketSides") or []:
                    if sd.get("long") and str(sd.get("price")) in ("0", "1"):
                        out = int(str(sd["price"]))
            cache[s] = {"tick": str(m.get("orderPriceMinTickSize") or
                                    "0.01"),
                        "outcome_yes": out, "closed": m.get("closed")}
        except Exception as exc:                          # noqa: BLE001
            cache[s] = {"tick": "0.01", "outcome_yes": None,
                        "error": type(exc).__name__}
    with open(cache_path, "w") as fh:
        json.dump(cache, fh, indent=1, sort_keys=True)
    return cache
