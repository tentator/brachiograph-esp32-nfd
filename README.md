# BrachioGraph on an ESP32-C3 OLED board (MicroPython)

A port of the [BrachioGraph](https://www.brachiograph.art/) pen plotter from the Raspberry Pi
to an **ESP32-C3 board with a built-in 0.42" 72x40 OLED**. The ESP32 runs its own Wi-Fi
access point and serves a web page; everything heavy (SVG parsing, image-to-line conversion,
path planning) happens in your browser. The finished drawing is uploaded to the board as
simple `G0`/`G1` commands and the board plots it on its own, so the phone can sleep or
disconnect meanwhile.

Current version: **`brachio-codez-oled-14.py`** (REV 14.0) with its web page
**`brachio-web-14.html`**

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

- Arms: inner `L1` = 80 mm, outer `L2` = 80 mm by default; set your lengths in the web page
  (see [Arms and drawing area](#arms-and-drawing-area)).
- Servos: standard hobby servos at 50 Hz. Pulse widths are clamped to 750–2250 µs for the
  arm and 1000–2000 µs for the pen so a bad calibration can't drive them into their end stops.
- **Power the servos from a separate 5 V supply** (common ground with the ESP32). Three servos
  starting together can draw over 1 A; powered from the board they cause brownouts, resets and
  OLED glitches.
- If GPIO 0 ever stops working, change `PIN_SHOULDER` to 4.

The arm geometry and drawing area follow the original BrachioGraph: the shoulder is at (0, 0),
the paper lies in front of it, and the default drawing area is X −50…50 mm, Y 80…150 mm
(adjustable in the web page).

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

Three files are needed on the board:

| On your computer | On the board |
|---|---|
| `ssd1306.py` | `ssd1306.py` |
| `brachio-codez-oled-14.py` | `main.py` (runs automatically at boot) |
| `brachio-web-14.html` | `index.html` (the web page) |

The web page is a separate file because the ESP32-C3 has little RAM: MicroPython compiles the
whole script in memory before running it, and with the page embedded as a string (REV 11) the
script no longer fit, so nothing ran at all, not even the Wi-Fi. The page is now streamed from
flash in 1 KB pieces. **Always upload the script and the page of the same revision together.**

With Thonny: open the file, *File → Save as… → MicroPython device*. Or with
[mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html):

```sh
mpremote cp ssd1306.py :ssd1306.py
mpremote cp brachio-codez-oled-14.py :main.py
mpremote cp brachio-web-14.html :index.html
```

Two more files appear on the board by themselves:

- `config.json`: calibration settings, created the first time you press *Apply Config*.
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
| `REV 14.0` / `READY` / IP | Server running |
| `UPLOADING` / `840 pts` | Receiving a drawing |
| `PLOTTING` / `120/840` / `14%` | Plotting, line 120 of 840 (updated every 5 s) |
| `PAUSED` / `120/840` | Plot paused |
| `REV 14.0` / `DONE` (or `STOPPED`, `ERROR`) / `840/840` | Plot ended |
| `S:-45` / `E:90` / `PEN UP` | After a jog or angle command |
| `NO SERVER` / `PRESS RST` | Port 80 still in use after a soft reboot; press RESET |

If the display stops responding (e.g. after a servo brownout), the code re-initialises it
automatically (at most every 5 s), and the **Refresh OLED** button forces a full restart.

While a plot runs, the heartbeat dot and scrolling pause (each display update briefly holds up
the motion), and only the progress is updated every 5 s.

---

## The web interface

1. **Motor Pin Diagnostic Tests.** Sweep each servo (1200 → 1500 → 1800 → 1500 µs) to check
   wiring; the arm returns to where it was afterwards.
2. **Safe Nudge Controls.** Move shoulder/elbow by ±5°, toggle the pen, go to bed centre
   (−45°, 90°), refresh the OLED. The readout shows the position the ESP32 actually reports.
3. **Direct Motion Test.** Draws a 30x30 mm square in the middle of the drawing area: the
   quickest check of calibration (it should be square, with straight sides).
4. **File Plotter (SVG).** Load an SVG, check the preview, press *Plot Loaded SVG*.
   - Supported: `path`, `rect`, `circle`, `ellipse`, `line`, `polyline`, `polygon`, and any
     `transform` (scale/rotate/translate, also on groups). Shapes inside `<defs>`, masks etc.
     are skipped.
   - Not supported: text (convert to paths first, e.g. Inkscape *Path → Object to Path*),
     embedded images, fills (only outlines are drawn).
   - The drawing is scaled to fill 90 % of the drawing area, keeping its proportions.
   - **Rotate 90° when that makes the drawing bigger** (on by default): a drawing whose shape
     suits the area better when turned, typically a portrait image on the landscape drawing
     area, is rotated 90° clockwise (its top ends up on the right). The status line then says
     *rotated 90°* and shows the final size in mm. The drawing area itself doesn't change, so
     the paper stays where it is; turn the paper to view the result upright. Untick it to always
     plot upright.
   - The status line shows the number of lines/points and a rough time estimate.
5. **Image to Plot.** Converts a JPG/PNG into lines in the browser; see
   [below](#image-to-plot-conversion).
6. **Live Calibration.** Calibration, speed, hysteresis correction, arm lengths and drawing
   area; saved to `config.json` on the board and loaded at every boot. See
   [Calibration](#calibration).

### Plotting

Pressing *Plot Loaded SVG* (or *Draw Test Square*):

1. uploads the drawing to the board in chunks of about 3 KB (the status line shows
   *Uploading: n%*),
2. starts the plot. From then on **the board plots on its own**: you can lock the phone,
   switch apps, close the page or walk out of Wi-Fi range.
3. While the page is open it shows a progress bar with **Pause/Resume** and **Stop**.
   Pausing lifts the pen; resuming lowers it again and carries on from the same point. A
   stopped plot can't be resumed.
4. If you reopen the page during a plot (e.g. after the phone slept), it picks up the progress
   display again.

While a plot runs, the board refuses motion commands (jog, tests, other plots) with
*busy: a plot is in progress*.

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

| Field | Config key | Default | Meaning |
|---|---|---|---|
| S1 Park | `s1_ang` | −90 | Shoulder angle (°) at which the shoulder servo gets `s1_pw` (1500 µs) |
| S1 Slope | `s1_slope` | −10 | µs per degree for the shoulder (negative = mounted reversed) |
| S2 Park | `s2_ang` | 90 | Elbow angle (°) at `s2_pw` (1500 µs) |
| S2 Slope | `s2_slope` | 10 | µs per degree for the elbow |
| Pen Up / Pen Down | `pen_up`, `pen_down` | 1450 / 1850 | Pen servo pulse widths (µs) |
| Speed | `speed` | 25 | Drawing speed in mm/s |
| S1 / S2 Hysteresis | `s1_hyst`, `s2_hyst` | 0 | Backlash correction in µs (see below) |
| Inner / Outer arm | `L1`, `L2` | 80 / 80 | Arm lengths in mm, pivot to pivot (outer: elbow to pen tip) |
| Area X/Y min/max | `bed_minx` … `bed_maxy` | −50, 50, 80, 150 | Drawing area in mm |

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

`s1_pw` and `s2_pw` are also saved in `config.json` but are only editable in the script or by
editing the file. If you paste values from a Raspberry Pi BrachioGraph, the mapping is
`servo_1_parked_pw → s1_pw`, `servo_1_degree_ms → s1_slope`,
`servo_1_parked_angle → s1_ang`, `hysteresis_correction_1 → s1_hyst` (same for servo 2).

### Speed

Lower is steadier: try 15–25 mm/s for drawings with fine detail, up to 40–50 mm/s for large
simple shapes. Travel moves (pen up) aren't affected; they glide at about 100°/s.

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
               (progress, pause, stop)           G0: pen up, glide in 2° steps
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
- **Gliding**: travel moves and jogs are split into 2° steps, one per frame (≈100°/s), instead
  of jumping at full servo speed.
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
| `GET /config` | Current settings as JSON |
| `GET /status` | `{"rev": revision, "job": state, "line": n, "total": n, "s": deg, "e": deg, "p": 0/1}`; state is `idle`, `uploading`, `running`, `paused`, `done`, `stopped` or `error` |
| `POST /cmd` body = command | `OK S=<shoulder> E=<elbow> P=<0/1>` or `400 ERR <message>` |
| `POST /job` body = G-code lines | Appends to the job being uploaded (max 8 KB per request) |

Commands for `POST /cmd`:

| Command | Action |
|---|---|
| `G0 X<mm> Y<mm>` | Travel with pen up |
| `G1 X<mm> Y<mm>` | Draw a straight line with pen down |
| `ANG S=<deg> E=<deg> P=<0/1>` | Move joints directly to these angles |
| `CFG <key>=<value> ...` | Set any settings by their config key (e.g. `CFG speed=20 s1_hyst=8`) and save to `config.json` |
| `JOB_BEGIN <lines> <bytes>` | Start a new upload (checks free flash) |
| `JOB_START` | Start plotting the uploaded job |
| `JOB_PAUSE`, `JOB_RESUME`, `JOB_STOP` | Control the running plot |
| `TEST_SHOULDER`, `TEST_ELBOW`, `TEST_PEN` | Servo sweep tests |
| `TEST_OLED` | Re-initialise the display |
| `DONE` | Lift the pen and show DONE |

While a job runs, `G0`, `G1`, `ANG`, the tests, `DONE`, `JOB_BEGIN` and `JOB_START` are refused.
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
| `PIN_SHOULDER`, `PIN_ELBOW`, `PIN_PEN` | 0, 1, 3 | Servo GPIOs |
| `OLED_SDA`, `OLED_SCL`, `OLED_W`, `OLED_H` | 5, 6, 72, 40 | Display |
| `PEN_SETTLE_MS` | 150 | Wait after pen up/down before moving |
| `FRAME_MS` | 20 | Servo frame period (50 Hz); one motion step per frame |
| `GLIDE_STEP_DEG` | 2.0 | Joint step per frame for travel and jogs (≈100°/s) |
| `MAX_CHUNK` | 8192 | Largest accepted upload chunk (bytes) |
| `config` | see [Calibration](#calibration) | Defaults when there is no `config.json` (drawing speed is `speed` there) |

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
| No Wi-Fi network at all, OLED blank or unchanged after boot | The script didn't start. Run it with F5 in Thonny to see the error. `MemoryError` means the script is too big for the RAM (as REV 11 was); otherwise check that `ssd1306.py` is on the board. |
| Page shows "index.html is missing on the board" | Upload `brachio-web-14.html` to the board as `index.html`. |
| Page looks outdated or buttons don't work after an update | Script and page are from different revisions; upload both from the same revision and reload the page. |
| Wi-Fi connects but the page doesn't load | Most often Thonny stopped the program (OLED dot not blinking): reset the board with Thonny disconnected. Otherwise use `http://` explicitly and turn off mobile data. |
| Page loads sometimes, connection unstable | These small C3 boards have weak antennas; try `ap.config(txpower=8.5)` after `ap.active(True)`. |
| OLED shows `NO SERVER` | Port 80 still held after a soft reboot: press RESET. |
| OLED blank | Check the shell: `OLED HW I2C scan: []` means no display found (wiring/power). Any other error: re-upload the original `ssd1306.py`. |
| Board resets or OLED glitches when servos move | Brownout: power the servos separately. |
| Servo buzzes at rest | It's pushing against something or the pulse is at its limit; check calibration and mechanical range. |
| Lines are curved instead of straight | Calibration slopes or park angles are off; recalibrate with the test square. |
| Lines are shaky or wobbly | Lower the speed; power the servos separately with a 470–1000 µF capacitor across their supply; check the arms and pen holder for play; metal-gear servos help a lot. |
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
| `brachio-codez-oled-14.py` | **Current version** (upload as `main.py`) |
| `brachio-web-14.html` | Its web page (upload as `index.html`) |
| `ssd1306.py` | OLED driver (MicroPython standard driver) |
| `ESP32_GENERIC_C3-20260824-v1.29.0.bin` | MicroPython firmware for the ESP32-C3 |
| `square-svg.svg` | 40x40 test square for the SVG plotter |
| `brachiograph-esp32-nfd/` | Git repository (first working port, = REV 3 era) |

Revisions (each in its own file, older ones kept for fallback):

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
