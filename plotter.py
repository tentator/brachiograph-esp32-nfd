import network
import socket
import uasyncio as asyncio
import math
import time
import json
import os
from machine import Pin, PWM, I2C, SoftI2C
from rescue import settings, setting, read_json, event, connect, wifi_watch, wifi_state, recv, read_request, \
    handle_update, servo_pins, Led, chip, NAME, CAL_DEFAULTS_FILE, CAL_FILE

REVISION = "REV 18.0"

# --- 1. Self-healing OLED Driver (72x40) ---
# The built-in framebuf font is fixed at 8x8 px, so 72x40 fits 9 chars x 5 rows.
# Errors are printed (never silently swallowed) and the display is re-initialised
# on the next update, which recovers it after a servo-induced brownout/I2C glitch.
OLED_W, OLED_H = 72, 40
# "oled" in the settings (on by default on ESP32-C3 boards only); false for a C3 board without
# a display: then nothing touches the display pins and ssd1306.py isn't loaded.
USE_OLED = setting("oled")
# The C3 board's OLED is on GPIO 5/6; on a classic ESP32 GPIO 6 is the flash clock
OLED_SDA, OLED_SCL = (5, 6) if USE_OLED else (None, None)
has_oled = False
oled = None
oled_lines = ("", "", "")
oled_last_try = 0
oled_seen = False  # True once the display has worked: only then retry it after a glitch
ready_pages = []   # the idle screen's page(s), see ready_screen()

def init_oled():
    global oled, has_oled, oled_last_try, oled_seen
    has_oled = False
    oled_last_try = time.ticks_ms()
    if OLED_SDA is None:
        return False
    import ssd1306
    # 400 kHz keeps a full redraw at ~10 ms, shorter than one 20 ms servo frame
    for name, bus in (("HW I2C", lambda: I2C(0, sda=Pin(OLED_SDA), scl=Pin(OLED_SCL), freq=400000)),
                      ("SoftI2C", lambda: SoftI2C(sda=Pin(OLED_SDA), scl=Pin(OLED_SCL), freq=100000))):
        try:
            i2c = bus()
            found = i2c.scan()
            print("OLED", name, "scan:", [hex(d) for d in found])
            addr = 0x3C if 0x3C in found else (0x3D if 0x3D in found else None)
            if addr is None:
                continue
            oled = ssd1306.SSD1306_I2C(OLED_W, OLED_H, i2c, addr=addr)
            has_oled = oled_seen = True
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
        # Retry only a display that worked before (lost in a brownout), at most every 5 s so
        # it can't slow down plotting; a board without one is scanned once at boot and left alone
        if not oled_seen or time.ticks_diff(time.ticks_ms(), oled_last_try) < 5000 or not init_oled():
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
    # Scrolls long lines and blinks the corner dot and the board's LED together. If they stop
    # blinking, the program has stopped (e.g. Thonny interrupted it) and the web server is gone.
    # While plotting the dot pauses (no extra redraws) and the LED blinks faster instead.
    global oled_scroll, oled_beat
    led = Led()
    tick = 0
    while True:
        await asyncio.sleep_ms(350)
        tick += 1
        if job["state"] in ("running", "parking"):
            led.set(tick % 2 == 0)
            continue  # no extra redraws while plotting; run_job updates the progress
        if len(ready_pages) > 1 and oled_lines in ready_pages and tick % 17 == 0:
            # About every 6 s, the idle screen's other page (e.g. Wi-Fi problem / how to connect)
            update_oled(*ready_pages[(ready_pages.index(oled_lines) + 1) % len(ready_pages)])
        scrolling = any(len(str(l)) > OLED_COLS for l in oled_lines)
        if scrolling:
            oled_scroll += 1
        if tick % 3 == 0:
            oled_beat = not oled_beat
            led.set(oled_beat)
        if scrolling or tick % 3 == 0:
            draw_oled()  # each redraw costs ~40 ms of I2C, so skip idle ones

init_oled()
update_oled(REVISION, "BOOTING")

# --- 2. Hardware Pin Assignments ---
if not servo_pins():
    raise OSError("no servo pins: board_default_settings.json missing or broken, run flash.sh")  # rescue mode
