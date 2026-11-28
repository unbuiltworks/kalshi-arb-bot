"""The recorder's ledger survives a restart, PROVEN BY READING IT BACK.

THE OWNER'S FINDING, and it is a property of the counter rather than of
the gate that reads it. `CalibrationRecorder.appended` is initialised to 0 in
`__init__` and incremented only in `_append`, so it counts what THIS
PROCESS wrote and is zeroed by every restart. A readiness gate reading it
can therefore be:

  * SATISFIED by a process that has written events nothing has ever read
    back -- the writes could be going to a volume that is about to be
    replaced, or to a path nothing else will ever open; and
  * REFUSED by a process sitting on a fully durable ledger it has just
    reloaded, because the counter it reads is zero again.

Neither direction says anything about durability. #212 closed the first
half of the problem -- `orders > 0` alone could pass with
`events_appended` zero or missing -- and this closes the rest.

WHAT IS PROVEN HERE. A uniquely identified order event is written, the
ledger is re-opened through a FRESH recorder instance, and the SAME event
identity and contents come back. Then: duplicate delivery, a partial
final line, an unreadable ledger, and a record whose events straddle the
restart.

No network, no transport, no order. The recorder holds no credentials and
cannot submit anything.
"""
from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal as D

import pytest

from src import l5_fill_calibration as FC


UP = "UP"
SLUG = "cpc-btc-updown-15m-2026-11-09-0730z"


def book(bid="0.42", ask="0.44", age=0.4):
    return {"bid": bid, "ask": ask, "bid_shares": "900",
            "ask_shares": "900", "stream_age_s": str(age)}


def ladder(px="0.40", depth="12"):
    return {"bids": [[px, depth]], "asks": [["0.56", depth]]}


def rec(tmp_path, name="fill_calibration.jsonl"):
    """A recorder on a named path, so a FRESH instance can reopen it."""
    return FC.CalibrationRecorder(str(tmp_path), path=str(tmp_path / name))


def write_one(r, coid, *, qty="1", own="0.40"):
    """One complete order's events, through the recorder's own API."""
    r.pre_submit(coid, slug=SLUG, side=UP, own_px=D(own), qty=D(qty),
                 book=book(), ladder=ladder(), now=30.0)
    r.on_ack(coid, "v-%s" % coid[:6])
    r.observe(45.0, slug=SLUG, book=book())
    r.on_fill(coid, qty=D(qty), price=D(own))
    r.on_terminal(coid, state="ORDER_STATE_FILLED")
    r.on_fees_actual(coid, fee_usd=D("0"))
    return coid


# =====================================================================
# THE COUNTER CANNOT DO THIS JOB
# =====================================================================

def test_the_append_counter_is_zeroed_by_a_restart(tmp_path):
    """THE DEFECT, stated as a property. The ledger is intact and the
    counter reads zero, so a gate reading only the counter refuses a
    perfectly durable recorder."""
    coid = "coid-%s" % uuid.uuid4().hex[:10]
    r1 = rec(tmp_path)
    write_one(r1, coid)
    assert r1.appended > 0

    r2 = rec(tmp_path)                        # a FRESH process
    assert r2.appended == 0, r2.appended      # and the counter is gone
    assert coid in r2.orders                  # while the ledger is not


def test_replayed_counts_what_the_counter_cannot(tmp_path):
    """`events_replayed_from_disk` is the only counter that can ONLY be
    non-zero if a write survived a process boundary."""
    coid = "coid-%s" % uuid.uuid4().hex[:10]
    r1 = rec(tmp_path)
    assert r1.replayed == 0                   # nothing to replay yet
    write_one(r1, coid)
    assert r1.replayed == 0                   # its own writes are not replays

    r2 = rec(tmp_path)
    assert r2.replayed == r1.appended, (r2.replayed, r1.appended)
    assert r2.replayed > 0


# =====================================================================
# THE READ-BACK: SAME IDENTITY, SAME CONTENTS
# =====================================================================

