"""WHEN the experiment begins -- experimental scheduling, not strategy.

C4 and C7 entered at 129s and 136s and filled nothing across 19 cycles; C5 and
C6 entered at 282s and 207s and took one fill each. n=4 is a CORRELATION and
must not become strategy knowledge, so nothing the controller reads knows this
number exists. What changes is only which contract the experiment starts in,
which until now was decided by when the workflow happened to reach the armed
step -- CONTINUOUS-7 got 136s by accident of the dispatch clock.

The load-bearing properties are: it can only NARROW the standing entry rule,
it sends nothing, and it runs BEFORE the latch so a wait that never reaches
the band leaves the authorization unspent.
"""
import asyncio

import pytest

from src.gemini_execution import run_continuous as RC


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ================================================================== parsing

@pytest.mark.parametrize("raw,want", [
    ("240:280", (240.0, 280.0)),
    ("90:300", (90.0, 300.0)),
    (None, None), ("", None), ("off", None),
])
def test_the_band_parses_or_is_absent(raw, want):
    assert RC.parse_entry_band(raw) == want


@pytest.mark.parametrize("bad", ["280:240", "0:100", "-5:10", "240", "a:b"])
def test_a_nonsense_band_is_refused_rather_than_guessed(bad):
    with pytest.raises(ValueError):
        RC.parse_entry_band(bad)


# ====================================== it can only NARROW, never widen

def _terms(status="active", state="open"):
    return {"contract_status": status, "market_state": state}


