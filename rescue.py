# Wi-Fi, updates over Wi-Fi and the event log. Shared by the plotter and by the rescue
# server that main.py starts when the plotter fails, so a broken plotter.py can always be
# replaced over Wi-Fi. Keep this file small and change it rarely: a broken rescue.py needs USB.
import binascii
import json
import machine
import network
import os
import time
import uasyncio as asyncio

# Board settings (name, Wi-Fi, update password, access point, hardware): board_default_settings.json
# (from git, uploaded by flash.sh with every update), overridden key by key by the optional
# board_settings.json (yours, uploaded by flash.sh when it is next to it; the board keeps its own
# otherwise). Never served over HTTP. The calibration is separate, see plotter.py.
DEFAULTS_FILE, SETTINGS_FILE = "board_default_settings.json", "board_settings.json"
CAL_DEFAULTS_FILE, CAL_FILE = "board_default_calibration.json", "board_calibration.json"
# The only defaults in the code: enough to open an access point and be repaired over Wi-Fi if
# board_default_settings.json is missing or broken
FALLBACK = {"hostname": "plotter", "mac_suffix": True, "networks": [],
            "ap": {"ssid": "ESP32-Plotter", "password": "plotterpassword"}}
notes = []  # for /log, once event() exists

def read_json(name):
    try:
        with open(name) as f:
            return json.load(f)
    except OSError:
        return None  # not there
    except ValueError as e:  # a typo must not stop the boot: carry on without it, error in /log
        notes.append("%s invalid, ignored: %r" % (name, e))
        return None

def exists(name):
    try:
        os.stat(name)
        return True
    except OSError:
        return False

def migrate():
    # Up to REV 17 the settings were in customization.json and the calibration in config.json
    if exists("config.json") and not exists(CAL_FILE):
        os.rename("config.json", CAL_FILE)
        notes.append("config.json -> %s" % CAL_FILE)
    if exists("customization.json") and not exists(SETTINGS_FILE):
        old = read_json("customization.json") or {}
        cal_keys = read_json(CAL_DEFAULTS_FILE) or {}
        mine = {k: v for k, v in old.items() if k not in cal_keys and k != "arms"}
        with open(SETTINGS_FILE, "w") as f:
            json.dump(mine, f)
        # Calibration values in it join the calibration file; what is already there wins
        cal = {k: v for k, v in old.items() if k in cal_keys}
        if cal:
            cal.update(read_json(CAL_FILE) or {})
            with open(CAL_FILE, "w") as f:
                json.dump(cal, f)
        os.remove("customization.json")
        notes.append("customization.json -> %s" % SETTINGS_FILE)

try:
    migrate()
except OSError as e:
    notes.append("settings migration failed: %r" % e)

defaults = read_json(DEFAULTS_FILE)
if defaults is None:
    notes.append("%s missing or broken: only an access point (flash.sh puts it back)" % DEFAULTS_FILE)
    defaults = FALLBACK
own = read_json(SETTINGS_FILE) or {}
settings = dict(defaults)
settings.update(own)

def chip():
    # "ESP32C3 module with ESP32-C3" -> "ESP32C3", "Generic ESP32 module with ESP32" -> "ESP32"
    return os.uname().machine.split()[-1].replace("-", "")

def for_chip(value):
    # Settings that differ by chip are maps {"*": for any chip, "ESP32C3": ..., "ESP32": ...}
    if isinstance(value, dict) and "*" in value:
        return value.get(chip(), value["*"])
    return value

def setting(name):
    return for_chip(settings.get(name))

def servo_pins():
    # (shoulder, elbow, pen) from "pins": the chip's defaults, then any joints set in
    # board_settings.json, e.g. {"shoulder": 4} if GPIO 0 was damaged. () if unknown.
    pins = dict(for_chip(defaults.get("pins")) or {})
    pins.update(for_chip(own.get("pins")) or {})
    try:
        return pins["shoulder"], pins["elbow"], pins["pen"]
    except KeyError:
        return ()

class Led:
    # The board's own LED, blinked as a heartbeat: "led" in the settings, {"pin": 8,
    # "active_low": true} or false for none (S3 boards have an RGB LED that needs a driver)
    def __init__(self):
        led = setting("led")
        self.pin, self.low = None, False
        if led:
            if led["pin"] in servo_pins():
                event("LED pin %d is a servo pin, LED off" % led["pin"])
                return
            self.pin, self.low = machine.Pin(led["pin"], machine.Pin.OUT), led.get("active_low", False)
        self.set(False)

    def set(self, on):
        if self.pin:
            self.pin.value(on != self.low)