PIN_SHOULDER, PIN_ELBOW, PIN_PEN = servo_pins()

# --- 3. Calibration Configuration ---
# board_default_calibration.json (from git, uploaded with every update) holds the defaults, the
# only place they are kept; board_calibration.json on the board (written by Apply Config and the
# precise calibration, never uploaded by flash.sh) holds what differs from them.
# L1, L2: arm lengths (mm); s1/s2: shoulder/elbow, park pulse, park angle, slope (us per deg,
# s2_slope negative with the elbow servo on the upper arm as on the printed arms), hysteresis
# correction (us, see set_hardware); pen_up/pen_down: pulses; speed: mm/s; bed_*: drawing area
# (mm), used by the web page; flip 1: drawings turned 180 deg for someone on the paper side, 0:
# from behind; s1_table/s2_table: precise calibration, [angle, pulse us] pairs replacing Park + Slope.
cal_defaults = read_json(CAL_DEFAULTS_FILE)
if not cal_defaults:
    raise OSError(CAL_DEFAULTS_FILE + " missing or broken: run flash.sh")  # rescue mode, error in /log
config = dict(cal_defaults)

def check_table(table):
    # [[angle, pulse], ...] -> sorted list of float pairs, or ValueError
    t = sorted([float(a), float(p)] for a, p in table)
    if any(not 500 <= p <= 2500 for a, p in t):
        raise ValueError("pulse outside 500-2500 us")
    if any(t[i][0] == t[i + 1][0] for i in range(len(t) - 1)):
        raise ValueError("the same angle twice")
    steps = [t[i + 1][1] - t[i][1] for i in range(len(t) - 1)]
    if not (all(d > 0 for d in steps) or all(d < 0 for d in steps)):
        raise ValueError("pulses must keep rising (or keep falling) with the angle")
    return t

def load_config():
    # The defaults, then what this board's calibration changes (checked: a broken value is
    # ignored, not fatal)
    saved = read_json(CAL_FILE) or {}
    for k, v in saved.items():
        if k not in config:
            continue  # a setting from an older version
        try:
            config[k] = check_table(v) if isinstance(config[k], list) else float(v)
        except (ValueError, TypeError) as e:
            event("%s: %s ignored: %r" % (CAL_FILE, k, e))
    print("Calibration:", config)

def save_config():
    # Only what differs from the defaults, so later default changes still reach untouched values
    mine = {k: v for k, v in config.items() if v != cal_defaults.get(k)}
    with open(CAL_FILE, "w") as f:
        json.dump(mine, f)
    print("Calibration saved to", CAL_FILE, mine)

load_config()

# The servos start at the park pulses (the park pose) at once: PWM's default duty is
# far outside the servo range and swung the arm off the paper until the park pose was set.
# (PWM(pin, freq=, duty_ns=) raises "PWM is inactive" on MicroPython 1.29.)
def servo(pin, us):
    pwm = PWM(Pin(pin), freq=50)
    pwm.duty_ns(int(us * 1000))
    return pwm

# One after another: each servo draws its biggest current while it moves into the park
# pose, and all three at once can brown out a board that powers them from its own 5V pin.
SERVO_START_GAP_MS = 300
servo_shoulder = servo(PIN_SHOULDER, config["s1_pw"])
time.sleep_ms(SERVO_START_GAP_MS)
servo_elbow = servo(PIN_ELBOW, config["s2_pw"])
time.sleep_ms(SERVO_START_GAP_MS)
servo_pen = servo(PIN_PEN, config["pen_up"])

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
# Park: power on, end of every plot, and the pose to mount the arms in. At the
# calibration's park angles both arm servos get their park pulse (s1_pw, s2_pw),
# so the arm parks where it was mounted however the calibration changes.
def park():
    return config["s1_ang"], config["s2_ang"]

state = {"s": config["s1_ang"], "e": config["s2_ang"], "pen": False}

