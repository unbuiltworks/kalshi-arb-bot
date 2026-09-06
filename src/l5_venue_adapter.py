"""Venue adapters feeding ONE normalized L5 state/action interface.

OWNER 5554263833 (Gemini transfer prep, item 2). The frozen L5 model
(`analysis/l5/l5_action_model_v2.json`) is conditioned on a venue-free
state -- price band x time-to-expiry x behavioral imbalance, plus the
last relation and last size bucket -- and emits a RELATION
(open/extend/complete/sell). Everything venue-specific lives in an
adapter: how a raw book becomes YES/NO prices, which clock gives TTE,
tick and price bounds, fee math, the fee-safe complement bound, the
open-side convention and settlement semantics. No Gemini strategy is
put into the model; a Gemini market is just another source of the same
normalized state.

Shared pieces:

* `NormalizedBook`   -- YES-quoted top of book with NO prices derived,
                        tick, bounds, TTE and provenance.
* `L5Inventory`      -- exact-Decimal per-market inventory with the
                        behavioral-unit residual (first fill = 1 unit),
                        mirroring the live PMUS session's Inventory.
* `l5_state(...)`    -- the model's base-state key from a book+inventory.
* `decision_record`  -- the canonical decision record (same field set
                        as the live PMUS decision log) with venue and
                        market stamped, so a shared dashboard reads both
                        venues' shadows identically.

Adapters:

* `PMUSAdapter`      -- Polymarket US sports (live venue): tick 0.005,
                        band 0.20..0.80, taker 0.06*p*(1-p), maker rebate
                        0.0125*p*(1-p) NEVER counted toward edge,
                        complement bound from `pmus_q1_executor`.
* `GeminiAdapter`    -- Gemini BTC-5m prediction pairs, vendored
                        mechanics from `polymarket_l5.transfer_backtest`
                        (@80f40c86): Up == YES, tick 0.01, band 0.20..0.80,
                        maker fee ceil-cent 0.0175*q*p*(1-p) counted
                        against edge, expiry from the ticker stamp.

Read-only; nothing here places orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal as D
from typing import Any, Mapping, Optional, Protocol

from src.polymarket_l5.action_model import state_key
from src.polymarket_l5 import transfer_backtest as tb
from src import pmus_q1_executor as q1

ONE = D("1")
MAX_RESIDUAL_UNITS = 3            # shared planner bound (pmus_spring_l5_session)
PAIR_TARGET = D("0.98")           # OWNER 5533476555 observation cap
SPORTS_TTE_DEFAULT_S = 1e9        # no settlement clock -> coarsest bucket
SIZE_BUCKET_DEFAULT = "q<5"       # q=1 behavioral unit


def _d(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:
        return None


@dataclass
class NormalizedBook:
    venue: str
    market_id: str
    t: float
    yes_bid: Optional[D]
    yes_ask: Optional[D]
    yes_bid_qty: Optional[D]
    yes_ask_qty: Optional[D]
    tick: D
    min_price: D
    max_price: D
    tte_s: float
    last_px: Optional[D] = None
    extra: dict = field(default_factory=dict)

    # NO side derived from the YES book: buying NO == selling YES
    @property
    def no_bid(self) -> Optional[D]:
        return (ONE - self.yes_ask) if self.yes_ask is not None else None

    @property
    def no_ask(self) -> Optional[D]:
        return (ONE - self.yes_bid) if self.yes_bid is not None else None

    @property
    def mid(self) -> Optional[D]:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return (self.yes_bid + self.yes_ask) / 2

    @property
    def spread(self) -> Optional[D]:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid

    def join_price(self, side_yes: bool) -> Optional[D]:
        """Maker join at the touch, in the side's own price, tick-floored."""
        px = self.yes_bid if side_yes else self.no_bid
        if px is None:
            return None
        return (px / self.tick).to_integral_value(rounding=ROUND_DOWN) * self.tick

    def opposite_ask(self, side_yes: bool) -> Optional[D]:
        return self.yes_ask if side_yes else self.no_ask

    def as_dict(self) -> dict:
        return {"venue": self.venue, "market_id": self.market_id, "t": self.t,
                "yes_bid": str(self.yes_bid), "yes_ask": str(self.yes_ask),
                "yes_bid_qty": str(self.yes_bid_qty),
                "yes_ask_qty": str(self.yes_ask_qty),
                "no_bid": str(self.no_bid), "no_ask": str(self.no_ask),
                "tick": str(self.tick), "tte_s": round(self.tte_s, 1),
                "spread": str(self.spread)}