def _clock(monkeypatch, tte_by_symbol):
    """Drive eligible_symbol from a table of symbol -> tte."""
    monkeypatch.setattr(RC.MD, "current_btc5m_ticker", lambda t: "TK0")
    monkeypatch.setattr(RC.MD, "sibling_ticker",
                        lambda tk, minutes=5: "TK%d" % (minutes // 5))
    monkeypatch.setattr(RC.MD, "terms_for", lambda s: _terms())
    monkeypatch.setattr(RC, "_expiry_epoch",
                        lambda s: 1000.0 + tte_by_symbol.get(s, -9e9))


def test_a_band_WIDER_than_the_standing_rule_cannot_widen_it(monkeypatch):
    """The standing rule exists because a window below it cannot complete a
    maker-rest cycle. An experiment may ask for less of the window, never for
    a contract the normal rule refuses."""
    _clock(monkeypatch, {"GEMI-TK0-UP": 45.0})        # below MIN_ENTRY_TTE_S
    sel = RC.eligible_symbol(now=1000.0, band=(10.0, 3000.0))
    assert sel["eligible"] is False
    assert sel["band"] == [RC.MIN_ENTRY_TTE_S, RC.MAX_ENTRY_TTE_S]


def test_the_band_narrows_the_scan(monkeypatch):
    _clock(monkeypatch, {"GEMI-TK0-UP": 136.0, "GEMI-TK1-UP": 265.0})
    # the standing rule would take the 136s contract -- C7's accident
    assert RC.eligible_symbol(now=1000.0)["chosen"]["tte_s"] == 136.0
    # the band skips it and takes the one inside the band
    sel = RC.eligible_symbol(now=1000.0, band=(240.0, 280.0))
    assert sel["eligible"] is True and sel["chosen"]["tte_s"] == 265.0


def test_no_band_is_byte_identical_to_the_standing_behaviour(monkeypatch):
    _clock(monkeypatch, {"GEMI-TK0-UP": 136.0})
    a = RC.eligible_symbol(now=1000.0)
    b = RC.eligible_symbol(now=1000.0, band=None)
    assert a == b and a["chosen"]["tte_s"] == 136.0


# ========================================================= the bounded wait

def test_it_returns_at_once_when_the_band_is_already_open():
    slept = []
    sel = {"eligible": True, "chosen": {"symbol": "S", "tte_s": 262.0}}
    got = _run(RC.await_entry_band((240.0, 280.0),
                                   scan_fn=lambda band: sel,
                                   sleep_fn=lambda s: _noop(slept, s),
                                   now_fn=lambda: 0.0))
    assert got["in_band"] is True and got["entered_tte_s"] == 262.0
    assert got["polls"] == 1 and slept == []
    assert got["selection"] is sel


async def _noop(slept, s):
    slept.append(s)


def test_it_waits_and_then_enters(monkeypatch):
    """The realistic case: we arrive at TTE 136 and the band opens ~160s
    later, on the NEXT contract."""
    slept, now = [], {"t": 0.0}
    answers = [{"eligible": False, "considered": [{"tte_s": 136.0}]},
               {"eligible": False, "considered": [{"tte_s": 126.0}]},
               {"eligible": True, "chosen": {"symbol": "S", "tte_s": 276.0}}]

    async def sleep(s):
        slept.append(s)
        now["t"] += s

    got = _run(RC.await_entry_band((240.0, 280.0),
                                   scan_fn=lambda band: answers.pop(0),
                                   sleep_fn=sleep, now_fn=lambda: now["t"],
                                   poll_s=5.0))
    assert got["in_band"] is True and got["entered_tte_s"] == 276.0
    assert got["polls"] == 3 and slept == [5.0, 5.0]
    assert got["waited_s"] == 10.0
    # what it saw while waiting is kept, so an unexpected wait is explainable
    assert [o["tte_s"] for o in got["observed"]] == [[136.0], [126.0]]


def test_the_wait_is_BOUNDED_and_refuses_rather_than_holding_the_step_open():
    """A scheduler that can wait forever holds an armed workflow step open
    until its timeout, which is a worse failure than not entering."""
    now = {"t": 0.0}

    async def sleep(s):
        now["t"] += s

    got = _run(RC.await_entry_band((240.0, 280.0),
                                   scan_fn=lambda band: {"eligible": False,
                                                         "considered": []},
                                   sleep_fn=sleep, now_fn=lambda: now["t"],
                                   poll_s=5.0, max_wait_s=30.0))
    assert got["in_band"] is False
    assert got["waited_s"] <= 30.0
    assert "refusing BEFORE the latch" in got["why"]


def test_the_default_bound_covers_the_worst_arrival():
    """Worst case is arriving just under the band: the next band opens one
    contract later. 300s contracts, band top at 280 -> ~260s."""
    lo, hi = RC.DEFAULT_ENTRY_BAND
    worst = (lo - 0.01) + (300.0 - hi)
    assert worst < RC.MAX_ENTRY_WAIT_S, (worst, RC.MAX_ENTRY_WAIT_S)


def test_the_scheduler_has_no_session_and_cannot_send(monkeypatch):
    """It takes a scan and a sleep. There is no session, no signer and no
    frame anywhere in its signature, so 'it sends nothing' is a property of
    what it CAN reach rather than of what it happens to do."""
    import inspect
    sig = set(inspect.signature(RC.await_entry_band).parameters)
    assert sig == {"band", "max_wait_s", "poll_s", "now_fn", "sleep_fn",
                   "scan_fn"}


# ======================================= and the refusal is BEFORE the latch

def test_an_unreachable_band_refuses_with_the_latch_UNSPENT(monkeypatch):
    """The authorization survives a window that never started. Spending one on
    a wait that timed out would be the worst of both outcomes."""
    async def never(band, **kw):
        return {"in_band": False, "band": list(band), "waited_s": 420.0,
                "polls": 84, "selection": {"eligible": False},
                "why": "no BTC5m contract reached the entry band"}

    monkeypatch.setattr(RC, "await_entry_band", never)
    rep = _run(RC._build("OWNER-TEST", state_dir="/tmp/nope",
                         ledger_path="/tmp/nope.jsonl",
                         entry_band=(240.0, 280.0)))
    assert rep["state"] == RC.WC.DO_NOT_TRADE
    assert rep["latch"]["consumed"] is False
    assert "BEFORE the latch" in rep["latch"]["why"]
    assert rep["entry_schedule"]["in_band"] is False
    # nothing downstream ran
    assert "window" not in rep and "preflight" not in rep


def test_without_a_band_the_report_still_states_how_the_contract_was_chosen(
        monkeypatch):
    """Silence would read as 'scheduled'. C7 was not scheduled and the record
    should have said so."""
    _clock(monkeypatch, {"GEMI-TK0-UP": -9e9})
    rep = _run(RC._build("OWNER-TEST", state_dir="/tmp/nope",
                         ledger_path="/tmp/nope.jsonl", entry_band=None))
    assert rep["entry_schedule"]["scheduled"] is False
    assert "dispatch time" in rep["entry_schedule"]["why"]
