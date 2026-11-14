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
import queue
import signal
import threading
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
# OWNER 2026-11-14 "If a pair is just sitting there for a while, cancel and
# resubmit closer": every STALE_S unfilled, one tick closer to the touch.
STALE_S = float(os.getenv("PMUS_TWO_SIDED_STALE_S", "60"))
# OWNER 2026-11-14 "set up the daily reward credit check": the venue's own
# /v1/incentives/earnings (credited vs pending split) every REWARD_CHECK_S.
REWARD_CHECK_S = float(os.getenv("PMUS_TWO_SIDED_REWARD_CHECK_S", "3600"))
# OWNER 2026-11-14 "close out single-side fills early": a lone leg unpaired
# for EXIT_S is completed by TAKING the complement at its ask, but only while
# that pair costs <= EXIT_MAX_PAIR (a small, bounded realized loss). Beyond
# that the loss is already sunk and the maker completion keeps resting.
EXIT_S = float(os.getenv("PMUS_TWO_SIDED_EXIT_S", "180"))
EXIT_MAX_PAIR = _env_d("PMUS_TWO_SIDED_EXIT_MAX_PAIR", "1.03")
# OWNER 2026-11-14: college football swings from ~45c to <10c within minutes,
# past the 180s/1.03 exit. CFB lone legs exit sooner and accept a little more.
EXIT_S_CFB = float(os.getenv("PMUS_TWO_SIDED_EXIT_S_CFB", "60"))
EXIT_MAX_PAIR_CFB = _env_d("PMUS_TWO_SIDED_EXIT_MAX_PAIR_CFB", "1.05")


def exit_rule(slug: str) -> tuple:
    if "-cfb-" in slug:
        return EXIT_S_CFB, EXIT_MAX_PAIR_CFB
    return EXIT_S, EXIT_MAX_PAIR
STATE_DIR = Path(os.getenv("PMUS_TWO_SIDED_STATE_DIR",
                           "/state/pmus-spring/two_sided"))
SPORTS_PREFIXES = ("aec-", "asc-", "tsc-", "atc-", "astatc-")

# OWNER 2026-11-14 liquidity-reward mode. DEFAULT OFF: with
# PMUS_TWO_SIDED_REWARDS unset every code path below is skipped and the bot
# behaves exactly as before. When on: live sports markets carrying a paying
# liquidity program (GET /v1/incentives) are selected FIRST; in them both legs
# join the touch (shallowest pair-cap-legal depth whose legs sit within the
# program's maxSpread of mid) at REWARD_QTY contracts; the per-market cap
# there is REWARD_PER_MARKET_CAP on WORST LONE-LEG cost; and for EVERY market
# a new pair opens only if total unmatched + other markets' resting-pair
# worst lone leg + this pair's worst lone leg <= TOTAL_CAP. Program rewards
# are THEORETICAL and never enter P&L.
REWARDS_ENV = "PMUS_TWO_SIDED_REWARDS"
REWARD_QTY = _env_d("PMUS_TWO_SIDED_REWARD_QTY", "10")
REWARD_PER_MARKET_CAP = _env_d("PMUS_TWO_SIDED_REWARD_PER_MARKET_USD", "7.50")
REWARD_REFRESH_S = float(os.getenv("PMUS_TWO_SIDED_REWARD_REFRESH_S", "300"))
REWARD_LOG_S = float(os.getenv("PMUS_TWO_SIDED_REWARD_LOG_S", "60"))
# optional comma-separated slug prefixes; empty = any rewarded sports market
REWARD_ALLOW_ENV = "PMUS_TWO_SIDED_REWARD_ALLOW"


