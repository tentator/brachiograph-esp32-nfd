#!/bin/bash
# Lets whoever is logged in use the ESP32's USB port without sudo or the group dialout: a udev
# rule with systemd's uaccess tag for the USB chips flash.sh looks for. Run it once on the
# computer itself (not in a toolbox or container), then plug the ESP32 in again.
set -euo pipefail
[[ ! -e /run/.containerenv ]] || { echo "deploy_udev.sh: run it on the computer itself, not in a container" >&2; exit 1; }
sudo tee /etc/udev/rules.d/70-esp32-plotter.rules >/dev/null <<'RULES'
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", TAG+="uaccess"
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", TAG+="uaccess"
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", TAG+="uaccess"
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="55d4", TAG+="uaccess"
RULES
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=tty
echo "Done: plug the ESP32 in again, then ./flash.sh"
