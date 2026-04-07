"""
ADR-018 Frame Parser Module for ESP32 CSI frames.
Parses raw UDP binary packets into Python objects.

Supports both plaintext ADR-018 frames (magic 0xC5110001) and
AES-128-GCM encrypted ADR-018-enc frames (magic 0xC5110003).

To enable decryption, call set_key() once at startup:
    import csi_parser
    import os
    csi_parser.set_key(bytes.fromhex(os.environ["CSI_AES_KEY"]))
"""

import struct
import numpy as np
from dataclasses import dataclass
from typing import Optional

from csi_crypto import decrypt_frame, ENCRYPTED_MAGIC

CSI_MAGIC = 0xC5110001
HEADER_FORMAT = "<IBBHIIBB2x"  # little-endian, 20 bytes total
HEADER_SIZE = 20  # struct.calcsize(HEADER_FORMAT) must equal this

# Module-level AES key; set via set_key() before receiving encrypted frames.
_KEY: Optional[bytes] = None


def set_key(key: bytes) -> None:
    """Configure the AES-128 decryption key (16 bytes).

    Call once at application startup before the receive loop.
    Example:
        import os, csi_parser
        csi_parser.set_key(bytes.fromhex(os.environ["CSI_AES_KEY"]))
    """
    global _KEY
    if len(key) != 16:
        raise ValueError(f"AES-128 key must be 16 bytes, got {len(key)}")
    _KEY = key

@dataclass
class CsiFrame:
    seq: int               # sequence number
    node_id: int           # which ESP32 node (0-255)
    rssi: int              # received signal strength (dBm, negative)
    noise_floor: int       # noise floor (dBm, negative)
    n_antennas: int        # number of antennas
    n_subcarriers: int     # number of subcarriers
    freq_mhz: int          # center frequency in MHz
    channel: int           # derived WiFi channel number
    amplitudes: np.ndarray # float64 array of shape (n_subcarriers,)
    amplitude_mean: float  # mean of amplitudes

def _freq_to_channel(freq_mhz: int) -> int:
    """Derive WiFi channel number from center frequency in MHz."""
    if 2412 <= freq_mhz <= 2472:
        return (freq_mhz - 2412) // 5 + 1
    elif freq_mhz == 2484:
        return 14
    elif freq_mhz >= 5000:
        return (freq_mhz - 5000) // 5
    return 0

def parse_frame(data: bytes) -> Optional[CsiFrame]:
    """Parse a raw UDP packet into a CsiFrame.

    Accepts both plaintext ADR-018 (magic 0xC5110001) and encrypted
    ADR-018-enc frames (magic 0xC5110003).  For encrypted frames, set_key()
    must have been called beforehand; otherwise the frame is dropped.

    Returns None if:
    - data is too short
    - magic number is unrecognised
    - decryption or GCM authentication fails
    - payload is empty (0 I/Q pairs)
    """
    if len(data) < 4:
        return None

    # Detect encrypted frames and transparently decrypt them.
    magic_peek = struct.unpack_from("<I", data, 0)[0]
    if magic_peek == ENCRYPTED_MAGIC:
        if _KEY is None:
            return None  # encrypted frame but no key configured
        data = decrypt_frame(data, _KEY)
        if data is None:
            return None  # authentication failure

    if len(data) < HEADER_SIZE:
        return None
        
    try:
        header = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
    except struct.error:
        return None
        
    magic, node_id, n_antennas, n_sub, freq_mhz, seq, rssi_byte, noise_byte = header
    
    if magic != CSI_MAGIC:
        return None
        
    expected_payload_size = n_antennas * n_sub * 2
    if expected_payload_size == 0 or len(data) < HEADER_SIZE + expected_payload_size:
        return None
        
    # Signed byte conversion for RSSI/Noise
    rssi = rssi_byte if rssi_byte < 128 else rssi_byte - 256
    noise_floor = noise_byte if noise_byte < 128 else noise_byte - 256
    
    # Process I/Q pairs using NumPy
    payload = data[HEADER_SIZE : HEADER_SIZE + expected_payload_size]
    raw = np.frombuffer(payload, dtype=np.uint8)
    signed = raw.astype(np.int16)
    signed[signed >= 128] -= 256
    
    i_vals = signed[0::2].astype(np.float64)
    q_vals = signed[1::2].astype(np.float64)
    amplitudes = np.sqrt(i_vals**2 + q_vals**2)
    
    # If multiple antennas, amplitudes array will have shape (n_antennas * n_subcarriers,)
    # By default, ESP32-S3 uses 1 antenna.
    
    return CsiFrame(
        seq=seq,
        node_id=node_id,
        rssi=rssi,
        noise_floor=noise_floor,
        n_antennas=n_antennas,
        n_subcarriers=n_sub,
        freq_mhz=freq_mhz,
        channel=_freq_to_channel(freq_mhz),
        amplitudes=amplitudes,
        amplitude_mean=float(np.mean(amplitudes))
    )

