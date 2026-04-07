"""
AES-128-GCM decryption for ADR-018-enc CSI frames.

Encrypted frame layout (produced by the ESP32 firmware):
  [Magic 0xC5110003 LE (4B)] [NodeID (1B)] [Nonce (12B)]
  [Ciphertext (N B)] [GCM Tag (16B)]

AAD (authenticated but not encrypted): bytes 0-4 (magic + node_id).
The GCM tag is appended by mbedTLS directly after the ciphertext, so
Python's AESGCM.decrypt() receives ciphertext||tag as a single buffer.

Requirements: pip install cryptography
"""

import struct
from typing import Optional

ENCRYPTED_MAGIC = 0xC5110003
PLAINTEXT_MAGIC = 0xC5110001
_NONCE_LEN = 12
_TAG_LEN   = 16
# Minimum encrypted packet: magic(4) + node_id(1) + nonce(12) + tag(16) = 33 bytes
_MIN_ENC_LEN = 4 + 1 + _NONCE_LEN + _TAG_LEN


def decrypt_frame(data: bytes, key: bytes) -> Optional[bytes]:
    """Decrypt an ADR-018-enc UDP packet.

    Returns the equivalent plaintext ADR-018 bytes on success, or None on
    size underflow, wrong magic, or GCM authentication failure.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise ImportError(
            "pip install cryptography  (required for AES-GCM decryption)"
        ) from exc

    if len(data) < _MIN_ENC_LEN:
        return None

    aad          = data[:5]                         # magic + node_id  (authenticated)
    nonce        = data[5 : 5 + _NONCE_LEN]
    ct_plus_tag  = data[5 + _NONCE_LEN :]           # AESGCM expects ciphertext||tag

    try:
        plaintext = AESGCM(key).decrypt(nonce, ct_plus_tag, aad)
    except Exception:
        return None  # authentication failure or wrong key

    # Reassemble as original ADR-018: swap encrypted magic back to plaintext magic,
    # keep node_id, append decrypted payload.
    orig_magic = struct.pack("<I", PLAINTEXT_MAGIC)
    return orig_magic + data[4:5] + plaintext
