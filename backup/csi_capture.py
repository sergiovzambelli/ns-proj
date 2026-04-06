#!/usr/bin/env python3
"""CSI Capture — record N seconds of raw CSI, then plot the metrics."""

import argparse
import socket
import time
import sys
import numpy as np
from csi_parser import parse_frame

try:
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


def capture(port: int, duration: float) -> list[dict]:
    """Listen on UDP for `duration` seconds, return list of per-frame data."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(0.5)

    frames = []
    prev_amps = None
    cv_buffer = []
    bar_len = 30

    start = time.time()
    print(f"Capturing for {duration}s on UDP port {port}...")

    while True:
        now = time.time()
        elapsed = now - start
        if elapsed >= duration:
            break

        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue

        frame = parse_frame(data)
        if frame is None:
            continue

        amps = frame.amplitudes
        mean = np.mean(amps)
        std = np.std(amps)
        cv = std / (mean + 1e-9)

        # Temporal diff (only if same size)
        t_diff = 0.0
        if prev_amps is not None and len(amps) == len(prev_amps):
            t_diff = np.sqrt(np.mean((amps - prev_amps) ** 2)) / (mean + 1e-9)
        prev_amps = amps

        cv_buffer.append(cv)
        moving_var_cv = float(np.var(cv_buffer[-30:])) if len(cv_buffer) >= 2 else 0.0

        frames.append({
            "t": elapsed,
            "seq": frame.seq,
            "rssi": frame.rssi,
            "amp_mean": float(mean),
            "amp_var": float(np.var(amps)),
            "cv": cv,
            "t_diff": t_diff,
            "mv_cv": moving_var_cv,
        })

        # Progress bar
        filled = int(bar_len * elapsed / duration)
        bar = '█' * filled + '░' * (bar_len - filled)
        sys.stdout.write(f'\r  {bar} {elapsed:.0f}/{duration:.0f}s  ({len(frames)} frames)')
        sys.stdout.flush()

    sock.close()
    print(f"\n  Done! Captured {len(frames)} frames.\n")
    return frames


def plot(frames: list[dict], output: str):
    if not MATPLOTLIB_AVAILABLE:
        print("matplotlib not installed. Skipping plot.")
        return

    t = [f["t"] for f in frames]

    fig, axes = plt.subplots(4, 1, figsize=(14, 10), dpi=150, sharex=True)
    fig.suptitle(f"CSI Capture — {len(frames)} frames over {t[-1]:.1f}s", fontsize=14)

    # 1. Amplitude Mean
    axes[0].plot(t, [f["amp_mean"] for f in frames], color='steelblue', linewidth=0.5)
    axes[0].set_ylabel("Amplitude Mean")
    axes[0].grid(alpha=0.3)

    # 2. CV (sigma/mu)
    axes[1].plot(t, [f["cv"] for f in frames], color='darkorange', linewidth=0.5)
    axes[1].set_ylabel("CV (σ/μ)")
    axes[1].grid(alpha=0.3)

    # 3. Temporal Diff
    axes[2].plot(t, [f["t_diff"] for f in frames], color='green', linewidth=0.5)
    axes[2].set_ylabel("Temporal Diff")
    axes[2].grid(alpha=0.3)

    # 4. Moving Variance of CV
    axes[3].plot(t, [f["mv_cv"] for f in frames], color='crimson', linewidth=0.5)
    axes[3].set_ylabel("Moving Var CV")
    axes[3].set_xlabel("Time (s)")
    axes[3].grid(alpha=0.3)

    fig.tight_layout()
    plt.savefig(output)
    print(f"  Plot saved to {output}")
    plt.show()


def main():
    parser = argparse.ArgumentParser(description="CSI Capture — record then plot")
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--duration", type=float, default=30.0, help="Seconds to capture")
    parser.add_argument("--output", type=str, default="recordings/capture.png", help="Plot output path")
    args = parser.parse_args()

    frames = capture(args.port, args.duration)
    if len(frames) == 0:
        print("No frames received. Is the ESP32 sending data?")
        sys.exit(1)

    plot(frames, args.output)


if __name__ == "__main__":
    main()
