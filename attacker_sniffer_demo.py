import socket
import struct
import numpy as np
from csi_parser import parse_frame

# Configuration
UDP_IP = "0.0.0.0" # Listen on all interfaces
UDP_PORT = 5005    # Default ADR-018 protocol port

def start_sniffer():
    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))

    print("=" * 60)
    print(" SECURITY AUDIT: CSI PACKET SNIFFER (VULNERABILITY TEST) ")
    print(" Listening for unencrypted CSI traffic on port", UDP_PORT)
    print("=" * 60)

    try:
        while True:
            data, addr = sock.recvfrom(2048) # Max packet size
            
            # 1. Show "Raw" packet (as Wireshark would see it)
            hex_payload = data[:24].hex(' ') # First 24 bytes for brevity
            print(f"\n[INTERCEPTED] From: {addr[0]}")
            print(f"RAW HEX (First 24B): {hex_payload} ...")

            # 2. Attempt decoding (If unencrypted, it will work!)
            frame = parse_frame(data)
            
            if frame:
                print(f"    [INFO] Sequence:  {frame.seq}")
                print(f"    [INFO] RSSI:      {frame.rssi} dBm")
                print(f"    [INFO] Frequency: {frame.freq_mhz} MHz (Channel {frame.channel})")
                
                # Demonstrate we can see the physical signal
                avg_amp = np.mean(frame.amplitudes)
                print(f"    [CSI] Average Amplitude: {avg_amp:.4f}")
                print(f"    [SECURITY ALERT] CLEAR TEXT DATA DETECTED.")
            else:
                print(">>> Packet not recognized or potentially encrypted.")

    except KeyboardInterrupt:
        print("\nSniffer stopped.")
    finally:
        sock.close()

if __name__ == "__main__":
    start_sniffer()