# Hysteresis correction (as in the original BrachioGraph): servo gears have some
# slack, so a joint stops short depending on the direction it came from. Adding
# half the correction while the pulse rises (and subtracting it while it falls)
# makes both directions land on the same spot.
hyst = [[None, 0], [None, 0]]  # per joint: [last uncorrected pulse, direction]

def hysteresis(joint, pw, correction):
    h = hyst[joint]
    if h[0] is not None:
        if pw > h[0]: h[1] = 1
        elif pw < h[0]: h[1] = -1
    h[0] = pw
    return pw + h[1] * correction / 2

def pulse(joint, deg):
    # Joint angle -> pulse (us): through the measured points of the precise calibration if
    # there are two or more (straight between neighbours, the end segments continued beyond
    # them), else the straight line given by Park and Slope
    table = config["s1_table" if joint == 0 else "s2_table"]
    if len(table) >= 2:
        i = 1
        while i < len(table) - 1 and deg > table[i][0]:
            i += 1
        (a0, p0), (a1, p1) = table[i - 1], table[i]
        return p0 + (deg - a0) * (p1 - p0) / (a1 - a0)
    if joint == 0:
        return config["s1_pw"] + (deg - config["s1_ang"]) * config["s1_slope"]
    return config["s2_pw"] + (deg - config["s2_ang"]) * config["s2_slope"]

def angle_of(joint, pw):
    # Pulse -> joint angle, the inverse of pulse() (bisection; pulse() is monotonic)
    lo, hi = -270.0, 270.0
    rising = pulse(joint, hi) > pulse(joint, lo)
    for _ in range(40):
        mid = (lo + hi) / 2
        if (pulse(joint, mid) < pw) == rising:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

# Raw pulses for the precise calibration (PW command); None when the servos follow angles
raw = {"s": None, "e": None}

def set_hardware(sh_deg, el_deg, pen_down):
    state["s"], state["e"], state["pen"] = sh_deg, el_deg, pen_down
    raw["s"] = raw["e"] = None
    pw_1 = pulse(0, sh_deg)
    pw_2 = pulse(1, el_deg)
    pw_3 = config["pen_down"] if pen_down else config["pen_up"]
    set_raw_us(hysteresis(0, pw_1, config["s1_hyst"]),
               hysteresis(1, pw_2, config["s2_hyst"]), pw_3)

def restore_hardware():
    set_hardware(state["s"], state["e"], state["pen"])

set_hardware(*park(), False)  # before joining Wi-Fi, which takes seconds

# Servos take a new position every 20 ms (50 Hz). Every motion step waits for the
# next frame slot, so each step is actually seen by the servo and the speed is even.
FRAME_MS = 20
_next_frame = time.ticks_ms()

async def next_frame():
    global _next_frame
    now = time.ticks_ms()
    if time.ticks_diff(_next_frame, now) < 0:
        _next_frame = now  # idle or running late: restart the clock instead of rushing to catch up
    _next_frame = time.ticks_add(_next_frame, FRAME_MS)
    await asyncio.sleep_ms(time.ticks_diff(_next_frame, now))

# Travel moves and jogs glide at GLIDE_STEP_DEG per frame on average (~50 deg/s),
# ramped up from standstill and back down. Abrupt full-speed starts and stops made
# 80 mm 3D-printed arms overshoot the target and swing back.
GLIDE_STEP_DEG = 1.0

async def glide(sh_deg, el_deg):
    s0, e0 = state["s"], state["e"]
    steps = max(1, math.ceil(max(abs(sh_deg - s0), abs(el_deg - e0)) / GLIDE_STEP_DEG))
    for i in range(1, steps + 1):
        f = i / steps
        f = f * f * (3 - 2 * f)  # smoothstep: zero speed at both ends, peak 1.5x average
        set_hardware(s0 + (sh_deg - s0) * f, e0 + (el_deg - e0) * f, state["pen"])
        await next_frame()

async def move_to(sh_deg, el_deg, pen_down):
    # Lift the pen before moving, and lower it only after arriving,
    # otherwise travel moves drag a line across the paper.
    if state["pen"] and not pen_down:
        set_hardware(state["s"], state["e"], False)
        await asyncio.sleep_ms(PEN_SETTLE_MS)
    await glide(sh_deg, el_deg)
    if pen_down and not state["pen"]:
        set_hardware(sh_deg, el_deg, True)
        await asyncio.sleep_ms(PEN_SETTLE_MS)

