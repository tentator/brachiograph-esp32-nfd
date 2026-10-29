import network
import socket
import uasyncio as asyncio
import math
from machine import Pin, PWM, I2C
import ssd1306

# --- OLED Setup ---
try:
    i2c = I2C(0, sda=Pin(5), scl=Pin(6), freq=400000)
    oled = ssd1306.SSD1306_I2C(72, 40, i2c)
    has_oled = True
except Exception:
    has_oled = False

def update_oled(l1, l2=""):
    if has_oled:
        oled.fill(0)
        oled.text(l1, 0, 8)
        oled.text(l2, 0, 24)
        oled.show()

update_oled("Starting...")

# --- Official BrachioGraph Geometry & Calibration ---
L1 = 80.0  # Inner arm length (mm)
L2 = 80.0  # Outer arm length (mm)

# Default BrachioGraph servo calibration (microseconds)
# If you tuned these on your Raspberry Pi, paste your values here:
SERVO_1_PARKED_PW    = 1500   # µs at -90 degrees
SERVO_1_PARKED_ANGLE = -90.0  # degrees
SERVO_1_DEGREE_PW    = -10.0  # µs per degree (NEGATIVE for shoulder)

SERVO_2_PARKED_PW    = 1500   # µs at +90 degrees
SERVO_2_PARKED_ANGLE = 90.0   # degrees
SERVO_2_DEGREE_PW    = 10.0   # µs per degree (POSITIVE for elbow)

PW_DOWN = 1850  # Pen Down pulse-width in µs (adjust if needed: e.g. 1100 to 1900)
PW_UP   = 1450  # Pen Up pulse-width in µs

# --- Hardware Setup (ESP32-C3 Pins) ---
servo_shoulder = PWM(Pin(0), freq=50)
servo_elbow    = PWM(Pin(1), freq=50)
servo_pen      = PWM(Pin(3), freq=50)

def set_hardware(sh_deg, el_deg, pen_down):
    # Official BrachioGraph angle-to-pulse-width translation
    pw_1 = SERVO_1_PARKED_PW + (sh_deg - SERVO_1_PARKED_ANGLE) * SERVO_1_DEGREE_PW
    pw_2 = SERVO_2_PARKED_PW + (el_deg - SERVO_2_PARKED_ANGLE) * SERVO_2_DEGREE_PW
    pw_3 = PW_DOWN if pen_down else PW_UP

    # Clamp safety limits (500µs to 2500µs)
    pw_1 = max(500, min(2500, pw_1))
    pw_2 = max(500, min(2500, pw_2))
    pw_3 = max(500, min(2500, pw_3))

    # Convert microseconds (µs) to nanoseconds (ns) for MicroPython duty_ns
    servo_shoulder.duty_ns(int(pw_1 * 1000))
    servo_elbow.duty_ns(int(pw_2 * 1000))
    servo_pen.duty_ns(int(pw_3 * 1000))

def execute_gcode(line):
    parts = line.strip().split()
    if not parts: return
    cmd = parts[0]
    x, y = None, None
    for p in parts[1:]:
        if p.startswith('X'): x = float(p[1:])
        elif p.startswith('Y'): y = float(p[1:])
        
    if x is not None and y is not None:
        # Exact BrachioGraph Trigonometry
        hypotenuse = math.sqrt(x**2 + y**2)
        if hypotenuse > (L1 + L2):
            hypotenuse = (L1 + L2) * 0.999
            
        hypotenuse_angle = math.asin(x / hypotenuse)
        
        cos_inner = (hypotenuse**2 + L1**2 - L2**2) / (2 * hypotenuse * L1)
        inner_angle = math.acos(max(-1.0, min(1.0, cos_inner)))
        
        cos_outer = (L1**2 + L2**2 - hypotenuse**2) / (2 * L1 * L2)
        outer_angle = math.acos(max(-1.0, min(1.0, cos_outer)))
        
        # Shoulder angle relative to straight ahead (Y-axis)
        shoulder_deg = math.degrees(hypotenuse_angle - inner_angle)
        # Elbow angle relative to inner arm
        elbow_deg = math.degrees(math.pi - outer_angle)
        
        set_hardware(shoulder_deg, elbow_deg, (cmd == 'G1'))

# --- Wi-Fi Setup ---
ap = network.WLAN(network.AP_IF)
ap.active(True)
ap.config(essid="ESP32-Plotter", password="plotterpassword")
update_oled("Ready!", "192.168.4.1")

