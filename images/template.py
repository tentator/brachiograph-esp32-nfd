# Draws the templates for the 3D-printed robot (REV 18 layout), two A4 sheets in landscape that
# both hang on the robot's two pins (the A4 filing holes, punched with an office 2-hole punch):
#   template-a4-front.pdf   in front of the robot, the robot at its top edge: drawing, the
#                           "Draw 1 cm Grid" check, and the shoulder lines from -90 to +90 deg
#   template-a4-back.pdf    behind the robot: printed like the front (holes at the top edge, same
#                           punch), then turned 180 deg and put on the same pins; shows the shoulder
#                           lines behind the robot (-90 to -180 and +90 to +180 deg). Its text is
#                           printed upside down, so it reads the right way once turned.
# The shoulder axis is on the line through the filing holes (80 mm apart), in the middle, 12 mm
# from the sheet's edge (from 3d-prints/base.stl and a4-bracket.stl). Everything is in mm at
# 100 % scale.
#
# Run: python3 images/template.py [--shift-y MM] [--out DIR]   (needs: pip install reportlab)
#   --shift-y MM  moves everything printed MM towards the holes' edge, for a printer that shifts
#                 the print (or a punch whose holes aren't 12 mm from the edge): if the punched holes
#                 sit 3 mm nearer the edge than the printed circles, use --shift-y 3
import argparse, json, math, os
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color, black, white

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 297.0, 210.0              # A4 landscape
HOLE_EDGE = 12.0                 # filing holes: 12 mm from the edge (ISO 838), 80 mm apart, 6 mm across
HOLE_DX, HOLE_D = 40.0, 6.0
L1 = L2 = 80.0                   # arm lengths the templates are made for
# Footprint of the base around the shoulder axis (base.stl), in robot mm: x to the robot's left,
# y away from the robot
BASE = [(-42.8, 11), (42.8, 11), (42.8, -11), (25.3, -31), (25.3, -69.7), (-25.3, -69.7),
        (-25.3, -31), (-42.8, -11)]
MARGIN = 5.0                     # printers can't print closer to the edge
LEGEND_MARGIN = 10.0             # many printers lose more than that at the bottom
RED = Color(0.80, 0.10, 0.15); BLUE = Color(0.05, 0.25, 0.60); GREEN = Color(0.2, 0.6, 0.3)
GRID = Color(0.75, 0.75, 0.75); GRID5 = Color(0.45, 0.45, 0.45); GREY = Color(0.92, 0.92, 0.92)

def defaults():
    try:
        with open(os.path.join(HERE, "..", "board_default_calibration.json")) as f:
            return json.load(f)
    except OSError:
        return {}

