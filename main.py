import network
import socket
import uasyncio as asyncio
import math
import time
import json
from machine import Pin, PWM, I2C, SoftI2C
import ssd1306

REVISION = "REV 10.0"

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

# Calibration survives reboots in config.json on the board's flash
CONFIG_FILE = "config.json"

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            saved = json.load(f)
        for k in config:
            if k in saved:
                config[k] = float(saved[k])
        print("Config loaded from", CONFIG_FILE, config)
    except OSError:
        print("No", CONFIG_FILE, "yet - using defaults")
    except Exception as e:
        print(CONFIG_FILE, "unreadable - using defaults:", repr(e))

def save_config():
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)
    print("Config saved to", CONFIG_FILE)

load_config()

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

# Big moves are split into steps of GLIDE_STEP_DEG every GLIDE_STEP_MS (~100 deg/s).
# 20 ms matches the 50 Hz servo frame, so faster updates would just be dropped.
GLIDE_STEP_DEG = 2.0
GLIDE_STEP_MS = 20

async def glide(sh_deg, el_deg):
    # Small moves (normal G1 drawing steps) go straight through
    s0, e0 = state["s"], state["e"]
    steps = int(max(abs(sh_deg - s0), abs(el_deg - e0)) / GLIDE_STEP_DEG)
    for i in range(1, steps):
        f = i / steps
        set_hardware(s0 + (sh_deg - s0) * f, e0 + (el_deg - e0) * f, state["pen"])
        await asyncio.sleep_ms(GLIDE_STEP_MS)
    set_hardware(sh_deg, el_deg, state["pen"])

async def move_to(sh_deg, el_deg, pen_down):
    # Lift the pen before moving, and lower it only after arriving,
    # otherwise travel moves drag a line across the paper.
    if state["pen"] and not pen_down:
        set_hardware(state["s"], state["e"], False)
        await asyncio.sleep_ms(PEN_SETTLE_MS)
    await glide(sh_deg, el_deg)
    if pen_down and not state["pen"]:
        await asyncio.sleep_ms(PEN_SETTLE_MS)
        set_hardware(sh_deg, el_deg, True)

def inverse_kinematics(x, y):
    # Pen position (mm) -> (shoulder_deg, elbow_deg), or None if unreachable
    L1, L2 = config["L1"], config["L2"]
    hypotenuse = math.sqrt(x**2 + y**2)
    if hypotenuse < 1.0:
        return None  # unreachable (and would divide by zero)
    if hypotenuse > (L1 + L2):
        hypotenuse = (L1 + L2) * 0.999

    hyp_angle = math.asin(max(-1.0, min(1.0, x / hypotenuse)))
    cos_inner = (hypotenuse**2 + L1**2 - L2**2) / (2 * hypotenuse * L1)
    inner_ang = math.acos(max(-1.0, min(1.0, cos_inner)))

    cos_outer = (L1**2 + L2**2 - hypotenuse**2) / (2 * L1 * L2)
    outer_ang = math.acos(max(-1.0, min(1.0, cos_outer)))

    return math.degrees(hyp_angle - inner_ang), math.degrees(math.pi - outer_ang)

# Long G1 lines are split into G1_STEP_MM pieces so they stay straight on paper
# (moving the joints directly between two far points draws an arc).
G1_STEP_MM = 1.0
G1_STEP_MS = 15
pos = {"x": None, "y": None}  # last G-code position in mm, None if unknown

async def execute_gcode(line):
    parts = line.strip().split()
    if not parts: return
    cmd = parts[0]
    if cmd not in ("G0", "G1"): return
    x, y = None, None
    for p in parts[1:]:
        if p.startswith('X'): x = float(p[1:])
        elif p.startswith('Y'): y = float(p[1:])
    if x is None or y is None:
        return

    pen = (cmd == 'G1')
    x0, y0 = pos["x"], pos["y"]
    steps = 0
    if pen and x0 is not None:
        steps = int(math.sqrt((x - x0)**2 + (y - y0)**2) / G1_STEP_MM)
    if steps > 1:
        if not state["pen"]:
            await move_to(state["s"], state["e"], True)  # pen down at the start point
        for i in range(1, steps):
            f = i / steps
            a = inverse_kinematics(x0 + (x - x0) * f, y0 + (y - y0) * f)
            if a:
                await move_to(a[0], a[1], True)
                await asyncio.sleep_ms(G1_STEP_MS)
    a = inverse_kinematics(x, y)
    if a:
        await move_to(a[0], a[1], pen)
    pos["x"], pos["y"] = x, y

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
  <div id="svgHolder" style="position:absolute; width:0; height:0; overflow:hidden; visibility:hidden;"></div>
  <button id="plotBtn" style="width:100%;" disabled onclick="startPlot()">Plot Loaded SVG</button>