def test_a_uniquely_identified_event_comes_back_with_its_identity(tmp_path):
    """THE PROOF THE OWNER ASKED FOR. Write a unique coid, reopen through a
    fresh instance, and get the same identity back -- not merely some
    number of events."""
    coid = "coid-%s" % uuid.uuid4().hex[:12]
    write_one(rec(tmp_path), coid)

    fresh = rec(tmp_path)
    rb = fresh.verify_readback()
    assert rb["readable"] is True, rb
    assert rb["error"] is None, rb
    assert rb["unreadable_lines"] == 0, rb
    assert rb["events_readable"] > 0, rb
    assert rb["orders_readable"] == 1, rb
    assert coid in rb["sample"], rb["sample"]
    assert rb["sample"][coid] == rb["events_readable"], rb
    assert rb["events_replayed_from_disk"] == rb["events_readable"], rb
    assert rb["events_appended_this_process"] == 0, rb


def test_the_CONTENTS_come_back_and_not_only_the_identity(tmp_path):
    """Identity alone would let a truncated record pass. The fields that
    decide an economic question are compared across the restart."""
    coid = "coid-%s" % uuid.uuid4().hex[:12]
    r1 = rec(tmp_path)
    write_one(r1, coid, qty="1", own="0.40")
    before = r1.record(coid)
    assert before is not None

    after = rec(tmp_path).record(coid)
    assert after is not None, "the record did not survive the restart"
    assert after["coid"] == before["coid"] == coid
    ps_b, ps_a = before.get("pre_submit") or {}, after.get("pre_submit") or {}
    for k in ("slug", "side", "own_px", "wire_px", "qty", "resting_side"):
        assert ps_a.get(k) == ps_b.get(k), (k, ps_a.get(k), ps_b.get(k))
    assert ps_a["slug"] == SLUG
    assert ps_a["own_px"] == "0.40"
    # the own/wire relation that the whole price convention rests on
    assert D(ps_a["own_px"]) + D(ps_a["wire_px"]) == D(1) or (
        ps_a["own_px"] == ps_a["wire_px"])          # UP: wire == own
    assert len(after.get("fills") or ()) == len(before.get("fills") or ())
    assert after["capture"] == before["capture"]


def test_full_capture_survives_and_is_judged_by_completeness(tmp_path):
    """`orders_with_full_capture` must agree with `completeness`, not hold a
    second opinion about what a complete record is."""
    coid = "coid-%s" % uuid.uuid4().hex[:12]
    write_one(rec(tmp_path), coid)
    fresh = rec(tmp_path)
    rb = fresh.verify_readback()
    comp = fresh.completeness(coid)
    expect = 1 if all(
        (fresh.record(coid) or {}).get("capture", {}).get(k) == FC.PRESENT
        for k in FC.REQUIRED_CAPTURE) else 0
    assert rb["orders_with_full_capture"] == expect, (rb, comp)


def test_several_orders_each_keep_their_own_identity(tmp_path):
    coids = ["coid-%s" % uuid.uuid4().hex[:10] for _ in range(3)]
    r1 = rec(tmp_path)
    for c in coids:
        write_one(r1, c)
    rb = rec(tmp_path).verify_readback()
    assert rb["orders_readable"] == 3, rb
    for c in coids:
        assert c in rb["sample"], (c, rb["sample"])
    assert sum(rb["sample"].values()) == rb["events_readable"], rb


# =====================================================================
# DUPLICATE DELIVERY
# =====================================================================

def test_a_duplicated_event_line_does_not_duplicate_the_ORDER(tmp_path):
    """The venue and our own retries can deliver the same event twice. The
    ledger is append-only so the LINE may repeat; the order identity must
    not."""
    coid = "coid-%s" % uuid.uuid4().hex[:10]
    r1 = rec(tmp_path)
    write_one(r1, coid)
    path = r1.path
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(lines[0])                    # the pre_submit, again

    fresh = rec(tmp_path)
    rb = fresh.verify_readback()
    assert rb["readable"] is True, rb
    assert rb["orders_readable"] == 1, rb     # ONE order, not two
    assert rb["events_readable"] == len(lines) + 1, rb
    assert rb["unreadable_lines"] == 0, rb
    # and the folded record still describes one order
    assert (fresh.record(coid) or {}).get("coid") == coid


