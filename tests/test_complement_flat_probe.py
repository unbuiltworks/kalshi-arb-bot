"""The gate that stopped R5F trading for 55 minutes, and the narrow re-arm
the owner authorised for it (#167 comment 5586502825, Option A).

The defect was not the gate refusing -- it was the gate having no way back.
Re-proof was only ever built from a HELD market's residual, so a flat account
could never re-prove, and a flat account is exactly what the gate produces by
blocking every first leg. These tests pin the re-arm AND pin the things that
must not move with it: what counts as proof, and the probe never being an
L5 action."""
from __future__ import annotations

import itertools
import json
from decimal import Decimal as D

import pytest

from src import pmus_spring_l5_session as base


# --------------------------------------------------------------------------
# the price the probe uses

def test_the_probe_joins_the_bid_so_a_rejection_cannot_be_a_cross():
    plan = base.flat_probe_plan({"bid": D("0.39"), "ask": D("0.42")},
                                D("0.01"))
    assert plan["px"] == D("0.39")
    assert plan["side"] == "OUTCOME_SIDE_YES"
    assert plan["opp_ask"] == "0.42"


@pytest.mark.parametrize("bid,ask,why", [
    (D("0.42"), D("0.42"), "not_two_sided"),      # locked
    (D("0.43"), D("0.42"), "not_two_sided"),      # crossed
    (D("0"), D("0.42"), "not_two_sided"),
    (None, D("0.42"), "no_two_sided_book"),
    (D("0.42"), None, "no_two_sided_book"),
])
def test_no_non_crossing_join_means_no_evidence_to_be_had(bid, ask, why):
    """A locked or crossed book has no non-crossing join. Guessing a price
    there would produce a rejection that says nothing about acceptance --
    which is the mistake the retired 'no fee safe complement' rule made."""
    plan = base.flat_probe_plan({"bid": bid, "ask": ask}, D("0.01"))
    assert plan["px"] is None and why in plan["why"]


def test_an_off_tick_bid_is_declined_rather_than_rounded():
    plan = base.flat_probe_plan({"bid": D("0.395"), "ask": D("0.42")},
                                D("0.01"))
    assert plan["px"] is None and "off_tick" in plan["why"]


# --------------------------------------------------------------------------
# the probe itself

class _Micro:
    def __init__(self, states, minq="0.01", tick="0.01"):
        self.states = list(states)
        self.minq, self.tick = minq, tick
        self.placed = []

    def market_micro_spec(self, t, slug, entry):
        return {"minimum_trade_qty": D(self.minq), "tick": D(self.tick)}

    def place_limit_buy(self, t, slug, side, px, qty, stage,
                        post_only=False):
        self.placed.append({"slug": slug, "side": side, "px": px,
                            "qty": qty, "stage": stage,
                            "post_only": post_only})
        return "OID1"


class _Q1:
    POLL_S = 0.0

    def __init__(self, states):
        self.states = list(states)
        self.cancelled = []
        self.rows = []

    def outcome_bbo(self, t, slug, side):
        return {"bid": D("0.39"), "ask": D("0.42")}

    def _order_status(self, t, oid):
        return {"state": self.states.pop(0) if self.states else "OPEN"}

    def cancel_exact(self, t, oid):
        self.cancelled.append(oid)
        return True

    path = None

    def ledger_append(self, row):
        self.rows.append(row)
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8") as sink:
                sink.write(json.dumps(row, separators=(",", ":"),
                                      default=str) + "\n")


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "complement_proof_path",
                        lambda: tmp_path / "proof.json")
    # The probe now tests the per-market cap BEFORE it submits, and that test
    # reads the durable ledger. Point the state dir at tmp_path and let the
    # fake ledger write THROUGH to it, so the cap sees exactly the rows the
    # probe wrote rather than a stub that can never disagree with it.
    monkeypatch.setenv("PMUS_Q1_STATE_DIR", str(tmp_path))

    def _install(states, fill=None):
        m, q = _Micro(states), _Q1(states)
        q.path = tmp_path / "ledger.jsonl"
        monkeypatch.setattr(base.micro, "market_micro_spec",
                            m.market_micro_spec)
        monkeypatch.setattr(base.micro, "place_limit_buy", m.place_limit_buy)
        monkeypatch.setattr(base.micro, "authoritative_fill",
                            lambda o, q_: fill)
        monkeypatch.setattr(base.micro, "_terminal_positive_fill",
                            lambda t, o, q_: fill)
        monkeypatch.setattr(base.q1, "outcome_bbo", q.outcome_bbo)
        monkeypatch.setattr(base.q1, "_order_status", q._order_status)
        monkeypatch.setattr(base.q1, "cancel_exact", q.cancel_exact)
        monkeypatch.setattr(base.q1, "ledger_append", q.ledger_append)
        return m, q
    return _install


