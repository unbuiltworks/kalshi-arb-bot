"""A completion at the redemption line earns nothing. Make that optional.

THE MEASURED DEFECT. Edge per contract on a matched pair is EXACTLY
(1.00 - pair). The 2026-11-02 four-window campaign completed three pairs:

    1330z  pair 1.00  qty 2.20  ->  +0.0000
    1345z  pair 0.96  qty 1.93  ->  +0.0772
    1400z  pair 0.99  qty 2.23  ->  +0.0223
                                    -------
                     total matched  +0.0995

Two of the three were economically free: they spent cash, consumed the
single order slot and used up the window's cycle to lock $0.00 and $0.02.

AND DECLINING ONE COSTS NO EXPECTANCY. Priced by the complement's own
executable cost -- the only calibrated settlement probability we have --
EV(complete at 1.00) and EV(hold the strand) were each exactly +0.0000 at
13:32:34. Holding would in fact have returned +1.4080 because UP won 1330z,
but that was VARIANCE, not edge, and this module does not bank on it.

SO THE CEILING IS THE LEVER, and it is the single testable change:
complete only where the pair creates real edge, otherwise carry the
residual and keep the slot free for a cycle that can.

THE 1.00 DEFAULT WAS THE DEFECT, and 2026-11-07 0100z is where it cost real
money: the requote cancelled a resting UP quote at a pair of 0.9500 to chase
a pair of EXACTLY 1.0000, venue-confirmed twice, and the window settled with
no pair and -$0.4900. Inert was not harmless. The default is now the
OPENING ceiling `max_pair_cost()`, which the requote path had never been
bound by; the variable still overrides it, up to the 1.00 clamp. See
`tests/test_l5_requote_pair_ceiling_0100z.py` for the recorded sequence.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS


def test_the_default_is_the_opening_ceiling_not_the_redemption_line(
        monkeypatch):
    """An opening priced to a 0.96 pair that a requote may reprice to 1.00
    is not a ceiling. Absent an explicit owner choice the two now agree."""
    monkeypatch.delenv(TS.COMPLETION_MAX_PAIR_ENV, raising=False)
    monkeypatch.delenv(TS.MAX_PAIR_COST_ENV, raising=False)
    assert TS.completion_max_pair() == TS.max_pair_cost() == D("0.96")


@pytest.mark.parametrize("opening", ["0.90", "0.96", "0.99"])
def test_the_default_tracks_the_configured_opening_ceiling(monkeypatch,
                                                           opening):
    monkeypatch.delenv(TS.COMPLETION_MAX_PAIR_ENV, raising=False)
    monkeypatch.setenv(TS.MAX_PAIR_COST_ENV, opening)
    assert TS.completion_max_pair() == D(opening)


@pytest.mark.parametrize("raw,want", [
    ("0.97", D("0.97")), ("0.93", D("0.93")), ("1.00", D("1.00")),
])
def test_a_configured_ceiling_is_honoured(monkeypatch, raw, want):
    monkeypatch.setenv(TS.COMPLETION_MAX_PAIR_ENV, raw)
    assert TS.completion_max_pair() == want


@pytest.mark.parametrize("raw", ["1.01", "2", "0", "-0.5", "abc", "", "NaN"])
def test_it_can_only_ever_tighten_never_loosen(monkeypatch, raw):
    """Above redemption would permit a pair that settles at a loss, which
    `redeem_cap` exists to forbid. Unreadable falls back to the OPENING
    ceiling -- the tighter of the two candidates -- never to a guess and
    never to the looser bound."""
    monkeypatch.setenv(TS.COMPLETION_MAX_PAIR_ENV, raw)
    monkeypatch.delenv(TS.MAX_PAIR_COST_ENV, raising=False)
    assert TS.completion_max_pair() == D("0.96")
    assert TS.completion_max_pair() <= D("1.00")


def test_the_campaign_pairs_show_where_the_edge_actually_was():
    """Pins the arithmetic the ceiling is chosen against."""
    pairs = [(D("1.00"), D("2.20")), (D("0.96"), D("1.93")),
             (D("0.99"), D("2.23"))]
    edges = [q * (D(1) - p) for p, q in pairs]
    assert edges[0] == D("0.0000")
    assert sum(edges) == D("0.0995")
    # a 0.97 ceiling would have declined the 1.00 pair and kept the other
    # two, kept 2.20 x 0.36 = $0.792 of cash free, and freed one cycle
    kept = [e for (p, q), e in zip(pairs, edges) if p <= D("0.97")]
    assert len(kept) == 1 and sum(kept) == D("0.0772")


def test_declining_a_zero_edge_completion_is_ev_neutral():
    """The reason this is safe to try: at market prices both sides are zero.

    1330z, 13:32:34. Held UP 2.2 at own 0.36; completing cost DOWN at own
    0.64, so P(DOWN wins) = 0.64.
    """
    q, up_px, dn_px = D("2.2"), D("0.36"), D("0.64")
    cost = q * up_px
    ev_hold = (D(1) - dn_px) * (q - cost) + dn_px * (-cost)
    ev_complete = q * (D(1) - (up_px + dn_px))
    assert ev_hold == D(0)
    assert ev_complete == D(0)
    assert ev_hold == ev_complete


def test_the_module_still_never_permits_a_pair_above_redemption(monkeypatch):
    """The existing hard bound is untouched by this knob."""
    monkeypatch.setenv(TS.COMPLETION_MAX_PAIR_ENV, "0.90")
    assert TS.completion_max_pair() == D("0.90") <= D("1.00")
    monkeypatch.delenv(TS.COMPLETION_MAX_PAIR_ENV, raising=False)
    monkeypatch.setenv(TS.MAX_PAIR_COST_ENV, "0.99")
    assert TS.completion_max_pair() == D("0.99") <= D("1.00")


def test_this_knob_moves_no_risk_limit():
    """It READS the opening ceiling for its default; it never writes one.

    `max_pair_cost()` is in the source deliberately -- that is the whole
    fix -- so the assertion is that nothing here MUTATES a bound: no
    assignment to any limit, and the opening ceiling is unchanged across a
    call.
    """
    import inspect
    src = inspect.getsource(TS.completion_max_pair)
    for banned in ("max_total_exposure", "max_live_orders", "daily_loss",
                   "residual", "cash", "setenv", "os.environ["):
        assert banned not in src, banned
    assert "max_pair_cost()" in src
    before = TS.max_pair_cost()
    TS.completion_max_pair()
    assert TS.max_pair_cost() == before