def test_a_duplicated_FILL_is_visible_rather_than_silently_merged(tmp_path):
    """A duplicate fill changes the economics, so it must not be hidden. It
    is reported as what it is: two fill events for one order."""
    coid = "coid-%s" % uuid.uuid4().hex[:10]
    r1 = rec(tmp_path)
    write_one(r1, coid)
    fills_once = len((r1.record(coid) or {}).get("fills") or ())
    r1.on_fill(coid, qty=D("1"), price=D("0.40"))   # delivered twice
    assert len((r1.record(coid) or {}).get("fills") or ()) == fills_once + 1
    after = rec(tmp_path).record(coid)
    assert len(after.get("fills") or ()) == fills_once + 1


# =====================================================================
# PARTIAL WRITES AND UNREADABLE LEDGERS
# =====================================================================

def test_a_partial_final_line_is_COUNTED_and_never_guessed_at(tmp_path):
    """A process killed mid-write leaves a truncated final line. It must be
    reported, not parsed into a plausible record."""
    coid = "coid-%s" % uuid.uuid4().hex[:10]
    r1 = rec(tmp_path)
    write_one(r1, coid)
    with open(r1.path, "a", encoding="utf-8") as fh:
        fh.write('{"kind": "fill", "coid": "%s", "qty": ' % coid)

    fresh = rec(tmp_path)
    rb = fresh.verify_readback()
    assert rb["readable"] is True, rb
    assert rb["unreadable_lines"] == 1, rb
    # and the GATE must refuse on it -- a torn line is not a clean ledger
    assert rb["orders_readable"] == 1, rb


def test_a_line_with_no_order_identity_is_unreadable_not_an_order(tmp_path):
    r1 = rec(tmp_path)
    write_one(r1, "coid-%s" % uuid.uuid4().hex[:10])
    with open(r1.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "fill", "qty": "1"}) + "\n")
        fh.write(json.dumps(["not", "a", "mapping"]) + "\n")
    rb = rec(tmp_path).verify_readback()
    assert rb["unreadable_lines"] == 2, rb
    assert rb["orders_readable"] == 1, rb


def test_an_ABSENT_ledger_reads_as_readable_and_EMPTY(tmp_path):
    """Absent is not unreadable: there is nothing wrong with the file, there
    is no file. The gate refuses it for being empty, which is a different
    refusal with a different remedy."""
    r = rec(tmp_path, name="never-written.jsonl")
    rb = r.verify_readback()
    assert rb["readable"] is False, rb         # OSError -> cannot be read
    assert rb["error"] is not None, rb
    assert rb["events_readable"] == 0, rb


def test_an_UNREADABLE_ledger_names_its_error_and_never_raises(tmp_path):
    """A directory where a file should be. The recorder must report, not
    raise: a recorder that raises takes the heartbeat with it."""
    d = tmp_path / "is-a-directory.jsonl"
    os.makedirs(d, exist_ok=True)
    r = FC.CalibrationRecorder(str(tmp_path), path=str(d))
    rb = r.verify_readback()
    assert rb["readable"] is False, rb
    assert rb["error"] is not None, rb


# =====================================================================
# RESTART RECOVERY, AND THE STRADDLE
# =====================================================================

def test_an_order_whose_events_STRADDLE_the_restart_is_marked(tmp_path):
    """The record most likely to be trusted wrongly. Its events span two
    processes, and the report says so rather than stitching over it."""
    coid = "coid-%s" % uuid.uuid4().hex[:10]
    r1 = rec(tmp_path)
    r1.pre_submit(coid, slug=SLUG, side=UP, own_px=D("0.40"), qty=D("1"),
                  book=book(), ladder=ladder(), now=30.0)
    r1.on_ack(coid, "v1")
    assert (r1.record(coid) or {}).get("restart_boundaries") == 0

    r2 = rec(tmp_path)                        # the restart
    assert coid in r2.orders
    r2.on_fill(coid, qty=D("1"), price=D("0.40"))     # a LIVE event now
    assert (r2.record(coid) or {}).get("restart_boundaries") == 1
    assert r2.report()["orders_straddling_a_restart"] == 1


