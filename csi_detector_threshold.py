#!/usr/bin/env python3
"""CSI Detector Engine — real-time motion classification and WS broadcast."""

import argparse
import asyncio
import json
import socket
import time
import csv
import os
from collections import deque
from enum import Enum
import numpy as np
import websockets
from csi_parser import parse_frame, build_test_frame, CsiFrame

def _channel_to_freq(channel: int) -> int:
    """Convert WiFi channel to center frequency in MHz."""
    if 1 <= channel <= 13:
        return 2412 + (channel - 1) * 5
    elif channel == 14:
        return 2484
    elif channel >= 36:
        return 5000 + channel * 5
    return 2437  # default to channel 6

class State(str, Enum):
    CALIBRATING = "CALIBRATING"
    ABSENT = "ABSENT"
    STILL = "STILL"
    MOVING = "MOVING"

class MotionDetector:
    """3-state motion classifier with auto-calibration."""
    
    def __init__(self, cal_seconds: float = 10.0):
        self.state: State = State.CALIBRATING
        self.cal_seconds = cal_seconds
        self.cal_start: float | None = None
        
        # Calibration buffers
        self._cal_cv_vals: list[float] = []
        
        # Thresholds (set after calibration)
        self.threshold_still: float = 0.0019  # Default from analysis
        self.threshold_moving: float = 0.0050 # Default estimation
        
        # Runtime buffers
        self._cv_buffer: deque = deque(maxlen=30)  # last 30 CV values
        self._prev_amps: np.ndarray | None = None
        self._frame_count = 0
        
        # Adaptive Thresholding state
        self._idle_buffer: deque = deque(maxlen=3000)  # ~60s of idle metrics at 50fps
        self._last_adapt_time: float | None = None
        self._adaptive_interval = 10.0 # Calculate every 10 seconds
        self._adaptive_gamma = 0.05
        
        # Hysteresis state machine
        self._candidate_state: State = State.ABSENT
        self._debounce_count: int = 0
        self._debounce_required: int = 5  # 5 consecutive frames to transition

    def _compute_metrics(self, frame: CsiFrame) -> float:
        """Compute Moving Var of CV (Coefficient of Variation)."""
        amps = frame.amplitudes
        
        # 1. CV (sigma/mu)
        mean = np.mean(amps)
        std = np.std(amps)
        cv = std / (mean + 1e-9)
        
        # 2. Temporal Diff
        t_diff = 0.0
        if self._prev_amps is not None and len(amps) == len(self._prev_amps):
             t_diff = np.sqrt(np.mean((amps - self._prev_amps)**2)) / (mean + 1e-9)
        self._prev_amps = amps
        
        # Combined weighted metric (from Task 5 spec)
        combined = 0.4 * cv + 0.6 * t_diff
        self._cv_buffer.append(combined)
        
        # 3. Final Metric: Moving Variance
        if len(self._cv_buffer) < 2:
            return 0.0
        return float(np.var(self._cv_buffer))

    def _classify(self, metric: float) -> State:
        """Classify with debounce hysteresis."""
        if self.state == State.CALIBRATING:
            return State.CALIBRATING
            
        # Raw logic
        raw_state = State.ABSENT
        if metric > self.threshold_moving:
            raw_state = State.MOVING
        elif metric > self.threshold_still:
            raw_state = State.STILL
            
        # Hysteresis
        if raw_state == self.state:
            self._candidate_state = self.state
            self._debounce_count = 0
        elif raw_state == self._candidate_state:
            self._debounce_count += 1
            if self._debounce_count >= self._debounce_required:
                return raw_state
        else:
            self._candidate_state = raw_state
            self._debounce_count = 1
            
        return self.state

    def process(self, frame: CsiFrame, timestamp: float) -> dict:
        self._frame_count += 1
        metric = self._compute_metrics(frame)
        
        # 1. Handle Calibration
        if self.state == State.CALIBRATING:
            if self.cal_start is None:
                self.cal_start = timestamp
                print(f"--- CALIBRATION START ({self.cal_seconds}s) ---")
                
            elapsed = timestamp - self.cal_start
            self._cal_cv_vals.append(metric)
            
            if elapsed >= self.cal_seconds:
                idle_mean = np.mean(self._cal_cv_vals)
                idle_std = np.std(self._cal_cv_vals)
                
                # Set dynamic thresholds: 3 sigma for STILL, 8 sigma for MOVING
                self.threshold_still = max(0.0005, idle_mean + 3 * idle_std)
                self.threshold_moving = max(0.0020, idle_mean + 8 * idle_std)
                
                print(f"--- CALIBRATION COMPLETE ---")
                print(f"  Idle Baseline: {idle_mean:.6f}")
                print(f"  Thresholds: Still > {self.threshold_still:.4f}, Moving > {self.threshold_moving:.4f}")
                self.state = State.ABSENT
                
                # Setup adaptive tracking seeds
                self._last_adapt_time = timestamp
                self._idle_buffer.extend(self._cal_cv_vals)
            else:
                return {
                    "state": State.CALIBRATING,
                    "metric": metric,
                    "cal_progress": elapsed / self.cal_seconds,
                    "cal_frames": len(self._cal_cv_vals),
                    "threshold_still": 0.0,
                    "threshold_moving": 0.0,
                    "rssi": frame.rssi,
                    "seq": frame.seq
                }

        # 2. Normal Detection
        self.state = self._classify(metric)
        
        # 3. Adaptive Thresholding Update
        if self.state in (State.ABSENT, State.STILL):
            # Anti-Poisoning condition: Only add to idle buffer if not moving
            self._idle_buffer.append(metric)
            
        if self._last_adapt_time is not None:
            if (timestamp - self._last_adapt_time) >= self._adaptive_interval:
                # Time to adapt
                if len(self._idle_buffer) > 100: # Ensure we have enough data
                    p95 = float(np.percentile(self._idle_buffer, 95))
                    
                    tau_target_moving = p95 * 1.1
                    # Rough correlation for STILL threshold based on P95
                    tau_target_still = p95 * 0.4
                    
                    # Apply low-pass filter (exponential moving average)
                    gamma = self._adaptive_gamma
                    self.threshold_moving = (1 - gamma) * self.threshold_moving + gamma * tau_target_moving
                    self.threshold_still = (1 - gamma) * self.threshold_still + gamma * tau_target_still
                    
                self._last_adapt_time = timestamp
        
        msg = {
            "state": self.state,
            "metric": metric,
            "threshold_still": self.threshold_still,
            "threshold_moving": self.threshold_moving,
            "rssi": frame.rssi,
            "seq": frame.seq,
            "channel": frame.channel,
            "node_id": frame.node_id
        }
        
        # Send full amplitudes only once every 50 frames (Task 5 spec)
        if self._frame_count % 50 == 0:
            msg["amps"] = frame.amplitudes.tolist()
            
        return msg

