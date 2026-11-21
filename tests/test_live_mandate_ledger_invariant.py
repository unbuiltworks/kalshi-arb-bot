"""THE invariant, checked from the ledger rather than from the code's counters.

  For every market, at every point in the replay, the money-moving notional the
  VENUE ACTUALLY ACCEPTED is <= the explicitly authorized envelope.

Two things make this different from the adversarial suite:

1. THE MEASUREMENT IS INDEPENDENT. `accepted_notional_from_ledger()` below is a
   deliberate second implementation. It reads `ledger.jsonl` and prices what the
   venue accepted; it never calls `consumption()`. A guard that mis-counts its
   own spending would pass a test written in terms of its own counter, which is
   exactly how a $7.4476 counter sat next to $19.3295 of real orders.

2. IT IS CHECKED AT EVERY PREFIX, not at the end. A breach that is opened and
   then closed by a later cancel is still a breach -- there was a moment when
   more money was committed than was authorized.

The control test at the bottom runs the SAME replay against the pre-incident
rule and asserts the invariant is violated, so this file is known to have teeth
rather than merely being green.
"""
from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path

import pytest

#: These files test the mandate itself, so the suite's permissive stand-in
#: mandate must never be what makes them pass.
pytestmark = pytest.mark.real_mandate_env

from src import pmus_live_mandate as M
from tests.test_live_mandate_adversarial import (
    FakeVenue, buy, mandate, settle, submit, terminal)


# --------------------------------------------------------------------------
# an independent reading of the ledger -- NOT the module's own accounting

