import network
import socket
import uasyncio as asyncio
import math
import time
from machine import Pin, PWM, I2C, SoftI2C
import ssd1306

REVISION = "REV 8.0"

# --- 1. Self-healing OLED Driver (72x40) ---
# The built-in framebuf font is fixed at 8x8 px, so 72x40 fits 9 chars x 5 rows.
# Errors are printed (never silently swallowed) and the display is re-initialised
# on the next update, which recovers it after a servo-induced brownout/I2C glitch.
OLED_W, OLED_H = 72, 40
OLED_SDA, OLED_SCL = 5, 6
has_oled = False
oled = None
oled_lines = ("", "", "")
oled_last_try = 0

def init_oled():
    global oled, has_oled, oled_last_try
    has_oled = False
    oled_last_try = time.ticks_ms()
    for name, bus in (("HW I2C", lambda: I2C(0, sda=Pin(OLED_SDA), scl=Pin(OLED_SCL), freq=100000)),
                      ("SoftI2C", lambda: SoftI2C(sda=Pin(OLED_SDA), scl=Pin(OLED_SCL), freq=100000))):
        try:
            i2c = bus()
            found = i2c.scan()
            print("OLED", name, "scan:", [hex(d) for d in found])
            addr = 0x3C if 0x3C in found else (0x3D if 0x3D in found else None)
            if addr is None:
                continue
            oled = ssd1306.SSD1306_I2C(OLED_W, OLED_H, i2c, addr=addr)
            has_oled = True
            print("OLED OK via", name, "at", hex(addr))
            return True
        except Exception as e:
            print("OLED", name, "init failed:", repr(e))
    print("OLED not found - check ssd1306.py on the board and SDA=%d/SCL=%d" % (OLED_SDA, OLED_SCL))
    return False

OLED_COLS = OLED_W // 8   # 9 characters per row with the 8x8 font
OLED_ROWS_Y = (4, 16, 28)
oled_scroll = 0
oled_beat = False

def draw_oled():
    # Lines longer than 9 chars scroll as a marquee (advanced by oled_task)
    global has_oled
    if not has_oled:
        # Retry at most every 5 s so a dead display can't slow down plotting
        if time.ticks_diff(time.ticks_ms(), oled_last_try) < 5000 or not init_oled():
            return
    try:
        oled.fill(0)
        for line, y in zip(oled_lines, OLED_ROWS_Y):
            line = str(line)
            if len(line) > OLED_COLS:
                loop = line + "   "
                i = oled_scroll % len(loop)
                line = (loop[i:] + loop[:i])
            oled.text(line[:OLED_COLS], 0, y)
        if oled_beat:
            oled.fill_rect(OLED_W - 2, OLED_H - 2, 2, 2, 1)  # heartbeat: script alive
        oled.show()
    except Exception as e:
        print("OLED draw error:", repr(e))
        has_oled = False  # force re-init next time

def update_oled(l1="", l2="", l3=""):
    global oled_lines, oled_scroll
    oled_lines = (l1, l2, l3)
    oled_scroll = 0
    print("OLED:", l1, "|", l2, "|", l3)
    draw_oled()

async def oled_task():
    # Scrolls long lines and blinks the corner dot. If the dot stops blinking,
    # the program has stopped (e.g. Thonny interrupted it) and the web server is gone.
    global oled_scroll, oled_beat
    tick = 0
    while True:
        await asyncio.sleep_ms(350)
        tick += 1
        scrolling = any(len(str(l)) > OLED_COLS for l in oled_lines)
        if scrolling:
            oled_scroll += 1
        if tick % 3 == 0:
            oled_beat = not oled_beat
        if scrolling or tick % 3 == 0:
            draw_oled()  # each redraw costs ~40 ms of I2C, so skip idle ones

init_oled()
update_oled(REVISION, "BOOTING")

# --- 2. Hardware Pin Assignments ---
# If GPIO 0 was damaged during the stall, change PIN_SHOULDER to 4
PIN_SHOULDER = 0
PIN_ELBOW    = 1
PIN_PEN      = 3