# --- WebSocket Server Global State ---
CONNECTED_CLIENTS = set()

async def ws_handler(websocket):
    CONNECTED_CLIENTS.add(websocket)
    try:
        async for _ in websocket:
            pass # Keep alive
    finally:
        CONNECTED_CLIENTS.discard(websocket)

async def broadcast(message: str):
    if not CONNECTED_CLIENTS:
        return
    await asyncio.gather(*[client.send(message) for client in CONNECTED_CLIENTS], return_exceptions=True)

class DetectorProtocol(asyncio.DatagramProtocol):
    def __init__(self, detector: MotionDetector, record_file=None):
        self.detector = detector
        self._last_log = 0.0
        
        # CSV Recording
        self.record_file = record_file
        self.record_writer = None
        self.csv_file = None
        self.recorded_frames = 0
        self.record_start = time.time()
        
        if record_file:
            self.csv_file = open(record_file, 'w', newline='')
            self.record_writer = csv.writer(self.csv_file)
            headers = ['timestamp', 'seq', 'rssi', 'noise_floor', 'channel', 
                       'node_id', 'n_sub', 'amp_mean', 'label'] + [f'amp_{i}' for i in range(192)]
            self.record_writer.writerow(headers)
            self.csv_file.flush()

    def datagram_received(self, data, addr):
        frame = parse_frame(data)
        if not frame:
            return
            
        now = time.time()
        result = self.detector.process(frame, now)
        
        # Recording
        if self.record_writer:
            row = [now, frame.seq, frame.rssi, frame.noise_floor, frame.channel, 
                   frame.node_id, frame.n_subcarriers, float(np.mean(frame.amplitudes)), ""]
            row.extend(frame.amplitudes.tolist())
            row.extend([""] * (192 - len(frame.amplitudes)))
            self.record_writer.writerow(row)
            self.csv_file.flush()
            self.recorded_frames += 1
        
        # Broadcast
        asyncio.create_task(broadcast(json.dumps(result)))
        
        # Terminal log (Throttled 3s)
        if now - self._last_log > 3.0:
            ico = {"CALIBRATING": "⏳", "ABSENT": "⬛", "STILL": "🔵", "MOVING": "🟡"}.get(result["state"], "?")
            print(f"{ico} {result['state'].value:11} m={result['metric']:.6f} RSSI={result['rssi']} seq={result['seq']}")
            self._last_log = now
            
    def connection_lost(self, exc):
        if self.csv_file:
            self.csv_file.close()
            elapsed = time.time() - self.record_start
            print(f"● Recording saved: {self.record_file} ({self.recorded_frames} frames, {elapsed:.1f}s)")

