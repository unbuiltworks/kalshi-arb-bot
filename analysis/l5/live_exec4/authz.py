"""The signed authorization document, and the only thing that verifies it.

WHY A DOCUMENT AND NOT ENVIRONMENT VARIABLES. Railway applies variables at
deployment, so every authorization has cost a redeploy -- 13, 25 and 54
minutes in observation, against a 13-minute placement window. That is how
Attempt 4 died. The runtime scheduler must not be the deploy pipeline.

WHAT STAYS TRUE. The owner still authorizes every single experiment, and
Claude still cannot author one: the service holds only the owner's PUBLIC
key, baked into the frozen spec and therefore into the fingerprint. A
document Claude could mint would be a document the owner did not sign.

THE TWO LEDGERS ARE SEPARATE, AND MUST STAY SEPARATE.

  * the AUTHORIZATION ledger records that a signed document was CONSUMED --
    that this authorization has been used up and cannot be replayed;
  * the SUBMISSION latch (live_exec2.latch, unchanged) records that a
    session has made its one submission ATTEMPT.

Collapsing them would be a real loss of safety. An authorization can be
consumed without a submission ever happening (gates refuse, window missed),
and in that case the session latch must remain unspent so the evidence still
says "no order was attempted". Conversely a spent submission latch must not
free up its authorization. Two facts, two files, neither inferred from the
other.

NOTHING HERE CAN SUBMIT. This module verifies and records. It opens no
socket and holds no order route.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

#: the fields the owner signs. Order is irrelevant: the signed bytes are
#: canonical JSON with sorted keys, so a reordered document verifies and a
#: reworded one does not.
REQUIRED_FIELDS = ("probe", "spec_fingerprint", "authorization_id",
                   "session_id", "market_slug", "quantity_shares",
                   "max_possible_loss_usd", "not_before_unix",
                   "not_after_unix", "max_orders")

#: fields the document may NOT carry. `side` is the important one: the side
#: is derived from sha256(slug|session_id), so a signed document must not be
#: able to smuggle in a directional view, and `price` likewise.
FORBIDDEN_FIELDS = ("side", "outcome_side", "price", "offset_ticks",
                    "pair_cost_reservation", "tte_cutoff_s",
                    "staleness_bound_s")

SLUG_RE = re.compile(r"^cpc-btc-updown-15m-\d{4}-\d{2}-\d{2}-\d{4}z$")
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class AuthzRefused(RuntimeError):
    """The document is not a valid, unused, owner-signed authorization."""


class AuthzLedgerUnavailable(RuntimeError):
    """The durable consumption ledger is unusable, so nothing is authorized.

    Fail CLOSED. An authorization we cannot record as consumed is an
    authorization we could replay, which is the one thing the nonce exists
    to prevent.
    """


def canonical_bytes(doc: Mapping[str, Any]) -> bytes:
    """Exactly the bytes the owner signs: canonical JSON of the document
    WITHOUT its signature field."""
    body = {k: v for k, v in doc.items() if k != "signature_b64"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def verify_signature(doc: Mapping[str, Any], public_key_b64: str) -> bool:
    """Detached Ed25519 over `canonical_bytes`. False on ANY failure --
    malformed key, malformed signature, wrong signature. Never raises past
    the caller, because a verification error is a refusal, not an incident."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ed25519
        pub = ed25519.Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key_b64))
        sig = base64.b64decode(str(doc.get("signature_b64") or ""))
        try:
            pub.verify(sig, canonical_bytes(doc))
            return True
        except InvalidSignature:
            return False
    except Exception:                                          # noqa: BLE001
        return False


# ------------------------------------------------------- the nonce ledger

def authz_path(ledger_dir: str, authorization_id: str) -> str:
    stem = _SAFE.sub("-", authorization_id)[:48]
    digest = hashlib.sha256(authorization_id.encode()).hexdigest()[:16]
    return os.path.join(ledger_dir, "%s.%s.authz" % (stem, digest))