def test_only_the_venue_showing_it_resting_restores_the_proof(wired):
    m, q = wired(["PENDING_NEW", "OPEN"])
    assert base.complement_acceptance_proven() is False
    proof = base.prove_complement_when_flat(None, "aec-x", clock=lambda: 0.0,
                                            sleep=lambda _s: None,
                                            wall=lambda: 100.0)
    assert proof["proven"] is True
    assert proof["end_reason"].startswith("resting:")
    assert base.complement_acceptance_proven() is True
    # exactly one order, at the venue minimum, post-only
    assert len(m.placed) == 1
    assert m.placed[0]["qty"] == D("0.01")
    assert m.placed[0]["post_only"] is True
    assert m.placed[0]["px"] == D("0.39")


def test_a_rejection_leaves_the_gate_exactly_as_closed_as_it_was(wired,
                                                                monkeypatch):
    """The revocation that started the 55-minute outage was exactly this
    shape. A rejected probe must not accidentally re-open the gate."""
    m, q = wired([])

    def _boom(t, oid):
        raise base.RejectedNoFill("ORDER_REJECTED_ZERO_FILL:OID1")
    monkeypatch.setattr(base.q1, "_order_status", _boom)
    proof = base.prove_complement_when_flat(None, "aec-x", clock=lambda: 0.0,
                                            sleep=lambda _s: None,
                                            wall=lambda: 100.0)
    assert proof["proven"] is False
    assert proof["reason"].startswith("rejected:")
    assert base.complement_acceptance_proven() is False


def test_an_unreadable_book_does_not_consume_the_retry_interval(wired,
                                                                monkeypatch):
    """The retired rule burned the retry budget on 'no legal price at this
    book' and kept first legs closed for hours. A skip must not write
    attempted_epoch."""
    m, q = wired(["OPEN"])
    base.save_complement_proof({"proven": False, "attempted_epoch": 50.0})

    def _boom(*_a, **_k):
        raise RuntimeError("gateway 503")
    monkeypatch.setattr(base.q1, "outcome_bbo", _boom)
    out = base.prove_complement_when_flat(None, "aec-x", clock=lambda: 0.0,
                                          sleep=lambda _s: None,
                                          wall=lambda: 100.0)
    assert "book_or_spec_unreadable" in out["skipped"]
    assert base.load_complement_proof()["attempted_epoch"] == 50.0
    assert m.placed == []


def test_a_locked_book_writes_evidence_and_places_nothing(wired,
                                                          monkeypatch):
    m, q = wired(["OPEN"])
    monkeypatch.setattr(base.q1, "outcome_bbo",
                        lambda t, s, side: {"bid": D("0.42"),
                                            "ask": D("0.42")})
    out = base.prove_complement_when_flat(None, "aec-x", clock=lambda: 0.0,
                                          sleep=lambda _s: None,
                                          wall=lambda: 100.0)
    assert "no_non_crossing_join" in out["skipped"]
    assert m.placed == []


def test_a_probe_fill_is_recorded_with_its_provenance_and_is_not_an_l5_action(
        wired):
    """A venue-minimum fill is small but it is not nothing. It must reach the
    durable ledger so the settlement auditor and the venue-vs-ledger
    reconciliation both see it -- labelled as a probe, never as an L5
    relation."""
    m, q = wired(["OPEN"], fill={"qty": D("0.01"), "price": D("0.39")})
    proof = base.prove_complement_when_flat(None, "aec-x", clock=lambda: 0.0,
                                            sleep=lambda _s: None,
                                            wall=lambda: 100.0)
    assert proof["proven"] is True and proof["filled_qty"] == "0.01"
    # kind is `filled`, not the old bespoke `order_filled`: ledger_qty_total()
    # counts `filled` rows, so the old kind made an acceptance position
    # invisible to the venue-vs-ledger quantity check.
    rows = [r for r in q.rows if r.get("kind") == "filled"]
    assert len(rows) == 1
    assert rows[0]["l5_action"] is False
    assert rows[0]["relation"] is None
    assert rows[0]["stage"] == base.FLAT_PROBE_STAGE
    assert rows[0]["price"] == "0.39" and rows[0]["qty"] == "0.01"
    assert not [r for r in q.rows if r.get("kind") == "order_filled"]


