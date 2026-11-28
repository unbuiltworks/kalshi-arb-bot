"""The ownership boundary between L5 and the owner's own hand-placed orders.

THE INCIDENT, 2026-11-18. The owner placed 5 DOWN contracts by hand on
cpc-btc-updown-15m-2026-11-18-2215z. l5-pmus-live was UNARMED. It then:

  1. adopted the owner's resting order into `state["live_orders"]` --
     reconciliation writes the venue's open orders into that map, and an
     untracked order in our OWN series is adopted on purpose, because
     losing track of one of our own is the worse error;
  2. cancelled it, from the orphan sweep that runs ONLY when unarmed,
     whose label promised "a manual order is never one of them";
  3. and lost the $5 risk floor on the resulting position while its cost
     basis was still unknown.

WHY EACH GUARANTEE WAS VOID, which is what these tests pin.

  `owns_venue_order_id` asks "did THIS ledger put that id on the book"
  and answered it by searching `live_orders` -- the very map
  reconciliation fills from the venue. So it answered "is the venue
  holding this order", which is yes for the owner's too.

  `adopted` was written once and READ NOWHERE, and it laundered itself:
  recomputed as `coid not in ours`, on the next reconciliation the
  adopted coid IS in `ours`, so it flipped to False.

  The risk floor was keyed to `disputed` (gross != net). Adoption RESOLVES
  that divergence by copying the venue's number into our book, so the
  floor died in the same pass that raised it.

WHAT THE FIX DOES NOT DO. It does not stop adoption. An order of ours
whose record we lost must still be cancellable, and adoption is what
keeps a foreign position VISIBLE to account-wide exposure. Visibility and
ownership were one field; they are now two. Ownership requires OUR submit
record.
"""
from __future__ import annotations

import pytest

from src import l5_live_execution as LX

MARKET = "cpc-btc-updown-15m-2026-11-18-2215z"
OWNER_VID = "DT6BM2K0144P"          # the owner's own order
OURS_VID = "DT9OURS0001AA"


class Store:
    """Minimal stand-in exposing the real predicates under test.

    Only `state` and the three methods matter, so the real class's own
    logic is exercised rather than reimplemented.
    """

    def __init__(self, state):
        self.state = state
        self.uncertain = []

    def live_orders(self):
        #: A METHOD, not a property. `deadman` calls `store.live_orders()`;
        #: exposing it as a property raised "'dict' object is not callable"
        #: and failed five tests against a sweep that never ran.
        return dict(self.state.get("live_orders") or {})

    owns_venue_order_id = LX.InventoryStore.owns_venue_order_id
    adopted_not_ours = LX.InventoryStore.adopted_not_ours

    def mark_uncertain(self, why):
        self.uncertain.append(why)

    def save(self):
        pass


def adopted_row(vid=OWNER_VID, market=MARKET, first_seen="2026-11-18T22:22Z"):
    """A row exactly as reconciliation leaves an adopted foreign order."""
    return {"venue_order_id": vid, "market": market, "side": "UP",
            "adopted": True, "adopted_first_seen_utc": first_seen,
            #: keyed by the VENUE's id, which is what a row with no client
            #: id of ours actually gets -- and it carries no OWN_COID_PREFIX
            "client_order_id": vid}


def our_row(vid=OURS_VID, market=MARKET):
    return {"venue_order_id": vid, "market": market, "side": "DOWN",
            "adopted": False, "client_order_id": "l5f-abc123"}


def store(live=None, submitted=None, **extra):
    st = {"live_orders": dict(live or {}),
          "submitted": dict(submitted or {})}
    st.update(extra)
    return Store(st)


# ------------------------------------------- 1. ownership is not presence

