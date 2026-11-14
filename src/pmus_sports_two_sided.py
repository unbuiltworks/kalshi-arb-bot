"""Live sports: rest maker bids N ticks below the touch on BOTH sides.

OWNER 2026-11-14: "Just look at the live markets. Place orders 2 ticks below
the price on both sides." Bounded by the approved R5FB budget ($1.50 per
market, 3 markets, $4.50 total).

Per market, one pair at a time:
  YES bid = yes_bid - D ticks         NO bid = (1 - yes_ask) - D ticks
  pair cost = 1 - spread - 2D ticks < 1, so a completed pair is locked.
  Both legs unfilled -> follow the touch (keep D ticks under it).
  One leg filled     -> the other is the COMPLETION: it never moves down;
                        it may move up toward its own bid while the pair
                        stays <= PAIR_CAP (patient, fee-safe).
  Both filled        -> pair locked; start the next pair in that market.

RISK. Post-only maker orders only. QTY contracts per order. A market is
entered only when the account holds no position and no open order there
that this module did not place (the owner's manual trades, the BTC bots).
Unmatched (lone-leg) cost is capped per market and in total; at the cap no
new pair opens anywhere. Worst case = total lone-leg cost <= TOTAL_CAP.
Kill switch: PMUS_TWO_SIDED_ENABLED != YES -> cancel own orders, idle.
On exit (SIGTERM) every own open order is cancelled.

LABELS. Fills are venue-reported. A completed pair's edge is LOCKED pair
economics, not realized until settlement/redemption; a lone leg is open
directional exposure until the market settles.
"""
from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal as D
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

ONE = D("1")


def _env_d(name: str, default: str) -> D:
    try:
        return D(str(os.getenv(name, default)).strip())
    except Exception:
        return D(default)


ENABLED_ENV = "PMUS_TWO_SIDED_ENABLED"
DEPTH_TICKS = int(os.getenv("PMUS_TWO_SIDED_DEPTH_TICKS", "2"))
QTY = _env_d("PMUS_TWO_SIDED_QTY", "1")
MAX_MARKETS = int(os.getenv("PMUS_TWO_SIDED_MAX_MARKETS", "3"))
PER_MARKET_CAP = _env_d("PMUS_TWO_SIDED_PER_MARKET_USD", "1.50")
TOTAL_CAP = _env_d("PMUS_TWO_SIDED_TOTAL_USD", "4.50")
PAIR_CAP = _env_d("PMUS_TWO_SIDED_PAIR_CAP", "0.98")
BAND_LO = _env_d("PMUS_TWO_SIDED_BAND_LO", "0.25")
BAND_HI = _env_d("PMUS_TWO_SIDED_BAND_HI", "0.75")
MAX_SPREAD_TICKS = int(os.getenv("PMUS_TWO_SIDED_MAX_SPREAD_TICKS", "6"))
LOOP_S = float(os.getenv("PMUS_TWO_SIDED_LOOP_S", "5"))
STATE_DIR = Path(os.getenv("PMUS_TWO_SIDED_STATE_DIR",
                           "/state/pmus-spring/two_sided"))
SPORTS_PREFIXES = ("aec-", "asc-", "tsc-", "atc-", "astatc-")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(kind: str, **kw: Any) -> None:
    print("PMUS_TWO_SIDED " + json.dumps({"kind": kind, "t": now_iso(), **kw},
                                         default=str, separators=(",", ":")),
          flush=True)


# ------------------------------------------------------------- pure pricing

