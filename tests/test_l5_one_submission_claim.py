"""ONE SUBMISSION PER AUTHORIZATION, enforced by the filesystem.

WHAT WAS NOT COVERED BEFORE. `max_live_orders` is checked as
`live_order_count >= max_live_orders`, which bounds CONCURRENCY: a
cancellation returns the count to zero and the check passes again. The cash
gate hands back the unfilled headroom after a partial fill. The ARMING claim
resets per window, so a restart can re-arm the same window. So "one order"
was a property of the written specification, not of the engine.

These tests pin it as a property of a durable lock. They drive the REAL
`claim_submission` against a REAL temporary directory -- never a stubbed
claim, because a stubbed claim is exactly what would hide this.
"""
from __future__ import annotations

import os

import pytest

from src import l5_live_authorization as AUTH


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "EXP-QUEUE-1")
    return str(tmp_path / "auth.jsonl")


MKT = "cpc-btc-updown-15m-2026-10-15-1800z"


def _take(ledger, market=MKT, **payload):
    return AUTH.claim_submission(market, payload or {"q": "1"}, path=ledger)


# ------------------------------------------------- the claim itself

def test_the_first_submission_takes_the_claim(ledger):
    rec = _take(ledger)
    assert rec["sid"] == "sub1.EXP-QUEUE-1"      # experiment-scoped
    assert rec["experiment"] == "EXP-QUEUE-1"
    assert rec["market"] == MKT


def test_a_second_submission_under_the_same_authorization_is_refused(ledger):
    _take(ledger)
    with pytest.raises(AUTH.Refused) as e:
        _take(ledger)
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"


# ------------------------------------------------- the seven scenarios

def test_a_cancellation_does_not_release_the_claim(ledger):
    """The case the old controls missed: cancel, count back to 0, retry."""
    _take(ledger)
    # a cancellation is whatever happens elsewhere; nothing here resets.
    with pytest.raises(AUTH.Refused):
        _take(ledger)


def test_a_partial_fill_does_not_release_the_claim(ledger):
    """Cash headroom returns after a partial fill; the claim does not."""
    _take(ledger)
    with pytest.raises(AUTH.Refused):
        _take(ledger, note="0.50 of 1.93 filled, $0.76 headroom restored")


def test_a_zero_fill_does_not_release_the_claim(ledger):
    _take(ledger)
    with pytest.raises(AUTH.Refused):
        _take(ledger, note="cancelled unfilled, full headroom restored")


def test_a_restart_does_not_release_the_claim(ledger):
    """A fresh process sees the lock on the durable volume and refuses.

    Simulated the only way that matters: nothing in memory is carried over,
    the module is re-imported, and the same path is used.
    """
    _take(ledger)
    import importlib
    fresh = importlib.reload(AUTH)
    try:
        with pytest.raises(fresh.Refused):
            fresh.claim_submission(MKT, {"after": "restart"}, path=ledger)
    finally:
        importlib.reload(fresh)


def test_a_rejected_submission_still_spends_the_claim(ledger):
    """Deliberate: a venue rejection is NOT a free retry.

    Retrying into a venue that may already hold the order is the failure this
    refuses to risk, so a rejected attempt consumes the authorization.
    """
    _take(ledger, note="venue answered 400")
    with pytest.raises(AUTH.Refused):
        _take(ledger, note="retry after rejection")


def test_an_ambiguous_acknowledgement_still_spends_the_claim(ledger):
    """An ack we could not read is the most dangerous case, not the safest."""
    _take(ledger, note="timeout, ack unknown, order may be live")
    with pytest.raises(AUTH.Refused):
        _take(ledger, note="retry after ambiguous ack")


def test_claim_persistence_failure_fails_closed(tmp_path, monkeypatch):
    """A claim we cannot persist is not a claim, so the submission refuses."""
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "EXP-QUEUE-1")
    # A FILE where the claim's directory must be. This fails for root too,
    # which a mode-500 directory does not -- and these suites run as root.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_submission(MKT, {}, path=str(blocker / "sub" / "a.jsonl"))
    assert e.value.reason == "SUBMISSION_CLAIM_UNAVAILABLE"
    assert "NotADirectoryError" in str(e.value.detail["error"])


# ------------------------------------------------- binding and scope

def test_an_unnamed_experiment_is_refused_never_defaulted(ledger, monkeypatch):
    monkeypatch.delenv(AUTH.EXPERIMENT_ENV, raising=False)
    with pytest.raises(AUTH.Refused) as e:
        _take(ledger)
    assert e.value.reason == "SUBMISSION_CLAIM_NO_EXPERIMENT"