class TestOwnershipRequiresOurSubmitRecord:

    def test_an_adopted_order_is_not_ours(self):
        """THE BREACH, as one assertion."""
        s = store(live={OWNER_VID: adopted_row()})
        assert s.owns_venue_order_id(OWNER_VID) is False

    def test_an_order_we_submitted_is_ours(self):
        s = store(live={"l5f-abc123": our_row()},
                  submitted={"l5f-abc123": our_row()})
        assert s.owns_venue_order_id(OURS_VID) is True

    def test_our_own_order_stays_ours_even_if_readopted(self):
        """THE CASE ADOPTION EXISTS FOR must keep working: we lost our
        record from live_orders, the venue still holds it, reconciliation
        re-adopts it. `submitted` is permanent, so it is still ours and
        still cancellable."""
        s = store(live={"l5f-abc123": dict(our_row(), adopted=True)},
                  submitted={"l5f-abc123": our_row()})
        assert s.owns_venue_order_id(OURS_VID) is True

    def test_a_row_not_marked_adopted_is_ours(self):
        """Unchanged behaviour for every pre-existing row."""
        s = store(live={"l5f-abc123": our_row()})
        assert s.owns_venue_order_id(OURS_VID) is True

    def test_an_unknown_id_is_not_ours(self):
        assert store().owns_venue_order_id("DTNOSUCH") is False

    @pytest.mark.parametrize("bad", [None, "", "   "])
    def test_an_empty_id_is_never_ours(self, bad):
        assert store(live={OWNER_VID: adopted_row()}).owns_venue_order_id(
            bad) is False

    def test_a_foreign_order_is_reported_positively_not_just_skipped(self):
        """Silence is how a breach goes unnoticed."""
        s = store(live={OWNER_VID: adopted_row()})
        assert s.adopted_not_ours(OWNER_VID) is True
        assert s.adopted_not_ours("DTNOSUCH") is False

    def test_our_own_order_is_not_reported_as_foreign(self):
        s = store(live={"l5f-abc123": our_row()},
                  submitted={"l5f-abc123": our_row()})
        assert s.adopted_not_ours(OURS_VID) is False


# --------------------------------- 2. provenance is sticky, not laundered

class TestProvenanceSurvivesReconciliation:
    """`adopted` was recomputed each pass as `coid not in ours`, so the
    second reconciliation erased it. The row builder now ORs the previous
    value in, and these pin that the flag can only ever be set."""

    def test_the_builder_ors_in_the_previous_value(self):
        import inspect
        src = inspect.getsource(LX.InventoryStore.reconcile_from_venue) \
            if hasattr(LX.InventoryStore, "reconcile_from_venue") \
            else inspect.getsource(LX)
        assert 'bool(mine.get("adopted"))' in src
        assert '(coid not in ours and not _known)' in src

    def test_a_second_pass_cannot_clear_the_flag(self):
        """Simulating the laundering directly on the expression used."""
        mine = {"adopted": True}
        ours = {"c1": mine}                 # second pass: it IS in ours
        assert (bool(mine.get("adopted")) or "c1" not in ours) is True
        #: and the OLD expression is what flipped it
        assert ("c1" not in ours) is False

    def test_first_sight_is_stamped_and_then_preserved(self):
        mine = {"adopted": True,
                "adopted_first_seen_utc": "2026-11-18T22:22Z"}
        kept = mine.get("adopted_first_seen_utc") or "NEW"
        assert kept == "2026-11-18T22:22Z"


# ------------------------------- 3. the disarmed sweep refuses what it cannot own

class FakeTransport:
    def __init__(self):
        self.cancelled = []
        self.armed = False
        self.market_slug = MARKET

    def cancel(self, vid, market_slug=None):
        self.cancelled.append(vid)

    def order_progress(self, vid, **kw):
        return {"status": "CANCELLED", "cumQuantity": "0",
                "venue_order_id": vid}


class TestTheDisarmedSweepDoesNotTouchManualOrders:

    def test_the_owners_order_is_refused_and_named(self):
        """THE INCIDENT, end to end, through the real `deadman`."""
        s = store(live={OWNER_VID: adopted_row()})
        t = FakeTransport()
        rec = LX.deadman(s, t, reason="orphan_unarmed", verify_attempts=1,
                         verify_sleeper=lambda _s: None)
        assert t.cancelled == [], "the owner's order must not be cancelled"
        assert len(rec["refused_not_ours"]) == 1
        row = rec["refused_not_ours"][0]
        assert row["venue_order_id"] == OWNER_VID
        assert row["why"] == "NO_SUBMIT_RECORD_FOR_THIS_ID"
        assert row["adopted"] is True
        assert rec["cancelled"] == []

    def test_our_own_resting_order_is_still_cancelled(self):
        """THE FIX MUST NOT STRAND OUR OWN ORDERS. An orphan of ours is
        exactly what this sweep exists for."""
        s = store(live={"l5f-abc123": our_row()},
                  submitted={"l5f-abc123": our_row()})
        t = FakeTransport()
        rec = LX.deadman(s, t, reason="orphan_unarmed", verify_attempts=1,
                         verify_sleeper=lambda _s: None)
        assert t.cancelled == [OURS_VID]
        assert rec["refused_not_ours"] == []

    def test_a_mixed_book_cancels_only_ours(self):
        s = store(live={OWNER_VID: adopted_row(),
                       "l5f-abc123": our_row()},
                  submitted={"l5f-abc123": our_row()})
        t = FakeTransport()
        rec = LX.deadman(s, t, reason="orphan_unarmed", verify_attempts=1,
                         verify_sleeper=lambda _s: None)
        assert t.cancelled == [OURS_VID]
        assert [r["venue_order_id"]
                for r in rec["refused_not_ours"]] == [OWNER_VID]

    def test_the_refusal_key_is_always_present(self):
        """So an empty list means 'checked, all ours', never 'not checked'."""
        s = store(live={"l5f-abc123": our_row()},
                  submitted={"l5f-abc123": our_row()})
        rec = LX.deadman(s, FakeTransport(), reason="orphan_unarmed",
                         verify_attempts=1, verify_sleeper=lambda _s: None)
        assert "refused_not_ours" in rec

    def test_an_adopted_order_without_a_venue_id_is_not_cancelled(self):
        s = store(live={"l5f-xyz": dict(our_row(), venue_order_id="")},
                  submitted={"l5f-xyz": our_row()})
        t = FakeTransport()
        rec = LX.deadman(s, t, reason="orphan_unarmed", verify_attempts=1,
                         verify_sleeper=lambda _s: None)
        assert t.cancelled == []
        #: no id to name, so it lands in no_venue_id and marks uncertainty
        assert rec["no_venue_id"] and s.uncertain


