"""The entry study must not be able to read the future, and its own
arithmetic must not fool it twice.

TWO DEAD METRICS ARE PINNED HERE. The residual study pre-registered a
guaranteed-floor discriminator that is arithmetically always-true. The entry
study then pre-registered `entry_px + opp_ask`, which is identically 1.0000
whenever we join our side's touch -- because on a binary book the complement's
ask IS `1 - our_bid`. Both were registered as discriminators; both were
constants. The identity is asserted below so nobody re-derives it as a feature
a third time.
"""
from __future__ import annotations

import ast
import inspect

from src import l5_entry_quality as eq

BANNED_READS = ("realized_usd", "realized_net_usd", "resolution",
                "resolved_side", "settlement", "settled_utc", "winner")


def _row(label, spread, **kw):
    r = {"slug": "s", "label": label, "spread": spread,
         "first_fill_utc": "2026-09-08T00:00:00Z"}
    r.update(kw)
    return r


# ------------------------------------------------- no settlement hindsight
def test_the_module_never_reads_a_settlement_field():
    """Walks the AST for field READS, not for words: a first version of the
    residual test failed on the module's own sentence saying those fields
    are not read."""
    tree = ast.parse(inspect.getsource(eq))
    reads = set()
    for node in ast.walk(tree):
        # attribute access: obj.settlement
        if isinstance(node, ast.Attribute):
            reads.add(node.attr)
        # mapping read by key: obj.get("settlement") and obj["settlement"]
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant)):
            reads.add(node.args[0].value)
        if (isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)):
            reads.add(node.slice.value)
    for banned in BANNED_READS:
        assert banned not in reads, f"reads {banned}"


def test_the_label_comes_from_inventory_not_from_settlement():
    assert "residual" in inspect.getsource(eq.entry_row)
    assert "STRANDED" in inspect.getsource(eq.entry_row)


# --------------------------------------------------------- the identity
def test_entry_plus_complement_ask_is_one_by_construction():
    """Amendment 1. Joining our side's touch makes immediate completion cost
    exactly $1.00, so it can never separate paired from stranded. This is
    arithmetic; the test states it so it cannot be re-proposed."""
    for yes_bid in (0.05, 0.37, 0.45, 0.50, 0.63, 0.94):
        no_ask = 1.0 - yes_bid          # the venue's own book identity
        assert round(yes_bid + no_ask, 10) == 1.0


def test_the_spread_is_the_whole_gross_edge():
    """Both legs maker: buy YES at the bid, buy NO at ITS bid (1 - yes_ask).
    Pair cost = 1 - spread, so the spread is the entire attainable edge."""
    yes_bid, yes_ask = 0.45, 0.47
    no_bid = 1.0 - yes_ask
    assert round(yes_bid + no_bid, 10) == round(1.0 - (yes_ask - yes_bid), 10)
    f = eq.selection_features({"bid": "0.45", "ask": "0.47"})
    assert round(f["spread"], 10) == 0.02
    assert round(f["best_pair_cost"], 10) == 0.98


# ------------------------------------------------------ the filter's shape
def test_the_threshold_refuses_thin_books_not_expensive_ones():
    """Direction inverted by Amendment 1. A rule that kept the thin books
    would be the original hypothesis, which the identity killed."""
    rows = [_row("PAIRED", 0.05), _row("STRANDED", 0.005)]
    got = eq.apply_threshold(rows, 0.01)
    assert got["retained"] == 1 and got["discarded"] == 1
    assert got["retained_paired"] == 1 and got["retained_stranded"] == 0


def test_retention_is_reported_beside_every_incidence_figure():
    """Pre-registration 8-2: a filter that keeps four episodes has not solved
    incidence, it has stopped trading. The counts must travel with the rate."""
    rows = [_row("PAIRED", 0.05)] + [_row("STRANDED", 0.005)] * 9
    got = eq.apply_threshold(rows, 0.01)
    assert got["incidence"] == 0.0
    assert got["retained"] == 1, "1 of 10 -- and the row says so"
    assert got["paired_kept_share"] == 1.0


def test_the_counterfactual_is_exact_and_signed_the_right_way():
    """Avoiding a stranded market is worth +1.9120; losing a paired one costs
    -0.2851. A sign error here would recommend trading the losers."""
    rows = [_row("PAIRED", 0.05), _row("STRANDED", 0.005)]
    got = eq.apply_threshold(rows, 0.01)
    assert float(got["delta_usd_indicative"]) > 0
    flipped = eq.apply_threshold(
        [_row("PAIRED", 0.005), _row("STRANDED", 0.05)], 0.01)
    assert float(flipped["delta_usd_indicative"]) < 0


# ------------------------------------------------------------ the split
def test_the_split_is_chronological_and_two_thirds():
    rows = [_row("PAIRED", 0.01, ) for _ in range(9)]
    for i, r in enumerate(rows):
        r["first_fill_utc"] = f"2026-09-0{i+1}T00:00:00Z"
    sp = eq.split(rows)
    assert len(sp["train"]) == 6 and len(sp["holdout"]) == 3
    assert sp["train"][-1]["first_fill_utc"] < sp["holdout"][0]["first_fill_utc"]


def test_the_holdout_is_sealed_unless_explicitly_opened():
    src = inspect.getsource(eq.study)
    assert 'open_holdout' in src and '"status": "SEALED"' in src


# ------------------------------------------------------------- measuring
def test_auc_is_a_half_when_the_feature_says_nothing():
    rows = [_row("PAIRED", 0.02), _row("STRANDED", 0.02)]
    assert eq.auc(rows) == 0.5


def test_auc_direction_matches_the_hypothesis():
    """Thin strands -> stranded scores LOWER on spread -> AUC below 0.5."""
    rows = [_row("PAIRED", 0.05), _row("STRANDED", 0.005)]
    assert eq.auc(rows) == 0.0


def test_a_fill_is_a_fill_even_when_partial():
    assert eq._is_filled({"filled_qty": "0.02"}) is True
    assert eq._is_filled({"filled_qty": "0"}) is False
    assert eq._is_filled(None) is False


def test_category_uses_the_venue_prefix_grammar():
    assert eq.category_of("aec-atp-nickja-moeech-2026-09-11") == "aec-atp"
    assert eq.category_of("tsc-mlb-laa-bos-2026-09-07-7pt5") == "tsc-mlb"


# ------------------------------------------------------- read-only, pure
def test_the_module_cannot_trade():
    src = inspect.getsource(eq)
    for banned in ("place_limit", "submit", "cancel", "Transport", "request(",
                   "os.environ", "set_concurrent_owned"):
        assert banned not in src, banned


def test_only_the_chosen_market_contributes_its_book():
    """The other admitted rows describe markets we passed over. Counting
    their spreads would measure the selector's menu, not our trades."""
    src = inspect.getsource(eq.selections_by_market)
    assert 'adm.get("slug") == chosen' in src
