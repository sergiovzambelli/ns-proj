#!/usr/bin/env python3
"""CSI Record — capture raw CSI frames to CSV and broadcast live to the dashboard."""

import argparse
import asyncio
import csv
import json
import os
import time
import numpy as np
import websockets
from csi_parser import parse_frame

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


class RecordProtocol(asyncio.DatagramProtocol):
    def __init__(self, writer, csv_file, max_subs, duration, ws_port, start_time):
        self.writer = writer
        self.csv_file = csv_file
        self.max_subs = max_subs
        self.duration = duration
        self.ws_port = ws_port
        self.start_time = start_time
        self.frame_count = 0
        self._prev_amps = None
        self._cv_buffer = []
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        now = time.time()
        elapsed = now - self.start_time

        if self.duration and elapsed >= self.duration:
            self.transport.close()
            return

        frame = parse_frame(data)
        if frame is None:
            return

        amps = frame.amplitudes
        mean = float(np.mean(amps))
        std = float(np.std(amps))
        cv = std / (mean + 1e-9)

        # Temporal diff
        t_diff = 0.0
        if self._prev_amps is not None and len(amps) == len(self._prev_amps):
            t_diff = float(np.sqrt(np.mean((amps - self._prev_amps) ** 2)) / (mean + 1e-9))
        self._prev_amps = amps

        # Moving Var CV
        self._cv_buffer.append(cv)
        if len(self._cv_buffer) > 30:
            self._cv_buffer = self._cv_buffer[-30:]
        mv_cv = float(np.var(self._cv_buffer)) if len(self._cv_buffer) >= 2 else 0.0

        # --- Save to CSV ---
        row = [now, frame.seq, frame.rssi, frame.noise_floor, frame.channel,
               frame.node_id, frame.n_subcarriers, mean, ""]
        row.extend(amps.tolist())
        row.extend([""] * (self.max_subs - len(amps)))
        self.writer.writerow(row)
        self.csv_file.flush()
        self.frame_count += 1

        # --- Broadcast to dashboard (same format as csi_bridge.py) ---
        msg = json.dumps({
            "t": now,
            "seq": frame.seq,
            "rssi": frame.rssi,
            "amp_mean": round(mean, 2),
            "cv": round(cv, 4),
            "t_diff": round(t_diff, 4),
            "mv_cv": round(mv_cv, 6),
            "n_sub": frame.n_subcarriers,
            "channel": frame.channel,
            # Recording metadata for the dashboard
            "recording": True,
            "rec_frames": self.frame_count,
            "rec_elapsed": round(elapsed, 1),
        })
        asyncio.ensure_future(broadcast(msg))


async def run(udp_port, ws_port, output, duration):
    max_subs = 192
    headers = ['timestamp', 'seq', 'rssi', 'noise_floor', 'channel',
               'node_id', 'n_sub', 'amp_mean', 'label'] + [f'amp_{i}' for i in range(max_subs)]

    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    csv_file = open(output, 'w', newline='')
    writer = csv.writer(csv_file)
    writer.writerow(headers)
    csv_file.flush()

    start_time = time.time()
    dur_str = f"{duration}s" if duration else "∞ (Ctrl+C to stop)"

    print(f"\n  🔴 RECORDING to {output}")
    print(f"  UDP: {udp_port}  →  WS: {ws_port}")
    print(f"  Duration: {dur_str}")
    print(f"  Open csi_dashboard.html and connect to port {ws_port} to watch live.\n")

    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: RecordProtocol(writer, csv_file, max_subs, duration, ws_port, start_time),
        local_addr=("0.0.0.0", udp_port),
        reuse_port=True,
    )

    async with websockets.serve(ws_handler, "0.0.0.0", ws_port):
        try:
            while True:
                await asyncio.sleep(1.0)
                elapsed = time.time() - start_time
                if duration and elapsed >= duration:
                    break
                if duration:
                    filled = int(30 * elapsed / duration)
                    bar = '█' * filled + '░' * (30 - filled)
                    print(f'\r  {bar} {elapsed:.0f}/{duration:.0f}s  ({protocol.frame_count} frames)', end='')
                else:
                    print(f'\r  ● {elapsed:.0f}s  ({protocol.frame_count} frames)', end='')
        except asyncio.CancelledError:
            pass
        finally:
            transport.close()
            csv_file.close()
            elapsed = time.time() - start_time
            print(f"\n\n  ✅ Recording saved: {output}")
            print(f"     {protocol.frame_count} frames in {elapsed:.1f}s\n")


def main():
    parser = argparse.ArgumentParser(description="CSI Record — save frames to CSV + live dashboard")
    parser.add_argument("output", nargs='?', default=None, help="Output CSV path")
    parser.add_argument("--port", type=int, default=5005, help="UDP port to listen on")
    parser.add_argument("--ws-port", type=int, default=8098, help="WebSocket port for live dashboard (default: 8098)")
    parser.add_argument("--duration", type=float, default=None, help="Recording duration in seconds (default: infinite)")
    args = parser.parse_args()

    if args.output is None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        args.output = f"recordings/recording_{ts}.csv"

    try:
        asyncio.run(run(args.port, args.ws_port, args.output, args.duration))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