# OWNER 2026-11-14 realtime reaction ("stop being picked off"). DEFAULT OFF:
# with PMUS_TWO_SIDED_WS unset no socket is opened, no key is written and
# every code path below behaves exactly as before. When on, a read-only
# market-data socket (src/pmus_two_sided_ws.py) feeds Venue.bbo (fresh <= 3s,
# else the gateway poll) and every touch change wakes the main loop at once:
#   * an UNFILLED pair whose touch moved >= 1 tick since it was priced (or
#     whose opposite side moved toward it) is pulled together and re-set by
#     the normal step at the new price;
#   * a move of >= JUMP_TICKS ticks in one update pulls unfilled opening legs
#     and blocks a new pair in that market for JUMP_PAUSE_S. Completion legs
#     (one side filled) are never pulled by this; they lock the pair.
WS_ENV = "PMUS_TWO_SIDED_WS"
JUMP_TICKS = int(os.getenv("PMUS_TWO_SIDED_JUMP_TICKS", "2"))
# A jump is also measured in PRICE: on 0.005-tick books (college football)
# 2 ticks is 1c of noise, which paused CFB ~60 times in 2 min live (19:05Z).
JUMP_MIN_PX = _env_d("PMUS_TWO_SIDED_JUMP_MIN_PX", "0.02")
JUMP_PAUSE_S = float(os.getenv("PMUS_TWO_SIDED_JUMP_PAUSE_S", "15"))


def ws_on() -> bool:
    return os.getenv(WS_ENV, "").strip().lower() in ("1", "yes", "true", "on")


def ticks_moved(prev: Mapping[str, D], new: Mapping[str, D], tick: D) -> int:
    """Largest best-bid / best-ask move between two tops, in whole ticks."""
    m = D(0)
    for k in ("bid", "ask"):
        if prev.get(k) is not None and new.get(k) is not None:
            m = max(m, abs(D(new[k]) - D(prev[k])))
    return int((m / tick).to_integral_value(rounding=ROUND_DOWN))


def stale_reasons(quoted: Mapping[str, Any], new: Mapping[str, D],
                  tick: D) -> list:
    """Why an unfilled pair priced off `quoted` is stale at `new`.
    YES leg's touch = best bid, its opposite = best ask (moving DOWN is
    toward it). NO leg's touch = 1 - best ask, its opposite = 1 - best bid
    (best bid moving UP is toward it)."""
    qb, qa = D(str(quoted["bid"])), D(str(quoted["ask"]))
    out = []
    if abs(new["bid"] - qb) >= tick:
        out.append("yes_touch_moved")
        if new["bid"] > qb:
            out.append("no_opposite_toward")
    if abs(new["ask"] - qa) >= tick:
        out.append("no_touch_moved")
        if new["ask"] < qa:
            out.append("yes_opposite_toward")
    return out


def rewards_on() -> bool:
    return os.getenv(REWARDS_ENV, "").strip().lower() in (
        "1", "yes", "true", "on")


def reward_allowed(slug: str) -> bool:
    raw = os.getenv(REWARD_ALLOW_ENV, "").strip()
    if not raw:
        return True
    return any(slug.startswith(p.strip()) for p in raw.split(",")
               if p.strip())


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


def reward_open_prices(yes_bid: D, yes_ask: D, tick: D,
                       max_spread: Optional[float]) -> Optional[tuple]:
    """(prices, depth) for a rewarded market, or None.

    Depth 0 = both legs AT the touch (YES bid at best bid, NO bid at
    1 - best ask, i.e. a YES offer at best ask). If that pair breaks
    PAIR_CAP (spread < 2c at a 0.98 cap) take the next deeper legal depth,
    exactly as the normal ladder does. The chosen legs must ALSO sit within
    the program's maxSpread of mid (|YES - mid| and |(1 - NO) - mid|);
    deeper is only farther from mid, so the first legal depth that fails
    maxSpread ends the search (None -> quote as a non-rewarded market)."""
    from src.pmus_sports_rewards import legs_within_max_spread
    for d in range(0, DEPTH_TICKS + 1):
        p = open_prices(yes_bid, yes_ask, tick, depth=d)
        if p is None:
            continue
        if legs_within_max_spread(float(p["YES"]), float(p["NO"]),
                                  float(yes_bid), float(yes_ask), max_spread):
            return p, d
        return None
    return None


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

    def resting_pair_exposure(self, slug: str) -> D:
        """Worst NEW lone-leg cost the market's resting orders can create:
        when balanced, one open leg filling alone (the dearer remainder).
        When unbalanced the open order is the completion, which only
        reduces unmatched cost -> 0."""
        mk = self.d["markets"].get(slug)
        if not mk or mk.get("done"):
            return D(0)
        if D(mk["pos"]["YES"]) != D(mk["pos"]["NO"]):
            return D(0)
        legs = [D(o["px"]) * (D(o["qty"]) - D(o.get("filled", "0")))
                for o in mk["orders"].values() if o.get("open")]
        return max(legs, default=D(0))

    def committed_excluding(self, slug: str) -> D:
        """Total unmatched + every OTHER market's resting-pair exposure."""
        return self.total_unmatched() + sum(
            (self.resting_pair_exposure(s) for s in self.d["markets"]
             if s != slug), D(0))


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

    ws = None              # src.pmus_two_sided_ws.TwoSidedWs when WS is on

    def bbo(self, slug: str) -> Optional[Dict[str, D]]:
        if self.ws is not None:
            hit = self.ws.fresh_bbo(slug)
            if hit is not None:
                return hit
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

    def place(self, slug: str, side: str, px: D, qty: D, stage: str,
              post_only: bool = True) -> str:
        return self.micro.place_limit_buy(self.t, slug, side, px, qty, stage,
                                          post_only=post_only)

    def cancel(self, oid: str) -> bool:
        return self.q1.cancel_exact(self.t, oid)