def make(out, back, title, notes, shift_y):
    # Everything is drawn as the sheet lies on the table, seen from the paper side: the front
    # sheet with the robot at its top edge, the back sheet (turned) with the robot at its bottom
    # edge. The back sheet is then rotated 180 deg on the page, so it prints like the front.
    c = canvas.Canvas(out, pagesize=(W * mm, H * mm))
    c.setTitle(title)
    c.translate(0, shift_y * mm)          # printed frame: towards the holes' (top) edge
    if back:
        c.translate(W * mm, H * mm); c.rotate(180)
    SX, SY = W / 2, (H - HOLE_EDGE if back else HOLE_EDGE)   # shoulder axis on the sheet

    def page(px, py):           # sheet mm (from the top left, as it lies) -> PDF points
        return px * mm, (H - py) * mm
    def robot(rx, ry):          # robot mm (X to the robot's right, Y away from it) -> sheet mm
        return SX - rx, SY + ry  # the robot faces down the sheet: its right is the sheet's left
    base = [(SX + bx, SY + by) for bx, by in BASE]
    def in_base(px, py):        # point in the base's footprint (ray casting)
        inside = False
        for (x0, y0), (x1, y1) in zip(base, base[1:] + base[:1]):
            if (y0 > py) != (y1 > py) and px < x0 + (py - y0) * (x1 - x0) / (y1 - y0):
                inside = not inside
        return inside
    def printable(px, py):
        return MARGIN <= px <= W - MARGIN and MARGIN <= py <= H - MARGIN
    def ray(px, py, dx, dy, step=0.25):   # from (px, py) along (dx, dy) to the printable border
        t = 0.0
        while printable(px + dx * (t + step), py + dy * (t + step)):
            t += step
        return px + dx * t, py + dy * t
    def line(p0, p1, col, width):
        c.setStrokeColor(col); c.setLineWidth(width)
        c.line(*page(*p0), *page(*p1))
    def clipped_line(p0, p1, col, width, n=600):   # only where it isn't under the base
        run = []
        for i in range(n + 2):
            p = (p0[0] + (p1[0] - p0[0]) * i / n, p0[1] + (p1[1] - p0[1]) * i / n) if i <= n else None
            if p is not None and printable(*p) and not in_base(*p):
                run.append(p)
            else:
                if len(run) > 1:
                    line(run[0], run[-1], col, width)
                run = []

    # 1 cm grid in robot coordinates, labelled every 5 cm
    c.setFont("Helvetica", 6.5); c.setFillColor(GRID5)
    for rx in range(-15, 16):
        px = robot(rx * 10, 0)[0]
        if MARGIN <= px <= W - MARGIN:
            clipped_line((px, MARGIN), (px, H - MARGIN), GRID5 if rx % 5 == 0 else GRID, 0.5 if rx % 5 == 0 else 0.25)
            if rx % 5 == 0:
                c.drawCentredString(*page(px, (MARGIN + 4) if back else (H - LEGEND_MARGIN - 1)), "%d" % rx)
    for ry in range(-21, 22):
        py = robot(0, ry * 10)[1]
        if MARGIN <= py <= H - MARGIN:
            clipped_line((MARGIN, py), (W - MARGIN, py), GRID5 if ry % 5 == 0 else GRID, 0.5 if ry % 5 == 0 else 0.25)
            if ry % 5 == 0 and ry != 0:
                c.drawString(*page(W - MARGIN - 6, py - 1), "%d" % ry)

    # Default drawing area (board_default_calibration.json), on the front sheet
    d = defaults()
    if not back and all(k in d for k in ("bed_minx", "bed_maxx", "bed_miny", "bed_maxy")):
        x0, y0 = robot(d["bed_maxx"], d["bed_miny"]); x1, y1 = robot(d["bed_minx"], d["bed_maxy"])
        c.setStrokeColor(GREEN); c.setLineWidth(0.8); c.setDash(3, 2)
        c.rect(x0 * mm, (H - y1) * mm, (x1 - x0) * mm, (y1 - y0) * mm, stroke=1, fill=0)
        c.setDash(); c.setFillColor(GREEN); c.setFont("Helvetica", 7)
        c.drawString(*page(x0 + 1.5, y1 - 1.5), "default drawing area")

    # Red: shoulder angles, from the shoulder axis (on the back sheet those behind the robot)
    c.setFont("Helvetica-Bold", 8)
    for a in (range(-180, 180, 15) if back else range(-90, 91, 15)):
        dx, dy = -math.sin(math.radians(a)), math.cos(math.radians(a))
        if back and dy > 0.01 or not back and dy < -0.01:
            continue                      # on the other sheet
        end = ray(SX, SY, dx, dy)
        clipped_line((SX, SY), end, RED, 0.6)
        r = math.hypot(end[0] - SX, end[1] - SY) - (11 if abs(dx) > 0.85 else 8)
        if r > 30:
            c.setFillColor(RED)
            c.drawCentredString(*page(SX + dx * r + (3 if dx > 0 else -3), SY + dy * r + 1), "%d°" % (a if a != -180 else 180))

    # Blue: elbow angles relative to the upper arm, with the shoulder at 0 deg (front sheet)
    if not back:
        EX, EY = robot(0, L1)
        for e in range(15, 166, 15):
            dx, dy = -math.sin(math.radians(e)), math.cos(math.radians(e))
            end = ray(EX, EY, dx, dy)
            clipped_line((EX, EY), end, BLUE, 0.6)
            r = min(math.hypot(end[0] - EX, end[1] - EY), L2) - 4
            c.setFillColor(BLUE); c.drawCentredString(*page(EX + dx * r - 3, EY + dy * r - 2), "%d°" % e)
        c.setFillColor(BLUE); c.circle(*page(EX, EY), 0.8 * mm, stroke=0, fill=1)
        c.setFont("Helvetica", 6.5); c.drawString(*page(EX + 2, EY + 4), "elbow, shoulder at 0°")

    # Base (what's on this sheet), holes, shoulder axis, centre mark for the hole punch
    p = c.beginPath(); p.moveTo(*page(*base[0]))
    for pt in base[1:]:
        p.lineTo(*page(*pt))
    p.close()
    c.setFillColor(GREY); c.setStrokeColor(GRID5); c.setLineWidth(0.4); c.drawPath(p, stroke=1, fill=1)
    c.setFillColor(GRID5); c.setFont("Helvetica", 7)
    c.drawCentredString(*page(SX, SY + (-30 if back else 8.5)), "robot base")
    for hx in (-HOLE_DX, HOLE_DX):
        c.setStrokeColor(black); c.setLineWidth(0.6); c.setFillColor(white)
        c.circle(*page(SX + hx, SY), HOLE_D / 2 * mm, stroke=1, fill=1)
        line((SX + hx - 4.5, SY), (SX + hx + 4.5, SY), black, 0.3)
        line((SX + hx, SY - 4.5), (SX + hx, SY + 4.5), black, 0.3)
        c.setFillColor(black); c.setFont("Helvetica", 6.5)
        c.drawCentredString(*page(SX + hx, SY + (7.5 if back else -5.5)), "hole")
    c.setFillColor(RED); c.circle(*page(SX, SY), 1.0 * mm, stroke=0, fill=1)
    c.setFont("Helvetica", 6.5); c.drawString(*page(SX + 2, SY + 3.5), "shoulder axis")
    edge = H - MARGIN + 1 if back else MARGIN - 1
    line((SX, edge), (SX, edge + (-3.5 if back else 3.5)), black, 0.5)

    # Legend with a 10 cm bar to check the print scale, on a white box, away from the robot
    bw, bh = 150, 6 + 3.4 * (len(notes) + 1) + 6
    lx = MARGIN + 3
    ly = (LEGEND_MARGIN if back else H - LEGEND_MARGIN - bh)
    c.setFillColor(white); c.setStrokeColor(GRID5); c.setLineWidth(0.3)
    c.rect((lx - 2) * mm, (H - ly - bh) * mm, bw * mm, bh * mm, stroke=1, fill=1)
    c.setFillColor(black); c.setFont("Helvetica-Bold", 9)
    c.drawString(*page(lx, ly + 4), title)
    c.setFont("Helvetica", 7)
    for i, txt in enumerate(notes):
        c.drawString(*page(lx, ly + 8 + i * 3.4), txt)
    by = ly + bh - 2.5
    line((lx, by), (lx + 100, by), black, 1.0)
    for t in range(0, 101, 10):
        line((lx + t, by - (2 if t % 50 else 3)), (lx + t, by), black, 0.5)
    c.setFont("Helvetica", 6.5); c.setFillColor(black); c.drawString(*page(lx + 102, by + 0.5), "10 cm")
    c.showPage(); c.save()
    print("written", out)