</div>

<div class="card">
  <h4>5. Image to Plot (JPG/PNG, converted in the browser)</h4>
  <input type="file" id="imgInput" accept="image/*"><br>
  <div class="grid" style="margin-top:8px;">
    <div>Detail (px): <input type="number" id="imgRes" value="160" min="60" max="400" step="20"></div>
    <div>Hatch spacing: <input type="number" id="imgSpacing" value="4" min="2" max="12" step="1"></div>
    <div>Hatch layers: <input type="number" id="imgLayers" value="2" min="0" max="4" step="1"></div>
    <div>Darkness: <input type="number" id="imgDark" value="1.0" min="0.3" max="2" step="0.1"></div>
    <div><label><input type="checkbox" id="imgOutline" checked> Outlines</label></div>
    <div><label><input type="checkbox" id="imgInvert"> Invert</label></div>
  </div>
  <div class="btn-row">
    <button class="alt" onclick="convertImage()">Re-convert</button>
    <button class="alt" id="svgDownload" disabled onclick="downloadSvg()">Download SVG</button>
  </div>
  <div style="font-size:12px; color:#555;">Result appears in the preview above; use "Plot Loaded SVG" to draw it.</div>
</div>

<div class="card">
  <h4>6. Live Calibration</h4>
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

// Fill the calibration fields with what is saved on the board
fetch('/config').then(r => r.json()).then(cfg => {
  const map = { s1_ang: 's1_ang', s2_ang: 's2_ang', s1_slope: 's1_slope', s2_slope: 's2_slope',
                pen_up: 'pen_up', pen_down: 'pen_down' };
  for (const k in map) if (k in cfg) document.getElementById(map[k]).value = cfg[k];
}).catch(() => {});

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
  alert("Configuration applied and saved to the board");
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
  // Corners only: the ESP32 splits long G1 lines into 1 mm steps itself
  const minX = -15, maxX = 15, minY = 100, maxY = 130;
  const pts = [[minX, minY], [maxX, minY], [maxX, maxY], [minX, maxY], [minX, minY]];

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

  runQueue(queue);
}

// Every SVG shape the browser can measure (all are SVGGeometryElements)
const SHAPES = 'path,rect,circle,ellipse,line,polyline,polygon';
const SIMPLIFY_MM = 0.2;   // max deviation when dropping points after scaling to the bed

document.getElementById('fileInput').addEventListener('change', async (e) => {
  if (!e.target.files.length) return;
  const doc = new DOMParser().parseFromString(await e.target.files[0].text(), "image/svg+xml");
  if (doc.querySelector('parsererror')) { status.innerText = "Error: not a valid SVG"; return; }

  // Attach the SVG (invisibly) so the browser resolves transforms and shape geometry
  const holder = document.getElementById('svgHolder');
  holder.innerHTML = '';
  const svg = holder.appendChild(document.importNode(doc.documentElement, true));

  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  const sampled = [];
  const bb = svg.getBBox();
  const step = Math.max(Math.hypot(bb.width, bb.height) / 500, 1e-3);  // fine, size-independent

  svg.querySelectorAll(SHAPES).forEach(el => {
    if (el.closest('defs,clipPath,mask,symbol,marker,pattern')) return;  // not drawn directly
    const len = el.getTotalLength();
    if (!len) return;
    const m = el.getCTM();  // includes transform/scale/rotate of the element and its groups
    const n = Math.ceil(len / step);
    const pts = [];
    for (let i = 0; i <= n; i++) {
      let pt = el.getPointAtLength(len * i / n);
      if (m) pt = pt.matrixTransform(m);
      pts.push({ x: pt.x, y: pt.y });
      if (pt.x < minX) minX = pt.x; if (pt.x > maxX) maxX = pt.x;
      if (pt.y < minY) minY = pt.y; if (pt.y > maxY) maxY = pt.y;
    }
    sampled.push(pts);
  });
  holder.innerHTML = '';

  if (!sampled.length) { status.innerText = "No drawable shapes found in SVG"; return; }
  buildQueue(sampled, minX, maxX, minY, maxY);
});