def test_an_empty_market_is_refused(ledger):
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_submission("   ", {}, path=ledger)
    assert e.value.reason == "SUBMISSION_CLAIM_NO_MARKET"


def test_a_new_authorization_gets_its_own_claim(ledger, monkeypatch):
    _take(ledger)
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "EXP-QUEUE-2")
    assert _take(ledger)["experiment"] == "EXP-QUEUE-2"


def test_one_experiment_cannot_submit_in_a_SECOND_market(ledger):
    """THE DEFECT THIS PINS. Keyed on experiment AND market, one $1
    authorization got one submission *per market* -- it could have placed an
    order in every 15-minute window of the day, each on its own fresh claim.
    A single-market authorization must be single across the whole experiment.
    """
    first = _take(ledger)
    assert first["market"] == MKT
    other = "cpc-btc-updown-15m-2026-10-15-1815z"
    with pytest.raises(AUTH.Refused) as e:
        _take(ledger, market=other)
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"


def test_the_market_is_recorded_but_does_not_widen_the_authorization(ledger):
    """The audit record still says which market spent the claim."""
    rec = _take(ledger)
    assert rec["market"] == MKT
    assert rec["sid"] == "sub1.EXP-QUEUE-1"      # no market in the id
    assert MKT not in rec["sid"]


def test_the_submission_claim_is_a_different_namespace_from_arming(ledger):
    """The arming claim is keyed on the session id; this one must not
    collide with it, or arming would consume the submission claim."""
    sid = AUTH.submission_sid("EXP-QUEUE-1")
    assert sid.startswith("sub1.")
    AUTH.claim_once(ledger, "EXP-QUEUE-1", {"kind": "arming"})
    assert _take(ledger)["sid"] == sid        # arming did not consume it


# ------------------------------------------------- the wire ordering

def test_the_claim_is_persisted_before_it_returns(ledger):
    """Durable BEFORE the submit, so a death between the two spends it."""
    rec = _take(ledger)
    lock = AUTH._lock_path(ledger, rec["sid"])
    assert lock.exists() and lock.read_text().strip()


def test_the_executor_takes_the_claim_only_when_armed():
    """A shadow-locked or disarmed transport cannot reach the venue, so the
    claim must not be burned there -- in shadow it would be spent on the
    first decision of the day."""
    import inspect
    from src import l5_live_execution as LX
    src = inspect.getsource(LX)
    i = src.index("THE ONE-SUBMISSION CLAIM")
    tail = src[i:]
    assert 'getattr(self.transport, "armed", False)' in tail[:2000]
    # and it precedes both the reservation and the wire
    assert tail.index("claim_submission") < tail.index("record_submit")
    assert tail.index("record_submit") < tail.index("self.transport.submit")


# ------------------------------------------- through the real executor

def test_a_restart_cannot_resubmit_through_the_real_executor(tmp_path,
                                                             monkeypatch):
    """END TO END, under ONE authorization, with a real FaithfulExecutor.

    This is the case `test_a_crash_between_submit_and_save_still_cannot_double`
    covers from the other side: there, each attempt stands for a separate
    authorization and the VENUE rejects the duplicate. Here the authorization
    is the SAME, and the claim stops the second attempt BEFORE the wire -- so
    the venue is never asked at all, which is the stronger guarantee and the
    one the measurement experiment relies on.
    """
    from tests import test_l5_live_execution as H

    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "EXP-ONE-AUTH")
    from src import l5_live_execution as E

    t = H.FakeTransport(fill_all=True)
    first = E.FaithfulExecutor(H.store(tmp_path), H.limits(), transport=t,
                              now_fn=H.NOW)
    first.handle(H.row(), {}, market="m", window="16:15")
    assert len(t.sent) == 1

    # a crash loses the store; a fresh process re-arms the SAME experiment
    lost = E.InventoryStore(str(tmp_path / "lost.json"))
    fresh = E.FaithfulExecutor(lost, H.limits(), transport=t, now_fn=H.NOW)
    rec = fresh.handle(H.row(), {}, market="m", window="16:15")

    # the refusal names the CAUSE, not just the gate: the claim for this
    # experiment and market was already spent.
    assert rec["refused_reason"] == "AUTHORIZATION_ALREADY_USED"
    assert rec["ORDER"]["reason"] == "SUBMISSION_CLAIM"
    assert rec["ORDER"]["detail"] == "AUTHORIZATION_ALREADY_USED"
    assert "NEVER reset automatically" in rec["ORDER"]["labels"]
    assert len(t.sent) == 1          # the venue was never asked a second time


# ------------------------------------------- no bypass around the claim

