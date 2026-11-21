"""The inventory must not quietly loosen a money bound, and must not hide
the real blocker behind the gates it removes."""
from src import l5_barrier_inventory as B


def test_every_row_is_fully_classified():
    for b in B.BARRIERS:
        for k in ("barrier", "where", "blocks_now", "l5_supported",
                  "safety_required", "action", "note"):
            assert k in b, (b["barrier"], k)
        assert b["action"] in ("KEEP", "REMOVE")
        assert isinstance(b["blocks_now"], bool)


def test_no_safety_required_barrier_is_removed():
    """The one invariant that matters: dollar, exposure, deadman and
    concurrency bounds survive this change set."""
    for b in B.to_remove():
        assert b["safety_required"] is False, b["barrier"]


def test_the_money_bounds_are_all_kept():
    kept = {b["barrier"] for b in B.to_keep()}
    assert any("SESSION_NOTIONAL_CAP" in k for k in kept)
    assert any("daily realized loss" in k for k in kept)
    assert any("MAX_RESIDUAL_UNITS" in k for k in kept)
    assert any("deadman" in k for k in kept)
    assert any("concurrency" in k for k in kept)


def test_the_binding_barrier_is_named_as_capital_not_a_gate():
    """The owner asked to be told when it is an economic mismatch rather
    than a readiness gate. It is."""
    assert "does NOT unblock the lane" in B.THE_ANSWER
    assert "tenth of the venue minimum" in B.THE_ANSWER
    assert "software gates were never the reason" in B.THE_ANSWER
    top = B.BARRIERS[0]
    assert "SESSION_NOTIONAL_CAP" in top["barrier"]
    assert top["action"] == "KEEP"
    assert "BINDING CONSTRAINT" in top["note"]


def test_removal_leaves_real_blockers_and_says_so():
    still = B.blocking_after_removal()
    assert len(still) >= 4
    names = " ".join(b["barrier"] for b in still)
    assert "SESSION_NOTIONAL_CAP" in names
    assert "daily realized loss" in names


def test_the_removals_are_justified_against_l5_evidence_not_taste():
    """Each REMOVE cites why L5's own tape does not require it."""
    by = {b["barrier"]: b for b in B.to_remove()}
    band = next(v for k, v in by.items() if "SPRING_BAND" in k)
    assert "RISES with attainable pair cost" in band["note"]
    rest = next(v for k, v in by.items() if "MAKER_REST_MAX_S" in k)
    assert "median gap between legs is 47s" in " ".join(rest["note"].split())
    ceil = next(v for k, v in by.items() if "PAIR_TARGET" in k)
    assert "19.7% of matched units" in " ".join(ceil["note"].split())


def test_off_distribution_is_kept_because_it_is_l5_evidence():
    od = next(b for b in B.BARRIERS if "off_distribution" in b["barrier"])
    assert od["action"] == "KEEP"
    assert od["l5_supported"] is True
    assert "fidelity, not ceremony" in od["note"]


def test_the_inventory_states_it_loosens_nothing():
    assert "no dollar-loss, exposure, deadman or concurrency bound is" in \
        " ".join(B.NOT_LOOSENED.split())