def floor_tick(px: D, tick: D) -> D:
    return (px / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def open_prices(yes_bid: D, yes_ask: D, tick: D, depth: int = DEPTH_TICKS
                ) -> Optional[Dict[str, D]]:
    """Both opening bids, D ticks under each side's touch. None if refused."""
    if yes_bid is None or yes_ask is None or yes_ask <= yes_bid:
        return None
    if not (BAND_LO <= yes_bid <= BAND_HI):
        return None
    if (yes_ask - yes_bid) > MAX_SPREAD_TICKS * tick:
        return None
    y = floor_tick(yes_bid - depth * tick, tick)
    n = floor_tick((ONE - yes_ask) - depth * tick, tick)
    if y < tick or n < tick or y + n > PAIR_CAP:
        return None
    return {"YES": y, "NO": n}


def side_bid(side: str, yes_bid: D, yes_ask: D) -> D:
    return yes_bid if side == "YES" else ONE - yes_ask


def completion_price(side: str, current: D, filled_other_px: D,
                     yes_bid: D, yes_ask: D, tick: D) -> D:
    """Never lower; up to this side's own bid while pair <= PAIR_CAP."""
    target = min(side_bid(side, yes_bid, yes_ask),
                 floor_tick(PAIR_CAP - filled_other_px, tick))
    return max(current, floor_tick(target, tick))


# ------------------------------------------------------------- state

class Book:
    """Durable per-market state: own orders, fills, locked pairs."""

    def __init__(self, path: Path):
        self.path = path
        self.d: Dict[str, Any] = {"markets": {}, "own_order_ids": [],
                                  "pairs": [], "lone": {}}
        if path.exists():
            try:
                self.d = json.loads(path.read_text())
            except Exception:
                pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, default=str))
        tmp.replace(self.path)

    def m(self, slug: str) -> Dict[str, Any]:
        return self.d["markets"].setdefault(slug, {"orders": {}, "pos": {
            "YES": "0", "NO": "0"}, "cost": {"YES": "0", "NO": "0"},
            "tick": None, "min_qty": None, "done": False})

    def unmatched_cost(self, slug: str) -> D:
        mk = self.d["markets"].get(slug)
        if not mk:
            return D(0)
        y, n = D(mk["pos"]["YES"]), D(mk["pos"]["NO"])
        cy, cn = D(mk["cost"]["YES"]), D(mk["cost"]["NO"])
        if y > n and y > 0:
            return cy * (y - n) / y
        if n > y and n > 0:
            return cn * (n - y) / n
        return D(0)

    def total_unmatched(self) -> D:
        return sum((self.unmatched_cost(s) for s in self.d["markets"]), D(0))


# ------------------------------------------------------------- venue I/O

class Venue:
    def __init__(self):
        from src import pmus_q1_executor_v4 as q1
        from src import pmus_micro_executor as micro
        from src import pmus_spring_gateway_candidates as gw
        os.environ.setdefault("PMUS_Q1_STATE_DIR", str(STATE_DIR / "q1"))
        self.q1, self.micro, self.gw = q1, micro, gw
        self.t = q1.Transport()
        self._spec: Dict[str, Any] = {}

    def gget(self, path: str) -> Any:
        return self.gw.http_get_json(path)

    def live_sports(self) -> list:
        events, _ = self.gw.fetch_live_events(self.gget)
        return [tg for tg in self.gw.live_event_targets(events)
                if str(tg["slug"]).startswith(SPORTS_PREFIXES)]

    def bbo(self, slug: str) -> Optional[Dict[str, D]]:
        md = (self.gget(f"/v1/markets/{slug}/bbo") or {}).get("marketData") or {}
        b, a = self.q1._d(md.get("bestBid")), self.q1._d(md.get("bestAsk"))
        if b is None or a is None:
            return None
        return {"bid": b, "ask": a}

    def spec(self, slug: str) -> Optional[Dict[str, Any]]:
        hit = self._spec.get(slug)
        if hit and time.monotonic() - hit[0] < 60:
            return hit[1]
        out = self._spec_fetch(slug)
        self._spec[slug] = (time.monotonic(), out)
        return out

    def _spec_fetch(self, slug: str) -> Optional[Dict[str, Any]]:
        m = (self.gget(f"/v1/market/slug/{slug}") or {}).get("market") or {}
        if not (m.get("active") is True and m.get("closed") is False
                and m.get("archived") is False):
            return None
        tick = self.q1._d(m.get("orderPriceMinTickSize")) or D("0.01")
        mq = self.q1._d(m.get("minimumTradeQty")) or D("1")
        return {"tick": tick, "min_qty": mq}

    def account(self) -> Dict[str, Any]:
        pos = self.t.request("GET", "/v1/portfolio/positions")
        rows = pos.get("positions") if isinstance(pos, Mapping) else None
        held: Dict[str, D] = {}
        if isinstance(rows, Mapping):
            for slug, p in rows.items():
                if isinstance(p, Mapping):
                    v = self.q1._d(p.get("netPositionDecimal") or
                                   p.get("netPosition") or p.get("quantity"))
                    if v:
                        held[slug] = v
        elif isinstance(rows, list):
            for p in rows:
                if isinstance(p, Mapping) and p.get("marketSlug"):
                    v = self.q1._d(p.get("netPositionDecimal") or
                                   p.get("netPosition") or p.get("quantity"))
                    if v:
                        held[p["marketSlug"]] = v
        oo = self.t.request("GET", "/v1/orders/open")
        orders = [o for o in (oo.get("orders") or []) if isinstance(o, Mapping)]
        return {"positions": held, "open_orders": orders}

    def status(self, oid: str) -> Dict[str, Any]:
        """{'qty': filled, 'px': outcome-terms avg, 'open': bool}."""
        try:
            o = self.q1._order_status(self.t, oid)
        except self.q1.RejectedNoFill:
            return {"qty": D(0), "px": None, "open": False, "state": "REJECTED"}
        state = str(o.get("state") or o.get("status") or "").upper()
        f = self.micro.authoritative_fill(o, D(str(o.get("quantity") or
                                                  o.get("qty") or "1e9")),
                                          accept_partial=True)
        is_open = not any(x in state for x in
                          ("CANCEL", "EXPIRED", "REJECT", "FILLED")) or \
            "PARTIALLY" in state
        return {"qty": f["qty"] if f else D(0), "px": f["price"] if f else None,
                "open": is_open, "state": state}

    def place(self, slug: str, side: str, px: D, qty: D, stage: str) -> str:
        return self.micro.place_limit_buy(self.t, slug, side, px, qty, stage,
                                          post_only=True)

    def cancel(self, oid: str) -> bool:
        return self.q1.cancel_exact(self.t, oid)


