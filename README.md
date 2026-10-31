# BrachioGraph on an ESP32-C3 OLED board (MicroPython)

A port of the [BrachioGraph](https://www.brachiograph.art/) pen plotter from the Raspberry Pi
to an **ESP32-C3 board with a built-in 0.42" 72x40 OLED**. The ESP32 runs its own Wi-Fi
access point and serves a web page; everything heavy (SVG parsing, image-to-line conversion,
path planning) happens in your browser, and the board only receives simple `G0`/`G1`
commands one at a time.

Current version: **`brachio-codez-oled-10.py`** (REV 10.0)

---

## Contents

1. [Hardware](#hardware)
2. [Installing](#installing)
3. [Running and connecting](#running-and-connecting)
4. [The web interface](#the-web-interface)
5. [Calibration](#calibration)
6. [Image to plot conversion](#image-to-plot-conversion)
7. [How it works](#how-it-works)
8. [HTTP API](#http-api)
9. [Tuning constants](#tuning-constants)
10. [Troubleshooting](#troubleshooting)
11. [Files and revision history](#files-and-revision-history)

---

## Hardware

| Part | Connection |
|---|---|
| Shoulder servo (inner arm) | GPIO **0** |
| Elbow servo (outer arm) | GPIO **1** |
| Pen-lift servo | GPIO **3** |
| OLED SSD1306 72x40 (on board) | I2C SDA = GPIO **5**, SCL = GPIO **6**, address 0x3C (0x3D also detected) |

- Arms: inner `L1` = 80 mm, outer `L2` = 80 mm (change in `config` if yours differ).
- Servos: standard hobby servos at 50 Hz. Pulse widths are clamped to 750–2250 µs for the
  arm and 1000–2000 µs for the pen so a bad calibration can't drive them into their end stops.
- **Power the servos from a separate 5 V supply** (common ground with the ESP32). Three servos
  starting together can draw over 1 A; powered from the board they cause brownouts, resets and
  OLED glitches.
- If GPIO 0 ever stops working, change `PIN_SHOULDER` to 4.

The arm geometry and drawing area follow the original BrachioGraph: the shoulder is at (0, 0),
the paper lies in front of it, and the default drawing area (`BED` in the web page) is
X −50…50 mm, Y 80…150 mm.

---

## Installing

### 1. Flash MicroPython (only once)

The firmware `ESP32_GENERIC_C3-20260824-v1.29.0.bin` is in this folder. With
[esptool](https://docs.espressif.com/projects/esptool/) (`pip install esptool`):

```sh
esptool.py --chip esp32c3 --port /dev/ttyACM0 erase_flash
esptool.py --chip esp32c3 --port /dev/ttyACM0 write_flash -z 0x0 ESP32_GENERIC_C3-20260824-v1.29.0.bin
```

(On the C3 the firmware goes to offset `0x0`. If the port isn't found, hold BOOT while
plugging in the board.) Thonny can do the same via *Tools → Options → Interpreter → Install or
update MicroPython*.

### 2. Copy the files to the board

Two files are needed on the board:

| On your computer | On the board |
|---|---|
| `ssd1306.py` | `ssd1306.py` |
| `brachio-codez-oled-10.py` | `main.py` (runs automatically at boot) |

With Thonny: open the file, *File → Save as… → MicroPython device*. Or with
[mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html):

```sh
mpremote cp ssd1306.py :ssd1306.py
mpremote cp brachio-codez-oled-10.py :main.py
```

> Use the `ssd1306.py` from this folder unchanged. A modified driver on the board once made
> the display fail silently; the code now prints the error instead, but the fix is to
> re-upload the original.

`config.json` (your calibration) is created on the board automatically the first time you
press *Apply Config*.

---

## Running and connecting

There are two ways to run it:

**A. From Thonny (while developing).** Open the script and press **Run (F5)**. All messages
(I2C scan, IP address, every page request) appear in Thonny's shell. Don't press Stop or
Ctrl-C while using the web page; that ends the server.

**B. Standalone.** Save it as `main.py`, close Thonny (or *Run → Disconnect*), then press
RESET or power-cycle the board.

> **Important:** when Thonny *connects* to the board it interrupts the running program to give
> you a `>>>` prompt. That stops the web server, but the OLED keeps showing its last text and
> the Wi-Fi access point stays up. That looks like "connected to Wi-Fi but the page won't
> load". The **blinking dot in the bottom-right corner of the OLED** shows the program is
> running; if it doesn't blink, the server is gone.

Then:

1. Connect your phone or laptop to the Wi-Fi **`ESP32-Plotter`**, password
   **`plotterpassword`** (change both in section 4 of the script).
2. Open **`http://192.168.4.1`**. Type the `http://`, as browsers otherwise try https.
   On phones, turn mobile data off so the phone doesn't avoid a Wi-Fi network without internet.

**About the IP address:** in access-point mode the ESP32 always gives itself `192.168.4.1`
(the ESP-IDF default) and hands out `192.168.4.x` addresses to clients. The script reads the
real address with `ap.ifconfig()` and shows it on the OLED (it scrolls, as it's longer than
the 9 characters that fit). To use a different address, add after `ap.active(True)`:

```python
ap.ifconfig(('192.168.10.1', '255.255.255.0', '192.168.10.1', '8.8.8.8'))
```

### The OLED

Three lines of 9 characters (the built-in MicroPython font is a fixed 8x8 px, so 72x40 px fits
9 characters by 5 rows). Longer lines scroll automatically. Typical screens:

| Display | Meaning |
|---|---|
| `REV 10.0` / `READY` / IP | Server running |
| `DRAWING` / `120/840` | Plotting, point 120 of 840 |
| `S:-45` / `E:90` / `PEN UP` | After a jog or angle command |
| `NO SERVER` / `PRESS RST` | Port 80 still in use after a soft reboot; press RESET |

If the display stops responding (e.g. after a servo brownout), the code re-initialises it
automatically (at most every 5 s), and the **Refresh OLED** button forces a full restart.

---

## The web interface

1. **Motor Pin Diagnostic Tests.** Sweep each servo (1200 → 1500 → 1800 → 1500 µs) to check
   wiring; the arm returns to where it was afterwards.
2. **Safe Nudge Controls.** Move shoulder/elbow by ±5°, toggle the pen, go to bed centre
   (−45°, 90°), refresh the OLED. The readout shows the position the ESP32 actually reports.
3. **Direct Motion Test.** Draws a 30x30 mm square: the quickest check of calibration
   (it should be square, with straight sides).
4. **File Plotter (SVG).** Load an SVG, check the preview, press *Plot Loaded SVG*.
   - Supported: `path`, `rect`, `circle`, `ellipse`, `line`, `polyline`, `polygon`, and any
     `transform` (scale/rotate/translate, also on groups). Shapes inside `<defs>`, masks etc.
     are skipped.
   - Not supported: text (convert to paths first, e.g. Inkscape *Path → Object to Path*),
     embedded images, fills (only outlines are drawn).
   - The drawing is scaled to fill 90 % of the drawing area, keeping its proportions.
   - The status line shows the number of lines/points and a rough time estimate.
5. **Image to Plot.** Converts a JPG/PNG into lines in the browser; see
   [below](#image-to-plot-conversion).
6. **Live Calibration.** Edit and apply calibration; it's saved to `config.json` on the board
   and loaded at every boot. See [Calibration](#calibration).

If a command fails during a plot (Wi-Fi drop, error on the board), the plot stops and the
status line says at which point.

---

## Calibration

Each arm servo's pulse width is a straight line through one known point:

```
pulse_us = park_pw + (angle_deg − park_angle) × slope
```

| Field | Config key | Default | Meaning |
|---|---|---|---|
| S1 Park | `s1_ang` | −90 | Shoulder angle (°) at which the shoulder servo gets `s1_pw` (1500 µs) |
| S1 Slope | `s1_slope` | −10 | µs per degree for the shoulder (negative = mounted reversed) |
| S2 Park | `s2_ang` | 90 | Elbow angle (°) at `s2_pw` (1500 µs) |
| S2 Slope | `s2_slope` | 10 | µs per degree for the elbow |
| Pen Up / Pen Down | `pen_up`, `pen_down` | 1450 / 1850 | Pen servo pulse widths (µs) |

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

`L1`, `L2`, `s1_pw` and `s2_pw` are also saved in `config.json` but are only editable in the
script or by editing the file. If you paste values from a Raspberry Pi BrachioGraph, the
mapping is `servo_1_parked_pw → s1_pw`, `servo_1_degree_ms → s1_slope`,
`servo_1_parked_angle → s1_ang` (same for servo 2).

To return to the defaults, delete `config.json` from the board (Thonny's file view) and reset.

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
   line are dropped). The preview shows the result; press *Plot Loaded SVG* to draw it.

| Setting | Effect |
|---|---|
| Detail (px) | Working resolution. Higher = finer outlines but many more points. 120–200 is a good range. |
| Hatch spacing | Distance between hatch lines in working pixels. Smaller = darker and slower. |
| Hatch layers | 0 = outlines only; 1–4 = more shading levels. |
| Darkness | Multiplies all thresholds: >1 draws more (lighter areas get lines), <1 draws less. |
| Outlines | Turn contour tracing on/off. |
| Invert | Swap dark and light (for white-on-black images). |

*Download SVG* saves the result as polylines, to keep or edit in Inkscape. Loading that SVG in
section 4 gives the same plot.

Tips: high-contrast images with simple backgrounds work best. Start with 1–2 layers and check
the time estimate; a full-page cross-hatch can mean thousands of points.

---

## How it works

```
Browser                                         ESP32 (MicroPython, uasyncio)
-------                                         -----------------------------
SVG / image → polylines in mm
           → G0/G1 queue  ── POST /cmd, one ──▶  inverse kinematics (x,y → angles)
                             command at a time   G1 lines split into 1 mm steps
           ◀── "OK S=.. E=.. P=.." ───────────   big moves glide in 2° steps
                                                 pen lifted before travel moves
                                                 angles → pulse widths → PWM
```

- **One command per HTTP request**: the browser only sends the next point after the board
  answers, so the board never needs to buffer a drawing (the C3 has little RAM).
- **Inverse kinematics**: for pen position (x, y), with `h = √(x² + y²)`:
  shoulder = `asin(x/h) − acos((h² + L1² − L2²) / (2·h·L1))`,
  elbow = `π − acos((L1² + L2² − h²) / (2·L1·L2))`. Points beyond reach are clamped
  to the arm's maximum length.
- **Straight lines**: moving both joints directly between two far-apart points draws an arc, so
  `G1` moves longer than 1 mm are split into 1 mm pieces on the board. This is also why the
  browser can send just the two ends of a straight hatch line.
- **Gliding**: any move bigger than 2° (travel, jogs) is split into 2° steps every 20 ms
  (≈100°/s) instead of jumping at full servo speed.
- **Pen handling**: on a travel move (`G0`) the pen is lifted and given 150 ms to rise before
  the arm moves; on a drawing move it is lowered only after arriving.

---

## HTTP API

All on port 80. Every response closes the connection.

| Request | Response |
|---|---|
| `GET /` | The web page |
| `GET /config` | Current calibration as JSON |
| `POST /cmd` body = command | `OK S=<shoulder> E=<elbow> P=<0/1>` or `400 ERR <message>` |

Commands (the request body; optionally followed by `|progress text` shown on the OLED, e.g.
`G1 X10 Y100|5/200`):

| Command | Action |
|---|---|
| `G0 X<mm> Y<mm>` | Travel with pen up |
| `G1 X<mm> Y<mm>` | Draw a straight line with pen down |
| `ANG S=<deg> E=<deg> P=<0/1>` | Move joints directly to these angles |
| `CFG S1A= S2A= S1S= S2S= PU= PD=` | Set calibration (any subset) and save to `config.json` |
| `TEST_SHOULDER`, `TEST_ELBOW`, `TEST_PEN` | Servo sweep tests |
| `TEST_OLED` | Re-initialise the display |
| `DONE` | Lift the pen and show DONE |

Example from a computer on the plotter's Wi-Fi:

```sh
curl -d 'G0 X-10 Y100' http://192.168.4.1/cmd
curl -d 'G1 X10 Y100'  http://192.168.4.1/cmd
curl -d 'DONE'         http://192.168.4.1/cmd
```

---

## Tuning constants

In the script:

| Constant | Default | Purpose |
|---|---|---|
| `PIN_SHOULDER`, `PIN_ELBOW`, `PIN_PEN` | 0, 1, 3 | Servo GPIOs |
| `OLED_SDA`, `OLED_SCL`, `OLED_W`, `OLED_H` | 5, 6, 72, 40 | Display |
| `PEN_SETTLE_MS` | 150 | Wait after pen up/down before moving |
| `GLIDE_STEP_DEG`, `GLIDE_STEP_MS` | 2.0, 20 | Speed of large moves (≈100°/s) |
| `G1_STEP_MM`, `G1_STEP_MS` | 1.0, 15 | Straight-line resolution and drawing speed (≈65 mm/s) |
| `config` | see [Calibration](#calibration) | Defaults when there is no `config.json` |

In the web page's script:

| Constant | Default | Purpose |
|---|---|---|
| `BED` | X −50…50, Y 80…150 | Drawing area in mm (drawings are fitted to 90 % of it) |
| `SIMPLIFY_MM` | 0.2 | Line simplification tolerance |

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Thonny's shell shows nothing | The program was started at boot before Thonny connected. Run it with F5 from Thonny instead. |
| Wi-Fi connects but the page doesn't load | Most often Thonny stopped the program (OLED dot not blinking): reset the board with Thonny disconnected. Otherwise use `http://` explicitly and turn off mobile data. |
| Page loads sometimes, connection unstable | These small C3 boards have weak antennas; try `ap.config(txpower=8.5)` after `ap.active(True)`. |
| OLED shows `NO SERVER` | Port 80 still held after a soft reboot: press RESET. |
| OLED blank | Check the shell: `OLED HW I2C scan: []` means no display found (wiring/power). Any other error: re-upload the original `ssd1306.py`. |
| Board resets or OLED glitches when servos move | Brownout: power the servos separately. |
| Servo buzzes at rest | It's pushing against something or the pulse is at its limit; check calibration and mechanical range. |
| Lines are curved instead of straight | Calibration slopes or park angles are off; recalibrate with the test square. |
| Pen drags between shapes | Increase `PEN_SETTLE_MS` or lower `pen_up` (more lift). |

---

## Files and revision history

| File | Description |
|---|---|
| `brachio-codez-oled-10.py` | **Current version** (upload as `main.py`) |
| `ssd1306.py` | OLED driver (MicroPython standard driver) |
| `ESP32_GENERIC_C3-20260824-v1.29.0.bin` | MicroPython firmware for the ESP32-C3 |
| `square-svg.svg` | 40x40 test square for the SVG plotter |
| `brachiograph-esp32-nfd/` | Git repository (first working port, = REV 3 era) |
| `Brachiograph on an ESP32 with Mycrophyton and Browser based upload.pdf` | Original notes on the approach |

Revisions (each in its own file, older ones kept for fallback):

Numbered by file (`brachio-codez-1.py`, `brachio-codez-oled-N.py`). Files 4–6 show an older
`REVISION` string (REV 3.0–5.0); from file 7 on, the two numbers match.

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
