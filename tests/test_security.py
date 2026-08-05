"""M3 unit tests for API key generation and hashing."""

from __future__ import annotations

from notifly.application.security import (
    _HASH_VERSION,
    generate_key,
    hash_key,
    key_prefix,
    verify_key,
)


def test_generate_key_has_prefix() -> None:
    key = generate_key()
    assert key.startswith("notifly_")
    assert len(key) > len("notifly_")


def test_generate_key_has_entropy() -> None:
    keys = {generate_key() for _ in range(100)}
    assert len(keys) == 100


def test_generate_key_custom_prefix() -> None:
    assert generate_key(prefix="custom_").startswith("custom_")


def test_key_prefix_is_short_and_stable() -> None:
    key = generate_key()
    prefix = key_prefix(key)
    assert prefix == key[:16]
    assert prefix == key_prefix(key)


def test_hash_key_and_verify_roundtrip() -> None:
    key = generate_key()
    stored = hash_key(key, iterations=1000)
    assert stored.startswith(f"{_HASH_VERSION}$1000$")
    assert verify_key(key, stored)


def test_verify_rejects_wrong_key() -> None:
    stored = hash_key("correct-horse", iterations=1000)
    assert not verify_key("battery-staple", stored)


def test_verify_rejects_tampered_stored_value() -> None:
    stored = hash_key("secret", iterations=1000)
    assert not verify_key("secret", stored[:-2])
    assert not verify_key("secret", "garbage")
    assert not verify_key("secret", "")
    assert not verify_key("secret", "unknown$1$abcd$efgh")


def test_verify_rejects_wrong_salt_reuse() -> None:
    stored = hash_key("secret", iterations=1000)
    salt_hex = stored.split("$")[2]
    tampered = f"{_HASH_VERSION}$1000${salt_hex}${'0' * 64}"
    assert not verify_key("secret", tampered)


def test_same_key_hashes_differ_per_salt() -> None:
    stored_a = hash_key("secret", iterations=1000)
    stored_b = hash_key("secret", iterations=1000)
    assert stored_a != stored_b
    assert verify_key("secret", stored_a)
    assert verify_key("secret", stored_b)
