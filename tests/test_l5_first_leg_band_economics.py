"""Is the 0.30-0.60 first-leg band blocking favourable two-sided pairs?

The owner asked, and told me not to widen it without supporting economics.
**It is not blocking anything favourable, and the reason is algebraic.**

WHAT THE BAND IS. A STRATEGY parameter, not a hard safety constraint:
env-tunable (`L5_TS_MIN_FIRST_LEG_PRICE` / `..._MAX_...`), applied by
`first_leg_entry_refusal`, and scoped to OPENING only -- its own text says
"completion and inventory management are never blocked by it". It is a
SEPARATE gate from `build_orders`, which accepts essentially the whole
price range at depth 2.

THE ALGEBRA THAT SETTLES IT. For a simultaneous depth-2 pair the two legs
sum to the pair cost: if UP own is x then DOWN own is `pair - x`. Both must
sit in [floor, ceiling], so

    x in [max(floor, pair - ceiling), min(ceiling, pair - floor)]

With pair 0.95 and ceiling 0.60 the lower end is `0.95 - 0.60 = 0.35`,
which is ABOVE the deployed floor of 0.30. **The floor never binds. The
0.60 ceiling alone sets BOTH ends of the eligible range.**

So "widening the band" for this probe means RAISING THE CEILING -- and the
ceiling carries the strongest evidence in the module: on L5's own
2,216-window history completion falls 85.2% in 0.5-0.6 to
76.2%/60.7%/46.7% across 0.6-0.9, and EV per contract turns negative above
0.60 (-$0.0633, -$0.2220, -$0.3971 over 849 windows).

Raising it is exactly what the evidence forbids. The gate IS the economics.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS

TICK = D("0.01")
DEPTH = 2


@pytest.fixture
def deployed_band(monkeypatch):
    """The DEPLOYED values, which differ from the code defaults: the floor
    is 0.30 in production and 0.20 in code. Read from the live
    L5_EFFECTIVE_RUNTIME_CONFIG, not assumed."""
    monkeypatch.setenv("L5_TS_MIN_FIRST_LEG_PRICE", "0.30")
    monkeypatch.setenv("L5_TS_MAX_FIRST_LEG_PRICE", "0.60")
    assert TS.min_first_leg_own_price() == D("0.30")
    assert TS.max_first_leg_own_price() == D("0.60")
    yield D("0.30"), D("0.60")


def literal_text(fn):
    """Every string constant in a function, joined.

    Asserting on `inspect.getsource` fails here: the notes are written as
    ADJACENT string literals across source lines, so a phrase that reads
    as one sentence is several constants with quotes and indentation
    between them. Reading the constants is what actually tests the prose.
    """
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return " ".join(out)


def admits(bid, ask, depth=DEPTH):
    up = D(str(bid)) - depth * TICK
    dn = (D(1) - D(str(ask))) - depth * TICK
    if up <= 0 or dn <= 0:
        return False
    return (TS.first_leg_entry_refusal(side="UP", own_px=up, held_up=0,
                                       held_down=0) is None
            and TS.first_leg_entry_refusal(side="DOWN", own_px=dn, held_up=0,
                                           held_down=0) is None)


# ===================================== the band is strategy, not safety
def test_the_band_is_env_tunable_and_scoped_to_opening_only(deployed_band):
    import inspect
    txt = literal_text(TS.first_leg_entry_refusal)
    assert "This refuses OPENING" in txt
    assert "completion and inventory management are never blocked" in txt
    # and it is a SEPARATE gate: build_orders does not enforce it
    assert "first_leg_entry_refusal" not in inspect.getsource(
        TS.build_orders)


def test_both_bounds_can_be_disabled_only_together(monkeypatch):
    """`L5_TS_MIN_FIRST_LEG_PRICE=0` used to switch off a ceiling the owner
    never mentioned. Each bound is applied only if itself enabled."""
    monkeypatch.setenv("L5_TS_MIN_FIRST_LEG_PRICE", "0")
    monkeypatch.setenv("L5_TS_MAX_FIRST_LEG_PRICE", "0.60")
    # the ceiling still bites with the floor disabled
    assert TS.first_leg_entry_refusal(side="UP", own_px=D("0.90"),
                                      held_up=0, held_down=0) is not None
    monkeypatch.setenv("L5_TS_MAX_FIRST_LEG_PRICE", "0")
    assert TS.first_leg_entry_refusal(side="UP", own_px=D("0.90"),
                                      held_up=0, held_down=0) is None


# ======================================== the floor never binds a pair
def test_the_deployed_floor_does_not_bind_a_simultaneous_depth_2_pair(
        deployed_band):
    """THE RESULT THAT ANSWERS THE QUESTION. pair - ceiling = 0.35 > 0.30,
    so the floor is slack and the ceiling sets both ends."""
    floor, ceiling = deployed_band
    pair = D(1) - TICK - 2 * DEPTH * TICK          # a one-tick book
    assert pair == D("0.95")
    lo = max(floor, pair - ceiling)
    hi = min(ceiling, pair - floor)
    assert pair - ceiling == D("0.35")
    assert lo == D("0.35") and lo > floor, (lo, floor)
    assert hi == ceiling


def test_lowering_the_floor_to_the_code_default_changes_nothing(
        monkeypatch):
    """So relaxing the deployed floor back to the code's own 0.20 would
    not admit one extra book. There is nothing to gain there."""
    ranges = {}
    for f in ("0.30", "0.20"):
        monkeypatch.setenv("L5_TS_MIN_FIRST_LEG_PRICE", f)
        monkeypatch.setenv("L5_TS_MAX_FIRST_LEG_PRICE", "0.60")
        good = [D(c) / 100 for c in range(1, 100)
                if D(c) / 100 + TICK < 1
                and admits(D(c) / 100, D(c) / 100 + TICK)]
        ranges[f] = (min(good), max(good), len(good))
    assert ranges["0.30"] == ranges["0.20"], ranges
    assert ranges["0.30"] == (D("0.37"), D("0.62"), 26)


# =============================== the eligible range, including wide spreads
@pytest.mark.parametrize("spread,lo,hi,n", [
    ("0.01", "0.37", "0.62", 26),
    ("0.02", "0.36", "0.62", 27),
    ("0.03", "0.35", "0.62", 28),
    ("0.05", "0.33", "0.62", 30),
    ("0.06", "0.32", "0.62", 31),
])
def test_a_wider_spread_widens_the_eligible_bid_range(spread, lo, hi, n,
                                                       deployed_band):
    """A wider spread makes the pair CHEAPER, which moves the lower end
    down. So the constraint is loosest exactly where the economics are
    best -- the gate and the edge point the same way."""
    s = D(spread)
    good = [D(c) / 100 for c in range(1, 100)
            if D(c) / 100 + s < 1 and admits(D(c) / 100, D(c) / 100 + s)]
    assert (min(good), max(good), len(good)) == (D(lo), D(hi), n)
    pair = D(1) - s - 2 * DEPTH * TICK
    assert pair <= TS.max_pair_cost()


def test_the_observed_live_book_was_legitimately_out_of_band(deployed_band):
    """bid 0.1800 / ask 0.1900, read from L5_CONTROLLER_OPEN_TRIGGER at
    01:23:10Z. BOTH legs are refused, and for opposite reasons -- which is
    the band doing its job, not a defect."""
    assert admits("0.18", "0.19") is False
    up = TS.first_leg_entry_refusal(side="UP", own_px=D("0.16"),
                                    held_up=0, held_down=0)
    dn = TS.first_leg_entry_refusal(side="DOWN", own_px=D("0.79"),
                                    held_up=0, held_down=0)
    assert up["reason"] == "FIRST_LEG_BELOW_MIN_PRICE"
    assert dn["reason"] == "FIRST_LEG_ABOVE_MAX_PRICE"


def test_an_even_money_book_is_admitted(deployed_band):
    """Which is where a fresh BTC15m window starts."""
    assert admits("0.50", "0.51") is True
    assert admits("0.40", "0.41") is True
    assert admits("0.62", "0.63") is True


# ============================ the measured in-band frequency, recorded
def test_the_measured_in_band_frequency_is_recorded():
    """Measured over 18 recorded windows / 1,101 frames with a readable
    touch, depth 2 at the deployed band. Pinned as the basis for "one
    window may legitimately produce no fills".

    The collapse after 420 s is the point: the band is satisfiable early,
    when a fresh up/down market is near even money, and not late once it
    has drifted to an extreme. 480 s is the entry cutoff, so the engine
    stops opening at almost exactly the moment the band stops admitting.
    """
    by_bucket = {0: 29.3, 60: 36.6, 120: 34.6, 180: 39.5, 240: 37.4,
                 300: 45.0, 360: 24.4, 420: 0.0, 480: 11.4, 540: 0.0}
    early = [v for k, v in by_bucket.items() if k < 360]
    assert min(early) >= 29.0, early
    assert by_bucket[420] == 0.0
    assert sum(1 for k, v in by_bucket.items() if k >= 420 and v > 0) <= 1
    # whole entry window
    assert 30.0 <= 32.3 <= 35.0
    # and every in-band pair observed was inside the ceiling
    assert D("0.9500") <= TS.max_pair_cost()
    assert D("0.9300") <= D("0.9500")


def test_widening_means_raising_the_ceiling_which_the_evidence_forbids():
    """The 849-window evidence, carried in the refusal's own text so it
    cannot be separated from the number it justifies."""
    txt = literal_text(TS.first_leg_entry_refusal)
    assert "2,216-window history" in txt
    assert "85.2%" in txt and "46.7%" in txt
    assert "849 windows" in txt
    assert "-$0.0633" in txt and "-$0.3971" in txt
