# CSI Motion Detection Demo

A self-contained WiFi motion-detection demo using **Channel State Information (CSI)** captured from an ESP32-S3 node. The system detects human presence and movement by analysing the multipath distortion of WiFi signals — no camera, no IR, no dedicated radar.

## How it works

```
ESP32-S3 (firmware)
   │  UDP frames  (:5005)
   ▼
Python bridge / recorder
   │  WebSocket  (:8099)
   ▼
Browser dashboard (csi_dashboard.html)
```

The ESP32 samples the WiFi channel at ~100 Hz, computes per-subcarrier I/Q data, and streams binary frames over UDP. The Python layer decodes them, computes motion metrics (CV, Moving Variance), and pushes live JSON to a browser dashboard via WebSocket.

## Repository structure

```
demo/
├── plaintext/          # Live pipeline — unencrypted CSI frames
│   ├── csi_parser.py           # Frame decoder (ADR-018 binary protocol)
│   ├── csi_bridge.py           # UDP → WebSocket bridge for the dashboard
│   ├── csi_record.py           # Record frames to CSV + live view
│   ├── csi_dashboard.html      # Real-time dashboard
│   ├── csi_dashboard_threshold.html  # Dashboard with threshold overlay
│   └── replay/                 # Offline analysis tools
│       ├── csi_replay.py           # Replay a recorded CSV
│       ├── csi_replay_hampel.py    # Replay + Hampel filter comparison
│       └── csi_replay_nbvi.py      # Replay + NBVI subcarrier selection
├── encrypt/            # Encrypted pipeline (AES-128-GCM, ADR-018-enc)
│   ├── csi_crypto.py       # AES-GCM decryptor
│   ├── csi_parser.py       # Frame decoder with transparent decryption
│   └── sniffer.py          # Security audit tool
├── firmware/           # ESP32-S3 firmware + provisioning
│   ├── esp32-csi-node/     # ESP-IDF firmware project
│   └── provision.py        # Flash WiFi + AES key via NVS (no recompile)
├── images/             # Captured screenshots and analysis plots
├── txt/                # Build notes and development references
└── recordings/         # (gitignored) CSV session files go here
```

## Requirements

```bash
pip install numpy websockets cryptography
```

For provisioning the ESP32 (optional):
```bash
pip install esptool esp-idf-nvs-partition-gen
```

## Quick start

### 1 — Flash the firmware

See [`firmware/README.md`](firmware/README.md) to flash and provision the ESP32-S3 node.

### 2 — Run the bridge (plaintext mode)

```bash
cd plaintext
python csi_bridge.py
```

Open `plaintext/csi_dashboard.html` in your browser, then trigger CSI frames by pinging the ESP32 from another host:

```bash
sudo ping -f <esp32-ip>
```

### 3 — Record a session

```bash
cd plaintext
python csi_record.py recordings/my_session.csv --duration 60
```

### 4 — Replay and analyse

```bash
# Basic replay
python plaintext/replay/csi_replay.py recordings/my_session.csv

# With Hampel filter (spike rejection)
python plaintext/replay/csi_replay_hampel.py recordings/my_session.csv --idle-seconds 10

# With NBVI subcarrier selection
python plaintext/replay/csi_replay_nbvi.py recordings/my_session.csv --idle-seconds 10
```

## Encrypted mode

If your firmware has AES-128-GCM enabled, use the scripts in `encrypt/`.  
The key must be provided as an environment variable — never hardcode it:

```bash
export CSI_AES_KEY=<your-32-hex-char-key>
```

See [`encrypt/README.md`](encrypt/README.md) for details.