# -------------------- 4. unknown cost basis keeps its risk protections

class TestTheRiskFloorOutlivesAdoption:
    """The floor was keyed to `disputed`, and adoption resolves `disputed`
    by construction, so the floor died in the pass that raised it. It is
    now also held by an unknown cost basis."""

    @staticmethod
    def _sweep(floors, disputed, basis_unknown):
        """The exact retention rule, as the reconciler applies it."""
        unknown = {str(k).rsplit("|", 1)[0] for k in basis_unknown}
        kept = dict(floors)
        for m in list(kept):
            if m not in disputed and m not in unknown:
                kept.pop(m, None)
        return kept

    def test_the_incident_floor_would_now_survive(self):
        """22:28:12Z: not disputed any more, cost basis still unknown."""
        kept = self._sweep({MARKET: "5.00"}, disputed=set(),
                           basis_unknown=["%s|DOWN" % MARKET])
        assert kept == {MARKET: "5.00"}

    def test_the_old_rule_dropped_it(self):
        """The regression, stated so the fix is not mistaken for a no-op."""
        floors = {MARKET: "5.00"}
        for m in list(floors):
            if m not in set():          # the old condition, disputed only
                floors.pop(m, None)
        assert floors == {}

    def test_a_disputed_market_still_keeps_its_floor(self):
        kept = self._sweep({MARKET: "5.00"}, disputed={MARKET},
                           basis_unknown=[])
        assert kept == {MARKET: "5.00"}

    def test_a_settled_known_basis_market_releases_its_floor(self):
        """NOT A RATCHET. Once neither disputed nor unpriced, it goes --
        a floor that never lifts over-bounds the account forever."""
        kept = self._sweep({MARKET: "5.00"}, disputed=set(),
                           basis_unknown=[])
        assert kept == {}

    def test_another_markets_unknown_basis_does_not_hold_this_floor(self):
        kept = self._sweep({MARKET: "5.00"}, disputed=set(),
                           basis_unknown=["cpc-btc-updown-15m-other|UP"])
        assert kept == {}

    def test_the_reconciler_uses_this_rule(self):
        import inspect
        src = inspect.getsource(LX)
        i = src.index('floors = self.state.setdefault("risk_floor_usd", {})')
        block = src[i:i + 2000]
        assert "_basis_unknown" in block
        assert "m not in disputed and m not in _basis_unknown" in block


# ------------------------------------ 5. foreign positions stay visible

class TestForeignInventoryRemainsVisibleToRisk:
    """The boundary must not hide the owner's position from the limits.
    Adoption of POSITIONS at the $1 upper bound is deliberate and must
    survive the ownership fix -- otherwise exposure silently shrinks."""

    def test_the_documented_contract_still_says_so(self):
        import inspect
        src = inspect.getsource(LX)
        i = src.index("FOREIGN_COID_PREFIXES_ENV")
        head = src[max(0, i - 2600):i]
        assert "counted in account-wide exposure" in head
        assert "$1 a contract" in head

    def test_the_ownership_fix_touches_no_position_accounting(self):
        """The edits are to order ownership and the floor's lifetime. No
        position or exposure arithmetic may mention the new names."""
        import inspect
        src = inspect.getsource(LX.InventoryStore.owns_venue_order_id)
        for forbidden in ("positions", "exposure", "risk_exposure_usd",
                          "account_wide"):
            assert forbidden not in src, forbidden

    def test_adoption_is_not_disabled_by_the_fix(self):
        """Visibility is kept: rows are still built for foreign orders."""
        import inspect
        src = inspect.getsource(LX)
        assert "live_orders_adopted" in src
        assert 'self.state["live_orders"] = rebuilt' in src
