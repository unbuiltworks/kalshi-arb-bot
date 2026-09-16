"""The cross-shadow ledger must never blur the legacy cohort with the R1 probe.

Both services emit the same `PMUS_L5_CAMPAIGN {...}` vocabulary and the dump
glob is service-agnostic, so the ONLY thing separating them is the provenance
stamped at ingest. These tests pin that, and pin that a cross-cohort key clash
keeps both rows rather than letting one overwrite the other.
"""
from __future__ import annotations

import json

from analysis.l5.cross_shadow_live import ingest as I


def _dump(tmp_path, name, service, entries):
    p = tmp_path / name
    p.write_text(json.dumps({"deploymentId": "dep-" + name, "serviceId": service,
                             "environmentId": "env", "status": "SUCCESS",
                             "deploy": entries}))
    return str(p)


def _line(ts, obj):
    return {"timestamp": ts, "message": "PMUS_L5_CAMPAIGN " + json.dumps(obj)}


def test_every_ledger_row_records_which_service_produced_it(tmp_path, monkeypatch):
    monkeypatch.setattr(I, "LEDGER", str(tmp_path / "ledger.jsonl"))
    legacy = _dump(tmp_path, "a.txt", I.LEGACY_PMUS_SERVICE, [
        _line("2026-09-16T01:00:00Z",
              {"kind": "session_end", "slug": "legacy-mkt", "commit": "aaa"})])
    r1 = _dump(tmp_path, "b.txt", I.R1_PROBE_SERVICE, [
        _line("2026-09-16T02:00:00Z",
              {"kind": "session_end", "slug": "r1-mkt", "commit": "bbb"})])
    I.ingest_ledger([legacy, r1])
    rows = [json.loads(l) for l in open(I.LEDGER) if l.strip()]
    got = {r["slug"]: r["_cohort"] for r in rows}
    assert got == {"legacy-mkt": "pmus-legacy", "r1-mkt": "r1-probe"}
    assert all(r["_service"] and r["_deployment"] for r in rows)


def test_an_unknown_service_is_marked_not_filed_under_the_legacy_cohort(
        tmp_path, monkeypatch):
    monkeypatch.setattr(I, "LEDGER", str(tmp_path / "ledger.jsonl"))
    d = _dump(tmp_path, "c.txt", "some-service-we-have-never-seen", [
        _line("2026-09-16T03:00:00Z",
              {"kind": "session_end", "slug": "x", "commit": "ccc"})])
    I.ingest_ledger([d])
    rows = [json.loads(l) for l in open(I.LEDGER) if l.strip()]
    assert [r["_cohort"] for r in rows] == [I.UNATTRIBUTED]
    assert I.UNATTRIBUTED != "pmus-legacy"


def test_a_settlement_from_each_cohort_stays_separately_attributable(
        tmp_path, monkeypatch):
    """The failure this exists to prevent: an R1 settlement absorbed into the
    legacy net. Both settle; each row must still name its own service."""
    monkeypatch.setattr(I, "LEDGER", str(tmp_path / "ledger.jsonl"))
    def settled(slug, key, usd):
        return _line("2026-09-16T04:00:00Z", {
            "kind": "shutdown_idle", "commit": "ddd",
            "settlement": {"settled_now": [{
                "slug": slug, "realized_usd": usd, "realized_confirmed": True,
                "breakdown": {"settlement_key": key}}]}})
    a = _dump(tmp_path, "d.txt", I.LEGACY_PMUS_SERVICE, [settled("m-legacy", "k1", -1.5)])
    b = _dump(tmp_path, "e.txt", I.R1_PROBE_SERVICE, [settled("m-r1", "k2", 0.02)])
    I.ingest_ledger([a, b])
    rows = [json.loads(l) for l in open(I.LEDGER) if l.strip()]
    by = {r["slug"]: r for r in rows if r["_kind"] == "settlement_nested"}
    assert by["m-legacy"]["_cohort"] == "pmus-legacy"
    assert by["m-r1"]["_cohort"] == "r1-probe"
    legacy_net = sum(float(r["realized_usd"]) for r in rows
                     if r.get("_cohort") == "pmus-legacy" and r.get("realized_confirmed"))
    assert legacy_net == -1.5          # the R1 +0.02 is NOT in it


def test_a_cross_cohort_key_clash_keeps_both_rows(tmp_path, monkeypatch):
    """The venue's settlement_key is stable per market, so without this guard
    the second cohort's row would silently replace the first's."""
    monkeypatch.setattr(I, "LEDGER", str(tmp_path / "ledger.jsonl"))
    def settled(usd):
        return _line("2026-09-16T05:00:00Z", {
            "kind": "shutdown_idle", "commit": "eee",
            "settlement": {"settled_now": [{
                "slug": "same-market", "realized_usd": usd,
                "realized_confirmed": True,
                "breakdown": {"settlement_key": "shared-key"}}]}})
    a = _dump(tmp_path, "f.txt", I.LEGACY_PMUS_SERVICE, [settled(-1.0)])
    b = _dump(tmp_path, "g.txt", I.R1_PROBE_SERVICE, [settled(0.02)])
    _, _, _, collisions = I.ingest_ledger([a, b])
    rows = [json.loads(l) for l in open(I.LEDGER) if l.strip()]
    assert len(collisions) == 1
    assert {r["_cohort"] for r in rows} == {"pmus-legacy", "r1-probe"}
    assert len({r["_key"] for r in rows}) == 2


def test_reingesting_the_same_dump_does_not_duplicate_or_lose_provenance(
        tmp_path, monkeypatch):
    monkeypatch.setattr(I, "LEDGER", str(tmp_path / "ledger.jsonl"))
    d = _dump(tmp_path, "h.txt", I.R1_PROBE_SERVICE, [
        _line("2026-09-16T06:00:00Z",
              {"kind": "session_end", "slug": "r1-mkt", "commit": "fff"})])
    I.ingest_ledger([d])
    _, after, _, collisions = I.ingest_ledger([d])
    rows = [json.loads(l) for l in open(I.LEDGER) if l.strip()]
    assert after == 1 and len(rows) == 1 and not collisions
    assert rows[0]["_cohort"] == "r1-probe"
