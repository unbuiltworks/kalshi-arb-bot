"""Terminal resolution from the contract object, and the flap it has to survive.

TWO CORRECTIONS ARE ENCODED HERE.

First, the settlement gate recorded that this venue "publishes NO terminal
value anywhere public", measured across four expired BTC5m windows. It does:
the contract object carries status, resolutionSide, resolvedAt and
settlementValue, and carried them for all four of those same windows. The
earlier measurement was of the BOOK and the print tape, which do go empty at
expiry, and the conclusion was drawn about the venue rather than about where
I had looked.

Second, the endpoint flaps. GEMI-BTC05M2609180100-UP, polled ten times over
twenty seconds on 2026-09-18, answered settled/yes/76525.56 three times and
then active/null/null seven times -- for a contract that had expired fifteen
minutes earlier. So an `active` reading on an expired contract is a STALE
READ, not evidence of non-settlement, and a settlement (being irreversible)
latches: one settled reading beats any number of active ones.
"""
import pytest

from src.gemini_execution import metadata as MD

NOW = 1789693639.0            # 2026-09-18T01:07:19Z
SYM = "GEMI-BTC05M2609180100-UP"


def _contract(**over):
    c = {"instrumentSymbol": SYM, "status": "active", "marketState": "open",
         "resolutionSide": None, "resolvedAt": None, "settlementValue": None,
         "expiryDate": "2026-09-18T01:00:00.000Z",
         "strike": {"type": "reference", "value": "76478.94"}}
    c.update(over)
    return c


SETTLED_C = _contract(status="settled", marketState="closed",
                      resolutionSide="yes",
                      resolvedAt="2026-09-18T01:00:07.468Z",
                      settlementValue="76525.56162790695")


def _feed(monkeypatch, sequence, event_status="active"):
    """sequence: one contract dict (or None for an unreadable read) per call."""
    calls = {"n": 0}

    def fake(ticker, timeout=15.0):
        i = min(calls["n"], len(sequence) - 1)
        calls["n"] += 1
        c = sequence[i]
        if c is None:
            return {"readable": False, "ticker": ticker, "err": "boom"}
        return {"readable": True, "ticker": ticker,
                "event": {"status": event_status, "contracts": [c]}}

    monkeypatch.setattr(MD, "fetch_event", fake)
    return calls


# ------------------------------------------------------------ the flap

def test_one_settled_read_beats_six_stale_ones(monkeypatch):
    _feed(monkeypatch, [_contract(), _contract(), SETTLED_C])
    out = MD.resolution_for(SYM, reads=3, now=NOW)
    assert out["verdict"] == MD.SETTLED
    assert out["is_terminal"] is True
    assert out["resolutionSide"] == "yes"
    assert out["resolvedAt"] == "2026-09-18T01:00:07.468Z"
    assert out["settlementValue"] == "76525.56162790695"
    assert out["flapped"] is True
    assert out["reads_settled"] == 1 and out["reads_readable"] == 3


def test_the_order_of_the_reads_does_not_matter(monkeypatch):
    _feed(monkeypatch, [SETTLED_C, _contract(), _contract()])
    out = MD.resolution_for(SYM, reads=3, now=NOW)
    assert out["verdict"] == MD.SETTLED and out["resolutionSide"] == "yes"


def test_an_expired_contract_that_never_reads_settled_is_unconfirmed(
        monkeypatch):
    """UNCONFIRMED is the absence of the evidence. It is NOT a claim that the
    contract did not settle, and it must never read as one."""
    _feed(monkeypatch, [_contract()])
    out = MD.resolution_for(SYM, reads=3, now=NOW)
    assert out["verdict"] == MD.UNCONFIRMED
    assert out["is_terminal"] is False
    assert out["expired"] is True
    assert out["resolutionSide"] is None
    assert "NOT that none occurred" in out["labels"]


def test_a_live_contract_is_pending_not_unconfirmed(monkeypatch):
    """Before expiry, unsettled is the correct and complete answer."""
    _feed(monkeypatch, [_contract(expiryDate="2026-09-18T02:00:00.000Z")])
    out = MD.resolution_for(SYM, reads=2, now=NOW)
    assert out["verdict"] == MD.PENDING
    assert out["expired"] is False


def test_a_settled_status_without_the_fields_is_not_terminal(monkeypatch):
    """status alone is not a resolution."""
    _feed(monkeypatch, [_contract(status="settled")])
    out = MD.resolution_for(SYM, reads=2, now=NOW)
    assert out["verdict"] == MD.UNCONFIRMED
    assert out["is_terminal"] is False


