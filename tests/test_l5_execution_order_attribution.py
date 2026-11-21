"""Ownership by VENUE ORDER ID, not client order id alone.

THE DEFECT, and it is the owner's correction. `classify_fills` decided
ownership from the client order id only:

    owner = L5_OWNED if coid in ours else OWNER_MANUAL if coid
            else UNKNOWN_OWNER

so a fill row carrying NO coid became UNKNOWN_OWNER whoever placed it.
Measured on this account: all 74 fills across the 49 unknown-basis legs
carry `clientOrderId` null, so the classifier could never resolve one of
them and every single leg read UNKNOWN_OWNER. That is unresolved attribution
being reported as though it were evidence of external activity -- and $294.09
of placeholder cost was read that way.

THE EVIDENCE WAS ALREADY IN THE FEED. The audit's own
`execution_field_inventory` found 2,038 fill rows carrying an execution
object whose key set includes `order`, while the per-leg view reported
`order_id: null` on all 74. The executor also persists a durable
coid -> venue order id link at booking time, plus the same ids across
`live_orders`, `pending_terminal`, `quarantine` and `submitted` -- the same
record `tracked_coid_for_venue_order` reads to stop a venue order being
tracked twice.

So ownership now has two independent routes, and the third state stays
third: identified-but-not-ours is OWNER_MANUAL, and a fill with no identity
of either kind stays UNKNOWN_OWNER. Every fill records WHICH evidence
resolved it, so a reader can tell a match from an absence.

READ-ONLY throughout. Nothing here writes a ledger key, a cost basis or an
attestation, and nothing can relax a loss halt.
"""
from __future__ import annotations

import json

import pytest

from src import l5_cost_basis_audit as CBA


class FakeStore:
    def __init__(self, state):
        self.state = state


def row(*, market="cpc-btc-updown-15m-2026-10-16-0000z", side="DOWN",
        qty="1.0000", price="0.3000", cost="0.3000", coid=None,
        venue_order=None, kind="activity_type_trade"):
    body = {"marketSlug": market, "qty": qty, "price": price, "cost": cost,
            "fee": "0.0000", "tradeId": "T-%s" % (coid or venue_order or "x"),
            "outcomeSide": "OUTCOME_SIDE_NO" if side == "DOWN"
                           else "OUTCOME_SIDE_YES"}
    if coid:
        body["clientOrderId"] = coid
    if venue_order:
        body["execution"] = {"order": venue_order, "aggressor": False,
                             "tradeId": "T1"}
    return {"type": kind, "trade": body}


def classify(rows, *, our_coids=(), our_vids=None, side="DOWN",
             market="cpc-btc-updown-15m-2026-10-16-0000z"):
    return CBA.classify_fills(rows, market=market, side=side,
                              our_coids=set(our_coids),
                              our_venue_orders=(None if our_vids is None
                                                else set(our_vids)))


# --------------------------------------------- the collector reads our ids

def test_the_collector_reads_the_durable_link_and_every_row_book():
    st = {"venue_order_ids": {"l5f-aaa": {"venue_order_id": "VO-1"}},
          "live_orders": {"l5f-bbb": {"venue_order_id": "VO-2"}},
          "pending_terminal": {"l5f-ccc": {"venue_order_id": "VO-3"}},
          "quarantine": {"l5f-ddd": {"venue_order_id": "VO-4"}},
          "submitted": {"l5f-eee": {"venue_order_id": "VO-5"}}}
    got = CBA.our_venue_order_ids(FakeStore(st))
    assert {"VO-1", "VO-2", "VO-3", "VO-4", "VO-5"} <= got


def test_the_collector_is_empty_and_quiet_on_a_fresh_store():
    assert CBA.our_venue_order_ids(FakeStore({})) == set()
    assert CBA.our_venue_order_ids(FakeStore(None)) == set()


def test_the_collector_never_emits_a_blank_id():
    st = {"live_orders": {"a": {"venue_order_id": ""},
                          "b": {"venue_order_id": "  "},
                          "c": {}}}
    assert CBA.our_venue_order_ids(FakeStore(st)) == set()


# ------------------------------------------- the venue order id is extracted

@pytest.mark.parametrize("body,want", [
    ({"execution": {"order": "VO-9"}}, "VO-9"),
    ({"execution": {"orderId": "VO-9"}}, "VO-9"),
    ({"execution": {"order": {"id": "VO-9"}}}, "VO-9"),
    ({"exec": {"order": "VO-9"}}, "VO-9"),
    ({"orderId": "VO-9"}, "VO-9"),                 # flat fallback
    ({"venueOrderId": "VO-9"}, "VO-9"),
    ({}, None),
    ({"execution": {}}, None),
    ({"execution": {"order": ""}}, None),
])
def test_the_execution_order_is_found_wherever_the_feed_puts_it(body, want):
    assert CBA._row_venue_order_id(body) == want


