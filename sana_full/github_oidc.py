"""Verify GitHub Actions OIDC tokens for the backup wake-up endpoint."""

import base64
import json
import threading
import time
from urllib.request import urlopen

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = f"{ISSUER}/.well-known/jwks"
JWKS_TTL_SECONDS = 6 * 60 * 60
_jwks_cache = {"keys": {}, "expires_at": 0.0}
_jwks_lock = threading.Lock()


def _decode_segment(value):
    value += "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value.encode())


def _fetch_github_keys():
    with urlopen(JWKS_URL, timeout=10) as response:
        payload = json.load(response)
    return {key["kid"]: key for key in payload.get("keys", [])}


def _github_keys(force_refresh=False, now=None):
    """Cache JWKS briefly; refresh immediately when a new signing key appears."""
    now = time.monotonic() if now is None else float(now)
    with _jwks_lock:
        if (
            not force_refresh
            and _jwks_cache["keys"]
            and now < _jwks_cache["expires_at"]
        ):
            return _jwks_cache["keys"]
        keys = _fetch_github_keys()
        _jwks_cache.update(
            keys=keys,
            expires_at=now + JWKS_TTL_SECONDS,
        )
        return keys


def _key_for_id(key_id):
    keys = _github_keys()
    if key_id in keys:
        return keys[key_id]
    keys = _github_keys(force_refresh=True)
    if key_id not in keys:
        raise ValueError("GITHUB_OIDC_UNKNOWN_KEY")
    return keys[key_id]


def verify_github_actions_token(token, audience, repository, now=None):
    """Return verified claims or raise ValueError without exposing the token."""
    try:
        encoded_header, encoded_claims, encoded_signature = token.split(".")
        header = json.loads(_decode_segment(encoded_header))
        claims = json.loads(_decode_segment(encoded_claims))
        key = _key_for_id(header["kid"])
        public_key = rsa.RSAPublicNumbers(
            int.from_bytes(_decode_segment(key["e"]), "big"),
            int.from_bytes(_decode_segment(key["n"]), "big"),
        ).public_key()
        public_key.verify(
            _decode_segment(encoded_signature),
            f"{encoded_header}.{encoded_claims}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("GITHUB_OIDC_INVALID") from exc

    now = int(now or time.time())
    expected = {
        "iss": ISSUER,
        "aud": audience,
        "repository": repository,
        "ref": "refs/heads/main",
    }
    for field, value in expected.items():
        actual = claims.get(field)
        if field == "aud" and isinstance(actual, list):
            if value not in actual:
                raise ValueError("GITHUB_OIDC_CLAIMS_INVALID")
        elif actual != value:
            raise ValueError("GITHUB_OIDC_CLAIMS_INVALID")
    if claims.get("event_name") not in {"schedule", "workflow_dispatch"}:
        raise ValueError("GITHUB_OIDC_CLAIMS_INVALID")
    if int(claims.get("exp") or 0) <= now or int(claims.get("nbf") or 0) > now:
        raise ValueError("GITHUB_OIDC_EXPIRED")
    return claims