#!/usr/bin/env bash
# ============================================================================
# MeetingBox WiFi Hotspot Manager
#
# Uses NetworkManager (nmcli) to create/destroy a WiFi access point.
# The hotspot runs a DHCP server automatically via NM's shared mode.
#
# Usage:
#   sudo bash hotspot.sh start [SSID_SUFFIX]   # Start hotspot
#   sudo bash hotspot.sh stop                    # Stop and restore WiFi
#   sudo bash hotspot.sh status                  # Show hotspot state
# ============================================================================

set -euo pipefail

CON_NAME="MeetingBox-Hotspot"
SSID_PREFIX="MeetingBox-"
HOTSPOT_IP="192.168.4.1"

# Auto-detect the WiFi interface if WIFI_IFACE is not set explicitly.
# Picks the first wireless interface reported by the kernel (wlan0, wlp2s0, etc.)
if [ -z "${WIFI_IFACE:-}" ]; then
    WIFI_IFACE=$(ls /sys/class/net/ | grep -E "^wl" | head -1)
    if [ -z "$WIFI_IFACE" ]; then
        echo "[Hotspot] ERROR: No WiFi interface found. Plug in a USB WiFi adapter first."
        exit 1
    fi
    echo "[Hotspot] Auto-detected WiFi interface: $WIFI_IFACE"
fi

get_suffix() {
    # Use last 4 chars of wlan0 MAC as suffix, or fallback
    local mac
    mac=$(cat "/sys/class/net/${WIFI_IFACE}/address" 2>/dev/null || echo "00:00:00:00:00:00")
    echo "${mac//:/}" | tail -c 5 | tr '[:lower:]' '[:upper:]'
}

cmd_start() {
    local suffix="${1:-$(get_suffix)}"
    local ssid="${SSID_PREFIX}${suffix}"

    echo "[Hotspot] Starting access point: ${ssid}"

    # Remove any previous hotspot connection profile
    nmcli connection delete "$CON_NAME" 2>/dev/null || true

    # Write DNS redirect config BEFORE starting the hotspot so dnsmasq
    # picks it up on first launch (avoids the down/up bounce)
    local dnsmasq_conf="/etc/NetworkManager/dnsmasq-shared.d/meetingbox.conf"
    mkdir -p "$(dirname "$dnsmasq_conf")"
    cat > "$dnsmasq_conf" <<EOF
address=/meetingbox.setup/${HOTSPOT_IP}
address=/meetingbox.local/${HOTSPOT_IP}
EOF

    # Create WiFi hotspot (no password — open network for easy onboarding)
    nmcli connection add \
        type wifi \
        ifname "$WIFI_IFACE" \
        con-name "$CON_NAME" \
        autoconnect no \
        ssid "$ssid" \
        -- \
        wifi.mode ap \
        wifi.band bg \
        wifi.channel 6 \
        ipv4.method shared \
        ipv4.addresses "${HOTSPOT_IP}/24" \
        ipv6.method disabled

    nmcli connection up "$CON_NAME"

    # Brief wait for AP to stabilise
    sleep 2

    echo "[Hotspot] AP active — SSID: ${ssid}, IP: ${HOTSPOT_IP}"
    echo "[Hotspot] meetingbox.setup → ${HOTSPOT_IP}"
}

cmd_stop() {
    echo "[Hotspot] Stopping access point..."
    nmcli connection down "$CON_NAME" 2>/dev/null || true
    nmcli connection delete "$CON_NAME" 2>/dev/null || true

    # Clean up DNS redirect
    rm -f /etc/NetworkManager/dnsmasq-shared.d/meetingbox.conf

    echo "[Hotspot] AP stopped. WiFi will reconnect to previous network."
}

cmd_status() {
    if nmcli -t connection show --active | grep -q "$CON_NAME"; then
        local ssid
        ssid=$(nmcli -t -f 802-11-wireless.ssid connection show "$CON_NAME" 2>/dev/null | cut -d: -f2)
        echo "active|${ssid}|${HOTSPOT_IP}"
    else
        echo "inactive"
    fi
}

case "${1:-help}" in
    start)  cmd_start "${2:-}" ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    *)
        echo "Usage: sudo bash $0 {start [SUFFIX]|stop|status}"
        exit 1
        ;;
esac
