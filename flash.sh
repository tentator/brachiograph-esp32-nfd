#!/bin/bash
# Put the plotter on the ESP32: ./flash.sh [port or host]
# Plugged in by USB: MicroPython and the files. Otherwise: the files over Wi-Fi
# (needs "ota_password" in customization.json and a version with updates already on the board).
set -euo pipefail
cd "$(dirname "$0")"
files="rescue.py plotter.py ssd1306.py index.html customization.json main.py"  # main.py last: it needs the others
[[ -f customization.json ]] || { echo "customization.json missing: cp customization.example.json customization.json and fill it in" >&2; exit 1; }
python3 -c "import json; json.load(open('customization.json'))" || { echo "customization.json is not valid JSON, see the error above" >&2; exit 1; }
# Mistakes the board can't recover from by itself: it would start without Wi-Fi or with the wrong AP
python3 - <<'PY' || exit 1
import json, sys
c = json.load(open("customization.json"))
errors = ["networks entry %d needs \"ssid\" and \"password\"" % (i + 1)
          for i, n in enumerate(c.get("networks", [])) if not {"ssid", "password"} <= set(n)]
if "ap" in c and not 8 <= len(c["ap"].get("password", "plotterpassword")) <= 63:
    errors.append("the \"ap\" password needs 8 to 63 characters")
for e in errors:
    print("customization.json:", e, file=sys.stderr)
sys.exit(1 if errors else 0)
PY
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
  echo "USB ports found: ${ports:-none}"
fi

if [[ -n $target && -e $target ]]; then
  # Any ESP32 with Wi-Fi: esptool names the chip (ESP32-C3, ESP32-S3, ...); a classic ESP32
  # shows its package instead (ESP32-D0WD-V3, ESP32-PICO-D4, ESP32-S0WD), which leaves model empty
  info=$(esptool --port "$target" chip-id) || { echo "The ESP32 on $target does not answer" >&2; exit 1; }
  model=$(grep -oE 'ESP32-[CSHP][1-9][0-9]*' <<<"$info" | head -1 || true)
  variant=${model#ESP32-}
  case $variant in H*|P*) echo "$model has no Wi-Fi of its own; the plotter needs Wi-Fi" >&2; exit 1 ;; esac
  chip=esp32${variant,,}
  fw=ESP32_GENERIC${variant:+_$variant}-20260824-v1.29.0.bin
  case $variant in ""|S2) offset=0x1000 ;; C5) offset=0x2000 ;; *) offset=0x0 ;; esac  # where the bootloader goes
  echo "Found ${model:-a classic ESP32} on $target"
  [[ -f $fw ]] || curl -fLO "https://micropython.org/resources/firmware/$fw" \
    || { echo "No MicroPython $fw for ${model:-ESP32}" >&2; exit 1; }
  # Show what's on the board before erasing it: its files and the end of its log
  if before=$(timeout 20 mpremote connect "$target" fs ls 2>&1); then
    echo "Files on the ESP32 now:"; echo "$before"
    echo "Last lines of its log (erased with the flash):"
    timeout 20 mpremote connect "$target" exec "
try:
    print(''.join(open('log.txt').readlines()[-15:]), end='')
except OSError:
    print('(no log)')" || true
  else
    echo "No MicroPython on the ESP32 yet (or it does not answer): nothing to list"
  fi
  esptool --chip $chip --port "$target" erase-flash
  esptool --chip $chip --port "$target" --baud 460800 write-flash $offset "$fw"
  echo "Waiting for MicroPython's first-boot setup..."
  for try in $(seq 1 10); do
    mpremote connect "$target" exec pass >/dev/null 2>&1 && break
    [[ $try == 10 ]] && { echo "ESP32 does not answer on $target" >&2; exit 1; }
  done
  mpremote connect "$target" $(for f in $files; do printf 'cp %s :%s + ' "$f" "$f"; done) \
    exec "print('Files on the ESP32 now:')" + fs ls + reset
else
  pw=$(get ota_password)
  [[ -n $pw ]] || { echo "No ESP32 on USB, and no ota_password in customization.json for Wi-Fi" >&2; exit 1; }
  # Its hostname on your Wi-Fi, or 192.168.4.1 when this computer is on the board's own AP
  hn=$(get hostname)
  candidates=${1:-${hn:+$hn.local} 192.168.4.1}
  host=
  for h in $candidates; do
    echo "Looking for the ESP32 at $h..."
    curl -fs -o /dev/null -m 5 "http://$h/log" && { host=$h; break; }
  done
  if [[ -z $host ]]; then
    echo "No ESP32 found: not on USB, and no answer over Wi-Fi from $candidates." >&2
    echo "If it is on its own access point, connect this computer to that Wi-Fi first." >&2
    exit 1
  fi
  echo "Found the ESP32 at $host. Files on it now:"
  curl -fsS -m 5 "http://$host/files" || echo "(its version can't list files yet)"
  echo "Updating $host over Wi-Fi"
  for f in $files; do
    for try in 1 2 3; do
      reply=$(curl -fsS -u "ota:$pw" -T "$f" "http://$host/file/$f") && break
      [[ $try == 3 ]] && { echo "$f: upload failed" >&2; exit 1; }
    done
    echo "$f: $reply"
  done
  curl -fsS -u "ota:$pw" -X POST "http://$host/reset" >/dev/null
  # /log answers in the plotter and in the rescue server (which gives /status a 503)
  for _ in $(seq 1 20); do curl -fs -o /dev/null -m 1 "http://$host/log" || break; done  # until it restarts
  if curl -fs -o /dev/null -m 3 --retry 30 --retry-delay 1 --retry-all-errors "http://$host/log"; then
    echo "$host is back. Files on it now:"
    curl -fsS -m 5 "http://$host/files" || true
    echo "Last lines of its log:"
    curl -fsS -m 5 "http://$host/log" | tail -n 8
  else
    echo "$host did not come back within 30 s: it may have joined another network or opened its own AP" >&2
    exit 1
  fi
fi
