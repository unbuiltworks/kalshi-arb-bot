"""One-shot read-only probe for the authenticated Kalshi API usage tier.

Emits only tier/bucket metadata. Credentials and signatures are never logged.
No order-entry or mutation endpoints are called.
"""
from __future__ import annotations

import base64
import json
import os
import time

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

BASE = "https://external-api.kalshi.com"
PATH = "/trade-api/v2/account/limits"


def main() -> None:
    key_id = os.environ["KALSHI_API_KEY_ID"].strip()
    pem = os.environ["KALSHI_PRIVATE_KEY_PEM"].replace("\\n", "\n").encode()
    private_key = serialization.load_pem_private_key(pem, password=None)
    ts = str(int(time.time() * 1000))
    signed_path = PATH
    message = f"{ts}GET{signed_path}".encode()
    signature = private_key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    headers = {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
    }
    with httpx.Client(timeout=10.0) as client:
        response = client.get(BASE + PATH, headers=headers)
    if response.status_code != 200:
        print("KALSHI_ACCOUNT_LIMITS " + json.dumps({
            "ok": False,
            "http_status": response.status_code,
            "order_entry": False,
        }, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    body = response.json()
    print("KALSHI_ACCOUNT_LIMITS " + json.dumps({
        "ok": True,
        "usage_tier": body.get("usage_tier"),
        "read": body.get("read"),
        "write": body.get("write"),
        "host": "external-api.kalshi.com",
        "order_entry": False,
    }, separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    main()