def inverse_kinematics(x, y):
    # Pen position (mm) -> (shoulder_deg, elbow_deg), or None if unreachable
    L1, L2 = config["L1"], config["L2"]
    hypotenuse = math.sqrt(x**2 + y**2)
    if hypotenuse < 1.0 or hypotenuse > L1 + L2:
        return None  # unreachable (and would divide by zero)

    hyp_angle = math.asin(max(-1.0, min(1.0, x / hypotenuse)))
    cos_inner = (hypotenuse**2 + L1**2 - L2**2) / (2 * hypotenuse * L1)
    inner_ang = math.acos(max(-1.0, min(1.0, cos_inner)))

    cos_outer = (L1**2 + L2**2 - hypotenuse**2) / (2 * L1 * L2)
    outer_ang = math.acos(max(-1.0, min(1.0, cos_outer)))

    return math.degrees(hyp_angle - inner_ang), math.degrees(math.pi - outer_ang)

pos = {"x": None, "y": None}  # last G-code position in mm, None if unknown

async def line_to(x, y, pen=True):
    # Straight line at config["speed"]: one small step per servo frame.
    # (Moving the joints directly between two far points would draw an arc.)
    x0, y0 = pos["x"], pos["y"]
    if pen and not state["pen"]:
        set_hardware(state["s"], state["e"], True)  # pen down at the start point
        await asyncio.sleep_ms(PEN_SETTLE_MS)
    step = max(0.05, config["speed"] * FRAME_MS / 1000)
    steps = max(1, math.ceil(math.sqrt((x - x0)**2 + (y - y0)**2) / step))
    for i in range(1, steps + 1):
        if pen and job["stop"] and job["state"] in JOB_ACTIVE:
            x, y = x0 + (x - x0) * (i - 1) / steps, y0 + (y - y0) * (i - 1) / steps
            break  # Stop: don't finish the line, the plot ends here
        f = i / steps
        a = inverse_kinematics(x0 + (x - x0) * f, y0 + (y - y0) * f)
        if a:
            set_hardware(a[0], a[1], pen)
            await next_frame()
    pos["x"], pos["y"] = x, y

def forward_kinematics(sh_deg, el_deg):
    # (shoulder_deg, elbow_deg) -> pen position (mm), the inverse of inverse_kinematics
    s, se = math.radians(sh_deg), math.radians(sh_deg + el_deg)
    return (config["L1"] * math.sin(s) + config["L2"] * math.sin(se),
            config["L1"] * math.cos(s) + config["L2"] * math.cos(se))

async def go_park():
    # Pen up, then a straight line to park at drawing speed instead of a fast joint glide
    await move_to(state["s"], state["e"], False)
    if pos["x"] is not None:
        await line_to(*forward_kinematics(*park()), pen=False)
    await move_to(*park(), False)
    pos["x"] = pos["y"] = None

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
    a = inverse_kinematics(x, y)
    if not a:
        raise ValueError("out of reach: X%g Y%g" % (x, y))

    if cmd == 'G1' and pos["x"] is not None:
        await line_to(x, y)  # sets pos, also where a Stop cut the line short
    else:
        await move_to(a[0], a[1], cmd == 'G1')
        pos["x"], pos["y"] = x, y

# --- Plot jobs: the whole drawing is uploaded to flash, then plotted by the board ---
# The browser can sleep or disconnect meanwhile, and there are no per-point
# network pauses (which made the arm stop and start at every point).
JOB_FILE = "job.gcode"
job = {"state": "idle", "line": 0, "total": 0, "pause": False, "stop": False, "task": None}
JOB_ACTIVE = ("running", "paused", "parking")

