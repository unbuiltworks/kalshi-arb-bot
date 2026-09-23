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
                        band 0.20..0.80, taker fee and maker rebate
                        from `src.pmus_fees` (effective-dated; 0.0695 /
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

from src.polymarket_l5.action_model import SIZE_BUCKETS, _bucket, state_key
from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2
# executor / tape-builder code is imported LAZILY (below): the read-only
# recorder image carries this adapter for the canonical state lookup and
# must never import an order path


def _q1():
    from src import pmus_q1_executor as q1
    return q1


def _tb():
    from src.polymarket_l5 import transfer_backtest as tb
    return tb


# venue-observed PMUS sports grid (orderPriceMinTickSize=0.01) used when a
# raw row carries no tick; the executor's crypto constants are never
# imported for a read-only normalisation
PMUS_TICK_DEFAULT = D("0.01")
PMUS_MIN_PRICE, PMUS_MAX_PRICE = D("0.01"), D("0.99")

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


#: PMUS products this adapter can describe. The venue is one venue, but its
#: SETTLEMENT SEMANTICS are not one thing: a sports market resolves when a
#: game ends, with no fixed expiry, while `btc-updown-15m` expires on a hard
#: clock against a named index. Reporting the sports string over a crypto row
#: would mislabel every piece of provenance the lane produces.
PMUS_PRODUCT_SPORTS = "sports"
PMUS_PRODUCT_CRYPTO_UPDOWN = "crypto_updown"
PMUS_PRODUCTS = (PMUS_PRODUCT_SPORTS, PMUS_PRODUCT_CRYPTO_UPDOWN)


class PMUSAdapter:
    venue = "PMUS"

    def __init__(self, product: str = PMUS_PRODUCT_SPORTS) -> None:
        """`product` selects SETTLEMENT/PROVENANCE description only.

        It deliberately changes no price, size, bound or action. The sports
        default preserves every existing construction site.
        """
        if product not in PMUS_PRODUCTS:
            raise ValueError(
                "unknown PMUS product %r; declared: %s"
                % (product, ", ".join(PMUS_PRODUCTS)))
        self.product = product

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
            tick=_d(raw.get("tick")) or PMUS_TICK_DEFAULT,
            min_price=PMUS_MIN_PRICE, max_price=PMUS_MAX_PRICE,
            tte_s=tte, last_px=_d(raw.get("last")),
            extra={"shares_traded": raw.get("shares_traded")})

    def maker_fee(self, px: D, qty: D) -> D:
        return D("0")                       # rebate exists; NEVER counted

    def taker_fee(self, px: D, qty: D) -> Optional[D]:
        return _q1().taker_fee(D(str(px))) * D(str(qty))

    def complement_price(self, surplus_avg: D, opp_ask: D,
                         tick: D) -> tuple[Optional[D], dict]:
        hard = _q1().complement_bound(surplus_avg)
        spring = PAIR_TARGET - D(str(surplus_avg))
        info = {"hard_bound_px": str(hard) if hard is not None else None,
                "pair_target_px": str(spring)}
        if hard is None:
            return None, {**info, "regime": "no_fee_safe_complement"}
        px = min(spring, D(str(hard)), D(str(opp_ask)) - tick)
        px = (px / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        if px < _q1().MIN_PRICE or px > _q1().MAX_PRICE or px <= 0:
            return None, {**info, "regime": "no_legal_maker_price"}
        return px, {**info, "regime": "pair_target" if px <= spring
                    else "hard_bound_only", "chosen_px": str(px)}

    def entry_ok(self, px: D) -> bool:
        return _q1().entry_ok(D(str(px)))

    def settlement(self) -> dict:
        if self.product == PMUS_PRODUCT_CRYPTO_UPDOWN:
            return {
                "authority": "venue POSITION_RESOLUTION activity / "
                             "afterPosition.realized",
                "clock": ("LITERAL. The market serves startTime and endDate "
                          "exactly 15 minutes apart, so seconds-to-expiry is "
                          "a fact and the sports event-progress translation "
                          "must NOT be applied"),
                "index": "CF Benchmarks BRTI",
                "rule": ("settles Up iff the BRTI value at T_end is greater "
                         "than or equal to the value at T_start; each value "
                         "is the simple average of the 60 BRTI prices in the "
                         "last minute before that time, rounded to 2 dp"),
                "payout": "1.00 to the winning outcome",
                "source": "market description served by "
                          "GET /v1/events?series_id=375",
            }
        return {"authority": "venue POSITION_RESOLUTION activity / "
                             "afterPosition.realized", "clock": "game end "
                             "(no fixed expiry; TTE bucket coarsest)",
                "payout": "1.00 to the winning outcome"}

    def provenance(self) -> dict:
        import datetime as _dt
        from src import pmus_fees as _f
        th = _f.provenance()
        out = {"book": "/v1/markets/{slug}/bbo (YES bid/ask)",
               "prints": "sharesTraded delta + recorder WS tape",
               "fees": ("taker %s*C*p*(1-p); maker rebate %s*C*p*(1-p) "
                        "credited at the point of trade and NOT counted "
                        "toward edge (%s)"
                        % (th["taker_theta"], th["maker_rebate_theta"],
                           th["source"]))}
        if self.product == PMUS_PRODUCT_CRYPTO_UPDOWN:
            out["book"] = ("/v1/markets/{slug}/bbo and /book -- PUBLIC BOOK IS "
                           "~30s CACHED (9 distinct ladder states across 88 "
                           "polls); a fresher book requires the authenticated "
                           "WS")
            out["prints"] = ("sharesTraded delta only -- /trades is 404 on the "
                             "public gateway, so trade COUNT is not observable "
                             "and bursts are a LOWER BOUND, never a count")
            out["incentive"] = ("liquidity program crypto_up_down_20260922, "
                                "pool $30 per daily_event shared across the "
                                "program's markets; counted at $0 until "
                                "eligibility and allocation are established")
        return out


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
            expiry = _tb().ticker_expiry_epoch(_tb().event_of_symbol(sym))
        if expiry is None:
            return None
        return NormalizedBook(
            venue=self.venue, market_id=sym, t=t, yes_bid=bid, yes_ask=ask,
            yes_bid_qty=_d(raw.get("bid_sz")), yes_ask_qty=_d(raw.get("ask_sz")),
            tick=_tb().TICK, min_price=_tb().MIN_PRICE, max_price=_tb().MAX_PRICE,
            tte_s=max(expiry - t, 0.0), last_px=_d(raw.get("last")),
            extra={"expiry": expiry, "up_is_yes": True})

    def maker_fee(self, px: D, qty: D) -> D:
        return _tb().maker_fee(D(str(px)), D(str(qty)))

    def taker_fee(self, px: D, qty: D) -> Optional[D]:
        # Vendored 2026-09-15 from the official schedule this repo already
        # carried (user-verified 2026-08-31, see transfer_backtest.taker_fee).
        # It was never unknown -- only unvendored, and returning None here made
        # every conservative Gemini evaluation refuse for want of a number we
        # already had.
        return _tb().taker_fee(D(str(px)), D(str(qty)))

    def complement_price(self, surplus_avg: D, opp_ask: D,
                         tick: D) -> tuple[Optional[D], dict]:
        """Vendored fee-aware bound (ceil-cent maker fee on BOTH legs),
        tightened by the shared pair<=0.98 target and strictly below the
        opposite ask, on the 0.01 grid."""
        avg = D(str(surplus_avg))
        hard = _tb().complement_price("YES", avg, _tb().MIN_EDGE)   # symmetric
        spring = PAIR_TARGET - avg
        info = {"hard_bound_px": str(hard) if hard is not None else None,
                "pair_target_px": str(spring)}
        if hard is None:
            return None, {**info, "regime": "no_fee_safe_complement"}
        px = min(spring, hard, D(str(opp_ask)) - tick)
        px = (px / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        if px < _tb().MIN_PRICE or px > _tb().MAX_PRICE:
            return None, {**info, "regime": "no_legal_maker_price"}
        if _tb().pair_edge(avg, px) < _tb().MIN_EDGE:
            return None, {**info, "regime": "no_fee_safe_complement"}
        return px, {**info, "regime": "pair_target" if px <= spring
                    else "hard_bound_only", "chosen_px": str(px)}

    def entry_ok(self, px: D) -> bool:
        return _tb().MIN_PRICE <= D(str(px)) <= _tb().MAX_PRICE

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


# ---------------------------------------------------------------------------
# Canonical lookup (lookup_version 3) -- the ONE code path both shadows and
# the live session ask the frozen model through. Moved here from
# gemini_l5_shadow_v2 (which re-exports it) so the read-only PMUS recorder
# can run the same lookup without importing any Gemini tape or order code.
# ---------------------------------------------------------------------------
LOOKUP_VERSION = 3
SUPERSEDED_LOOKUP_VERSIONS = {2: "superseded: behavioral last_size with a unit-based "
                                 "imbalance (half translation); diagnostic only"}
EXECUTED_SIZE_BUCKET = "q<5"          # the safety-scaled unit's bucket (v1 input)


def behavioral_size_shares(art: Optional[Mapping[str, Any]], base_state: str,
                           relation: str) -> Optional[float]:
    """L5's p50 size (shares) for this state|relation, None when unknown."""
    row = fid.l5_target_size(art, base_state, relation) if art else {}
    p50 = row.get("shares_p50") if isinstance(row, Mapping) else None
    try:
        return float(p50) if p50 is not None else None
    except (TypeError, ValueError):
        return None


def record_behavioral_fill(inv: L5Inventory, art: Optional[Mapping[str, Any]],
                           state_at_fill: str, relation: str, side_up: bool) -> None:
    """After a fill: the model-scale size (bucket + shares) of the action
    the clone behaviorally made, on the filled side."""
    p50 = behavioral_size_shares(art, state_at_fill, relation)
    bucket = (_bucket(p50, SIZE_BUCKETS) if p50 is not None else EXECUTED_SIZE_BUCKET)
    inv.record_behavioral_shares(side_up, p50 if p50 is not None else 0.0, bucket)


def behavioral_size_bucket(art: Optional[Mapping[str, Any]], base_state: str,
                           relation: str) -> str:
    """The model-scale size bucket of the action the clone BEHAVIORALLY
    targets (L5's p50 for this state|relation), never the safety-scaled
    executable size. Unavailable artifact -> the executed bucket."""
    row = fid.l5_target_size(art, base_state, relation) if art else {}
    p50 = row.get("shares_p50") if isinstance(row, Mapping) else None
    try:
        return _bucket(float(p50), SIZE_BUCKETS) if p50 is not None else EXECUTED_SIZE_BUCKET
    except (TypeError, ValueError):
        return EXECUTED_SIZE_BUCKET


def lookup_inputs(book: NormalizedBook, inv: L5Inventory) -> dict:
    """Both state conventions and both last_size values, so the frozen
    model is looked up on the canonical key while the v1 key stays
    recorded for the before/after."""
    pre_fill = inv.total == 0
    return {"lookup_version": LOOKUP_VERSION,
            "state": l5_state_v2(book, inv), "state_v1": l5_state(book, inv),
            "last_rel": inv.last_rel or "open",
            "last_size_executed": EXECUTED_SIZE_BUCKET,
            "last_size_behavioral": (inv.last_size_behavioral
                                     if not pre_fill and inv.last_size_behavioral
                                     else EXECUTED_SIZE_BUCKET),
            "pre_fill": pre_fill,
            "residual_units": inv.behavioral_residual(),
            "residual_shares_behavioral": inv.behavioral_residual_shares(),
            "price_convention": ("outcome price of the last own fill"
                                 if inv.last_px_outcome is not None
                                 else "cheaper touch (outcome the open convention buys)")}


def proposal_v2(model: Mapping[str, Any], book: NormalizedBook,
                inv: L5Inventory, code_sha: str = "") -> dict:
    """The frozen v2 policy looked up on the canonical key (OWNER
    5558477213 A/B). Pre-fill: the canonical history keys NO first-in-
    window transition (every transition is (prev fill, next action)), so
    the only faithful pre-fill relation is OPEN and off-distribution means
    the BASE state is absent -- no state is added, the model is untouched.
    The v1 lookup's off flag is recorded beside it for the before/after."""
    lk = lookup_inputs(book, inv)
    v1 = pv2.v2_dist(model, lk["state_v1"], lk["last_rel"], lk["last_size_executed"])
    lk["off_distribution_v1"] = bool(v1["backed_off"])
    lk["full_key_v1"] = v1["state"]
    prop = pv2.proposal(model, lk["state"], lk["last_rel"], lk["last_size_behavioral"],
                        residual=inv.behavioral_residual())
    if lk["pre_fill"]:
        base_seen = lk["state"] in ((model.get("base") or {}))
        prop["recommended_relation"] = "open"
        prop["relation_ranked"] = [{"relation": "open", "p": 1.0}]
        prop["persist_extend_p"], prop["switch_complete_p"] = 0.0, 0.0
        prop["off_distribution"] = not base_seen
        prop["state_n"] = sum((model.get("base") or {}).get(lk["state"], {}).values())
        lk["lookup_tier"] = ("pre_fill:first_action_is_open (canonical history keys no "
                             "first-in-window transition; off = base state absent)")
    else:
        lk["lookup_tier"] = "full->base->marginal on the canonical key"
    lk["model_sha"] = str(model.get("_sha256_16") or "")
    lk["code_sha"] = code_sha
    lk["state_inputs"] = {"yes_bid": str(book.yes_bid), "yes_ask": str(book.yes_ask),
                          "no_bid": str(book.no_bid), "tte_s": round(float(book.tte_s), 1),
                          "residual_units": inv.behavioral_residual(),
                          "last_px_outcome": (str(inv.last_px_outcome)
                                              if inv.last_px_outcome is not None else None)}
    prop["lookup"] = lk
    return prop