#: EVERY route to `transport.submit`, and each one's claim call. A new entry
#: here is a deliberate act: adding a submission path means gating it, and
#: this list is what makes "gated" checkable rather than asserted in prose.
#:
#: The second entry arrived with the bounded cancellation-mechanism leg, which
#: submits ONE predetermined order without going through the frozen decision
#: path at all (see l5_pmus_transport._require_within_order_cap, which
#: anticipates exactly that caller). It is a real second route, so it carries
#: a real claim.
#:
#: The third entry is the BTC 5-minute adapter. It was kept OUT of src/ for
#: as long as it had no claim (this list is what refused it). By owner
#: decision of 2026-11-11 the existing mechanism was extended to explicitly
#: approved 5-minute markets, and the adapter now spends
#: `claim_btc5m_submission` -- built from the same approval gate, window
#: ledger, budgets and O_EXCL lock -- immediately before its one submit.
SUBMIT_SITES = {
    "l5_live_execution.py": "claim_submission",
    "l5_cancel_mechanism.py": "claim_submission",
    "l5_btc5m_execution.py": "claim_btc5m_submission",
}


def _submit_sites():
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1]
    out = {}
    for py in sorted((root / "src").rglob("*.py")):
        text = py.read_text()
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(r"\btransport\.submit\(", line):
                out.setdefault(py.name, []).append(n)
    return out


def test_every_reachable_submission_path_goes_through_the_claim():
    """Not "one call site" -- EVERY call site, and each one claim-gated.

    This used to assert a single route. That was the right invariant stated
    the wrong way: what protects the account is that no path reaches the wire
    without spending a single-use claim, not that there happens to be one
    path. A second route appeared (the cancellation-mechanism leg), so the
    test now enumerates the routes and requires the claim on each, which is
    strictly stronger than the count it replaces -- a new ungated path still
    fails, and so does an existing path that loses its claim.

    Line numbers are deliberately NOT asserted: pinning them makes this fail
    whenever a submit path is edited, which is noise rather than a finding.
    """
    sites = _submit_sites()
    assert set(sites) == set(SUBMIT_SITES), sites
    for name in sites:
        assert len(sites[name]) == 1, (name, sites[name])


def test_each_submission_path_calls_the_claim_in_its_own_module():
    """The claim must be in the SAME module as the submit it guards.

    A claim taken somewhere far away, by a different caller, is not a gate on
    this path: it is an assumption about call order.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for name, fn in SUBMIT_SITES.items():
        text = (root / "src" / name).read_text()
        assert fn in text, (name, fn)


def test_the_claim_precedes_the_wire_in_every_path():
    """Ordering, not just presence: a claim spent AFTER the POST would let a
    process that died in between submit twice."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1]
    for name, fn in SUBMIT_SITES.items():
        lines = (root / "src" / name).read_text().splitlines()
        claims = [n for n, l in enumerate(lines, 1) if fn + "(" in l
                  and not l.strip().startswith(("#", "*", "def "))]
        submits = [n for n, l in enumerate(lines, 1)
                   if re.search(r"\btransport\.submit\(", l)]
        assert claims, name
        assert submits, name
        assert min(claims) < submits[0], (name, claims, submits)


def test_no_image_module_the_runner_uses_posts_an_order_directly():
    """A DIRECT `POST /v1/orders` would skip `transport.submit` and therefore
    the claim. One module in the executor image has such a call --
    `pmus_q1_executor.place_limit_buy` -- and the runner imports that module.
    This pins the fact that the runner uses only its credential helpers, so
    the direct-POST function is unreachable in the deployed configuration. If
    anyone wires it up, this fails.
    """
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1]
    dockerfile = (root / "deploy" / "l5_pmus_live" / "Dockerfile").read_text()
    in_image = set(re.findall(r"^COPY (src/[\w./]+\.py)", dockerfile,
                              re.M))
    posters = set()
    for rel in sorted(in_image):
        p = root / rel
        if not p.exists():
            continue
        txt = p.read_text()
        for n, line in enumerate(txt.splitlines(), 1):
            if re.search(r'request\(\s*"POST"\s*,\s*"/v1/orders"', line):
                posters.add(rel)
    # the transport's own submit body is the gated one; everything else must
    # be unreachable from the runner.
    assert posters <= {"src/l5_pmus_transport.py",
                       "src/pmus_q1_executor.py"}, posters
    runner = (root / "src" / "l5_pmus_live_runner.py").read_text()
    used = set(re.findall(r"Q1\.([A-Za-z_]+)", runner))
    assert used == {"armed", "has_credentials"}, used
    assert "place_limit_buy" not in runner
    assert "run_campaign" not in runner