def build_test_frame(
    seq: int = 0,
    node_id: int = 1,
    rssi: int = -45,
    n_sub: int = 56,
    freq_mhz: int = 2437,  # channel 6
    amplitudes: Optional[list[float]] = None,
) -> bytes:
    """Build a synthetic ADR-018 binary frame for testing.
    
    If amplitudes is None, generates random I/Q pairs.
    If amplitudes is provided, generates I/Q pairs that produce
    approximately those amplitudes (I=round(amp*0.7071), Q=round(amp*0.7071)).
    """
    import random
    
    magic = CSI_MAGIC
    n_antennas = 1
    noise_floor = -90
    
    rssi_byte = (rssi + 256) % 256
    noise_byte = (noise_floor + 256) % 256
    
    header = struct.pack(
        HEADER_FORMAT,
        magic, node_id, n_antennas, n_sub, freq_mhz, seq, rssi_byte, noise_byte
    )
    
    iq_bytes = bytearray()
    if amplitudes is None:
        for _ in range(n_sub):
            i_val = random.randint(-10, 10)
            q_val = random.randint(-10, 10)
            iq_bytes.append((i_val + 256) % 256)
            iq_bytes.append((q_val + 256) % 256)
    else:
        for amp in amplitudes:
            # i = q = amp * sqrt(2)/2 ensures sqrt(i*i + q*q) = amp
            i_val = int(round(amp * 0.70710678118))
            q_val = int(round(amp * 0.70710678118))
            iq_bytes.append((i_val + 256) % 256)
            iq_bytes.append((q_val + 256) % 256)
            
    return header + bytes(iq_bytes)

if __name__ == "__main__":
    tests_passed = 0
    total_tests = 7
    
    print("Running csi_parser.py self-tests...")
    
    # 1. Header size
    if struct.calcsize(HEADER_FORMAT) == 20:
        tests_passed += 1
        print("✓ Test 1: Header size is exactly 20")
    else:
        print("✗ Test 1: Header size mismatch")
        
    # 2. Round-trip test
    frame_bytes = build_test_frame(seq=42, rssi=-50, n_sub=56)
    frame = parse_frame(frame_bytes)
    if frame is not None and frame.seq == 42 and frame.rssi == -50 and len(frame.amplitudes) == 56:
        tests_passed += 1
        print("✓ Test 2: Round-trip parsed correctly")
    else:
        print("✗ Test 2: Round-trip failed")
        
    # 3. Invalid magic
    if parse_frame(b'\x00' * 80) is None:
        tests_passed += 1
        print("✓ Test 3: Invalid magic rejected")
    else:
        print("✗ Test 3: Invalid magic accepted")
        
    # 4. Short data
    if parse_frame(b'\x00' * 10) is None:
        tests_passed += 1
        print("✓ Test 4: Short data rejected")
    else:
        print("✗ Test 4: Short data accepted")
        
    # 5. Channel derivation
    c1 = _freq_to_channel(2437)
    c2 = _freq_to_channel(2412)
    c3 = _freq_to_channel(2462)
    if c1 == 6 and c2 == 1 and c3 == 11:
        tests_passed += 1
        print("✓ Test 5: Channel derivation correct")
    else:
        print(f"✗ Test 5: Channel derivation failed ({c1}, {c2}, {c3})")
        
    # 6. Signed conversion
    # rssi byte 206 (0xCE) = -50. rssi byte 45 = 45.
    test6_1 = build_test_frame(rssi=-50)
    test6_2 = build_test_frame(rssi=45)
    f1 = parse_frame(test6_1)
    f2 = parse_frame(test6_2)
    if f1 and f2 and f1.rssi == -50 and f2.rssi == 45:
        tests_passed += 1
        print("✓ Test 6: Signed conversion correct")
    else:
        print("✗ Test 6: Signed conversion failed")
        
    # 7. Amplitude correctness (I=3, Q=4 -> amp=5.0)
    magic = CSI_MAGIC
    header = struct.pack(HEADER_FORMAT, magic, 1, 1, 1, 2437, 0, 0, 0)
    iq_bytes = bytes([(3 + 256) % 256, (4 + 256) % 256])
    f_amp = parse_frame(header + iq_bytes)
    if f_amp and abs(f_amp.amplitudes[0] - 5.0) < 1e-6:
        tests_passed += 1
        print("✓ Test 7: Amplitude calculated correctly")
    else:
        print("✗ Test 7: Amplitude incorrect")
        
    print(f"\ncsi_parser: {tests_passed}/{total_tests} tests passed")
    if tests_passed == total_tests:
        print("PASS")
    else:
        print("FAIL")
