#!/usr/bin/env python3
"""CSI Replay — play back a recorded CSV through WebSocket for the dashboard."""

import argparse
import asyncio
import csv
import json
import time
import numpy as np
import websockets

CONNECTED = set()


async def ws_handler(ws):
    CONNECTED.add(ws)
    try:
        async for _ in ws:
            pass
    finally:
        CONNECTED.discard(ws)


async def broadcast(msg: str):
    if CONNECTED:
        await asyncio.gather(*[c.send(msg) for c in CONNECTED], return_exceptions=True)


async def replay(csv_path: str, ws_port: int, speed: float):
    """Read CSV and broadcast each row via WebSocket at realistic timing."""

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total = len(rows)
    if total == 0:
        print("CSV is empty!")
        return

    print(f"\n  ▶ REPLAY: {csv_path}")
    print(f"  {total} frames, speed={speed}x")
    print(f"  WS port: {ws_port}")
    print(f"  Open csi_dashboard.html in browser, then press Enter to start.\n")

    async with websockets.serve(ws_handler, "0.0.0.0", ws_port):
        # Wait for dashboard to connect
        input("  Press Enter to start playback...")
        print()

        last_ts = None
        cv_buffer = []
        prev_amps = None
        start_time = time.time()

        for i, row in enumerate(rows):
            ts = float(row['timestamp'])

            # Timing
            if last_ts is not None:
                dt = ts - last_ts
                wait = max(0.005, min(1.0, dt)) / speed
                await asyncio.sleep(wait)
            last_ts = ts

            # Parse amplitudes
            n_sub = int(row['n_sub'])
            amps = np.array([float(row[f'amp_{j}']) for j in range(n_sub)
                             if row.get(f'amp_{j}', '') != ''], dtype=np.float64)

            # Compute metrics (same as csi_bridge.py)
            mean = float(np.mean(amps))
            std = float(np.std(amps))
            cv = std / (mean + 1e-9)

            t_diff = 0.0
            if prev_amps is not None and len(amps) == len(prev_amps):
                t_diff = float(np.sqrt(np.mean((amps - prev_amps) ** 2)) / (mean + 1e-9))
            prev_amps = amps

            cv_buffer.append(cv)
            if len(cv_buffer) > 30:
                cv_buffer = cv_buffer[-30:]
            mv_cv = float(np.var(cv_buffer)) if len(cv_buffer) >= 2 else 0.0

            # Broadcast in the same JSON format as csi_bridge.py
            msg = json.dumps({
                "t": time.time(),
                "seq": int(row['seq']),
                "rssi": int(row['rssi']),
                "amp_mean": round(mean, 2),
                "cv": round(cv, 4),
                "t_diff": round(t_diff, 4),
                "mv_cv": round(mv_cv, 6),
                "n_sub": n_sub,
                "channel": int(row['channel']),
            })
            await broadcast(msg)

            # Progress
            pct = (i + 1) / total * 100
            filled = int(30 * (i + 1) / total)
            bar = '█' * filled + '░' * (30 - filled)
            print(f'\r  {bar} {pct:5.1f}%  frame {i+1}/{total}  mv_cv={mv_cv:.6f}', end='')

        elapsed = time.time() - start_time
        print(f"\n\n  ■ REPLAY COMPLETE ({total} frames in {elapsed:.1f}s)")
        print(f"  Dashboard will stay live for 10s...")
        await asyncio.sleep(10)


def main():
    parser = argparse.ArgumentParser(description="CSI Replay — play recorded CSV to dashboard")
    parser.add_argument("csv_file", help="Path to recorded CSV file")
    parser.add_argument("--ws-port", type=int, default=8099, help="WebSocket port (default: 8099)")
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier")
    args = parser.parse_args()

    try:
        asyncio.run(replay(args.csv_file, args.ws_port, args.speed))
    except KeyboardInterrupt:
        print("\n\n  Replay stopped.")


if __name__ == "__main__":
    main()
