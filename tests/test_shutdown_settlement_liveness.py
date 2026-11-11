"""The halted probe must keep DRAINING settlements, not stall on the first one.

The shutdown path walked EVERY pending market in one unbounded pass. That was
survivable while the set was ~1; when the accounting fix correctly returned ~38
markets to it, the first pass stopped completing and the loop reported nothing
for 23+ minutes. A settlement loop that never finishes a cycle never reports,
and one slow market blocks every market behind it.

Each test below is one of the properties that had to be proved before deploying.
"""
import json

import pytest

from src import pmus_l5_continuous_session as cont


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(cont.base.q1.base, "state_dir", lambda: tmp_path)
    return tmp_path


def _ledger(p, slugs):
    p.joinpath("ledger.jsonl").write_text("".join(
        json.dumps({"kind": "filled", "stage": "spring_open", "slug": s}) + "\n"
        for s in slugs))


# ------------------------------------------------- bounded, incremental drain

def test_a_large_set_is_processed_incrementally_not_all_at_once():
    order = [f"m{i}" for i in range(38)]
    seen, cursor = [], 0
    for _ in range(4):
        batch, cursor = cont.settle_batch(order, cursor)
        assert len(batch) == cont.SETTLE_AUDIT_BATCH
        seen += batch
    assert len(seen) == 4 * cont.SETTLE_AUDIT_BATCH
    assert len(set(seen)) == len(seen), "a market was audited twice in 4 cycles"


def test_every_cycle_has_bounded_work():
    order = [f"m{i}" for i in range(500)]
    batch, _ = cont.settle_batch(order, 0)
    assert len(batch) == cont.SETTLE_AUDIT_BATCH


def test_the_whole_set_is_eventually_covered():
    """Round-robin must reach the tail, not re-audit the head forever."""
    order = [f"m{i}" for i in range(38)]
    seen, cursor = set(), 0
    for _ in range(20):
        batch, cursor = cont.settle_batch(order, cursor)
        seen |= set(batch)
    assert seen == set(order)


def test_a_short_set_is_never_double_audited_in_one_pass():
    """Concatenating and slicing audited the SAME market twice whenever fewer
    markets were pending than the batch. Preserved-as-fixed."""
    for n in range(1, cont.SETTLE_AUDIT_BATCH + 1):
        order = [f"m{i}" for i in range(n)]
        batch, _ = cont.settle_batch(order, 0)
        assert len(batch) == n and len(set(batch)) == n


def test_empty_set_is_safe():
    assert cont.settle_batch([], 7) == ([], 7)


# ------------------------------------------------------- durable across restart

def test_progress_is_durable_across_restart(state):
    """In memory the cursor resets to 0 every restart, so a process restarting
    faster than it can drain re-audits the head forever and never reaches the
    tail."""
    cont.save_settle_cursor(17)
    assert cont.load_settle_cursor() == 17


def test_a_missing_or_corrupt_cursor_reads_as_zero_not_a_crash(state):
    assert cont.load_settle_cursor() == 0
    state.joinpath(cont.SETTLE_CURSOR_FILE).write_text("{not json")
    assert cont.load_settle_cursor() == 0


def test_saving_the_cursor_never_raises(monkeypatch):
    """Failing to persist progress must not stop the drain."""
    monkeypatch.setattr(cont.base.q1.base, "state_dir",
                        lambda: (_ for _ in ()).throw(OSError("no dir")))
    cont.save_settle_cursor(3)          # must not raise


# --------------------------------------------- one bad market cannot block all

def test_one_stuck_market_cannot_block_the_others(state, monkeypatch):
    _ledger(state, [f"m{i}" for i in range(38)])
    monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: True)

    calls = []

    def fake_settle(t, slug, **kw):
        calls.append(slug)
        if slug == "m0":
            raise TimeoutError("venue hung on this one")
        return {"status": "PENDING_SETTLEMENT", "settled": False}

    monkeypatch.setattr(cont, "settle_held", fake_settle)
    out = cont.shutdown_settle_pass(t=object())
    assert len(calls) == cont.SETTLE_AUDIT_BATCH, "the cycle stayed bounded"
    assert any("m0" in e for e in out["errors"]), "the failure is visible"
    assert len(calls) > 1, "the stuck market did not take the cycle down"