def test_the_probe_never_touches_the_behavioural_fidelity_artifact():
    """The held-market probe records a behavioural fill because there it IS
    an L5 complete. The flat probe answers no L5 question, so recording one
    would put a decision the model never made into the fidelity evidence."""
    src = open(base.__file__, encoding="utf-8").read()
    body = src.split("def prove_complement_when_flat")[1].split(
        "\ndef revoke_complement_proof")[0]
    assert "record_behavioral_fill" not in body
    assert "inv.record" not in body
    assert "spring_proposal" not in body


def test_the_probe_is_bounded_to_the_venue_minimum_and_post_only():
    src = open(base.__file__, encoding="utf-8").read()
    body = src.split("def prove_complement_when_flat")[1].split(
        "\ndef revoke_complement_proof")[0]
    assert 'qty = D(str(spec["minimum_trade_qty"]))' in body
    assert "post_only=True" in body
    # no sizing, cap, ladder or notional decision is made here
    for forbidden in ("target_notional", "max_leg_notional", "ladder",
                      "choose_micro_quantity", "pair_cost_max"):
        assert forbidden not in body, forbidden


# --------------------------------------------------------------------------
# the campaign wiring

def test_the_campaign_probes_when_flat_and_starts_the_session_same_cycle():
    src = open("src/pmus_l5_continuous_session.py", encoding="utf-8").read()
    site = src.split("if not base.complement_acceptance_proven():")[1]
    assert "prove_complement_when_flat" in site
    assert "COMPLEMENT_PROOF_RETRY_S" in site      # same rate limit as held
    assert "l5_action=False" in site
    # the gate is re-checked after the probe, so a proof earned this cycle
    # starts the session now rather than after another SELECT_POLL_S
    assert src.count("if not base.complement_acceptance_proven():") == 2


def test_the_held_market_path_is_unchanged():
    """Option A adds a path; it does not alter the one that already worked."""
    src = open(base.__file__, encoding="utf-8").read()
    assert ("if resume and inv.residual != 0 and \\\n"
            "                not complement_acceptance_proven() and \\") in src


def test_what_counts_as_proof_is_identical_on_both_paths():
    src = open(base.__file__, encoding="utf-8").read()
    held = src.split("def _prove_complement_acceptance")[1][:6000]
    flat = src.split("def prove_complement_when_flat")[1].split(
        "\ndef revoke_complement_proof")[0]
    criterion = 'proven = bool(got > 0 or end_reason.startswith("resting"))'
    assert criterion in held and criterion in flat


def test_a_filled_probe_never_falls_through_into_an_open():
    """LIVE, 16:44:07Z 2026-09-08. The flat probe joined the bid at 0.6900
    on aec-setkameua-beldmy-dobvol and was LIFTED for 0.01 -- proof
    restored, and a real position created in the same breath. The campaign
    fell straight through into session_start on that same market, whose
    OPEN preflight requires a flat account, and got
    `PREFLIGHT_FAILED nonzero_positions:...=0.0100` one second later.

    A post-only order that joins the bid can always be lifted, so this is
    the ordinary case, not a freak one. The gate itself is untouched: what
    changes is that a probe which leaves inventory stands down for the
    cycle instead of opening a session that cannot pass preflight."""
    import inspect
    from src import pmus_l5_continuous_session as cs
    src = inspect.getsource(cs.run_campaign)
    i = src.index('kind="complement_flat_probe"') if \
        'kind="complement_flat_probe"' in src else src.index("complement_flat_probe")
    after = src[i:i + 2200]
    # the fill is detected, named, and stops this cycle
    assert 'D(str(probe.get("filled_qty") or 0)) > 0' in after
    assert "complement_probe_left_residual" in after
    assert 'status = "CAMPAIGN_HOLD:POSITION_HELD"' in after
    # and it is still not an L5 action
    assert "l5_action=False" in after


def test_the_probe_residual_is_never_promoted_into_an_l5_leg():
    """The owner's Option A says the probe is not an L5 trade. A filled
    probe must not become the opening leg of a sequence by the back door:
    nothing in the stand-down path records a behavioral fill, adds the
    slug to filled_markets, or touches inventory."""
    import inspect
    from src import pmus_l5_continuous_session as cs
    src = inspect.getsource(cs.run_campaign)
    i = src.index("complement_probe_left_residual")
    block = src[i - 900:i + 900]
    for forbidden in ("record_behavioral_fill", "filled_markets.add",
                      "inv.record", "spring_proposal", "session_runner"):
        assert forbidden not in block, forbidden
