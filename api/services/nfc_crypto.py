"""Encryption at rest for NFC tag AES keys (nfc_tags.aes_key_encrypted).

Per security.md: per-tag AES keys must never be stored or returned in
plaintext. Uses Fernet (AES-128-CBC + HMAC, from the `cryptography`
package) keyed by NFC_KEY_ENCRYPTION_SECRET — generate one per
environment with `Fernet.generate_key()`, never reuse across dev/prod.

DEV NOTE: real NTAG 424 DNA tags arrive factory-programmed with their
own AES key, which a future admin/ops provisioning flow would load
here. Until that exists, dashboard_router.pair_tag generates a random
placeholder key for newly-seen tag_uids so the pairing flow (and SUN
verification later) has something real to encrypt/decrypt against.
"""
import os

from cryptography.fernet import Fernet

_fernet = Fernet(os.environ["NFC_KEY_ENCRYPTION_SECRET"].encode())


def encrypt_tag_key(raw_key: bytes) -> str:
    return _fernet.encrypt(raw_key).decode()


def decrypt_tag_key(encrypted: str) -> bytes:
    return _fernet.decrypt(encrypted.encode())