class L5Inventory:
    """Exact-Decimal per-market inventory; the BEHAVIORAL residual is in
    action units (first fill defines one unit, partials proportional,
    sign preserved). Mirrors the live PMUS session's Inventory."""

    def __init__(self) -> None:
        self.up = self.dn = D("0")
        self.cost_up = self.cost_dn = D("0")
        self.last_rel: Optional[str] = None
        self.last_px: Optional[D] = None
        self.last_px_outcome: Optional[D] = None      # canonical convention
        self.last_size_behavioral: Optional[str] = None  # model-scale bucket
        # OWNER 5558477213 A, completed: the model's imbalance is in SHARES
        # and co-varies with size; the clone's behavioral residual is kept
        # in model-scale shares (each fill = L5's target size for that
        # state|relation) beside the unit-based one
        self.up_shares_behavioral: float = 0.0
        self.dn_shares_behavioral: float = 0.0
        self.unit: Optional[D] = None
        self.actions: list[dict] = []

    @property
    def residual(self) -> D:
        return self.up - self.dn

    @property
    def matched(self) -> D:
        return min(self.up, self.dn)

    @property
    def total(self) -> D:
        return self.up + self.dn

    def avg_up(self) -> Optional[D]:
        return (self.cost_up / self.up) if self.up else None

    def avg_dn(self) -> Optional[D]:
        return (self.cost_dn / self.dn) if self.dn else None

    def spent(self) -> D:
        return self.cost_up + self.cost_dn

    def record_behavioral_shares(self, side_up: bool, shares: float,
                                 size_bucket: Optional[str] = None) -> None:
        """Model-scale accounting of ONE fill: L5's target size (shares)
        for the state|relation the fill was made in, on the filled side."""
        if side_up:
            self.up_shares_behavioral += float(shares)
        else:
            self.dn_shares_behavioral += float(shares)
        if size_bucket:
            self.last_size_behavioral = size_bucket

    def behavioral_residual_shares(self) -> Optional[float]:
        """Signed model-scale residual in shares (None until a fill has been
        recorded with its behavioral size)."""
        if self.up_shares_behavioral == 0.0 and self.dn_shares_behavioral == 0.0:
            return None
        return self.up_shares_behavioral - self.dn_shares_behavioral

    def behavioral_residual(self) -> float:
        if not self.unit:
            return 0.0
        return float(self.residual / self.unit)

    def record(self, side_up: bool, px: D, qty: D, relation: str,
               t: Optional[float] = None) -> None:
        if self.unit is None and qty > 0:
            self.unit = qty
        if side_up:
            self.up += qty
            self.cost_up += px * qty
        else:
            self.dn += qty
            self.cost_dn += px * qty
        self.last_rel = relation
        self.last_px = px if side_up else (ONE - px)
        # OWNER 5558477213 B: the canonical L5 history keys the price band
        # by the price of the OUTCOME traded (a Down fill at 0.08 is
        # p<10c), never mirrored to the YES scale; kept beside last_px
        self.last_px_outcome = px
        self.actions.append({"t": t, "relation": relation,
                             "side": "YES" if side_up else "NO",
                             "px": str(px), "qty": str(qty),
                             "inventory_after": self.snapshot()})

    def pair_cost(self) -> Optional[D]:
        if self.up and self.dn:
            return self.avg_up() + self.avg_dn()
        return None

    def snapshot(self) -> dict:
        return {"up": str(self.up), "dn": str(self.dn),
                "matched": str(self.matched), "residual": str(self.residual),
                "avg_up": str(self.avg_up()) if self.up else None,
                "avg_dn": str(self.avg_dn()) if self.dn else None,
                "spent": str(self.spent()),
                "behavioral_residual": self.behavioral_residual()}


def l5_state(book: NormalizedBook, inv: L5Inventory) -> str:
    """The model's base-state key: last own price (else market bid),
    venue TTE, behavioral residual -- identical on every venue."""
    px = inv.last_px if inv.last_px is not None else book.yes_bid
    return state_key(float(px if px is not None else 0), book.tte_s,
                     inv.behavioral_residual())


def l5_state_v2(book: NormalizedBook, inv: L5Inventory) -> str:
    """OWNER 5558477213 B: the base-state key under the CANONICAL price
    convention. Post-fill: the price of the outcome last traded (never
    mirrored to the YES scale -- the frozen model is not mirror-symmetric:
    57 of 78 mirrored band pairs differ). Pre-fill: the cheaper touch, i.e.
    the price of the outcome the shared open convention would buy.
    Residual and TTE unchanged. `l5_state` (v1) is kept for before/after."""
    if inv.last_px_outcome is not None:
        px = inv.last_px_outcome
    else:
        y, n = book.yes_bid, book.no_bid
        cands = [p for p in (y, n) if p is not None]
        px = min(cands) if cands else None
    # imbalance in model-scale SHARES when the fills carry their behavioral
    # size (the model's imbalance and last_size buckets are both in shares
    # and co-vary: a q>40 open never leaves an imb1-10 state in L5 history)
    shares = inv.behavioral_residual_shares()
    resid = shares if shares is not None else inv.behavioral_residual()
    return state_key(float(px if px is not None else 0), book.tte_s, resid)