// Ramer-Douglas-Peucker: drops points that deviate less than eps from the line
// through their neighbours, so straight runs collapse to their two endpoints.
function simplify(pts, eps) {
  if (pts.length < 3) return pts;
  const keep = new Uint8Array(pts.length);
  keep[0] = keep[pts.length - 1] = 1;
  const stack = [[0, pts.length - 1]];
  while (stack.length) {
    const [a, b] = stack.pop();
    const [ax, ay] = pts[a], [bx, by] = pts[b];
    const len = Math.hypot(bx - ax, by - ay);
    let maxD = 0, idx = -1;
    for (let i = a + 1; i < b; i++) {
      const [px, py] = pts[i];
      const d = len ? Math.abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / len : Math.hypot(px - ax, py - ay);
      if (d > maxD) { maxD = d; idx = i; }
    }
    if (maxD > eps) { keep[idx] = 1; stack.push([a, idx], [idx, b]); }
  }
  return pts.filter((_, i) => keep[i]);
}

// Fits polylines (any units, y pointing down as in SVG/images) onto the bed,
// fills motionQueue and draws the preview.
function buildQueue(sampled, minX, maxX, minY, maxY) {
  const scale = Math.min((BED.maxX - BED.minX) * 0.9 / (maxX - minX || 1), (BED.maxY - BED.minY) * 0.9 / (maxY - minY || 1));
  const cX = (minX + maxX)/2, cY = (minY + maxY)/2;
  const bcX = (BED.minX + BED.maxX)/2, bcY = (BED.minY + BED.maxY)/2;

  motionQueue = [];
  resetCanvas();
  ctx.beginPath();
  ctx.strokeStyle = '#0066cc';
  let lengthMm = 0;

  sampled.forEach(pts => {
    const mm = simplify(pts.map(p => [bcX + (p.x - cX) * scale, bcY - (p.y - cY) * scale]), SIMPLIFY_MM);
    let [lx, ly] = mm[0];
    motionQueue.push(`G0 X${lx.toFixed(2)} Y${ly.toFixed(2)}`);
    ctx.moveTo(toCanvasX(lx), toCanvasY(ly));
    mm.forEach(([px, py]) => {
      lengthMm += Math.hypot(px - lx, py - ly);
      motionQueue.push(`G1 X${px.toFixed(2)} Y${py.toFixed(2)}`);
      ctx.lineTo(toCanvasX(px), toCanvasY(py));
      lx = px; ly = py;
    });
  });
  ctx.stroke();
  document.getElementById('plotBtn').disabled = false;
  // Rough estimate: ~60 ms per request plus ~15 ms per mm of drawn line
  const minutes = Math.ceil((motionQueue.length * 0.06 + lengthMm * 0.015) / 60);
  status.innerText = `Ready: ${sampled.length} lines, ${motionQueue.length} points, ~${minutes} min`;
}

// ---------- Image -> line drawing (runs entirely in the browser) ----------
// Outlines: marching squares on a blurred copy. Shading: hatch lines over dark
// areas, one more direction per darker layer (like the original BrachioGraph).
let imgElement = null, lastLines = null, lastSize = null;

document.getElementById('imgInput').addEventListener('change', (e) => {
  if (!e.target.files.length) return;
  const img = new Image();
  img.onload = () => { imgElement = img; convertImage(); URL.revokeObjectURL(img.src); };
  img.onerror = () => { status.innerText = "Error: could not read image"; };
  img.src = URL.createObjectURL(e.target.files[0]);
});

function num(id) { return parseFloat(document.getElementById(id).value); }

