"""An opening leg may never exceed the approved 3-contract residual ceiling.

THE GAP THIS CLOSES, found live. The residual cap in `l5_live_execution`
refuses only when the prospective book is NOT flat (`exempt_open`), because it
was written to stop accumulation onto existing inventory after the 1130z burst
reached 3.24 against a cap of 1.80. An OPENING leg from a flat book was never
bounded by it; the clip came from cash, as qty = cash / (1 + up_px). At the $5
Stage 1 envelope that produced a 4.09-contract fill at 2026-10-24T13:31:45Z --
a 4.09-contract residual if it stranded, above the approved 3.

AND LOWERING CASH CANNOT FIX IT, which is why this cap is a quantity and not a
dollar figure: qty grows as up_px falls, so at $3.70 a 0.02 leg still sizes
3.62 and only cash <= 3 x 1.01 = $3.03 would bound it across eligible prices.

Nothing here touches a venue, a credential or a limit.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS

SLUG = "cpc-btc-updown-15m-2026-10-24-1400z"

#: books spanning the eligible price band, as the live tapes show them
BOOKS = [("0.1400", "0.1500"), ("0.2100", "0.2200"), ("0.3100", "0.3200"),
         ("0.5000", "0.5100"), ("0.6200", "0.6300"), ("0.7400", "0.7500")]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("L5_TS_MARKET", SLUG)
    monkeypatch.setenv("L5_TS_EXPERIMENT_ID", "opening-cap-test")
    monkeypatch.delenv("L5_TS_OPENING_LEG_MAX_CONTRACTS", raising=False)
    monkeypatch.delenv("L5_TS_ROUND_ALLOCATIONS", raising=False)


def plan(bid, ask, cash="3.70"):
    return TS.build_orders(
        market=SLUG, bid=bid, ask=ask, bid_depth="5000", ask_depth="5000",
        tick="0.01", min_qty="0.01", depth_ticks=2, cash_envelope=D(cash),
        max_worst_case_loss=D("20"), budget_headroom=D("20.74"),
        book_age_s=0.05)


class TestNoOpeningLegCanExceedThree:

    @pytest.mark.parametrize("bid,ask", BOOKS)
    @pytest.mark.parametrize("cash", ["3.70", "5", "20", "100"])
    def test_across_every_eligible_price_and_every_envelope(self, bid, ask,
                                                            cash):
        """The cap is price-independent AND envelope-independent, which is the
        whole point: no cash figure bounds a contract count."""
        out = plan(bid, ask, cash=cash)
        assert out["ok"] is True, out.get("reason")
        assert D(out["qty_per_leg"]) <= D("3")

    @pytest.mark.parametrize("bid,ask", BOOKS)
    def test_the_cap_is_named_when_it_binds(self, bid, ask):
        out = plan(bid, ask, cash="100")
        assert out["sized_by"] == "OPENING_LEG_CONTRACT_CAP"
        assert out["opening_leg_cap_binds"] is True
        assert D(out["opening_leg_max_contracts"]) == D("3")

    def test_cash_still_binds_when_it_is_the_tighter_bound(self):
        out = plan("0.7400", "0.7500", cash="3.70")
        assert out["sized_by"] == "CASH_ENVELOPE"
        assert out["opening_leg_cap_binds"] is False
        assert D(out["qty_per_leg"]) < D("3")

    def test_the_4_09_contract_fill_could_not_happen_again(self):
        """The live book at 13:31 was 0.21/0.22 wire with up_px 0.22 at the $5
        envelope: 5 / 1.22 = 4.09. Same inputs, capped."""
        out = plan("0.2100", "0.2200", cash="5")
        assert D(out["qty_per_leg"]) <= D("3")


class TestTheCapCannotBeRaised:

    @pytest.mark.parametrize("raw", ["4", "10", "999", "3.01", "inf",
                                     "nan", "", "three", "}{", "0", "-1"])
    def test_malformed_or_larger_reads_as_three(self, monkeypatch, raw):
        monkeypatch.setenv("L5_TS_OPENING_LEG_MAX_CONTRACTS", raw)
        assert TS.opening_leg_max_contracts() == D("3")

    @pytest.mark.parametrize("raw,want", [("2", "2"), ("1", "1"),
                                          ("0.5", "0.5")])
    def test_it_is_configurable_downward_only(self, monkeypatch, raw, want):
        monkeypatch.setenv("L5_TS_OPENING_LEG_MAX_CONTRACTS", raw)
        assert TS.opening_leg_max_contracts() == D(want)

    def test_a_lower_cap_actually_sizes_the_plan(self, monkeypatch):
        monkeypatch.setenv("L5_TS_OPENING_LEG_MAX_CONTRACTS", "1")
        out = plan("0.2100", "0.2200", cash="5")
        assert D(out["qty_per_leg"]) <= D("1")


class TestItDoesNotBlockRiskReducingCompletion:
    """`build_orders` plans an OPENING pair. Completing inventory already held
    is sized against that inventory on a different path, so capping the plan
    cannot strand an open position by refusing its complement."""

    def test_build_orders_takes_no_inventory_argument_at_all(self):
        """Checked on the SIGNATURE, not the prose: an earlier version of this
        test grepped the body and tripped on the word "inventory" appearing in
        a label string."""
        import inspect
        sig = inspect.signature(TS.build_orders)
        for p in sig.parameters:
            assert "held" not in p and "inventor" not in p and \
                "position" not in p, p
        assert "opening_leg_max_contracts()" in inspect.getsource(
            TS.build_orders)

    def test_the_cap_constant_documents_that_limitation(self):
        import inspect
        src = inspect.getsource(TS)
        i = src.index("OPENING_LEG_MAX_CONTRACTS = ")
        head = src[max(0, i - 2000):i]
        assert "cannot block a" in head and "complement" in head


class TestTheOtherBoundsAreUnchanged:

    def test_the_pair_cap_still_holds(self):
        out = plan("0.3100", "0.3200", cash="100")
        assert D(out["pair_cost"]) <= TS.max_pair_cost()

    def test_maker_post_only_still_holds(self):
        out = plan("0.3100", "0.3200", cash="100")
        for leg in out["legs"]:
            assert leg["mode"] == "MAKER" and leg["post_only"] is True

    def test_the_obligation_basis_is_still_a_dollar_a_share(self):
        out = plan("0.3100", "0.3200", cash="5")
        q = D(out["qty_per_leg"])
        up = D([l for l in out["legs"] if l["side"] == "UP"][0]["price"])
        assert D(out["cash_gate_obligation_usd"]) == q * (D(1) + up)

    def test_the_round_divisor_is_still_one_by_default(self):
        out = plan("0.3100", "0.3200")
        assert out["round_allocations"] == 1
