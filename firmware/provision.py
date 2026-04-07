#!/usr/bin/env python3
"""
ESP32-S3 CSI Node Provisioning Script

Writes WiFi credentials and aggregator target to the ESP32's NVS partition
so users can configure a pre-built firmware binary without recompiling.

Usage:
    python provision.py --port COM7 --ssid "MyWiFi" --password "secret" --target-ip 192.168.1.20

Requirements:
    pip install esptool esp-idf-nvs-partition-gen
    (or use the nvs_partition_gen.py bundled with ESP-IDF)
"""

import argparse
import csv
import io
import os
import struct
import subprocess
import sys
import tempfile


# NVS partition table offset — default for ESP-IDF 4MB flash with standard
# partition scheme.  The "nvs" partition starts at 0x9000 (36864) and is
# 0x6000 (24576) bytes.
NVS_PARTITION_OFFSET = 0x9000
NVS_PARTITION_SIZE = 0x6000  # 24 KiB


def build_nvs_csv(ssid, password, target_ip, target_port, node_id, aes_key_hex=None):
    """Build an NVS CSV string for the csi_cfg namespace."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["key", "type", "encoding", "value"])
    writer.writerow(["csi_cfg", "namespace", "", ""])
    if ssid:
        writer.writerow(["ssid", "data", "string", ssid])
    if password is not None:
        writer.writerow(["password", "data", "string", password])
    if target_ip:
        writer.writerow(["target_ip", "data", "string", target_ip])
    if target_port is not None:
        writer.writerow(["target_port", "data", "u16", str(target_port)])
    if node_id is not None:
        writer.writerow(["node_id", "data", "u8", str(node_id)])
    if aes_key_hex is not None:
        # hex2bin encoding: nvs_partition_gen converts the hex string to a binary blob
        writer.writerow(["aes_key", "data", "hex2bin", aes_key_hex])
    return buf.getvalue()



def _candidate_pythons():
    """Yield Python executables to try, in preference order."""
    # 1. Project-local venv (NS/venv) — has esp_idf_nvs_partition_gen installed
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_venv = os.path.join(script_dir, "venv", "bin", "python3")
    if os.path.isfile(local_venv):
        yield local_venv

    # 2. The Python running this script
    yield sys.executable

    # 3. System python3
    for name in ("python3", "python"):
        found = subprocess.run(
            ["which", name], capture_output=True, text=True
        ).stdout.strip()
        if found and found != sys.executable:
            yield found


def generate_nvs_binary(csv_content, size):
    """Generate an NVS partition binary from CSV.

    Tries each candidate Python with the esp_idf_nvs_partition_gen module
    until one succeeds.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f_csv:
        f_csv.write(csv_content)
        csv_path = f_csv.name

    bin_path = csv_path.replace(".csv", ".bin")

    try:
        last_err = None
        for python in _candidate_pythons():
            try:
                subprocess.check_call(
                    [python, "-m", "esp_idf_nvs_partition_gen.nvs_partition_gen",
                     "generate", csv_path, bin_path, hex(size)],
                    stderr=subprocess.DEVNULL,
                )
                with open(bin_path, "rb") as f:
                    return f.read()
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                last_err = e

        raise last_err or RuntimeError("No Python with esp_idf_nvs_partition_gen found")

    finally:
        for p in (csv_path, bin_path):
            if os.path.isfile(p):
                os.unlink(p)


def flash_nvs(port, baud, nvs_bin):
    """Flash the NVS partition binary to the ESP32."""
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(nvs_bin)
        bin_path = f.name

    try:
        cmd = [
            sys.executable, "-m", "esptool",
            "--chip", "esp32s3",
            "--port", port,
            "--baud", str(baud),
            "write_flash",
            hex(NVS_PARTITION_OFFSET), bin_path,
        ]
        print(f"Flashing NVS partition ({len(nvs_bin)} bytes) to {port}...")
        subprocess.check_call(cmd)
        print("NVS provisioning complete!")
    finally:
        os.unlink(bin_path)


