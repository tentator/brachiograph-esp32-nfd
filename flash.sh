#!/bin/bash
# Put the plotter on an ESP32, by USB or over Wi-Fi. ./flash.sh --help for the options.
#
# By USB: finds the ESP32 among the serial ports, installs MicroPython only if it's missing
# (or with --firmware), and copies the plotter's files. Your calibration (config.json) stays.
# Over Wi-Fi (no ESP32 on USB, --wifi or a host name): finds the plotter, checks the update
# password and uploads the files. Needs "ota_password" in customization.json.
set -euo pipefail
cd "$(dirname "$0")"

MP_VERSION=v1.29.0          # MicroPython installed on new boards
MP_DATE=20260824            # its build date, part of the firmware file name
COMPILED="rescue plotter ssd1306"   # precompiled to .mpy when possible: less RAM on the board
# main.py goes last: it starts everything else, so a cut-off update can't start half the files
RAW="index.html customization.defaults.json"

usage() {
  cat <<EOF
Usage: ./flash.sh [options] [port | host]

  port         serial port of the ESP32: /dev/ttyACM0, ttyUSB0, ACM0 ... (default: found by itself)
  host         update over Wi-Fi at this address, e.g. plotter.local or 192.168.4.1

  -l, --list   show the ESP32s found on USB and Wi-Fi, change nothing
  --firmware   (re)install MicroPython by USB; erases the flash, config.json is kept
  --fw FILE    MicroPython firmware file to install (default: downloaded for the chip found)
  --wifi       update over Wi-Fi even if an ESP32 is plugged in
  --py         upload .py sources instead of precompiled .mpy files
  -y, --yes    don't ask before erasing or uploading (keeps the board's own settings)
  -h, --help   this help
EOF
}

die() { echo "flash.sh: $*" >&2; exit 1; }
say() { echo "==> $*"; }
YES=0 GO=0
ask() {
  [[ $YES == 1 || $GO == 1 ]] && return 0
  [[ -t 0 ]] || die "$1 Run with --yes to go ahead without asking."
  local a; read -r -p "$1 [y/N] " a; [[ $a == [yYjJ]* ]]
}

LIST=0 FIRMWARE=0 FW_FILE= WIFI=0 PY=0 TARGET=
while (( $# )); do
  case $1 in
    -l|--list) LIST=1 ;;
    --firmware) FIRMWARE=1 ;;
    --fw) FW_FILE=${2:?--fw needs a file}; FIRMWARE=1; shift ;;
    --wifi) WIFI=1 ;;
    --py) PY=1 ;;
    -y|--yes) YES=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) usage >&2; die "unknown option $1" ;;
    *) [[ -z $TARGET ]] || die "only one port or host, got '$TARGET' and '$1'"; TARGET=$1 ;;
  esac
  shift
done
[[ -z $FW_FILE || -f $FW_FILE ]] || die "firmware file $FW_FILE not found"

