#!/usr/bin/env python3
"""Draws images/flash.png and images/wiring.png to scale: the ESP32-C3 0.42" OLED board on a
170-hole mini breadboard (46.2 x 34.4 mm). Needs Pillow: pip install pillow; python3 draw.py

Drawn turned by 180 degrees: USB-C on the left, servos on the right, text upright.
Jumper colours: black GND bus, brown GND to the servos, red +5 V bus, orange +5 V to the servos, green shoulder (GPIO0), yellow elbow (GPIO1),
white pen (GPIO3); each servo's own cable (brown -, red +, yellow signal) plugs into them halfway.
"""
import os
from PIL import Image, ImageDraw, ImageFont

TEXT = 1.2                   # text scale
MM = 24                      # pixels per mm while drawing; halved at the end for smooth edges
P = 2.54                     # hole pitch, mm
BB_W, BB_H = 46.2, 34.4      # breadboard
BOARD_W, BOARD_H = 24.8, 20.45
COLOURS = {"gnd": (30, 30, 30), "5v": (211, 47, 47), "servo5v": (240, 110, 20), "servognd": (150, 95, 50), "shoulder": (46, 158, 68),
           "elbow": (242, 194, 0), "pen": (250, 250, 250), "arrow": (220, 30, 30),
           "servo-": (120, 72, 40), "servo+": (211, 47, 47), "servo-signal": (250, 175, 0)}
SERVO_CABLE = ("servo-", "servo+", "servo-signal")   # every servo: brown -, red +, yellow signal
TOP = ["0", "1", "2", "TX", "RX", "3V3", "GND", "5V"]     # header in row h, columns 10..17
BOTTOM = ["3", "4", "5", "6", "7", "8", "9", "10"]        # header in row c, columns 10..17
ROWS = {r: u for u, r in enumerate("abcde")} | {r: u + 7 for u, r in enumerate("fghij")}


def font(size_mm):
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, int(size_mm * TEXT * MM))
        except OSError:
            pass
    return ImageFont.load_default(size=int(size_mm * TEXT * MM))