ap = argparse.ArgumentParser(description="Draws the robot's A4 templates")
ap.add_argument("--shift-y", type=float, default=0.0, help="mm towards the holes' edge (printer correction)")
ap.add_argument("--out", default=HERE, help="folder for the PDFs")
args = ap.parse_args()
shift = " (printed %+g mm towards the holes)" % args.shift_y if args.shift_y else ""
COMMON = ["Print at 100 % (actual size): the bar below must measure exactly 10 cm.",
          "Punch the two holes with an office 2-hole punch; the printed circles should sit on them (if they",
          "don't, see --shift-y in images/template.py). Grid: 1 cm in robot coordinates (X to the robot's right,",
          "Y away from it). Red: shoulder angles from the shoulder axis. Made for 80 mm arms (L1 = L2 = 80 mm)."]
make(os.path.join(args.out, "template-a4-front.pdf"), False, "ESP32 BrachioGraph template - front" + shift,
     ["Lies in front of the robot, on its two pins. For drawing, Draw 1 cm Grid and the precise calibration.",
      "Blue: elbow angles with the shoulder at 0°, from the elbow (0, 8 cm)."] + COMMON, args.shift_y)
make(os.path.join(args.out, "template-a4-back.pdf"), True, "ESP32 BrachioGraph template - back" + shift,
     ["Printed like the front, then turned round and put on the same two pins, behind the robot:",
      "the shoulder angles behind the robot, for the precise calibration (-105°, -120° ...)."] + COMMON, args.shift_y)