function grayscale(img, W, H, invert) {
  const c = document.createElement('canvas');
  c.width = W; c.height = H;
  const cx = c.getContext('2d');
  cx.fillStyle = '#fff';  // transparent PNG areas count as white paper
  cx.fillRect(0, 0, W, H);
  cx.drawImage(img, 0, 0, W, H);
  const d = cx.getImageData(0, 0, W, H).data;
  const g = new Float32Array(W * H);
  let lo = 255, hi = 0;
  for (let i = 0; i < W * H; i++) {
    let v = 0.299 * d[i*4] + 0.587 * d[i*4+1] + 0.114 * d[i*4+2];
    if (invert) v = 255 - v;
    g[i] = v;
    if (v < lo) lo = v; if (v > hi) hi = v;
  }
  const k = 255 / ((hi - lo) || 1);  // stretch contrast to the full range
  for (let i = 0; i < g.length; i++) g[i] = (g[i] - lo) * k;
  return g;
}

function blur(g, W, H) {
  const out = new Float32Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let sum = 0, n = 0;
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      const xx = x + dx, yy = y + dy;
      if (xx >= 0 && yy >= 0 && xx < W && yy < H) { sum += g[yy * W + xx]; n++; }
    }
    out[y * W + x] = sum / n;
  }
  return out;
}

// Marching squares edge pairs per case (tl=8, tr=4, br=2, bl=1); edges 0=top 1=right 2=bottom 3=left
const MS_CASES = [[], [[3,2]], [[2,1]], [[3,1]], [[0,1]], [[0,1],[3,2]], [[0,2]], [[3,0]],
                  [[3,0]], [[0,2]], [[3,0],[2,1]], [[0,1]], [[3,1]], [[2,1]], [[3,2]], []];

function contours(g, W, H, thr, minPts) {
  const pts = new Map(), adj = new Map();
  const dark = (x, y) => g[y * W + x] < thr;
  // Each crossing point is identified by its grid edge: horizontal = even id, vertical = odd id
  function edgePoint(x, y, edge) {
    let id, ax, ay, bx, by;
    if (edge === 0) { ax = x; ay = y; bx = x + 1; by = y; }
    else if (edge === 2) { ax = x; ay = y + 1; bx = x + 1; by = y + 1; }
    else if (edge === 3) { ax = x; ay = y; bx = x; by = y + 1; }
    else { ax = x + 1; ay = y; bx = x + 1; by = y + 1; }
    id = (ay * W + ax) * 2 + (ay === by ? 0 : 1);
    if (!pts.has(id)) {
      const va = g[ay * W + ax], vb = g[by * W + bx];
      const t = va === vb ? 0.5 : Math.min(1, Math.max(0, (thr - va) / (vb - va)));
      pts.set(id, { x: ax + (bx - ax) * t, y: ay + (by - ay) * t });
    }
    return id;
  }
  function link(a, b) {
    if (!adj.has(a)) adj.set(a, []);
    if (!adj.has(b)) adj.set(b, []);
    adj.get(a).push(b); adj.get(b).push(a);
  }
  for (let y = 0; y < H - 1; y++) for (let x = 0; x < W - 1; x++) {
    const c = (dark(x, y) ? 8 : 0) | (dark(x + 1, y) ? 4 : 0) | (dark(x + 1, y + 1) ? 2 : 0) | (dark(x, y + 1) ? 1 : 0);
    for (const [e1, e2] of MS_CASES[c]) link(edgePoint(x, y, e1), edgePoint(x, y, e2));
  }
  // Every point has at most 2 neighbours, so the segments join into simple chains
  const seen = new Set(), lines = [];
  function walk(start) {
    const line = [];
    let prev = -1, cur = start;
    while (cur !== undefined && !seen.has(cur)) {
      seen.add(cur);
      line.push(pts.get(cur));
      const next = adj.get(cur).find(n => n !== prev && !seen.has(n));
      prev = cur; cur = next;
    }
    // Close loops back to their start point
    if (line.length > 2 && adj.get(start).includes(prev)) line.push(pts.get(start));
    return line;
  }
  for (const [id, nb] of adj) if (nb.length === 1 && !seen.has(id)) lines.push(walk(id));  // open chains
  for (const id of adj.keys()) if (!seen.has(id)) lines.push(walk(id));                    // closed loops
  return lines.filter(l => l.length >= minPts);
}

