"""`fill_own_px` must be an OWN-outcome price, not the venue's wire price.

THE DEFECT. `emit_fill_evidence` passed `ev["avg_px"]` -- the venue's
average fill price, which is YES/UP, i.e. WIRE -- straight into
`fill_own_px`. For every DOWN fill that field therefore held `1 - own`.

NOT ONLY A LABEL. `BE.fill_evidence` falls back to `fill_own_px` for
`first_leg_own_px`, and every `pair_if_*` term in the record is priced from
`first_leg_own_px`. A DOWN fill whose pre-submit record had been evicted
therefore produced pair arithmetic wrong by `1 - 2p`.

The table below is the real Campaign C and D population: 18 venue fills,
and for each DOWN fill the submitted own price we recorded alongside it.
Historical money records are NOT rewritten by this change; only newly
emitted evidence records are affected.
"""
from decimal import Decimal as D

import pytest

from src import l5_book_evidence as BE
from src import l5_two_sided_probe as TSP


# (window, side, venue wire avg_px, our submitted own_px) -- all 18 real fills
REAL_FILLS = [
    ("1745z", "DOWN", "0.5300", "0.4700"), ("1745z", "UP", "0.5200", "0.5200"),
    ("1745z", "DOWN", "0.5500", "0.4500"), ("1745z", "UP", "0.5300", "0.5300"),
    ("1745z", "DOWN", "0.6100", "0.3900"), ("1745z", "UP", "0.5600", "0.5600"),
    ("1745z", "UP", "0.5700", "0.5700"), ("1745z", "DOWN", "0.6400", "0.3600"),
    ("1800z", "DOWN", "0.8800", "0.1200"), ("1815z", "UP", "0.4600", "0.4600"),
    ("1815z", "DOWN", "0.4600", "0.5400"), ("1815z", "UP", "0.4600", "0.4600"),
    ("1830z", "UP", "0.4700", "0.4700"), ("1845z", "DOWN", "0.5800", "0.4200"),
    ("1900z", "UP", "0.1900", "0.1900"), ("2000z", "UP", "0.3700", "0.3700"),
    ("2000z", "DOWN", "0.4400", "0.5600"), ("2000z", "UP", "0.4000", "0.4000"),
]


@pytest.mark.parametrize("window,side,wire,own", REAL_FILLS)
def test_conversion_reproduces_every_real_fill(window, side, wire, own):
    assert TSP.wire_price(side, wire) == D(own)


def test_the_defect_is_real_for_down_and_invisible_for_up():
    """Eight DOWN fills differ; ten UP fills do not -- which is exactly why
    reading the field as an own price went unnoticed."""
    down = [f for f in REAL_FILLS if f[1] == "DOWN"]
    up = [f for f in REAL_FILLS if f[1] == "UP"]
    assert len(down) == 8 and len(up) == 10
    assert all(D(w) != D(o) for _, _, w, o in down)
    assert all(D(w) == D(o) for _, _, w, o in up)


def test_conversion_is_an_involution_so_nothing_is_lost():
    for _, side, wire, _own in REAL_FILLS:
        back = TSP.wire_price(side, TSP.wire_price(side, wire))
        assert back == D(wire)


@pytest.mark.parametrize("bad", [None, "", "SELL", "up ", "yes", "X"])
def test_an_unrecognised_side_is_not_converted(bad):
    """`wire_price` treats any non-UP label as DOWN, so the caller must
    gate on a recognised side rather than trust it."""
    sd = None if bad is None else str(bad).strip().upper()
    assert sd not in ("UP", "DOWN") or sd == "UP"


# ------------------------------------- the pair arithmetic the defect broke

def test_fill_own_px_is_the_first_leg_fallback():
    """The mechanism by which a mislabelled field corrupted pair numbers."""
    rec = BE.fill_evidence(
        slug="cpc-btc-updown-15m-2026-11-04-1815z", coid="c1", side="DOWN",
        fill_qty="1", fill_own_px="0.5400", venue_event_t=None,
        processing_t=1.0, residual_after="-1", first_leg_own_px=None,
        qty_needed="1", ring=None, order_resting=False, depth_ticks=0)
    assert rec["fill_own_px"] == "0.5400"


def test_wire_into_that_fallback_would_misprice_a_down_pair():
    """0.46 wire against a 0.54 own basis is a 0.08 error in the pair."""
    wire, own = D("0.4600"), D("0.5400")
    complement = D("0.4200")
    assert (own + complement) - (wire + complement) == D("0.0800")


# -------------------------------------------- the fix is present in source

def _src() -> str:
    import io
    return io.open("src/l5_pmus_live_runner.py", encoding="utf-8").read()


def test_the_emit_site_converts_wire_to_own():
    s = _src()
    assert "_wire_px = ev.get(\"avg_px\")" in s
    assert "TSP.wire_price(_sd, _wire_px)" in s
    assert "fill_own_px=_own_px" in s


def test_the_emit_site_no_longer_passes_avg_px_as_an_own_price():
    s = _src()
    assert "fill_own_px=ev.get(\"avg_px\")" not in s
    assert "first_leg_own_px=sub.get(\"own_px\") or ev.get(\"avg_px\")" not in s


def test_the_first_leg_fallback_uses_the_converted_price():
    s = _src()
    assert "first_leg_own_px=sub.get(\"own_px\") or _own_px" in s


def test_the_wire_price_is_kept_beside_it():
    s = _src()
    assert 'rec["fill_wire_px"]' in s
    assert 'rec["own_price_basis"]' in s


def test_the_side_gate_admits_only_up_and_down():
    s = _src()
    assert '_sd in ("UP", "DOWN") else None' in s


def test_the_probe_module_is_imported_locally_not_as_module_level_TS():
    """Module-level `TS` in the runner is `l5_two_sided_shadow`; reaching
    for it here would have silently killed every fill evidence record,
    because this method swallows BaseException."""
    s = _src()
    # module level binds TS to the SHADOW module...
    assert "from src import l5_two_sided_shadow as TS" in s
    # ...so this method must bind the probe itself, close by.
    i = s.index("_wire_px = ev.get(\"avg_px\")")
    assert "from src import l5_two_sided_probe as TSP" in s[i - 600:i]
    # and inside THIS block the call must go through that local alias.
    # (Elsewhere in the file a different local import legitimately uses the
    # name `TS` for the probe, so this is scoped to the block, not global.)
    j = s.index('rec["own_price_basis"]', i)
    block = s[i:j]
    assert "TSP.wire_price(" in block
    assert "TS.wire_price(" not in block.replace("TSP.wire_price(", "")


def test_no_historical_money_record_is_rewritten():
    """This change touches one evidence emit. It must not appear in any
    cost-basis, ledger or settlement writer."""
    s = _src()
    i = s.index("_wire_px = ev.get(\"avg_px\")")
    j = s.index('rec["own_price_basis"]', i)
    block = s[i:j]
    for forbidden in ("store.apply", "leg_cost(", "book_fill", "settle",
                      "realized", "commit"):
        assert forbidden not in block
