"""Phase 3 evals: settings gating + Wi-Fi/Bluetooth read-only + LAN IP.

Covers:
* netsh wlan interface parser (uses a captured sample, not live hardware)
* net_status LAN IP returns a real address on this host
* wifi/bluetooth modules become read-only no-ops on desktop
* settings module exposes the desktop gating flag
"""

from __future__ import annotations

import platform_compat
import net_status


# --- Wi-Fi parser (deterministic, sample input) --------------------------

_NETSH_SAMPLE = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    GUID                   : 1234abcd
    Physical address       : aa:bb:cc:dd:ee:ff
    State                  : connected
    SSID                   : MyHomeNetwork
    BSSID                  : 11:22:33:44:55:66
    Network type           : Infrastructure
    Radio type             : 802.11ac
    Authentication         : WPA2-Personal
    Cipher                 : CCMP
    Connection mode        : Auto Connect
    Channel                : 36
    Receive rate (Mbps)    : 866.7
    Transmit rate (Mbps)   : 866.7
    Signal                 : 92%
    Profile                : MyHomeNetwork
"""


def test_netsh_wlan_parser_extracts_ssid_and_signal():
    info = net_status._parse_netsh_wlan_interfaces(_NETSH_SAMPLE)
    assert info is not None
    assert info["ssid"] == "MyHomeNetwork"
    assert info["signal_strength"] == 92
    assert info["connected"] is True


def test_netsh_wlan_parser_ignores_bssid_as_ssid():
    # Ensure BSSID line never overwrites SSID.
    info = net_status._parse_netsh_wlan_interfaces(_NETSH_SAMPLE)
    assert info["ssid"] != "11:22:33:44:55:66"


def test_netsh_wlan_parser_disconnected_returns_none():
    text = "    State : disconnected\n"
    assert net_status._parse_netsh_wlan_interfaces(text) is None


# --- LAN IP (live, this host) --------------------------------------------

def test_primary_ipv4_returns_lan_address():
    ip = net_status.primary_ipv4()
    # On a networked host this must be a non-loopback IPv4.
    assert ip is not None
    assert not ip.startswith("127.")
    parts = ip.split(".")
    assert len(parts) == 4 and all(p.isdigit() for p in parts)


# --- Wi-Fi / Bluetooth modules become read-only on desktop ---------------

def test_wifi_mutations_are_noops_on_desktop():
    import wifi_nmcli_local as w

    if platform_compat.IS_LINUX:
        import pytest
        pytest.skip("Linux appliance keeps nmcli mutations")
    assert w._DESKTOP is True
    res = w.set_wifi_radio(True)
    assert res["ok"] is False
    res2 = w.connect_wifi_network("SomeSSID", "pw")
    assert res2["status"] == "failed"


def test_bluetooth_mutations_are_noops_on_desktop():
    import bluetooth_local as b

    if platform_compat.IS_LINUX:
        import pytest
        pytest.skip("Linux appliance keeps bluetoothctl mutations")
    assert b._DESKTOP is True
    res = b.set_power(True)
    assert res["ok"] is False


def test_scan_wifi_returns_list_without_raising_on_desktop():
    import wifi_nmcli_local as w

    if platform_compat.IS_LINUX:
        import pytest
        pytest.skip("Linux scan path needs nmcli")
    # Must return a list (possibly empty) and never raise.
    nets = w.scan_wifi_networks(rescan=False)
    assert isinstance(nets, list)


# --- Settings gating flag -------------------------------------------------

def test_settings_appliance_flag_matches_platform():
    import importlib

    settings = importlib.import_module("screens.settings")
    assert settings._SHOW_APPLIANCE_ROWS == platform_compat.IS_LINUX
    assert settings._DESKTOP_BUILD == (not platform_compat.IS_LINUX)
