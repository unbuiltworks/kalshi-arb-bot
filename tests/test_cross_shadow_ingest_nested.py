"""In shutdown mode the probe stops emitting standalone kind=settlement lines
and reports each settlement inside shutdown_idle.settlement.settled_now[].
The kind test matched nothing and `continue`d, so eight markets settled between
16:30Z and 16:53Z on 2026-09-15 and none reached the ledger -- with `unparsed`
staying 0, so nothing was reported either. Same silent loss as the 80 KiB
split-fragment bug, by a different route."""
import importlib.util
import json
import pathlib

_p = (pathlib.Path(__file__).resolve().parents[1]
      / "analysis" / "l5" / "cross_shadow_live" / "ingest.py")
_spec = importlib.util.spec_from_file_location("cs_ingest_nested", _p)
ING = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ING)

KEY = "flat_at_resolution:2026-09-14T19:28:54.081937520Z"


def _breakdown(**over):
    b = {"slug": "aec-atpcq-ozabar-evazhu-2026-09-14",
         "matched_pairs": "17.97", "matched_pair_cost": "0.85422927",
         "matched_contribution_usd": "2.6195",
         "directional_residual": "0.00", "residual_contribution_usd": None,
         "fees_usd": "venue_unreported", "realized_usd_venue": None,
         "maker_fills": 24, "taker_fills": 0, "cost_basis_usd": "15.3505",
         "incentives": {"credited_to_us_usd": "0.0800",
                        "charged_to_us_usd": "0.0000",
                        "net_to_us_usd": "0.0800",
                        "our_fills_with_a_credit": 8},
         "settlement_status": "SETTLED_REALIZED_UNCONFIRMED",
         "settled_by": "venue_market_resolved_account_flat",
         "settlement_key": KEY}
    b.update(over)
    return b


def _idle(ts, breakdown=None, confirmed=False, realized=None):
    row = {"kind": "shutdown_idle", "commit": "6adcc4c43e67",
           "settlement": {"settled_now": [{
               "slug": "aec-atpcq-ozabar-evazhu-2026-09-14",
               "realized_confirmed": confirmed, "realized_usd": realized,
               "breakdown": breakdown or _breakdown()}]}}
    return {"deploy": [{"timestamp": ts,
                        "message": "PMUS_L5_CAMPAIGN " + json.dumps(row)}]}


def _dump(tmp_path, doc, name="d.json"):
    p = tmp_path / name
    p.write_text(json.dumps(doc))
    return str(p)


def _ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(ING, "LEDGER", str(tmp_path / "ledger.jsonl"))


def test_a_settlement_nested_in_shutdown_idle_reaches_the_ledger(
        tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    before, after, unparsed, _ = ING.ingest_ledger(
        [_dump(tmp_path, _idle("2026-09-15T16:30:51Z"))])
    assert (before, after) == (0, 1)
    assert unparsed == []


def test_the_same_settlement_re_reported_never_duplicates(
        tmp_path, monkeypatch):
    """Overlapping log pulls re-read the same cycles. Keying on the log
    timestamp would append the settlement again on every poll."""
    _ledger(tmp_path, monkeypatch)
    a = _dump(tmp_path, _idle("2026-09-15T16:30:51Z"), "a.json")
    b = _dump(tmp_path, _idle("2026-09-15T16:41:56Z"), "b.json")
    ING.ingest_ledger([a])
    before, after, _, _ = ING.ingest_ledger([b])
    assert (before, after) == (1, 1)
    before, after, _, _ = ING.ingest_ledger([a, b])
    assert (before, after) == (1, 1)


def test_the_five_streams_are_kept_separate_and_never_summed(
        tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    ING.ingest_ledger([_dump(tmp_path, _idle("2026-09-15T16:30:51Z"))])
    r = json.loads(pathlib.Path(ING.LEDGER).read_text().strip())
    assert r["matched_pair_cost"] == "0.85422927"          # 1. matched pair
    assert r["directional_residual"] == "0.00"             # 2. residual
    assert r["fees_usd"] == "venue_unreported"             # 3. fees
    assert r["incentives_credited_usd"] == "0.0800"        # 4. credited only
    assert r["realized_usd_venue"] is None                 # 5. venue realized
    # the whole point: no field merges any two of them
    assert "net_pnl" not in r and "total_usd" not in r


def test_an_unconfirmed_settlement_is_never_marked_confirmed(
        tmp_path, monkeypatch):
    """Everything observed so far is SETTLED_REALIZED_UNCONFIRMED and must stay
    OUT of the net, exactly as honwon-yanhou is held out."""
    _ledger(tmp_path, monkeypatch)
    ING.ingest_ledger([_dump(tmp_path, _idle("2026-09-15T16:30:51Z"))])
    r = json.loads(pathlib.Path(ING.LEDGER).read_text().strip())
    assert r["realized_confirmed"] is False
    assert r["realized_usd"] is None
    assert r["status"] == "SETTLED_REALIZED_UNCONFIRMED"


def test_a_venue_confirmed_settlement_carries_its_realized_through(
        tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    doc = _idle("2026-09-15T16:30:51Z", confirmed=True, realized="1.23",
                breakdown=_breakdown(settlement_status="SETTLED",
                                     realized_usd_venue="1.23"))
    ING.ingest_ledger([_dump(tmp_path, doc)])
    r = json.loads(pathlib.Path(ING.LEDGER).read_text().strip())
    assert r["realized_confirmed"] is True
    assert r["realized_usd"] == "1.23"
    assert r["realized_usd_venue"] == "1.23"


def test_a_standalone_settlement_line_still_ingests(tmp_path, monkeypatch):
    """The nested path must not regress the ordinary one."""
    _ledger(tmp_path, monkeypatch)
    doc = {"deploy": [{"timestamp": "2026-09-15T11:41:05Z",
                       "message": "PMUS_L5_CAMPAIGN " + json.dumps(
                           {"kind": "settlement", "slug": "s", "settled": False,
                            "realized_confirmed": False})}]}
    before, after, _, _ = ING.ingest_ledger([_dump(tmp_path, doc)])
    assert (before, after) == (0, 1)


def test_a_row_with_no_nested_settlement_adds_nothing(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    doc = {"deploy": [{"timestamp": "T", "message": "PMUS_L5_CAMPAIGN " +
                       json.dumps({"kind": "shutdown_idle",
                                   "settlement": {"settled_now": []}})}]}
    before, after, _, _ = ING.ingest_ledger([_dump(tmp_path, doc)])
    assert (before, after) == (0, 0)
