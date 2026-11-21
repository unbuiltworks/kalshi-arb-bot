"""The preflight's worst case must track the limit actually in force.

THE DISCREPANCY THE OWNER FLAGGED. `L5_LIVE_PREFLIGHT` reported
`max_committed_usd: 5` and `worst_case_total_usd: 5.1738` against a stated
$5 ceiling. Both numbers are correct and they measure different things:

  * the cash gate counts PRINCIPAL ONLY. `committed` is own_price * qty
    (l5_live_execution: `committed[market] += own * qty`),
    `pending_obligation` is qty * price, or qty for a resting short, and the
    proposed order is q * px. No term carries a fee.
  * the taker fee is therefore charged ON TOP of the ceiling.

So $5 bounds principal and the all-in worst case is larger. Nothing was
silently raised; the label was incomplete.

AND THE $5.1738 WAS ITSELF OPTIMISTIC. It assumed the fee peaks at p = 0.50,
which is true PER CONTRACT but not under a fixed principal: contracts = cap/p,
so fee = Theta*cap*(1-p), monotone DECREASING in p and maximised at the
CHEAPEST admissible price. On $5 of principal that is $0.3301 at the 0.05 band
floor, for an all-in worst case of $5.3301 -- the reported figure understated
it by 90%.

To hold the ALL-IN total at X the principal ceiling must be
X / (1 + Theta*(1 - floor_p)), which for X = $5 is $4.69.

Separately, the cap was HARDCODED, so a tightened ceiling would not have
shown up in the evidence a campaign is activated on. These tests pin all of it.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_fees as fees
from src.l5_pmus_transport import PmusTransport


class _Bare(PmusTransport):
    """Only `worst_case_from_venue` is under test; it touches no socket."""

    def __init__(self):            # noqa: D107 - deliberately no super()
        pass


def _wc(monkeypatch, env=None):
    if env is None:
        monkeypatch.delenv("L5_MAX_CASH_DEPLOYED_USD", raising=False)
    else:
        monkeypatch.setenv("L5_MAX_CASH_DEPLOYED_USD", env)
    return _Bare().worst_case_from_venue({"tickSize": "0.01"})


def test_the_corrected_default_worst_case(monkeypatch):
    out = _wc(monkeypatch)
    assert out["max_committed_usd"] == "5"
    assert out["max_committed_source"] == "default"
    # the cheapest admissible price, not 0.50
    assert out["fee_maximising_price"] == "0.05"
    assert out["max_taker_fee_usd"] == "0.3301"
    assert out["worst_case_total_usd"] == "5.3301"


def test_the_old_p_equals_half_figure_understated_the_fee(monkeypatch):
    """Pins the magnitude of the correction so it cannot silently regress."""
    out = _wc(monkeypatch)
    old_fee = fees.taker_fee(D("0.5"), D("5") / D("0.5"))
    assert old_fee == D("0.1738")
    assert D(out["max_taker_fee_usd"]) > old_fee
    assert D(out["max_taker_fee_usd"]) / old_fee > D("1.8")


def test_the_fee_is_declared_to_be_outside_the_cash_ceiling(monkeypatch):
    """The label, not the limit, was the problem."""
    out = _wc(monkeypatch)
    assert out["fee_is_outside_the_cash_ceiling"] is True
    assert "charged" in out["basis_fees"]


def test_a_tightened_ceiling_now_shows_up_in_the_evidence(monkeypatch):
    """Previously hardcoded: the preflight would have kept reporting $5."""
    out = _wc(monkeypatch, "4.69")
    assert out["max_committed_usd"] == "4.69"
    assert out["max_committed_source"] == "L5_MAX_CASH_DEPLOYED_USD"
    assert D(out["worst_case_total_usd"]) <= D("5")


@pytest.mark.parametrize("bad", ["", "abc", "0", "-1"])
def test_an_unusable_setting_falls_back_to_the_figure_it_always_reported(
        monkeypatch, bad):
    out = _wc(monkeypatch, bad)
    assert out["max_committed_usd"] == "5"
    assert out["worst_case_total_usd"] == "5.3301"


def test_the_all_in_principal_for_a_five_dollar_ceiling_is_4_69(monkeypatch):
    """X / (1 + Theta*(1-floor_p)) with X=5, Theta=0.0695, floor_p=0.05."""
    principal = (D(5) / (D(1) + D("0.0695") * (D(1) - D("0.05")))
                 ).quantize(D("0.01"))
    assert principal == D("4.69")
    out = _wc(monkeypatch, str(principal))
    assert D(out["worst_case_total_usd"]) <= D("5")
    # and the figure derived from the OLD, wrong maximum does not hold
    out_wrong = _wc(monkeypatch, "4.83")
    assert D(out_wrong["worst_case_total_usd"]) > D("5")


def test_the_fee_bound_is_taken_at_the_cheapest_price_under_fixed_principal(
        monkeypatch):
    """fee = Theta*cap*(1-p) is monotone decreasing in p, so no price above
    the floor can beat it."""
    out = _wc(monkeypatch)
    cap = D(out["max_committed_usd"])
    at_floor = D(out["max_taker_fee_usd"])
    for p in ("0.06", "0.1", "0.25", "0.5", "0.75", "0.95"):
        assert fees.taker_fee(D(p), cap / D(p)) <= at_floor, p


def test_the_fee_floor_matches_the_probe_band():
    """`l5_pmus_transport.FEE_FLOOR_PRICE` restates the probe's MIN_PRICE
    rather than importing it, because the transport ships in the GET-only
    observer image and `l5_two_sided_probe` does not
    (`test_deploy_fallthrough_guard`). Tests may import both, so this is
    where the two are kept from drifting."""
    from src import l5_pmus_transport as T
    from src import l5_two_sided_probe as TSP
    assert D(str(T.FEE_FLOOR_PRICE)) == D(str(TSP.MIN_PRICE))


def test_the_transport_does_not_import_the_probe():
    """The import that broke the observer image closure must not come back."""
    import ast
    src = ast.parse(open("src/l5_pmus_transport.py").read())
    names = set()
    for n in ast.walk(src):
        if isinstance(n, ast.ImportFrom) and n.module:
            names.update("%s.%s" % (n.module, a.name) for a in n.names)
        elif isinstance(n, ast.Import):
            names.update(a.name for a in n.names)
    assert not any("l5_two_sided_probe" in x for x in names), sorted(
        x for x in names if "two_sided" in x)


# ===== POINT 3: $4.69 holds an all-in $5 ceiling at EVERY permitted price

PRINCIPAL = D("4.69")
ALL_IN = D("5.00")


def test_4_69_holds_all_in_under_5_at_every_permitted_tick_price():
    """Exhaustive over the venue band on 0.01 ticks, at FULL principal --
    which is the binding case, since a smaller size pays a smaller fee."""
    from src import l5_two_sided_probe as TSP
    worst = D(0)
    p = D(str(TSP.MIN_PRICE))
    while p <= D(str(TSP.MAX_PRICE)):
        contracts = PRINCIPAL / p
        total = PRINCIPAL + fees.taker_fee(p, contracts)
        assert total <= ALL_IN, (str(p), str(total))
        worst = max(worst, total)
        p += D("0.01")
    # and the maximum really is at the floor, as the model claims
    floor_total = PRINCIPAL + fees.taker_fee(
        D(str(TSP.MIN_PRICE)), PRINCIPAL / D(str(TSP.MIN_PRICE)))
    assert worst == floor_total
    assert worst == D("4.9997")


def test_any_smaller_size_is_strictly_cheaper():
    """Sizes below the cap cannot breach a ceiling the cap itself respects."""
    from src import l5_two_sided_probe as TSP
    floor = D(str(TSP.MIN_PRICE))
    full = fees.taker_fee(floor, PRINCIPAL / floor)
    for frac in ("0.1", "0.25", "0.5", "0.75", "0.9"):
        part = PRINCIPAL * D(frac)
        assert part + fees.taker_fee(floor, part / floor) <= ALL_IN
        assert fees.taker_fee(floor, part / floor) <= full


def test_one_cent_more_principal_breaches_the_ceiling():
    """So 4.69 is the binding value, not a round number chosen loosely."""
    from src import l5_two_sided_probe as TSP
    floor = D(str(TSP.MIN_PRICE))
    over = D("4.70")
    assert over + fees.taker_fee(floor, over / floor) > ALL_IN


def test_the_preflight_reports_that_same_ceiling(monkeypatch):
    """The evidence a campaign is activated on must show it."""
    out = _wc(monkeypatch, str(PRINCIPAL))
    assert out["max_committed_usd"] == "4.69"
    assert out["max_committed_source"] == "L5_MAX_CASH_DEPLOYED_USD"
    assert D(out["worst_case_total_usd"]) == D("4.9997")
    assert out["fee_is_outside_the_cash_ceiling"] is True


def test_per_order_cent_rounding_is_charged_to_the_loss_gates_not_the_cap():
    """HONEST LIMIT, MEASURED RATHER THAN ASSUMED. The fee is rounded per
    order, so splitting a principal across orders changes the total slightly
    -- here by $0.0001, and marginally CHEAPER, not super-additive as the
    fee model's own comment warns it could be in the per-FILL case.

    Either way that is REALIZED cost, bounded by the loss gates, not by the
    cash ceiling, which bounds concurrent PRINCIPAL. At max_live_orders=1
    the ceiling-relevant exposure is one order.
    """
    from src import l5_two_sided_probe as TSP
    floor = D(str(TSP.MIN_PRICE))
    one = fees.taker_fee(floor, PRINCIPAL / floor)
    split = sum(fees.taker_fee(floor, (PRINCIPAL / D(4)) / floor)
                for _ in range(4))
    assert abs(split - one) < D("0.05")          # small either way
    assert PRINCIPAL + one <= ALL_IN
    assert PRINCIPAL + split <= ALL_IN


def test_cumulative_loss_state_is_durable_and_atomic(tmp_path):
    """Point 3, second half: the cumulative gate reads PERSISTED state, so
    the ledger must survive a restart and must never be torn."""
    import json as _json
    from src import l5_live_execution as LX
    p = tmp_path / "sub" / "inventory.json"
    s1 = LX.InventoryStore(str(p))
    s1.state["retired_markets"] = {
        "cpc-btc-updown-15m-2026-10-25-1945z": {
            "t": "2026-10-25T20:00:00+00:00",
            "l5_owner_attribution_verified": True,
            "realized_usd": "-0.6864"}}
    s1.save()
    assert p.exists(), "ledger was not written"
    # a fresh process sees it -- this is what makes the gate durable
    s2 = LX.InventoryStore(str(p))
    assert "cpc-btc-updown-15m-2026-10-25-1945z" in s2.state["retired_markets"]
    # atomic: no .tmp left behind and the file parses
    assert not list(p.parent.glob("*.tmp"))
    assert _json.loads(p.read_text())["retired_markets"]


def test_an_unreadable_ledger_refuses_rather_than_reading_zero_loss():
    """THE FAILURE THAT WOULD MATTER. `_load` swallows a parse error and
    leaves defaults, so a corrupted inventory.json loses the baseline. That
    must not become permission: with no L5-scoped figure the gate REFUSES.
    """
    from src import l5_live_validation_gates as VG
    g = VG.ValidationGates(max_cash_deployed_usd=D("4.69"),
                           max_unmatched_contracts=D("5"),
                           max_l5_cumulative_loss_usd=D("20"),
                           max_l5_daily_loss_usd=D("5"))
    why = g.refuse(order_qty=D("1"), order_price=D("0.44"),
                   order_intent="ORDER_INTENT_BUY_LONG",
                   filled_legs=(), outstanding=(),
                   unmatched_after=D(0), cumulative_realized=D(0),
                   cumulative_unrealized_loss=D(0),
                   l5_scoped_realized=None)          # the corrupted case
    assert why == "L5_SCOPED_REALIZED_UNAVAILABLE", why


def test_a_corrupt_ledger_does_not_crash_the_store(tmp_path):
    from src import l5_live_execution as LX
    p = tmp_path / "inventory.json"
    p.write_text("{ truncated")
    s = LX.InventoryStore(str(p))               # must not raise
    assert s.state["positions"] == {}
    assert s.state["committed"] == {}
