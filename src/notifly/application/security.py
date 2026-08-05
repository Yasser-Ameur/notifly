"""API key generation and PBKDF2-SHA256 hashing.

Plaintext keys are high-entropy random strings shown to the caller exactly once,
at creation. Only a salted, self-describing PBKDF2-SHA256 digest is stored, so a
database leak does not reveal usable keys. The short ``key_prefix`` (first 16
characters) is stored separately to scope verification lookups without slowing
authentication down to one PBKDF2 derivation per stored key.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_HASH_VERSION = "pbkdf2_sha256"
_SALT_BYTES = 16
_DERIVED_KEY_BYTES = 32
_KEY_PREFIX_LENGTH = 16


def generate_key(prefix: str = "notifly_") -> str:
    """Generate a new plaintext API key, e.g. ``notifly_<43 url-safe chars>``."""
    return f"{prefix}{secrets.token_urlsafe(32)}"


def key_prefix(raw_key: str, length: int = _KEY_PREFIX_LENGTH) -> str:
    """Return the short, non-secret prefix used to identify a key."""
    return raw_key[:length]


def hash_key(raw_key: str, iterations: int) -> str:
    """Hash a plaintext key into its storage format."""
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", raw_key.encode("utf-8"), salt, iterations, dklen=_DERIVED_KEY_BYTES
    )
    return f"{_HASH_VERSION}${iterations}${salt.hex()}${derived.hex()}"


def verify_key(raw_key: str, stored: str) -> bool:
    """Verify a plaintext key against a stored digest (constant-time compare)."""
    try:
        version, iterations_raw, salt_hex, digest_hex = stored.split("$")
        if version != _HASH_VERSION:
            return False
        salt = bytes.fromhex(salt_hex)
        iterations = int(iterations_raw)
    except ValueError:
        return False
    derived = hashlib.pbkdf2_hmac(
        "sha256", raw_key.encode("utf-8"), salt, iterations, dklen=_DERIVED_KEY_BYTES
    )
    return hmac.compare_digest(derived.hex(), digest_hex)