# Wi-Fi, update and error events with seconds since boot, served at /log. Kept in flash,
# so after a hang and a power cycle the log still shows what happened before it.
LOG, LOG_OLD, LOG_MAX = "log.txt", "log.old", 8192

def event(msg):
    line = "%d %s\n" % (time.ticks_ms() // 1000, msg)
    print(line, end="")
    try:
        if os.stat(LOG)[6] > LOG_MAX:
            os.rename(LOG, LOG_OLD)
    except OSError:
        pass  # no log yet
    try:
        with open(LOG, "a") as f:
            f.write(line)
    except OSError:
        pass  # flash full or failing: the log must never stop the board

event("boot, reset cause %d" % machine.reset_cause())
for n in notes:
    event(n)

JOIN_TIMEOUT_S = 30  # 15 s was too short on a weak signal: the board fell back to its own AP
ERROR_SHOW_MS = 2500  # how long a Wi-Fi error stays on the OLED before the board moves on
# Why the board is on its own access point instead of a network, for the OLED and /log
wifi_state = {"problem": None}
DEFAULT_AP = (FALLBACK["ap"]["ssid"], FALLBACK["ap"]["password"])
SCANS = 3  # a single scan often misses a network that is there

# "-60": the last byte of the board's MAC address (as esptool prints it), added to the hostname
# and the access point's name, so boards sharing one board_settings.json stay apart
SUFFIX = "-%02x" % machine.unique_id()[-1] if settings.get("mac_suffix", True) else ""
NAME = settings.get("hostname", FALLBACK["hostname"]) + SUFFIX  # also http://<NAME>.local where mDNS works

# Transmit power in dBm, null for the chip's full power. The defaults give ESP32-C3 boards 13: at
# the full 20 dBm the access point of a C3 "SuperMini" was invisible, and a C3 OLED board couldn't
# join a network 1 m from the router or kept dropping it; at 13 both work.
TXPOWER = setting("txpower")

def set_txpower(wlan):
    if TXPOWER:
        try:
            wlan.config(txpower=TXPOWER)
        except (OSError, ValueError) as e:
            event("txpower %r refused: %r" % (TXPOWER, e))

def why(status):
    # The station's status code in words, for the OLED and /log
    for name, text in (("STAT_WRONG_PASSWORD", "wrong password"), ("STAT_NO_AP_FOUND", "not found"),
                       ("STAT_HANDSHAKE_TIMEOUT", "wrong password?"), ("STAT_ASSOC_FAIL", "refused")):
        if status == getattr(network, name, None):
            return text
    return "no answer"

def join_wifi(networks, show):
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    set_txpower(sta)
    sta.config(pm=sta.PM_NONE)  # power save delays every reply by up to ~150 ms
    wanted = [n["ssid"] for n in networks]
    seen = []
    for i in range(SCANS):
        seen += [n[0].decode() for n in sta.scan()]
        if any(w in seen for w in wanted):
            break
        show("WIFI", "scanning", "%d/%d" % (i + 2, SCANS))
    tried = []
    for n in networks:
        if n["ssid"] not in seen:
            continue
        show("WIFI", n["ssid"], "joining")
        sta.connect(n["ssid"], n["password"])
        bad = 0  # how long (in 100 ms) the station has kept reporting a definite failure
        for i in range(JOIN_TIMEOUT_S * 10):
            if sta.isconnected():
                event("joined %s rssi %d IP %s" % (n["ssid"], sta.status("rssi"), sta.ifconfig()[0]))
                network.WLAN(network.AP_IF).active(False)  # an AP survives a soft reboot
                return sta.ifconfig()[0]
            # "wrong password" also shows up for a moment while the handshake is retried,
            # so only give up early if it stays for 10 s
            bad = bad + 1 if why(sta.status()) in ("wrong password", "not found") else 0
            if bad >= 100:
                break
            if i % 50 == 49:
                show("WIFI", n["ssid"], "%d s" % (JOIN_TIMEOUT_S - (i + 1) // 10))
            time.sleep_ms(100)
        reason = why(sta.status())
        event("could not join %s: %s (status %d)" % (n["ssid"], reason, sta.status()))
        show("NO WIFI", reason, n["ssid"])
        time.sleep_ms(ERROR_SHOW_MS)  # long enough to read on the OLED
        tried.append("%s (%s)" % (n["ssid"], reason))
        sta.disconnect()
    if tried:
        wifi_state["problem"] = "could not join " + ", ".join(tried)
    else:
        # Not in the scan: out of range, 5 GHz only (the ESP32 has 2.4 GHz only) or a typo
        wifi_state["problem"] = "not in range: " + ", ".join(n["ssid"] for n in networks)
        event("no network from the settings in range; seen: " + ", ".join(sorted(set(seen))))
        show("NO WIFI", "not in range", networks[0]["ssid"])
        time.sleep_ms(ERROR_SHOW_MS)
    sta.active(False)
    return None

def start_ap(ssid, password):
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    set_txpower(ap)
    try:
        ap.config(essid=ssid, password=password, authmode=3)  # WPA2-PSK, as in Chris Huang's Wi-Fi client mode
    except (OSError, ValueError) as e:  # e.g. a password under 8 characters: no Wi-Fi at all
        event("AP '%s' failed: %r, opening '%s' instead" % (ssid, e, DEFAULT_AP[0] + SUFFIX))
        ssid, password = DEFAULT_AP[0] + SUFFIX, DEFAULT_AP[1]
        ap.config(essid=ssid, password=password, authmode=3)
    wifi_state["ap"] = ssid
    while not ap.active():
        time.sleep_ms(100)
    # AP mode defaults to 192.168.4.1 (ESP-IDF default); read it instead of assuming
    event("AP '%s' up, IP %s" % (ssid, ap.ifconfig()[0]))
    return ap.ifconfig()[0]

def connect(show=print):
    # Joins the first network from the settings that is in range, otherwise opens
    # the board's own access point. Returns the board's IP.
    sta = network.WLAN(network.STA_IF)
    if sta.isconnected():
        return sta.ifconfig()[0]
    network.hostname(NAME)  # before any interface is active
    event("name %s, txpower %s" % (NAME, TXPOWER or "default"))
    ap = settings.get("ap") or {}
    return (settings.get("networks") and join_wifi(settings["networks"], show)) or \
        start_ap(ap.get("ssid", DEFAULT_AP[0]) + SUFFIX, ap.get("password", DEFAULT_AP[1]))

async def wifi_watch(show=None):
    # Rejoin when the connection drops. Not on the fallback access point: trying networks
    # takes the radio off the AP's channel, and phones then couldn't even see the AP.
    # The board tries the networks again at the next restart.
    # show(lines...) reports on the OLED, show() with no lines when connected again.
    sta, ap = network.WLAN(network.STA_IF), network.WLAN(network.AP_IF)
    lost = False
    while not ap.active():
        await asyncio.sleep(10)
        if sta.isconnected():
            if lost and show:
                show()
            lost = False
            continue
        if not lost and show:
            show("WIFI LOST", "rejoining")
        lost = True
        if sta.status() == network.STAT_CONNECTING:
            event("Wi-Fi lost, reconnecting")  # the Wi-Fi stack retries by itself
            continue
        event("Wi-Fi lost, status %d" % sta.status())
        sta.active(True)
        set_txpower(sta)
        for n in settings["networks"]:
            try:
                sta.connect(n["ssid"], n["password"])
            except OSError as e:
                event("connect %s: %r" % (n["ssid"], e))
                continue
            for _ in range(JOIN_TIMEOUT_S * 10):
                await asyncio.sleep_ms(100)
                if sta.isconnected():
                    break
            if sta.isconnected():
                event("rejoined %s rssi %d IP %s" % (n["ssid"], sta.status("rssi"), sta.ifconfig()[0]))
                break
            sta.disconnect()

# Updates over Wi-Fi (flash.sh without USB) need "ota_password" in board_settings.json
OTA_AUTH = settings.get("ota_password") and \
    b"Basic " + binascii.b2a_base64(b"ota:" + settings["ota_password"].encode()).strip()
def recv(awaitable, timeout_s=10):
    # A client that sends nothing would otherwise hold one of the few sockets for good
    return asyncio.wait_for(awaitable, timeout_s)

async def read_request(reader):
    # Request line and the two headers we use: (request line, Content-Length, Authorization)
    req = (await recv(reader.readline())).decode()
    length, auth = 0, b""
    while True:
        h = await recv(reader.readline())
        if h in (b"\r\n", b""):
            return req, length, auth
        if h.lower().startswith(b"content-length:"):
            length = int(h.split(b":", 1)[1].decode().strip())
        elif h.lower().startswith(b"authorization:"):
            auth = h.split(b":", 1)[1].strip()

async def reset_soon():
    await asyncio.sleep_ms(500)  # let the reply go out first
    # End the servo pulses cleanly first, as at power-on: a reset in the middle of a pulse
    # left the signal undefined and the servos jumped
    for pin in servo_pins():
        machine.Pin(pin, machine.Pin.OUT, value=0)
    await asyncio.sleep_ms(100)
    machine.reset()

async def handle_update(req, reader, writer, length, auth):
    # PUT/DELETE /file/<name>, POST /reset, GET /auth, GET /log and GET /files; False for any other request
    if req.startswith(("PUT /file/", "DELETE /file/", "POST /reset", "GET /auth ")) and \
            (not OTA_AUTH or auth != OTA_AUTH):
        writer.write(b"HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\nERR password")
    elif req.startswith("GET /auth "):
        # Lets flash.sh check the update password before it uploads anything, and tells it the
        # MicroPython version (precompiled .mpy files must match it)
        writer.write(("HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nOK " + os.uname().release).encode())
    elif req.startswith("DELETE /file/"):
        # flash.sh removes plotter.py when it uploads plotter.mpy: MicroPython would load the .py first
        name = req.split()[1][6:]
        if not name or "/" in name:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\nERR name")
        else:
            try:
                os.remove(name)
                event("deleted %s" % name)
            except OSError:
                pass  # not there: fine
            writer.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nOK")
    elif req.startswith("PUT /file/"):
        # One file: written to <name>.tmp, renamed when complete
        name = req.split()[1][6:]
        if not name or "/" in name:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\nERR name")
        elif not length:
            # without Content-Length (chunked) the file would be replaced by an empty one
            writer.write(b"HTTP/1.1 411 Length Required\r\nConnection: close\r\n\r\nERR length")
        else:
            left = length
            try:
                with open(name + ".tmp", "wb") as f:
                    while left:
                        data = await recv(reader.read(min(1024, left)), 30)  # weak Wi-Fi stalls for >10 s
                        if not data:
                            raise OSError("upload cut off")
                        f.write(data)
                        left -= len(data)
            except Exception:
                os.remove(name + ".tmp")
                raise
            os.rename(name + ".tmp", name)
            event("updated %s (%d bytes)" % (name, length))
            writer.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nOK")
    elif req.startswith("POST /reset"):
        event("reset requested")
        writer.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nOK")
        asyncio.create_task(reset_soon())
    elif req.startswith("GET /log "):
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n")
        for name in (LOG_OLD, LOG):
            try:
                with open(name, "rb") as f:
                    writer.write(f.read())
            except OSError:
                pass  # not written yet
    elif req.startswith("GET /files "):
        # What flash.sh put on the board: one "size name" line per file
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n")
        for name in sorted(os.listdir()):
            writer.write(("%7d %s\n" % (os.stat(name)[6], name)).encode())
    else:
        return False
    return True

def serve(error):
    # Rescue mode: Wi-Fi and updates only, the plotter's error in /log
    event("plotter failed, rescue mode:\n" + error)

    async def client(reader, writer):
        req = ""
        try:
            req, length, auth = await read_request(reader)
            if not await handle_update(req, reader, writer, length, auth):
                writer.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/plain\r\n"
                             b"Connection: close\r\n\r\nThe plotter failed to start, see /log")
            await writer.drain()
        except Exception as e:
            event("HTTP error %r on %s" % (e, req.strip()[:40]))
        finally:
            writer.close()
            await writer.wait_closed()

    async def run():
        ip = connect()
        await asyncio.start_server(client, "0.0.0.0", 80)
        event("rescue server on http://%s/" % ip)
        if settings.get("networks"):
            asyncio.create_task(wifi_watch())
        # Three quick LED flashes every 2 s: rescue mode, as opposed to the plotter's slow heartbeat
        led = Led()
        while True:
            for _ in range(3):
                led.set(True)
                await asyncio.sleep_ms(100)
                led.set(False)
                await asyncio.sleep_ms(100)
            await asyncio.sleep_ms(1400)

    asyncio.new_event_loop()  # drop the failed plotter's tasks
    asyncio.run(run())