def test_an_unreadable_body_does_not_raise():
    assert CBA._row_venue_order_id(None) is None
    assert CBA._row_venue_order_id("not a mapping") is None


# ------------------------------------------------ the four ownership states

def test_a_coid_we_know_is_ours_by_client_order_id():
    out = classify([row(coid="l5f-mine")], our_coids=["l5f-mine"])
    f = out["fills"][0]
    assert f["owner"] == CBA.L5_OWNED
    assert f["owner_evidence"] == "client_order_id"


def test_NO_COID_BUT_OUR_VENUE_ORDER_IS_OURS():
    """THE FIX. This exact shape -- no coid, a venue order we track -- was
    classified UNKNOWN_OWNER on all 74 fills of the 49 legs."""
    out = classify([row(venue_order="VO-7")], our_vids=["VO-7"])
    f = out["fills"][0]
    assert f["owner"] == CBA.L5_OWNED
    assert f["owner_evidence"] == "venue_order_id"
    assert f["venue_order_id"] == "VO-7"


def test_a_venue_order_we_do_not_track_is_manual_not_unknown():
    out = classify([row(venue_order="VO-SOMEONE-ELSE")], our_vids=["VO-7"])
    f = out["fills"][0]
    assert f["owner"] == CBA.OWNER_MANUAL
    assert f["owner_evidence"] == "venue_order_id_not_ours"


def test_a_coid_that_is_not_ours_is_manual():
    out = classify([row(coid="someone-else")], our_coids=["l5f-mine"])
    assert out["fills"][0]["owner"] == CBA.OWNER_MANUAL
    assert out["fills"][0]["owner_evidence"] == "client_order_id_not_ours"


def test_no_identity_of_either_kind_stays_unknown():
    """Unresolved attribution is NOT evidence of external activity, so this
    must not be quietly reclassified as manual."""
    out = classify([row()], our_coids=["l5f-mine"], our_vids=["VO-7"])
    f = out["fills"][0]
    assert f["owner"] == CBA.UNKNOWN_OWNER
    assert f["owner_evidence"] == "no_identity_on_the_row"


def test_the_coid_route_wins_when_both_are_present_and_ours():
    out = classify([row(coid="l5f-mine", venue_order="VO-7")],
                   our_coids=["l5f-mine"], our_vids=["VO-7"])
    assert out["fills"][0]["owner_evidence"] == "client_order_id"


def test_the_venue_route_rescues_a_coid_we_do_not_recognise():
    """A coid we never ACKed plus a venue order we DO track: the venue
    evidence is the stronger one, because the link was written at booking."""
    out = classify([row(coid="stale-or-rewritten", venue_order="VO-7")],
                   our_coids=[], our_vids=["VO-7"])
    assert out["fills"][0]["owner"] == CBA.L5_OWNED
    assert out["fills"][0]["owner_evidence"] == "venue_order_id"


# -------------------------------------- backward compatibility is exact

def test_omitting_the_venue_set_reproduces_the_old_behaviour_exactly():
    """171 existing cost-basis tests call classify_fills without the new
    argument; none of them may change."""
    rows = [row(coid="l5f-mine"), row(coid="other"), row()]
    old = CBA.classify_fills(rows, market=row()["trade"]["marketSlug"],
                             side="DOWN", our_coids={"l5f-mine"})
    owners = [f["owner"] for f in old["fills"]]
    assert owners == [CBA.L5_OWNED, CBA.OWNER_MANUAL, CBA.UNKNOWN_OWNER]


def test_an_empty_venue_set_is_the_same_as_omitting_it():
    rows = [row(venue_order="VO-7")]
    a = classify(rows, our_vids=[])
    b = CBA.classify_fills(rows, market=row()["trade"]["marketSlug"],
                           side="DOWN", our_coids=set())
    assert a["fills"][0]["owner"] == b["fills"][0]["owner"] == CBA.OWNER_MANUAL


# ----------------------------------------- the real shape, end to end

def test_the_measured_account_shape_resolves_once_the_link_is_consulted():
    """The 49 legs' real shape: a venue order id present, no coid. With the
    link consulted the same rows resolve to L5_OWNED instead of UNKNOWN."""
    rows = [row(venue_order="DFWWGBW012WP", qty="31.3300", cost="0.9399")]
    before = classify(rows, our_coids=[], our_vids=None)
    after = classify(rows, our_coids=[], our_vids=["DFWWGBW012WP"])
    assert before["fills"][0]["owner"] == CBA.OWNER_MANUAL
    assert after["fills"][0]["owner"] == CBA.L5_OWNED
    # and the cost the placeholder was standing in for is unchanged by either
    assert before["fills"][0]["cost"] == after["fills"][0]["cost"] == "0.9399"


