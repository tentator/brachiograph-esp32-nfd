# Starts the plotter. If it fails (a bad update, for example), the rescue server keeps
# Wi-Fi and updates over Wi-Fi running, so a fixed plotter.py can be uploaded without USB.
from machine import Pin
import rescue

# Servo signals low (no pulses, the servos keep still) while the plotter compiles, about
# a second: left floating, the signal pins pick up noise and the servos jerk
for pin in rescue.servo_pins():
    Pin(pin, Pin.OUT, value=0)

try:
    import plotter
except Exception as e:
    import io
    import sys
    trace = io.StringIO()
    sys.print_exception(e, trace)
    print(trace.getvalue())
    rescue.serve(trace.getvalue())