# ---------- customization.defaults.json, overridden by customization.json (optional) ----------
custom() { python3 -c "import json, os, sys
c = json.load(open('customization.defaults.json'))
if os.path.exists('customization.json'):
    c.update(json.load(open('customization.json')))
print(c.get(sys.argv[1], ''))" "$1"; }
if [[ -f customization.json ]]; then
  # Mistakes the board can't fix by itself: it would start without your Wi-Fi or with the wrong pins
  python3 - <<'PY' || die "fix customization.json first (see customization.defaults.json)"
import json, sys
d = json.load(open("customization.defaults.json"))
try:
    c = dict(d, **json.load(open("customization.json")))
except ValueError as e:
    sys.exit("customization.json is not valid JSON: %s" % e)
errors = ["networks entry %d needs \"ssid\" and \"password\"" % (i + 1)
          for i, n in enumerate(c.get("networks", [])) if not {"ssid", "password"} <= set(n)]
if "ap" in c and not 8 <= len(c["ap"].get("password", "plotterpassword")) <= 63:
    errors.append("the \"ap\" password needs 8 to 63 characters")
if c.get("arms", "3d-printed") not in ("wooden", "3d-printed"):
    errors.append("\"arms\" must be \"wooden\" or \"3d-printed\"")
if "oled" in c and not isinstance(c["oled"], bool):
    errors.append("\"oled\" must be true or false")
led = c.get("led")
if led not in (None, False) and not (isinstance(led, dict) and isinstance(led.get("pin"), int)):
    errors.append("\"led\" must be false or like {\"pin\": 8, \"active_low\": true}")
if not all(isinstance(v, int) for v in c.get("pins", {}).values()):
    errors.append("\"pins\" values must be GPIO numbers")
if "txpower" in c and not (isinstance(c["txpower"], (int, float)) and 2 <= c["txpower"] <= 20):
    errors.append("\"txpower\" must be a number of dBm from 2 to 20 (e.g. 8.5)")
if "mac_suffix" in c and not isinstance(c["mac_suffix"], bool):
    errors.append("\"mac_suffix\" must be true or false")
errors += ["\"%s\" must be a number" % k for k, v in d.items()
           if type(v) is int and not (isinstance(c[k], (int, float)) and not isinstance(c[k], bool))]
for e in errors:
    print("customization.json:", e, file=sys.stderr)
sys.exit(1 if errors else 0)
PY
else
  echo "No customization.json here. The board keeps the one it already has, if any; without one it"
  echo "opens its own Wi-Fi 'ESP32-Plotter-<xx>' (password plotterpassword) and can't be updated over Wi-Fi."
  echo "To set your Wi-Fi: cp customization.defaults.json customization.json and fill it in."
fi

# The local customization.json replaces the board's own settings (customization.json, config.json)
# only when typed y; then config.json goes too, so the calibration starts from the defaults again
settings() {  # $1: the board's file list; sets FILES_EXTRA and OVERWRITE
  FILES_EXTRA= OVERWRITE=0
  [[ -f customization.json ]] || return 0
  local own a; own=$(grep -owE 'customization\.json|config\.json' <<<"$1" | sort -u | xargs || true)
  if [[ -n $own ]]; then
    echo "The ESP32 has its own settings: $own."
    if [[ $YES == 1 || ! -t 0 ]]; then echo "Keeping them (answer y without --yes to replace them)."; return 0; fi
    read -r -p "Replace them with your customization.json? The board's calibration is lost. [y/N] " a
    [[ $a == [yYjJ]* ]] || { echo "Keeping the board's settings."; return 0; }
    OVERWRITE=1
  fi
  FILES_EXTRA=customization.json
}

# One question up front, then no more: a board without MicroPython (new or stuck) gets it, which
# erases it; that's never done on --yes alone
if [[ $LIST == 0 && $YES == 0 ]]; then
  echo "This puts the plotter on your ESP32: by USB, or over Wi-Fi if none is plugged in."
  echo "A board without MicroPython (new, or its program stuck) gets it installed: that ERASES the board."
  ask "Go ahead?" || die "nothing changed"
  GO=1
fi
REV=$(grep -oE '^REVISION = "[^"]+"' plotter.py | cut -d'"' -f2)

# ---------- tools ----------
tool() {  # first of: the command itself, or python3 -m <module>
  if command -v "$1" >/dev/null; then echo "$1"
  elif python3 -c "import $2" 2>/dev/null; then echo "python3 -m $2"
  fi
}
ESPTOOL=$(tool esptool esptool); [[ -n $ESPTOOL ]] || ESPTOOL=$(tool esptool.py esptool)
MPREMOTE=$(tool mpremote mpremote)
MPYCROSS=$(tool mpy-cross mpy_cross)
need() { [[ -n $1 ]] || die "$2 not found: pip install --user -r requirements.txt"; }
# esp <command> [options] [-- arguments]: options go before the command, arguments after it.
# esptool 5 names its commands chip-id, erase-flash, write-flash; esptool 4 chip_id, ...
esp() {
  local cmd=$1 opts=(); shift
  while (( $# )) && [[ $1 != -- ]]; do opts+=("$1"); shift; done
  [[ ${1:-} == -- ]] && shift
  if [[ $($ESPTOOL version 2>/dev/null | grep -oE 'v[0-9]+' | head -1) =~ ^v[0-4]$ ]]; then cmd=${cmd//-/_}; fi
  $ESPTOOL "${opts[@]}" "$cmd" "$@"
}

# ---------- finding the ESP32 on USB ----------
# USB IDs of the usual ESP32 USB connections
describe() {  # vendor:product -> what it is, empty if it isn't a usual ESP32 adapter
  case $1 in
    303a:*) echo "Espressif built-in USB (ESP32-C3/S3/C6 ...)" ;;
    10c4:ea60) echo "CP210x USB-serial (ESP32 DevKit)" ;;
    1a86:7523|1a86:55d4) echo "CH340/CH9102 USB-serial (ESP32 DevKit)" ;;
    0403:6001|0403:6010|0403:6015) echo "FTDI USB-serial" ;;
  esac
}
usb_id() {  # port -> vendor:product (Linux), empty if unknown
  local v p
  if command -v udevadm >/dev/null; then
    v=$(udevadm info -q property -n "$1" 2>/dev/null | sed -n 's/^ID_VENDOR_ID=//p')
    p=$(udevadm info -q property -n "$1" 2>/dev/null | sed -n 's/^ID_MODEL_ID=//p')
  fi
  [[ -n ${v:-} ]] && echo "$v:$p"
}
PORTS=() DESCS=()
scan_usb() {
  local dev id d
  for dev in /dev/ttyACM* /dev/ttyUSB*; do
    [[ -e $dev ]] || continue
    id=$(usb_id "$dev" || true)
    d=$(describe "${id:-}")
    if [[ -n $d ]]; then PORTS+=("$dev"); DESCS+=("$d")
    elif [[ -z $id ]]; then PORTS+=("$dev"); DESCS+=("unknown USB device")  # no udev: can't tell
    else echo "    (skipping $dev: USB $id is not a usual ESP32 adapter)"
    fi
  done
}
pick_port() {  # sets PORT from TARGET or the ports found; asks if there are several
  if [[ -n $TARGET ]]; then
    local dev
    for dev in "$TARGET" "/dev/$TARGET" "/dev/tty$TARGET"; do [[ -e $dev ]] && { PORT=$dev; return; }; done
    die "no serial port $TARGET. Found: ${PORTS[*]:-none}"
  fi
  case ${#PORTS[@]} in
    0) PORT= ;;
    1) PORT=${PORTS[0]} ;;
    *)
      echo "Several possible ESP32s on USB:"
      local i; for i in "${!PORTS[@]}"; do echo "  $((i + 1))) ${PORTS[$i]}  ${DESCS[$i]}"; done
      [[ -t 0 && $YES == 0 ]] || die "pick one: ./flash.sh <port>"
      read -r -p "Which one? [1-${#PORTS[@]}] " i
      [[ $i =~ ^[0-9]+$ ]] && (( i >= 1 && i <= ${#PORTS[@]} )) || die "no such choice"
      PORT=${PORTS[$((i - 1))]} ;;
  esac
}

# ---------- finding the plotter over Wi-Fi ----------
# Boards installed by USB from this folder, by name (hostname + MAC suffix): found again over Wi-Fi
BOARDS=boards.txt
remember() { touch "$BOARDS"; grep -qx "$1" "$BOARDS" || echo "$1" >> "$BOARDS"; }

probe() {  # host -> "plotter <rev> <job> <name>", "rescue" or nothing; checks it's really our plotter
  local body code
  body=$(curl -s -m 4 -w '\n%{http_code}' "http://$1/status" 2>/dev/null) || return 0
  code=${body##*$'\n'}; body=${body%$'\n'*}
  if [[ $code == 200 ]] && grep -q '"rev"' <<<"$body"; then
    python3 -c 'import json, sys; s = json.loads(sys.argv[1]); print("plotter", s["rev"].replace(" ", "_"), s["job"], s.get("name", "?"))' "$body" 2>/dev/null || true
  elif [[ $code == 503 ]] && grep -q "plotter failed" <<<"$body"; then
    echo rescue
  fi
}
wifi_candidates() {
  if [[ -n $TARGET ]]; then echo "$TARGET"; return; fi
  [[ -f $BOARDS ]] && sed 's/$/.local/' "$BOARDS"   # boards installed from here
  local hn; hn=$(custom hostname)
  echo "${hn:-plotter}.local"   # a board without the MAC suffix ("mac_suffix": false)
  echo 192.168.4.1   # the board's own access point
}

if [[ $LIST == 1 ]]; then
  say "USB:"; scan_usb
  if (( ${#PORTS[@]} )); then for i in "${!PORTS[@]}"; do echo "    ${PORTS[$i]}  ${DESCS[$i]}"; done; else echo "    no ESP32 found"; fi
  say "Wi-Fi:"
  for h in $(wifi_candidates); do r=$(probe "$h"); echo "    $h: ${r:-no plotter answers}"; done
  [[ -f $BOARDS ]] && echo "    (boards installed from here: $(xargs < "$BOARDS"))"
  exit 0
fi

# ---------- the files to upload ----------
BUILD=$(mktemp -d); trap 'rm -rf "$BUILD"' EXIT
prepare() {  # uses MP_BOARD_VERSION (e.g. "1.29.0", empty if unknown); sets UPLOAD and STALE
  local m want have
  UPLOAD=() STALE=()
  [[ $OVERWRITE == 0 ]] || STALE+=(config.json)
  want=${MP_BOARD_VERSION:-}
  have=$([[ -n $MPYCROSS ]] && $MPYCROSS --version 2>/dev/null | grep -oE 'v[0-9]+\.[0-9]+' | head -1 | tr -d v || true)
  if [[ $PY == 0 && -n $MPYCROSS && -n $want && ${want%.*} == "$have" ]]; then
    for m in $COMPILED; do
      $MPYCROSS -o "$BUILD/$m.mpy" "$m.py" || die "mpy-cross failed on $m.py"
      UPLOAD+=("$BUILD/$m.mpy"); STALE+=("$m.py")
    done
    echo "Precompiled $COMPILED to .mpy for MicroPython $want (less RAM on the board)."
  else
    for m in $COMPILED; do UPLOAD+=("$m.py"); STALE+=("$m.mpy"); done
    if [[ $PY == 0 && -z $MPYCROSS ]]; then
      echo "Uploading .py sources (no mpy-cross installed: pip install --user -r requirements.txt)."
    elif [[ $PY == 0 ]]; then
      echo "Uploading .py sources (mpy-cross $have, MicroPython ${want:-unknown} on the board)."
    fi
    [[ $PY == 1 ]] || echo "  Sources need much more RAM on the board than .mpy files; if it fails to start, fix this first."
  fi
  for m in $RAW $FILES_EXTRA; do UPLOAD+=("$m"); done
  UPLOAD+=(main.py)
}

# ---------- by USB ----------
release() {
  # After esptool's reset the native USB port can hold the chip in reset until it is opened
  # once with DTR/RTS released (as a terminal does); mpremote alone then gets no answer
  python3 - "$PORT" <<'PY' 2>/dev/null || true
import serial, sys, time
s = serial.Serial()
s.port, s.baudrate, s.dtr, s.rts = sys.argv[1], 115200, False, False
for _ in range(20):
    try:
        s.open(); time.sleep(0.5); s.close(); break
    except serial.SerialException:
        time.sleep(0.3)  # the port disappears for a moment during the reset
PY
}
identify() {  # esptool chip-id: sets variant ("C3", "S3", "" for a classic ESP32) and mac
  info=$(esp chip-id --port "$PORT" 2>&1) || { echo "$info" | tail -5 >&2; release; die "the ESP32 on $PORT does not answer (hold BOOT while plugging it in, then try again)"; }
  release
  # esptool names the chip (ESP32-C3, ESP32-S3 ...); a classic ESP32 shows its package (ESP32-D0WD-V3)
  model=$(grep -oE 'ESP32-[CSHP][0-9]+' <<<"$info" | head -1 || true)
  variant=${model#ESP32-}
  mac=$(grep -oiE 'MAC: *([0-9a-f]{2}:){5}[0-9a-f]{2}' <<<"$info" | head -1 | grep -oiE '[0-9a-f]{2}$' | tr 'A-F' 'a-f' || true)
  echo "esptool: ${model:-a classic ESP32}"
}
usb_flash() {
  need "$ESPTOOL" esptool; need "$MPREMOTE" mpremote
  if [[ ! -r $PORT || ! -w $PORT ]]; then
    local grp; grp=$(stat -c %G "$PORT")
    if id -nG | tr ' ' '\n' | grep -qx "$grp"; then
      die "no access to $PORT although you are in the group '$grp': log out and in again (or reboot)"
    fi
    die "no access to $PORT: you're not in the group '$grp'. Run ./deploy_udev.sh once (it asks for your password), then plug the ESP32 in again"
  fi
  # Talking to MicroPython over USB stops the running plotter (as Thonny does): restart the
  # board if we end up changing nothing
  abort() { timeout 20 $MPREMOTE connect "$PORT" reset >/dev/null 2>&1 || true; die "$1"; }
  say "Checking the ESP32 on $PORT"
  local info model variant chip fw offset mac name board_hn board_sfx files=
  # Ask MicroPython first: its version, chip, MAC and the board's own customization.json.
  # (esptool resets the chip in a way that leaves MicroPython unreachable over native USB,
  # so it's only used when MicroPython doesn't answer or is to be installed.)
  local try
  for try in 1 2; do
    release  # in case an earlier esptool or terminal left the chip held in reset
    info=$(timeout 25 $MPREMOTE connect "$PORT" exec "import os, machine, json
u = os.uname()
try:
    c = json.load(open('customization.json'))
except Exception:
    c = {}
print('MP', u.release, u.machine.split()[-1].replace('-', ''), '%02x' % machine.unique_id()[-1], c.get('hostname', '-'), c.get('mac_suffix', True))" 2>/dev/null | tr -d '\r' | grep '^MP ' || true)
    [[ -n $info ]] && break
  done
  if [[ -n $info ]]; then
    read -r _ MP_BOARD_VERSION model mac board_hn board_sfx <<<"$info"
    variant=${model#ESP32}
    echo "MicroPython $MP_BOARD_VERSION is on it (${model}). Files:"
    files=$(timeout 20 $MPREMOTE connect "$PORT" fs ls 2>/dev/null || true)
    sed 's/^/    /' <<<"$files"
  else
    MP_BOARD_VERSION=
    echo "MicroPython doesn't answer on $PORT (a new board, or one whose program is stuck)."
    identify
    if [[ $FIRMWARE != 1 ]]; then
      # Erasing a board that may well have MicroPython and settings on it: never just on --yes
      [[ $GO == 1 ]] || die "run ./flash.sh --firmware to install MicroPython on it (this erases the board)"
      FIRMWARE=1 CONFIRMED=1
    fi
  fi
  case $variant in H*|P*) die "ESP32-$variant has no Wi-Fi of its own; the plotter needs Wi-Fi" ;; esac
  settings "$files"
  chip=esp32$(tr 'A-Z' 'a-z' <<<"$variant")
  # Its name: hostname plus "-" and the MAC's last byte, as the board computes it (see rescue.py).
  # The hostname comes from the customization.json that will be on the board.
  if [[ -f customization.json ]]; then board_hn=$(custom hostname); board_sfx=$(custom mac_suffix); fi
  [[ $board_hn == - || -z $board_hn ]] && board_hn=plotter
  name=$board_hn
  [[ $board_sfx == False || -z $mac ]] || name=$name-$mac
  echo "It will be called '$name'."

  if [[ $FIRMWARE == 1 ]]; then
    fw=${FW_FILE:-firmware/ESP32_GENERIC${variant:+_$variant}-$MP_DATE-$MP_VERSION.bin}
    case $variant in ""|S2) offset=0x1000 ;; C5) offset=0x2000 ;; *) offset=0x0 ;; esac  # where the bootloader goes
    if [[ ! -f $fw ]]; then
      mkdir -p firmware
      say "Downloading $(basename "$fw")"
      curl -fL -o "$fw.part" "https://micropython.org/resources/firmware/$(basename "$fw")" && mv "$fw.part" "$fw" \
        || { rm -f "$fw.part"; die "no MicroPython $MP_VERSION for ${model:-ESP32} at micropython.org; download one and use --fw FILE"; }
    fi
    [[ ${CONFIRMED:-0} == 1 ]] || \
      ask "Install MicroPython ($(basename "$fw")) on $PORT? This ERASES the whole flash (the board's settings are saved and put back)." \
      || abort "nothing changed"
    # Save the board's settings first, unless they are to be replaced
    local f keep=() stamp; stamp=$(date +%Y%m%d-%H%M%S)
    if [[ -n $MP_BOARD_VERSION ]]; then
      for f in config.json customization.json; do
        [[ $OVERWRITE == 1 ]] && break
        if timeout 20 $MPREMOTE connect "$PORT" fs ls ":$f" >/dev/null 2>&1; then
          mkdir -p backup
          timeout 20 $MPREMOTE connect "$PORT" fs cp ":$f" "backup/${f%.json}-$stamp.json" >/dev/null \
            && keep+=("backup/${f%.json}-$stamp.json") && echo "Saved the board's $f to backup/${f%.json}-$stamp.json"
        fi
      done
    fi
    esp erase-flash --chip "$chip" --port "$PORT"
    esp write-flash --chip "$chip" --port "$PORT" --baud 460800 -- "$offset" "$fw"
    release
    say "Waiting for MicroPython's first start"
    for try in $(seq 1 15); do
      MP_BOARD_VERSION=$(timeout 10 $MPREMOTE connect "$PORT" exec "import os; print(os.uname().release)" 2>/dev/null | tr -d '\r' | tail -1 || true)
      [[ $MP_BOARD_VERSION =~ ^[0-9]+\.[0-9]+ ]] && break
      MP_BOARD_VERSION=; sleep 2
    done
    [[ -n $MP_BOARD_VERSION ]] || die "MicroPython does not answer on $PORT after flashing"
    (( ${#keep[@]} )) && FILES_EXTRA="$FILES_EXTRA ${keep[*]}"
  fi

  prepare
  ask "Copy the plotter ($REV) to the ESP32 on $PORT?" || abort "nothing changed"
  local args=() f f2 stale
  for f in "${UPLOAD[@]}"; do
    # backup/config-<time>.json goes back as config.json, backup/customization-<time>.json likewise
    if [[ $f == backup/* ]]; then f2=${f#backup/}; args+=(cp "$f" ":${f2%-*-*.json}.json" +)
    else args+=(cp "$f" ":${f##*/}" +); fi
  done
  # Remove the other kind of each module: MicroPython would load a stale .py before a new .mpy
  stale=$(printf "'%s'," "${STALE[@]}")
  $MPREMOTE connect "$PORT" exec "import os
for f in ($stale):
    try:
        os.remove(f)
    except OSError:
        pass" + "${args[@]}" exec "print('Files on the ESP32 now:')" + fs ls + reset
  remember "$name"
  say "Done: the ESP32 restarts with $REV as '$name'."
  local ap hn sfx; ap=$(custom ap | grep -oE "'ssid': '[^']*'" | cut -d"'" -f4 || true)
  hn=$(custom hostname); sfx=${name#"${hn:-plotter}"}
  if [[ $(custom networks) != @(|\[\]) ]]; then
    echo "When it has joined your Wi-Fi: http://$name.local"
  else
    echo "Connect to its Wi-Fi '${ap:-ESP32-Plotter}$sfx' and open http://192.168.4.1"
  fi
  echo "Don't open the serial port (Thonny, mpremote) while it runs: that stops the plotter."
}

# ---------- over Wi-Fi ----------
wifi_flash() {
  local pw host= found= h r code body
  pw=$(custom ota_password)
  [[ -n $pw ]] || die "updates over Wi-Fi need \"ota_password\" in customization.json (and a first install by USB)"
  local hosts=() finds=() i
  for h in $(wifi_candidates); do
    say "Looking for the plotter at $h"
    r=$(probe "$h")
    [[ -n $r ]] && { hosts+=("$h"); finds+=("$r"); }
  done
  if (( ${#hosts[@]} > 1 )); then
    echo "Several plotters answer:"
    for i in "${!hosts[@]}"; do echo "  $((i + 1))) ${hosts[$i]}  (${finds[$i]//_/ })"; done
    [[ -t 0 && $YES == 0 ]] || die "pick one: ./flash.sh <host>"
    read -r -p "Which one? [1-${#hosts[@]}] " i
    [[ $i =~ ^[0-9]+$ ]] && (( i >= 1 && i <= ${#hosts[@]} )) || die "no such choice"
    host=${hosts[$((i - 1))]} found=${finds[$((i - 1))]}
  elif (( ${#hosts[@]} == 1 )); then
    host=${hosts[0]} found=${finds[0]}
  fi
  if [[ -z $host ]]; then
    echo "No plotter found at: $(wifi_candidates | xargs)." >&2
    echo "  - On its own access point? Connect this computer to that Wi-Fi first." >&2
    echo "  - On your Wi-Fi? Check the hostname, or give its IP (shown on the OLED): ./flash.sh 192.168.1.23" >&2
    echo "  - <name>.local needs mDNS on this computer (avahi / nss-mdns)." >&2
    exit 1
  fi
  set -- $found
  local busy='^(running|paused|parking)$'
  case $1 in
    plotter) echo "Found the plotter '$4' (${2//_/ }) at $host (job: $3)."
             if [[ $3 =~ $busy ]]; then die "a plot is in progress: let it finish or stop it first"; fi ;;
    rescue)  echo "Found the board at $host in rescue mode (the plotter failed to start; its error is at http://$host/log)." ;;
  esac
  body=$(curl -s -m 5 -w '\n%{http_code}' -u "ota:$pw" "http://$host/auth") || die "$host stopped answering"
  code=${body##*$'\n'}; body=${body%$'\n'*}
  case $code in
    200) MP_BOARD_VERSION=${body#OK } ;;
    401) die "wrong ota_password for $host (customization.json)" ;;
    *)   if ! curl -fs -m 5 -o /dev/null "http://$host/files"; then
           die "$host can't be updated over Wi-Fi (it predates updates over Wi-Fi): flash it once by USB"
         fi
         echo "(older version on the board: the password is checked with the first file)"
         MP_BOARD_VERSION= ;;
  esac
  settings "$(curl -fs -m 5 "http://$host/files" || true)"
  prepare
  ask "Upload the plotter ($REV) to $host and restart it?" || die "nothing changed"
  local f name try reply
  for name in "${STALE[@]}"; do curl -s -m 10 -o /dev/null -u "ota:$pw" -X DELETE "http://$host/file/$name" || true; done
  for f in "${UPLOAD[@]}"; do
    name=$(basename "$f")
    for try in 1 2 3; do
      reply=$(curl -fsS -m 60 -u "ota:$pw" -T "$f" "http://$host/file/$name" 2>&1) && break
      [[ $try == 3 ]] && die "$name: upload failed: $reply"
      sleep 2
    done
    echo "    $name: $reply"
  done
  curl -fsS -m 10 -u "ota:$pw" -X POST "http://$host/reset" >/dev/null || die "restart refused"
  say "Restarting $host"
  # Until it stops answering at all (rescue mode answers 503, which still means "up")
  for _ in $(seq 1 20); do curl -s -o /dev/null -m 1 "http://$host/log" || break; sleep 0.5; done
  for _ in $(seq 1 45); do
    r=$(probe "$host"); [[ -n $r ]] && break; sleep 1
  done
  case $r in
    "plotter ${REV// /_} "*) say "Done: $host runs $REV." ;;
    plotter*) die "$host is back but runs ${r#plotter } instead of $REV" ;;
    rescue) curl -s -m 5 "http://$host/log" | tail -n 12 >&2; die "$host is in rescue mode: the new plotter failed to start (log above)" ;;
    *) die "$host did not come back within a minute: it may have joined another network or opened its own access point" ;;
  esac
}

# ---------- which way ----------
if [[ $WIFI == 1 ]]; then
  wifi_flash
elif [[ -n $TARGET && ! $TARGET =~ ^(/dev/|tty|ACM|USB) ]]; then
  wifi_flash   # a host name or IP
else
  scan_usb
  pick_port
  if [[ -n $PORT ]]; then usb_flash
  elif [[ $FIRMWARE == 1 ]]; then die "no ESP32 on USB: installing MicroPython needs the USB cable"
  else echo "No ESP32 on USB, trying Wi-Fi."; wifi_flash
  fi
fi
