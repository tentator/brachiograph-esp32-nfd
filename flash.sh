#!/bin/bash
# Put the plotter on the ESP32: ./flash.sh [port or host]
# Plugged in by USB: MicroPython and the files. Otherwise: the files over Wi-Fi
# (needs "ota_password" in customization.json and a version with updates already on the board).
set -euo pipefail
cd "$(dirname "$0")"
files="rescue.py plotter.py ssd1306.py index.html customization.json main.py"  # main.py last: it needs the others
[[ -f customization.json ]] || { echo "customization.json missing: cp customization.example.json customization.json and fill it in" >&2; exit 1; }
python3 -c "import json; json.load(open('customization.json'))" || { echo "customization.json is not valid JSON, see the error above" >&2; exit 1; }
get() { python3 -c "import json, sys; print(json.load(open('customization.json')).get(sys.argv[1], ''))" "$1"; }
# ESP32-C3 boards show up as /dev/ttyACM*, classic ESP32 boards with a USB-serial chip as /dev/ttyUSB*
# The port can be given in full (/dev/ttyACM1) or short (ttyACM1, ACM1, USB0)
if [[ -n ${1:-} ]]; then
  target=$1
  for dev in "/dev/$1" "/dev/tty$1"; do [[ $1 != /* && -e $dev ]] && { target=$dev; break; }; done
  if [[ $target =~ ^(/dev/|tty|ACM|USB) && ! -e $target ]]; then
    echo "No ESP32 on $target: is it plugged in? Ports found: $(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null | xargs || true)" >&2; exit 1
  fi
else
  ports=$(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true)
  if (( $(wc -w <<<"$ports") > 1 )); then
    echo "Several USB ports found, pick one: ./flash.sh <port>" >&2; echo "$ports" >&2; exit 1
  fi
  target=$ports
fi

if [[ -n $target && -e $target ]]; then
  # Captured first: "| grep -q" would stop reading early and fail the pipeline under pipefail
  info=$(esptool --port "$target" chip-id) || { echo "The ESP32 on $target does not answer" >&2; exit 1; }
  case $info in
    *ESP32-C3*) chip=esp32c3 fw=ESP32_GENERIC_C3-20260824-v1.29.0.bin offset=0x0 ;;
    *ESP32-S*|*ESP32-C*|*ESP32-H*|*ESP32-P*) echo "Only ESP32-C3 and classic ESP32 boards are supported" >&2; exit 1 ;;
    *) chip=esp32 fw=ESP32_GENERIC-20260824-v1.29.0.bin offset=0x1000 ;;
  esac
  [[ -f $fw ]] || curl -fLO "https://micropython.org/resources/firmware/$fw"
  esptool --chip $chip --port "$target" erase-flash
  esptool --chip $chip --port "$target" --baud 460800 write-flash $offset "$fw"
  echo "Waiting for MicroPython's first-boot setup..."
  for try in $(seq 1 10); do
    mpremote connect "$target" exec pass >/dev/null 2>&1 && break
    [[ $try == 10 ]] && { echo "ESP32 does not answer on $target" >&2; exit 1; }
  done
  mpremote connect "$target" $(for f in $files; do printf 'cp %s :%s + ' "$f" "$f"; done) reset
else
  host=${1:-$(get hostname).local}
  pw=$(get ota_password)
  [[ -n $pw ]] || { echo "No ESP32 on USB, and no ota_password in customization.json for Wi-Fi" >&2; exit 1; }
  echo "Updating $host over Wi-Fi"
  for f in $files; do
    for try in 1 2 3; do
      reply=$(curl -fsS -u "ota:$pw" -T "$f" "http://$host/file/$f") && break
      [[ $try == 3 ]] && { echo "$f: upload failed" >&2; exit 1; }
    done
    echo "$f: $reply"
  done
  curl -fsS -u "ota:$pw" -X POST "http://$host/reset" >/dev/null
  for _ in $(seq 1 20); do curl -fs -o /dev/null -m 1 "http://$host/status" || break; done  # until it restarts
  curl -fs -o /dev/null -m 3 --retry 30 --retry-delay 1 --retry-all-errors "http://$host/status" && echo "$host is back"
fi
