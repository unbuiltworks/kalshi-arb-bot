"""
Kalshi API authentication and async REST client.

Handles RSA-PSS signature generation and provides an authenticated httpx client
for hitting the Kalshi REST API.

Signature format: RSA-PSS(SHA-256) over "{timestamp_ms}{METHOD}{path}"
Headers required on every request:
    KALSHI-ACCESS-KEY: <api_key_id>
    KALSHI-ACCESS-SIGNATURE: <base64(signature)>
    KALSHI-ACCESS-TIMESTAMP: <unix_ms>
"""

import base64
import os
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from config.settings import API_KEY_ID, PRIVATE_KEY_PATH, REST_URL


def load_private_key(path: str | None = None):
    """Load RSA private key from PEM string (env var) or file."""
    pem_env = os.getenv("KALSHI_PRIVATE_KEY_PEM", "").strip()
    if pem_env:
        # Railway may store the PEM with literal "\n" instead of real newlines
        pem_env = pem_env.replace("\\n", "\n")
        return serialization.load_pem_private_key(pem_env.encode(), password=None)
    key_path = Path(path or PRIVATE_KEY_PATH).expanduser()
    pem_bytes = key_path.read_bytes()
    return serialization.load_pem_private_key(pem_bytes, password=None)


def sign_request(
    private_key,
    timestamp_ms: int,
    method: str,
    path: str,
) -> str:
    """
    Produce a base64-encoded RSA-PSS signature for a Kalshi API request.

    Args:
        private_key: loaded RSA private key
        timestamp_ms: unix epoch milliseconds
        method: HTTP method (GET, POST, DELETE, …)
        path: request path starting with /trade-api/… (no query string)

    Returns:
        base64-encoded signature string
    """
    message = f"{timestamp_ms}{method.upper()}{path}".encode()
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            # Kalshi requires an RSA-PSS salt equal to the SHA-256 digest size.
            # MAX_LENGTH signatures can be locally valid RSA-PSS yet rejected
            # by Kalshi's authenticated REST/WS gateway with HTTP 401.
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode()


def auth_headers(
    private_key,
    method: str,
    path: str,
    api_key_id: str | None = None,
) -> dict[str, str]:
    """Build the three auth headers required by every request."""
    timestamp_ms = int(time.time() * 1000)
    return {
        "KALSHI-ACCESS-KEY": api_key_id or API_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
        "KALSHI-ACCESS-SIGNATURE": sign_request(private_key, timestamp_ms, method, path),
    }


class KalshiClient:
    """Small authenticated async REST client."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key_id: str | None = None,
        private_key_path: str | None = None,
    ):
        self.base_url = (base_url or REST_URL).rstrip("/")
        self.api_key_id = api_key_id or API_KEY_ID
        self.private_key = load_private_key(private_key_path)
        self._client = httpx.AsyncClient(timeout=10.0)

    async def request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        headers.update(auth_headers(self.private_key, method, f"/trade-api/v2{path}", self.api_key_id))
        return await self._client.request(method, url, headers=headers, **kwargs)

    async def get(self, path: str, **kwargs) -> httpx.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs) -> httpx.Response:
        return await self.request("POST", path, **kwargs)

    async def delete(self, path: str, **kwargs) -> httpx.Response:
        return await self.request("DELETE", path, **kwargs)

    async def get_exchange_status(self) -> dict:
        """Hit GET /exchange/status and return the JSON body."""
        resp = await self.get("/exchange/status")
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        if not self._client.is_closed:
            await self._client.aclose()