async def run_job():
    job["state"], job["line"] = "running", 0
    job["pause"] = job["stop"] = False
    pos["x"] = pos["y"] = None
    last_oled = time.ticks_ms()
    update_oled("PLOTTING", "0/%d" % job["total"], "0%")
    try:
        with open(JOB_FILE) as f:
            while not job["stop"]:
                if job["pause"]:
                    was_down = state["pen"]
                    await move_to(state["s"], state["e"], False)
                    job["state"] = "paused"
                    update_oled("PAUSED", "%d/%d" % (job["line"], job["total"]))
                    while job["pause"] and not job["stop"]:
                        await asyncio.sleep_ms(100)
                    if job["stop"]:
                        break
                    job["state"] = "running"
                    if was_down:
                        await move_to(state["s"], state["e"], True)
                line = f.readline()
                if not line:
                    break
                await execute_gcode(line)
                job["line"] += 1
                if time.ticks_diff(time.ticks_ms(), last_oled) > 5000:
                    last_oled = time.ticks_ms()
                    pct = job["line"] * 100 // max(1, job["total"])
                    update_oled("PLOTTING", "%d/%d" % (job["line"], job["total"]), "%d%%" % pct)
        final = "stopped" if job["stop"] else "done"
    except Exception as e:
        print("Job error:", repr(e))
        final = "error"
    # "parking": motion commands stay blocked until parked, and the page says why it waits
    job["state"] = "parking"
    update_oled("PARKING", "%d/%d" % (job["line"], job["total"]))
    await go_park()
    job["state"] = final
    update_oled(REVISION, job["state"].upper(), "%d/%d" % (job["line"], job["total"]))

# --- 4. Wi-Fi: rescue.py (transmit power from the settings, see TXPOWER there) ---
import rescue

IP = connect(lambda *lines: update_oled(*lines))

def ready_screen():
    # The idle OLED: how to reach the board. On the fallback access point, two pages that
    # alternate (oled_task): why the networks from the settings failed, and the AP.
    global ready_pages
    sta = network.WLAN(network.STA_IF)
    ip = sta.ifconfig()[0] if sta.isconnected() else IP  # may change after a rejoin
    ap, problem = wifi_state.get("ap"), wifi_state["problem"]
    if problem:
        ready_pages = [(REVISION, "NO WIFI", problem), (REVISION, "AP " + ap, ip)]
    else:
        # On a network: its name (http://<name>.local) and IP; on its own AP: the AP and IP
        ready_pages = [(REVISION, "AP " + ap if ap else NAME, ip)]
    update_oled(*ready_pages[0])

def wifi_show(*lines):
    # For wifi_watch: a message while the connection is lost, the idle screen once it's back
    if lines:
        update_oled(*lines)
    else:
        ready_screen()

# --- 5. Web UI ---
# The page is a separate file on flash (upload index.html).
# Embedding it as a string made the script too big to compile in the C3's RAM.
WEB_FILE = "index.html"
web_buf = bytearray(1024)  # reused for every page request

# --- 6. Async Server Loop ---
MOTION_CMDS = ("G0", "G1", "ANG", "PARK", "CFG", "PW", "CAL", "TEST_SHOULDER", "TEST_ELBOW", "TEST_PEN", "DONE")
MAX_CHUNK = 8192

moving = {"cmd": None}  # a single command still moving the arm: a second one would fight it

async def handle_cmd(cmd_text, progress):
    name = cmd_text.split(" ", 1)[0]
    if name in MOTION_CMDS + ("JOB_BEGIN", "JOB_START"):
        if job["state"] in JOB_ACTIVE:
            raise ValueError("busy: a plot is in progress")
        if moving["cmd"]:
            raise ValueError("busy: still moving for " + moving["cmd"])
    if name not in MOTION_CMDS:
        return await run_cmd(cmd_text, progress)
    moving["cmd"] = cmd_text
    try:
        await run_cmd(cmd_text, progress)
    finally:
        moving["cmd"] = None

