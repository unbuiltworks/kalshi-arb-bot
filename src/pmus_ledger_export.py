"""P4: a safe OFFLINE export path for the durable probe ledger.

THE PROBLEM. The probe's durable ledger (9,055 orders and growing) lives on
its Railway volume. Railway exposes no volume read, the CLI is not present
in the research environment, and the probe runs no HTTP server -- so every
study of our own execution has been running on whatever Railway log
retention still holds, which is now a few hours. That is the research
bottleneck.

THE CONSTRAINTS, taken as hard limits and not preferences:
  * no HTTP endpoint, authenticated or otherwise;
  * no credentials anywhere near the artifact;
  * no mutation of the ledger;
  * no second executor;
  * no volume move;
  * and it must be PROVEN unable to affect execution before it is deployed.

THE DESIGN. The probe already writes to stdout, and that stream already
reaches research through Railway's own authenticated log API -- it is how
every live decision analysed so far was obtained. So the export adds no
transport at all: it reads the existing ledger file READ-ONLY, in bounded
chunks, redacts to an allowlist, and prints the chunks as ordinary log
rows that research reassembles. No socket is opened, no credential is
read, no byte of the ledger is written, and nothing new is deployed
alongside the executor.

WHY NOT THE OTHER OPTIONS, briefly, so the choice is auditable:
  * Railway bucket / object storage: would put write credentials for an
    external store inside the money-moving service. Rejected.
  * CI artifact: CI cannot see the probe's volume at all. Not a path.
  * recorder's existing :8080 status server: cross-service, and the
    ledger is not on the recorder's volume. Not a path.
  * a new endpoint on the probe: explicitly excluded by the owner.

FAILURE POSTURE. Every entry point is total: any exception, any unreadable
file, any redaction violation returns an empty result and DISABLES the
exporter for the life of the process. It can decline to export; it can
never raise into the caller. The default is OFF and stays off until the
environment says otherwise.

NOT DEPLOYED. This module is built, tested in isolation, and left inert.
Wiring it into the probe means a new image on a service that moves money,
which is a separate decision.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

ENABLE_ENV = "PMUS_LEDGER_EXPORT"          # unset/0/false => inert
LEDGER_DEFAULT = Path("/state/pmus-spring/ledger.jsonl")
MAX_ROWS_PER_CALL = 200
MAX_BYTES_PER_CALL = 256_000
KIND = "LEDGER_EXPORT"

# Allowlist, not denylist. A denylist silently exports whatever field gets
# added to the ledger next; an allowlist fails closed on exactly that.
ALLOWED_KEYS = frozenset({
    "t_utc", "slug", "session_id", "decision_id", "decision_idx", "stage",
    "order_id", "relation", "side", "px", "qty", "filled_qty", "outcome",
    "rest_s", "end_reason", "rejection_class", "rejection_margin_ticks",
    "tick", "touch_join", "side_convention_ok", "state", "acted",
    "refusal", "cap_binding", "divergence", "divergence_class",
    "safety_shell_version", "inventory_before", "l5_touch_px",
    "fresh_opp_ask", "venue_market", "commit", "kind",
})

# Anything that looks like a key, token or signature never leaves, even if
# a future ledger field lands on the allowlist by name. Length + charset,
# because that is what every credential this project handles looks like.
_SECRETISH = re.compile(r"(?:[A-Za-z0-9+/]{40,}={0,2}|[0-9a-fA-F]{48,})")

_disabled = False


def enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """OFF unless explicitly switched on, and off forever after a fault."""
    if _disabled:
        return False
    v = (env if env is not None else os.environ).get(ENABLE_ENV, "")
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _disable() -> None:
    global _disabled
    _disabled = True


def redact(row: Mapping[str, Any]) -> Optional[dict]:
    """Allowlisted keys only; None if anything secret-shaped is present.

    Dropping the whole row rather than the offending value is deliberate:
    a partially redacted row invites someone to reason about what was
    removed, and the ledger has no field whose loss costs more than a
    leaked credential would.
    """
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k not in ALLOWED_KEYS:
            continue
        s = v if isinstance(v, str) else json.dumps(v, sort_keys=True,
                                                    default=str)
        if _SECRETISH.search(s):
            return None
        out[k] = v
    return out or None


def export_chunk(path: Path = LEDGER_DEFAULT, *, offset: int = 0,
                 max_rows: int = MAX_ROWS_PER_CALL,
                 max_bytes: int = MAX_BYTES_PER_CALL,
                 opener=None) -> dict:
    """One bounded, read-only slice of the ledger starting at `offset`.

    `opener` exists so the isolation tests can prove the mode this uses is
    read-only, rather than trusting the literal below.
    """
    op = opener or (lambda p: open(p, "r", encoding="utf-8"))
    rows: list[dict] = []
    dropped = 0
    read = 0
    with op(path) as fh:
        fh.seek(offset)
        while len(rows) < max_rows and read < max_bytes:
            line = fh.readline()
            if not line:
                break
            read += len(line)
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                dropped += 1
                continue
            r = redact(row) if isinstance(row, Mapping) else None
            if r is None:
                dropped += 1
                continue
            rows.append(r)
        nxt = fh.tell()
    body = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return {"kind": KIND, "offset": offset, "next_offset": nxt,
            "rows": rows, "n": len(rows), "dropped": dropped,
            "eof": nxt <= offset or not rows,
            "sha256_16": hashlib.sha256(body.encode()).hexdigest()[:16],
            "labels": "READ-ONLY export of the existing durable ledger; "
                      "allowlisted fields only; no credentials"}


def safe_export(path: Path = LEDGER_DEFAULT, *, offset: int = 0,
                env: Optional[Mapping[str, str]] = None, **kw) -> Optional[dict]:
    """Total wrapper. Returns None rather than raising, ever.

    This is the only function the probe would call. It cannot raise, it
    cannot loop, and one fault retires it for the life of the process --
    so the worst case for the executor is that an export does not happen.
    """
    if not enabled(env):
        return None
    try:
        return export_chunk(path, offset=offset, **kw)
    except BaseException:                                  # noqa: BLE001
        # BaseException on purpose: a KeyboardInterrupt or MemoryError
        # raised inside an export must not take the session with it.
        _disable()
        return None


def reassemble(chunks: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Research side: chunks (in any order) back into ledger rows."""
    ordered = sorted((c for c in chunks if c.get("kind") == KIND),
                     key=lambda c: int(c.get("offset") or 0))
    out: list[dict] = []
    seen: set[int] = set()
    for c in ordered:
        off = int(c.get("offset") or 0)
        if off in seen:
            continue
        seen.add(off)
        out.extend(c.get("rows") or [])
    return out
