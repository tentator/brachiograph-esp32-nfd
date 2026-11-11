#!/bin/bash
# Put the plotter on an ESP32, by USB or over Wi-Fi. ./flash.sh --help for the options.
#
# By USB: finds the ESP32 among the serial ports, installs MicroPython only if it's missing
# (or with --firmware), and copies the plotter's files. Over Wi-Fi (no ESP32 on USB, --wifi or a
# host name): finds the plotter, checks the update password and uploads the files.
#
# Settings: board_default_settings.json (defaults, in git) always goes to the board;
# board_settings.json (yours, next to this script, not in git) too when it exists, otherwise the
# board keeps its own. The calibration (board_calibration.json, set on the web page) stays on the
# board: never uploaded, saved and put back around a reinstall, --get-calibration fetches a copy.
set -euo pipefail
cd "$(dirname "$0")"

MP_VERSION=v1.29.0          # MicroPython installed on new boards
MP_DATE=20260824            # its build date, part of the firmware file name
COMPILED="rescue plotter ssd1306"   # precompiled to .mpy when possible: less RAM on the board
DEF_SETTINGS=board_default_settings.json DEF_CAL=board_default_calibration.json
SETTINGS=board_settings.json CAL=board_calibration.json
# main.py goes last: it starts everything else, so a cut-off update can't start half the files
RAW="index.html $DEF_SETTINGS $DEF_CAL"

usage() {
  cat <<EOF
Usage: ./flash.sh [options] [port | host]

  port         serial port of the ESP32: /dev/ttyACM0, ttyUSB0, ACM0 ... (default: found by itself)
  host         update over Wi-Fi at this address, e.g. plotter-60.local or 192.168.4.1

  -l, --list   show the ESP32s found on USB and Wi-Fi, change nothing
  --get-calibration
               copy the board's calibration to boards/<name>-calibration.json, change nothing
  --firmware   (re)install MicroPython by USB; erases the board, its settings are put back
  --fw FILE    MicroPython firmware file to install (default: downloaded for the chip found)
  --wifi       update over Wi-Fi even if an ESP32 is plugged in
  --py         upload .py sources instead of precompiled .mpy files
  -y, --yes    don't ask (a board without MicroPython is still only erased with --firmware)
  -v, --verbose
               show every step and the tools' output
  -h, --help   this help
EOF
}

VERBOSE=0 YES=0 LIST=0 GETCAL=0 FIRMWARE=0 FW_FILE= WIFI=0 PY=0 TARGET=
while (( $# )); do
  case $1 in
    -l|--list) LIST=1 ;;
    --get-calibration) GETCAL=1 ;;
    --firmware) FIRMWARE=1 ;;
    --fw) FW_FILE=${2:?--fw needs a file}; FIRMWARE=1; shift ;;
    --wifi) WIFI=1 ;;
    --py) PY=1 ;;
    -y|--yes) YES=1 ;;
    -v|--verbose) VERBOSE=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) usage >&2; echo "flash.sh: unknown option $1" >&2; exit 1 ;;
    *) [[ -z $TARGET ]] || { echo "flash.sh: only one port or host, got '$TARGET' and '$1'" >&2; exit 1; }; TARGET=$1 ;;
  esac
  shift
done

# ---------- output ----------
die() { echo "flash.sh: $*" >&2; exit 1; }
say() { echo "$*"; }                              # always shown: what happens
detail() { [[ $VERBOSE == 0 ]] || echo "  $*"; }  # --verbose only
TOOLLOG=$(mktemp); BUILD=$(mktemp -d); trap 'rm -rf "$TOOLLOG" "$BUILD"' EXIT
run() {
  # run <what> <command...>: the command's output only with --verbose; on failure its last lines
  local what=$1; shift
  if [[ $VERBOSE == 1 ]]; then
    "$@" || die "$what failed"
  elif ! "$@" >"$TOOLLOG" 2>&1; then
    tail -n 25 "$TOOLLOG" >&2
    die "$what failed (output above; --verbose shows everything)"
  fi
}
ask() {
  [[ $YES == 1 ]] && return 0
  [[ -t 0 ]] || die "$1 Run with --yes to go ahead without asking."
  local a; read -r -p "$1 [y/N] " a; [[ $a == [yYjJ]* ]]
}
[[ -z $FW_FILE || -f $FW_FILE ]] || die "firmware file $FW_FILE not found"
[[ -f $DEF_SETTINGS && -f $DEF_CAL ]] || die "$DEF_SETTINGS or $DEF_CAL missing: run flash.sh from the repository"

