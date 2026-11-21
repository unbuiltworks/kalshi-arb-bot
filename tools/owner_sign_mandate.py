#!/usr/bin/env python3
"""OWNER-ONLY. Sign an L5 mandate offline. Run this on YOUR machine.

NOT PART OF THE DEPLOYED IMAGE and never invoked by the executor. It is here
so the bytes you sign are produced by the SAME canonicaliser the executor
verifies with -- a second implementation would eventually disagree and your
signature would stop validating for reasons nobody could see.

    python3 tools/owner_sign_mandate.py \
        --key    ~/.l5/mandate_ed25519.key   # OUTSIDE this repo
        --payload my_mandate.json \
        --key-id owner-2026-10 \
        --out    signed_mandate.json

THE KEY FILE MUST NOT LIVE IN THIS REPOSITORY, in Railway, or on any machine
the executor can read. This script refuses to read a key from inside the
repository working tree, and refuses a world- or group-readable key file.
It also never prints key material.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys


def _die(msg: str) -> "None":
    sys.stderr.write("REFUSED: %s\n" % msg)
    raise SystemExit(2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True, help="raw 32-byte Ed25519 seed")
    ap.add_argument("--payload", required=True, help="unsigned payload JSON")
    ap.add_argument("--key-id", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    key_path = os.path.realpath(os.path.expanduser(a.key))
    repo = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
    if key_path.startswith(repo + os.sep):
        _die("the key is inside the repository working tree (%s). Move it "
             "somewhere the executor can never read, e.g. ~/.l5/" % repo)

    st = os.stat(key_path)
    if st.st_mode & (stat.S_IRGRP | stat.S_IROTH):
        _die("key file is group- or world-readable; run: chmod 600 %s" % a.key)

    with open(key_path, "rb") as fh:
        seed = fh.read().strip()
    if len(seed) == 64:                       # hex-encoded seed is accepted
        try:
            seed = bytes.fromhex(seed.decode())
        except (ValueError, UnicodeDecodeError):
            pass
    if len(seed) != 32:
        _die("key must be 32 raw bytes (or 64 hex chars), got %d" % len(seed))

    sys.path.insert(0, repo)
    from src import l5_mandate_signature as M          # the ONE canonicaliser

    with open(a.payload, "r") as fh:
        payload = json.load(fh)

    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization as S
    priv = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    pub_hex = priv.public_key().public_bytes(
        encoding=S.Encoding.Raw, format=S.PublicFormat.Raw).hex()

    msg = M.canonical_bytes(payload)
    sig = priv.sign(msg).hex()
    doc = {"payload": payload, "sig": sig, "key_id": a.key_id}

    # VERIFY OUR OWN OUTPUT before writing it, with the real verifier and
    # this key temporarily trusted, so a payload that the executor would
    # reject is caught here rather than in production. Everything except the
    # signature is still checked (binding, validity, schema), and those
    # refusals are printed as warnings because only you know the intended
    # environment and series.
    M.MANDATE_PUBKEYS[a.key_id] = pub_hex
    v = M.verify(json.dumps(doc),
                 environment=payload.get("environment", ""),
                 service_role=payload.get("service_role", ""),
                 series=payload.get("series", ""),
                 revoked=[], record_seq=False)
    if not v.get("ok"):
        sys.stderr.write("WARNING: the executor would refuse this mandate: "
                         "%s %s\n" % (v.get("reason"), v.get("detail") or ""))

    with open(a.out, "w") as fh:
        json.dump(doc, fh, sort_keys=True, separators=(",", ":"))
    print("signed            : %s" % a.out)
    print("key_id            : %s" % a.key_id)
    print("PUBLIC key (commit this into MANDATE_PUBKEYS):\n  %s" % pub_hex)
    print("payload sha256-ish bytes signed: %d" % len(msg))
    print("\nPaste the contents of %s into L5_MANDATE_DOC." % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
