#!/bin/bash
# Put the plotter on the ESP32: ./flash.sh [port or host]
# Plugged in by USB: MicroPython and the files. Otherwise: the files over Wi-Fi
# (needs "ota_password" in customization.json and a version with updates already on the board).
set -euo pipefail
cd "$(dirname "$0")"
files="main.py ssd1306.py index.html customization.json"
[[ -f customization.json ]] || { echo "customization.json missing: cp customization.example.json customization.json and fill it in" >&2; exit 1; }
get() { python3 -c "import json, sys; print(json.load(open('customization.json')).get(sys.argv[1], ''))" "$1"; }
target=${1:-/dev/ttyUSB0}

if [[ -e $target ]]; then
  fw=ESP32_GENERIC-20260824-v1.29.0.bin
  [[ -f $fw ]] || curl -fLO "https://micropython.org/resources/firmware/$fw"
  esptool --chip esp32 --port "$target" erase-flash
  esptool --chip esp32 --port "$target" --baud 460800 write-flash 0x1000 "$fw"
  echo "Waiting for MicroPython's first-boot setup..."
  for try in $(seq 1 10); do
    mpremote connect "$target" exec pass >/dev/null 2>&1 && break
    [[ $try == 10 ]] && { echo "ESP32 does not answer on $target" >&2; exit 1; }
  done
  mpremote connect "$target" $(for f in $files; do printf 'cp %s :%s + ' "$f" "$f"; done) reset
else
  host=${1:-$(get hostname).local}
  pw=$(get ota_password)
  [[ -n $pw ]] || { echo "No USB on $target, and no ota_password in customization.json for Wi-Fi" >&2; exit 1; }
  echo "Updating $host over Wi-Fi"
  for f in $files; do
    reply=$(curl -fsS -u "ota:$pw" -T "$f" "http://$host/file/$f")
    echo "$f: $reply"
  done
  curl -fsS -u "ota:$pw" -X POST "http://$host/reset" >/dev/null
  for _ in $(seq 1 20); do curl -fs -o /dev/null -m 1 "http://$host/status" || break; done  # until it restarts
  curl -fs -o /dev/null -m 3 --retry 30 --retry-delay 1 --retry-all-errors "http://$host/status" && echo "$host is back"
fi