servo_shoulder = PWM(Pin(PIN_SHOULDER), freq=50)
servo_elbow    = PWM(Pin(PIN_ELBOW), freq=50)
servo_pen      = PWM(Pin(PIN_PEN), freq=50)

# --- 3. Calibration Configuration ---
config = {
    "L1": 80.0,
    "L2": 80.0,
    "s1_pw": 1500,
    "s1_ang": -90.0,
    "s1_slope": -10.0,
    "s2_pw": 1500,
    "s2_ang": 90.0,
    "s2_slope": 10.0,
    "pen_up": 1450,
    "pen_down": 1850
}

def set_raw_us(pw1, pw2, pw3):
    # Absolute safety limits (750us to 2250us) to prevent mechanical jamming
    pw1 = max(750, min(2250, pw1))
    pw2 = max(750, min(2250, pw2))
    pw3 = max(1000, min(2000, pw3))
    
    servo_shoulder.duty_ns(int(pw1 * 1000))
    servo_elbow.duty_ns(int(pw2 * 1000))
    servo_pen.duty_ns(int(pw3 * 1000))

PEN_SETTLE_MS = 150

# Last commanded position, so tests can restore it and the web UI can stay in sync
state = {"s": -45.0, "e": 90.0, "pen": False}

def set_hardware(sh_deg, el_deg, pen_down):
    state["s"], state["e"], state["pen"] = sh_deg, el_deg, pen_down
    pw_1 = config["s1_pw"] + (sh_deg - config["s1_ang"]) * config["s1_slope"]
    pw_2 = config["s2_pw"] + (el_deg - config["s2_ang"]) * config["s2_slope"]
    pw_3 = config["pen_down"] if pen_down else config["pen_up"]
    set_raw_us(pw_1, pw_2, pw_3)

def restore_hardware():
    set_hardware(state["s"], state["e"], state["pen"])

async def move_to(sh_deg, el_deg, pen_down):
    # Lift the pen before moving, and lower it only after arriving,
    # otherwise travel moves drag a line across the paper.
    if state["pen"] and not pen_down:
        set_hardware(state["s"], state["e"], False)
        await asyncio.sleep_ms(PEN_SETTLE_MS)
    set_hardware(sh_deg, el_deg, state["pen"] and pen_down)
    if pen_down and not state["pen"]:
        await asyncio.sleep_ms(PEN_SETTLE_MS)
        set_hardware(sh_deg, el_deg, True)

async def execute_gcode(line):
    parts = line.strip().split()
    if not parts: return
    cmd = parts[0]
    if cmd not in ("G0", "G1"): return
    x, y = None, None
    for p in parts[1:]:
        if p.startswith('X'): x = float(p[1:])
        elif p.startswith('Y'): y = float(p[1:])

    if x is not None and y is not None:
        L1, L2 = config["L1"], config["L2"]
        hypotenuse = math.sqrt(x**2 + y**2)
        if hypotenuse < 1.0:
            return  # unreachable (and would divide by zero)
        if hypotenuse > (L1 + L2):
            hypotenuse = (L1 + L2) * 0.999

        hyp_angle = math.asin(max(-1.0, min(1.0, x / hypotenuse)))
        cos_inner = (hypotenuse**2 + L1**2 - L2**2) / (2 * hypotenuse * L1)
        inner_ang = math.acos(max(-1.0, min(1.0, cos_inner)))
        
        cos_outer = (L1**2 + L2**2 - hypotenuse**2) / (2 * L1 * L2)
        outer_ang = math.acos(max(-1.0, min(1.0, cos_outer)))
        
        shoulder_deg = math.degrees(hyp_angle - inner_ang)
        elbow_deg = math.degrees(math.pi - outer_ang)
        await move_to(shoulder_deg, elbow_deg, (cmd == 'G1'))

# --- 4. Wi-Fi AP Setup ---
ap = network.WLAN(network.AP_IF)
ap.active(True)
ap.config(essid="ESP32-Plotter", password="plotterpassword")
while not ap.active():
    time.sleep_ms(100)
