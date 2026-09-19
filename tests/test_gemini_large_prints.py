"""Large-print forensics: the frozen policy replayed, never re-decided."""
from decimal import Decimal as D

import pytest

from src.gemini_execution import large_prints as LP


def test_the_buckets_are_the_ones_asked_for():
    assert LP.bucket_of("9.99") == "<10"
    assert LP.bucket_of("10") == "10-25"
    assert LP.bucket_of("24.99") == "10-25"
    assert LP.bucket_of("25") == "25-100"
    assert LP.bucket_of("99.99") == "25-100"
    assert LP.bucket_of("100") == "100+"
    assert LP.bucket_of("714") == "100+"
    assert LP.bucket_of(None) is None


# ============================ the state mirror is pinned to the frozen one

def test_THE_STATE_BUILDER_MIRRORS_THE_FROZEN_ONE_EXACTLY(monkeypatch):
    """This module builds the policy's input state from a recorded book to
    avoid one network read per replayed cycle. If the mirror drifts, every
    replayed decision is computed from a different state than the live path
    would have seen -- so it is pinned."""
    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import metadata as MD

    book = {"readable": True, "identity_consistent": True, "tick": "0.01",
            "t_utc": 1789781728,
            "yes": {"bid": "0.31", "ask": "0.58"},
            "no": {"bid": "0.42", "ask": "0.69"}}
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})
    inv = CP.Inventory()
    theirs = CP.state_from_venue("S", 271.0, inv, now=1789781728, book=book)
    mine = LP.state_from_recorded_book("S", 271.0, inv, book)
    assert mine == theirs


def test_an_unreadable_book_mirrors_the_frozen_refusal(monkeypatch):
    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import metadata as MD
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})
    inv = CP.Inventory()
    for book in ({"readable": False, "t_utc": 1.0},
                 {"readable": True, "identity_consistent": False,
                  "t_utc": 1.0},
                 {"readable": True, "identity_consistent": True, "t_utc": 1.0,
                  "yes": {"bid": "0.60", "ask": "0.40"},
                  "no": {"bid": "0.60", "ask": "0.40"}}):
        mine = LP.state_from_recorded_book("S", 100.0, inv, book)
        theirs = CP.state_from_venue("S", 100.0, inv, now=1.0, book=book)
        assert mine["inventory_known"] is False
        assert mine["unreadable_why"] == theirs["unreadable_why"]


# ================================= the tape's side convention, both legs

def test_a_YES_rung_is_reached_by_an_aggressor_selling_YES():
    assert LP.reaches("YES", "0.31", {"type": "sell", "price": "0.30"})
    assert not LP.reaches("YES", "0.31", {"type": "sell", "price": "0.40"})
    assert not LP.reaches("YES", "0.31", {"type": "buy", "price": "0.30"})


def test_a_NO_rung_is_reached_by_an_aggressor_BUYING_YES():
    """Checked against CONTINUOUS-5: our passive NO buy at 0.46 appears on
    the tape as an aggressor YES buy at 0.54."""
    assert LP.reaches("NO", "0.46", {"type": "buy", "price": "0.54"})
    assert LP.reaches("NO", "0.46", {"type": "buy", "price": "0.60"})
    assert not LP.reaches("NO", "0.46", {"type": "buy", "price": "0.50"})
    assert not LP.reaches("NO", "0.46", {"type": "sell", "price": "0.54"})


# ================================= the queue decides how much we get

def test_a_print_smaller_than_the_queue_gives_us_nothing():
    got = LP.absorbed({"amount": "3.66"}, D("245"), "0.01")
    assert got["computable"] is True
    assert got["hypothetically_filled"] == "0"
    assert got["reached_us"] == "0"


def test_a_big_print_fills_our_whole_size_and_no_more():
    got = LP.absorbed({"amount": "714"}, D("0"), "0.01")
    assert got["hypothetically_filled"] == "0.01"
    assert got["fully_filled"] is True


def test_larger_observational_sizes_absorb_more_of_the_same_print():
    small = LP.absorbed({"amount": "5"}, D("0"), "0.01")
    big = LP.absorbed({"amount": "5"}, D("0"), "10.00")
    assert small["hypothetically_filled"] == "0.01"
    assert big["hypothetically_filled"] == "5"      # capped by the print
    assert big["fully_filled"] is False


def test_an_unreadable_queue_refuses_to_state_an_amount():
    got = LP.absorbed({"amount": "100"}, None, "0.01")
    assert got["computable"] is False
    assert "one ladder, the YES side" in got["why"]


# ================================= reconstruction only where evidence exists

def test_a_lookback_with_no_cycle_of_ours_is_declared_absent():
    rows = LP.reconstruct_before({"utc_ms": 1_000_000, "type": "sell"},
                                 "S", [])
    assert [r["lookback_s"] for r in rows] == list(LP.LOOKBACKS_S)
    assert all(r["evidence"] is False for r in rows)
    assert "not reconstructed" in rows[0]["why"]


def test_nearest_cycle_never_interpolates_beyond_tolerance():
    cycles = [{"utc_ms": 1_000_000}]
    assert LP.nearest_cycle(cycles, 1_000_500) is cycles[0]
    assert LP.nearest_cycle(cycles, 1_099_000) is None


def test_the_census_states_its_coverage_limit():
    got = LP.census([])
    assert "recorded and NOT evaluated rather than guessed at" \
        in got["coverage"]["why"]


# ================================= the path is the frozen policy

def test_every_theoretical_fill_carries_its_provenance():
    """`Inventory.apply_fill` requires a source, and every fill in this
    replay is marked as what it is so no reader can mistake it for a venue
    fill."""
    import inspect
    src = inspect.getsource(LP.inventory_path)
    assert "SHADOW_FORENSIC_SEED_THEORETICAL" in src
    assert "SHADOW_FORENSIC_REPLAY_THEORETICAL" in src


def test_the_module_chooses_no_price_size_or_action():
    """Model A is untouched: the actions come from the frozen `plan`."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(LP))
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "plan" in calls
    assert "desired_surface" not in calls
    src = inspect.getsource(LP)
    for bad in ("order.place", "order.cancel", "ws_session"):
        assert bad not in src, bad


def test_a_seed_fill_that_is_not_a_fill_is_refused():
    got = LP.inventory_path(symbol="S", leg="YES", qty="0", price="0.31",
                            from_ms=0, cycles=[], prints=[], resolution={})
    assert got["computable"] is False


def test_the_path_labels_name_every_stage_the_owner_asked_for():
    got = LP.inventory_path(symbol="S", leg="YES", qty="0.01", price="0.31",
                            from_ms=0, cycles=[], prints=[],
                            resolution={"verdict": "SETTLED",
                                        "resolutionSide": "no"})
    for stage in ("maker fill", "movement", "complement", "matched-pair",
                  "residual", "settlement"):
        assert stage in got["path_labels"]
    assert "nothing here is realized" in got["path_labels"]


def test_matched_and_residual_are_never_summed_in_the_path():
    got = LP.inventory_path(symbol="S", leg="YES", qty="0.01", price="0.31",
                            from_ms=0, cycles=[], prints=[],
                            resolution={"verdict": "SETTLED",
                                        "resolutionSide": "no"})
    s = got["settlement"]
    assert "matched_pair_economics" in s and "directional_residual_pnl" in s
    assert s.get("realized") in (None, "0", 0)


@pytest.mark.parametrize("bad", [None, "", "abc"])
def test_garbage_in_is_refused(bad):
    assert LP.bucket_of(bad) is None