def test_the_counts_still_sum_to_the_fill_count():
    rows = [row(coid="l5f-mine"), row(venue_order="VO-7"),
            row(venue_order="VO-X"), row()]
    out = classify(rows, our_coids=["l5f-mine"], our_vids=["VO-7"])
    assert out["l5_owned"] == 2
    assert out["owner_manual"] == 1
    assert out["unknown_owner"] == 1
    assert sum((out["l5_owned"], out["owner_manual"],
                out["unknown_owner"])) == len(out["fills"])
    assert set(out["owners"]) == {CBA.L5_OWNED, CBA.OWNER_MANUAL,
                                  CBA.UNKNOWN_OWNER}


# ------------------------------------------------------- still read-only

def test_the_module_writes_nothing_and_cannot_relax_a_halt():
    import inspect
    src = inspect.getsource(CBA)
    for forbidden in ("correct_settled_market(", "self.state[",
                      "state[\"realized"):
        assert forbidden not in src, forbidden
    assert "WRITES NOTHING" in src.upper() or "IT WRITES NOTHING" in src


def test_the_collector_does_not_mutate_the_store():
    st = {"live_orders": {"l5f-a": {"venue_order_id": "VO-1"}}}
    snapshot = json.dumps(st, sort_keys=True)
    CBA.our_venue_order_ids(FakeStore(st))
    assert json.dumps(st, sort_keys=True) == snapshot


# ================================================================
# THE REAL FEED SHAPE, and the defect the production audit exposed.
#
# The 2026-11-03T16:22:24Z audit run collected 420 venue order ids of ours
# and matched NONE of them: `venue_order_id` came back null on all 76 fills
# of the 51 unknown-basis legs and every one read UNKNOWN_OWNER /
# no_identity_on_the_row. The cause was the extraction path, not the
# evidence -- the feed nests the id under `aggressorExecution` /
# `passiveExecution` (EXEC_KEYS), never under `execution`.
# ================================================================

def real_row(*, market="cpc-btc-updown-15m-2026-10-16-0000z", side="UP",
             ours="VO-OURS", theirs="VO-THEIRS", is_aggressor=True,
             qty="0.4500", price="0.0700", cost="0.0315"):
    """A trade row shaped the way PMUS actually sends one."""
    agg, pas = (ours, theirs) if is_aggressor else (theirs, ours)
    body = {
        "marketSlug": market, "qty": qty, "price": price, "cost": cost,
        "tradeId": "D3NMZPH42Z6Y", "isAggressor": is_aggressor,
        "outcomeSide": "OUTCOME_SIDE_YES" if side == "UP"
                       else "OUTCOME_SIDE_NO",
        "aggressorExecution": {"order": agg, "lastPx": price,
                               "lastShares": qty,
                               "commissionNotionalCollected": "0"},
        "passiveExecution": {"order": pas, "lastPx": price,
                             "lastShares": qty,
                             "commissionNotionalCollected": "0"},
    }
    return {"type": "activity_type_trade", "trade": body}


def test_THE_REAL_NESTING_IS_READ_at_all():
    """Before the fix this returned None on every production fill."""
    assert CBA._row_venue_order_id(real_row()["trade"]) == "VO-OURS"


def test_the_aggressor_flag_picks_our_execution_not_the_counterpartys():
    ours_t, every_t, src_t = CBA._row_venue_order_candidates(
        real_row(is_aggressor=True)["trade"])
    assert (ours_t, src_t) == ("VO-OURS", "flag")
    assert every_t == {"VO-OURS", "VO-THEIRS"}
    ours_f, every_f, src_f = CBA._row_venue_order_candidates(
        real_row(is_aggressor=False)["trade"])
    assert (ours_f, src_f) == ("VO-OURS", "flag")
    assert every_f == {"VO-OURS", "VO-THEIRS"}


def test_the_real_shape_now_resolves_to_L5_OWNED():
    out = classify([real_row()], our_coids=[], our_vids=["VO-OURS"], side="UP")
    f = out["fills"][0]
    assert f["owner"] == CBA.L5_OWNED
    assert f["owner_evidence"] == "venue_order_id"
    assert f["venue_order_id"] == "VO-OURS"


def test_a_positive_match_resolves_even_without_the_aggressor_flag():
    """An id of OURS cannot be the counterparty's, so the match still holds
    when the row does not say which side we are."""
    r = real_row()
    r["trade"].pop("isAggressor")
    out = classify([r], our_coids=[], our_vids=["VO-OURS"], side="UP")
    assert out["fills"][0]["owner"] == CBA.L5_OWNED
    assert out["fills"][0]["venue_order_id"] == "VO-OURS"