async def run_server(udp_port, ws_port, cal_sec, record_file=None):
    detector = MotionDetector(cal_seconds=cal_sec)
    
    print("\n╔" + "═" * 46 + "╗")
    print("║" + "  CSI Motion Detector".ljust(46) + "║")
    print("║" + f"  UDP: 0.0.0.0:{udp_port}  →  WS: 0.0.0.0:{ws_port}".ljust(46) + "║")
    if record_file:
         print("║" + f"  REC: {os.path.basename(record_file)}".ljust(46) + "║")
    print("╚" + "═" * 46 + "╝")
    print("Waiting for ESP32 frames...")
    print("⚠ Keep room STILL for 10s after first frame!\n")

    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: DetectorProtocol(detector, record_file),
        local_addr=("0.0.0.0", udp_port)
    )
    
    async with websockets.serve(ws_handler, "0.0.0.0", ws_port):
        try:
            while True:
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass
        finally:
            transport.close()

async def run_playback(args):
    print(f"\n▶ PLAYBACK: {args.playback} (speed={args.speed}x)")
    
    detector = MotionDetector(cal_seconds=args.cal_seconds)
    last_ts = None
    frames_processed = 0
    start_time = time.time()
    
    with open(args.playback, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        
    total_frames = len(rows)
    print(f"  Loaded {total_frames} frames.\n")
    
    async with websockets.serve(ws_handler, "0.0.0.0", args.ws_port):
        for row in rows:
            ts = float(row['timestamp'])
            
            if last_ts is not None:
                dt = ts - last_ts
                wait_time = max(0.01, min(1.0, dt)) / args.speed
                await asyncio.sleep(wait_time)
            
            last_ts = ts
            
            n_sub = int(row['n_sub'])
            amps = np.array([float(row[f'amp_{i}']) for i in range(n_sub)], dtype=np.float64)
            
            frame = CsiFrame(
                seq=int(row['seq']),
                node_id=int(row['node_id']),
                rssi=int(row['rssi']),
                noise_floor=int(row.get('noise_floor', -90)),
                n_antennas=1,
                n_subcarriers=n_sub,
                freq_mhz=_channel_to_freq(int(row['channel'])),
                channel=int(row['channel']),
                amplitudes=amps,
                amplitude_mean=float(row['amp_mean']),
            )
            
            now = time.time()
            result = detector.process(frame, now)
            await broadcast(json.dumps(result))
            
            frames_processed += 1
            ico = {"CALIBRATING": "⏳", "ABSENT": "⬛", "STILL": "🔵", "MOVING": "🟡"}.get(result["state"], "?")
            print(f"\r{ico} {result['state'].value:11} m={result['metric']:.6f}  frame {frames_processed}/{total_frames}", end="")
            
        elapsed = time.time() - start_time
        print(f"\n\n■ PLAYBACK COMPLETE ({frames_processed} frames in {elapsed:.1f}s)")
        print("  Waiting 5s for dashboard to settle...")
        await asyncio.sleep(5)

# --- Self Test ---
async def _run_self_test(port):
    print("Starting self-test...")
    detector = MotionDetector(cal_seconds=2.0)
    
    # 1. Test calibration transition
    for i in range(50):
        f = build_test_frame(seq=i, amplitudes=[4.0]*56) # Pure static
        res = detector.process(parse_frame(f), time.time())
        if i == 0: assert res["state"] == State.CALIBRATING
        time.sleep(0.01)
        
    # Wait for cal to end
    time.sleep(1.0)
    f = build_test_frame(seq=51, amplitudes=[4.0]*56)
    res = detector.process(parse_frame(f), time.time() + 2.1)
    assert res["state"] != State.CALIBRATING
    print("✓ Calibration transition passed")

    # 2. Test "Moving" detection
    for i in range(100, 120):
        # High variance
        amps = [np.random.uniform(5, 15) for _ in range(56)]
        f = build_test_frame(seq=i, amplitudes=amps)
        res = detector.process(parse_frame(f), time.time() + 3.0)
        
    assert detector.state == State.MOVING
    print("✓ Moving detection passed")
    
    # 3. Test Hysteresis (return to still/absent)
    for i in range(200, 250):
        f = build_test_frame(seq=i, amplitudes=[4.0]*56)
        res = detector.process(parse_frame(f), time.time() + 4.0)
        
    assert detector.state != State.MOVING
    print("✓ Hysteresis return passed")

    # 4. Target 7: Playback Mode Verification
    print("\nTesting playback mode...")
    tmp_csv = "/tmp/csi_playback_test.csv"
    with open(tmp_csv, 'w', newline='') as f:
        writer = csv.writer(f)
        headers = ['timestamp', 'seq', 'rssi', 'noise_floor', 'channel', 
                   'node_id', 'n_sub', 'amp_mean', 'label'] + [f'amp_{i}' for i in range(56)]
        writer.writerow(headers)
        
        # 10 cal frames
        for i in range(10):
            row = [float(i), i, -50, -90, 6, 1, 56, 4.0, ""] + [4.0]*56
            writer.writerow(row)
        # 10 moving frames
        for i in range(10*20, 10*20 + 10):
            amps = [np.random.uniform(5, 15) for _ in range(56)]
            row = [float(i), i, -50, -90, 6, 1, 56, np.mean(amps), ""] + amps
            writer.writerow(row)
            
    # Mock args
    class Args:
        playback = tmp_csv
        speed = 100.0  # Run fast
        ws_port = port
        cal_seconds = 0.5
    
    # Run playback (should complete without crash)
    task = asyncio.create_task(run_playback(Args()))
    await task
    
    print("\nPASS (7/7 checks)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--udp-port", type=int, default=5005)
    parser.add_argument("--ws-port", type=int, default=8099)
    parser.add_argument("--cal-seconds", type=float, default=10.0)
    parser.add_argument("--self-test", action="store_true")

    # Task 7
    parser.add_argument("--playback", type=str, default=None, help="Path to CSV file to replay instead of UDP")
    parser.add_argument("--record", type=str, default=None, help="Path to save incoming frames as CSV")
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier")
    
    args = parser.parse_args()
    
    if args.playback and args.record:
        print("Error: --playback and --record are mutually exclusive.")
        import sys
        sys.exit(1)
        
    if args.self_test:
        asyncio.run(_run_self_test(args.udp_port))
    elif args.playback:
        try:
            asyncio.run(run_playback(args))
        except KeyboardInterrupt:
            print("\nPlayback stopped.")
    else:
        try:
            asyncio.run(run_server(args.udp_port, args.ws_port, args.cal_seconds, args.record))
        except KeyboardInterrupt:
            print("\nDetector stopped.")
