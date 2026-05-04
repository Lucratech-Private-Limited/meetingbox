"""
Primary IPv4 for the home-screen footer: the **host** LAN address people use
to reach the panel (e.g. ``192.168.1.14``), not the container bridge (``172.18``).

**Dynamic:** on each call we re-resolve. The appliance compose uses ``pid: host``;
we use ``nsenter -t 1 -n`` to run ``ip`` in the **host** network namespace so
DHCP / new networks (plugging into another router) are reflected without static config.

Optional overrides: :envvar:`MEETINGBOX_LAN_IP` or a one-line
``MEETINGBOX_LAN_IP_FILE`` (e.g. ``/data/config/lan_ip``) — only if you must
override auto-detection.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import shutil
import socket
import subprocess
from typing import List, Tuple

logger = logging.getLogger(__name__)

_FALLBACK = "—"

_INET = re.compile(r"inet (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/", re.MULTILINE)

# Interfaces we never use for "SSH / browser on the room LAN" when a better
# address exists. VPN/overlay (172.16/…) often sort before 192.168 in naive picks.
_KNOWN_NOISE_IFACES = (
    "lo",
    "docker0",
    "br-",
    "veth",
    "virbr",
    "lxc",
    "cni",
    "tun",
    "tap",
    "vnet",
    "waydroid",
    "tailscale",
    "wg",
    "zt",
    "zerotier",
    "gretap",
    "isatap",
    "lxdbr",
    "lxcbr",
    "cni-",
    "flannel",
    "cilium",
    "kubetunnel",
    "nodelocal",
    "hologram",
    "vboxnet",
    "virbr0",
    "vboxnet0",
    "tunl",
    "nflog",
    "xfrm",
)

_BR_LINE = re.compile(
    r"^(\S+)\s+(UP|DOWN|UNKNOWN)\s+((?:\d{1,3}\.){3}\d{1,3}/\d+)",
    re.MULTILINE,
)


def _is_rfc1918(ipv4: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(ipv4)
    except (ipaddress.AddressValueError, ValueError):
        return False
    a = int(ip) >> 24
    if a == 10:
        return True
    if 172 <= a <= 31:
        return True
    if a == 192 and ((int(ip) >> 8) & 0xFF) == 168:
        return True
    return False


def _lan_preference_score(ipv4: str) -> int | None:
    """Lower is better. None = ignore.

    172.16.0.0/16 (second octet == 16) is often a VPN/overlay, not the office LAN
    (192.168.x); score it so real LAN always wins in multi-homed picks.
    """
    try:
        ip = ipaddress.IPv4Address(ipv4)
    except (ipaddress.AddressValueError, ValueError):
        return None
    if ip.is_loopback or ip.is_link_local:
        return None
    if not _is_rfc1918(str(ip)):
        return 500
    a = (int(ip) >> 24) & 0xFF
    b = (int(ip) >> 16) & 0xFF
    if a == 192 and b == 168:
        return 0
    if a == 10:
        return 10
    if a == 172 and 16 <= b <= 31:
        if b == 17:
            return 200
        if b == 18:
            return 150
        if b == 16:
            # 172.16.0.0/12 (second octet 16) — very often Tailscale/WireGuard/ZT, not
            # the Ethernet/WiFi IP others use to SSH in on the same switch.
            return 120
        return 20 + b
    return 40


def _iface_is_physical_or_wifi(name: str) -> bool:
    n = (name or "").lower()
    if n == "lo" or n.startswith("docker") or n.startswith("br-"):
        return False
    if n.startswith("veth") or n.startswith("virbr") or n.startswith("cni"):
        return False
    return n.startswith("en") or n.startswith("eth") or n.startswith("wl") or n.startswith("usb")


def _iface_skip(name: str) -> bool:
    n = (name or "").lower()
    for p in _KNOWN_NOISE_IFACES:
        if p.endswith("-"):
            if n.startswith(p):
                return True
        elif n == p or n.startswith(p):
            return True
    if re.match(r"^zt", n):  # ZeroTier: ztly…
        return True
    return False


def _looks_like_classic_docker_ipv4(ipv4: str) -> bool:
    """True for default ``docker0`` (172.17/16) and typical compose bridges (172.18/16).

    Those are the host/container side of Docker NAT, not the LAN IP peers use
    to reach the mini PC on Ethernet/Wi‑Fi.
    """
    try:
        ip = ipaddress.IPv4Address(ipv4)
    except (ipaddress.AddressValueError, ValueError):
        return False
    if int(ip) >> 24 != 172:
        return False
    b = (int(ip) >> 16) & 0xFF
    return b in (17, 18)


def _parse_ip_br_text(text: str) -> List[Tuple[str, str, str]]:
    """Return list of (ifname, state, ip_without_cidr) from ``ip -4 -br addr`` output."""
    rows: list[tuple[str, str, str]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _BR_LINE.match(line)
        if m:
            ifname, state, cidr = m.group(1), m.group(2), m.group(3)
            ip_s = cidr.split("/")[0]
        else:
            parts = line.split()
            if len(parts) < 3:
                continue
            ifname, state = parts[0], parts[1]
            ip_s = None
            for p in parts[2:]:
                if "/" in p and p[0].isdigit():
                    ip_s = p.split("/")[0]
                    break
            if not ip_s:
                continue
        if ip_s and not ip_s.startswith("127."):
            rows.append((ifname, state, ip_s))
    return rows


def _best_ip_from_rows(rows: List[Tuple[str, str, str]]) -> str | None:
    """Pick best (iface, state, ip) using scores + interface hints."""
    best: tuple[int, int, int, str] | None = None
    for ifname, state, ip in rows:
        if _iface_skip(ifname):
            continue
        sc = _lan_preference_score(ip)
        if sc is None:
            continue
        up_bonus = 0 if (state or "").upper() == "UP" else 2
        phys = 0 if _iface_is_physical_or_wifi(ifname) else 1
        key = (sc + up_bonus, phys, len(ifname), ip)
        if best is None or key < best:
            best = (key[0], key[1], key[2], ip)
    if best is not None:
        return best[3]
    return None


def _best_on_physical_lan_first(rows: List[Tuple[str, str, str]]) -> str | None:
    """
    IPv4 on Ethernet/Wi-Fi (en|eth|wl|*) first — not VPN/overlay 172.16 on tun/wg/zt*.

    ``scope global`` can omit a valid ``en*`` line; a broad row list is used before the
    combined heuristic on all interfaces.
    """
    for require_up in (True, False):
        for ifname, state, ip in rows:
            if ifname != "enp1s0" or _iface_skip(ifname):
                continue
            stu = (state or "").upper()
            if require_up and stu not in ("UP", "UNKNOWN"):
                continue
            if _lan_preference_score(ip) is not None:
                return ip
        pruned: list[tuple[str, str, str]] = []
        for ifname, state, ip in rows:
            if not _iface_is_physical_or_wifi(ifname) or _iface_skip(ifname):
                continue
            stu = (state or "").upper()
            if require_up and stu not in ("UP", "UNKNOWN"):
                continue
            pruned.append((ifname, state, ip))
        if pruned:
            b = _best_ip_from_rows(pruned)
            if b:
                return b
    return None


def _host_lan_src_via_nsenter_route() -> str | None:
    """IPv4 ``src`` from ``ip route get 8.8.8.8`` in the host netns (real outbound LAN)."""
    ns = shutil.which("nsenter")
    ipbin = shutil.which("ip")
    if not ns or not ipbin:
        return None
    try:
        p = subprocess.run(
            [ns, "-t", "1", "-n", "--", ipbin, "route", "get", "8.8.8.8"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if p.returncode != 0:
            logger.debug(
                "nsenter ip route get rc=%s stderr=%s", p.returncode, p.stderr
            )
            return None
        m = re.search(
            r"\bsrc\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b",
            p.stdout or "",
        )
        if not m:
            return None
        cand = m.group(1)
        if cand.startswith("127."):
            return None
        if _lan_preference_score(cand) is None:
            return None
        return cand
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        logger.debug("nsenter ip route get failed: %s", e)
        return None


def _nsenter_ip_addr_on_dev(dev: str) -> str | None:
    """First global IPv4 on *dev* in the host netns (``ip -4 addr show dev enp1s0``)."""
    ns = shutil.which("nsenter")
    ipbin = shutil.which("ip")
    if not ns or not ipbin or not dev:
        return None
    try:
        p = subprocess.run(
            [ns, "-t", "1", "-n", "--", ipbin, "-4", "addr", "show", "dev", dev],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if p.returncode != 0:
            return None
        for m in _INET.finditer(p.stdout or ""):
            addr = m.group(1)
            if addr.startswith("127."):
                continue
            if _lan_preference_score(addr) is not None:
                return addr
        return None
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        logger.debug("nsenter ip addr show dev %s failed: %s", dev, e)
        return None


def _host_lan_from_nsenter() -> str | None:
    """
    True host addresses when the app runs in Docker with ``pid: host`` and
    ``util-linux`` (``nsenter``) available. Uses PID 1 (init) in the **host** net namespace.
    """
    ns = shutil.which("nsenter")
    if not ns:
        return None
    ipbin = shutil.which("ip")
    if not ipbin:
        return None

    # Outbound route source — matches ``192.168.x`` on enp1s0 when the host has Internet.
    route_ip = _host_lan_src_via_nsenter_route()
    if route_ip and not _looks_like_classic_docker_ipv4(route_ip):
        return route_ip

    # Explicit NICs common on mini PCs (avoids ``ip -br`` parse edge cases).
    for dev in ("enp1s0", "eno1", "enp0s31f6", "eth0"):
        got = _nsenter_ip_addr_on_dev(dev)
        if got and not _looks_like_classic_docker_ipv4(got):
            return got

    for args in (
        (ns, "-t", "1", "-n", "--", ipbin, "-4", "-br", "addr", "show", "up"),
        (
            ns,
            "-t",
            "1",
            "-n",
            "--",
            ipbin,
            "-4",
            "-br",
            "addr",
            "show",
            "up",
            "scope",
            "global",
        ),
    ):
        try:
            p = subprocess.run(
                list(args),
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            if p.returncode != 0:
                logger.debug("nsenter ip rc=%s stderr=%s", p.returncode, p.stderr)
                continue
            rows = _parse_ip_br_text(p.stdout or "")
            # 1) Real LAN (enp*/wlp*/usb*) with 192.168.x / 10.x before any VPN 172.16 on tun0.
            phys = _best_on_physical_lan_first(rows)
            if phys and not _looks_like_classic_docker_ipv4(phys):
                return phys
            # 2) All non-loopback, skipping docker/veth/tun/…
            best = _best_ip_from_rows(rows)
            if best and not _looks_like_classic_docker_ipv4(best):
                return best
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            logger.debug("nsenter ip failed: %s", e)
    return None


def _candidates() -> List[str]:
    out: list[str] = []

    def _add(addr: str) -> None:
        addr = (addr or "").split("%")[0].strip()
        if addr and addr not in out:
            out.append(addr)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        try:
            s.connect(("8.8.8.8", 80))
            _add(s.getsockname()[0])
        finally:
            s.close()
    except OSError as e:
        logger.debug("UDP local-IP probe failed: %s", e)

    if shutil.which("hostname"):
        try:
            p = subprocess.run(
                ["hostname", "-I"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            for tok in (p.stdout or "").split():
                _add(tok)
        except (subprocess.SubprocessError, OSError, ValueError) as e:
            logger.debug("hostname -I failed: %s", e)

    if shutil.which("ip"):
        try:
            p = subprocess.run(
                ["ip", "-4", "addr", "show", "up", "scope", "global"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            for a in _INET.findall(p.stdout or ""):
                _add(a)
        except (subprocess.SubprocessError, OSError, ValueError) as e:
            logger.debug("ip addr failed: %s", e)

    return out


def _read_env_lan() -> str | None:
    for key in ("MEETINGBOX_LAN_IP", "APPLIANCE_LAN_IP"):
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        try:
            ipaddress.IPv4Address(raw.split("%")[0].strip())
        except (ipaddress.AddressValueError, ValueError):
            logger.warning("%s is not a valid IPv4: %r", key, raw)
            continue
        return raw.split("%")[0].strip()
    return None


def _read_lan_file() -> str | None:
    path = (os.getenv("MEETINGBOX_LAN_IP_FILE") or "/data/config/lan_ip").strip()
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            line = f.readline()
    except OSError as e:
        logger.debug("LAN IP file %s not read: %s", path, e)
        return None
    raw = (line or "").split("#", 1)[0].strip()
    if not raw:
        return None
    try:
        ipaddress.IPv4Address(raw.split("%")[0].strip())
    except (ipaddress.AddressValueError, ValueError):
        logger.warning("Invalid IPv4 in %s: %r", path, raw)
        return None
    return raw.split("%")[0].strip()


def _first_ipv4_from_hostname_i_text(text: str) -> str | None:
    for tok in (text or "").split():
        raw = tok.split("%")[0].strip()
        if not raw or raw.startswith("127."):
            continue
        try:
            ipaddress.IPv4Address(raw)
        except (ipaddress.AddressValueError, ValueError):
            continue
        return raw
    return None


def _hostname_i_first_on_host_via_nsenter() -> str | None:
    """``hostname -I`` in the **host** netns (PID 1), not the container's."""
    ns = shutil.which("nsenter")
    hostbin = shutil.which("hostname")
    if not ns or not hostbin:
        return None
    try:
        p = subprocess.run(
            [ns, "-t", "1", "-n", "--", hostbin, "-I"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if p.returncode != 0:
            logger.debug(
                "nsenter hostname -I rc=%s stderr=%s", p.returncode, p.stderr
            )
            return None
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        logger.debug("nsenter hostname -I failed: %s", e)
        return None
    return _first_ipv4_from_hostname_i_text(p.stdout or "")


def get_hostname_i_first_ipv4() -> str:
    """First IPv4 from ``hostname -I``, matching what you see on the **host** shell.

    In Docker (bridge network), plain ``hostname -I`` is the container's addresses
    (e.g. ``172.18.x``) — not SSH-reachable from the LAN. When ``nsenter`` is
    available (appliance compose: ``pid: host``, privileged), we run
    ``hostname -I`` in the host network namespace first.
    """
    host_first = _hostname_i_first_on_host_via_nsenter()
    if host_first:
        return host_first

    if not shutil.which("hostname"):
        return _FALLBACK
    try:
        p = subprocess.run(
            ["hostname", "-I"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        logger.debug("hostname -I (first) failed: %s", e)
        return _FALLBACK
    first = _first_ipv4_from_hostname_i_text(p.stdout or "")
    return first if first else _FALLBACK


def get_primary_ipv4() -> str:
    """
    A usable LAN IPv4** for** “open this in a browser on the same network”.

    1) **Host** namespace (``nsenter``) — best on the real appliance; tracks DHCP and new networks.
    2) Optional :envvar:`MEETINGBOX_LAN_IP` / :envvar:`APPLIANCE_LAN_IP` (fixed override).
    3) Optional one-line :envvar:`MEETINGBOX_LAN_IP_FILE` (e.g. ``/data/config/lan_ip``).
    4) Heuristic in-container addresses (not ideal: often ``172.18``).
    """
    # 1) Dynamic host LAN (appliance Docker + pid:host + privileged)
    host_ip = _host_lan_from_nsenter()
    if host_ip:
        return host_ip

    env_ip = _read_env_lan()
    if env_ip:
        return env_ip
    file_ip = _read_lan_file()
    if file_ip:
        return file_ip

    best: tuple[int, str] | None = None
    for c in _candidates():
        if _looks_like_classic_docker_ipv4(c):
            continue
        sc = _lan_preference_score(c)
        if sc is None:
            continue
        if c.startswith("127."):
            continue
        if best is None or sc < best[0] or (sc == best[0] and c < best[1]):
            best = (sc, c)

    if best is not None:
        return best[1]
    return _FALLBACK