def test_a_counterparty_id_alone_stays_UNKNOWN_and_is_never_called_manual():
    """THE ASYMMETRY. Without the flag we cannot tell which of the two ids
    is ours, so a non-match proves nothing. Calling it OWNER_MANUAL would
    report unresolved attribution as evidence of external activity --
    exactly the error the owner corrected."""
    r = real_row()
    r["trade"].pop("isAggressor")
    out = classify([r], our_coids=[], our_vids=["VO-SOMETHING-ELSE"],
                   side="UP")
    assert out["fills"][0]["owner"] == CBA.UNKNOWN_OWNER
    assert out["fills"][0]["owner_evidence"] == "no_identity_on_the_row"


def test_a_flag_sourced_negative_IS_manual_ONLY_once_calibrated():
    """The flag says which execution is ours -- but only a CALIBRATED flag
    makes that positive evidence of someone else's order."""
    out = CBA.classify_fills(
        [real_row(ours="VO-NOT-TRACKED")],
        market=real_row()["trade"]["marketSlug"], side="UP",
        our_coids=set(), our_venue_orders={"VO-OURS"}, flag_calibrated=True)
    assert out["fills"][0]["owner"] == CBA.OWNER_MANUAL
    assert out["fills"][0]["owner_evidence"] == "venue_order_id_not_ours"


def test_AN_UNCALIBRATED_FLAG_SOURCED_NEGATIVE_IS_NOT_MANUAL():
    """PRODUCTION'S ACTUAL CASE, and the second over-claim this module made.

    The 2026-11-03T17:06:15Z run returned NOT_CALIBRATED with EMPTY counts:
    no fill row in the 2543-row walk carries a client order id at all, so
    the one independent route that could check `isAggressor` had no rows to
    check it on. With the flag unverified, calling 75 of 76 fills
    OWNER_MANUAL asserts external activity on a SHARED account from an
    unverified reading of a field name -- the same error as the original
    UNKNOWN_OWNER sweep, pointing the other way.

    The id is still REPORTED. What is withheld is the conclusion."""
    for cal in (None, False):
        out = CBA.classify_fills(
            [real_row(ours="VO-NOT-TRACKED")],
            market=real_row()["trade"]["marketSlug"], side="UP",
            our_coids=set(), our_venue_orders={"VO-OURS"},
            flag_calibrated=cal)
        f = out["fills"][0]
        assert f["owner"] == CBA.UNKNOWN_OWNER, cal
        assert f["owner_evidence"] == CBA.EV_NOT_OURS_UNCALIBRATED, cal
        assert f["venue_order_id"] == "VO-NOT-TRACKED", cal


def test_a_POSITIVE_match_never_needs_the_flag_calibrated():
    """An id in our own tracked set cannot be the counterparty's, so the
    asymmetry holds: positives resolve, negatives wait for calibration."""
    for cal in (None, False, True):
        out = CBA.classify_fills(
            [real_row()], market=real_row()["trade"]["marketSlug"], side="UP",
            our_coids=set(), our_venue_orders={"VO-OURS"},
            flag_calibrated=cal)
        assert out["fills"][0]["owner"] == CBA.L5_OWNED, cal
        assert out["fills"][0]["owner_evidence"] == "venue_order_id", cal


def test_an_UNAMBIGUOUS_shape_is_manual_without_any_calibration():
    """A single execution object or a flat id leaves nothing to select, so
    the flag is irrelevant and the negative verdict stands on its own."""
    out = classify([row(venue_order="VO-SOMEONE-ELSE")], our_vids=["VO-7"])
    assert out["fills"][0]["owner"] == CBA.OWNER_MANUAL
    assert out["fills"][0]["owner_evidence"] == "venue_order_id_not_ours"


def test_the_coid_route_is_untouched_by_the_nesting_fix():
    r = real_row()
    r["trade"]["clientOrderId"] = "l5f-mine"
    out = classify([r], our_coids=["l5f-mine"], our_vids=[], side="UP")
    assert out["fills"][0]["owner_evidence"] == "client_order_id"


def test_the_nesting_fix_changes_no_money_field():
    """Attribution only. The cost the placeholder stood in for is the same
    figure before and after."""
    r = real_row(qty="2.9000", price="0.2034", cost="0.5900")
    a = classify([r], our_coids=[], our_vids=[], side="UP")["fills"][0]
    b = classify([r], our_coids=[], our_vids=["VO-OURS"],
                 side="UP")["fills"][0]
    assert a["cost"] == b["cost"] == "0.5900"
    assert a["qty"] == b["qty"]
    assert a["price"] == b["price"]