def test_an_unreadable_contract_is_never_a_resolution(monkeypatch):
    _feed(monkeypatch, [None])
    out = MD.resolution_for(SYM, reads=3, now=NOW)
    assert out["verdict"] == MD.RESOLUTION_UNREADABLE
    assert out.get("is_terminal") is not True


def test_the_four_required_fields_are_always_named(monkeypatch):
    _feed(monkeypatch, [SETTLED_C])
    out = MD.resolution_for(SYM, reads=1, now=NOW)
    for f in ("status", "resolutionSide", "resolvedAt", "settlementValue"):
        assert f in out
    assert out["missing_fields"] == []


# ------------------------------------------------------- the strike check

def test_the_strike_check_agrees_on_a_real_settled_contract(monkeypatch):
    _feed(monkeypatch, [SETTLED_C])
    sc = MD.resolution_for(SYM, reads=1, now=NOW)["strike_check"]
    assert sc["available"] is True and sc["agrees"] is True
    assert sc["implied_by_strike"] == "yes"


def test_a_disagreement_is_reported_and_never_overrides(monkeypatch):
    """resolutionSide stays authoritative; the check only flags."""
    bad = dict(SETTLED_C, settlementValue="1.00")     # far below the strike
    _feed(monkeypatch, [bad])
    out = MD.resolution_for(SYM, reads=1, now=NOW)
    assert out["resolutionSide"] == "yes"             # unchanged
    assert out["strike_check"]["agrees"] is False
    assert out["strike_check"]["implied_by_strike"] == "no"


def test_the_rule_is_not_assumed_for_a_down_contract(monkeypatch):
    down = dict(SETTLED_C, instrumentSymbol="GEMI-BTC05M2609180100-DOWN")
    _feed(monkeypatch, [down])
    sc = MD.resolution_for("GEMI-BTC05M2609180100-DOWN", reads=1,
                           now=NOW)["strike_check"]
    assert sc["available"] is False and "not assumed" in sc["why"]


# ------------------------------------------------------------- helpers

@pytest.mark.parametrize("iso", [None, "", "not-a-date", "2026-13-45"])
def test_an_unparseable_expiry_is_unknown_never_past(iso):
    assert MD._is_past(iso, NOW) is None


def test_expiry_comparison_is_utc_aware():
    assert MD._is_past("2026-09-18T01:00:00.000Z", NOW) is True
    assert MD._is_past("2026-09-18T02:00:00.000Z", NOW) is False


# ------------------------------------------- the settled-window capability

def test_the_walk_back_stops_at_the_first_settled_window(monkeypatch):
    seen = []

    def fake(symbol, reads=3, now=None):
        seen.append(symbol)
        return {"verdict": MD.SETTLED if len(seen) == 2 else MD.UNCONFIRMED,
                "missing_fields": []}

    monkeypatch.setattr(MD, "resolution_for", fake)
    out = MD.most_recent_settled(SYM, look_back=4, now=NOW)
    assert out["verdict"] == MD.SETTLED
    assert len(seen) == 2                       # stopped, did not walk on
    assert out["windows_tried"][-1]["symbol"] == seen[-1]


def test_the_walk_back_uses_the_clock_not_the_quoted_window(monkeypatch):
    """Stepping back from an instrument far in the future lands on windows
    that have not expired yet, and four PENDING readings answer nothing."""
    seen = []

    def fake(symbol, reads=3, now=None):
        seen.append(symbol)
        return {"verdict": MD.PENDING}

    monkeypatch.setattr(MD, "resolution_for", fake)
    MD.most_recent_settled("GEMI-BTC05M2609180900-UP", look_back=2, now=NOW)
    # NOW is 01:07:19Z, so the current window ends 01:10 and the first step
    # back is 01:05 -- not 08:55, which is where the quoted window would lead
    assert seen[0] == "GEMI-BTC05M2609180105-UP"
    assert "0855" not in seen[0]


def test_no_settled_window_is_unconfirmed_not_a_denial(monkeypatch):
    monkeypatch.setattr(MD, "resolution_for",
                        lambda s, reads=3, now=None: {"verdict": MD.PENDING})
    out = MD.most_recent_settled(SYM, look_back=3, now=NOW)
    assert out["verdict"] == MD.UNCONFIRMED
    assert "not a claim that none settled" in out["why"]
    assert len(out["windows_tried"]) == 3
