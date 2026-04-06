#!/usr/bin/env python3
"""CSI Experiment Recorder — capture labeled idle/movement data."""

import argparse
import csv
import os
import socket
import sys
import threading
import time
from datetime import datetime
from csi_parser import parse_frame, build_test_frame, CsiFrame

def print_banner():
    print("\n╔" + "═" * 54 + "╗")
    print("║" + "  CSI Experiment Recorder".ljust(54) + "║")
    print("║" + "  This will record idle + movement CSI data.".ljust(54) + "║")
    print("╚" + "═" * 54 + "╝\n")

def record_phase(sock: socket.socket, duration: float, label: str, writer, n_cols: int) -> int:
    """Record frames for `duration` seconds, writing rows to CSV."""
    start_time = time.time()
    frame_count = 0
    bar_len = 20
    
    while True:
        now = time.time()
        elapsed = now - start_time
        if elapsed >= duration:
            break
            
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
            
        frame = parse_frame(data)
        if frame is None:
            continue
            
        frame_count += 1
        
        # Build row
        # timestamp,seq,rssi,noise_floor,channel,node_id,n_sub,amp_mean,label,amp_0...
        row = [
            now,
            frame.seq,
            frame.rssi,
            frame.noise_floor,
            frame.channel,
            frame.node_id,
            frame.n_subcarriers,
            round(frame.amplitude_mean, 4),
            label
        ]
        
        # Amp columns
        amps = list(frame.amplitudes)
        if len(amps) > n_cols:
            amps = amps[:n_cols]
        elif len(amps) < n_cols:
            amps += [0.0] * (n_cols - len(amps))
            
        row.extend([round(a, 4) for a in amps])
        writer.writerow(row)
        
        # Progress bar
        filled = int(bar_len * elapsed / duration)
        bar = '█' * filled + '░' * (bar_len - filled)
        sys.stdout.write(f'\r  Recording... {bar} {elapsed:.0f}/{duration:.0f}s ({frame_count} frames)')
        sys.stdout.flush()
        
    print(f"\r  Recording complete! {bar_len*'█'} {duration:.0f}/{duration:.0f}s ({frame_count} frames)")
    return frame_count

def run_experiment(port: int, idle_sec: float, move_sec: float, output: str):
    # Ensure directory
    os.makedirs(os.path.dirname(output), exist_ok=True)
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(0.1)
    
    print_banner()
    
    # Get first frame to determine column count
    print("Waiting for first ESP32 frame to detect subcarrier count...")
    first_frame = None
    while first_frame is None:
        try:
            data, addr = sock.recvfrom(4096)
            first_frame = parse_frame(data)
        except (socket.timeout, KeyboardInterrupt):
            if isinstance(sys.exc_info()[0], KeyboardInterrupt):
                return
            continue
            
    n_subs = first_frame.n_subcarriers
    print(f"✓ Detected {n_subs} subcarriers. Output file: {output}")
    
    # Setup CSV
    with open(output, 'w', newline='') as f:
        writer = csv.writer(f)
        header = ["timestamp", "seq", "rssi", "noise_floor", "channel", "node_id", "n_subs", "amp_mean", "label"]
        header.extend([f"amp_{i}" for i in range(n_subs)])
        writer.writerow(header)
        
        try:
            # Phase 1: IDLE
            print(f"\nPhase 1: IDLE ({idle_sec} seconds)")
            print("  Please leave the room or sit completely still.")
            input("  Press ENTER when ready...")
            count_idle = record_phase(sock, idle_sec, "idle", writer, n_subs)
            print("  ✓ Idle phase complete.")
            
            # Phase 2: MOVEMENT
            print(f"\nPhase 2: MOVEMENT ({move_sec} seconds)")
            print("  Now walk around the room normally.")
            input("  Press ENTER when ready...")
            count_move = record_phase(sock, move_sec, "movement", writer, n_subs)
            print("  ✓ Movement phase complete.")
            
            print(f"\nPhase 3: SAVE")
            print(f"  Saved {count_idle + count_move} frames to {output}")
            print(f"  Summary:")
            print(f"    Idle frames:     {count_idle}")
            print(f"    Movement frames: {count_move}")
            
        except KeyboardInterrupt:
            print("\n\nRecording interrupted. File partially saved.")
        finally:
            sock.close()

def _send_synthetic_experiment(port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = ("127.0.0.1", port)
    
    # 100 idle frames
    for i in range(100):
        # I=Q=3 -> amp=4.24
        frame = build_test_frame(seq=i, amplitudes=[4.24]*56)
        sock.sendto(frame, addr)
        time.sleep(0.01)
        
    # 100 move frames
    for i in range(100, 200):
        # random amp between 10-20
        import random
        amps = [random.uniform(10, 20) for _ in range(56)]
        frame = build_test_frame(seq=i, amplitudes=amps)
        sock.sendto(frame, addr)
        time.sleep(0.01)
        
    sock.close()

def run_self_test(port: int):
    print("Running self-test...")
    output = "/tmp/csi_self_test.csv"
    
    # Background sender
    sender = threading.Thread(target=_send_synthetic_experiment, args=(port,))
    sender.start()
    
    # Run a miniature headless version of the experiment
    os.makedirs(os.path.dirname(output), exist_ok=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))
    sock.settimeout(1.0)
    
    try:
        data, addr = sock.recvfrom(4096)
        first = parse_frame(data)
        n_subs = first.n_subcarriers
        
        with open(output, 'w', newline='') as f:
            writer = csv.writer(f)
            header = ["timestamp", "seq", "rssi", "noise_floor", "channel", "node_id", "n_subs", "amp_mean", "label"]
            header.extend([f"amp_{i}" for i in range(n_subs)])
            writer.writerow(header)
            
            # Just record 200 frames total automatically
            record_phase(sock, 1.2, "idle", writer, n_subs)
            record_phase(sock, 1.2, "movement", writer, n_subs)
            
        # Verify
        with open(output, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
        checks = 0
        if len(rows) >= 150: # Expect ~200, give some slack
            print(f"✓ Recorded {len(rows)} frames")
            checks += 1
            
        if rows[0]['label'] == 'idle' and rows[-1]['label'] == 'movement':
            print("✓ Labels applied correctly")
            checks += 1
            
        if 'amp_0' in rows[0] and 'amp_55' in rows[0]:
            print("✓ CSV schema correct")
            checks += 1
            
        if float(rows[0]['amp_mean']) < float(rows[-1]['amp_mean']):
            print("✓ Moving data has higher amplitude mean (as expected)")
            checks += 1
            
        if checks == 4:
            print("\nPASS (4/4)")
            exit(0)
        else:
            print(f"\nFAIL ({checks}/4)")
            exit(1)
            
    finally:
        sock.close()
        sender.join()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CSI Experiment Recorder")
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--idle", type=float, default=15.0)
    parser.add_argument("--move", type=float, default=15.0)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--self-test", action="store_true")
    
    args = parser.parse_args()
    
    if args.self_test:
        run_self_test(args.port)
    else:
        if args.output is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            args.output = f"recordings/experiment_{ts}.csv"
        run_experiment(args.port, args.idle, args.move, args.output)
