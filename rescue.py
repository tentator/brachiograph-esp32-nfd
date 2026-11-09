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

# customization.json (optional, see customization.example.json): this board's name,
# Wi-Fi, update password and pins. Set on the computer and uploaded by flash.sh;
# never served over HTTP. The calibration lives in config.json, set on the web page.
custom_error = None
try:
    with open("customization.json") as f:
        custom = json.load(f)
except OSError:
    custom = {}
except ValueError as e:  # a typo in the file must not stop the boot: defaults, error in /log
    custom, custom_error = {}, e

def servo_pins():
    # (shoulder, elbow, pen): classic ESP32 DevKit GPIO 25/26/27, ESP32-S2/S3 GPIO 4/5/6 (25-27
    # are flash pins there), ESP32-C2/C3/C5/C6 GPIO 0/1/3 (the C3 OLED board's wiring);
    # "pins" in customization.json overrides any of them, e.g. {"shoulder": 4} if GPIO 0 was damaged
    chip = os.uname().machine.split()[-1]  # "Generic ESP32S3 module with ESP32S3" -> "ESP32S3"
    pins = dict(zip(("shoulder", "elbow", "pen"),
                    {"ESP32": (25, 26, 27), "ESP32S2": (4, 5, 6), "ESP32S3": (4, 5, 6)}.get(chip, (0, 1, 3))))
    pins.update(custom.get("pins", {}))
    return pins["shoulder"], pins["elbow"], pins["pen"]

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
if custom_error:
    event("customization.json invalid, using defaults: %r" % custom_error)

JOIN_TIMEOUT_S = 30  # 15 s was too short on a weak signal: the board fell back to its own AP

def join_wifi(networks, show):
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.config(pm=sta.PM_NONE)  # power save delays every reply by up to ~150 ms
    seen = [n[0].decode() for n in sta.scan()]
    for n in networks:
        if n["ssid"] not in seen:
            continue
        show("WIFI", n["ssid"])
        sta.connect(n["ssid"], n["password"])
        for _ in range(JOIN_TIMEOUT_S * 10):
            if sta.isconnected():
                event("joined %s rssi %d IP %s" % (n["ssid"], sta.status("rssi"), sta.ifconfig()[0]))
                return sta.ifconfig()[0]
            time.sleep_ms(100)
        event("could not join " + n["ssid"])
        sta.disconnect()
    sta.active(False)
    return None

def start_ap(ssid, password):
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=ssid, password=password)
    while not ap.active():
        time.sleep_ms(100)
    # AP mode defaults to 192.168.4.1 (ESP-IDF default); read it instead of assuming
    event("AP '%s' up, IP %s" % (ssid, ap.ifconfig()[0]))
    return ap.ifconfig()[0]

def connect(show=print):
    # Joins the first network from customization.json that is in range, otherwise opens
    # the board's own access point. Returns the board's IP.
    sta = network.WLAN(network.STA_IF)
    if sta.isconnected():
        return sta.ifconfig()[0]
    if "hostname" in custom:
        network.hostname(custom["hostname"])  # before any interface is active
    ap = custom.get("ap", {})
    return (custom.get("networks") and join_wifi(custom["networks"], show)) or \
        start_ap(ap.get("ssid", "ESP32-Plotter"), ap.get("password", "plotterpassword"))

async def wifi_watch():
    # Rejoin when the connection drops, and leave the fallback access point as soon as a
    # network from customization.json can be joined; until then the board is unreachable
    sta, ap = network.WLAN(network.STA_IF), network.WLAN(network.AP_IF)
    while True:
        await asyncio.sleep(60 if ap.active() else 10)
        if sta.isconnected():
            continue
        if sta.status() == network.STAT_CONNECTING:
            event("Wi-Fi lost, reconnecting")  # the Wi-Fi stack retries by itself
            continue
        if ap.active() and ap.status("stations"):
            continue  # trying networks hops channels and drops the phones on the board's own AP
        if not ap.active():
            event("Wi-Fi lost, status %d" % sta.status())
        sta.active(True)
        for n in custom["networks"]:
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
                ap.active(False)
                break
            sta.disconnect()

# Updates over Wi-Fi (flash.sh without USB) need "ota_password" in customization.json
OTA_AUTH = custom.get("ota_password") and \
    b"Basic " + binascii.b2a_base64(b"ota:" + custom["ota_password"].encode()).strip()
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
    # PUT /file/<name>, POST /reset and GET /log; False for any other request
    if req.startswith(("PUT /file/", "POST /reset")) and (not OTA_AUTH or auth != OTA_AUTH):
        writer.write(b"HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\nERR password")
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
        if custom.get("networks"):
            asyncio.create_task(wifi_watch())
        while True:
            await asyncio.sleep(3600)

    asyncio.new_event_loop()  # drop the failed plotter's tasks
    asyncio.run(run())
