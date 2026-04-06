#!/usr/bin/env python3
"""CSI Probe — validate raw ESP32 CSI data from UDP."""

import argparse
import math
import socket
import threading
import time
from csi_parser import parse_frame, build_test_frame, CsiFrame

class WelfordStats:
    """Computes online mean, variance, and standard deviation."""
    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.min_val = float('inf')
        self.max_val = float('-inf')

    def update(self, x: float):
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        delta2 = x - self.mean
        self.m2 += delta * delta2
        if x < self.min_val:
            self.min_val = x
        if x > self.max_val:
            self.max_val = x

    @property
    def variance(self) -> float:
        if self.n < 2:
            return 0.0
        return self.m2 / (self.n - 1)

    @property
    def std_dev(self) -> float:
        return math.sqrt(self.variance)

class ProbeStats:
    def __init__(self):
        self.received = 0
        self.dropped = 0
        self.rssi = WelfordStats()
        self.amp_mean = WelfordStats()
        self.last_seq = None
        self.gap_count = 0
        self.last_n_sub = None
        self.consistent_subs = True
        self.channel = None

    def update(self, frame: CsiFrame):
        self.received += 1
        
        # Sequence gaps
        if self.last_seq is not None:
            diff = frame.seq - self.last_seq
            if diff > 1 and diff < 10000:
                self.gap_count += (diff - 1)
        self.last_seq = frame.seq

        # Stats
        self.rssi.update(frame.rssi)
        self.amp_mean.update(frame.amplitude_mean)

        # Consistency
        if self.last_n_sub is not None and frame.n_subcarriers != self.last_n_sub:
            self.consistent_subs = False
        self.last_n_sub = frame.n_subcarriers
        
        self.channel = frame.channel

def run_listener(port: int, duration: float | None, is_self_test: bool = False, quiet: bool = False):
    """Main UDP receive loop. Returns stats (used by self-test)."""
    stats = ProbeStats()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Bind to 127.0.0.1 for self-test to avoid firewall prompts/conflicts, otherwise 0.0.0.0
    bind_ip = "127.0.0.1" if is_self_test else "0.0.0.0"
    sock.bind((bind_ip, port))
    sock.settimeout(1.0)
    
    start_time = time.time()
    last_print = start_time
    last_summary = start_time
    frames_since_summary = 0
    
    try:
        if not quiet:
            print(f"Listening on UDP {bind_ip}:{port}...")
            
        while True:
            now = time.time()
            elapsed = now - start_time
            if duration is not None and elapsed >= duration:
                break
                
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except KeyboardInterrupt:
                break
                
            frame = parse_frame(data)
            if frame is None:
                stats.dropped += 1
                continue
                
            stats.update(frame)
            frames_since_summary += 1
            
            # Throttled per-frame output (max 4 per sec)
            if not quiet and now - last_print >= 0.25:
                amp_min = float(frame.amplitudes.min())
                amp_max = float(frame.amplitudes.max())
                print(f"#{frame.seq:<5} rssi:{frame.rssi}dBm  subs:{frame.n_subcarriers}  "
                      f"amp:[{amp_min:5.2f}-{amp_max:5.2f}] μ={frame.amplitude_mean:5.2f}  "
                      f"ch:{frame.channel}  node:{frame.node_id}")
                last_print = now
                
            # Summary output (every 5 seconds)
            if not quiet and now - last_summary >= 5.0 and stats.received > 0:
                hz = frames_since_summary / (now - last_summary)
                print(f"\n──── {time.strftime('%H:%M:%S')} (elapsed: {elapsed:.1f}s) ────")
                print(f"  Rate:    {hz:.1f} Hz ({stats.received} frames, {stats.dropped} dropped)")
                print(f"  Gaps:    {stats.gap_count} (missing seq numbers)")
                print(f"  RSSI:    μ={stats.rssi.mean:.1f} σ={stats.rssi.std_dev:.1f} "
                      f"[{stats.rssi.min_val}..{stats.rssi.max_val}] dBm")
                print(f"  Amp:     μ={stats.amp_mean.mean:.2f} σ={stats.amp_mean.std_dev:.2f}")
                print(f"  Subs:    {stats.last_n_sub} (consistent: {'YES' if stats.consistent_subs else 'NO'})")
                print(f"  Channel: {stats.channel}")
                print(f"────────────────────────────────────\n")
                last_summary = now
                frames_since_summary = 0
                stats.consistent_subs = True # Reset consistency checker for the next window
                
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        
    return stats

