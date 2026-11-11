# BrachioGraph on an ESP32-C3 OLED board (MicroPython)

A port of the [BrachioGraph](https://www.brachiograph.art/) pen plotter from the Raspberry Pi
to an **ESP32-C3 board with a built-in 0.42" 72x40 OLED**. The ESP32 runs its own Wi-Fi
access point and serves a web page; everything heavy (SVG parsing, image-to-line conversion,
path planning) happens in your browser. The finished drawing is uploaded to the board as
simple `G0`/`G1` commands and the board plots it on its own, so the phone can sleep or
disconnect meanwhile. It also runs on a classic ESP32 DevKit and the other ESP32 chips with
Wi-Fi (S2, S3, C2, C5, C6; no OLED there), and can join an existing Wi-Fi instead of opening its own.

Current version: **`plotter.py`** (REV 18.0) with its web page **`index.html`**, started by
**`main.py`**. The short build guide for beginners is [README.md](README.md).

---

## Contents

1. [Hardware](#hardware)
2. [Installing](#installing)
3. [Running and connecting](#running-and-connecting)
4. [The web interface](#the-web-interface)
5. [Calibration](#calibration)
6. [Image to plot conversion](#image-to-plot-conversion) and [Text](#text)
7. [How it works](#how-it-works)
8. [HTTP API](#http-api)
9. [Tuning constants](#tuning-constants)
10. [Troubleshooting](#troubleshooting)
11. [Files and revision history](#files-and-revision-history)

---

## Hardware

| Part | ESP32-C3 OLED board (also C2, C5, C6) | Classic ESP32 DevKit | ESP32-S2 / S3 |
|---|---|---|---|
| Shoulder servo (inner arm) | GPIO **0** | GPIO **25** | GPIO **4** |
| Elbow servo (outer arm) | GPIO **1** | GPIO **26** | GPIO **5** |
| Pen-lift servo | GPIO **3** | GPIO **27** | GPIO **6** |
| OLED SSD1306 72x40 (on board) | C3 only: I2C SDA = GPIO **5**, SCL = GPIO **6**, address 0x3C (0x3D also detected) | none | none |

Wire colours used in the README's wiring diagram (`images/wiring.png`, drawn by
`images/draw.py`): black GND, red +5 V, green shoulder, yellow elbow, white pen.

### Settings files

Two kinds of settings, each with its defaults in exactly one file in git:

| | Defaults (in git, uploaded with every update) | Your own | Where yours live |
|---|---|---|---|
| **Board settings**: name, Wi-Fi, passwords, access point, transmit power, pins, LED, display | `board_default_settings.json` | `board_settings.json` | On your computer next to `flash.sh` (not in git: it holds passwords); `flash.sh` sends it to the board |
| **Calibration**: arm lengths, Park/Slope, pen, speed, drawing area, view, measured points | `board_default_calibration.json` | `board_calibration.json` | Only on the board, written by *Apply Config* and the precise calibration |

- The board reads the defaults first and then yours on top, key by key. Your files only need the
  keys you change; new settings in later versions come from the defaults. The code itself holds no
  defaults, except an emergency access point in `rescue.py` for when the defaults file is missing.
- **`flash.sh` and the board settings:** with a `board_settings.json` next to `flash.sh`, it goes to
  the board (checked first); without one, the board keeps the settings it has, and a new board starts
  with the defaults. Start yours with `cp board_default_settings.json board_settings.json`.
- **The calibration stays on the board:** `flash.sh` never uploads or replaces it. It saves it and
  puts it back around a reinstall (`--firmware`); `./flash.sh --get-calibration` copies it to
  `boards/<name>-calibration.json` on your computer (`GET /calibration` over Wi-Fi). Only the values
  that differ from the defaults are saved, so a later change of a default still reaches the others.
- Boards from REV 17 and earlier convert themselves at the first start: `customization.json` becomes
  `board_settings.json` (its calibration values and `"arms"` aside), `config.json` becomes
  `board_calibration.json`. A local `customization.json` is copied to `board_settings.json` by
  `flash.sh` (the original stays; delete it when you like).

**Board settings** (in `board_settings.json`; keys starting with `_` are comments):

| Setting | Default | Effect |
|---|---|---|
| `"networks"` | `[]` | Wi-Fi networks to join, `[{"ssid": "...", "password": "..."}, ...]`, the most important first; none: the board opens its own access point (see [Wi-Fi](#wi-fi)) |
| `"ap"` | `{"ssid": "ESP32-Plotter", "password": "plotterpassword"}` | The board's own access point (with the MAC suffix), opened when no network is joined; password 8–63 characters |
| `"ota_password"` | `""` | Password for updates over Wi-Fi; empty: no updates over Wi-Fi. `flash.sh` needs it in your `board_settings.json` to update a board over Wi-Fi |
| `"hostname"` | `"plotter"` | The board's name on the network, `http://<hostname>-<xx>.local` (see `"mac_suffix"`) |
| `"mac_suffix"` | `true` | Adds `-` and the last byte of the board's MAC address (`-60` for `…:65:60`, as `esptool` prints it) to the hostname and to the name of its own access point, so several boards sharing one `board_settings.json` stay apart. The OLED and `/status` show the full name. |
| `"txpower"` | C3: `13`; other chips: `null` (full power) | Wi-Fi transmit power in dBm (2–20). ESP32-C3 boards can't transmit cleanly at the full 20 dBm: a C3 "SuperMini" (no display, LED on GPIO 8) had an invisible access point at 20 dBm that appeared at 17 and below, and a C3 board with the 0.42" OLED couldn't join a network 1 m from the router or kept dropping it; at 13 both work. If an access point still doesn't show up, try `8.5`. |
| `"oled"` | C3: `true`, else `false` | Use the 72x40 display on GPIO 5/6; `false` on a C3 board without one (nothing touches GPIO 5/6 then). A C3 board without a display also works with `true`: it scans once at boot and then leaves the I2C pins alone. |
| `"led"` | C3: `{"pin": 8, "active_low": true}`, classic ESP32: `{"pin": 2, "active_low": false}`, else `false` | The board's LED, blinked as a heartbeat (see [The OLED](#the-oled)); `false` for none |
| `"pins"` | per chip, see the table above | Servo pins; yours only need the joints you change, e.g. `{"shoulder": 4}` |

Settings that differ by chip are written in `board_default_settings.json` as a map: `"*"` for any
chip, or a chip name (`ESP32`, `ESP32C3`, `ESP32S3` ...), e.g. `"txpower": {"*": null, "ESP32C3": 13}`.
Yours can be a plain value (`"txpower": 8.5`) or such a map.

- Arms: inner `L1` = 80 mm, outer `L2` = 80 mm by default; set your lengths in the web page
  (see [Arms and drawing area](#arms-and-drawing-area)).
- Servos: standard hobby servos at 50 Hz. Pulse widths are clamped to 750–2250 µs for the
  arm and 1000–2000 µs for the pen so a bad calibration can't drive them into their end stops.
- The servos are switched on one after another, 300 ms apart (`SERVO_START_GAP_MS`), so their
  start-up currents don't add up.
- **Power:** the servos run off the board's `5V` pin (USB power), with a 2200 µF capacitor
  across their supply on the breadboard to absorb the current spikes (see the README's wiring).
  Three servos starting together can draw over 1 A; if the board resets or the OLED glitches,
  use a stronger USB power supply or feed the servos from a separate 5 V supply (common ground
  with the ESP32).
- If GPIO 0 ever stops working, set `"pins": {"shoulder": 4}` in `board_settings.json`.

The arm geometry follows the original BrachioGraph: the shoulder is at (0, 0),
the paper lies in front of it, and the default drawing area is X −87…30 mm, Y 45…128 mm
(adjustable in the web page).

---

## Installing

### flash.sh

`./flash.sh` does the installation by USB and the later updates over Wi-Fi (Linux;
`pip install --user -r requirements.txt` first for esptool, mpremote and mpy-cross):

```
./flash.sh [options] [port | host]

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
```

**Output:** a normal run prints a few lines: which board it found, its one question, what it
does, and when done the board's address. `--verbose` shows every step, the files on the board and
the tools' own output (esptool, mpremote, each uploaded file). When something fails, the last
lines of the failing tool's output are always shown.

**One question per run:** *Copy the plotter REV 18.0 to plotter-60?* (by USB), *Upload ... and
restart it?* (over Wi-Fi), or for a board on which MicroPython doesn't answer: *Install
MicroPython and the plotter? This ERASES the board.* That last one is never answered by `--yes`:
"doesn't answer" can also mean a stuck board with settings on it, so erasing needs a typed *y* or
`--firmware`.

By USB:

- **Finding the board:** it looks at `/dev/ttyACM*` and `/dev/ttyUSB*` and keeps the ports
  whose USB ID is a usual ESP32 connection: Espressif's built-in USB (`303a`, C3/S3/C6 boards),
  CP210x (`10c4:ea60`), CH340/CH9102 (`1a86`), FTDI (`0403`). Other devices, e.g. a laptop's
  mobile modem on `/dev/ttyACM0`, are skipped. If several ports remain it shows a numbered list to
  choose from; `./flash.sh ACM1` picks one directly.
- **What's on it:** it asks MicroPython first (`mpremote`): its version, the chip, the MAC (for
  the board's name, e.g. *Found plotter-60*) and the board's own hostname. Only if MicroPython
  doesn't answer does it use `esptool chip-id` to name the chip (ESP32-C3, -S3, -C6 ..., or a
  classic ESP32); chips without Wi-Fi (ESP32-H2, -P4) are refused. Works with esptool 4 and 5. On
  boards with native USB (C3, S3, C6), `esptool`'s reset can leave the chip held in reset so that
  `mpremote` gets no answer; `flash.sh` opens the port once with the control lines released
  afterwards (and before its first try), which frees it.
- **MicroPython:** installed only if the board has none, or with `--firmware`. The firmware
  (`ESP32_GENERIC_<chip>-20260824-v1.29.0.bin`) is downloaded from micropython.org into
  `firmware/` and written at the right offset for the chip; `--fw FILE` uses another file (e.g.
  an S3 build with PSRAM support). Before erasing, the board's own files (`board_calibration.json`,
  its `board_settings.json` if you have none here, and `config.json`/`customization.json` of
  earlier versions) are saved to `backup/` and put back afterwards.
- **Files:** `board_default_settings.json` and `board_default_calibration.json` always, your
  `board_settings.json` if it's next to `flash.sh` (checked first, see below), the code precompiled
  to `.mpy` when the installed `mpy-cross` matches the board's MicroPython version (otherwise
  `.py`, with a note), stale counterparts deleted, `main.py` last, then the board restarts. The
  calibration is never uploaded (see [Settings files](#settings-files)).
- **`board_settings.json` checks:** valid JSON, networks with `ssid` and `password`, an `ap`
  password of 8–63 characters, `"mac_suffix"` and `"oled"` true/false, `"led"`, `"txpower"` and
  `"pins"` well-formed. Calibration keys and unknown keys are reported and ignored (e.g. `"arms"`,
  replaced by S2 Slope on the web page).
- **Remembering boards:** the names of boards installed from this folder go into `boards.txt`
  (not in git), so later Wi-Fi updates find them as `<name>.local`.
- **Permissions:** serial ports belong to root and the group `dialout`. Not in that group:
  `./deploy_udev.sh` (once, on the computer itself, not in a toolbox; asks for your password)
  installs a udev rule that gives whoever is logged in access to the ESP32's USB port
  (`uaccess`); then plug the ESP32 in again.
- **Classic ESP32 boards** (e.g. an ESP32-D0WD-V3 DevKit with a CP210x or CH340 USB chip, usually
  `/dev/ttyUSB0`) work the same way: `esptool` reports them by package name, `flash.sh` installs
  `ESP32_GENERIC` at offset `0x1000`, and the plotter uses GPIO 25/26/27 for the servos and the
  LED on GPIO 2 (no OLED).

Talking to the board over USB stops the running plotter, as Thonny does: `flash.sh` always ends
with a restart, but don't leave a serial monitor open while plotting.

Over Wi-Fi: see [Updates over Wi-Fi](#updates-over-wi-fi).

### 1. Flash MicroPython (only once)

Download the firmware from [micropython.org](https://micropython.org/download/):
[ESP32_GENERIC_C3](https://micropython.org/download/ESP32_GENERIC_C3/) for the C3 board,
[ESP32_GENERIC](https://micropython.org/download/ESP32_GENERIC/) for a classic ESP32. With
[esptool](https://docs.espressif.com/projects/esptool/) (`pip install --user esptool`):

```sh
esptool.py --chip esp32c3 --port /dev/ttyACM0 erase_flash
esptool.py --chip esp32c3 --port /dev/ttyACM0 write_flash -z 0x0 ESP32_GENERIC_C3-20260824-v1.29.0.bin
```

On a classic ESP32 use `--chip esp32`, its port (often `/dev/ttyUSB0`) and offset `0x1000`;
other chips take `ESP32_GENERIC_<chip>` (e.g. `ESP32_GENERIC_S3`) at offset `0x0`, except the S2
(`0x1000`) and the C5 (`0x2000`). `flash.sh` picks all this by itself from the chip it finds.
(On the C3 the firmware goes to offset `0x0`. If the port isn't found, hold BOOT while
plugging in the board.) Thonny can do the same via *Tools → Options → Interpreter → Install or
update MicroPython*.

### 2. Copy the files to the board

**Use `./flash.sh`** (see [flash.sh](#flashsh)): it does all of this, including the
precompiling below. By hand, these files go on the board:

| On your computer | On the board |
|---|---|
| `rescue.py` | `rescue.mpy` (precompiled) (Wi-Fi, updates over Wi-Fi, rescue server) |
| `plotter.py` | `plotter.mpy` (precompiled) (the plotter) |
| `ssd1306.py` | `ssd1306.mpy` (precompiled) (display driver) |
| `index.html` | `index.html` (the web page) |
| `board_default_settings.json`, `board_default_calibration.json` | the same (the defaults; the plotter doesn't start without them) |
| `board_settings.json` | `board_settings.json` (optional, see [Settings files](#settings-files)) |
| `main.py` | `main.py` (runs automatically at boot, starts the plotter; copy it last) |

**Why precompiled:** the ESP32-C3 has little RAM, and MicroPython normally compiles each `.py`
file on the board before running it, which needs far more memory than running it. Measured with
MicroPython 1.29 (the Linux build with a limited heap, so relative numbers): REV 10 and REV 15,
which run on the board, needed about 106 and 88 KB; REV 11, which didn't start at all (not even
the Wi-Fi), about 148 KB. REV 16 as `.py` sources needs about 133 KB (it still started on an
ESP32-C3 OLED board, so the real limit is somewhat higher), as `.mpy` files about 65 KB. REV 17
(with the precise calibration) needs about 71 KB as `.mpy` and about 154 KB as `.py`, REV 18
about 75 and 157 KB: install it with `flash.sh`, which uploads `.mpy` files. `mpy-cross` (installed by `requirements.txt`) compiles on the computer;
its version must match the board's MicroPython (1.29 here):

```sh
mpy-cross rescue.py && mpy-cross plotter.py && mpy-cross ssd1306.py
mpremote rm :rescue.py + rm :plotter.py + rm :ssd1306.py   # if there: a .py is loaded before the .mpy
mpremote cp rescue.mpy :rescue.mpy + cp plotter.mpy :plotter.mpy + cp ssd1306.mpy :ssd1306.mpy \
  + cp index.html :index.html + cp board_default_settings.json :board_default_settings.json \
  + cp board_default_calibration.json :board_default_calibration.json + cp main.py :main.py + reset
```

If the plotter doesn't fit in RAM (or fails for any other reason), the board starts the rescue
server instead (see [Updates over Wi-Fi](#updates-over-wi-fi)) and `GET /log` shows a
`MemoryError`. The web page is a separate file for the same reason: with the page embedded as a
string (REV 11) the script no longer fit. **Always upload the files of the same revision
together.**

With Thonny you can also save the `.py` files to the board (*File → Save as… → MicroPython
device*), for experiments: expect less free RAM.

Two more files appear on the board by themselves:

- `board_calibration.json`: this board's calibration, created the first time you press *Apply
  Config* (only what differs from the defaults).
- `job.gcode`: the last uploaded drawing (replaced by each new plot).

---

## Running and connecting

There are two ways to run it:

**A. From Thonny (while developing).** Open the script and press **Run (F5)**. All messages
(I2C scan, IP address, every page request) appear in Thonny's shell. Don't press Stop or
Ctrl-C while using the web page: that ends the server.

**B. Standalone.** Save it as `main.py`, close Thonny (or *Run → Disconnect*), then press
RESET or power-cycle the board.

> **Important:** when Thonny *connects* to the board it interrupts the running program to give
> you a `>>>` prompt. That stops the web server, but the OLED keeps showing its last text and
> the Wi-Fi access point stays up. That looks like "connected to Wi-Fi but the page won't
> load". The **blinking dot in the bottom-right corner of the OLED** shows the program is
> running; if it doesn't blink, the server is gone.

Then:

1. Connect your phone or laptop to the Wi-Fi **`ESP32-Plotter`**, password
   **`plotterpassword`** (or the network and name from your `board_settings.json`, see [Wi-Fi](#wi-fi)).
2. Open **`http://192.168.4.1`**. Type the `http://`, as browsers otherwise try https.
   On phones, turn mobile data off so the phone doesn't avoid a Wi-Fi network without internet.

**About the IP address:** in access-point mode the ESP32 always gives itself `192.168.4.1`
(the ESP-IDF default) and hands out `192.168.4.x` addresses to clients. The script reads the
real address with `ap.ifconfig()` and shows it on the OLED (it scrolls, as it's longer than
the 9 characters that fit). To use a different address, add after `ap.active(True)`:

```python
ap.ifconfig(('192.168.10.1', '255.255.255.0', '192.168.10.1', '8.8.8.8'))
```

### Wi-Fi

With the defaults (no networks) it opens the access point above. With a `board_settings.json` copied
from [`board_default_settings.json`](board_default_settings.json) and its networks and update password filled in, it sets its hostname, joins the first listed network
that is in range (it scans up to 3 times: a single scan often misses a network that is there), and
opens the `ap` access point only if none is. Both names get the MAC suffix (see `"mac_suffix"` in
[Hardware](#hardware)): with `"hostname": "plotter"` and `"ap": {"ssid": "plotter"}` a board
whose MAC ends in `60` is `plotter-60` on the network and opens the access point `plotter-60`. `board_settings.json` holds passwords:
it is in `.gitignore`, keep it out of the repository. Joined to a network, the board gets its
address from that network's DHCP (shown on the OLED and in the shell); the hostname also works
as `http://<hostname>-<xx>.local` where mDNS is available. If no network could be joined, the OLED
says why: during the boot each failure is shown for 2.5 s (`NO WIFI` / `wrong password`, `not
found`, `no answer` or `not in range` / the network's name), and afterwards the idle screen
alternates every 6 s between the reason and the access point's name and IP. The board waits up
to 30 s per network; it gives up earlier only if "wrong password" or "not found" persists for 10 s
(the ESP32 reports "wrong password" for a moment while it retries a handshake, so giving up at
the first report, as an earlier draft did, dropped good connections). If the connection drops later, the
OLED shows `WIFI LOST` / `rejoining` until it's back. "Not in range" also means a 5 GHz-only network or a typo in the name: the
ESP32 has 2.4 GHz only, and `/log` lists the networks it did see. If the `ap` settings can't be
used, the board opens `ESP32-Plotter-<xx>` / `plotterpassword` instead; `flash.sh` refuses an `ap`
password shorter than 8 characters and network entries without `ssid` or `password`.

### Updates over Wi-Fi

`./flash.sh` without the ESP32 on USB (or `./flash.sh --wifi`, or `./flash.sh <host>`) updates the
board over Wi-Fi; it needs an `ota_password` in `board_settings.json`, and the first version with
updates goes on by USB. Before uploading anything it makes sure it found the right thing:

1. It looks for the board at the given host, else at the boards installed from this folder
   (`boards.txt`, as `<name>.local`), at `<hostname>.local` (a board without the MAC suffix) and
   at `192.168.4.1` (its own access point: connect the computer to that Wi-Fi first). A device
   only counts if it answers like the plotter (`/status` with a revision) or like its rescue
   mode; any other web server at that address is ignored. If several plotters answer, it shows
   them by name and asks which one.
2. It refuses while a plot is running, paused or parking (the board refuses too).
3. It checks the password (`GET /auth`) and gets the board's MicroPython version from it, to
   precompile matching `.mpy` files. A board from before `GET /auth` is accepted if it can be
   updated at all; its password is checked with the first file.
4. It shows what it found and uploads (the question was asked up front).
5. It deletes stale counterparts (e.g. `plotter.py` when uploading `plotter.mpy`), uploads the
   files with `main.py` last, restarts the board and waits until it answers again with the new
   revision; if it comes back in rescue mode it prints the end of the log.

Each file is written to `<name>.tmp` and renamed only when complete, so a cut connection leaves
the old file in place.

If `plotter.py` fails to start (a bad update), `main.py` starts the rescue server from
`rescue.py` instead: Wi-Fi and updates keep working, every page answers 503 and `GET /log`
shows the error, so a fixed `plotter.py` can be uploaded over Wi-Fi. Only a broken `main.py`
or `rescue.py` needs USB; both are small and rarely change.
Joined to a network, the board checks it every 10 s and rejoins when it drops; it waits up to
30 s per network. Running its own access point because no network could be joined, it keeps
the access point steady and tries the networks again only at the next restart: trying takes
the radio off the access point's channel, and phones then couldn't even see it. `GET /log`
shows the last Wi-Fi and update events (seconds since boot).

### The OLED

Three lines of 9 characters (the built-in MicroPython font is a fixed 8x8 px, so 72x40 px fits
9 characters by 5 rows). Longer lines scroll automatically. Typical screens:

| Display | Meaning |
|---|---|
| `REV 18.0` / name / IP | Server running on your network, e.g. `plotter-60` and its IP |
| `REV 18.0` / `AP <name>` / IP | Server running on the board's own access point |
| `REV 18.0` / `NO WIFI` / reason, alternating with `REV 18.0` / `AP <name>` / IP | Couldn't join the networks from `board_settings.json` (see [Wi-Fi](#wi-fi)) |
| `WIFI` / `scanning` / `2/3` | The networks from `board_settings.json` weren't in the first scan, scanning again |
| `WIFI` / network / `joining` (then `25 s`, `20 s` ...) | Joining a network at boot |
| `WIFI LOST` / `rejoining` | Connection dropped, rejoining |
| `PARKING` / `120/840` | Plot ended or stopped, the arm is on its way to park |
| `UPLOADING` / `840 pts` | Receiving a drawing |
| `PLOTTING` / `120/840` / `14%` | Plotting, line 120 of 840 (updated every 5 s) |
| `PAUSED` / `120/840` | Plot paused |
| `REV 18.0` / `DONE` (or `STOPPED`, `ERROR`) / `840/840` | Plot ended |
| `S:-45` / `E:90` / `PEN UP` | After a jog or angle command |
| `NO SERVER` / `PRESS RST` | Port 80 still in use after a soft reboot; press RESET |

If the display stops responding (e.g. after a servo brownout), the code re-initialises it
automatically (at most every 5 s), and the **Refresh OLED** button forces a full restart. A
display that wasn't found at boot isn't retried (no I2C traffic on a board without one), except
with *Refresh OLED*, e.g. after plugging one in.

While a plot runs, the heartbeat dot and scrolling pause (each display update briefly holds up
the motion), and only the progress is updated every 5 s.

**The board's LED** (GPIO 8 on C3 boards, GPIO 2 on a classic DevKit, see `"led"` in
[Hardware](#hardware)) tells the same story without a display:

| LED | Meaning |
|---|---|
| Slow blink, in step with the OLED's dot (about 1 s on, 1 s off) | Plotter running, idle |
| Fast blink (about 1.5 per second) | Plotting or parking |
| Three quick flashes every 2 s | Rescue mode: the plotter failed to start, see `GET /log` |
| Off or steady | The program isn't running (e.g. stopped by Thonny or `mpremote`) |

---

## The web interface

1. **Motor Pin Diagnostic Tests.** Sweep each servo (1200 → 1500 → 1800 → 1500 µs) to check
   wiring; the arm returns to where it was afterwards.
2. **Safe Nudge Controls.** Move shoulder/elbow by ±5°, toggle the pen, go to bed centre
   (−45°, 90°), refresh the OLED. The readout shows the position the ESP32 actually reports.
3. **Direct Motion Test.** Draws a 30x30 mm square in the middle of the drawing area: the
   quickest check of calibration (it should be square, with straight sides).
4. **File Plotter (SVG).** Load an SVG, check the preview, press *Plot Drawing*. The preview and
   the *Plot Drawing* button are shared by sections 4–6: whatever was loaded or created last is
   what gets plotted.
   - Supported: `path`, `rect`, `circle`, `ellipse`, `line`, `polyline`, `polygon`, and any
     `transform` (scale/rotate/translate, also on groups). Shapes inside `<defs>`, masks etc.
     are skipped.
   - Not supported: text (convert to paths first, e.g. Inkscape *Path → Object to Path*),
     embedded images, fills (only outlines are drawn).
   - The drawing is scaled to fill 90 % of the drawing area, keeping its proportions.
   - A path made of several pieces (each "M" in the path data starts one, e.g. the letters of
     a word) is drawn piece by piece with the pen lifted in between.
   - Example drawings to try: `images/redhat.svg`, `images/redhat-logo.svg`,
     `images/switzerland-map-country.svg` (from [SVG Repo](https://www.svgrepo.com)).
   - **Rotate 90° when that makes the drawing bigger** (on by default): a drawing whose shape
     suits the area better when turned, typically a portrait image on the landscape drawing
     area, is rotated 90° clockwise (its top ends up on the right). The status line then says
     *rotated 90°* and shows the final size in mm. The drawing area itself doesn't change, so
     the paper stays where it is; turn the paper to view the result upright. Untick it to always
     plot upright.
   - The status line shows the number of lines/points and a rough time estimate.
5. **Image to Plot.** Converts a JPG/PNG into lines in the browser; see
   [below](#image-to-plot-conversion).
6. **Text.** Writes your text in a single-stroke font; see [below](#text).
7. **Live Calibration.** Calibration, speed, hysteresis correction, arm lengths and drawing
   area; saved to `board_calibration.json` on the board and loaded at every boot. See
   [Calibration](#calibration).

### Plotting

Pressing *Plot Drawing* (or *Draw Test Square*):

1. uploads the drawing to the board in chunks of about 3 KB (the status line shows
   *Uploading: n%*),
2. starts the plot. From then on **the board plots on its own**: you can lock the phone,
   switch apps, close the page or walk out of Wi-Fi range.
3. While the page is open it shows a progress bar with **Pause/Resume** and **Stop**.
   Pausing lifts the pen; resuming lowers it again and carries on from the same point. A
   stopped plot can't be resumed.
   After the last line, or after *Stop*, the arm returns to its park pose at drawing speed,
   which takes a few seconds; meanwhile the page says *Plot ended at n / total, parking the
   arm...* and the board still refuses other motion and settings.
4. If you reopen the page during a plot (e.g. after the phone slept), it picks up the progress
   display again.

While a plot runs, is paused or parks, the board refuses motion commands (jog, tests, other
plots, *Apply Config*), restarts and file updates with *busy: a plot is in progress*.

### Keeping the screen on

During a plot the page asks the browser to keep the screen on (Screen Wake Lock). Browsers
only allow this on secure (`https://`) pages, and `http://192.168.4.1` doesn't count, so by
default the note under the progress bar says the screen may turn off. **That's fine: the plot
continues on the board.** If you still want the screen on (to watch the progress), in Chrome
on Android:

1. Open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.
2. Enter `http://192.168.4.1` and set the flag to *Enabled*.
3. Restart Chrome. The note then says *Screen kept on while plotting*.

Alternatively, Android's developer options have *Stay awake* (while charging).

---

## Calibration

Each arm servo's pulse width is a straight line through one known point:

```
pulse_us = park_pw + (angle_deg − park_angle) × slope
```

Defaults from `board_default_calibration.json` (the only place they are kept); what you set on the
web page is saved on the board in `board_calibration.json`, only where it differs from the defaults
(see [Settings files](#settings-files)):

| Field | Config key | Default | Meaning |
|---|---|---|---|
| S1 Park | `s1_ang` | −90 | Shoulder angle (°) at which the shoulder servo gets `s1_pw` (1500 µs) |
| S1 Slope | `s1_slope` | −10 | µs per degree for the shoulder (negative = mounted reversed) |
| S2 Park | `s2_ang` | 90 | Elbow angle (°) at `s2_pw` (1500 µs) |
| S2 Slope | `s2_slope` | −10 | µs per degree for the elbow: negative with the elbow servo on the upper arm (the 3D-printed arms), +10 with it on the lower arm (wooden arms, as in the original BrachioGraph): set it on the web page |
| Pen Up / Pen Down | `pen_up`, `pen_down` | 1450 / 1850 | Pen servo pulse widths (µs) |
| Speed | `speed` | 25 | Drawing speed in mm/s |
| S1 / S2 Hysteresis | `s1_hyst`, `s2_hyst` | 0 | Backlash correction in µs (see below) |
| Inner / Outer arm | `L1`, `L2` | 80 / 80 | Arm lengths in mm, pivot to pivot (outer: elbow to pen tip) |
| Area X/Y min/max | `bed_minx` … `bed_maxy` | −87, 30, 45, 128 | Drawing area in mm |
| (section 8) | `s1_table`, `s2_table` | empty | Measured [angle, pulse] points of the [precise calibration](#precise-calibration-optional); with two or more they replace Park and Slope for that joint |
| View | `flip` | 1 | 1 turns every drawing 180° for someone in front of the robot, on the paper side; 0 for someone behind it (the preview turns with it) |

Angles: shoulder 0° = arm pointing straight ahead (+Y), negative = to the left. The elbow angle
is measured relative to the inner arm (0° = straight, 90° = right angle).

A practical procedure:

1. Press **Bed Center** (−45°, 90°). The inner arm should point 45° to the left and the outer arm
   should be at a right angle to it.
2. If the shoulder is off, adjust **S1 Park** (it shifts the whole shoulder range); likewise
   **S2 Park** for the elbow.
3. Jog ±5° several times and check that 5° on screen is 5° on the arm. If the arm moves too
   much or too little, adjust the **slope** (e.g. −10 → −11 for more movement). If it moves the
   wrong way, flip the sign.
4. Set **Pen Up** so the pen clears the paper and **Pen Down** so it touches lightly.
5. Draw the test square and fine-tune.

`s1_pw` and `s2_pw` have no field on the page: change them in `board_default_calibration.json` (for
every board) or with `CFG s1_pw=1520` (for one board, see [HTTP API](#http-api)). If you paste values from a Raspberry Pi BrachioGraph, the mapping is
`servo_1_parked_pw → s1_pw`, `servo_1_degree_ms → s1_slope`,
`servo_1_parked_angle → s1_ang`, `hysteresis_correction_1 → s1_hyst` (same for servo 2).

### Speed

Lower is steadier: try 15–25 mm/s for drawings with fine detail, up to 40–50 mm/s for large
simple shapes. Travel moves (pen up) aren't affected; they glide at about 50°/s, ramped up and down.

### Hysteresis correction

Servo gears have some slack, so a joint stops slightly short in whichever direction it was
moving. A circle then comes out with small steps where the movement reverses, and lines drawn
in opposite directions don't overlap. The correction adds half the value to the pulse while it
rises and subtracts half while it falls (as in the original BrachioGraph).

To set it: draw the test square, then look at the corners and at lines drawn back and forth.
If they are offset, increase the value for the joint concerned in steps of about 5 µs
(cheap SG90 servos typically need 5–15 µs, metal-gear servos less). Too much makes the
offset appear the other way round.

### Arms and drawing area

Enter the arm lengths (L1 = shoulder pivot to elbow pivot, L2 = elbow pivot to pen tip), then
press **Auto-fit Area to Arms**. It finds the largest landscape area with A-paper proportions
(1 : 1.414) where every point:

- is reachable with the elbow between 25° and 150° (not stretched straight, which is
  imprecise, and not folded into the shoulder), and
- keeps both arm servos within 800–2200 µs with the current calibration.

Check the values and press **Apply Config**. The preview and the fit of loaded drawings update
immediately. You can also type the area by hand.

The area usually ends up off-centre to the left, as in the original BrachioGraph: with the
default calibration the shoulder servo covers about 70° either side of −90°, which reaches
further on the left side. Calibrate first, so auto-fit works with the real servo range.

Approximate largest area by arm length (both arms equal):

| Arms | Geometry only | With default calibration | Paper |
|---|---|---|---|
| 80 mm | 135 × 96 mm | 118 × 84 mm | ~A7 |
| 120 mm | 206 × 145 mm | 180 × 127 mm | ~A6–A5 |
| 150 mm | 256 × 181 mm | 225 × 159 mm | ~A5 |
| 180 mm | 309 × 218 mm | 271 × 192 mm | ~A4 |

Longer arms come at a price: the same angular slop of the servos gives a proportionally larger
error at the pen (1.5× with 120 mm arms), and longer arms are heavier and flex more. With
arms over 100 mm, metal-gear servos (e.g. MG90S) are strongly recommended.

**The area warning:** under the area fields the page warns when part of the saved drawing area
is out of reach, needs a pulse beyond the drawing limits of 750–2250 µs (the board clips it, so
drawings come out squashed there), or needs the elbow almost straight or folded. With the default
calibration and the standard mounting this is the case for the original area X −50…50, Y 80…150:
its far right corner would need about 504 µs from the shoulder. *Auto-fit Area to Arms* finds an
area that fits.

**Why the middle of the servo's travel matters:** the shoulder's park pulse (1500 µs, the servo's
middle) points at −90°, while the drawing needs roughly −120° to 0°. The shoulder therefore works
mostly on one side of its middle, where cheap servos are least linear, and runs out of travel on
the right. Mounting the upper arm so that 1500 µs points at about −60° instead (S1 Park −60, arm
remounted after *Park*) puts the drawing in the middle of the shoulder's travel; auto-fit then
finds about 136 × 96 mm instead of 121 × 85 mm with 80 mm arms. This is optional: the standard
park pose stays the default, and the precise calibration below corrects either mounting.

### Precise calibration (optional)

Park and Slope describe each servo as a straight line through one point. Real servos, cheap ones
especially, deviate from that line, more the further they turn from the middle: drawings then come
out slightly distorted towards the edges. Section 8 of the page measures where each servo really
is, on a printed template, and replaces Park and Slope by the measured points. It's an extra step:
mount and calibrate as usual first.

**The templates** (`images/template.py` draws them from the robot's geometry; print at 100 % and
check the 10 cm bar on the sheet) are made for 80 mm arms. They use the plotter's own coordinates in
cm: the shoulder axis is at 0,0, X to the robot's right (on the sheet: to the left, as the robot
faces down the page), Y away from the robot. The robot is held by the two A4 filing holes: the
shoulder axis is on the line through them, in the middle, 12 mm from the A4 sheet's edge (from
`3d-prints/base.stl` and `a4-bracket.stl`). Both templates are A4 landscape sheets punched the same
way with an office 2-hole punch, and both hang on the robot's two pins:

| Template | Where it lies | For |
|---|---|---|
| [template-a4-front.pdf](images/template-a4-front.pdf) | In front of the robot, the robot at its top edge | Drawing, *Draw 1 cm Grid*, the elbow lines and the shoulder lines from −90° to +90° |
| [template-a4-back.pdf](images/template-a4-back.pdf) | Behind the robot: printed like the front (holes at the top edge), then turned round and put on the same pins; the two sheets overlap by 24 mm at the pins. Its text is printed upside down, so it reads the right way once turned | The shoulder lines behind the robot (−90° to −180°, +90° to +180°), which the precise calibration needs down to −120° |

**Checking the print:** the 10 cm bar must measure exactly 10 cm, and the printed hole circles must
sit on the punched holes. Printers often shift the whole print by 1–3 mm (and lose a few mm at
the bottom edge); a punch may also not hit exactly 12 mm from the edge. If the punched holes sit,
say, 3 mm nearer the edge than the printed circles, draw a corrected pair:
`python3 images/template.py --shift-y 3 --out ~/Downloads` (needs `pip install reportlab`).
`images/template-a4-front-shift3mm.pdf` and `images/template-a4-back-shift3mm.pdf` are such a pair,
3 mm towards the holes, tested with a laser printer and an office punch: try them first if the
exact ones don't fit your printer.

The original BrachioGraph template (`images/template-grid.pdf`, robot in a cut-out at the bottom of
a portrait page) has the same lines and grid, for robots set up that way.

- **Red lines** start at the shoulder axis: shoulder angles (0° straight ahead, i.e. down the page;
  −90° to the robot's left, i.e. to the right on the page). The front sheet shows −90° to +90°,
  the back sheet the rest.
- **Blue lines** start at 0,8 cm, where the elbow is when the shoulder is at 0° with an 80 mm upper
  arm: elbow angles relative to the upper arm, from 15° to 165° (0° would continue straight on).
- The green dashed rectangle is the default drawing area.

**Steps:**

1. Put both templates on the robot's two pins (the back sheet turned round, behind the robot).
   Tape them down.
2. **Calibrate Shoulder:** the page names a red line (−120° to +15°, every 15°) and moves the
   shoulder there with the current calibration. Move it onto the line with the fine buttons
   (±1, ±5, ±20 µs; 1 µs is about 0.1°) until the line from the shoulder pivot through the elbow
   pivot lies on it, looking straight down onto the pivot holes, then press **Here**. Lines the
   servo can't reach: **Skip This Line**. After the last line the points are saved and the arm
   parks.
3. **Calibrate Elbow:** the page turns the shoulder to 0° (with the points just measured), so the
   upper arm lies on the red 0° line and the elbow is over 0,8. Then the forearm (elbow pivot to
   pen tip) goes onto the blue lines (15° to 150°) the same way.
4. **Check:** *Draw 1 cm Grid* draws the drawing area's 1 cm grid lines in the robot's own
   coordinates, onto the template: they should land on the printed grid. Where they don't, you see
   how far off the robot is and where.

**Approach each line from both sides** (optional checkbox): for every line the page first starts a
little below it (approach with the + buttons only, press Here), then a little above it (approach
with the − buttons only, press Here). The servo stops slightly short in either direction because
of the play in its gears; the middle of the two pulses is saved as the point, and their average
difference over all lines is set as that joint's [hysteresis correction](#hysteresis-correction),
measured instead of guessed. Twice the work.

**Limits and checks:**

- During the calibration the servos may go from 500 to 2500 µs (the drawing limit is 750–2250):
  with the standard mounting the shoulder needs about 600 µs for 0°, which the elbow step needs.
  Near the ends the page warns; if the servo buzzes against its stop, go back and skip the line.
- Pressing Here at the 500/2500 µs limit is refused (the servo may not really be on the line).
- Each line's pulse must continue the trend of the previous ones (keep rising or keep falling):
  otherwise the page asks to check the alignment or skip, and the board refuses the table too.
- Between measured points the board interpolates in a straight line; beyond the first and last
  point it continues the end segments. Auto-fit and the area warning use the measured points.
- *Delete Measured Points* goes back to Park and Slope for both joints. Changing Park or Slope has
  no effect on a joint with measured points.

To return to the defaults, delete `board_calibration.json` from the board (Thonny's file view) and reset.

---

## Image to plot conversion

Section 5 turns a photo or drawing into pen lines entirely in the browser (nothing is uploaded
to the ESP32 except the final coordinates). It works like the original BrachioGraph
`linedraw.py`:

1. The image is scaled down to *Detail* pixels wide, converted to grayscale and
   contrast-stretched.
2. **Outlines:** the image is blurred slightly and traced at mid-grey with *marching squares*,
   giving closed contour lines around dark areas.
3. **Hatching:** dark areas are filled with parallel lines. Each extra layer adds another
   direction (45°, 135°, 0°, 90°) over progressively darker areas, so shadows get cross-hatched.
4. The lines are fitted to the drawing area and simplified (points within 0.2 mm of a straight
   line are dropped). The preview shows the result; press *Plot Drawing* to draw it.
   Straight hatch lines need only their two end points; the board draws the line between them.

| Setting | Effect |
|---|---|
| Detail (px) | Working resolution. Higher = finer outlines but many more points. 120–200 is a good range. |
| Hatch spacing | Distance between hatch lines in working pixels. Smaller = darker and slower. |
| Hatch layers | 0 = outlines only; 1–4 = more shading levels. |
| Darkness | Multiplies all thresholds: >1 draws more (lighter areas get lines), <1 draws less. Accepts `1.5` or `1,5`. |
| Outlines | Turn contour tracing on/off. |
| Invert | Swap dark and light (for white-on-black images). |

The preview updates as soon as you change a setting (*Re-convert* does the same by hand), and
the status line confirms the settings used, e.g. *Converted (darkness 1.5, 2 layers)*. If the
settings leave nothing to draw, the preview is cleared and plotting is disabled.

*Download SVG* saves the result as polylines, to keep or edit in Inkscape. Loading that SVG in
section 4 gives the same plot.

Tips: high-contrast images with simple backgrounds work best. Start with 1–2 layers and check
the time estimate; a full-page cross-hatch can mean thousands of points.

---

## Text

Section 6 writes up to 3 lines of text, laid out in the browser like the image conversion (the
board only receives the finished lines, so it costs no RAM on the ESP32; the fonts add about
13 KB to the web page file).

Plotters need **single-stroke fonts**: normal computer fonts (TTF/OTF) describe letter
outlines, so every letter would be drawn as a hollow double line. The page uses three of the
classic **Hershey fonts**, made for plotters:

| Font | Hershey name | Looks like |
|---|---|---|
| Script (handwriting) | Script Simplex (`scripts`) | Flowing cursive, letters join up |
| Block | Roman Simplex (`futural`) | Clean thin sans serif, fastest to plot |
| Block bold | Roman Duplex (`futuram`) | Two strokes per line, good for banners |

How it works:

- Type the text; the preview updates after a short pause. Line breaks you type are kept; long
  lines wrap at word boundaries (very long words are split). At most **3 lines** and 120
  characters.
- **Largest that fits** (on by default): the page picks the biggest letter height at which the
  text fits the drawing area (minus the usual 10 % margin) in at most 3 lines, and shows it in
  the *Letter height* field. This may spread a short phrase over several lines to make it
  bigger; for a one-line banner, untick it and enter a height.
- **Letter height** is the height of capital letters in mm. With *Largest that fits* unticked,
  the text is set at exactly that height; if it doesn't fit in 3 lines the status line says so.
- **Align**: centre or left.
- Text is always plotted upright (the rotate option of section 4 doesn't apply).
- **Characters**: the printable ASCII set (letters, digits, punctuation). German umlauts
  (ä ö ü Ä Ö Ü) are drawn as the base letter plus two dots, ß as "ss"; other accented letters
  lose their accent (é → e); anything else becomes "?".
- *Block bold* draws each line twice, about 1 font unit apart (≈ 0.05 × the letter height).
  Up to about 10 mm letters the two strokes merge into a bold line; at larger sizes they show
  as a hollow outline.

Font acknowledgement (required by the Hershey fonts' terms of use, which allow use for any
purpose): *The Hershey Fonts were originally created by Dr. A. V. Hershey while working at the
U. S. National Bureau of Standards. The format of the font data in this distribution was
originally created by James Hurt, Cognition, Inc., 900 Technology Park Drive, Billerica, MA
01821.* The font data was taken from the
[hershey-fonts](https://github.com/kamalmostafa/hershey-fonts) package (`.jhf` files) and
converted into JavaScript strings in the web page.

---

## How it works

```
Browser                                       ESP32 (MicroPython, uasyncio)
-------                                       -----------------------------
SVG / image → polylines in mm
           → simplified G0/G1 list
           ── JOB_BEGIN, POST /job chunks ──▶  job.gcode on flash
           ── JOB_START ────────────────────▶  plot task reads it line by line:
                                                 inverse kinematics (x,y → angles)
           ◀── GET /status once a second ───     G1: straight line, one step per 20 ms
               (progress, pause, stop)           G0: pen up, ramped glide
                                                 angles → hysteresis → pulse → PWM
```

- **Upload, then plot**: the whole drawing is stored in `job.gcode` on the board's flash (it
  has about 1–2 MB free; a typical drawing is 10–100 KB) and plotted by the board itself. There
  are no network pauses between points (earlier versions sent one point per HTTP request, so
  the arm stopped for 50–100 ms at every point), and the phone isn't needed during the plot.
  Before the upload, the board checks there is enough free flash.
- **Servo frames**: hobby servos read a new position every 20 ms (50 Hz). Every motion step
  waits for the next 20 ms slot, so each step is actually seen by the servo and the motion is
  even. If the board falls behind (e.g. while answering a request), it carries on from the
  current time rather than rushing to catch up.
- **Straight lines at constant speed**: moving both joints directly between two far-apart
  points draws an arc, so a `G1` line is cut into steps of `speed × 20 ms` (0.5 mm at 25 mm/s),
  each converted to joint angles. Every line takes at least one frame, so the speed stays even
  however many points a drawing has.
- **Inverse kinematics**: for pen position (x, y), with `h = √(x² + y²)`:
  shoulder = `asin(x/h) − acos((h² + L1² − L2²) / (2·h·L1))`,
  elbow = `π − acos((L1² + L2² − h²) / (2·L1·L2))`. Points beyond reach are clamped
  to the arm's maximum length.
- **Gliding**: travel moves and jogs are split into small steps, one per frame, instead of
  jumping at full servo speed. The steps follow a ramp (smoothstep): speed rises from zero to
  ≈75°/s and falls back to zero at the target, ≈50°/s on average. A constant 100°/s glide with
  abrupt starts and stops threw 80 mm 3D-printed arms past their target and made them swing
  back.
- **Pen handling**: on a travel move (`G0`) the pen is lifted and given 150 ms to rise before
  the arm moves; on a drawing move it is lowered only after arriving.
- **Display**: the OLED runs at 400 kHz I2C, so a full redraw (~10 ms) fits inside one servo
  frame.

---

## HTTP API

All on port 80. Every response closes the connection.

| Request | Response |
|---|---|
| `GET /` | The web page (`index.html` from flash) |
| `GET /config` | The calibration in effect (defaults and this board's changes) as JSON |
| `GET /calibration` | This board's own `board_calibration.json` (only what differs from the defaults), for `flash.sh --get-calibration` |
| `GET /status` | `{"rev": revision, "job": state, "line": n, "total": n, "s": deg, "e": deg, "p": 0/1}`; state is `idle`, `uploading`, `running`, `paused`, `parking` (plot ended, arm returning to park), `done`, `stopped` or `error` |
| `POST /cmd` body = command | `OK S=<shoulder> E=<elbow> P=<0/1>` or `400 ERR <message>` |
| `POST /job` body = G-code lines | Appends to the job being uploaded (max 8 KB per request) |
| `PUT /file/<name>`, basic auth `ota:<ota_password>` | Writes the file to flash (`<name>.tmp`, then renamed); refused (409) during a plot |
| `DELETE /file/<name>`, basic auth | Deletes the file (used to remove a `.py` when its `.mpy` is uploaded) |
| `GET /auth`, basic auth | `OK <MicroPython version>` if the password is right, else 401 (used by `flash.sh`) |
| `POST /reset`, basic auth `ota:<ota_password>` | Restarts the board |
| `GET /files` | Files on the board's flash, one `size name` line each |
| `GET /log` | Boot (with `machine.reset_cause()`: 1 power on, 2 reset button or `/reset`, 3 watchdog, 5 soft reset), Wi-Fi, update and error events, one per line, seconds since boot first; kept in flash (`log.txt`, rotated to `log.old` at 8 KB) |

Commands for `POST /cmd`:

| Command | Action |
|---|---|
| `G0 X<mm> Y<mm>` | Travel with pen up |
| `G1 X<mm> Y<mm>` | Draw a straight line with pen down |
| `ANG S=<deg> E=<deg> P=<0/1>` | Move joints directly to these angles |
| `CFG <key>=<value> ...` | Set any settings by their config key (e.g. `CFG speed=20 s1_hyst=8`) and save to `board_calibration.json` |
| `PW S=<us> E=<us>` | Precise calibration: move the shoulder (S) and/or elbow (E) servo to exact pulses (500–2500 µs), gliding, without hysteresis correction |
| `CAL s1\|s2 <angle>:<us>,...` | Save the measured points of a joint (at least two; pulses must keep rising or falling with the angle); `CAL s1` alone deletes them |
| `JOB_BEGIN <lines> <bytes>` | Start a new upload (checks free flash) |
| `JOB_START` | Start plotting the uploaded job |
| `JOB_PAUSE`, `JOB_RESUME`, `JOB_STOP` | Control the running plot |
| `TEST_SHOULDER`, `TEST_ELBOW`, `TEST_PEN` | Servo sweep tests |
| `TEST_OLED` | Re-initialise the display |
| `DONE` | Lift the pen and show DONE |

While a job runs or a single command is still moving the arm, `G0`, `G1`, `ANG`, `PARK`, `CFG`,
`PW`, `CAL`, the tests, `DONE`, `JOB_BEGIN` and `JOB_START` are refused (the page sends the precise
calibration's nudges one after another, so none get lost). A `G0`/`G1` target farther than `L1 + L2`
from the shoulder is refused with `out of reach`; in a job it ends the plot with `error`.
Single `G0`/`G1` commands still work without a job (optionally followed by `|text` to show on the
OLED, e.g. `G1 X10 Y100|5/200`).

Examples from a computer on the plotter's Wi-Fi:

```sh
# single moves
curl -d 'G0 X-10 Y100' http://192.168.4.1/cmd
curl -d 'G1 X10 Y100'  http://192.168.4.1/cmd

# plot a G-code file as a job (file must be under 8 KB per POST)
printf 'G0 X-10 Y100\nG1 X10 Y100\nG1 X10 Y120\n' > job.txt
curl -d "JOB_BEGIN 3 $(wc -c < job.txt)" http://192.168.4.1/cmd
curl --data-binary @job.txt http://192.168.4.1/job
curl -d 'JOB_START' http://192.168.4.1/cmd
curl http://192.168.4.1/status
```

---

## Tuning constants

In the script:

| Constant | Default | Purpose |
|---|---|---|
| `PIN_SHOULDER`, `PIN_ELBOW`, `PIN_PEN` | from `"pins"` in the settings: 0, 1, 3 (C2/C3/C5/C6), 25, 26, 27 (classic), 4, 5, 6 (S2/S3) | Servo GPIOs (see [Settings files](#settings-files)) |
| `OLED_SDA`, `OLED_SCL`, `OLED_W`, `OLED_H` | 5, 6, 72, 40 | Display |
| `PEN_SETTLE_MS` | 150 | Wait after pen up/down before moving |
| `FRAME_MS` | 20 | Servo frame period (50 Hz); one motion step per frame |
| `GLIDE_STEP_DEG` | 1.0 | Average joint step per frame for travel and jogs (≈50°/s), ramped up and down |
| `MAX_CHUNK` | 8192 | Largest accepted upload chunk (bytes) |
| `config` | see [Calibration](#calibration) | The calibration in effect: `board_default_calibration.json`, then `board_calibration.json` |

In the web page's script:

| Constant | Default | Purpose |
|---|---|---|
| `SIMPLIFY_MM` | 0.2 | Line simplification tolerance |
| `CHUNK_CHARS` | 3000 | Upload chunk size |

The drawing area is no longer a constant in the page: it comes from the board's settings.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Thonny's shell shows nothing | The program was started at boot before Thonny connected. Run it with F5 from Thonny instead. |
| No Wi-Fi network at all, OLED blank or unchanged after boot | `main.py` or `rescue.py` didn't start. Run `main.py` with F5 in Thonny to see the error, or reflash with `./flash.sh`. |
| LED flashes three times every 2 s, pages answer "The plotter failed to start" | Rescue mode: `http://<board>/log` shows the error. `MemoryError`: the plotter was uploaded as `.py` sources and doesn't fit in RAM; update with `./flash.sh` (installs precompiled `.mpy` files). Anything else: a broken update; fix it and update over Wi-Fi. |
| `flash.sh` skips your board ("not a usual ESP32 adapter") | Its USB-serial chip isn't in the list: give the port explicitly, `./flash.sh /dev/ttyUSB0`. |
| `flash.sh` says "No plotter found" over Wi-Fi | Not on the same network, `<hostname>.local` not resolved (needs avahi/nss-mdns), or the board is on its own AP: connect to it, or give the IP shown on the OLED. |
| Page shows "index.html is missing on the board" | Upload `index.html` to the board. |
| Page looks outdated or buttons don't work after an update | Script and page are from different revisions; upload both from the same revision and reload the page. |
| Wi-Fi connects but the page doesn't load | Most often Thonny stopped the program (OLED dot not blinking): reset the board with Thonny disconnected. Otherwise use `http://` explicitly and turn off mobile data. |
| The board's access point doesn't show up at all, or joining your Wi-Fi fails now and then ("wrong password" with the right one) | The board can't transmit cleanly at high power: set `"txpower": 8.5` in `board_settings.json` (C3 boards already default to 13). |
| Page loads sometimes, connection unstable | Same cause: lower `"txpower"`. |
| `flash.sh` says "MicroPython doesn't answer" on a board that has it | The program on it is stuck, or a terminal (Thonny) has the port: close it, unplug and replug the board, try again. `--firmware` reinstalls it (settings are saved first). |
| OLED shows `NO SERVER` | Port 80 still held after a soft reboot: press RESET. |
| OLED blank | Check the shell: `OLED HW I2C scan: []` means no display found (wiring/power). Any other error: re-upload the original `ssd1306.py`. |
| Board resets or OLED glitches when servos move | Brownout: check the capacitor (polarity, firmly in the breadboard), use a stronger USB power supply, or power the servos separately. |
| Board opens `ESP32-Plotter` instead of joining your Wi-Fi, `/log` says `board_settings.json invalid` | A typo in `board_settings.json` (it was uploaded without `flash.sh`, which checks it): fix it and flash by USB. |
| `/log` says `board_default_settings.json missing` or the plotter fails with `no servo pins` / `board_default_calibration.json missing` | The defaults files weren't uploaded (e.g. files copied by hand): run `./flash.sh`. The board still opens its access point and the rescue server meanwhile. |
| `flash.sh`: "updates over Wi-Fi need ota_password" | Wi-Fi updates need the board's update password in your `board_settings.json` next to `flash.sh` (the board doesn't hand out its settings). |
| Servo buzzes at rest | It's pushing against something or the pulse is at its limit; check calibration and mechanical range. |
| Lines are curved instead of straight | Calibration slopes or park angles are off; recalibrate with the test square, or do the [precise calibration](#precise-calibration-optional). |
| ⚠ warning under the drawing area fields | Part of the area is beyond the servos' limits or reach (see [Arms and drawing area](#arms-and-drawing-area)): *Auto-fit Area to Arms*, then *Apply Config*. |
| Precise calibration: "That doesn't fit the previous lines" | The arm isn't on the named line, or the servo is at its end: check the alignment, or *Skip This Line*. |
| `flash.sh`: "no access to /dev/ttyUSB0" | Serial ports belong to root and the group `dialout`. Not in it: run `./deploy_udev.sh` once on the computer itself (not in a toolbox), then plug the ESP32 in again. In it: log out and in again. |
| Lines are shaky or wobbly | Lower the speed; check the capacitor across the servo supply; check the arms and pen holder for play; metal-gear servos help a lot. |
| Small steps where lines reverse, back-and-forth lines don't overlap | Servo backlash: set the hysteresis correction. |
| Pen drags between shapes | Increase `PEN_SETTLE_MS` or lower `pen_up` (more lift). |
| *busy: a plot is in progress* | A plot is running or paused; stop it first (progress bar buttons, or `curl -d JOB_STOP .../cmd`). |
| *drawing too big* on upload | Not enough free flash; simplify the drawing (fewer hatch layers, less detail) or delete files from the board. |
| Upload works but nothing moves after a reset | A plot doesn't survive a reset; upload it again. |
| Note says "Screen may turn off" | Normal on `http://`; the plot continues on the board. See [Keeping the screen on](#keeping-the-screen-on). |

---

## Files and revision history

| File | Description |
|---|---|
| `main.py` | Starts the plotter, or the rescue server if the plotter fails |
| `plotter.py` | **Current version** of the plotter |
| `rescue.py` | Wi-Fi, updates over Wi-Fi, event log and the rescue server |
| `index.html` | Its web page |
| `ssd1306.py` | OLED driver (MicroPython standard driver) |
| `deploy_udev.sh` | Gives the logged-in user access to the ESP32's USB port, see [flash.sh](#flashsh) |
| `flash.sh` | Installs and updates the board by USB or Wi-Fi, see [flash.sh](#flashsh) |
| `requirements.txt` | The computer-side tools: esptool, mpremote, mpy-cross |
| `README.md`, `PARTS.md` | Build guide and parts list |
| `images/`, `3d-prints/` | Pictures for the README (drawn by `images/draw.py`), the templates `template-a4-front.pdf` and `template-a4-back.pdf` (and a pair shifted 3 mm for printers that print lower, `*-shift3mm.pdf`; drawn by `images/template.py`), the original BrachioGraph `template-grid.pdf`, example SVGs, and the printed parts |
| `board_default_settings.json`, `board_default_calibration.json` | The defaults for the board settings and the calibration (see [Settings files](#settings-files)) |

Revisions (up to 15 each in its own file in the original working folder; from 16 on in git):

| File | Changes |
|---|---|
| 1 | First ESP32 port, no display |
| 2–3 | OLED status output; file 3 is the version in the git repo |
| 4 | Revision string on screen, 4-line OLED layout, web UI with jog controls and test square |
| 5 | 100 kHz I2C with SoftI2C fallback, servo relax, raw centre |
| 6 | Servo sweep tests, OLED back to the file 3 setup |
| 7 | OLED errors printed and auto-recovery; pen lifted before travel; exact HTTP body reads; sockets always closed; readout synced with the board; tests restore position; pen calibration in UI |
| 8 | Real IP from `ap.ifconfig()`; scrolling OLED lines; heartbeat dot; clear error if port 80 is busy; request logging |
| 9 | Calibration saved in `config.json`; gliding moves; SVG loader supports all basic shapes and transforms |
| 10 | Image → line drawing in the browser (outlines + hatching, SVG download); straight G1 lines split on the board; path simplification; page sent in chunks to save RAM |
| 11 | (Doesn't start on the board: too big to compile in RAM, so no Wi-Fi. Use 12.) Drawings uploaded to the board's flash and plotted by the board (pause/resume/stop, progress, survives phone sleep); motion synced to 20 ms servo frames at constant, adjustable speed; hysteresis correction; arm lengths and drawing area editable, with auto-fit; screen wake lock when the browser allows it; OLED at 400 kHz |
| 12 | Same features as 11; web page moved to a separate file (`index.html`) streamed from flash, so the script compiles and runs in the C3's RAM again (needs less memory than REV 10); revision badge read from the board |
| 13 | Image to Plot: preview updates on every setting change; loaded image kept in the page's own canvas (mobile browsers could discard it, so re-convert did nothing); Darkness accepts a decimal comma; errors shown instead of failing silently; "nothing to draw" clears the preview |
| 14 | Option to rotate drawings 90° when that makes them bigger (portrait images on the landscape area); status line shows the final size in mm |
| 15 | Text section: up to 3 lines in Script, Block or Block bold Hershey single-stroke fonts, word wrap, largest-that-fits or fixed letter height, German umlauts; plot button renamed *Plot Drawing* |
| 16 | Split into `main.py`, `plotter.py`, `rescue.py` (colleague's branch: joining a Wi-Fi via `customization.json`, updates over Wi-Fi with a rescue server, event log, classic ESP32 and other chips, park pose, view from the paper side, ramped glide, servos started one after another); installed as precompiled `.mpy` files (half the RAM); new `flash.sh`: finds the ESP32 by USB ID with a menu, keeps MicroPython and the calibration unless asked, any ESP32 with Wi-Fi, esptool 4 and 5, Wi-Fi updates only after checking it's the plotter and the password is right; `"arms"` setting for the elbow default; Wi-Fi errors shown on the OLED in words (wrong password, not found ...), alternating idle screen, connection-lost notice; the board's LED blinks as a heartbeat (fast while plotting, triple flash in rescue mode); "parking" state after a plot; display not retried when there is none; updates and restarts refused during a plot; board names with the MAC's last byte (`plotter-60`) for the hostname and the own access point; Wi-Fi transmit power setting (13 dBm default on C3: fixes boards whose access point was invisible); up to 3 Wi-Fi scans and no giving up on a momentary "wrong password"; `flash.sh` asks MicroPython before using esptool, frees boards left stuck by esptool's reset, never erases on `--yes` alone and saves the board's settings before erasing, remembers installed boards |
| 17 | Optional precise calibration on the BrachioGraph template (section 8): measured points per joint replace Park/Slope, guided line by line with fine pulse buttons, optional approach from both sides that also measures the backlash for the hysteresis correction; *Draw 1 cm Grid* to check on the template; warning when the drawing area is beyond the servos' limits or reach; full Wi-Fi power on C3 boards with the OLED (13 dBm only without display); `flash.sh` asks before going on without `customization.json` (and no longer stops at the end without it), explains missing serial port permissions; SVG paths made of several pieces no longer get stray connecting lines; BrachioGraph template and example SVGs in `images/`; includes PR #3 (out-of-reach targets refused, one moving command at a time, classic ESP32 pins 25/26/27, new STLs) |
| 18 | Robot on landscape A4 (PR #4: wiring picture, photos, STLs, 3D-printed arms and the view from the paper side as defaults, a drawing area within the servo limits, `deploy_udev.sh` instead of `sudo`, 13 dBm on every C3); settings in two files with the defaults in exactly one place each: `board_default_settings.json` / `board_settings.json` (name, Wi-Fi, passwords, hardware by chip; yours sent by `flash.sh`, otherwise the board keeps its own) and `board_default_calibration.json` / `board_calibration.json` (only on the board, only differences saved; `--get-calibration` copies it); `"arms"` replaced by the S2 Slope default; boards and local files of earlier versions converted automatically; `flash.sh` short by default with `--verbose`, one question per run, never uploads the calibration and saves it around a reinstall; templates for the new layout: two A4 sheets on the robot's pins, front and back (turned round, angles behind the shoulder), with a printer correction, drawn by `images/template.py` |
