# Firmware & Provisioning

The ESP32-S3 firmware (`esp32-csi-node/`) captures WiFi Channel State Information (CSI) and streams binary frames over UDP to the aggregator host at ~100 Hz.

Firmware source: [RuView/RuView](https://github.com/RuView/RuView).  
Supports both plaintext (**ADR-018**, magic `0xC5110001`) and AES-128-GCM encrypted (**ADR-018-enc**, magic `0xC5110003`) output modes.

## Contents

| Path | Description |
|------|-------------|
| `esp32-csi-node/` | ESP-IDF v5.2 firmware project — build and flash this to the ESP32-S3 |
| `provision.py` | Write WiFi credentials and AES key to NVS without recompiling |

## Option A — Provision a pre-built binary

If you have a pre-built `esp32-csi-node.bin`, use `provision.py` to write WiFi credentials (and optionally an AES-128 key) to the device's NVS partition over serial:

```bash
python provision.py \
    --port /dev/ttyACM0 \
    --ssid "MyWiFi" \
    --password "mypassword" \
    --target-ip 192.168.1.20 \
    --target-port 5005 \
    --aes-key <your-32-hex-char-key>   # omit for plaintext mode
```

Generate a fresh AES-128 key:
```bash
python -c "import os; print(os.urandom(16).hex())"
```

Dry run (generate the NVS binary without flashing, for inspection):
```bash
python provision.py --port /dev/ttyACM0 --ssid "MyWiFi" \
    --target-ip 192.168.1.20 --dry-run
```

Requirements:
```bash
pip install esptool esp-idf-nvs-partition-gen
```

## Option B — Build from source

Full build instructions (ESP-IDF installation, CMakeLists configuration, partition table, flash commands) are in [`../txt/about_flash_procedure.txt`](../txt/about_flash_procedure.txt).

Short version (after activating the ESP-IDF environment):

```bash
cd esp32-csi-node
rm -rf build sdkconfig
idf.py set-target esp32s3
idf.py build
```

Flash:
```bash
esptool --chip esp32s3 --port /dev/ttyACM0 --baud 460800 \
  write-flash --flash-mode dio --flash-size 8MB \
  0x0     build/bootloader/bootloader.bin \
  0x8000  build/partition_table/partition-table.bin \
  0x20000 build/esp32-csi-node.bin
```

Then run `provision.py` as above to set WiFi credentials.

## Serial monitor

```bash
python -m serial.tools.miniterm /dev/ttyACM0 115200
```

Expected boot output (encrypted mode):
```
I (xxx) main: ESP32-S3 CSI Node — Node ID: 1
I (xxx) main: AES-GCM encryption enabled — frames sent as ADR-018-enc
I (xxx) main: Connected to WiFi
I (xxx) main: CSI streaming active → 192.168.1.93:5005
```