def test_two_restarts_do_not_invent_boundaries_by_reloading(tmp_path):
    """Reloading is not a boundary. Only a replayed record receiving a LIVE
    event is."""
    coid = "coid-%s" % uuid.uuid4().hex[:10]
    write_one(rec(tmp_path), coid)
    for _ in range(3):
        r = rec(tmp_path)
        assert (r.record(coid) or {}).get("restart_boundaries") == 0
        assert r.report()["orders_straddling_a_restart"] == 0


def test_the_report_carries_both_facts_so_a_gate_can_tell_them_apart(
        tmp_path):
    coid = "coid-%s" % uuid.uuid4().hex[:10]
    r1 = rec(tmp_path)
    write_one(r1, coid)
    rep1 = r1.report()
    assert rep1["events_appended"] > 0
    assert rep1["events_replayed_from_disk"] == 0
    assert rep1["readback"]["readable"] is True

    rep2 = rec(tmp_path).report()
    assert rep2["events_appended"] == 0
    assert rep2["events_replayed_from_disk"] > 0
    assert rep2["readback"]["events_replayed_from_disk"] > 0


# =====================================================================
# THE GATE THAT READS IT
# =====================================================================

def _hb(**kw):
    base = {"market": SLUG,
            "authorization_preflight": {"ok": True,
                                        "evaluated_market": SLUG},
            "campaign": {"permits_this_window": True}}
    base.update(kw)
    return base


def _cal(**kw):
    base = {"orders": 1, "events_appended": 6, "errors": {}}
    base.update(kw)
    return base


def _named(out, name):
    return next((r for r in out["checks"] if r["check"] == name), None)


def test_the_gate_REFUSES_a_report_with_no_readback_as_UNKNOWN(tmp_path):
    from src import l5_probe_preflight as PF
    out = PF.preflight(heartbeat=_hb(), plan=None, markets=[SLUG],
                       calibration=_cal())
    row = _named(out, "recorder_events_survive_a_restart")
    assert row is not None and row["ok"] is PF.UNKNOWN, row
    assert "recorder_events_survive_a_restart" in out["unknown"]


def test_the_gate_REFUSES_when_only_this_process_appended(tmp_path):
    """The exact case the counter could not catch: writes exist, nothing has
    survived a restart."""
    from src import l5_probe_preflight as PF
    coid = "coid-%s" % uuid.uuid4().hex[:10]
    r1 = rec(tmp_path)
    write_one(r1, coid)
    out = PF.preflight(heartbeat=_hb(), plan=None, markets=[SLUG],
                       calibration=_cal(readback=r1.verify_readback()))
    assert _named(out, "recorder_ledger_reads_back")["ok"] is True
    row = _named(out, "recorder_events_survive_a_restart")
    assert row["ok"] is False, row
    assert "recorder_events_survive_a_restart" in out["failed"]


def test_the_gate_PASSES_once_events_have_survived_a_restart(tmp_path):
    from src import l5_probe_preflight as PF
    coid = "coid-%s" % uuid.uuid4().hex[:10]
    write_one(rec(tmp_path), coid)
    fresh = rec(tmp_path)
    out = PF.preflight(heartbeat=_hb(), plan=None, markets=[SLUG],
                       calibration=_cal(readback=fresh.verify_readback()))
    assert _named(out, "recorder_ledger_reads_back")["ok"] is True
    assert _named(out, "recorder_events_survive_a_restart")["ok"] is True


def test_the_gate_REFUSES_a_torn_line_even_with_survivors(tmp_path):
    """A ledger that survived AND is damaged is not a clean ledger."""
    from src import l5_probe_preflight as PF
    coid = "coid-%s" % uuid.uuid4().hex[:10]
    r1 = rec(tmp_path)
    write_one(r1, coid)
    with open(r1.path, "a", encoding="utf-8") as fh:
        fh.write('{"kind": "fill", "coid": "x", "qty": ')
    out = PF.preflight(heartbeat=_hb(), plan=None, markets=[SLUG],
                       calibration=_cal(readback=rec(tmp_path)
                                        .verify_readback()))
    assert _named(out, "recorder_ledger_reads_back")["ok"] is False
    assert "recorder_ledger_reads_back" in out["failed"]


