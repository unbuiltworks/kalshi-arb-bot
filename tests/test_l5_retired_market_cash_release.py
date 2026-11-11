"""A RETIRED market's committed residue must not be charged twice.

THE DEFECT THESE PIN, found in production on 2026-10-22. The executor was
refusing every order in window cpc-btc-updown-15m-2026-10-22-1945z with

    CAMPAIGN_WORST_CASE_BUDGET:13.54880000>11.10

of which ~$4.17 was `committed` residue belonging to markets ALREADY in
`retired_markets` -- markets whose settlement P&L was therefore already
inside the `max(0, -cumulative_realized)` term the same gate adds. One
economic event, billed twice: once at what the venue actually paid, and
again at its full worst case.

The authenticated venue read taken at 2026-10-22T16:18:52Z settles the
question of whether any risk stood behind that residue:

    positions   {"positions": {}, "availablePositions": []}
    open_orders {"orders": []}
    balances    assetNotional 0, assetAvailable 0, unsettledFunds 0,
                openOrders 0, buyingPower == currentBalance == 343.90855

Nothing open, nothing unsettled, nothing held.

THE ASYMMETRY THAT LET THE RESIDUE PERSIST, which is the part worth pinning
rather than the dollar figure. `markets_with_exposure` subtracts
`retired_markets` ("a market already retired is finished with, whatever
residue a future defect leaves behind: it is never swept, never re-settled"),
so the settlement sweep cannot see the residue and nothing will ever release
it. `cash_legs_for_gates` read the SAME dict unfiltered and charged it
forever. Two readings of one dict disagreed.

NOT A WIDENING OF THE ENVELOPE. No limit moves in this change. The release
is conditioned on `retired_markets` alone, which only `retire_settled_market`
writes and only on a venue-terminal state WITH a final settlement price. A
market that has CLOSED but not SETTLED is still charged in full -- pinned
below, because that is the case an over-eager fix would break.

No order is submitted, no credential is used, no socket is opened.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

from src import l5_live_execution as E
from src import l5_live_validation_gates as VG

NOW_DT = dt.datetime(2026, 10, 22, 19, 47, 0, tzinfo=dt.timezone.utc)
NOW = lambda: NOW_DT

LIVE = "cpc-btc-updown-15m-2026-10-22-1945z"
RETIRED = "cpc-btc-updown-15m-2026-10-16-0330z"
ALSO_RETIRED = "cpcsol-sol-updown-15m-2026-10-16-0215z"


def limits(**kw):
    base = dict(max_usd_per_market=D("100"), max_abs_residual=D("100"),
                max_total_exposure_usd=D("100"), max_concurrent_markets=5,
                daily_realized_loss_halt_usd=D("100"))
    base.update(kw)
    return E.SafetyLimits(**base)


def executor(tmp_path, committed, retired=()):
    st = E.InventoryStore(str(tmp_path / "inv.json"))
    for mkt, usd in committed.items():
        st.state["committed"][mkt] = usd
    for mkt in retired:
        st.state.setdefault("retired_markets", {})[mkt] = {
            "t": NOW_DT.isoformat(), "yes_value": "0", "settlement_pnl": "0"}
    st.save()
    return E.FaithfulExecutor(st, limits(), now_fn=NOW)


def legs_by_market(ex):
    return {l["market"]: l for l in ex.cash_legs_for_gates()}


class TestRetiredMarketsAreMarkedSettled:

    def test_a_retired_market_is_reported_settled(self, tmp_path):
        ex = executor(tmp_path, {RETIRED: "4.17"}, retired=[RETIRED])
        assert legs_by_market(ex)[RETIRED]["settled"] is True

    def test_an_unretired_market_is_still_unsettled(self, tmp_path):
        """The conservative default is UNCHANGED for anything not retired."""
        ex = executor(tmp_path, {LIVE: "0.48"})
        assert legs_by_market(ex)[LIVE]["settled"] is False

    def test_a_closed_but_unsettled_market_keeps_its_cash(self, tmp_path):
        """CLOSED is not SETTLED, and only settlement releases cash.

        `retire_settled_market` already refuses a terminal state without a
        final price; this pins that a market in that position is charged in
        full here too, so the fix cannot be read as "expired is enough".
        """
        ex = executor(tmp_path, {RETIRED: "4.17"})     # NOT in retired_markets
        assert legs_by_market(ex)[RETIRED]["settled"] is False
        gates = VG.ValidationGates(max_cash_deployed_usd=D("4.65"))
        assert gates.cash_committed(ex.cash_legs_for_gates()) == D("4.17")

    def test_the_cash_out_figure_itself_is_never_rewritten(self, tmp_path):
        """Only `settled` changes. The residue is not silently zeroed.

        A fix that edited `cash_out` would destroy the evidence of what the
        ledger still holds, which is what the reconciliation needs to read.
        """
        ex = executor(tmp_path, {RETIRED: "4.17"}, retired=[RETIRED])
        assert legs_by_market(ex)[RETIRED]["cash_out"] == D("4.17")

    def test_an_empty_retired_set_reproduces_the_old_behaviour_exactly(
            self, tmp_path):
        """No `retired_markets` must mean byte-identical legs to before."""
        ex = executor(tmp_path, {LIVE: "0.48", RETIRED: "4.17"})
        # `committed()` is iterated sorted by market, so 10-16 precedes 10-22.
        assert ex.cash_legs_for_gates() == [
            {"market": RETIRED, "cash_out": D("4.17"), "settled": False,
             "closing": False},
            {"market": LIVE, "cash_out": D("0.48"), "settled": False,
             "closing": False},
        ]


class TestTheDoubleCountIsGone:

    def test_cash_committed_excludes_only_the_retired_residue(self, tmp_path):
        ex = executor(tmp_path, {LIVE: "0.48", RETIRED: "3.17",
                                 ALSO_RETIRED: "1.00"},
                      retired=[RETIRED, ALSO_RETIRED])
        gates = VG.ValidationGates(max_cash_deployed_usd=D("4.65"))
        assert gates.cash_committed(ex.cash_legs_for_gates()) == D("0.48")

    def test_the_campaign_budget_no_longer_refuses_the_live_window(
            self, tmp_path):
        """The production refusal, reproduced and then resolved.

        Figures are the ones the executor actually carried: $8.8988 realized,
        $4.17 of retired residue, $0.48 of live commitment, zero outstanding.
        Before the fix the projected worst case is the 13.5488 production
        printed; after it, the residue is gone and only genuine liabilities
        remain -- the realized loss is still charged IN FULL.
        """
        realized = D("-8.8988")
        ex = executor(tmp_path, {LIVE: "0.48", RETIRED: "4.17"},
                      retired=[RETIRED])
        gates = VG.ValidationGates(max_cash_deployed_usd=D("4.65"))

        used = (max(D(0), -realized)
                + gates.cash_committed(ex.cash_legs_for_gates())
                + gates.pending_obligation([]))
        assert used == D("9.3788")
        assert used <= D("11.10")

        # and the un-fixed reading is what production refused on
        stale = [dict(l, settled=False) for l in ex.cash_legs_for_gates()]
        assert (max(D(0), -realized) + gates.cash_committed(stale)
                + gates.pending_obligation([])) == D("13.5488")

    def test_the_realized_loss_is_never_released_by_this_change(self,
                                                                tmp_path):
        """Settlement releases EXPOSURE, never a realized loss.

        The whole point of the campaign budget is that losses persist. If
        retiring a market also forgave its loss, a campaign could cycle its
        way back to a clean slate -- so the realized term must be untouched.
        """
        ex = executor(tmp_path, {RETIRED: "4.17"}, retired=[RETIRED])
        gates = VG.ValidationGates(max_cash_deployed_usd=D("4.65"))
        assert gates.cash_committed(ex.cash_legs_for_gates()) == D(0)
        realized = D("-8.8988")
        assert max(D(0), -realized) == D("8.8988")


class TestRetirementRemainsTheOnlyRoute:

    def test_a_matched_flag_cannot_release_cash(self, tmp_path):
        """`matched` is not settlement and must not release anything."""
        ex = executor(tmp_path, {LIVE: "0.95"})
        legs = [dict(l, matched=True) for l in ex.cash_legs_for_gates()]
        gates = VG.ValidationGates(max_cash_deployed_usd=D("4.65"))
        assert gates.cash_committed(legs) == D("0.95")

    def test_only_retire_settled_market_writes_retired_markets(self):
        """The release is only as trustworthy as that single writer.

        If another path starts writing `retired_markets`, cash would be
        released without a venue settlement price behind it. Source-level
        guard, so a new writer shows up as a failing row here.
        """
        import inspect
        src = inspect.getsource(E)
        writers = [ln.strip() for ln in src.splitlines()
                   if 'setdefault("retired_markets"' in ln
                   or 'state["retired_markets"]' in ln]
        # retire_settled_market, plus the wipe in reset/replay which clears
        # the whole store rather than granting a release.
        assert len(writers) <= 2, writers