# AP mode defaults to 192.168.4.1 (ESP-IDF default); read it instead of assuming
AP_IP = ap.ifconfig()[0]
print("AP 'ESP32-Plotter' up, IP:", AP_IP)

# --- 5. Web UI ---
HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Plotter """ + REVISION + """</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: system-ui, sans-serif; margin: 12px; background: #eef1f5; }
    .card { background: white; padding: 16px; border-radius: 8px; max-width: 460px; margin: auto; box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 12px; }
    h3, h4 { margin: 4px 0 8px 0; }
    .badge { background: #0066cc; color: white; padding: 2px 7px; border-radius: 4px; font-size: 12px; }
    button { background: #0066cc; color: white; border: none; padding: 8px 12px; border-radius: 4px; cursor: pointer; font-size: 14px; margin: 3px 1px; }
    button:disabled { background: #aaa; }
    button.alt { background: #4a5568; }
    button.warn { background: #d97706; }
    button.test { background: #7c3aed; }
    button.success { background: #059669; }
    .btn-row { display: flex; flex-wrap: wrap; gap: 4px; margin: 6px 0; }
    canvas { border: 1px solid #ccc; display: block; margin: 10px auto; background: #fafafa; border-radius: 4px; }
    input[type=number] { width: 68px; padding: 4px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 13px; margin-bottom: 8px; }
    #status { font-weight: bold; text-align: center; margin: 8px 0; color: #0066cc; }
    .readout { background: #f8fafc; border: 1px solid #e2e8f0; padding: 6px; border-radius: 4px; font-family: monospace; font-size: 13px; text-align: center; margin-bottom: 8px; }
  </style>
</head>
<body>

<div class="card">
  <h3>BrachioGraph <span class="badge">""" + REVISION + """</span></h3>
  <div id="status">Status: Ready</div>
  <div class="readout" id="posReadout">S: -45° | E: 90° | Pen: UP</div>

  <h4>1. Motor Pin Diagnostic Tests</h4>
  <div class="btn-row">
    <button class="test" onclick="sendCmd('TEST_SHOULDER')">Test Shoulder Sweep</button>
    <button class="test" onclick="sendCmd('TEST_ELBOW')">Test Elbow Sweep</button>
    <button class="test" onclick="sendCmd('TEST_PEN')">Test Pen</button>
  </div>

  <h4>2. Safe Nudge Controls (±5°)</h4>
  <div class="btn-row">
    <button onclick="jog('S', -5)">S: -5°</button>
    <button onclick="jog('S', 5)">S: +5°</button>
    <button onclick="jog('E', -5)">E: -5°</button>
    <button onclick="jog('E', 5)">E: +5°</button>
    <button class="alt" onclick="togglePen()">Toggle Pen</button>
  </div>
  <div class="btn-row">
    <button class="warn" onclick="centerBed()">Bed Center (-45°, 90°)</button>
    <button class="alt" onclick="sendCmd('TEST_OLED')">Refresh OLED</button>
  </div>

  <h4>3. Direct Motion Test</h4>
  <button class="success" style="width:100%; padding:10px;" onclick="drawTestSquare()">
    Draw Test Square (30x30mm)
  </button>
</div>

<div class="card">
  <h4>4. File Plotter (SVG)</h4>
  <input type="file" id="fileInput" accept=".svg"><br>
  <canvas id="preview" width="360" height="250"></canvas>
  <button id="plotBtn" style="width:100%;" disabled onclick="startPlot()">Plot Loaded SVG</button>
</div>

<div class="card">
  <h4>5. Live Calibration</h4>
  <div class="grid">
    <div>S1 Park: <input type="number" id="s1_ang" value="-90.0" step="1"></div>
    <div>S2 Park: <input type="number" id="s2_ang" value="90.0" step="1"></div>
    <div>S1 Slope: <input type="number" id="s1_slope" value="-10.0" step="0.5"></div>
    <div>S2 Slope: <input type="number" id="s2_slope" value="10.0" step="0.5"></div>
    <div>Pen Up (us): <input type="number" id="pen_up" value="1450" step="10"></div>
    <div>Pen Down (us): <input type="number" id="pen_down" value="1850" step="10"></div>
  </div>
  <button class="alt" style="width:100%;" onclick="applyConfig()">Apply Config</button>
</div>

<script>
let currentS = -45, currentE = 90, penDown = false;
let motionQueue = [];
const status = document.getElementById('status');
const readout = document.getElementById('posReadout');
const canvas = document.getElementById('preview');
const ctx = canvas.getContext('2d');
const BED = { minX: -50, maxX: 50, minY: 80, maxY: 150 };

function updateReadout() {
  readout.innerText = `S: ${currentS}° | E: ${currentE}° | Pen: ${penDown ? "DOWN" : "UP"}`;
}

function toCanvasX(x) { return ((x - BED.minX) / (BED.maxX - BED.minX)) * canvas.width; }
function toCanvasY(y) { return canvas.height - (((y - BED.minY) / (BED.maxY - BED.minY)) * canvas.height); }

function resetCanvas() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = '#ddd';
  ctx.strokeRect(0, 0, canvas.width, canvas.height);
}
resetCanvas();

// Sends one command; syncs the readout with the position the ESP32 reports back.
async function postCmd(body) {
  const res = await fetch('/cmd', { method: 'POST', body: body });
  const txt = await res.text();
  if (!res.ok) throw new Error(txt);
  const m = txt.match(/S=(-?[\\d.]+) E=(-?[\\d.]+) P=(\\d)/);
  if (m) {
    currentS = Math.round(parseFloat(m[1])); currentE = Math.round(parseFloat(m[2]));
    penDown = m[3] === '1';
    updateReadout();
  }
  return txt;
}

async function sendCmd(text) {
  status.innerText = "Sent: " + text;
  try {
    return await postCmd(text);
  } catch (err) {
    status.innerText = "Error: " + err.message;
  }
}

async function centerBed() {
  currentS = -45; currentE = 90;
  updateReadout();
  await sendCmd('ANG S=-45 E=90 P=0');
}

async function jog(axis, delta) {
  if (axis === 'S') currentS += delta;
  if (axis === 'E') currentE += delta;
  updateReadout();
  await sendCmd(`ANG S=${currentS} E=${currentE} P=${penDown ? 1 : 0}`);
}

async function togglePen() {
  penDown = !penDown;
  updateReadout();
  await sendCmd(`ANG S=${currentS} E=${currentE} P=${penDown ? 1 : 0}`);
}

async function applyConfig() {
  const s1a = document.getElementById('s1_ang').value;
  const s2a = document.getElementById('s2_ang').value;
  const s1s = document.getElementById('s1_slope').value;
  const s2s = document.getElementById('s2_slope').value;
  const pu = document.getElementById('pen_up').value;
  const pd = document.getElementById('pen_down').value;
  await sendCmd(`CFG S1A=${s1a} S2A=${s2a} S1S=${s1s} S2S=${s2s} PU=${pu} PD=${pd}`);
  alert("Configuration Applied!");
}

async function runQueue(queue) {
  for (let i = 0; i < queue.length; i++) {
    status.innerText = `Drawing: ${i+1} / ${queue.length}`;
    try {
      await postCmd(queue[i] + '|' + (i+1) + '/' + queue.length);
    } catch (err) {
      status.innerText = `Stopped at ${i+1} / ${queue.length}: ${err.message}`;
      document.getElementById('plotBtn').disabled = motionQueue.length === 0;
      return;
    }
  }
  await sendCmd('DONE');
  status.innerText = "Execution Finished!";
  document.getElementById('plotBtn').disabled = motionQueue.length === 0;
}

function drawTestSquare() {
  const pts = [];
  const minX = -15, maxX = 15, minY = 100, maxY = 130;
  for (let x = minX; x <= maxX; x += 1) pts.push([x, minY]);
  for (let y = minY; y <= maxY; y += 1) pts.push([maxX, y]);
  for (let x = maxX; x >= minX; x -= 1) pts.push([x, maxY]);
  for (let y = maxY; y >= minY; y -= 1) pts.push([minX, y]);

  resetCanvas();
  ctx.beginPath();
  ctx.strokeStyle = '#059669';
  ctx.moveTo(toCanvasX(pts[0][0]), toCanvasY(pts[0][1]));

  const queue = [];
  queue.push(`G0 X${pts[0][0]} Y${pts[0][1]}`);
  pts.forEach(p => {
    queue.push(`G1 X${p[0]} Y${p[1]}`);
    ctx.lineTo(toCanvasX(p[0]), toCanvasY(p[1]));
  });
  ctx.stroke();
  queue.push(`G0 X${pts[0][0]} Y${pts[0][1]}`);

  runQueue(queue);
}

document.getElementById('fileInput').addEventListener('change', async (e) => {
  if (!e.target.files.length) return;
  const doc = new DOMParser().parseFromString(await e.target.files[0].text(), "image/svg+xml");
  const paths = doc.querySelectorAll("path");
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  const sampled = [];

  paths.forEach(p => {
    const len = p.getTotalLength();
    const pts = [];
    for (let d = 0; d <= len; d += 1.5) {
      const pt = p.getPointAtLength(d);
      pts.push(pt);
      if(pt.x < minX) minX = pt.x; if(pt.x > maxX) maxX = pt.x;
      if(pt.y < minY) minY = pt.y; if(pt.y > maxY) maxY = pt.y;
    }
    sampled.push(pts);
  });

  const scale = Math.min((BED.maxX - BED.minX) * 0.9 / (maxX - minX || 1), (BED.maxY - BED.minY) * 0.9 / (maxY - minY || 1));
  const cX = (minX + maxX)/2, cY = (minY + maxY)/2;
  const bcX = (BED.minX + BED.maxX)/2, bcY = (BED.minY + BED.maxY)/2;

  motionQueue = [];
  resetCanvas();
  ctx.beginPath();
  ctx.strokeStyle = '#0066cc';

  sampled.forEach(pts => {
    if (!pts.length) return;
    let sx = bcX + (pts[0].x - cX) * scale, sy = bcY - (pts[0].y - cY) * scale;
    motionQueue.push(`G0 X${sx.toFixed(2)} Y${sy.toFixed(2)}`);
    ctx.moveTo(toCanvasX(sx), toCanvasY(sy));
    for (let i = 0; i < pts.length; i++) {
      let px = bcX + (pts[i].x - cX) * scale, py = bcY - (pts[i].y - cY) * scale;
      motionQueue.push(`G1 X${px.toFixed(2)} Y${py.toFixed(2)}`);
      ctx.lineTo(toCanvasX(px), toCanvasY(py));
    }
    motionQueue.push(`G0 X${sx.toFixed(2)} Y${sy.toFixed(2)}`);
  });
  ctx.stroke();
  document.getElementById('plotBtn').disabled = false;
  status.innerText = `Ready: ${motionQueue.length} points`;
});

function startPlot() {
  document.getElementById('plotBtn').disabled = true;
  runQueue(motionQueue);
}
</script>
</body>
</html>
"""

# --- 6. Async Server Loop ---
async def handle_cmd(cmd_text, progress):
    if cmd_text == "TEST_SHOULDER":
        update_oled("TESTING", "SHOULDER")
        # Directly sweep shoulder pin between 1200us and 1800us
        for pw in [1200, 1500, 1800, 1500]:
            servo_shoulder.duty_ns(pw * 1000)
            await asyncio.sleep_ms(300)
        restore_hardware()  # go back where we were instead of staying at 1500us
        update_oled(REVISION, "READY")

    elif cmd_text == "TEST_ELBOW":
        update_oled("TESTING", "ELBOW")
        for pw in [1200, 1500, 1800, 1500]:
            servo_elbow.duty_ns(pw * 1000)
            await asyncio.sleep_ms(300)
        restore_hardware()
        update_oled(REVISION, "READY")

    elif cmd_text == "TEST_PEN":
        update_oled("TESTING", "PEN")
        servo_pen.duty_ns(int(config["pen_down"] * 1000))
        await asyncio.sleep_ms(400)
        servo_pen.duty_ns(int(config["pen_up"] * 1000))
        await asyncio.sleep_ms(400)
        restore_hardware()
        update_oled(REVISION, "READY")

    elif cmd_text == "TEST_OLED":
        init_oled()  # full re-init, bypasses the retry throttle
        update_oled(REVISION, "TEST OK!", "%dx%d" % (OLED_W, OLED_H))

    elif cmd_text.startswith("ANG"):
        s_val, e_val, p_val = state["s"], state["e"], int(state["pen"])
        for p in cmd_text.split()[1:]:
            k, v = p.split('=')
            if k == 'S': s_val = float(v)
            elif k == 'E': e_val = float(v)
            elif k == 'P': p_val = int(v)
        set_hardware(s_val, e_val, p_val == 1)
        update_oled(f"S:{int(s_val)}", f"E:{int(e_val)}", "PEN DOWN" if p_val else "PEN UP")

    elif cmd_text.startswith("CFG"):
        keys = {'S1A': "s1_ang", 'S2A': "s2_ang", 'S1S': "s1_slope", 'S2S': "s2_slope",
                'PU': "pen_up", 'PD': "pen_down"}
        for p in cmd_text.split()[1:]:
            k, v = p.split('=')
            if k in keys and v:
                config[keys[k]] = float(v)
        restore_hardware()  # apply new calibration to the current position
        update_oled("CONFIG", "SAVED")

    elif cmd_text == "DONE":
        await move_to(state["s"], state["e"], False)
        update_oled(REVISION, "DONE!")

    else:
        await execute_gcode(cmd_text)
        if progress:
            update_oled("DRAWING", progress)

async def handle_client(reader, writer):
    try:
        req = (await reader.readline()).decode('utf-8')
        if not req.startswith("POST /cmd"):
            print("HTTP:", req.strip())
        length = 0
        while True:
            h = await reader.readline()
            if h in (b'\r\n', b''): break
            if h.lower().startswith(b'content-length:'):
                length = int(h.split(b':', 1)[1].decode().strip())

        if req.startswith("GET / "):
            headers = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html\r\n"
                "Cache-Control: no-cache, no-store, must-revalidate\r\n"
                "Connection: close\r\n\r\n"
            )
            writer.write(headers.encode('utf-8'))
            writer.write(HTML_PAGE.encode('utf-8'))
        elif req.startswith("POST /cmd"):
            # read(256) may return a partial body; read exactly Content-Length bytes
            raw = (await reader.readexactly(length)).decode('utf-8').strip() if length else ""
            cmd_text, progress = raw.split("|", 1) if "|" in raw else (raw, "")
            try:
                await handle_cmd(cmd_text, progress)
                reply = "OK S=%.1f E=%.1f P=%d" % (state["s"], state["e"], state["pen"])
                writer.write(("HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + reply).encode())
            except Exception as e:
                print("CMD error:", cmd_text, repr(e))
                writer.write(("HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\nERR " + repr(e)).encode())
        else:
            writer.write(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
        await writer.drain()
    except Exception as e:
        print("HTTP error:", repr(e))
    finally:
        # Always close, otherwise a bad request leaks a socket on every error
        writer.close()
        await writer.wait_closed()

async def main():
    set_hardware(-45, 90, False)
    try:
        server = await asyncio.start_server(handle_client, "0.0.0.0", 80)
    except OSError as e:
        # Typically EADDRINUSE after a soft reboot: port 80 is still held
        print("Server start failed:", repr(e), "- press RESET / power-cycle")
        update_oled("NO SERVER", "PRESS RST", str(e))
        return
    print(REVISION, "Server Running on http://%s/" % AP_IP)
    update_oled(REVISION, "READY", AP_IP)
    await oled_task()

asyncio.run(main())
