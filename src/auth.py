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
    """
    Build the three auth headers required by every Kalshi API call.

    Args:
        private_key: loaded RSA private key
        method: HTTP method
        path: request path (e.g. /trade-api/v2/exchange/status)
        api_key_id: override for API key (defaults to settings)

    Returns:
        dict with KALSHI-ACCESS-KEY, KALSHI-ACCESS-SIGNATURE, KALSHI-ACCESS-TIMESTAMP
    """
    ts = int(time.time() * 1000)
    sig = sign_request(private_key, ts, method, path)
    return {
        "KALSHI-ACCESS-KEY": api_key_id or API_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": sig,
        "KALSHI-ACCESS-TIMESTAMP": str(ts),
    }


class KalshiClient:
    """Async REST client for the Kalshi API with automatic auth signing."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key_id: str | None = None,
        private_key_path: str | None = None,
    ):
        self.base_url = (base_url or REST_URL).rstrip("/")
        self.api_key_id = api_key_id or API_KEY_ID
        self._private_key = load_private_key(private_key_path)
        self._http: httpx.AsyncClient | None = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=10.0)
        return self._http

    def _signed_headers(self, method: str, path: str) -> dict[str, str]:
        return auth_headers(self._private_key, method, path, self.api_key_id)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> httpx.Response:
        """
        Send an authenticated request to the Kalshi API.

        Args:
            method: HTTP method
            path: path relative to base URL (e.g. "/exchange/status")
            json: optional JSON body (for POST/PUT)
            params: optional query parameters

        Returns:
            httpx.Response
        """
        # path can be short (e.g. "/exchange/status") or full ("/trade-api/v2/...")
        if path.startswith("/trade-api"):
            full_path = path
        else:
            full_path = f"/trade-api/v2{path}"
        url = f"{self.base_url}{path}"
        headers = self._signed_headers(method.upper(), full_path)
        client = await self._client()
        return await client.request(
            method.upper(),
            url,
            headers=headers,
            json=json,
            params=params,
        )

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
        if self._http and not self._http.is_closed:
            await self._http.aclose()