def test_a_failed_audit_never_removes_a_market(state, monkeypatch):
    """A failed or timed-out audit is NOT evidence that a market settled."""
    _ledger(state, ["m0", "m1"])
    monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: True)
    monkeypatch.setattr(cont, "settle_held",
                        lambda t, s, **k: (_ for _ in ()).throw(OSError("boom")))
    cont.shutdown_settle_pass(t=object())
    assert set(cont.unsettled_slugs()) == {"m0", "m1"}


def test_per_market_timing_and_error_are_visible(state, monkeypatch):
    _ledger(state, ["slow", "ok"])
    monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: True)

    def fake(t, slug, **kw):
        if slug == "slow":
            raise TimeoutError("timed out")
        return {"status": "PENDING_SETTLEMENT", "settled": False}

    monkeypatch.setattr(cont, "settle_held", fake)
    out = cont.shutdown_settle_pass(t=object())
    rows = {r["slug"]: r for r in out["still_pending"]}
    assert rows["slow"]["status"] == "AUDIT_RAISED"
    assert "TimeoutError" in rows["slow"]["error"]
    assert "elapsed_s" in rows["ok"] and "elapsed_s" in rows["slow"]


# ------------------------------------------------- accounting must not weaken

def test_unresolved_stays_unresolved_without_venue_confirmation(state,
                                                                monkeypatch):
    """Settled-but-unconfirmed must not close a market. This is the invariant
    the whole cutover exists to protect and the batching must not erode it."""
    _ledger(state, ["m0"])
    state.joinpath("ledger.jsonl").open("a").write(
        json.dumps({"kind": "settled", "slug": "m0"}) + "\n")
    assert cont.unsettled_slugs() == ["m0"]


def test_the_budget_prices_the_whole_set_not_the_batch(state, monkeypatch):
    """Pricing only the audited five would understate every open obligation --
    the same class of error as the bug being fixed."""
    _ledger(state, [f"m{i}" for i in range(38)])
    monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: True)
    monkeypatch.setattr(cont, "settle_held",
                        lambda t, s, **k: {"status": "PENDING_SETTLEMENT",
                                           "settled": False})
    out = cont.shutdown_settle_pass(t=object())
    assert out["pending_total"] == 38
    assert out["pending_after"] == 38
    assert len(out["batch"]) == cont.SETTLE_AUDIT_BATCH


# ------------------------------------------------------ shutdown stays closed

def test_shutdown_mode_still_cannot_submit_an_order():
    """Structural, over the source: the shutdown settlement path must contain
    no order verb. Batching must not have opened one."""
    import inspect
    src = inspect.getsource(cont.shutdown_settle_pass)
    for verb in ("place_order", "submit", "post_order", "cancel_order",
                 "create_order"):
        assert verb not in src, verb


def test_held_inventory_is_preserved_by_the_shutdown_report():
    import inspect
    src = inspect.getsource(cont.safe_shutdown)
    assert "held_preserved" in src


def test_the_idle_loop_emits_progress_every_cycle():
    """A cycle that audits nothing and a cycle that is stuck must not look the
    same -- which is exactly why the stall went unnoticed for 23 minutes."""
    import inspect
    src = inspect.getsource(cont.shutdown_idle)
    for field in ("pending_total", "pending_after", "audited", "cursor",
                  "settled_now", "settle_errors"):
        assert field in src, field


def test_both_paths_share_one_batching_implementation():
    """Two settlement implementations would drift; the campaign loop's bounded
    mechanism is reused rather than duplicated."""
    import inspect
    src = inspect.getsource(cont)
    assert src.count("def settle_batch(") == 1
    assert src.count("settle_batch(") >= 3, "both callers must use it"