# ------------------------------------------------------------- engine

class Engine:
    def __init__(self, venue: Venue, book: Book):
        self.v, self.b = venue, book
        self.stop = False

    def own(self) -> set:
        return set(self.b.d["own_order_ids"])

    def foreign_exposure(self, slug: str, acct: Mapping[str, Any]) -> bool:
        mine = slug in self.b.d["markets"]
        if acct["positions"].get(slug) and not mine:
            return True
        for o in acct["open_orders"]:
            if o.get("marketSlug") == slug and \
                    str(o.get("id")) not in self.own():
                return True
        return False

    def active(self) -> list:
        return [s for s, mk in self.b.d["markets"].items() if not mk["done"]]

    def _record_fill(self, slug: str, side: str, oid: str, st: Dict) -> None:
        mk = self.b.m(slug)
        o = mk["orders"][oid]
        new = st["qty"] - D(o.get("filled", "0"))
        if new > 0 and st["px"] is not None:
            mk["pos"][side] = str(D(mk["pos"][side]) + new)
            mk["cost"][side] = str(D(mk["cost"][side]) + new * st["px"])
            o["filled"] = str(st["qty"])
            log("FILL", slug=slug, side=side, px=st["px"], qty=new,
                order_id=oid, pos=mk["pos"],
                label="venue-reported fill; not realized until settlement")

    def cancel_all_own(self, why: str) -> None:
        for slug, mk in self.b.d["markets"].items():
            for oid, o in list(mk["orders"].items()):
                if o.get("open"):
                    try:
                        self.v.cancel(oid)
                        st = self.v.status(oid)
                        self._record_fill(slug, o["side"], oid, st)
                        o["open"] = st["open"]
                    except Exception as exc:          # noqa: BLE001
                        log("cancel_error", slug=slug, order_id=oid,
                            err=str(exc)[:160])
        self.b.save()
        log("cancelled_all_own", why=why)

    def place(self, slug: str, side: str, px: D, qty: D, stage: str) -> None:
        oid = self.v.place(slug, side, px, qty, stage)
        mk = self.b.m(slug)
        mk["orders"][oid] = {"side": side, "px": str(px), "qty": str(qty),
                             "filled": "0", "open": True, "stage": stage}
        self.b.d["own_order_ids"].append(oid)
        self.b.save()
        log("ORDER", slug=slug, side=side, px=px, qty=qty, stage=stage,
            order_id=oid)

    def step_market(self, slug: str) -> None:
        mk = self.b.m(slug)
        # 1. refresh own orders
        for oid, o in list(mk["orders"].items()):
            if not o.get("open"):
                continue
            st = self.v.status(oid)
            self._record_fill(slug, o["side"], oid, st)
            o["open"] = st["open"]
        self.b.save()
        spec = self.v.spec(slug)
        bbo = self.v.bbo(slug)
        if spec is None or bbo is None:
            if spec is None:
                for oid, o in mk["orders"].items():
                    if o.get("open"):
                        try:
                            self.v.cancel(oid)
                        except Exception:             # noqa: BLE001
                            pass
                        o["open"] = False
                mk["done"] = True
                self.b.save()
                log("market_closed", slug=slug, pos=mk["pos"],
                    unmatched_cost=self.b.unmatched_cost(slug))
            return
        tick = spec["tick"]
        y, n = D(mk["pos"]["YES"]), D(mk["pos"]["NO"])
        open_by_side = {o["side"]: (oid, o) for oid, o in mk["orders"].items()
                        if o.get("open")}
        if y == n:
            # flat or paired: (re)quote both sides D ticks under the touch
            if y > 0 and not mk.get("pair_logged_at") == str(y):
                cpair = (D(mk["cost"]["YES"]) + D(mk["cost"]["NO"])) / y
                self.b.d["pairs"].append({"slug": slug, "qty": str(y),
                                          "pair_cost": str(cpair), "t": now_iso()})
                mk["pair_logged_at"] = str(y)
                log("PAIR_LOCKED", slug=slug, pairs=y, avg_pair_cost=cpair,
                    locked_edge_per_pair=ONE - cpair,
                    label="locked pair economics; realized at settlement")
            want = open_prices(bbo["bid"], bbo["ask"], tick)
            blocked = (self.b.total_unmatched() >= TOTAL_CAP or
                       os.getenv(ENABLED_ENV, "").upper() != "YES")
            qty = max(QTY, spec["min_qty"])
            in_place = (want is not None and not blocked and
                        set(open_by_side) == {"YES", "NO"} and
                        all(D(open_by_side[sd][1]["px"]) == want[sd]
                            for sd in ("YES", "NO")))
            if in_place:
                return
            # OWNER 2026-11-14 "Pairs should be set simultaneously": never
            # leave one side resting alone. Pull every open leg together,
            # then (still flat) set both legs together.
            for side, (oid, o) in open_by_side.items():
                self.v.cancel(oid)
                st = self.v.status(oid)
                self._record_fill(slug, side, oid, st)
                o["open"] = st["open"]
            self.b.save()
            if D(mk["pos"]["YES"]) != D(mk["pos"]["NO"]) or \
                    any(o.get("open") for o in mk["orders"].values()):
                return                  # a leg filled while pulling: complete it
            if want is None or blocked:
                return
            if (want["YES"] + want["NO"]) * qty > PER_MARKET_CAP:
                return
            mk["partner_px"] = {k: str(v) for k, v in want.items()}
            for side in ("YES", "NO"):
                self.place(slug, side, want[side], qty, "two_sided_open")
            return
        # unbalanced: the lagging side is the completion
        lag = "NO" if y > n else "YES"
        lead = "YES" if lag == "NO" else "NO"
        need = abs(y - n)
        lead_px = D(mk["cost"][lead]) / D(mk["pos"][lead])
        # stop adding to the leading side
        if lead in open_by_side:
            oid, o = open_by_side[lead]
            self.v.cancel(oid)
            st = self.v.status(oid)
            self._record_fill(slug, lead, oid, st)
            o["open"] = False
            self.b.save()
            return
        cur = open_by_side.get(lag)
        # The partner was set together with the filled leg: leave it at its
        # price (pair locked <= PAIR_CAP). Only a partial fill re-sizes it.
        if cur:
            rem_open = D(cur[1]["qty"]) - D(cur[1]["filled"])
            if rem_open == need:
                return
            self.v.cancel(cur[0])
            st = self.v.status(cur[0])
            self._record_fill(slug, lag, cur[0], st)
            cur[1]["open"] = st["open"]
            self.b.save()
            return
        px = D(mk.get("partner_px", {}).get(lag) or "0") or floor_tick(
            min(side_bid(lag, bbo["bid"], bbo["ask"]) - DEPTH_TICKS * tick,
                PAIR_CAP - lead_px), tick)
        px = min(px, floor_tick(PAIR_CAP - lead_px, tick))
        if px < tick or px >= side_bid(lag, bbo["bid"], bbo["ask"]) + \
                (bbo["ask"] - bbo["bid"]):
            return
        if need >= spec["min_qty"]:
            self.place(slug, lag, px, need, "two_sided_complete")

    def select(self, acct: Mapping[str, Any]) -> None:
        if os.getenv(ENABLED_ENV, "").upper() != "YES":
            return
        if self.b.total_unmatched() >= TOTAL_CAP:
            return
        if len(self.active()) >= MAX_MARKETS:
            return
        try:
            targets = self.v.live_sports()
        except Exception as exc:                      # noqa: BLE001
            log("discovery_error", err=str(exc)[:160])
            return
        scored = []
        for tg in targets:
            slug = tg["slug"]
            if slug in self.b.d["markets"]:
                continue
            meta = tg.get("market_meta") or {}
            b = self.v.gw._qnum(meta.get("bestBidQuote"))
            a = self.v.gw._qnum(meta.get("bestAskQuote"))
            if b is None or a is None:
                continue
            b, a = D(str(b)), D(str(a))
            if not (BAND_LO <= b <= BAND_HI):
                continue
            scored.append((a - b, abs(b - D("0.5")), slug))
        scored.sort()
        for _sp, _c, slug in scored:
            if len(self.active()) >= MAX_MARKETS:
                break
            if self.foreign_exposure(slug, acct):
                log("skip_foreign_exposure", slug=slug)
                continue
            spec = self.v.spec(slug)
            bbo = self.v.bbo(slug) if spec else None
            if not spec or not bbo or open_prices(bbo["bid"], bbo["ask"],
                                                   spec["tick"]) is None:
                continue
            self.b.m(slug)
            self.b.save()
            log("SELECTED", slug=slug, bid=bbo["bid"], ask=bbo["ask"],
                tick=spec["tick"])

    def report(self) -> None:
        pairs = self.b.d["pairs"]
        log("heartbeat", active=self.active(),
            unmatched_cost_total=self.b.total_unmatched(),
            pairs=len(pairs),
            locked_edge_usd=sum((ONE - D(p["pair_cost"])) * D(p["qty"])
                                for p in pairs) if pairs else 0,
            enabled=os.getenv(ENABLED_ENV, ""), depth_ticks=DEPTH_TICKS,
            caps={"per_market": PER_MARKET_CAP, "total": TOTAL_CAP,
                  "markets": MAX_MARKETS, "pair_cap": PAIR_CAP, "qty": QTY})

    def run(self) -> int:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stop", True))
        log("boot", caps={"per_market": PER_MARKET_CAP, "total": TOTAL_CAP,
                          "markets": MAX_MARKETS, "depth_ticks": DEPTH_TICKS,
                          "qty": QTY, "pair_cap": PAIR_CAP})
        last_sel = 0.0
        last_hb = 0.0
        while not self.stop:
            try:
                if os.getenv(ENABLED_ENV, "").upper() != "YES":
                    self.cancel_all_own("disabled")
                    time.sleep(30)
                    continue
                if time.monotonic() - last_sel > 20:
                    self.select(self.v.account())
                    last_sel = time.monotonic()
                for slug in self.active():
                    if self.stop:
                        break
                    try:
                        self.step_market(slug)
                    except Exception as exc:          # noqa: BLE001
                        log("market_error", slug=slug,
                            err=f"{type(exc).__name__}:{str(exc)[:200]}")
                if time.monotonic() - last_hb > 60:
                    self.report()
                    last_hb = time.monotonic()
            except Exception as exc:                  # noqa: BLE001
                log("loop_error", err=f"{type(exc).__name__}:{str(exc)[:200]}")
            time.sleep(LOOP_S)
        self.cancel_all_own("sigterm")
        return 0


def main() -> int:
    return Engine(Venue(), Book(STATE_DIR / "state.json")).run()


if __name__ == "__main__":
    raise SystemExit(main())