def main():
    parser = argparse.ArgumentParser(
        description="Provision ESP32-S3 CSI Node with WiFi and aggregator settings",
        epilog="Example: python provision.py --port COM7 --ssid MyWiFi --password secret --target-ip 192.168.1.20",
    )
    parser.add_argument("--port", required=True, help="Serial port (e.g. COM7, /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=460800, help="Flash baud rate (default: 460800)")
    parser.add_argument("--ssid", help="WiFi SSID")
    parser.add_argument("--password", help="WiFi password")
    parser.add_argument("--target-ip", help="Aggregator host IP (e.g. 192.168.1.20)")
    parser.add_argument("--target-port", type=int, help="Aggregator UDP port (default: 5005)")
    parser.add_argument("--node-id", type=int, help="Node ID 0-255 (default: 1)")
    parser.add_argument(
        "--aes-key",
        help="AES-128 encryption key as 32 hex characters (e.g. 00112233445566778899aabbccddeeff). "
             "Generate one with: python -c \"import os; print(os.urandom(16).hex())\"",
    )
    parser.add_argument("--dry-run", action="store_true", help="Generate NVS binary but don't flash")

    args = parser.parse_args()

    if not any([args.ssid, args.password is not None, args.target_ip,
                args.target_port, args.node_id is not None,
                args.aes_key is not None]):
        parser.error("At least one config value must be specified "
                     "(--ssid, --password, --target-ip, --target-port, --node-id, --aes-key)")

    if args.aes_key is not None:
        aes_key_hex = args.aes_key.strip().lower().replace(" ", "")
        if len(aes_key_hex) != 32 or not all(c in "0123456789abcdef" for c in aes_key_hex):
            parser.error("--aes-key must be exactly 32 hex characters (16 bytes / AES-128)")
        args.aes_key = aes_key_hex

    print("Building NVS configuration:")
    if args.ssid:
        print(f"  WiFi SSID:     {args.ssid}")
    if args.password is not None:
        print(f"  WiFi Password: {'*' * len(args.password)}")
    if args.target_ip:
        print(f"  Target IP:     {args.target_ip}")
    if args.target_port:
        print(f"  Target Port:   {args.target_port}")
    if args.node_id is not None:
        print(f"  Node ID:       {args.node_id}")
    if args.aes_key is not None:
        print(f"  AES-128 key:   {args.aes_key[:8]}...{args.aes_key[-8:]} (AES-GCM enabled)")
    else:
        print("  AES-128 key:   (not set — frames will be sent unencrypted)")

    csv_content = build_nvs_csv(args.ssid, args.password, args.target_ip,
                                args.target_port, args.node_id, args.aes_key)

    try:
        nvs_bin = generate_nvs_binary(csv_content, NVS_PARTITION_SIZE)
    except Exception as e:
        print(f"\nError generating NVS binary: {e}", file=sys.stderr)
        print("\nFallback: save CSV and flash manually with ESP-IDF tools.", file=sys.stderr)
        fallback_path = "nvs_config.csv"
        with open(fallback_path, "w") as f:
            f.write(csv_content)
        print(f"Saved NVS CSV to {fallback_path}", file=sys.stderr)
        print(f"Flash with: python -m esp_idf_nvs_partition_gen.nvs_partition_gen generate "
              f"{fallback_path} nvs.bin 0x6000", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        out = "nvs_provision.bin"
        with open(out, "wb") as f:
            f.write(nvs_bin)
        print(f"NVS binary saved to {out} ({len(nvs_bin)} bytes)")
        print(f"Flash manually: python -m esptool --chip esp32s3 --port {args.port} "
              f"write_flash 0x9000 {out}")
        return

    flash_nvs(args.port, args.baud, nvs_bin)


if __name__ == "__main__":
    main()