# ---------- settings ----------
# A customization.json from REV 17 or earlier becomes board_settings.json (the old file stays)
if [[ -f customization.json && ! -f $SETTINGS && $LIST == 0 ]]; then
  python3 - <<'PY' || die "customization.json is not valid JSON: fix it or remove it"
import json
old = json.load(open("customization.json"))
cal = json.load(open("board_default_calibration.json"))
json.dump({k: v for k, v in old.items() if k not in cal and k != "arms"}, open("board_settings.json", "w"), indent=2)
PY
  say "Copied your customization.json to $SETTINGS (the new name); you can delete customization.json."
fi
setting() {  # the value of a board setting: board_settings.json if there, else the default
  python3 -c "import json, os, sys
s = json.load(open('$DEF_SETTINGS'))
if os.path.exists('$SETTINGS'):
    s.update(json.load(open('$SETTINGS')))
v = s.get(sys.argv[1], '')
print(json.dumps(v) if isinstance(v, (list, dict)) else v)" "$1"
}
if [[ -f $SETTINGS ]]; then
  # Mistakes the board can't fix by itself: it would start without your Wi-Fi or with the wrong pins
  python3 - "$DEF_SETTINGS" "$SETTINGS" "$DEF_CAL" <<'PY' || die "fix $SETTINGS first (see $DEF_SETTINGS)"
import json, sys
d = json.load(open(sys.argv[1])); cal = json.load(open(sys.argv[3]))
try:
    own = json.load(open(sys.argv[2]))
except ValueError as e:
    sys.exit("%s is not valid JSON: %s" % (sys.argv[2], e))
c = dict(d, **own)
num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
def chipmap(v, ok):  # a value, or a map {"*": value, "ESP32C3": value, ...}
    return all(ok(x) for x in v.values()) if isinstance(v, dict) and "*" in v else ok(v)
errors, notes = [], []
errors += ["networks entry %d needs \"ssid\" and \"password\"" % (i + 1)
           for i, n in enumerate(c.get("networks", [])) if not {"ssid", "password"} <= set(n)]
if not 8 <= len((c.get("ap") or {}).get("password", "plotterpassword")) <= 63:
    errors.append("the \"ap\" password needs 8 to 63 characters")
if not isinstance(c.get("mac_suffix", True), bool):
    errors.append("\"mac_suffix\" must be true or false")
if not chipmap(c.get("oled", False), lambda v: isinstance(v, bool)):
    errors.append("\"oled\" must be true or false")
if not chipmap(c.get("led", False), lambda v: v is False or (isinstance(v, dict) and isinstance(v.get("pin"), int))):
    errors.append("\"led\" must be false or like {\"pin\": 8, \"active_low\": true}")
if not chipmap(c.get("txpower"), lambda v: v is None or (num(v) and 2 <= v <= 20)):
    errors.append("\"txpower\" must be a number of dBm from 2 to 20 (e.g. 8.5), or null for full power")
if not chipmap(own.get("pins", {}), lambda v: isinstance(v, dict) and all(isinstance(p, int) for p in v.values())):
    errors.append("\"pins\" must be like {\"shoulder\": 4} (GPIO numbers)")
for k in own:
    if k == "arms":
        notes.append("\"arms\" is no longer used: the elbow direction is S2 Slope on the web page (default -10, +10 for wooden arms)")
    elif k in cal:
        notes.append("\"%s\" is part of the calibration (set on the web page), not a board setting: ignored" % k)
    elif k not in d and not k.startswith("_"):
        notes.append("\"%s\" is not a board setting: ignored" % k)
for n in notes:
    print("%s: note: %s" % (sys.argv[2], n), file=sys.stderr)
