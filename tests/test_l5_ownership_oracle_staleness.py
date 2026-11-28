"""Ownership proof must be FRESH, not merely readable.

THE GAP THIS CLOSES. `OwnershipOracle.claimed()` accepted any document whose
`readable` was true, at any age. Two failure modes are not the same:

  * the executor CRASHES -- its registry server dies with the process, the
    read fails, and ownership was already unknown. Safe.
  * the executor WEDGES -- process alive, writer stalled, registry still
    serving its last snapshot. That snapshot was believed indefinitely, so a
    claim of arbitrary age counted as proof that an id is ours.

The registry publishes `as_of_utc` for exactly this reason, and
`RegistryOrderSource` already bounds it at DEFAULT_REPORT_MAX_AGE_S before
latching EXECUTOR_REPORT_STALE. The ownership path now uses the same bound.

The direction of every refusal below is the same: no proof, no cancel. An
uncancelled order with the executor halted is recoverable by hand; somebody
else's cancelled order is not.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import l5_halt_gate as HG
from src import l5_order_registry_server as REG
from src import l5_watchdog as W

OURS = "D4EFZNW00ZZ0"          # the order from the 2026-10-16 incident
MARKET = "cpcxrp-xrp-updown-15m-2026-10-16-0330z"


def doc(*, readable=True, as_of=None, rows=None):
    d = {"readable": readable,
         "orders": rows if rows is not None
         else [{"venue_order_id": OURS, "market": MARKET}]}
    if as_of is not None:
        d["as_of_utc"] = as_of
    return json.dumps(d).encode()


def oracle(served, *, now=None, **kw):
    return W.OwnershipOracle("http://x",
                             opener=lambda u, t: served,
                             now_fn=(lambda: now) if now else None, **kw)


NOW = dt.datetime(2026, 10, 18, 15, 0, 0, tzinfo=dt.timezone.utc)


def iso(offset_s):
    return (NOW - dt.timedelta(seconds=offset_s)).isoformat()


# ----------------------------------------------------- the fresh, valid case

def test_a_fresh_claim_is_ownership_proof():
    o = oracle(doc(as_of=iso(5.0)), now=NOW)
    got = o.claimed()
    assert set(got) == {OURS}
    assert got[OURS]["market"] == MARKET
    assert o.last_refusal is None


def test_a_claim_at_the_boundary_is_still_accepted():
    o = oracle(doc(as_of=iso(W.OwnershipOracle.MAX_CLAIM_AGE_S - 0.5)), now=NOW)
    assert set(o.claimed()) == {OURS}


# ------------------------------------------------------------ the wedged case

def test_a_stale_claim_is_not_ownership_proof():
    """The wedged executor: still serving, long since stopped writing."""
    o = oracle(doc(as_of=iso(600.0)), now=NOW)
    assert o.claimed() == {}
    assert o.last_refusal.startswith("CLAIM_STALE:"), o.last_refusal


def test_the_bound_matches_the_order_source_bound():
    """One executor, one staleness standard, both paths."""
    assert W.OwnershipOracle.MAX_CLAIM_AGE_S == W.DEFAULT_REPORT_MAX_AGE_S


@pytest.mark.parametrize("as_of,why", [
    (None, "CLAIM_AGE_UNREADABLE"),                   # field absent
    ("", "CLAIM_AGE_UNREADABLE"),                     # field empty
    ("not-a-timestamp", "CLAIM_AGE_UNREADABLE"),      # unparseable
    ("2026-10-18T15:00:00", "CLAIM_AGE_UNREADABLE"),  # naive, no tz
])
def test_an_unreadable_claim_age_is_not_ownership_proof(as_of, why):
    o = oracle(doc(as_of=as_of), now=NOW)
    assert o.claimed() == {}
    assert o.last_refusal == why


def test_a_claim_stamped_in_the_future_is_refused():
    o = oracle(doc(as_of=iso(-3600.0)), now=NOW)
    assert o.claimed() == {}
    assert o.last_refusal.startswith("CLAIM_IN_THE_FUTURE:")


def test_small_clock_skew_is_tolerated_not_refused():
    """The sweep routinely reports report_age_s of -0.011."""
    o = oracle(doc(as_of=iso(-1.0)), now=NOW)
    assert set(o.claimed()) == {OURS}


# ------------------------------------------------------------- the crash case

def test_a_crashed_executor_yields_no_ownership():
    """The registry dies with the process, so the read fails."""
    def boom(url, timeout):
        raise OSError("connection refused")
    o = W.OwnershipOracle("http://x", opener=boom)
    assert o.claimed() == {}
    assert o.last_refusal.startswith("REGISTRY_UNREACHABLE:"), o.last_refusal


def test_an_unreadable_ledger_yields_no_ownership():
    o = oracle(doc(readable=False, as_of=iso(1.0)), now=NOW)
    assert o.claimed() == {}
    assert o.last_refusal == "REGISTRY_NOT_READABLE"


@pytest.mark.parametrize("body", [b"not json", b"[]", b"", b"null"])
def test_a_malformed_body_yields_no_ownership(body):
    o = W.OwnershipOracle("http://x", opener=lambda u, t: body)
    assert o.claimed() == {}
    assert o.last_refusal is not None


# -------------------------------------------- the real registry stays valid

def test_the_real_registry_document_passes_its_own_oracle(tmp_path):
    """Whatever the bound is, the live registry must satisfy it."""
    from src import l5_live_execution as EX
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    st.record_submit("c1", {"market": MARKET, "side": "UP",
                            "qty": "1", "price": "0.6"})
    st.attach_venue_id("c1", OURS)
    served = json.dumps(REG.registry(st)).encode()
    o = W.OwnershipOracle("http://x", opener=lambda u, t: served)
    assert set(o.claimed()) == {OURS}, o.last_refusal


# ------------------------------------------- the sweep reports WHY, not just {}

def test_the_sweep_names_the_reason_it_cancelled_nothing(tmp_path):
    """An empty claim set and an unreachable registry are opposite facts."""
    class Venue:
        def request(self, *a, **kw):
            raise AssertionError("no venue call should happen")

        def open_orders(self):
            return [{"id": OURS, "marketSlug": MARKET,
                     "leavesQuantity": "1", "createdTime": 0}]

        def order(self, oid):
            raise AssertionError("ownership was never established")

    def boom(url, timeout):
        raise OSError("connection refused")

    store = HG.FileStore(str(tmp_path))
    wd = W.Watchdog(Venue(), HG.HaltAsserter(store, who="test"),
                    max_order_age_s=0.0, cancel_enabled=True,
                    oracle=W.OwnershipOracle("http://x", opener=boom))
    out = wd.sweep()
    assert out["overdue"], "the order is overdue and must still latch"
    assert out.get("cancelled") in (None, []), "nothing may be cancelled"
    assert out["claim_refused"].startswith("REGISTRY_UNREACHABLE:")