class Drawing:
    def __init__(self, w_mm, h_mm, bb_x, bb_y):
        self.img = Image.new("RGB", (int(w_mm * MM), int(h_mm * MM)), "white")
        self.d = ImageDraw.Draw(self.img)
        self.w, self.h = w_mm, h_mm
        self.bx, self.by = bb_x, bb_y
        self.turned = True

    def px(self, x, y):
        if self.turned:
            x, y = self.w - x, self.h - y
        return x * MM, y * MM

    def box(self, x0, y0, x1, y1):
        (ax, ay), (bx, by) = self.px(x0, y0), self.px(x1, y1)
        return [min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)]

    def hole(self, col, row):
        """Centre of a breadboard hole (column 1..17, row a..j) in mm"""
        return (self.bx + (BB_W - 16 * P) / 2 + (col - 1) * P,
                self.by + (BB_H - 11 * P) / 2 + (11 - ROWS[row]) * P)

    def rect(self, x, y, w, h, fill, outline=None, r=0.6, width=0.15):
        self.d.rounded_rectangle(self.box(x, y, x + w, y + h), radius=r * MM,
                                 fill=fill, outline=outline, width=max(1, int(width * MM)))

    def circle(self, x, y, r, fill, outline=None, width=0.1):
        self.d.ellipse(self.box(x - r, y - r, x + r, y + r), fill=fill,
                       outline=outline, width=max(1, int(width * MM)))

    def text(self, x, y, s, size=1.6, fill=(20, 20, 20), anchor="mm"):
        if self.turned:
            anchor = {"l": "r", "r": "l"}.get(anchor[0], anchor[0]) + anchor[1]
        self.d.text(self.px(x, y), s, font=font(size), fill=fill, anchor=anchor)

    def wire(self, points, colour, width=1.0):
        """Jumper wire through points (mm), with a dark edge so white wires stay visible"""
        pts = [self.px(*p) for p in points]
        self.d.line(pts, fill=(90, 90, 90), width=int((width + 0.35) * MM), joint="curve")
        self.d.line(pts, fill=colour, width=int(width * MM), joint="curve")
        for p in (points[0], points[-1]):
            self.circle(*p, width * 0.75, (90, 90, 90))
            self.circle(*p, width * 0.55, colour)

    def arrow(self, tip, tail, label):
        self.d.line([self.px(*tail), self.px(*tip)], fill=COLOURS["arrow"], width=int(1.0 * MM))
        (tx, ty), (sx, sy) = tip, tail
        dx, dy = tx - sx, ty - sy
        n = (dx * dx + dy * dy) ** 0.5
        ux, uy = dx / n, dy / n
        head = [tip, (tx - 3 * ux - 2 * uy, ty - 3 * uy + 2 * ux), (tx - 3 * ux + 2 * uy, ty - 3 * uy - 2 * ux)]
        self.d.polygon([self.px(*p) for p in head], fill=COLOURS["arrow"])
        self.text(sx + 1, sy, label, 2.2, COLOURS["arrow"], "lm")

    def breadboard(self):
        self.rect(self.bx, self.by, BB_W, BB_H, (245, 245, 240), (200, 200, 195), r=1.2)
        mid = (self.hole(1, "e")[1] + self.hole(1, "f")[1]) / 2
        self.rect(self.bx + 1, mid - 0.9, BB_W - 2, 1.8, (225, 225, 220), r=0.4)
        for col in range(1, 18):
            for row in ROWS:
                x, y = self.hole(col, row)
                self.rect(x - 0.55, y - 0.55, 1.1, 1.1, (70, 70, 70), r=0.15)
            self.text(self.hole(col, "j")[0], self.by + 1.2, str(col), 1.0, (150, 150, 150))
        for row in ROWS:
            self.text(self.bx + 1.1, self.hole(1, row)[1], row, 1.0, (150, 150, 150))

    def board(self):
        x10, x17 = self.hole(10, "h")[0], self.hole(17, "h")[0]
        cx, cy = (x10 + x17) / 2, (self.hole(10, "h")[1] + self.hole(10, "c")[1]) / 2
        left, top = cx - BOARD_W / 2, cy - BOARD_H / 2
        self.rect(left, top, BOARD_W, BOARD_H, (24, 32, 96), (12, 16, 60), r=0.8)
        self.rect(cx + BOARD_W / 2 - 7.4, cy - 4.45, 8.4, 8.9, (190, 190, 195), (120, 120, 125), r=1.2)
        self.text(cx + BOARD_W / 2 - 3, cy, "USB-C", 1.1, (60, 60, 60))
        self.rect(cx - 8.5, cy - 4, 11, 8, (10, 10, 10), r=0.5)
        self.rect(cx - 7.6, cy - 2.6, 9.2, 5.3, (20, 40, 60), r=0.2)
        self.text(cx - 3, cy, "READY", 1.4, (120, 200, 255))
        for i, (t, b) in enumerate(zip(TOP, BOTTOM)):
            for label, row, dy in ((t, "h", 1.9), (b, "c", -1.9)):
                x, y = self.hole(10 + i, row)
                self.circle(x, y, 0.8, (212, 175, 55), (150, 120, 30))
                self.text(x, y + dy, label, 0.9, (235, 235, 235))
        for name, y in (("BOOT", cy - 2.6), ("RST", cy + 2.6)):
            self.rect(cx + 2.9, y - 0.9, 1.8, 1.8, (200, 200, 200), r=0.3)
            self.text(cx + 3.8, y + (-1.7 if name == "BOOT" else 1.7), name, 0.8, (235, 235, 235))
        return cx + BOARD_W / 2 + 1, cy  # where the USB plug goes

    def servo(self, x, y, label):
        """Servo seen from above, 23 x 12 mm; returns where its cable starts"""
        self.rect(x, y, 23, 12, (40, 40, 140), (20, 20, 90), r=0.8)
        self.circle(x + 6, y + 6, 3, (230, 230, 230), (150, 150, 150))
        self.rect(x + 6, y + 4.8, 12, 2.4, (240, 240, 240), (150, 150, 150), r=1.2)
        self.text(x + 11.5, y + 14.2 if self.turned else y - 2, label, 1.8)
        return x + 23, y + 6

    def usb_cable(self, at):
        x, y = at
        self.rect(x, y - 2.2, 7, 4.4, (60, 60, 60), r=1.0)
        self.d.line([self.px(x + 7, y), self.px(x + 26, y)], fill=(60, 60, 60), width=int(1.6 * MM))
        return x + 27, y

    def save(self, path):
        w, h = self.img.size
        self.img.resize((w // 2, h // 2), Image.LANCZOS).save(path, optimize=True)


def flash(path):
    dr = Drawing(110, 52, 8, 9)
    dr.breadboard()
    end = dr.usb_cable(dr.board())
    dr.arrow((end[0] + 1, end[1]), (end[0] + 14, end[1]), "computer")
    dr.save(path)


def wiring(path):
    dr = Drawing(191, 92, 75, 16)
    dr.breadboard()
    end = dr.usb_cable(dr.board())
    dr.arrow((end[0] + 1, end[1]), (end[0] + 12, end[1]), "USB-C power supply")
    dr.text(end[0] + 13, end[1] - 3.6, "5 V, at least 2 A", 1.6, COLOURS["arrow"], "lm")
    h = dr.hole

    # Bus in column 1: +5 V in the upper half (f-j), GND in the lower half (a-e),
    # fed from the board's 5V and GND pins over the top of the breadboard
    dr.wire([h(17, "j"), (h(17, "j")[0], dr.by - 2), (h(1, "j")[0], dr.by - 2), h(1, "j")], COLOURS["5v"])
    dr.wire([h(16, "i"), (h(16, "i")[0], dr.by - 4.5), (dr.bx - 2, dr.by - 4.5),
             (dr.bx - 2, h(1, "a")[1]), h(1, "a")], COLOURS["gnd"])

    # 2200 uF capacitor across the middle channel: + in 1f, - (stripe) in 1e
    x, yp, yn = h(1, "f")[0], h(1, "f")[1], h(1, "e")[1]
    body = (x + 5.5, (yp + yn) / 2)
    for y in (yp, yn):
        dr.d.line([dr.px(x, y), dr.px(body[0] - 3, y), dr.px(body[0] - 3, body[1])],
                  fill=(150, 150, 150), width=int(0.5 * MM), joint="curve")
        dr.circle(x, y, 0.55, (150, 150, 150))
    dr.rect(body[0] - 3, body[1] - 3.2, 9, 6.4, (25, 60, 140), (10, 30, 80), r=1.5)
    dr.rect(body[0] - 3, body[1] + 1.4, 9, 1.8, (200, 200, 210), r=0.4)
    dr.text(body[0] + 1.5, body[1] - 1.2, "2200 µF", 1.3, (235, 235, 235))
    dr.text(body[0] + 1.5, body[1] + 2.3, "−", 1.8, (20, 20, 20))

    # Servos, drawn on the right; each cable: GND (brown), +5 V (orange), signal
    servos = [("Shoulder servo", 6, 12, "i", "d", (10, "j"), "shoulder"),
              ("Elbow servo", 6, 36, "h", "c", (11, "j"), "elbow"),
              ("Pen servo", 6, 64, "g", "b", (10, "a"), "pen")]
    for k, (label, sx, sy, plus_row, gnd_row, sig, colour) in enumerate(servos):
        ox, oy = dr.servo(sx, sy, label)
        # The servo's own cable (brown, red, yellow), its plug, then jumpers in our colours
        plug = ox + 7
        for i, c in enumerate(SERVO_CABLE):
            dr.wire([(ox, oy - 2.4 + i * 2.4), (plug, oy - 2.4 + i * 2.4)], COLOURS[c], 0.8)
        dr.rect(plug, oy - 3.8, 3.5, 7.6, (35, 35, 35), (10, 10, 10), r=0.4)
        lane = plug + 6 + k * 7.5        # each servo's jumpers turn in their own lane
        for i, (start, c) in enumerate(((h(1, gnd_row), "servognd"), (h(1, plus_row), "servo5v"), (h(*sig), colour))):
            y, x_turn = oy - 2.4 + i * 2.4, lane + i * 2
            if c == colour and sig[1] == "j":     # signals leave the board upwards
                top = dr.by - 7 - k * 2.5 - (0 if colour == "shoulder" else 0)
                route = [start, (start[0], top), (x_turn, top), (x_turn, y), (plug + 3.5, y)]
            elif c == colour:                      # the pen signal leaves the board downwards
                bottom = dr.by + BB_H + 3
                route = [start, (start[0], bottom), (x_turn, bottom), (x_turn, y), (plug + 3.5, y)]
            else:
                route = [start, (x_turn, start[1]), (x_turn, y), (plug + 3.5, y)]
            dr.wire(route, COLOURS[c], 0.8)

    # Legend, top left
    dr.turned = False
    lx, ly = 4, 10
    dr.text(lx, ly - 5, "Servo cable, all three servos", 2.0, anchor="lm")
    for i, (c, name) in enumerate((("servo-", "brown: -"), ("servo+", "red: +"), ("servo-signal", "yellow: signal"))):
        x = lx + i * 19
        dr.wire([(x, ly), (x + 5, ly)], COLOURS[c], 0.9)
        dr.text(x + 6.5, ly, name, 1.6, anchor="lm")
    ly += 9
    dr.text(lx, ly, "Jumper wires", 2.0, anchor="lm")
    for i, (c, name) in enumerate((("gnd", "GND, ESP32 to breadboard"), ("servognd", "GND, breadboard to servo"),
                                   ("5v", "+5 V, ESP32 to breadboard"),
                                   ("servo5v", "+5 V, breadboard to servo"), ("shoulder", "shoulder (GPIO 0)"),
                                   ("elbow", "elbow (GPIO 1)"), ("pen", "pen (GPIO 3)"))):
        y = ly + 4.5 + i * 4.2
        dr.wire([(lx, y), (lx + 8, y)], COLOURS[c], 0.9)
        dr.text(lx + 10, y, name, 1.8, anchor="lm")
    dr.save(path)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    flash(os.path.join(here, "flash.png"))
    wiring(os.path.join(here, "wiring.png"))