def authz_state(ledger_dir: str, authorization_id: str) -> Dict[str, Any]:
    """Read-only. Never creates, never consumes."""
    path = authz_path(ledger_dir, authorization_id)
    used = os.path.exists(path)
    rec = None
    if used:
        try:
            with open(path, "r", encoding="utf8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            rec = {"unreadable": True}
    return {"consumed": used, "path": path, "record": rec,
            "dir": ledger_dir, "dir_exists": os.path.isdir(ledger_dir)}


def consume_authorization(ledger_dir: str, doc: Mapping[str, Any]
                          ) -> Dict[str, Any]:
    """Mark this authorization used, atomically, BEFORE the experiment runs.

    Taken up front on purpose: a document whose experiment outcome we cannot
    observe must not be reusable. This is NOT the submission latch and says
    nothing about whether an order was attempted.
    """
    aid = str(doc["authorization_id"])
    path = authz_path(ledger_dir, aid)
    try:
        os.makedirs(ledger_dir, exist_ok=True)
    except OSError as exc:
        raise AuthzLedgerUnavailable(
            "authorization ledger %s is not writable: %s"
            % (ledger_dir, type(exc).__name__)) from exc
    record = {"authorization_id": aid,
              "session_id": doc.get("session_id"),
              "market_slug": doc.get("market_slug"),
              "spec_fingerprint": doc.get("spec_fingerprint"),
              "consumed_unix": time.time(),
              "consumed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                            time.gmtime()),
              "pid": os.getpid(),
              "deployment": os.getenv("RAILWAY_DEPLOYMENT_ID"),
              "MEANING": ("this AUTHORIZATION is spent. It does NOT mean an "
                          "order was attempted -- that is the submission "
                          "latch, which is a separate file."),
              "NOT_THE_SUBMISSION_LATCH": True}
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise AuthzRefused(
            "authorization %s has already been consumed (%s). A replayed "
            "document is refused." % (aid, path)) from exc
    except OSError as exc:
        raise AuthzLedgerUnavailable(
            "could not record consumption at %s: %s"
            % (path, type(exc).__name__)) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf8") as fh:
            json.dump(record, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        raise AuthzLedgerUnavailable(
            "authorization recorded but not durably written (%s); refusing "
            "to proceed" % type(exc).__name__) from exc
    return record


# ------------------------------------------------------------ verification

def verify(doc: Any, *, public_key_b64: str, my_fingerprint: str,
           ledger_dir: str, now: Optional[float] = None,
           expect_probe: str, expect_qty: str, expect_max_loss: str,
           ) -> Dict[str, Any]:
    """Every check, evaluated together. `ok` False means stay DISARMED.

    The order matters only for reporting: nothing is consumed here, so a
    refusal is free.
    """
    t = time.time() if now is None else now
    reasons: List[str] = []
    if not isinstance(doc, Mapping):
        return {"ok": False, "refusals": ["NOT_A_DOCUMENT"]}

    missing = [f for f in REQUIRED_FIELDS if f not in doc]
    if missing:
        reasons.append("MISSING_FIELDS:%s" % ",".join(missing))
    present_forbidden = [f for f in FORBIDDEN_FIELDS if f in doc]
    if present_forbidden:
        reasons.append("FORBIDDEN_FIELDS:%s" % ",".join(present_forbidden))
    if reasons:
        # a malformed document is refused before anything else is read
        return {"ok": False, "refusals": reasons}

    if not verify_signature(doc, public_key_b64):
        reasons.append("BAD_SIGNATURE")
    if str(doc["probe"]) != expect_probe:
        reasons.append("WRONG_PROBE:%s" % doc["probe"])
    if str(doc["spec_fingerprint"]) != my_fingerprint:
        reasons.append("FINGERPRINT_MISMATCH")
    if not str(doc["authorization_id"]).strip():
        reasons.append("EMPTY_AUTHORIZATION_ID")
    if not str(doc["session_id"]).strip():
        reasons.append("EMPTY_SESSION_ID")
    if not SLUG_RE.match(str(doc["market_slug"])):
        reasons.append("SLUG_NOT_BTC15M:%s" % doc["market_slug"])

    # The economics are the frozen spec's, not the document's. A signed
    # document authorizes THIS experiment; it does not get to redefine the
    # envelope, so a mismatch is refused rather than obeyed.
    try:
        if D(str(doc["quantity_shares"])) != D(expect_qty):
            reasons.append("QUANTITY_NOT_FROZEN_VALUE:%s"
                           % doc["quantity_shares"])
        if D(str(doc["max_possible_loss_usd"])) != D(expect_max_loss):
            reasons.append("MAX_LOSS_NOT_FROZEN_VALUE:%s"
                           % doc["max_possible_loss_usd"])
    except Exception:                                          # noqa: BLE001
        reasons.append("UNPARSEABLE_ECONOMICS")
    try:
        if int(doc["max_orders"]) != 1:
            reasons.append("MAX_ORDERS_NOT_ONE:%s" % doc["max_orders"])
    except Exception:                                          # noqa: BLE001
        reasons.append("UNPARSEABLE_MAX_ORDERS")

    try:
        nb, na = float(doc["not_before_unix"]), float(doc["not_after_unix"])
        if na <= nb:
            reasons.append("EMPTY_VALIDITY_WINDOW")
        if t < nb:
            reasons.append("NOT_YET_VALID:%.0fs early" % (nb - t))
        if t > na:
            reasons.append("EXPIRED:%.0fs ago" % (t - na))
    except Exception:                                          # noqa: BLE001
        reasons.append("UNPARSEABLE_VALIDITY")

    st = authz_state(ledger_dir, str(doc["authorization_id"]))
    if st["consumed"]:
        reasons.append("AUTHORIZATION_ALREADY_CONSUMED")

    return {"ok": not reasons, "refusals": reasons,
            "authorization_id": doc.get("authorization_id"),
            "session_id": doc.get("session_id"),
            "market_slug": doc.get("market_slug"),
            "authz_ledger": st,
            "NOTE": ("verification consumes nothing; the authorization is "
                     "consumed separately, and the submission latch is a "
                     "different file again")}