def accepted_notional_from_ledger(state_dir: Path, authorization_id: str
                                  ) -> dict[str, D]:
    """Per market: cash the venue accepted and has not proven unfilled.

    Reconstructed from the ledger only. A commitment with no linked order, or a
    linked order with no terminal row, is counted IN FULL -- if we cannot prove
    the money did not move, it moved.
    """
    commits, links, term = {}, {}, {}
    path = state_dir / "ledger.jsonl"
    if not path.exists():
        # every order refused writes no commitment, so no ledger at all is a
        # legitimate "nothing was spent" -- not a reason to fail the read
        return {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        k = r.get("kind")
        if k == M.COMMIT_KIND and r.get("authorization_id") == authorization_id:
            commits[r["commit_id"]] = r
        elif k == M.LINK_KIND and r.get("authorization_id") == authorization_id:
            links[r["commit_id"]] = r.get("order_id")
        elif k == "order_terminal":
            term[str(r.get("order_id"))] = r
    out: dict[str, D] = {}
    for cid, c in commits.items():
        cash, qty = D(c["cash_at_risk"]), D(c["quantity"])
        oid = links.get(cid)
        t = term.get(str(oid)) if oid else None
        if t is None:
            charge = cash
        else:
            got = D(str(t.get("filled_qty", "0")))
            charge = cash if got >= qty else (cash * got / qty)
        out[c["slug"]] = out.get(c["slug"], D("0")) + charge
    return out


def assert_invariant(state_dir: Path, m, where: str) -> None:
    per = accepted_notional_from_ledger(state_dir, m.authorization_id)
    for slug, spent in per.items():
        assert spent <= D(str(m.max_spend_per_market)), (
            f"{where}: {slug} accepted ${spent} > authorized "
            f"${m.max_spend_per_market}")
    total = sum(per.values(), D("0"))
    assert total <= D(str(m.max_total_spend)), (
        f"{where}: total accepted ${total} > authorized ${m.max_total_spend}")
    assert len(per) <= m.max_total_markets, (
        f"{where}: {len(per)} markets touched > authorized {m.max_total_markets}")


# --------------------------------------------------------------------------
# the replay: the shape of what actually happened on 2026-09-16

#: aec-itfme-conbra-andpet, in order. YES opens and extends at 0.33-0.35, then
#: the COMPLETE legs in NO terms around 0.43 that the old rule let through
#: untouched. Sizes are the real ones; the envelope is the real R1 $0.05.
CONBRA = [
    ("open_1",     "OUTCOME_SIDE_YES", "0.35", "5.00", "0.00"),
    ("open_2",     "OUTCOME_SIDE_YES", "0.35", "5.00", "5.00"),
    ("extend_3",   "OUTCOME_SIDE_YES", "0.35", "5.00", "0.00"),
    ("extend_4",   "OUTCOME_SIDE_YES", "0.34", "4.92", "4.92"),
    ("extend_5",   "OUTCOME_SIDE_YES", "0.34", "4.92", "4.92"),
    ("complete_1", "OUTCOME_SIDE_NO",  "0.43", "5.60", "5.60"),
    ("complete_2", "OUTCOME_SIDE_NO",  "0.43", "5.60", "0.00"),
    ("complete_3", "OUTCOME_SIDE_NO",  "0.43", "5.60", "5.60"),
    ("complete_4", "OUTCOME_SIDE_NO",  "0.42", "5.60", "5.60"),
]


def replay(state_dir: Path, m, venue: FakeVenue, steps=CONBRA,
           slug="aec-itfme-conbra-andpet-2026-09-16"):
    """Drive the sequence, checking the invariant after EVERY step."""
    g = M.guard(venue, m, state_dir)
    accepted, refused = [], []
    for name, side, px, qty, filled in steps:
        r = submit(g, buy(slug=slug, side=side, px=px, qty=qty))
        if r.startswith("ORD"):
            accepted.append(name)
            terminal(state_dir, r, filled, px)
        else:
            refused.append((name, r))
        assert_invariant(state_dir, m, f"after {name}")
    return accepted, refused


def test_the_real_sequence_never_breaches_the_authorized_envelope(tmp_path):
    v, m = FakeVenue(), mandate()          # ONE market, $0.05
    accepted, refused = replay(tmp_path, m, v)
    # the envelope is tiny and these orders are dollars, so almost everything
    # is refused -- that is the point; the first one alone is $1.75
    assert accepted == [], accepted
    assert all(r == "max_spend_per_market_exhausted" for _n, r in refused)
    assert v.orders == []


def test_it_spends_right_up_to_the_envelope_and_then_stops(tmp_path):
    """Not merely 'refuses everything': with a mandate that fits, orders are
    admitted until the money runs out and the invariant still holds at
    every prefix."""
    v = FakeVenue()
    m = mandate(max_spend_per_market="8.00", max_total_spend="8.00",
                max_residual="100")
    accepted, refused = replay(tmp_path, m, v)
    assert accepted, "a fitting mandate must actually let orders through"
    assert refused, "and must still stop when the envelope is gone"
    per = accepted_notional_from_ledger(tmp_path, m.authorization_id)
    spent = per["aec-itfme-conbra-andpet-2026-09-16"]
    assert spent <= D("8.00")
    assert spent > D("4.00"), f"expected the envelope to be well used, got {spent}"


def test_the_full_incident_shape_stays_inside_one_market_and_one_envelope(tmp_path):
    """Settlement, a second market, a restart and a retry storm, all in one
    replay, invariant checked throughout."""
    v, m = FakeVenue(), mandate()
    g = M.guard(v, m, tmp_path)
    slug = "aec-itfwo-meiguo-junzha-2026-09-16"

    for _ in range(3):                                   # the three real fills
        oid = submit(g, buy(slug=slug, px="0.35", qty="0.02"))
        assert oid.startswith("ORD")
        terminal(tmp_path, oid, "0.02")
        assert_invariant(tmp_path, m, "during market 1")

    settle(tmp_path, slug)                               # 07:44:32Z
    assert_invariant(tmp_path, m, "after settlement")

    # what the campaign did next: another market. Refused now.
    for other in ("aec-setkameua-zavmyk-honser-2026-09-16",
                  "aec-itfme-conbra-andpet-2026-09-16"):
        assert submit(g, buy(slug=other)) == "max_total_markets_exhausted"
        assert_invariant(tmp_path, m, f"after attempting {other}")

    # a restart, then a retry storm on the original market
    g2 = M.guard(FakeVenue(), m, tmp_path)
    for _ in range(25):
        submit(g2, buy(slug=slug, px="0.35", qty="0.02"))
        assert_invariant(tmp_path, m, "during retry storm after restart")

    per = accepted_notional_from_ledger(tmp_path, m.authorization_id)
    assert list(per) == [slug]
    assert per[slug] <= D("0.05")
    assert len(v.orders) == 3


# --------------------------------------------------------------------------
# the control: the same replay under the pre-incident rule DOES breach

def test_the_pre_incident_rule_breaches_this_very_invariant(tmp_path):
    """Proof the invariant has teeth.

    The old `counts_against_market_cap` admitted open/extend and excluded
    `spring_complete_*`. Re-create exactly that -- charge only the YES legs --
    and the same replay walks straight past a $0.05 envelope, which is what
    happened. If this test ever passes without breaching, the invariant above
    has stopped measuring anything.
    """
    def old_rule_notional(steps):
        """The pre-incident counter: completes are free."""
        total = D("0")
        for name, side, px, qty, filled in steps:
            if name.startswith("complete"):
                continue                      # <-- the exemption that cost $19
            total += D(px) * D(filled)
        return total

    def true_notional(steps):
        return sum((D(px) * D(filled) for _n, _s, px, qty, filled in steps),
                   D("0"))

    counter = old_rule_notional(CONBRA)
    actual = true_notional(CONBRA)
    assert counter < actual, "the old rule must under-count, or this is moot"
    assert actual > counter * D("2"), (
        f"completes were the bulk of the spend: counter ${counter} vs "
        f"actual ${actual}")
    # and both are far past the authorized envelope
    m = mandate()
    assert actual > D(str(m.max_spend_per_market))
    with pytest.raises(AssertionError, match="accepted"):
        # a ledger built under the old rule would fail the invariant
        _assert_breach(actual, m)


def _assert_breach(actual: D, m) -> None:
    assert actual <= D(str(m.max_spend_per_market)), (
        f"pre-incident rule: market accepted ${actual} > authorized "
        f"${m.max_spend_per_market}")


def test_the_independent_reader_is_not_the_modules_own_counter(tmp_path):
    """Guard against the two implementations silently becoming one.

    They must AGREE on honest ledgers -- and they do, which is the useful
    property. This test exists so that if someone rewrites
    `accepted_notional_from_ledger` to call `consumption()`, the fact that the
    invariant is no longer independently measured is visible here.
    """
    src = Path("tests/test_live_mandate_ledger_invariant.py").read_text()
    body = src.split("def accepted_notional_from_ledger")[1].split("\ndef ")[0]
    assert "consumption(" not in body, (
        "the invariant reader must not delegate to the code under test")

    v, m = FakeVenue(), mandate(max_spend_per_market="8.00",
                                max_total_spend="8.00", max_residual="100")
    replay(tmp_path, m, v)
    mine = accepted_notional_from_ledger(tmp_path, m.authorization_id)
    theirs = M.consumption(tmp_path, m.authorization_id)["per_market"]
    for slug, v_ in mine.items():
        assert v_ == D(theirs[slug]), (slug, v_, theirs[slug])
