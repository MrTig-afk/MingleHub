"""NFC SUN ("Secure Unique NFC") signature verification.

Per gamespec.md: a tag generates a signed URL using a rolling counter +
its per-tag AES key on every tap, proving physical presence at the
table. The server must verify the signature and reject any counter
that doesn't strictly increase (replay protection).

DEV-ONLY STUB: real NTAG 424 DNA tags use Secure Dynamic Messaging
(AES-CBC encrypted PICC data + AES-CMAC, see NXP AN12196) — implementing
that exactly requires real tag output to validate against, which we
don't have yet. This stub has the same security properties (can't be
forged without the tag's AES key, counter must strictly increase) via
plain HMAC-SHA256, so the verification *contract* below
(sign/verify_signature) is what every caller depends on. Swapping in
real SDM decryption later only touches this file.
"""
import hashlib
import hmac


def sign(raw_key: bytes, tag_uid: str, counter: int) -> str:
    """Computes the signature a tag would produce for this tap.

    Used by both the (eventual) real tag and the DEV_MODE-only
    simulate-tap endpoint that stands in for one.
    """
    message = f"{tag_uid}:{counter}".encode()
    return hmac.new(raw_key, message, hashlib.sha256).hexdigest()


def verify_signature(raw_key: bytes, tag_uid: str, counter: int, signature: str) -> bool:
    expected = sign(raw_key, tag_uid, counter)
    return hmac.compare_digest(expected, signature)
