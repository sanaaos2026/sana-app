import base64
import json
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import github_oidc
from github_oidc import ISSUER, verify_github_actions_token


def _encode(value):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class GitHubOidcTests(unittest.TestCase):
    def setUp(self):
        github_oidc._jwks_cache.update(keys={}, expires_at=0.0)

    def test_accepts_signed_token_only_for_expected_repo_and_event(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = private_key.public_key().public_numbers()
        key = {
            "kid": "test-key",
            "e": _encode(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
            "n": _encode(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        }
        header = _encode({"alg": "RS256", "kid": "test-key"})
        claims = {
            "iss": ISSUER,
            "aud": "https://sana.example",
            "repository": "sanaaos2026/sana-app",
            "ref": "refs/heads/main",
            "event_name": "schedule",
            "nbf": 900,
            "exp": 1100,
        }
        encoded_claims = _encode(claims)
        signing_input = f"{header}.{encoded_claims}".encode()
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        token = f"{header}.{encoded_claims}.{_encode(signature)}"

        with patch("github_oidc._fetch_github_keys", return_value={"test-key": key}):
            verified = verify_github_actions_token(
                token,
                "https://sana.example",
                "sanaaos2026/sana-app",
                now=1000,
            )
            self.assertEqual("schedule", verified["event_name"])
            with self.assertRaisesRegex(ValueError, "CLAIMS"):
                verify_github_actions_token(
                    token,
                    "https://sana.example",
                    "someone/else",
                    now=1000,
                )

    def test_jwks_cache_expires_and_refreshes(self):
        with patch(
            "github_oidc._fetch_github_keys",
            side_effect=[{"old": {"kid": "old"}}, {"new": {"kid": "new"}}],
        ) as fetch:
            self.assertIn("old", github_oidc._github_keys(now=10))
            self.assertIn("old", github_oidc._github_keys(now=11))
            self.assertIn(
                "new",
                github_oidc._github_keys(
                    now=10 + github_oidc.JWKS_TTL_SECONDS + 1
                ),
            )
        self.assertEqual(2, fetch.call_count)

    def test_unknown_kid_forces_one_rotation_refresh(self):
        with patch(
            "github_oidc._fetch_github_keys",
            side_effect=[
                {"old": {"kid": "old"}},
                {"rotated": {"kid": "rotated"}},
            ],
        ) as fetch:
            github_oidc._github_keys(now=10)
            self.assertEqual(
                {"kid": "rotated"},
                github_oidc._key_for_id("rotated"),
            )
        self.assertEqual(2, fetch.call_count)


if __name__ == "__main__":
    unittest.main()