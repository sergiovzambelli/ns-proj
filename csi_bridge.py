#!/usr/bin/env python3
"""CSI Bridge — UDP receiver → WebSocket broadcaster for the dashboard."""

import asyncio
import json
import socket
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

class UDPReceiver(asyncio.DatagramProtocol):
    def __init__(self):
        self._prev_amps = None
        self._cv_buffer = []
        self._count = 0

    def datagram_received(self, data, addr):
        frame = parse_frame(data)
        if frame is None:
            return

        self._count += 1
        amps = frame.amplitudes
        mean = float(np.mean(amps))
        std = float(np.std(amps))
        cv = std / (mean + 1e-9)

        # Temporal diff
        t_diff = 0.0
        if self._prev_amps is not None and len(amps) == len(self._prev_amps):
            t_diff = float(np.sqrt(np.mean((amps - self._prev_amps)**2)) / (mean + 1e-9))
        self._prev_amps = amps

        self._cv_buffer.append(cv)
        if len(self._cv_buffer) > 30:
            self._cv_buffer = self._cv_buffer[-30:]

        mv_cv = float(np.var(self._cv_buffer)) if len(self._cv_buffer) >= 2 else 0.0

        msg = json.dumps({
            "t": time.time(),
            "seq": frame.seq,
            "rssi": frame.rssi,
            "amp_mean": round(mean, 2),
            "cv": round(cv, 4),
            "t_diff": round(t_diff, 4),
            "mv_cv": round(mv_cv, 6),
            "n_sub": frame.n_subcarriers,
            "channel": frame.channel,
        })

        asyncio.ensure_future(broadcast(msg))

async def main(udp_port=5005, ws_port=8099):
    print(f"\n  CSI Bridge — UDP :{udp_port} → WS :{ws_port}")
    print(f"  Open demo/csi_dashboard.html in your browser.\n")

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        UDPReceiver, local_addr=("0.0.0.0", udp_port), reuse_port=True)

    async with websockets.serve(ws_handler, "0.0.0.0", ws_port):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--udp-port", type=int, default=5005)
    p.add_argument("--ws-port", type=int, default=8099)
    args = p.parse_args()
    try:
        asyncio.run(main(args.udp_port, args.ws_port))
    except KeyboardInterrupt:
        print("\nStopped.")