# ------------------------------------------------------------- engine

class Engine:
    def __init__(self, venue: Venue, book: Book):
        self.v, self.b = venue, book
        self._last_reward = time.monotonic() - REWARD_CHECK_S + 120  # first ~2min after boot
        self.stop = False
        # reward mode only: {slug: (fetched_monotonic, program_row | None)}
        self._prog: Dict[str, Any] = {}
        self._rget = None
        # realtime mode only (PMUS_TWO_SIDED_WS=1 -> venue.ws is set)
        self.ws = getattr(venue, "ws", None)
        self._wake = threading.Event()
        self._evq: "queue.Queue" = queue.Queue()
        self.reactive_pulls = 0
        self.jump_pauses = 0
        if self.ws is not None:
            self.ws.on_change = self._on_ws_change

    # ------------------------------------------------ realtime (WS) mode
    def _on_ws_change(self, slug: str, prev: Mapping, new: Mapping,
                      t: float) -> None:
        """SOCKET THREAD: enqueue and wake the main loop; nothing else.
        Stamped with this process's monotonic receipt time (latency_ms)."""
        self._evq.put((slug, dict(prev), dict(new), time.monotonic()))
        self._wake.set()

    def drain_ws_events(self) -> None:
        """MAIN THREAD: act on every queued touch change, one pass per slug."""
        if self.ws is None:
            return
        self.ws.set_slugs(self.active())
        moves: Dict[str, list] = {}
        while True:
            try:
                slug, prev, new, t = self._evq.get_nowait()
            except queue.Empty:
                break
            moves.setdefault(slug, []).append((prev, new, t))
        for slug, mv in moves.items():
            try:
                self.react(slug, mv)
            except Exception as exc:                  # noqa: BLE001
                log("market_error", slug=slug, at="ws_react",
                    err=f"{type(exc).__name__}:{str(exc)[:200]}")

    def react(self, slug: str, moves: list) -> None:
        mk = self.b.d["markets"].get(slug)
        if not mk or mk.get("done"):
            return
        spec = self.v.spec(slug) or {}
        tick = spec.get("tick") or D("0.01")
        t0 = moves[0][2]
        jump = max(ticks_moved(p, n, tick) for p, n, _t in moves)
        flat = D(mk["pos"]["YES"]) == D(mk["pos"]["NO"])
        open_legs = [o for o in mk["orders"].values() if o.get("open")]
        new = moves[-1][1]
        if jump >= JUMP_TICKS and jump * tick >= JUMP_MIN_PX:
            mk["jump_pause_until"] = time.time() + JUMP_PAUSE_S
            self.jump_pauses += 1
            self.b.save()
            log("jump_pause", slug=slug, ticks=jump, prev=moves[0][0],
                new=new, pause_s=JUMP_PAUSE_S,
                pulls_opening_legs=bool(flat and open_legs),
                completion_kept=bool(not flat and open_legs))
            if flat and open_legs:
                self._pull(slug, f"jump_{jump}_ticks", t0)
            return
        if not flat or not open_legs:
            return                 # completion legs: re-evaluated as today
        quoted = mk.get("quoted_bbo") or moves[0][0]
        why = stale_reasons(quoted, new, tick)
        if why:
            self._pull(slug, ",".join(why), t0)
            self.step_market(slug)          # re-set both legs at the new touch

    def _pull(self, slug: str, reason: str, t0: float) -> None:
        mk = self.b.m(slug)
        legs = 0
        for oid, o in list(mk["orders"].items()):
            if not o.get("open"):
                continue
            legs += 1
            self.v.cancel(oid)
            st = self.v.status(oid)
            self._record_fill(slug, o["side"], oid, st)
            o["open"] = st["open"]
        self.b.save()
        self.reactive_pulls += 1
        log("reactive_pull", slug=slug, reason=reason, legs=legs,
            latency_ms=round((time.monotonic() - t0) * 1000),
            pos=mk["pos"])

    def _sleep(self, seconds: float) -> None:
        """LOOP_S sleep; in WS mode every touch change is handled at once."""
        if self.ws is None:
            time.sleep(seconds)
            return
        end = time.monotonic() + seconds
        while not self.stop:
            left = end - time.monotonic()
            if left <= 0:
                break
            if self._wake.wait(left):
                self._wake.clear()
                self.drain_ws_events()

    # ------------------------------------------------ reward programs
    def _reward_get(self):
        if self._rget is None:
            from src.pmus_sports_rewards import SpacedGet
            self._rget = SpacedGet(self.v.gget)
        return self._rget

    def reward_programs(self, slugs: list) -> Dict[str, Any]:
        """{slug: paying period dict} for the given slugs; programs cached
        REWARD_REFRESH_S, only stale/unseen slugs are queried (<= 2 rps)."""
        from src import pmus_sports_rewards as R
        now = time.monotonic()
        stale = [s for s in slugs if s not in self._prog or
                 now - self._prog[s][0] > REWARD_REFRESH_S]
        if stale:
            found, diag = R.fetch_programs(self._reward_get(), stale)
            pm = R.build_program_map(found)
            for s in stale:
                self._prog[s] = (now, pm.get(s))
            log("reward_discovery", queried=len(stale), rewarded=len(pm),
                requests=diag["requests"], errors=diag["errors"][:3],
                label=R.LABEL)
        out = {}
        for s in slugs:
            prog = (self._prog.get(s) or (0, None))[1]
            if not prog or not reward_allowed(s):
                continue
            per = R.paying_period(prog, time.time(), event_live=True)
            if per:
                out[s] = per
        return out

    def refresh_active_rewards(self) -> None:
        """Keep each active market's paying period current (a period that
        stops paying drops the market back to normal quoting)."""
        act = self.active()
        if not act:
            return
        try:
            pp = self.reward_programs(act)
        except Exception as exc:                      # noqa: BLE001
            log("reward_discovery_error", err=str(exc)[:160])
            return
        for s in act:
            mk = self.b.m(s)
            new = pp.get(s)
            if (mk.get("reward") or None) != new:
                mk["reward"] = new
        self.b.save()

    def _reward_quote(self, slug: str, mk: Dict[str, Any], bbo: Mapping,
                      tick: D) -> Optional[Dict[str, Any]]:
        """Reward-mode prices for a rewarded market, else None."""
        if not rewards_on():
            return None
        rp = mk.get("reward")
        if not rp:
            return None
        r = reward_open_prices(bbo["bid"], bbo["ask"], tick,
                               rp.get("maxSpread"))
        self._log_reward(slug, mk, rp, bbo, tick, r)
        if r is None:
            return None
        return {"want": r[0], "depth": r[1]}

    def _log_reward(self, slug: str, mk: Dict[str, Any], rp: Mapping,
                    bbo: Mapping, tick: D, r: Optional[tuple]) -> None:
        last = float(mk.get("reward_logged_at") or 0)
        if time.time() - last < REWARD_LOG_S:
            return
        mk["reward_logged_at"] = time.time()
        est: Dict[str, Any] = {"qualifies": None, "est_share": None}
        try:
            from src import pmus_sports_rewards as R
            book = (self._reward_get()(f"/v1/markets/{slug}/book") or {}
                    ).get("marketData") or {}
            est = R.estimate(book, rp, float(REWARD_QTY), float(tick))
        except Exception as exc:                      # noqa: BLE001
            est["book_error"] = str(exc)[:120]
        log("reward_program", slug=slug, program_id=rp.get("programId"),
            pool=rp.get("pool"), period=rp.get("period"),
            period_s=rp.get("period_s"), targetSize=rp.get("targetSize"),
            maxSpread=rp.get("maxSpread"), bid=bbo["bid"], ask=bbo["ask"],
            quote_depth=(r[1] if r else None),
            quote=({k: str(v) for k, v in r[0].items()} if r else None),
            price_within_max_spread=r is not None,
            qualifies=est.get("qualifies"), est_share=est.get("est_share"),
            est=est, label="THEORETICAL reward estimate; never P&L")

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

    def place(self, slug: str, side: str, px: D, qty: D, stage: str,
              post_only: bool = True) -> None:
        oid = (self.v.place(slug, side, px, qty, stage) if post_only else
               self.v.place(slug, side, px, qty, stage, post_only=False))
        mk = self.b.m(slug)
        mk["orders"][oid] = {"side": side, "px": str(px), "qty": str(qty),
                             "filled": "0", "open": True, "stage": stage,
                             "placed_at": time.time()}
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
                mk["pair_set_at"] = time.time()
                log("PAIR_LOCKED", slug=slug, pairs=y, avg_pair_cost=cpair,
                    locked_edge_per_pair=ONE - cpair,
                    label="locked pair economics; realized at settlement")
            mk.pop("complete_px", None)
            mk.pop("lone_since", None)
            if self.ws is not None and \
                    time.time() < float(mk.get("jump_pause_until") or 0):
                # JUMP PAUSE: no opening legs rest in this market until the
                # pause ends (completion legs never reach this branch)
                if open_by_side:
                    self._pull(slug, "jump_pause_hold", time.monotonic())
                return
            set_at = float(mk.setdefault("pair_set_at", time.time()))
            steps = int((time.time() - set_at) // STALE_S) if STALE_S > 0 else 0
            want = None
            rq = self._reward_quote(slug, mk, bbo, tick)   # None unless on
            if rq is not None:
                want = rq["want"]
                qty = max(REWARD_QTY, spec["min_qty"])
            else:
                for d in range(max(0, DEPTH_TICKS - steps), DEPTH_TICKS + 1):
                    want = open_prices(bbo["bid"], bbo["ask"], tick, depth=d)
                    if want is not None:
                        break
                qty = max(QTY, spec["min_qty"])
            blocked = (self.b.total_unmatched() >= TOTAL_CAP or
                       os.getenv(ENABLED_ENV, "").upper() != "YES")
            worst_leg = max(want.values()) * qty if want else D(0)
            if rewards_on() and want is not None and not blocked and \
                    self.b.committed_excluding(slug) + worst_leg > TOTAL_CAP:
                # every market, reward mode: a new pair opens only if all
                # lone legs that could exist at once stay within TOTAL_CAP
                blocked = True
                if mk.get("precheck_logged") != str(worst_leg):
                    mk["precheck_logged"] = str(worst_leg)
                    log("total_cap_precheck_block", slug=slug,
                        committed=self.b.committed_excluding(slug),
                        worst_leg=worst_leg, total_cap=TOTAL_CAP)
            in_place = (want is not None and not blocked and
                        set(open_by_side) == {"YES", "NO"} and
                        all(D(open_by_side[sd][1]["px"]) == want[sd]
                            for sd in ("YES", "NO")) and
                        (not rewards_on() or
                         all(D(open_by_side[sd][1]["qty"]) == qty
                             for sd in ("YES", "NO"))))
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
            if want is None and not blocked:
                # flat and out of band/too wide: free the slot for another
                # live market after 2 minutes (pairs already locked stay).
                idle = mk.setdefault("idle_since", time.time())
                if time.time() - float(idle) > 120:
                    mk["done"] = True
                    self.b.save()
                    log("released_flat_market", slug=slug, pos=mk["pos"],
                        bid=bbo["bid"], ask=bbo["ask"])
                return
            mk.pop("idle_since", None)
            if want is None or blocked:
                return
            if rq is not None:
                # rewarded: cap the WORST LONE-LEG cost in this market
                if self.b.unmatched_cost(slug) + worst_leg > \
                        REWARD_PER_MARKET_CAP:
                    return
            elif (want["YES"] + want["NO"]) * qty > PER_MARKET_CAP:
                return
            mk["partner_px"] = {k: str(v) for k, v in want.items()}
            stage = "two_sided_open_rewarded" if rq is not None \
                else "two_sided_open"
            if self.ws is not None:
                mk["quoted_bbo"] = {"bid": str(bbo["bid"]),
                                    "ask": str(bbo["ask"])}
            for side in ("YES", "NO"):
                self.place(slug, side, want[side], qty, stage)
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
        lone_since = float(mk.setdefault("lone_since", time.time()))
        comp_ask = (bbo["ask"] if lag == "YES" else ONE - bbo["bid"])
        exit_s, exit_max = exit_rule(slug)
        if exit_s > 0 and time.time() - lone_since >= exit_s \
                and lead_px + comp_ask <= exit_max \
                and need >= spec["min_qty"]:
            if cur:                          # pull the resting maker first
                self.v.cancel(cur[0])
                st = self.v.status(cur[0])
                self._record_fill(slug, lag, cur[0], st)
                cur[1]["open"] = st["open"]
                self.b.save()
                return
            log("EXIT_LONE_LEG", slug=slug, lead=lead, lead_px=lead_px,
                take_side=lag, take_px=comp_ask, qty=need,
                pair_cost=lead_px + comp_ask,
                age_s=round(time.time() - lone_since),
                label="taker completion; realized pair loss bounded by "
                      "EXIT_MAX_PAIR, settles at par")
            self.place(slug, lag, comp_ask, need, "two_sided_exit",
                       post_only=False)
            return
        # The partner was set together with the filled leg: leave it at its
        # price (pair locked <= PAIR_CAP). Only a partial fill re-sizes it.
        cap_px = floor_tick(PAIR_CAP - lead_px, tick)
        lag_bid = side_bid(lag, bbo["bid"], bbo["ask"])
        if cur:
            rem_open = D(cur[1]["qty"]) - D(cur[1]["filled"])
            age = time.time() - float(cur[1].get("placed_at") or time.time())
            closer = min(D(cur[1]["px"]) + tick, lag_bid, cap_px)
            if rem_open == need and (age < STALE_S or
                                     closer <= D(cur[1]["px"])):
                return
            if rem_open == need:
                # sat unfilled: resubmit one tick closer (never past the
                # pair cap, never above this side's own bid)
                mk["complete_px"] = str(closer)
                log("tighten_complete", slug=slug, side=lag,
                    from_px=cur[1]["px"], to_px=closer, age_s=round(age))
            self.v.cancel(cur[0])
            st = self.v.status(cur[0])
            self._record_fill(slug, lag, cur[0], st)
            cur[1]["open"] = st["open"]
            self.b.save()
            return
        px = D(mk.get("complete_px") or mk.get("partner_px", {}).get(lag)
               or "0") or floor_tick(
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
        rewarded: Dict[str, Any] = {}
        if rewards_on() and scored:
            try:
                rewarded = self.reward_programs([s for *_x, s in scored])
            except Exception as exc:                  # noqa: BLE001
                log("reward_discovery_error", err=str(exc)[:160])
            # rewarded in-band markets FIRST, richest pool rate first; the
            # rest keep today's order (tightest spread, nearest 0.50)
            scored.sort(key=lambda r: (
                0 if r[2] in rewarded else 1,
                -rewarded[r[2]]["pool_rate_usd_s"] if r[2] in rewarded else 0,
                r))
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
            mk = self.b.m(slug)
            if slug in rewarded:
                mk["reward"] = rewarded[slug]
            self.b.save()
            log("SELECTED", slug=slug, bid=bbo["bid"], ask=bbo["ask"],
                tick=spec["tick"])
            if slug in rewarded:
                rp = rewarded[slug]
                log("reward_program", slug=slug, at="select",
                    program_id=rp["programId"], pool=rp["pool"],
                    period=rp["period"], period_s=rp["period_s"],
                    targetSize=rp["targetSize"], maxSpread=rp["maxSpread"],
                    pool_rate_usd_s=rp["pool_rate_usd_s"],
                    qualifies=None, est_share=None,
                    label="THEORETICAL reward program; never P&L")

    def _latest_pairs(self) -> Dict[str, Any]:
        last: Dict[str, Any] = {}
        for p in self.b.d.get("pairs") or []:
            last[p["slug"]] = p
        return last

    def reward_check(self) -> None:
        """OBSERVED reward earnings from the venue. Only rows the venue marks
        credited are money; pending/skipped are reported, never counted."""
        try:
            from src import pmus_incentive_audit as IA
            e = IA.earnings(self.v.t)
        except Exception as exc:                          # noqa: BLE001
            log("REWARD_CHECK", ok=False,
                err=f"{type(exc).__name__}:{str(exc)[:160]}")
            return
        ours = set(self.b.d.get("markets") or {})
        mine = [r for r in (e.get("rows_detail") or [])
                if str(r.get("marketSlug") or "") in ours]
        log("REWARD_CHECK", ok=bool(e.get("ok")), endpoint=e.get("endpoint"),
            rows=e.get("rows"), credited_usd=e.get("credited_usd"),
            accrued_not_credited_usd=e.get("accrued_not_credited_usd"),
            by_status=e.get("by_status"), by_program=e.get("by_program"),
            rows_on_our_markets=len(mine), sample_ours=mine[:10],
            error=e.get("error"),
            label="venue /v1/incentives/earnings; ONLY credited_usd is money")

    def report(self) -> None:
        pairs = self.b.d["pairs"]
        extra = {} if self.ws is None else {
            "ws": self.ws.status(), "reactive_pulls": self.reactive_pulls,
            "jump_pauses": self.jump_pauses}
        log("heartbeat", **extra, active=self.active(),
            unmatched_cost_total=self.b.total_unmatched(),
            pairs=len(pairs),
            # each PAIR_LOCKED row carries the market's CUMULATIVE pairs and
            # average cost, so only the LATEST row per market counts (summing
            # every row double-counted; owner report 18:28Z corrected).
            matched_pairs=str(sum((D(p["qty"]) for p in
                                   self._latest_pairs().values()), D(0))),
            locked_edge_usd=str(sum(((ONE - D(p["pair_cost"])) * D(p["qty"])
                                     for p in self._latest_pairs().values()),
                                    D(0)).quantize(D("0.0001"))),
            enabled=os.getenv(ENABLED_ENV, ""), depth_ticks=DEPTH_TICKS,
            caps={"per_market": PER_MARKET_CAP, "total": TOTAL_CAP,
                  "markets": MAX_MARKETS, "pair_cap": PAIR_CAP, "qty": QTY},
            rewards_enabled=rewards_on(),
            rewarded_active=sum(1 for s in self.active()
                                if self.b.m(s).get("reward")),
            reward_caps={"qty": REWARD_QTY,
                         "per_market_lone_leg": REWARD_PER_MARKET_CAP},
            reward_label="program rewards THEORETICAL; not in P&L")

    def run(self) -> int:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stop", True))
        log("boot", **({"ws": True} if self.ws is not None else {}), caps={"per_market": PER_MARKET_CAP, "total": TOTAL_CAP,
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
                    if rewards_on():
                        self.refresh_active_rewards()
                    last_sel = time.monotonic()
                for slug in self.active():
                    if self.stop:
                        break
                    self.drain_ws_events()          # no-op unless WS mode
                    try:
                        self.step_market(slug)
                    except Exception as exc:          # noqa: BLE001
                        log("market_error", slug=slug,
                            err=f"{type(exc).__name__}:{str(exc)[:200]}")
                if REWARD_CHECK_S > 0 and \
                        time.monotonic() - self._last_reward > REWARD_CHECK_S:
                    self._last_reward = time.monotonic()
                    self.reward_check()
                if time.monotonic() - last_hb > 60:
                    self.report()
                    last_hb = time.monotonic()
            except Exception as exc:                  # noqa: BLE001
                log("loop_error", err=f"{type(exc).__name__}:{str(exc)[:200]}")
            try:
                self.drain_ws_events()
            except Exception as exc:                  # noqa: BLE001
                log("loop_error", err=f"{type(exc).__name__}:{str(exc)[:200]}")
            self._sleep(LOOP_S)
        self.cancel_all_own("sigterm")
        return 0


_LAST_ACCESS_LOG = [0.0]
REGISTRY_KEY = "l5/live_orders.json"      # same path as l5_order_registry_server
LANE = "pmus-sports-two-sided"


def registry_doc(book: "Book") -> Dict[str, Any]:
    """The orders this bot placed and still believes live, in the shape the
    l5 watchdog's ownership oracle reads, plus its own freshness stamp. A
    fresh document is the bot attesting that it is ALIVE AND MANAGING these
    resting orders (maker bids that legitimately rest for minutes)."""
    rows = []
    for slug, mk in (book.d.get("markets") or {}).items():
        for oid, o in (mk.get("orders") or {}).items():
            if o.get("open"):
                rows.append({"venue_order_id": oid, "market": slug,
                             "side": o.get("side"), "session_id": LANE,
                             "price": o.get("px"), "qty": o.get("qty")})
    return {"readable": True, "lane": LANE, "manages_own_resting": True,
            "enabled": os.getenv(ENABLED_ENV, "").upper() == "YES",
            "orders": rows, "as_of_utc": now_iso(),
            "labels": ("venue order ids pmus_sports_two_sided placed and "
                       "still believes live; it cancels them itself on "
                       "requote, disable and SIGTERM")}


def serve_registry(book: "Book", port: int) -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):                               # noqa: N802
            path = self.path.split("?", 1)[0].lstrip("/")
            if path not in (REGISTRY_KEY, "health"):
                self.send_response(404); self.end_headers(); return
            body = json.dumps(registry_doc(book) if path == REGISTRY_KEY
                              else {"ok": True}, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *a):
            # one line per registry read, so a cross-service fetch (the l5
            # watchdog) is visible here; health checks stay quiet
            if REGISTRY_KEY in (self.path or ""):
                now = time.monotonic()
                if now - _LAST_ACCESS_LOG[0] > 60:
                    _LAST_ACCESS_LOG[0] = now
                    log("registry_read", client=self.client_address[0])

    try:                       # Railway private networking is IPv6
        import socket as _sock

        class V6(ThreadingHTTPServer):
            address_family = _sock.AF_INET6
        srv = V6(("::", port), H)
    except OSError:
        srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("registry_serving", port=port, path=REGISTRY_KEY,
        family=("ipv6_dualstack" if srv.address_family != 2 else "ipv4"))
    # SELF-TEST through the private DNS name other services use
    host = os.getenv("RAILWAY_PRIVATE_DOMAIN", "")
    if host:
        def _probe():
            import urllib.request
            time.sleep(2)
            url = f"http://{host}:{port}/{REGISTRY_KEY}"
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    log("registry_selftest", ok=True, url=url, status=r.status)
            except Exception as exc:                     # noqa: BLE001
                log("registry_selftest", ok=False, url=url,
                    err=f"{type(exc).__name__}:{str(exc)[:120]}")
        threading.Thread(target=_probe, daemon=True).start()


def main() -> int:
    book = Book(STATE_DIR / "state.json")
    try:
        serve_registry(book, int(os.getenv("PMUS_TWO_SIDED_REGISTRY_PORT",
                                           "8090")))
    except Exception as exc:                             # noqa: BLE001
        log("registry_error", err=f"{type(exc).__name__}:{str(exc)[:160]}")
    venue = Venue()
    if ws_on():
        try:
            from src.pmus_two_sided_ws import TwoSidedWs
            venue.ws = TwoSidedWs(log=log)
            venue.ws.set_slugs([s for s, mk in book.d["markets"].items()
                                if not mk.get("done")])
            venue.ws.start()
            log("ws_enabled", jump_ticks=JUMP_TICKS, jump_pause_s=JUMP_PAUSE_S,
                fresh_s=venue.ws.fresh_s)
        except Exception as exc:                         # noqa: BLE001
            venue.ws = None
            log("ws_error", err=f"{type(exc).__name__}:{str(exc)[:160]}",
                fallback="gateway_poll_only")
    return Engine(venue, book).run()


if __name__ == "__main__":
    raise SystemExit(main())
