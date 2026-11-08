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
try:
    with open("customization.json") as f:
        custom = json.load(f)
except OSError:
    custom = {}

def servo_pins():
    # (shoulder, elbow, pen): ESP32-C3 OLED board GPIO 0/1/3, classic ESP32 DevKit GPIO 25/26/27;
    # "pins" in customization.json overrides any of them, e.g. {"shoulder": 4} if GPIO 0 was damaged
    pins = dict(zip(("shoulder", "elbow", "pen"),
                    (0, 1, 3) if "ESP32C3" in os.uname().machine else (25, 26, 27)))
    pins.update(custom.get("pins", {}))
    return pins["shoulder"], pins["elbow"], pins["pen"]

events = []  # last Wi-Fi and update events, served at /log

def event(msg):
    line = "%d %s" % (time.ticks_ms() // 1000, msg)
    print(line)
    events.append(line)
    if len(events) > 30:
        events.pop(0)

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
        for _ in range(150):
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
    # the board's own access point. Returns (IP, joined).
    sta = network.WLAN(network.STA_IF)
    if sta.isconnected():
        return sta.ifconfig()[0], True
    if "hostname" in custom:
        network.hostname(custom["hostname"])  # before any interface is active
    ip = custom.get("networks") and join_wifi(custom["networks"], show)
    if ip:
        return ip, True
    ap = custom.get("ap", {})
    return start_ap(ap.get("ssid", "ESP32-Plotter"), ap.get("password", "plotterpassword")), False

async def wifi_watch():
    # Rejoin when the connection drops; until then the board is unreachable
    sta = network.WLAN(network.STA_IF)
    while True:
        await asyncio.sleep(10)
        if sta.isconnected():
            continue
        if sta.status() == network.STAT_CONNECTING:
            event("Wi-Fi lost, reconnecting")  # the Wi-Fi stack retries by itself
            continue
        event("Wi-Fi lost, status %d" % sta.status())
        for n in custom["networks"]:
            try:
                sta.connect(n["ssid"], n["password"])
            except OSError as e:
                event("connect %s: %r" % (n["ssid"], e))
                continue
            for _ in range(150):
                await asyncio.sleep_ms(100)
                if sta.isconnected():
                    break
            if sta.isconnected():
                event("rejoined %s rssi %d" % (n["ssid"], sta.status("rssi")))
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
        writer.write("\n".join(events).encode())
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
        ip, joined = connect()
        await asyncio.start_server(client, "0.0.0.0", 80)
        event("rescue server on http://%s/" % ip)
        if joined:
            asyncio.create_task(wifi_watch())
        while True:
            await asyncio.sleep(3600)

    asyncio.new_event_loop()  # drop the failed plotter's tasks
    asyncio.run(run())