# --- Web UI ---
HTML_PAGE = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>ESP32 Plotter</title><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { font-family: sans-serif; margin: 16px; background: #f0f2f5; }
  .card { background: white; padding: 20px; border-radius: 8px; max-width: 480px; margin: auto; box-shadow: 0 2px 6px rgba(0,0,0,0.1); }
  canvas { border: 1px solid #ccc; border-radius: 4px; display: block; margin: 12px auto; background: #fafafa; }
  button { background: #0066cc; color: white; border: none; padding: 10px 16px; border-radius: 4px; cursor: pointer; width: 100%; margin-top: 8px; }
  button:disabled { background: #999; }
  #status { margin-top: 12px; font-weight: bold; text-align: center; }
</style></head><body>
<div class="card">
  <h2>BrachioGraph ESP32</h2>
  <input type="file" id="fileInput" accept=".svg"><br>
  <canvas id="preview" width="360" height="270"></canvas>
  <button id="plotBtn" disabled onclick="startPlot()">Plot SVG</button>
  <div id="status">Select an SVG file</div>
</div>
<script>
// Reachable bed coordinates in mm (standard BrachioGraph drawing area)
const BED = { minX: -50, maxX: 50, minY: 80, maxY: 150 };
let motionCommands = [];
const ctx = document.getElementById('preview').getContext('2d');

function drawBed() {
  ctx.clearRect(0, 0, 360, 270);
  ctx.strokeStyle = '#ccc';
  ctx.strokeRect(0, 0, 360, 270);
}
drawBed();

document.getElementById('fileInput').addEventListener('change', async (e) => {
  if (!e.target.files.length) return;
  const doc = new DOMParser().parseFromString(await e.target.files[0].text(), "image/svg+xml");
  const paths = doc.querySelectorAll("path");
  
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  const sampled = [];

  paths.forEach(p => {
    const len = p.getTotalLength();
    const pts = [];
    for (let d = 0; d <= len; d += 1.5) { // 1.5mm interpolation steps
      const pt = p.getPointAtLength(d);
      pts.push(pt);
      if(pt.x < minX) minX = pt.x; if(pt.x > maxX) maxX = pt.x;
      if(pt.y < minY) minY = pt.y; if(pt.y > maxY) maxY = pt.y;
    }
    sampled.push(pts);
  });

  const svgW = maxX - minX || 1;
  const svgH = maxY - minY || 1;
  const bedW = BED.maxX - BED.minX;
  const bedH = BED.maxY - BED.minY;
  const scale = Math.min((bedW * 0.9) / svgW, (bedH * 0.9) / svgH);
  
  const cX = (minX + maxX) / 2;
  const cY = (minY + maxY) / 2;
  const bcX = (BED.minX + BED.maxX) / 2;
  const bcY = (BED.minY + BED.maxY) / 2;

  motionCommands = [];
  drawBed();
  ctx.beginPath();
  ctx.strokeStyle = '#0066cc';
  
  sampled.forEach(pts => {
    if (!pts.length) return;
    // Invert SVG Y so top of image stays top of page
    let sx = bcX + (pts[0].x - cX) * scale;
    let sy = bcY - (pts[0].y - cY) * scale;
    motionCommands.push(`G0 X${sx.toFixed(2)} Y${sy.toFixed(2)}`);
    
    ctx.moveTo((sx - BED.minX)/(BED.maxX - BED.minX)*360, 270 - (sy - BED.minY)/(BED.maxY - BED.minY)*270);

    for (let i = 0; i < pts.length; i++) {
      let px = bcX + (pts[i].x - cX) * scale;
      let py = bcY - (pts[i].y - cY) * scale;
      motionCommands.push(`G1 X${px.toFixed(2)} Y${py.toFixed(2)}`);
      ctx.lineTo((px - BED.minX)/(BED.maxX - BED.minX)*360, 270 - (py - BED.minY)/(BED.maxY - BED.minY)*270);
    }
    motionCommands.push(`G0 X${sx.toFixed(2)} Y${sy.toFixed(2)}`);
  });
  ctx.stroke();
  
  document.getElementById('status').innerText = `Ready: ${motionCommands.length} segments.`;
  document.getElementById('plotBtn').disabled = false;
});

async function startPlot() {
  document.getElementById('plotBtn').disabled = true;
  let tot = motionCommands.length;
  for (let i = 0; i < tot; i++) {
    document.getElementById('status').innerText = `Plotting: ${i + 1} / ${tot}`;
    await fetch('/cmd', { method: 'POST', body: motionCommands[i] + '|' + (i+1) + '/' + tot });
  }
  document.getElementById('status').innerText = "Plot Complete!";
  document.getElementById('plotBtn').disabled = false;
  await fetch('/cmd', { method: 'POST', body: 'DONE' });
}
</script></body></html>
"""

async def handle_client(reader, writer):
    req_line = await reader.readline()
    req = req_line.decode('utf-8')
    while await reader.readline() not in [b'\r\n', b'']: pass

    if "GET / " in req:
        writer.write(("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + HTML_PAGE).encode('utf-8'))
    elif "POST /cmd" in req:
        raw_cmd = (await reader.read(128)).decode('utf-8').strip()
        if raw_cmd == 'DONE':
            update_oled("Plotting", "Complete!")
        else:
            if "|" in raw_cmd:
                cmd_text, progress = raw_cmd.split("|", 1)
            else:
                cmd_text, progress = raw_cmd, ""
            execute_gcode(cmd_text)
            if progress:
                update_oled("Drawing...", progress)
                
        await asyncio.sleep_ms(30)
        writer.write(b"HTTP/1.1 200 OK\r\n\r\nOK")

    await writer.drain()
    writer.close()
    await writer.wait_closed()

async def main():
    # Start parked with pen up
    set_hardware(-45, 90, False)
    server = await asyncio.start_server(handle_client, "0.0.0.0", 80)
    while True: await asyncio.sleep(1)

asyncio.run(main())