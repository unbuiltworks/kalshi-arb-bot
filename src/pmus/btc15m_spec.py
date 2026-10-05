"""FROZEN specification for the PMUS BTC15m underdog price x time experiment.

Frozen 2026-10-05, BEFORE any underdog outcome, checkpoint calibration or
strategy P&L was inspected. Nothing in this module may be re-tuned after
results are seen; a change here invalidates the pre-registration and must be
recorded as a new, dated freeze.

Outcome-blind by construction: this module contains no tape, no outcomes and
no fitted parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from src.pmus import fees

FREEZE_DATE = "2026-10-05"
FREEZE_LABEL = "pmus-btc15m-underdog-v1"

# ---------------------------------------------------------------------------
# 1. Frozen checkpoints (minutes remaining in the 15-minute window)
# ---------------------------------------------------------------------------
CHECKPOINTS_MIN_REMAINING: tuple[int, ...] = (14, 12, 10, 8, 6, 5, 4, 3, 2, 1)

# ---------------------------------------------------------------------------
# 2. Frozen executable-price buckets (cents, [lo, hi) on the executable ASK)
# ---------------------------------------------------------------------------
PRICE_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("<10c",    1, 10),
    ("10-15c", 10, 15),
    ("15-20c", 15, 20),
    ("20-25c", 20, 25),
    ("25-30c", 25, 30),
    ("30-35c", 30, 35),
    ("35-40c", 35, 40),
    ("40-45c", 40, 45),
    ("45-50c", 45, 50),
)

# States where NEITHER outcome has an executable ask strictly below 50c are not
# dropped and not merged into any price bucket: they are tallied separately so
# that opportunity frequency and per-day economics stay honest.
NO_UNDERDOG_BUCKET = "no-executable-underdog(>=50c both sides)"

WINDOWS_PER_DAY = 96  # 15-minute windows in 24h; used for $/day normalisation

# ---------------------------------------------------------------------------
# 3. Sizing rule
# ---------------------------------------------------------------------------
TARGET_NET_PROFIT_CENTS = 500          # >= $5 NET settlement profit on a win
MIN_CONTRACTS = 1                      # PMUS whole-contract minimum
CONTRACT_STEP = 1                      # whole contracts only; no fractions
MAX_CONTRACTS_PER_ENTRY = 1000         # frozen self-imposed capacity cap
DEPTH_MULTIPLE_REQUIRED = 1.0          # displayed touch depth must cover size
ENTRY_LIQUIDITY = "taker"              # entries lift the causal executable ask
PRICE_TICK_CENTS = 1                   # executable asks are whole cents

# ---------------------------------------------------------------------------
# 4. Risk grid
# ---------------------------------------------------------------------------
LOSS_STREAKS: tuple[int, ...] = (5, 10, 15, 20)
WIN_PROB_GRID: tuple[float, ...] = tuple(round(0.025 * i, 3) for i in range(1, 39))  # 0.025..0.950
# Flat staking only. Stake is a function of entry price alone. No Martingale,
# no anti-Martingale, no dependence on prior results, ever.
STAKING_RULE = "flat-per-price (stake depends only on entry price; never on history)"


@dataclass(frozen=True)
class SizingRow:
    """One frozen sizing row for a single executable entry ask."""

    entry_ask_cents: int
    contracts: Optional[int]
    spend_cents: Optional[int]          # dollars actually risked (incl. entry fee)
    entry_fee_cents: Optional[int]
    net_profit_win_cents: Optional[int]
    loss_if_loser_cents: Optional[int]
    breakeven_win_prob: Optional[float]  # fee-inclusive
    required_touch_depth: Optional[int]
    feasible: bool
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def size_for_entry(entry_ask_cents: int) -> SizingRow:
    """Smallest whole-contract size producing >= $5 NET settlement profit on a win.

    Economics (all integer cents):
        entry_fee = PMUS fee for ``q`` contracts at ``entry_ask_cents`` (taker)
        spend     = q * entry_ask_cents + entry_fee          <- risked, and lost in full on a loss
        payout    = q * 100                                  <- settlement on a win
        net_win   = payout - spend                           <- no PMUS settlement fee
        breakeven = spend / (spend + net_win)                <- fee-inclusive win prob

    A loss is total: the underdog settles at 0 and the entire ``spend`` is lost.
    """
    if not (1 <= entry_ask_cents <= 49):
        raise ValueError("experiment domain is executable asks 1c..49c")

    q = MIN_CONTRACTS
    while q <= MAX_CONTRACTS_PER_ENTRY:
        entry_fee = fees.fee_cents(q, entry_ask_cents, "underdog", ENTRY_LIQUIDITY)
        spend = q * entry_ask_cents + entry_fee
        net_win = q * 100 - spend
        if net_win >= TARGET_NET_PROFIT_CENTS:
            return SizingRow(
                entry_ask_cents=entry_ask_cents,
                contracts=q,
                spend_cents=spend,
                entry_fee_cents=entry_fee,
                net_profit_win_cents=net_win,
                loss_if_loser_cents=spend,
                breakeven_win_prob=round(spend / (spend + net_win), 6),
                required_touch_depth=int(-(-q * DEPTH_MULTIPLE_REQUIRED // 1)),
                feasible=True,
            )
        q += CONTRACT_STEP

    return SizingRow(
        entry_ask_cents=entry_ask_cents,
        contracts=None,
        spend_cents=None,
        entry_fee_cents=None,
        net_profit_win_cents=None,
        loss_if_loser_cents=None,
        breakeven_win_prob=None,
        required_touch_depth=None,
        feasible=False,
        note=f"no size <= {MAX_CONTRACTS_PER_ENTRY} contracts reaches $5 net",
    )


def sizing_table() -> list[SizingRow]:
    return [size_for_entry(p) for p in range(1, 50)]


def bucket_for_price(ask_cents: Optional[int]) -> str:
    """Frozen bucket assignment. ``None`` / >=50c -> the separate no-underdog state."""
    if ask_cents is None or ask_cents >= 50 or ask_cents < 1:
        return NO_UNDERDOG_BUCKET
    for name, lo, hi in PRICE_BUCKETS:
        if lo <= ask_cents < hi:
            return name
    return NO_UNDERDOG_BUCKET


def loss_streak_drawdown_cents(row: SizingRow, streak: int) -> Optional[int]:
    """Bankroll consequence of ``streak`` CONSECUTIVE settlement losses, flat staked."""
    if not row.feasible or row.loss_if_loser_cents is None:
        return None
    return row.loss_if_loser_cents * streak


def expected_pnl_cents(row: SizingRow, win_prob: float) -> Optional[float]:
    """Expected P&L per trade at a hypothetical TRUE win probability (fee-inclusive)."""
    if not row.feasible:
        return None
    return win_prob * row.net_profit_win_cents - (1.0 - win_prob) * row.loss_if_loser_cents


def provenance() -> dict:
    return {
        "freeze_label": FREEZE_LABEL,
        "freeze_date": FREEZE_DATE,
        "fee_source": fees.FEE_SOURCE,
        "fee_schedule_fingerprint": fees.fee_schedule_fingerprint(),
        "target_net_profit_cents": TARGET_NET_PROFIT_CENTS,
        "max_contracts_per_entry": MAX_CONTRACTS_PER_ENTRY,
        "staking_rule": STAKING_RULE,
    }