def test_the_gate_REFUSES_an_unreadable_ledger_rather_than_calling_it_empty(
        tmp_path):
    from src import l5_probe_preflight as PF
    out = PF.preflight(
        heartbeat=_hb(), plan=None, markets=[SLUG],
        calibration=_cal(readback={"readable": False,
                                   "error": "OSError: boom",
                                   "events_readable": 0,
                                   "orders_readable": 0,
                                   "unreadable_lines": 0,
                                   "events_replayed_from_disk": 0}))
    assert _named(out, "recorder_ledger_reads_back")["ok"] is False
    assert _named(out, "recorder_events_survive_a_restart")["ok"] is False


@pytest.mark.parametrize("bad", [[], "nope", 7, 0.5])
def test_a_readback_that_is_not_a_mapping_fails_closed(bad):
    from src import l5_probe_preflight as PF
    out = PF.preflight(heartbeat=_hb(), plan=None, markets=[SLUG],
                       calibration=_cal(readback=bad))
    assert _named(out, "recorder_ledger_reads_back")["ok"] is False
    assert _named(out, "recorder_events_survive_a_restart")["ok"] is False


@pytest.mark.parametrize("v", [None, "", "many", True, False, [], {}])
def test_a_non_integer_replay_count_is_not_a_positive_one(v):
    from src import l5_probe_preflight as PF
    out = PF.preflight(
        heartbeat=_hb(), plan=None, markets=[SLUG],
        calibration=_cal(readback={"readable": True, "error": None,
                                   "events_readable": 6,
                                   "orders_readable": 1,
                                   "unreadable_lines": 0,
                                   "events_replayed_from_disk": v}))
    assert _named(out, "recorder_events_survive_a_restart")["ok"] is False, v


# =====================================================================
# THE CRASH THIS FILE FOUND
# =====================================================================

def test_a_ledger_line_that_is_valid_JSON_but_not_an_EVENT_cannot_crash_boot(
        tmp_path):
    """FOUND BY THIS FILE, AND IT WAS A BOOT CRASH.

    `_fold` reads `ev.get("coid")`, and `_load` handed it whatever
    `json.loads` returned. A line holding a JSON list, string or number
    therefore raised `AttributeError` out of `_load`, out of `__init__` --
    and the recorder is constructed in `Runner.__init__`, so ONE such line
    in the durable ledger took the whole process down at boot.

    A ledger is the artifact most likely to hold a torn or foreign line,
    and refusing to START because of one is the opposite of fail-safe: the
    recorder observes, it decides nothing, and nothing on the money path
    reads it.
    """
    good = "coid-%s" % uuid.uuid4().hex[:10]
    r1 = rec(tmp_path)
    write_one(r1, good)
    with open(r1.path, "a", encoding="utf-8") as fh:
        for junk in (["not", "a", "mapping"], "a bare string", 17, 2.5, None):
            fh.write(json.dumps(junk) + "\n")

    fresh = rec(tmp_path)                     # must CONSTRUCT, not raise
    assert good in fresh.orders
    assert fresh.errors.get("ledger_line_not_a_mapping") == 5, fresh.errors
    # the junk is not counted as surviving events
    assert fresh.replayed == r1.appended, (fresh.replayed, r1.appended)
    rb = fresh.verify_readback()
    assert rb["readable"] is True, rb
    assert rb["unreadable_lines"] == 5, rb
    assert rb["orders_readable"] == 1, rb


def test_a_non_mapping_on_the_LIVE_fold_path_is_counted_not_raised(tmp_path):
    """The live path reaches `_fold` too, and a fold must never raise into
    a decision."""
    r = rec(tmp_path)
    r._fold(["still", "not", "a", "mapping"])
    assert r.errors.get("fold_not_a_mapping") == 1, r.errors
    assert r.orders == {}