def _send_synthetic_frames(port: int):
    """Sends 200 synthetic frames to localhost via UDP."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = ("127.0.0.1", port)
    
    # Send seq 0..99
    for i in range(100):
        frame = build_test_frame(seq=i, rssi=-45, n_sub=56, freq_mhz=2437)
        sock.sendto(frame, addr)
        time.sleep(0.01)
        
    # Introduce gap: skip seq 100
    
    # Send seq 101..199
    for i in range(101, 200):
        frame = build_test_frame(seq=i, rssi=-45, n_sub=56, freq_mhz=2437)
        sock.sendto(frame, addr)
        time.sleep(0.01)
        
    sock.close()

def run_self_test(port: int):
    """Send synthetic frames and verify reception."""
    print("Starting self-test...")
    
    # Start sender in background
    sender_thread = threading.Thread(target=_send_synthetic_frames, args=(port,))
    sender_thread.start()
    
    # Run listener for a fixed duration (slightly longer than it takes to send)
    # sending 200 frames at 10ms = 2.0s. Give it 2.5s to be safe.
    stats = run_listener(port, duration=2.5, is_self_test=True, quiet=True)
    sender_thread.join()
    
    print("\n=== SELF-TEST RESULTS ===")
    
    checks_passed = 0
    total_checks = 5
    
    # Check 1: Frames received
    recv_expected = 199
    if stats.received == recv_expected:
        print(f"Frames received: {stats.received}/{recv_expected} (expect {recv_expected}) \t→ ✓")
        checks_passed += 1
    else:
        print(f"Frames received: {stats.received}/{recv_expected} (expect {recv_expected}) \t→ ✗")
        
    # Check 2: Gaps detected
    gap_expected = 1
    if stats.gap_count == gap_expected:
        print(f"Gaps detected:   {stats.gap_count} (expect {gap_expected}) \t\t→ ✓")
        checks_passed += 1
    else:
        print(f"Gaps detected:   {stats.gap_count} (expect {gap_expected}) \t\t→ ✗")
        
    # Check 3: RSSI mean
    if stats.received > 0 and abs(stats.rssi.mean - (-45.0)) < 0.1:
        print(f"RSSI mean:       {stats.rssi.mean:.1f} (expect -45.0) \t→ ✓")
        checks_passed += 1
    else:
        mean_rssi = stats.rssi.mean if stats.received > 0 else 'N/A'
        print(f"RSSI mean:       {mean_rssi} (expect -45.0) \t→ ✗")
        
    # Check 4: Subcarrier count
    if stats.last_n_sub == 56 and stats.consistent_subs:
        print(f"Subcarrier count: {stats.last_n_sub} (expect 56) \t\t→ ✓")
        checks_passed += 1
    else:
        print(f"Subcarrier count: {stats.last_n_sub} (expect 56) \t\t→ ✗")
        
    # Check 5: Channel
    if stats.channel == 6:
        print(f"Channel:         {stats.channel} (expect 6) \t\t→ ✓")
        checks_passed += 1
    else:
        print(f"Channel:         {stats.channel} (expect 6) \t\t→ ✗")
        
    print("========================")
    if checks_passed == total_checks:
        print(f"PASS ({checks_passed}/{total_checks})")
        exit(0)
    else:
        print(f"FAIL ({checks_passed}/{total_checks})")
        exit(1)

def main():
    parser = argparse.ArgumentParser(description="CSI Probe — validate raw ESP32 CSI data from UDP.")
    parser.add_argument("--port", type=int, default=5005, help="UDP port to listen on")
    parser.add_argument("--duration", type=float, default=None, help="Stop after N seconds")
    parser.add_argument("--self-test", action="store_true", help="Run self-test with synthetic frames")
    
    args = parser.parse_args()
    
    if args.self_test:
        run_self_test(args.port)
    else:
        run_listener(args.port, args.duration)

if __name__ == "__main__":
    main()
