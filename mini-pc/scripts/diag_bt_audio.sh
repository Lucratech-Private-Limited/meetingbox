#!/bin/sh
# diag_bt_audio.sh — diagnose Bluetooth audio routing on the appliance.
#
# Run from the host (NOT inside the container):
#
#   sudo sh mini-pc/scripts/diag_bt_audio.sh
#
# What it shows:
#   1) Host: BlueZ paired/connected BT devices
#   2) Host: PulseAudio/PipeWire sources + sinks visible on the host
#   3) Container: pactl sources/sinks visible from inside meetingbox-appliance-ui
#   4) Container: which ALSA PCMs (pulse / pipewire / default) actually exist
#   5) Container: audio-related lines from the latest device-ui logs
#
# Paste the full output back so we can pinpoint why the BT mic+speaker is
# not being picked over the built-in ones.

set -u
CONTAINER="${MEETINGBOX_CONTAINER:-meetingbox-appliance-ui}"

hr() {
    printf '\n========== %s ==========\n' "$1"
}

run() {
    printf '\n$ %s\n' "$*"
    sh -c "$*" 2>&1 || true
}

run_in_container() {
    printf '\n[container] $ %s\n' "$*"
    docker exec "$CONTAINER" sh -lc "$*" 2>&1 || true
}

hr "1. HOST BlueZ paired/connected devices"
run "bluetoothctl show | grep -E 'Powered:|Discoverable:|Pairable:'"
run "bluetoothctl devices Paired"
run "bluetoothctl devices Connected"

hr "2. HOST PulseAudio / PipeWire (user session)"
run "ls -la /run/user/1000/pulse 2>&1 | head"
run "ls -la /run/user/1000/pipewire-0 2>&1 | head"
run "XDG_RUNTIME_DIR=/run/user/1000 pactl info | head -20"
run "XDG_RUNTIME_DIR=/run/user/1000 pactl list sources short"
run "XDG_RUNTIME_DIR=/run/user/1000 pactl list sinks short"
run "XDG_RUNTIME_DIR=/run/user/1000 pactl get-default-source"
run "XDG_RUNTIME_DIR=/run/user/1000 pactl get-default-sink"

hr "3. CONTAINER pactl visibility (this is what the app sees)"
run_in_container "id"
run_in_container "env | grep -E 'PULSE|XDG|PIPEWIRE' | sort"
run_in_container "ls -la /run/user/1000/pulse 2>&1 | head"
run_in_container "ls -la /run/user/1000/pipewire-0 2>&1 | head"
run_in_container "pactl info 2>&1 | head -20"
run_in_container "pactl list sources short 2>&1"
run_in_container "pactl list sinks short 2>&1"
run_in_container "pactl get-default-source 2>&1"
run_in_container "pactl get-default-sink 2>&1"

hr "4. CONTAINER ALSA PCM availability"
run_in_container "arecord -L 2>&1 | head -50"
run_in_container "arecord -l 2>&1"
run_in_container "aplay -l 2>&1"
run_in_container "dpkg -l | grep -E 'libasound2-plugins|pipewire-alsa|pulseaudio' 2>&1"

hr "5. CONTAINER device-ui recent audio logs"
run "docker logs --tail 400 '$CONTAINER' 2>&1 | grep -iE 'audio|mic|pulse|pipewire|bluez|bluetooth|arecord|aplay|sounddevice|portaudio' | tail -80"

hr "DONE — please paste the entire output above back to the assistant."