for e in errors:
    print("%s: %s" % (sys.argv[2], e), file=sys.stderr)
sys.exit(1 if errors else 0)
PY
  FILES_EXTRA=$SETTINGS
else
  FILES_EXTRA=
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
describe() {  # USB vendor:product -> what it is, empty if it isn't a usual ESP32 adapter
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
    else detail "skipping $dev: USB $id is not a usual ESP32 adapter"
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
      say "Several possible ESP32s on USB:"
      local i; for i in "${!PORTS[@]}"; do say "  $((i + 1))) ${PORTS[$i]}  ${DESCS[$i]}"; done
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
  local hn; hn=$(setting hostname)
  echo "${hn:-plotter}.local"   # a board without the MAC suffix ("mac_suffix": false)
  echo 192.168.4.1   # the board's own access point
}

if [[ $LIST == 1 ]]; then
  echo "USB:"; VERBOSE=1 scan_usb
  if (( ${#PORTS[@]} )); then for i in "${!PORTS[@]}"; do echo "  ${PORTS[$i]}  ${DESCS[$i]}"; done; else echo "  no ESP32 found"; fi
  echo "Wi-Fi:"
  for h in $(wifi_candidates); do r=$(probe "$h"); echo "  $h: ${r:-no plotter answers}"; done
  [[ -f $BOARDS ]] && echo "  (boards installed from here: $(xargs < "$BOARDS"))"
  exit 0
fi

# ---------- the files to upload ----------
prepare() {  # uses MP_BOARD_VERSION (e.g. "1.29.0", empty if unknown); sets UPLOAD and STALE
  local m want have
  UPLOAD=() STALE=()
  want=${MP_BOARD_VERSION:-}
  have=$([[ -n $MPYCROSS ]] && $MPYCROSS --version 2>/dev/null | grep -oE 'v[0-9]+\.[0-9]+' | head -1 | tr -d v || true)
  if [[ $PY == 0 && -n $MPYCROSS && -n $want && ${want%.*} == "$have" ]]; then
    for m in $COMPILED; do
      $MPYCROSS -o "$BUILD/$m.mpy" "$m.py" || die "mpy-cross failed on $m.py"
      UPLOAD+=("$BUILD/$m.mpy"); STALE+=("$m.py")
    done
    detail "precompiled $COMPILED to .mpy for MicroPython $want"
  else
    for m in $COMPILED; do UPLOAD+=("$m.py"); STALE+=("$m.mpy"); done
    if [[ $PY == 0 && -z $MPYCROSS ]]; then
      say "Note: no mpy-cross installed (pip install --user -r requirements.txt): uploading .py sources, which need much more RAM on the board."
    elif [[ $PY == 0 ]]; then
      say "Note: mpy-cross $have doesn't match MicroPython ${want:-unknown} on the board: uploading .py sources, which need much more RAM."
    fi
  fi
  for m in $RAW $FILES_EXTRA; do UPLOAD+=("$m"); done
  UPLOAD+=(main.py)
}
settings_note() {
  if [[ -n $FILES_EXTRA ]]; then detail "your $SETTINGS goes to the board"
  else say "No $SETTINGS here: the board keeps its own settings (a new board starts with the defaults)."
  fi
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
  local out
  out=$(esp chip-id --port "$PORT" 2>&1) || { echo "$out" | tail -5 >&2; release; die "the ESP32 on $PORT does not answer (hold BOOT while plugging it in, then try again)"; }
  release
  # esptool names the chip (ESP32-C3, ESP32-S3 ...); a classic ESP32 shows its package (ESP32-D0WD-V3)
  model=$(grep -oE 'ESP32-[CSHP][0-9]+' <<<"$out" | head -1 || true)
  variant=${model#ESP32-}
  model=${model:-ESP32}
  mac=$(grep -oiE 'MAC: *([0-9a-f]{2}:){5}[0-9a-f]{2}' <<<"$out" | head -1 | grep -oiE '[0-9a-f]{2}$' | tr 'A-F' 'a-f' || true)
  detail "esptool: $model, MAC ...:$mac"
}
# Asked of MicroPython: version, chip, MAC byte, and the board's hostname and mac_suffix
QUERY="import os, machine, json
u = os.uname()
s = {}
for n in ('board_default_settings.json', 'customization.json', 'board_settings.json'):
    try:
        s.update(json.load(open(n)))
    except Exception:
        pass
print('MP', u.release, u.machine.split()[-1].replace('-', ''), '%02x' % machine.unique_id()[-1], s.get('hostname', '-'), s.get('mac_suffix', True))"
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
  local info model= variant= chip fw offset mac= name board_hn=- board_sfx=True files= try
  # Ask MicroPython first (esptool resets the chip in a way that leaves MicroPython unreachable
  # over native USB, so it's only used when MicroPython doesn't answer or is to be installed)
  for try in 1 2; do
    release  # in case an earlier esptool or terminal left the chip held in reset
    info=$(timeout 25 $MPREMOTE connect "$PORT" exec "$QUERY" 2>/dev/null | tr -d '\r' | grep '^MP ' || true)
    [[ -n $info ]] && break
  done
  if [[ -n $info ]]; then
    read -r _ MP_BOARD_VERSION model mac board_hn board_sfx <<<"$info"
    variant=${model#ESP32}
    if [[ $VERBOSE == 1 ]]; then
      files=$(timeout 20 $MPREMOTE connect "$PORT" fs ls 2>/dev/null || true)
      detail "MicroPython $MP_BOARD_VERSION, files on the board:"; sed 's/^/    /' <<<"$files"
    fi
  else
    MP_BOARD_VERSION=
    identify
    if [[ $FIRMWARE != 1 ]]; then
      # Erasing a board that may well have MicroPython and settings on it: never just on --yes
      [[ -t 0 ]] || die "MicroPython doesn't answer on $PORT: run ./flash.sh --firmware to install it (this erases the board)"
      say "MicroPython doesn't answer on $PORT: a new board, or one whose program is stuck."
      local a; read -r -p "Install MicroPython and the plotter $REV? This ERASES the board. [y/N] " a
      [[ $a == [yYjJ]* ]] || die "nothing changed"
      FIRMWARE=1 CONFIRMED=1
    fi
  fi
  case $variant in H*|P*) die "ESP32-$variant has no Wi-Fi of its own; the plotter needs Wi-Fi" ;; esac
  chip=esp32$(tr 'A-Z' 'a-z' <<<"$variant")
  # Its name: hostname plus "-" and the MAC's last byte, as the board computes it (see rescue.py),
  # from the settings it will have: yours if there are any here, else its own, else the defaults
  if [[ -f $SETTINGS || $board_hn == - ]]; then board_hn=$(setting hostname); board_sfx=$(setting mac_suffix); fi
  name=${board_hn:-plotter}
  [[ $board_sfx == False || -z $mac ]] || name=$name-$mac
  say "Found $name (${model:-ESP32}) on $PORT."

  if [[ $GETCAL == 1 ]]; then
    mkdir -p boards
    if timeout 20 $MPREMOTE connect "$PORT" fs cp ":$CAL" "boards/$name-calibration.json" >/dev/null 2>&1; then
      say "Calibration saved to boards/$name-calibration.json."
    else
      say "$name has no calibration of its own yet (it uses the defaults)."
    fi
    timeout 20 $MPREMOTE connect "$PORT" reset >/dev/null 2>&1 || true
    return
  fi
  settings_note

  if [[ $FIRMWARE == 1 ]]; then
    fw=${FW_FILE:-firmware/ESP32_GENERIC${variant:+_$variant}-$MP_DATE-$MP_VERSION.bin}
    case $variant in ""|S2) offset=0x1000 ;; C5) offset=0x2000 ;; *) offset=0x0 ;; esac  # where the bootloader goes
    [[ ${CONFIRMED:-0} == 1 ]] || \
      ask "Reinstall MicroPython and the plotter $REV on $name? This erases the board (its settings are saved and put back)." \
      || abort "nothing changed"
    if [[ ! -f $fw ]]; then
      mkdir -p firmware
      say "Downloading $(basename "$fw") ..."
      curl -fsL -o "$fw.part" "https://micropython.org/resources/firmware/$(basename "$fw")" && mv "$fw.part" "$fw" \
        || { rm -f "$fw.part"; die "no MicroPython $MP_VERSION for ${model:-ESP32} at micropython.org; download one and use --fw FILE"; }
    fi
    # Save the board's own files first (the calibration, its settings if none here, and the
    # files of earlier versions, which the board converts at the next start)
    local f keep=() stamp; stamp=$(date +%Y%m%d-%H%M%S)
    if [[ -n $MP_BOARD_VERSION ]]; then
      for f in $CAL $SETTINGS config.json customization.json; do
        [[ $f == "$SETTINGS" && -n $FILES_EXTRA ]] && continue
        mkdir -p backup
        if timeout 20 $MPREMOTE connect "$PORT" fs cp ":$f" "backup/$name-$stamp-$f" >/dev/null 2>&1; then
          keep+=("backup/$name-$stamp-$f"); detail "saved the board's $f to backup/$name-$stamp-$f"
        fi
      done
      (( ${#keep[@]} )) && say "Saved the board's settings to backup/ (put back after the install)."
    fi
    say "Installing MicroPython (about a minute) ..."
    run "erasing the board" esp erase-flash --chip "$chip" --port "$PORT"
    run "writing MicroPython" esp write-flash --chip "$chip" --port "$PORT" --baud 460800 -- "$offset" "$fw"
    release
    for try in $(seq 1 15); do
      MP_BOARD_VERSION=$(timeout 10 $MPREMOTE connect "$PORT" exec "import os; print(os.uname().release)" 2>/dev/null | tr -d '\r' | tail -1 || true)
      [[ $MP_BOARD_VERSION =~ ^[0-9]+\.[0-9]+ ]] && break
      MP_BOARD_VERSION=; sleep 2
    done
    [[ -n $MP_BOARD_VERSION ]] || die "MicroPython does not answer on $PORT after the install"
    (( ${#keep[@]} )) && FILES_EXTRA="$FILES_EXTRA ${keep[*]}"
  else
    ask "Copy the plotter $REV to $name?" || abort "nothing changed"
  fi

  prepare
  say "Copying the plotter $REV ..."
  local args=() f2 stale
  for f in "${UPLOAD[@]}"; do
    # backup/<name>-<time>-<file> goes back under its own name
    if [[ $f == backup/* ]]; then f2=${f#backup/$name-$stamp-}; args+=(cp "$f" ":$f2" +)
    else args+=(cp "$f" ":${f##*/}" +); fi
  done
  # Remove the other kind of each module: MicroPython would load a stale .py before a new .mpy
  stale=$(printf "'%s'," "${STALE[@]}")
  run "copying the files" $MPREMOTE connect "$PORT" exec "import os
for f in ($stale):
    try:
        os.remove(f)
    except OSError:
        pass" + "${args[@]}" exec "print('Files on the ESP32 now:')" + fs ls + reset
  remember "$name"
  say "Done: $name restarts with $REV."
  local ap sfx; ap=$(setting ap | python3 -c "import json, sys; print(json.load(sys.stdin).get('ssid', ''))" 2>/dev/null || true)
  sfx=${name#"${board_hn:-plotter}"}
  if [[ $(setting networks) != "[]" && -n $(setting networks) ]]; then
    say "Open http://$name.local once it has joined your Wi-Fi (its OLED shows the address)."
  else
    say "Connect to its Wi-Fi '${ap:-ESP32-Plotter}$sfx' and open http://192.168.4.1"
  fi
  detail "don't open the serial port (Thonny, mpremote) while it runs: that stops the plotter"
}

# ---------- over Wi-Fi ----------
wifi_flash() {
  local pw host= found= h r code body hosts=() finds=() i
  for h in $(wifi_candidates); do
    detail "looking for the plotter at $h"
    r=$(probe "$h")
    [[ -n $r ]] && { hosts+=("$h"); finds+=("$r"); }
  done
  if (( ${#hosts[@]} > 1 )); then
    say "Several plotters answer:"
    for i in "${!hosts[@]}"; do say "  $((i + 1))) ${hosts[$i]}  (${finds[$i]//_/ })"; done
    [[ -t 0 && $YES == 0 ]] || die "pick one: ./flash.sh <host>"
    read -r -p "Which one? [1-${#hosts[@]}] " i
    [[ $i =~ ^[0-9]+$ ]] && (( i >= 1 && i <= ${#hosts[@]} )) || die "no such choice"
    host=${hosts[$((i - 1))]} found=${finds[$((i - 1))]}
  elif (( ${#hosts[@]} == 1 )); then
    host=${hosts[0]} found=${finds[0]}
  fi
  if [[ -z $host ]]; then
    echo "flash.sh: no plotter found on USB or at: $(wifi_candidates | xargs)." >&2
    echo "  - On its own access point? Connect this computer to that Wi-Fi first." >&2
    echo "  - On your Wi-Fi? Check the hostname, or give its IP (shown on the OLED): ./flash.sh 192.168.1.23" >&2
    echo "  - <name>.local needs mDNS on this computer (avahi / nss-mdns)." >&2
    exit 1
  fi
  set -- $found
  local busy='^(running|paused|parking)$' name=$host
  case $1 in
    plotter) name=$4; say "Found $name (${2//_/ }) at $host."
             if [[ $3 =~ $busy ]]; then die "a plot is in progress: let it finish or stop it first"; fi ;;
    rescue)  say "Found the board at $host in rescue mode (the plotter failed to start; its error is at http://$host/log)." ;;
  esac
  name=${name//_/ }
  if [[ $GETCAL == 1 ]]; then
    [[ $1 == plotter ]] || die "in rescue mode the calibration can't be fetched; fix the plotter first"
    mkdir -p boards
    curl -fsS -m 10 -o "boards/$name-calibration.json" "http://$host/calibration" \
      || die "$host doesn't hand out its calibration (it predates REV 18): update it first"
    say "Calibration saved to boards/$name-calibration.json."
    return
  fi
  pw=$(setting ota_password)
  [[ -n $pw ]] || die "updates over Wi-Fi need \"ota_password\" in $SETTINGS (and a first install by USB)"
  body=$(curl -s -m 5 -w '\n%{http_code}' -u "ota:$pw" "http://$host/auth") || die "$host stopped answering"
  code=${body##*$'\n'}; body=${body%$'\n'*}
  case $code in
    200) MP_BOARD_VERSION=${body#OK } ;;
    401) die "wrong ota_password for $host ($SETTINGS)" ;;
    *)   if ! curl -fs -m 5 -o /dev/null "http://$host/files"; then
           die "$host can't be updated over Wi-Fi (it predates updates over Wi-Fi): flash it once by USB"
         fi
         detail "older version on the board: the password is checked with the first file"
         MP_BOARD_VERSION= ;;
  esac
  settings_note
  prepare
  ask "Upload the plotter $REV to $name and restart it?" || die "nothing changed"
  say "Uploading the plotter $REV ..."
  local f n try reply
  for n in "${STALE[@]}"; do curl -s -m 10 -o /dev/null -u "ota:$pw" -X DELETE "http://$host/file/$n" || true; done
  for f in "${UPLOAD[@]}"; do
    n=$(basename "$f")
    for try in 1 2 3; do
      reply=$(curl -fsS -m 60 -u "ota:$pw" -T "$f" "http://$host/file/$n" 2>&1) && break
      [[ $try == 3 ]] && die "$n: upload failed: $reply"
      sleep 2
    done
    detail "$n: $reply"
  done
  curl -fsS -m 10 -u "ota:$pw" -X POST "http://$host/reset" >/dev/null || die "restart refused"
  detail "restarting $host"
  # Until it stops answering at all (rescue mode answers 503, which still means "up")
  for _ in $(seq 1 20); do curl -s -o /dev/null -m 1 "http://$host/log" || break; sleep 0.5; done
  for _ in $(seq 1 45); do
    r=$(probe "$host"); [[ -n $r ]] && break; sleep 1
  done
  case $r in
    "plotter ${REV// /_} "*) say "Done: $name runs $REV (http://$host)." ;;
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
  else detail "no ESP32 on USB, trying Wi-Fi"; wifi_flash
  fi
fi
