import base64

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.auth import sign_request


def test_sign_request_uses_kalshi_digest_length_pss_salt():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    timestamp_ms = 1_700_000_000_000
    method = "GET"
    path = "/trade-api/ws/v2"

    encoded = sign_request(private_key, timestamp_ms, method, path)
    signature = base64.b64decode(encoded)
    message = f"{timestamp_ms}{method}{path}".encode()

    # Verification with the exact salt length required by Kalshi fails if
    # sign_request drifts back to PSS.MAX_LENGTH.
    private_key.public_key().verify(
        signature,
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
