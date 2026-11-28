"""The offline join, which needs no change to the money path.

Both inputs are records the DEPLOYED engine already emits:
L5_FILL_BOOK_EVIDENCE per fill and L5_WINDOW_ACCOUNTING phase CONFIRMED per
settled window. Joining them here rather than in the runner is deliberate --
the programme measures the execution model we already run, and instrumenting
the money path would measure a different one.
"""
import pytest

from analysis.l5.fill_outcome_join import join, report, settled_side_of

SLUG = "cpc-btc-updown-15m-2026-11-05-0245z"
REAL = [
    {"slug": SLUG, "coid": "l5f-5134e3b3", "side": "UP", "fill_qty": "1",
     "fill_own_px": "0.5700", "order_resting": True, "basis": "venue"},
    {"slug": SLUG, "coid": "l5f-aab4d9a8", "side": "DOWN", "fill_qty": "1",
     "fill_own_px": "0.4200", "order_resting": True, "basis": "venue"},
    {"slug": SLUG, "coid": "l5f-d6d5a2ce", "side": "UP", "fill_qty": "1",
     "fill_own_px": "0.3000", "order_resting": True, "basis": "venue"},
]
ACCT = [{"phase": "CONFIRMED", "market": SLUG,
         "execution": {"actual_fees_usd": "0"},
         "settled": {"residual_side": "UP", "residual_qty": "1",
                     "residual_pnl_usd": "-0.4350",
                     "resolution_source": "VENUE_SETTLEMENT_PX"}}]


# ----------------------------------------------- deriving the winner

def test_yes_value_one_is_up():
    assert settled_side_of({"yes_value": "1"}) == "UP"
    assert settled_side_of({"yes_value": "1.0000"}) == "UP"


def test_yes_value_zero_is_down():
    assert settled_side_of({"settlement_px": "0"}) == "DOWN"


def test_a_losing_residual_implies_the_other_side_won():
    assert settled_side_of(ACCT[0]) == "DOWN"


def test_a_winning_residual_implies_its_own_side_won():
    a = {"settled": {"residual_side": "UP", "residual_qty": "1",
                     "residual_pnl_usd": "+0.5000"}}
    assert settled_side_of(a) == "UP"


@pytest.mark.parametrize("a", [
    {}, {"yes_value": None}, {"yes_value": "0.5"}, {"yes_value": "banana"},
    {"settled": {}},
    {"settled": {"residual_side": "UP", "residual_qty": "0",
                 "residual_pnl_usd": "0"}},        # flat: nothing to infer
    {"settled": {"residual_side": "", "residual_qty": "1",
                 "residual_pnl_usd": "-1"}},
])
def test_an_unreadable_settlement_is_none_never_a_guess(a):
    assert settled_side_of(a) is None


def test_a_non_binary_settlement_px_is_refused():
    """0.01 was a real defect once; it is not a settlement."""
    assert settled_side_of({"settlement_px": "0.0100"}) is None


# -------------------------------------------------------- the join

def test_the_join_reproduces_campaign_e():
    w = join(REAL, ACCT)[0]
    assert w["n_fills"] == 3
    assert w["matched_qty"] == "1" and w["residual_qty"] == "1"
    assert w["residual_side"] == "UP"
    assert w["residual_side_lost"] is True
    assert w["settled_side"] == "DOWN"


def test_gross_per_fill_still_sums_to_the_venue_net():
    from decimal import Decimal as D
    w = join(REAL, ACCT)[0]
    assert sum(D(f["gross_settle_usd"]) for f in w["fills"]) == D("-0.29")


@pytest.mark.parametrize("basis", ["MODELLED_DESPITE_REFUSAL", "MODEL",
                                   "SHADOW_FILL", "REPLAY", "modelled"])
def test_a_modelled_row_is_excluded_on_its_label(basis):
    """Do not treat replay or modelled fills as confirmed fills."""
    extra = dict(REAL[0], coid="x", basis=basis, fill_qty="9")
    w = join(REAL + [extra], ACCT)[0]
    assert w["n_fills"] == 3


def test_a_row_without_a_coid_is_excluded():
    w = join(REAL + [{"slug": SLUG, "side": "UP", "fill_qty": "5",
                      "fill_own_px": "0.2", "basis": "venue"}], ACCT)[0]
    assert w["n_fills"] == 3


def test_provisional_windows_are_not_counted():
    acct = [dict(ACCT[0], phase="PROVISIONAL")]
    assert join(REAL, acct) == []


def test_a_window_is_counted_once_even_if_confirmed_twice():
    assert len(join(REAL, ACCT + ACCT)) == 1


def test_fills_are_matched_to_their_own_window():
    other = [dict(r, slug="other-window") for r in REAL]
    w = join(REAL + other, ACCT)[0]
    assert w["n_fills"] == 3


def test_a_confirmed_window_with_no_fills_still_reports():
    w = join([], ACCT)[0]
    assert w["n_fills"] == 0
    assert w["residual_side_lost"] is None


# ------------------------------------------------------- the report

def test_report_counts_and_refuses_to_estimate_latency():
    r = report(REAL, ACCT)
    assert r["windows_confirmed"] == 1
    assert r["fills"] == 3
    assert r["latency_observations"] == 0
    assert "unmeasurable" in r["latency_note"]


def test_report_carries_both_denominators():
    r = report(REAL, ACCT)["adverse_selection"]
    assert "window_level" in r and "fill_level_clustered" in r
    assert r["window_level"]["windows_with_residual"] == 1


def test_latency_is_counted_when_the_venue_supplies_it():
    withlat = [dict(REAL[0], latency_s="0.31")] + REAL[1:]
    assert report(withlat, ACCT)["latency_observations"] == 1