async def run_cmd(cmd_text, progress):
    if cmd_text.startswith("JOB_BEGIN"):
        # JOB_BEGIN <lines> <bytes>: start a new upload, checking there is room on flash
        _, total, size = cmd_text.split()
        st = os.statvfs("/")
        free = st[0] * st[4]
        try:
            free += os.stat(JOB_FILE)[6]  # the old job file gets replaced
        except OSError:
            pass
        if int(size) > free - 16384:
            raise ValueError("drawing too big: %d bytes, %d free" % (int(size), free))
        open(JOB_FILE, "w").close()
        job["state"], job["total"], job["line"] = "uploading", int(total), 0
        update_oled("UPLOADING", total + " pts")

    elif cmd_text == "JOB_START":
        if job["state"] != "uploading":
            raise ValueError("no uploaded job")
        job["state"] = "running"
        job["task"] = asyncio.create_task(run_job())  # keep a reference to the task

    elif cmd_text == "JOB_PAUSE":
        job["pause"] = True

    elif cmd_text == "JOB_RESUME":
        job["pause"] = False

    elif cmd_text == "JOB_STOP":
        job["stop"] = True
        if job["state"] == "uploading":
            job["state"] = "stopped"

    elif cmd_text == "TEST_SHOULDER":
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
        # CFG key=value ... with the config key names; unknown keys are rejected
        new = {}
        for p in cmd_text.split()[1:]:
            k, v = p.split('=')
            if k not in config or isinstance(config[k], list):
                raise ValueError("unknown setting " + k)
            if v:
                new[k] = float(v)
        if new.get("L1", 1) <= 0 or new.get("L2", 1) <= 0 or new.get("speed", 1) <= 0:
            raise ValueError("arm lengths and speed must be positive")
        config.update(new)
        save_config()
        restore_hardware()  # apply new calibration to the current position
        update_oled("CONFIG", "SAVED")

    elif cmd_text.startswith("PW"):
        # PW S=<us> E=<us>: precise calibration, both arm servos to exact pulses (either may
        # be left out). Glides at most 20 us per servo frame, no hysteresis correction.
        # 500-2500 us here, wider than the 750-2250 us while drawing: on the template the
        # shoulder must reach 0 deg for the elbow step (about 600 us with the standard mounting),
        # and someone is watching the arm.
        if raw["s"] is None:
            raw["s"], raw["e"] = pulse(0, state["s"]), pulse(1, state["e"])
        s_pw, e_pw = raw["s"], raw["e"]
        for p in cmd_text.split()[1:]:
            k, v = p.split('=')
            if k == 'S': s_pw = max(500.0, min(2500.0, float(v)))
            elif k == 'E': e_pw = max(500.0, min(2500.0, float(v)))
        steps = max(1, math.ceil(max(abs(s_pw - raw["s"]), abs(e_pw - raw["e"])) / 20))
        s0, e0 = raw["s"], raw["e"]
        pen = config["pen_down"] if state["pen"] else config["pen_up"]
        for i in range(1, steps + 1):
            servo_shoulder.duty_ns(int((s0 + (s_pw - s0) * i / steps) * 1000))
            servo_elbow.duty_ns(int((e0 + (e_pw - e0) * i / steps) * 1000))
            servo_pen.duty_ns(int(pen * 1000))
            await next_frame()
        raw["s"], raw["e"] = s_pw, e_pw
        # Keep the angles in step, so the next normal move starts from here
        state["s"], state["e"] = angle_of(0, s_pw), angle_of(1, e_pw)
        pos["x"] = pos["y"] = None
        update_oled("CALIBRATE", "S %d us" % s_pw, "E %d us" % e_pw)

    elif cmd_text.startswith("CAL"):
        # CAL s1|s2 angle:pulse,angle:pulse,... saves measured points; CAL s1|s2 alone deletes them
        parts = cmd_text.split()
        if len(parts) < 2 or parts[1] not in ("s1", "s2"):
            raise ValueError("CAL s1|s2 [angle:pulse,...]")
        points = [p.split(":") for p in parts[2].split(",")] if len(parts) > 2 else []
        table = check_table(points)
        if len(table) == 1:
            raise ValueError("needs at least two points")
        config[parts[1] + "_table"] = table
        save_config()
        if raw["s"] is not None:  # the servos stay put; their angles follow the new calibration
            state["s"], state["e"] = angle_of(0, raw["s"]), angle_of(1, raw["e"])
        update_oled("CALIBRATE", parts[1].upper(), "%d points" % len(table))

    elif cmd_text == "PARK":
        await go_park()
        update_oled(REVISION, "PARKED")

    elif cmd_text == "DONE":
        await move_to(state["s"], state["e"], False)
        update_oled(REVISION, "DONE!")

    elif cmd_text.split(" ", 1)[0] in ("G0", "G1"):
        await execute_gcode(cmd_text)
        if progress:
            update_oled("DRAWING", progress)

    else:
        raise ValueError("unknown command " + cmd_text.split(" ", 1)[0])

