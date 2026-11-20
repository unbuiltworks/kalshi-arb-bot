"""Apply the owner-listed venue-superset cost corrections, or preview them.

WHAT THIS IS FOR. The diagnostic of 2026-11-20 found 40 settled markets,
inside the account-wide loss figure, where the venue's fills contain more
than this ledger recorded (it adopted one leg of a position and never the
other) and the row is booked at $1 a share. `InventoryStore.
attest_retired_market_cost` can now correct such a row when it is handed the
owner's listed figures for it. This module is the only caller that hands
them in, and it does so only for the markets in ONE shipped manifest.

TWO MODES, AND OFF.

  OFF      `L5_VENUE_SUPERSET_CORRECTION` unset. Nothing is read.
  INSPECT  the value `INSPECT`. The feed is read (GET), and every listed
           market is run through the REAL correction code against a COPY of
           the ledger whose save is disabled. The real ledger is not
           touched. Reports exactly what APPLY would do.
  APPLY    the value `APPLY` AND `L5_VENUE_SUPERSET_CORRECTION_APPROVED`
           equal to the SHA-256 of the manifest file as shipped. The owner
           approves that exact list of figures; a different file is a
           different hash and is refused.

WHAT IT CANNOT DO. It cannot correct a market that is not in the manifest,
or one whose venue quantities, costs, ledger quantities, settlement value,
booked result or corrected result differ in any digit from the list. It
never raises a limit, resets a total or touches a row it was not handed.
Each trade id is consumed once by the store's own guard, so no fill is used
twice. One pass per process; a second pass finds the rows already corrected
and does nothing.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

from src import l5_venue_cost_attestation as VCA

MODE_ENV = "L5_VENUE_SUPERSET_CORRECTION"
APPROVAL_ENV = "L5_VENUE_SUPERSET_CORRECTION_APPROVED"
MANIFEST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "venue_superset_manifest_20261120.json")
MAX_MARKETS = VCA.MAX_MARKETS_PER_WALK
FIELDS = frozenset((
    "venue_up", "venue_down", "cost_up", "cost_down", "ledger_up",
    "ledger_down", "yes_value", "booked_pnl", "corrected_pnl"))


def load_manifest(path: Optional[str] = None) -> Dict[str, Any]:
    """The shipped list and the hash the owner approves. Never raises."""
    try:
        with open(path or MANIFEST_PATH, "rb") as fh:
            raw = fh.read()
        doc = json.loads(raw.decode("utf-8"))
        markets = doc.get("markets")
        if not isinstance(markets, dict) or not markets:
            return {"ok": False, "why": "NO_MARKETS"}
        if len(markets) > MAX_MARKETS:
            return {"ok": False, "why": "TOO_MANY_MARKETS"}
        for m, e in markets.items():
            if not isinstance(e, dict) or set(e) != FIELDS:
                return {"ok": False, "why": "ENTRY_FIELDS", "market": m}
        if doc.get("count") != len(markets):
            return {"ok": False, "why": "COUNT_NE_MARKETS"}
        return {"ok": True, "id": str(doc.get("id")),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "markets": markets}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "why": "MANIFEST_%s" % type(exc).__name__}


def mode(manifest: Mapping[str, Any],
         env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    e = os.environ if env is None else env
    want = str(e.get(MODE_ENV) or "").strip()
    if not want:
        return {"mode": "OFF"}
    if not manifest.get("ok"):
        return {"mode": "REFUSED", "why": manifest.get("why")}
    if want == "INSPECT":
        return {"mode": "INSPECT"}
    if want != "APPLY":
        return {"mode": "REFUSED", "why": "MODE_NOT_RECOGNISED"}
    given = str(e.get(APPROVAL_ENV) or "").strip()
    if not given:
        return {"mode": "REFUSED", "why": "APPLY_WITHOUT_APPROVAL"}
    if given != manifest["sha256"]:
        return {"mode": "REFUSED",
                "why": "APPROVAL_IS_FOR_A_DIFFERENT_MANIFEST"}
    return {"mode": "APPLY"}


def _detached(store: Any) -> Any:
    """A copy of the ledger that CANNOT be saved: same class, same code,
    deep-copied state, `save` a no-op. For INSPECT only."""
    twin = object.__new__(type(store))
    twin.__dict__.update({k: v for k, v in vars(store).items()
                          if k != "state"})
    twin.state = copy.deepcopy(store.state)
    twin.save = lambda *a, **k: None
    return twin


class Correction:
    """One paced pass. Progress in memory; the result is emitted once."""

    def __init__(self, manifest: Optional[Mapping[str, Any]] = None) -> None:
        self.manifest = dict(manifest) if manifest is not None \
            else load_manifest()
        self.walk: Optional[VCA.Walk] = None
        self.finished = False
        self.last_step = 0.0
        self.next_allowed = 0.0

    def step(self, store: Any, transport: Any, now: float) -> Dict[str, Any]:
        md = mode(self.manifest)
        if md["mode"] == "OFF":
            return {"idle": "OFF"}
        if self.finished:
            return {"idle": "DONE"}
        if md["mode"] == "REFUSED":
            self.finished = True
            return {"refused": md.get("why"), "applied": 0}
        if transport is None:
            return {"idle": "NO_TRANSPORT"}
        if now < self.next_allowed \
                or now - self.last_step < VCA.STEP_INTERVAL_S:
            return {"idle": "PACED"}
        self.last_step = now
        markets = sorted(self.manifest["markets"])
        if self.walk is None:
            self.walk = VCA.Walk(markets,
                                 started_at=dt.datetime.now(dt.timezone.utc))
        w = self.walk
        if not w.sufficient and w.ended is None:
            w.step(transport, VCA.PAGES_PER_STEP)
        if not w.sufficient:
            if w.ended is None:
                return {"walking": w.proof()}
            self.walk = None
            if str(w.ended).startswith("error"):
                self.next_allowed = now + VCA.ERROR_BACKOFF_S
                return {"walk_failed": w.ended}
            self.finished = True
            return {"walk_insufficient": w.proof(), "applied": 0}
        target = store if md["mode"] == "APPLY" else _detached(store)
        before = target.realized_lines()
        results, applied, delta = [], 0, D(0)
        for m in markets:
            entry = self.manifest["markets"][m]
            got = VCA.legs_from_rows(w.rows, m)
            if not got.get("ok"):
                results.append({"market": m, "attested": False,
                                "reason": str(got.get("reason"))})
                continue
            out = target.attest_retired_market_cost(
                m, legs=got["legs"], walk=w.proof(),
                correction_id="venue-superset|%s|%s" % (
                    self.manifest["id"], m),
                expect_superset=entry)
            rec = {"market": m, "attested": bool(out.get("attested"))}
            if out.get("attested"):
                applied += 1
                delta += D(str(out.get("delta")))
                rec.update(old_pnl=out.get("old_pnl"),
                           new_pnl=out.get("new_pnl"),
                           delta=out.get("delta"))
            else:
                rec.update(reason=out.get("reason"),
                           superset=out.get("superset"))
            results.append(rec)
        self.finished = True
        self.walk = None
        after = target.realized_lines()
        return {"mode": md["mode"], "manifest_id": self.manifest["id"],
                "manifest_sha256": self.manifest["sha256"],
                "listed": len(markets),
                "would_apply" if md["mode"] == "INSPECT" else "applied":
                    applied,
                "refused": len(markets) - applied,
                "delta_usd": str(delta),
                "realized_attributed_before": before.get(
                    "realized_attributed"),
                "realized_attributed_after": after.get("realized_attributed"),
                "ledger_written": md["mode"] == "APPLY" and applied > 0,
                "walk": w.proof(), "results": results,
                "labels": ("INSPECT ran the real correction against a copy "
                           "that cannot be saved; the ledger is unchanged. "
                           "APPLY wrote only the markets shown attested. "
                           "No limit was changed by either.")}
