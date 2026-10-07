"""Is the venue's realized P&L already NET of commission? Pinned, from
committed artifacts only.

WHY IT MATTERS. Ledger C holds $9.14 of maker rebates the venue actually
credited, per fill, as a negative `commissionNotionalCollected`. Ledger D
holds the venue's own realized figure. If the realized figure is already net
of that commission, then adding the $9.14 counts it twice and every "all-in
realized" number downstream is wrong by up to $9.14.

WHAT FIELD IS ACTUALLY UNDER TEST. The brief names `trade.realizedPnl`. That
field is **null on every PMUS trade row in every committed artifact**
(`r4_settlements.json`: `realized_pnl_rows: 0`; the 2026-09-13 live tape: 18
occurrences, none with a value), so it feeds no figure at all. The field that
actually produces Ledger D's `realized_usd` is the POSITION_RESOLUTION
activity's `afterPosition.realized`
(`pmus_settlement_auditor.audit_market`). That is what these tests settle.

THE ARITHMETIC. Two hypotheses differ by exactly the market's credited
commission:

    H_GROSS : realized == payout - cost_basis
    H_NET   : realized == payout - cost_basis +/- credited_commission

`aec-atpcq-aleher-tundur-2026-09-07` is the discriminating case: both figures
are present AND non-zero for the same market, and the credited commission is
3.0% of the realized figure -- far above the 4-decimal resolution of either
number.
"""
import json
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIDELITY = json.loads((ROOT / "analysis/l5/r4_fidelity_decomposition.json")
                      .read_text())
SETTLEMENTS = json.loads((ROOT / "analysis/l5/r4_settlements.json").read_text())
CREDITS = json.loads((ROOT / "analysis/l5/venue_rebate_credits.json").read_text())

DISCRIMINATING = "aec-atpcq-aleher-tundur-2026-09-07"


def _case():
    s = FIDELITY["per_market"][DISCRIMINATING]["settlement"]
    b = s["derived_breakdown"]
    return {
        "realized": D(s["realized_usd_venue"]),
        "payout": D(b["bought_yes"]),            # paying side YES, $1.00 each
        "cost_basis": D(b["cost_basis_usd"]),
        "credited": D(str(CREDITS["by_market"][DISCRIMINATING]["credited_usd"])),
        "paying_side": s["side"],
    }


def test_the_discriminating_case_has_both_figures_present_and_non_zero():
    c = _case()
    assert c["realized"] != 0 and c["credited"] != 0
    assert c["paying_side"] == "YES"
    assert CREDITS["by_market"][DISCRIMINATING]["prints"] == 4
    assert CREDITS["schema"]["venue_required"] is True
    assert all(r["venue"] == "polymarket_us" for r in CREDITS["rows"])
    # the commission is large enough to tell the hypotheses apart
    assert c["credited"] / abs(c["realized"]) > D("0.02")


def test_venue_realized_reconciles_to_GROSS_not_to_net_of_commission():
    c = _case()
    gross = c["payout"] - c["cost_basis"]
    assert gross == c["realized"] == D("1.9955")
    # H_NET in either sign direction is excluded
    assert c["realized"] != gross + c["credited"]      # 2.0555
    assert c["realized"] != gross - c["credited"]      # 1.9355


def test_the_second_settled_market_agrees_rather_than_disagreeing():
    """A single fill, lost outright: realized is exactly -(qty x price), with
    no commission term. Supporting, not discriminating -- this market's own
    credit rows fall outside the committed credits snapshot."""
    b = SETTLEMENTS["aec-atp-petbru-alecan-2026-09-07"]["breakdown"]
    cost = D(b["bought_yes"]) * D(b["avg_yes"])
    assert cost == D(b["cost_basis_usd"]) == D("1.9912")
    assert D(b["realized_usd_venue"]) == -cost
    assert DISCRIMINATING not in SETTLEMENTS           # distinct evidence


def test_so_the_9pt14_of_maker_rebates_is_additive_and_not_a_double_count():
    """The verdict, stated as the thing that must not drift: incentives are a
    SEPARATE line that may be added to venue realized P&L."""
    c = _case()
    assert c["realized"] == c["payout"] - c["cost_basis"]
    all_in = c["realized"] + c["credited"]
    assert all_in == D("2.0555")
    # and the venue reports no fee of its own inside the breakdown
    assert FIDELITY["per_market"][DISCRIMINATING]["settlement"][
        "derived_breakdown"]["fees_usd"] == "venue_unreported"


def test_trade_realizedPnl_itself_is_null_everywhere_in_committed_data():
    """So the field the brief names cannot be the source of any figure, and no
    future reader should re-open the question against it by name."""
    for slug, rec in SETTLEMENTS.items():
        tr = rec["trades"]
        assert tr["realized_pnl_rows"] == 0
        assert tr["realized_pnl_sum"] is None
        assert all(row["rpnl"] is None for row in tr["rows"])
    tape = (ROOT / "analysis/l5/live_tape_2026-09-13/r5ft_live_tape.jsonl")
    seen = 0
    for line in tape.read_text().splitlines():
        if "realizedPnl" not in line:
            continue
        for t in json.loads(line)["trade_summary"]["trades"]:
            assert t["realizedPnl"] is None
            seen += 1
    assert seen > 0


def test_the_two_streams_stay_separately_labelled():
    """Resolved-not-a-double-count is permission to ADD, never permission to
    merge: rebates remain their own line."""
    assert CREDITS["by_market"][DISCRIMINATING]["credited_usd"] == 0.06
    totals = sum(D(r["our_commission"]) for r in CREDITS["rows"])
    assert totals < 0                 # venue-negative == credited to us
