"""Railway splits a log message at a hard 80 KiB boundary. The ingest used to
drop every split settlement row via a bare `except: continue`, so
aec-atpcq-ozabar-evazhu settled at 19:28:43Z and stayed recorded as
PENDING_SETTLEMENT. A settlement we cannot READ must never look like a
settlement that did not HAPPEN."""
import importlib.util
import json
import pathlib

_p = (pathlib.Path(__file__).resolve().parents[1]
      / "analysis" / "l5" / "cross_shadow_live" / "ingest.py")
_spec = importlib.util.spec_from_file_location("cs_ingest", _p)
ING = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ING)


def _split_entry(obj, boundary=None):
    """Render obj the way Railway does when the line exceeds the boundary."""
    body = json.dumps(obj, separators=(",", ":"))
    msg = "PMUS_L5_CAMPAIGN " + body
    b = boundary if boundary is not None else ING.SPLIT_BOUNDARY
    if len(msg) <= b:
        return [{"timestamp": "T", "message": msg}]
    return [{"timestamp": "T", "message": msg[:b]},
            {"timestamp": "T", "message": msg[b:]}]


def _settlement(pad):
    return {"kind": "settlement", "slug": "aec-atpcq-ozabar-evazhu-2026-09-14",
            "status": "SETTLED_REALIZED_UNCONFIRMED", "settled": True,
            "realized_confirmed": False, "realized_usd": None,
            "filler": "x" * pad}


def test_a_split_settlement_row_is_reassembled_not_dropped():
    obj = _settlement(ING.SPLIT_BOUNDARY)          # forces a split
    entries = _split_entry(obj)
    assert len(entries) == 2, "test needs an actually-split message"
    got = ING._parse_entry(entries, 0)
    assert got is not None, "the split settlement was dropped"
    assert got["status"] == "SETTLED_REALIZED_UNCONFIRMED"
    assert got["settled"] is True and got["realized_confirmed"] is False


def test_a_short_malformed_line_is_not_glued_to_its_neighbour():
    """Only a line at/over the boundary may absorb continuations. Otherwise an
    ordinary bad line would silently swallow the NEXT record."""
    entries = [{"timestamp": "T", "message": "PMUS_L5_CAMPAIGN {broken"},
               {"timestamp": "T", "message": "PMUS_L5_CAMPAIGN " +
                json.dumps({"kind": "settlement", "slug": "next"})}]
    assert ING._parse_entry(entries, 0) is None
    nxt = ING._parse_entry(entries, 1)
    assert nxt is not None and nxt["slug"] == "next"


def test_an_unsplit_row_still_parses_normally():
    obj = {"kind": "settlement", "slug": "s", "status": "REALIZED",
           "settled": True, "realized_confirmed": True, "realized_usd": "1.25"}
    got = ING._parse_entry(_split_entry(obj), 0)
    assert got["realized_usd"] == "1.25"


def test_reassembly_is_bounded_and_gives_up_rather_than_scanning_forever():
    head = "PMUS_L5_CAMPAIGN {" + "x" * ING.SPLIT_BOUNDARY
    entries = [{"timestamp": "T", "message": head}] + [
        {"timestamp": "T", "message": "y"} for _ in range(ING.MAX_FRAGMENTS + 4)]
    assert ING._parse_entry(entries, 0) is None