async def handle_client(reader, writer):
    req = ""
    try:
        req, length, auth = await read_request(reader)
        if not req.startswith(("POST /cmd", "GET /status", "POST /job")):
            print("HTTP:", req.strip())

        if req.startswith("GET / "):
            headers = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html\r\n"
                "Cache-Control: no-cache, no-store, must-revalidate\r\n"
                "Connection: close\r\n\r\n"
            )
            try:
                f = open(WEB_FILE, "rb")
            except OSError:
                print("Web page missing: upload", WEB_FILE, "to the board")
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n"
                             b"index.html is missing on the board: upload index.html")
            else:
                # Stream from flash in 1 KB pieces so the page never sits in RAM
                writer.write(headers.encode('utf-8'))
                mv = memoryview(web_buf)
                with f:
                    while True:
                        n = f.readinto(web_buf)
                        if not n:
                            break
                        writer.write(mv[:n])
                        await writer.drain()
        elif req.startswith(("PUT /file/", "POST /reset")) and job["state"] in JOB_ACTIVE:
            # An update or a restart would cut the plot short: finish or stop it first
            writer.write(b"HTTP/1.1 409 Conflict\r\nConnection: close\r\n\r\nERR busy: a plot is in progress")
        elif await handle_update(req, reader, writer, length, auth):
            pass
        elif req.startswith("GET /config "):
            # The calibration in effect (defaults and this board's changes), for the web page
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
            writer.write(json.dumps(config).encode())
        elif req.startswith("GET /calibration "):
            # This board's own calibration file (only what differs from the defaults), for
            # flash.sh --get-calibration
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
            writer.write(json.dumps(read_json(CAL_FILE) or {}).encode())
        elif req.startswith("GET /status "):
            # Polled by the web page while a job runs
            st = {"rev": REVISION, "name": NAME, "job": job["state"], "line": job["line"], "total": job["total"],
                  "s": state["s"], "e": state["e"], "p": int(state["pen"])}
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
            writer.write(json.dumps(st).encode())
        elif req.startswith("POST /job "):
            # One chunk of G-code lines, appended to the job file
            if job["state"] != "uploading":
                writer.write(b"HTTP/1.1 409 Conflict\r\nConnection: close\r\n\r\nERR not uploading")
            elif length > MAX_CHUNK:
                writer.write(b"HTTP/1.1 413 Payload Too Large\r\nConnection: close\r\n\r\nERR chunk too big")
            else:
                data = await recv(reader.readexactly(length)) if length else b""
                with open(JOB_FILE, "ab") as f:
                    f.write(data)
                writer.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nOK")
        elif req.startswith("POST /cmd"):
            # read(256) may return a partial body; read exactly Content-Length bytes
            raw = (await recv(reader.readexactly(length))).decode('utf-8').strip() if length else ""
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
        event("HTTP error %r on %s" % (e, req.strip()[:40]))
    finally:
        # Always close, otherwise a bad request leaks a socket on every error
        writer.close()
        await writer.wait_closed()

async def main():
    try:
        server = await asyncio.start_server(handle_client, "0.0.0.0", 80)
    except OSError as e:
        # Typically EADDRINUSE after a soft reboot: port 80 is still held
        print("Server start failed:", repr(e), "- press RESET / power-cycle")
        update_oled("NO SERVER", "PRESS RST", str(e))
        return
    print(REVISION, "Server Running on http://%s/" % IP)
    ready_screen()
    if settings.get("networks"):
        asyncio.create_task(wifi_watch(wifi_show))
    try:
        await oled_task()
    finally:
        server.close()  # frees port 80 for the rescue server

asyncio.run(main())
