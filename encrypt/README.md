# Encrypted Pipeline (AES-128-GCM)

Scripts for receiving and analysing **encrypted** CSI frames (ADR-018-enc protocol, magic `0xC5110003`).

When AES-128-GCM is enabled in the ESP32 firmware, each UDP frame is encrypted before transmission. A passive eavesdropper capturing network traffic cannot read RSSI, frequency, or amplitude data — the `sniffer.py` script demonstrates this contrast.

## Files

| File | Description |
|------|-------------|
| `csi_crypto.py` | AES-128-GCM decryptor. Verifies the GCM tag and reassembles the plaintext ADR-018 frame. |
| `csi_parser.py` | Frame decoder with transparent decryption. Accepts both plaintext and encrypted frames — call `set_key()` once at startup to enable decryption. |
| `sniffer.py` | Security audit tool — binds to the CSI UDP port and shows what an attacker sees with and without encryption active. |

## Encrypted frame layout

```
[Magic 0xC5110003 LE  (4 B)]
[NodeID               (1 B)]
[Nonce                (12 B)]
[Ciphertext           (N B)]
[GCM Tag              (16 B)]
```

The GCM tag is appended directly after the ciphertext (mbedTLS convention).
AAD covers bytes 0–4 (magic + node_id): authenticated but not encrypted.

## Usage

### Set the decryption key

The AES-128 key is 16 bytes (128 bits), passed as a 32-character hex string.
**Never hardcode it in source files.** Pass it via environment variable:

```bash
export CSI_AES_KEY=<your-32-hex-char-key>
```

Generate a fresh key:
```bash
python -c "import os; print(os.urandom(16).hex())"
```

### Enable decryption in a script

```python
import os
import csi_parser

csi_parser.set_key(bytes.fromhex(os.environ["CSI_AES_KEY"]))
# parse_frame() now transparently decrypts every incoming frame.
```

### Run the sniffer (security audit)

```bash
python sniffer.py
```

- **Unencrypted firmware**: the sniffer decodes every frame and prints RSSI, frequency, and average amplitude — full attack surface visible.
- **Encrypted firmware**: the sniffer only sees `[ENCRYPTED] Data encrypted — cannot decode.`

## Provisioning the key on the ESP32

Use `firmware/provision.py` to write the AES key to the device's NVS partition without recompiling:

```bash
python ../firmware/provision.py \
    --port /dev/ttyACM0 \
    --ssid "MyWiFi" \
    --password "mypassword" \
    --target-ip 192.168.1.20 \
    --aes-key <your-32-hex-char-key>
```

The same key must be set in `CSI_AES_KEY` on the receiving host.

## Dependencies

```bash
pip install cryptography numpy
```