function hatch(g, W, H, thr, angleDeg, spacing) {
  const a = angleDeg * Math.PI / 180;
  const dx = Math.cos(a), dy = Math.sin(a);   // along the line
  const nx = -dy, ny = dx;                    // across the lines
  const cx = W / 2, cy = H / 2, R = Math.hypot(W, H) / 2;
  const lines = [];
  let flip = false;
  for (let o = -R; o <= R; o += spacing) {
    const runs = [];
    let run = null;
    for (let t = -R; t <= R; t += 0.5) {
      const x = cx + nx * o + dx * t, y = cy + ny * o + dy * t;
      const xi = Math.floor(x), yi = Math.floor(y);
      const isDark = xi >= 0 && yi >= 0 && xi < W && yi < H && g[yi * W + xi] < thr;
      if (isDark && !run) run = [{ x, y }, { x, y }];
      else if (isDark) run[1] = { x, y };
      else if (run) { runs.push(run); run = null; }
    }
    if (run) runs.push(run);
    const kept = runs.filter(r => Math.hypot(r[1].x - r[0].x, r[1].y - r[0].y) >= 2);
    // Alternate direction line by line to save pen-up travel
    if (flip) { kept.reverse(); kept.forEach(r => r.reverse()); }
    flip = !flip;
    lines.push(...kept);
  }
  return lines;
}

function convertImage() {
  if (!imgElement) { status.innerText = "Choose an image first"; return; }
  const W = Math.max(20, Math.round(num('imgRes')));
  const H = Math.max(2, Math.round(W * imgElement.naturalHeight / imgElement.naturalWidth));
  const dark = num('imgDark') || 1;
  const g = grayscale(imgElement, W, H, document.getElementById('imgInvert').checked);

  let lines = [];
  if (document.getElementById('imgOutline').checked)
    lines.push(...contours(blur(g, W, H), W, H, 128 * dark, 6));
  const layers = Math.max(0, Math.min(4, Math.round(num('imgLayers'))));
  const angles = [45, 135, 0, 90];
  for (let i = 0; i < layers; i++) {
    // Layer 0 covers mid-tones, each further layer only the darker areas
    const thr = 255 * dark * (layers - i) / (layers + 1);
    lines.push(...hatch(g, W, H, thr, angles[i], Math.max(1, num('imgSpacing'))));
  }
  if (!lines.length) { status.innerText = "Nothing to draw - try more darkness or layers"; return; }

  lastLines = lines; lastSize = [W, H];
  document.getElementById('svgDownload').disabled = false;
  buildQueue(lines, 0, W, 0, H);
}

function downloadSvg() {
  if (!lastLines) return;
  const [W, H] = lastSize;
  const body = lastLines.map(l => '<polyline points="' +
    l.map(p => p.x.toFixed(2) + ',' + p.y.toFixed(2)).join(' ') + '"/>').join('\\n');
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" ` +
    `fill="none" stroke="black" stroke-width="0.3">\\n${body}\\n</svg>\\n`;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
  a.download = 'plot.svg';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function startPlot() {
  document.getElementById('plotBtn').disabled = true;
  runQueue(motionQueue);
}
</script>
</body>
</html>
"""
HTML_BYTES = memoryview(HTML_PAGE.encode('utf-8'))  # encode once at boot, not per request
del HTML_PAGE

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
        await move_to(s_val, e_val, p_val == 1)
        pos["x"] = pos["y"] = None  # moved by angle, mm position no longer known
        update_oled(f"S:{int(s_val)}", f"E:{int(e_val)}", "PEN DOWN" if p_val else "PEN UP")

    elif cmd_text.startswith("CFG"):
        keys = {'S1A': "s1_ang", 'S2A': "s2_ang", 'S1S': "s1_slope", 'S2S': "s2_slope",
                'PU': "pen_up", 'PD': "pen_down"}
        for p in cmd_text.split()[1:]:
            k, v = p.split('=')
            if k in keys and v:
                config[keys[k]] = float(v)
        save_config()
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
            # Send in chunks: one big write needs the whole page buffered twice
            for i in range(0, len(HTML_BYTES), 2048):
                writer.write(HTML_BYTES[i:i + 2048])
                await writer.drain()
        elif req.startswith("GET /config "):
            # Lets the web UI show the saved calibration instead of hardcoded defaults
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
            writer.write(json.dumps(config).encode())
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