def open_side_yes(book: NormalizedBook) -> Optional[bool]:
    """SHARED adapter convention (pmus_sports_l5_decision.open_side_yes):
    the model predicts a relation, never a side; open the cheaper touch,
    ties to YES. Same rule on every venue."""
    y, n = book.yes_bid, book.no_bid
    if y is None and n is None:
        return None
    if n is None:
        return True
    if y is None:
        return False
    return y <= n


class VenueAdapter(Protocol):
    venue: str

    def normalize(self, raw: Mapping[str, Any], now: float) -> Optional[NormalizedBook]: ...
    def maker_fee(self, px: D, qty: D) -> D: ...
    def taker_fee(self, px: D, qty: D) -> Optional[D]: ...
    def complement_price(self, surplus_avg: D, opp_ask: D, tick: D) -> tuple[Optional[D], dict]: ...
    def entry_ok(self, px: D) -> bool: ...
    def settlement(self) -> dict: ...
    def provenance(self) -> dict: ...


class PMUSAdapter:
    venue = "PMUS"

    def normalize(self, raw: Mapping[str, Any], now: float,
                  settlement_t: Optional[float] = None) -> Optional[NormalizedBook]:
        bid, ask = _d(raw.get("bid")), _d(raw.get("ask"))
        if bid is None and ask is None:
            return None
        tte = (max(settlement_t - now, 0.0) if settlement_t
               else SPORTS_TTE_DEFAULT_S)
        return NormalizedBook(
            venue=self.venue, market_id=str(raw.get("slug") or raw.get("market_id")),
            t=now, yes_bid=bid, yes_ask=ask,
            yes_bid_qty=_d(raw.get("bid_qty")), yes_ask_qty=_d(raw.get("ask_qty")),
            tick=_d(raw.get("tick")) or q1.TICK, min_price=q1.MIN_PRICE,
            max_price=q1.MAX_PRICE, tte_s=tte, last_px=_d(raw.get("last")),
            extra={"shares_traded": raw.get("shares_traded")})

    def maker_fee(self, px: D, qty: D) -> D:
        return D("0")                       # rebate exists; NEVER counted

    def taker_fee(self, px: D, qty: D) -> Optional[D]:
        return q1.taker_fee(D(str(px))) * D(str(qty))

    def complement_price(self, surplus_avg: D, opp_ask: D,
                         tick: D) -> tuple[Optional[D], dict]:
        hard = q1.complement_bound(surplus_avg)
        spring = PAIR_TARGET - D(str(surplus_avg))
        info = {"hard_bound_px": str(hard) if hard is not None else None,
                "pair_target_px": str(spring)}
        if hard is None:
            return None, {**info, "regime": "no_fee_safe_complement"}
        px = min(spring, D(str(hard)), D(str(opp_ask)) - tick)
        px = (px / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        if px < q1.MIN_PRICE or px > q1.MAX_PRICE or px <= 0:
            return None, {**info, "regime": "no_legal_maker_price"}
        return px, {**info, "regime": "pair_target" if px <= spring
                    else "hard_bound_only", "chosen_px": str(px)}

    def entry_ok(self, px: D) -> bool:
        return q1.entry_ok(D(str(px)))

    def settlement(self) -> dict:
        return {"authority": "venue POSITION_RESOLUTION activity / "
                             "afterPosition.realized", "clock": "game end "
                             "(no fixed expiry; TTE bucket coarsest)",
                "payout": "1.00 to the winning outcome"}

    def provenance(self) -> dict:
        return {"book": "/v1/markets/{slug}/bbo (YES bid/ask)",
                "prints": "sharesTraded delta + recorder WS tape",
                "fees": "taker 0.06*p*(1-p); maker rebate 0.0125*p*(1-p) not counted"}


class GeminiAdapter:
    venue = "GEMINI"

    def normalize(self, raw: Mapping[str, Any], now: Optional[float] = None,
                  expiry: Optional[float] = None) -> Optional[NormalizedBook]:
        """Tape row {symbol,t,bid,bid_sz,ask,ask_sz} or a live /v1/book
        reduced to the same keys. Up == YES; expiry from the ticker."""
        sym = str(raw.get("symbol") or raw.get("market_id"))
        t = float(raw.get("t") if raw.get("t") is not None else (now or 0.0))
        bid, ask = _d(raw.get("bid")), _d(raw.get("ask"))
        if bid is None and ask is None:
            return None
        if expiry is None:
            expiry = tb.ticker_expiry_epoch(tb.event_of_symbol(sym))
        if expiry is None:
            return None
        return NormalizedBook(
            venue=self.venue, market_id=sym, t=t, yes_bid=bid, yes_ask=ask,
            yes_bid_qty=_d(raw.get("bid_sz")), yes_ask_qty=_d(raw.get("ask_sz")),
            tick=tb.TICK, min_price=tb.MIN_PRICE, max_price=tb.MAX_PRICE,
            tte_s=max(expiry - t, 0.0), last_px=_d(raw.get("last")),
            extra={"expiry": expiry, "up_is_yes": True})

    def maker_fee(self, px: D, qty: D) -> D:
        return tb.maker_fee(D(str(px)), D(str(qty)))

    def taker_fee(self, px: D, qty: D) -> Optional[D]:
        return None                         # not vendored: GAP (unverified)

    def complement_price(self, surplus_avg: D, opp_ask: D,
                         tick: D) -> tuple[Optional[D], dict]:
        """Vendored fee-aware bound (ceil-cent maker fee on BOTH legs),
        tightened by the shared pair<=0.98 target and strictly below the
        opposite ask, on the 0.01 grid."""
        avg = D(str(surplus_avg))
        hard = tb.complement_price("YES", avg, tb.MIN_EDGE)   # symmetric
        spring = PAIR_TARGET - avg
        info = {"hard_bound_px": str(hard) if hard is not None else None,
                "pair_target_px": str(spring)}
        if hard is None:
            return None, {**info, "regime": "no_fee_safe_complement"}
        px = min(spring, hard, D(str(opp_ask)) - tick)
        px = (px / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        if px < tb.MIN_PRICE or px > tb.MAX_PRICE:
            return None, {**info, "regime": "no_legal_maker_price"}
        if tb.pair_edge(avg, px) < tb.MIN_EDGE:
            return None, {**info, "regime": "no_fee_safe_complement"}
        return px, {**info, "regime": "pair_target" if px <= spring
                    else "hard_bound_only", "chosen_px": str(px)}

    def entry_ok(self, px: D) -> bool:
        return tb.MIN_PRICE <= D(str(px)) <= tb.MAX_PRICE

    def settlement(self) -> dict:
        return {"authority": "venue settlement (60s rolling RFR, "
                             "GRR-KAIKO_RFR_BTCUSD_60S)",
                "clock": "fixed expiry stamped in the ticker",
                "payout": "1.00 to UP or DOWN",
                "replay_proxy": "last mid before expiry >=0.80 UP / <=0.20 "
                                "DOWN / else AMBIGUOUS (THEORETICAL)"}

    def provenance(self) -> dict:
        return {"book": "api.gemini.com/v1/book/{symbol} (UP contract)",
                "prints": "api.gemini.com/v1/trades/{symbol}",
                "fees": "maker ceil-cent 0.0175*q*p*(1-p) pre-rebate @80f40c86; "
                        "taker not vendored"}


def decision_record(*, venue: str, market_id: str, t: float, state: str,
                    inv: L5Inventory, prop: Mapping[str, Any], acted: str,
                    refusal: Optional[str], planned_size: Mapping[str, Any],
                    shadow: Mapping[str, Any], book: Optional[NormalizedBook],
                    policy: str = "modal") -> dict:
    """Canonical decision record: the live PMUS field set plus venue,
    market, time, policy track and the normalized book."""
    sampled = (shadow.get("stochastic") or {}).get("sampled")
    return {"venue": venue, "market_id": market_id, "t": t, "policy": policy,
            "state": state, "last_rel": inv.last_rel,
            "v2_recommended": prop.get("recommended_relation"),
            "relation_ranked": prop.get("relation_ranked"),
            "state_n": prop.get("state_n"),
            "off_distribution": bool(prop.get("off_distribution")),
            "persist_extend_p": prop.get("persist_extend_p"),
            "switch_complete_p": prop.get("switch_complete_p"),
            "acted": acted, "refusal": refusal,
            "inventory_before": inv.snapshot(),
            "planned_safe_size": dict(planned_size),
            "shadow": dict(shadow),
            "shadow_differs_from_executed": bool(
                sampled is not None and sampled != acted),
            "book": book.as_dict() if book else None,
            # OWNER 5558477213 A/B: both state conventions, both last_size
            # values and both off flags (v1 lookup vs canonical lookup)
            "lookup": prop.get("lookup"),
            "orders_placed": 0}
