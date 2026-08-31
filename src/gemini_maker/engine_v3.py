"""V3 Gemini engine: fair-value-gated maker router (strategy brief, canonical
doc docs/GEMINI_PROFIT_REBATE_ENGINE.md).

What changed vs the pair engine (which survives as one exit inside this):
- Entry requires REFERENCE-PRICE EDGE, never flow volume: a maker bid is
  placed only when model fair value exceeds our resting price by the horizon's
  minimum edge. The first leg must be +EV by itself; rebates never count.
- Flow classification gates entries: PAIRABLE / DISLOCATED can enter,
  TOXIC never adds risk, DEAD only earns verified subsidy.
- Inventory has FOUR exits, chosen by expected value: sell held side back as
  maker, buy complement as maker (pair completion, <= $1.00 hard), IOC/FOK
  sell, IOC/FOK buy. Taker exits are LIMIT IOC intents with capped premium,
  used for stale-price capture or bounded rescue only.
- One inventory cycle at a time (global flag from the wrapper), q1 sizing
  until realized evidence justifies more, $10/window deployment ceiling with
  capital reserved for the exit leg.
- A fill is an information event: the wrapper must re-call decide() after
  every fill; this core never escalates on a fill by construction (entry gate
  re-evaluates fair value and flow fresh each call).

Pure decision core: no I/O, no venue calls, nothing here arms live trading.
All constants are v1 proposals pending lag-study validation, then freeze.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from src.gemini_maker.flow_class import FlowClass

PAIR_CEILING = 1.00
WINDOW_SPEND_CEILING = 10.0     # dollars deployed per window (ceiling not target)
BASE_SIZE = 1.0                 # q1 until realized evidence says otherwise
RESCUE_MAX_PREMIUM = 0.03       # max cents-vs-fair paid to cross
MIN_EXIT_EDGE = 0.02            # maker sell must beat fair by this


@dataclass(frozen=True)
class HorizonConfig:
    min_entry_edge: float       # fair - our_price must exceed this to enter
    no_new_risk_s: float        # no fresh entries with less time than this
    rescue_zone_s: float        # IOC exits allowed inside this window


CONFIG_5M = HorizonConfig(min_entry_edge=0.06, no_new_risk_s=60.0,
                          rescue_zone_s=75.0)
CONFIG_15M = HorizonConfig(min_entry_edge=0.04, no_new_risk_s=90.0,
                           rescue_zone_s=120.0)


@dataclass
class InventoryV3:
    yes_qty: float = 0.0
    yes_cost: float = 0.0
    no_qty: float = 0.0
    no_cost: float = 0.0
    realized: float = 0.0

    def buy(self, side: str, qty: float, price: float) -> None:
        if qty <= 0:
            return
        if side == "yes":
            self.yes_qty += qty
            self.yes_cost += qty * price
        elif side == "no":
            self.no_qty += qty
            self.no_cost += qty * price
        else:
            raise ValueError(side)

    def sell(self, side: str, qty: float, price: float) -> None:
        """Sell held contracts at avg cost; realizes P&L."""
        if qty <= 0:
            return
        if side == "yes":
            if qty > self.yes_qty + 1e-9:
                raise ValueError("sell exceeds yes holdings")
            avg = self.yes_cost / self.yes_qty
            self.yes_qty -= qty
            self.yes_cost -= qty * avg
            self.realized += qty * (price - avg)
        elif side == "no":
            if qty > self.no_qty + 1e-9:
                raise ValueError("sell exceeds no holdings")
            avg = self.no_cost / self.no_qty
            self.no_qty -= qty
            self.no_cost -= qty * avg
            self.realized += qty * (price - avg)
        else:
            raise ValueError(side)

    @property
    def spend(self) -> float:
        return self.yes_cost + self.no_cost

    @property
    def residual(self) -> float:
        return self.yes_qty - self.no_qty

    def held_side(self) -> Optional[str]:
        if self.residual > 1e-9:
            return "yes"
        if self.residual < -1e-9:
            return "no"
        return None

    def completion_ceiling(self) -> Optional[float]:
        """Portfolio-safe max price for the missing side (PR #165 math)."""
        qty = abs(self.residual)
        if qty < 1e-9:
            return None
        final_pairs = max(self.yes_qty, self.no_qty)
        return (final_pairs * PAIR_CEILING - self.spend) / qty

    def worst_settlement_loss(self) -> float:
        yes_wins = self.yes_qty - self.spend
        no_wins = self.no_qty - self.spend
        return max(0.0, -min(yes_wins, no_wins))


@dataclass(frozen=True)
class BookV3:
    """Both sides in their own price scale, with top-of-book depth."""
    yes_bid: Optional[float] = None
    yes_bid_depth: float = 0.0
    yes_ask: Optional[float] = None
    yes_ask_depth: float = 0.0

    @property
    def no_bid(self) -> Optional[float]:
        return round(1 - self.yes_ask, 4) if self.yes_ask is not None else None

    @property
    def no_ask(self) -> Optional[float]:
        return round(1 - self.yes_bid, 4) if self.yes_bid is not None else None


@dataclass(frozen=True)
class OrderIntent:
    side: str           # "yes" | "no"
    action: str         # "buy" | "sell"
    price: float
    size: float
    tif: str            # "POST_ONLY" (maker) | "IOC" (limit immediate-or-cancel)
    reason: str


@dataclass(frozen=True)
class MarketDecision:
    mode: str
    intents: List[OrderIntent]
    cancel_all: bool
    reason: str


@dataclass(frozen=True)
class MarketSnapshot:
    market: str
    horizon_minutes: int            # 5 or 15
    book: BookV3
    flow: FlowClass
    fair_p_yes: Optional[float]     # from fair_value; None = no model
    fair_confident: bool            # caller: enough vol/trail data
    seconds_to_expiry: Optional[float]
    inventory: InventoryV3
    in_reward_pool_verified: bool = False   # live-config verified, never assumed


def _config(h: int) -> HorizonConfig:
    return CONFIG_5M if h <= 5 else CONFIG_15M


def _entry_intent(s: MarketSnapshot, cfg: HorizonConfig) -> Optional[OrderIntent]:
    """Fair-value maker entry: one side, q1, edge must clear the gate."""
    if s.fair_p_yes is None or not s.fair_confident:
        return None
    p = s.fair_p_yes
    candidates: List[Tuple[float, OrderIntent]] = []
    if s.book.yes_bid is not None:
        edge = p - s.book.yes_bid
        if edge >= cfg.min_entry_edge:
            candidates.append((edge, OrderIntent(
                "yes", "buy", s.book.yes_bid, BASE_SIZE, "POST_ONLY",
                "FAIR_VALUE_ENTRY")))
    if s.book.no_bid is not None:
        edge = (1 - p) - s.book.no_bid
        if edge >= cfg.min_entry_edge:
            candidates.append((edge, OrderIntent(
                "no", "buy", s.book.no_bid, BASE_SIZE, "POST_ONLY",
                "FAIR_VALUE_ENTRY")))
    if not candidates:
        return None
    candidates.sort(key=lambda c: -c[0])
    intent = candidates[0][1]
    # Reserve exit capital: entry + worst-case completion must fit the ceiling.
    if s.inventory.spend + intent.size * PAIR_CEILING > WINDOW_SPEND_CEILING:
        return None
    return intent


def _exit_intent(s: MarketSnapshot, cfg: HorizonConfig) -> Optional[OrderIntent]:
    """Four-exit inventory router. Picks the best available single action."""
    inv = s.inventory
    held = inv.held_side()
    if held is None:
        return None
    qty = abs(inv.residual)
    p_held = s.fair_p_yes if held == "yes" else (
        1 - s.fair_p_yes if s.fair_p_yes is not None else None)
    held_ask = s.book.yes_ask if held == "yes" else s.book.no_ask
    held_bid = s.book.yes_bid if held == "yes" else s.book.no_bid
    comp = "no" if held == "yes" else "yes"
    comp_bid = s.book.no_bid if comp == "no" else s.book.yes_bid
    comp_ask = s.book.no_ask if comp == "no" else s.book.yes_ask
    ceiling = inv.completion_ceiling()
    in_rescue_zone = (s.seconds_to_expiry is not None
                      and s.seconds_to_expiry <= cfg.rescue_zone_s)
    toxic_against = s.flow.label == "TOXIC"

    options: List[Tuple[float, OrderIntent]] = []  # (EV per contract, intent)

    # 1) Maker sell the held side (same-side round trip).
    if held_ask is not None and p_held is not None:
        sell_px = held_ask  # join the ask
        if sell_px - p_held >= MIN_EXIT_EDGE:
            options.append((sell_px - p_held, OrderIntent(
                held, "sell", sell_px, qty, "POST_ONLY", "ROUND_TRIP_MAKER")))

    # 2) Maker buy the complement (pair completion within the hard ceiling).
    if comp_bid is not None and ceiling is not None:
        buy_px = min(comp_bid, ceiling)
        if buy_px >= 0.01 and buy_px <= ceiling + 1e-9:
            p_comp = (1 - p_held) if p_held is not None else None
            ev = (p_comp - buy_px) if p_comp is not None else 0.0
            locked = PAIR_CEILING - ceiling  # >=0 means locked profit at ceiling
            options.append((max(ev, locked * 0.0), OrderIntent(
                comp, "buy", round(buy_px, 2), qty, "POST_ONLY",
                "PAIR_COMPLETION_MAKER")))

    # 3) IOC sell held (stale bid capture, or bounded rescue).
    if held_bid is not None and p_held is not None:
        stale_edge = held_bid - p_held
        allowed = stale_edge >= MIN_EXIT_EDGE or (
            (in_rescue_zone or toxic_against)
            and p_held - held_bid <= RESCUE_MAX_PREMIUM)
        if allowed:
            options.append((stale_edge, OrderIntent(
                held, "sell", held_bid, qty, "IOC",
                "IOC_STALE_SELL" if stale_edge >= MIN_EXIT_EDGE else "IOC_RESCUE_SELL")))

    # 4) IOC buy complement (only if the completed pair stays <= $1.00).
    if comp_ask is not None and ceiling is not None and comp_ask <= ceiling + 1e-9:
        if in_rescue_zone or toxic_against:
            p_comp = (1 - p_held) if p_held is not None else 0.0
            options.append((p_comp - comp_ask, OrderIntent(
                comp, "buy", comp_ask, qty, "IOC", "IOC_RESCUE_COMPLETE")))

    if not options:
        return None
    # Highest EV wins; maker beats IOC on ties (cheaper, earns not pays).
    options.sort(key=lambda o: (-o[0], 0 if o[1].tif == "POST_ONLY" else 1))
    return options[0][1]


def decide_market(s: MarketSnapshot, *, halted: bool = False,
                  global_cycle_open: bool = False) -> MarketDecision:
    """One evaluation for one market. Wrapper calls on every update and after
    every fill. global_cycle_open=True means another market already holds an
    open inventory cycle: no fresh entries anywhere (one cycle at a time)."""
    if halted:
        return MarketDecision("HALTED", [], True, "HALTED_BY_WRAPPER")
    if s.seconds_to_expiry is None:
        return MarketDecision("HALTED", [], True, "NO_CLOCK")
    cfg = _config(s.horizon_minutes)

    # Inventory management always comes first and is allowed until expiry.
    if s.inventory.held_side() is not None:
        exit_i = _exit_intent(s, cfg)
        if exit_i is None:
            return MarketDecision("INVENTORY", [], True, "NO_SAFE_EXIT_HOLD")
        return MarketDecision("INVENTORY", [exit_i], True, exit_i.reason)

    if s.seconds_to_expiry <= cfg.no_new_risk_s:
        return MarketDecision("OBSERVE", [], True, "NO_NEW_RISK_NEAR_EXPIRY")
    if global_cycle_open:
        return MarketDecision("OBSERVE", [], True, "GLOBAL_CYCLE_OPEN")
    if s.inventory.spend >= WINDOW_SPEND_CEILING - 1e-9:
        return MarketDecision("OBSERVE", [], True, "WINDOW_SPEND_CEILING")

    label = s.flow.label
    if label == "TOXIC":
        return MarketDecision("OBSERVE", [], True, "TOXIC_FLOW")
    if label == "DEAD":
        if s.in_reward_pool_verified:
            # Subsidy quoting delegated to pair_engine's subsidy path by the
            # wrapper; disarmed by default and out of scope for V3 entries.
            return MarketDecision("SUBSIDY_ELIGIBLE", [], True,
                                  "DELEGATE_SUBSIDY")
        return MarketDecision("OBSERVE", [], True, "DEAD_NO_POOL")

    entry = _entry_intent(s, cfg)
    if entry is None:
        return MarketDecision("OBSERVE", [], True, "NO_FAIR_VALUE_EDGE")
    return MarketDecision("FAIR_VALUE_MAKER", [entry], False, entry.reason)


def score_market(s: MarketSnapshot) -> float:
    """Cross-market allocator score: expected entry edge, gated by flow.
    Higher is better; <= 0 means do not allocate fresh risk here."""
    if s.fair_p_yes is None or not s.fair_confident:
        return 0.0
    if s.flow.label in ("TOXIC", "DEAD"):
        return 0.0
    cfg = _config(s.horizon_minutes)
    if s.seconds_to_expiry is None or s.seconds_to_expiry <= cfg.no_new_risk_s:
        return 0.0
    edges = []
    if s.book.yes_bid is not None:
        edges.append(s.fair_p_yes - s.book.yes_bid)
    if s.book.no_bid is not None:
        edges.append((1 - s.fair_p_yes) - s.book.no_bid)
    best = max(edges, default=0.0)
    if best < cfg.min_entry_edge:
        return 0.0
    bonus = 0.02 if s.flow.label == "PAIRABLE" else 0.0
    if s.flow.label == "DISLOCATED":
        bonus += 0.03
    return best + bonus


def rank_markets(snapshots: Sequence[MarketSnapshot]) -> List[Tuple[float, str]]:
    scored = [(score_market(s), s.market) for s in snapshots]
    return sorted([x for x in scored if x[0] > 0], key=lambda x: -x[0])
