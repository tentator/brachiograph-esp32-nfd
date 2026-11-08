# Parts

Everything you need to build one drawing robot. The 3D-printed parts are in
[`3d-prints/`](3d-prints/); they are preliminary and may still change.

## 3D-printed parts

| Part | Qty | File |
|---|---|---|
| Base plate, holds the breadboard and the shoulder servo | 1 | [`3d-prints/base.stl`](3d-prints/base.stl) |
| Upper arm, 80 mm, holds the elbow servo | 1 | [`3d-prints/upper-arm.stl`](3d-prints/upper-arm.stl) |
| Lower arm, 80 mm, holds the pen-lift servo and the pen | 1 | [`3d-prints/lower-arm.stl`](3d-prints/lower-arm.stl) |
| A4 bracket, centres an A4 sheet by its punched filing holes | 1 | [`3d-prints/a4-bracket.stl`](3d-prints/a4-bracket.stl) |

## Mechanics

| Part | Qty | Notes |
|---|---|---|
| Servo, 9 g (e.g. MS18 or SG90) | 3 | shoulder, elbow and pen lift |
| Servo horn | 3 | one per servo, comes with the servo |
| Horn screw | 3 | one per servo, comes with the servo |
| Rubber band | 1 | around the lower arm, holds the pen |
| Pen | 1 | a fine felt-tip pen draws the cleanest lines |
| Small cable ties | 6 | hold the servo cables on the arms |

## Electronics

| Part | Qty | Notes |
|---|---|---|
| ESP32-C3 board with 0.42" OLED display (72 x 40 pixels) | 1 | USB-C, two rows of 8 pins |
| Mini breadboard, 170 holes, 34 x 46 mm | 1 | |
| Electrolytic capacitor, 2200 µF, at least 10 V | 1 | |
| Jumper wire, male to male, black | 4 | GND |
| Jumper wire, male to male, red | 4 | +5 V |
| Jumper wire, male to male, green | 1 | shoulder signal |
| Jumper wire, male to male, yellow | 1 | elbow signal |
| Jumper wire, male to male, white | 1 | pen signal |

## Tools

| Tool | Qty | Notes |
|---|---|---|
| Linux computer with Wi-Fi | 1 | flashing, and the browser to draw from |
| USB-C cable that carries data | 1 | to flash the ESP32 from the computer |
| USB-C power supply, 5 V, at least 2 A | 1 | powers the robot after flashing |
| Small Phillips screwdriver | 1 | horn screws |
| Side cutter or scissors | 1 | trim the cable ties |
