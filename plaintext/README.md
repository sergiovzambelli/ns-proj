# Plaintext Pipeline

Scripts for receiving, recording, and analysing **unencrypted** CSI frames (ADR-018 protocol, magic `0xC5110001`).

## Files

| File | Description |
|------|-------------|
| `csi_parser.py` | Decodes raw UDP binary frames into `CsiFrame` objects. Handles I/Q → amplitude conversion and WiFi channel derivation. Also supports encrypted frames if `set_key()` is called. |
| `csi_bridge.py` | UDP receiver → WebSocket broadcaster. Feeds the live dashboard in real time. |
| `csi_record.py` | Records incoming frames to CSV while simultaneously broadcasting live to the dashboard. |
| `csi_dashboard.html` | Real-time browser dashboard — connect to the WebSocket and watch metrics live. |
| `csi_dashboard_threshold.html` | Same dashboard with a configurable motion-threshold overlay. |
| `replay/csi_replay.py` | Replays a recorded CSV at realistic wall-clock speed with drift-free timing. |
| `replay/csi_replay_hampel.py` | Replay + Hampel filter comparison (baseline vs spike-rejected CV stream). |
| `replay/csi_replay_nbvi.py` | Replay + NBVI subcarrier selection (all subcarriers vs K most stable). |

## Usage

### Live view

```bash
# Start the bridge
python csi_bridge.py

# Open csi_dashboard.html in the browser
# Ping the ESP32 from another host to generate CSI traffic
sudo ping -f <esp32-ip>
```

By default the bridge listens on UDP `:5005` and exposes the WebSocket on `:8099`.
Override with `--udp-port` and `--ws-port`.

### Record

```bash
# Record for 60 seconds, then stop
python csi_record.py recordings/my_session.csv --duration 60

# Record indefinitely (Ctrl+C to stop)
python csi_record.py recordings/my_session.csv
```

The dashboard can be open at the same time — recording and live view run concurrently.

### Replay

```bash
# Basic replay
python replay/csi_replay.py recordings/my_session.csv --ws-port 8099

# Hampel filter: baseline vs spike-rejected pipeline (produces a PNG comparison)
python replay/csi_replay_hampel.py recordings/my_session.csv \
    --idle-seconds 10 \
    --hampel-window 7 \
    --hampel-sigma 3

# NBVI: all-subcarrier baseline vs K-most-stable-subcarrier pipeline
python replay/csi_replay_nbvi.py recordings/my_session.csv \
    --idle-seconds 10 \
    --k 12
```

Both analysis scripts print a comparison table and save a PNG plot alongside the CSV.

## Motion metrics

| Metric | Description |
|--------|-------------|
| `amp_mean` | Mean CSI amplitude across all subcarriers |
| `cv` | Coefficient of Variation (σ/μ) — subcarrier spread within a frame |
| `t_diff` | Frame-to-frame RMS amplitude change (temporal difference) |
| `mv_cv` | Moving Variance of CV over the last 30 frames — primary motion indicator |

A sustained spike in `mv_cv` indicates movement in the sensing area.

## CSV format

```
timestamp, seq, rssi, noise_floor, channel, node_id, n_sub, amp_mean, label, amp_0 ... amp_191
```

The `label` column is empty during live recording. Annotate frames manually (`idle` / `movement`) to use the evaluation and separation-score features in the replay scripts.

## Dependencies

```bash
pip install numpy websockets
```
