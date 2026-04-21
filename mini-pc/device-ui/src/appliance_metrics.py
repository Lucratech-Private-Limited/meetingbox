"""
Collect CPU / RAM / disk on the appliance for the web dashboard System page.

Uses psutil when available; falls back to /proc + shutil.disk_usage.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any, Dict

logger = logging.getLogger(__name__)

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None


def _disk_path() -> str:
    if os.path.isdir("/data"):
        return "/data"
    return "/"


def collect_appliance_metrics() -> Dict[str, Any]:
    path = _disk_path()
    if psutil is not None:
        try:
            cpu = float(psutil.cpu_percent(interval=0.25))
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage(path)
            return {
                "cpu_percent": round(cpu, 1),
                "memory_percent": round(float(mem.percent), 1),
                "memory_used_gb": round(mem.used / (1024**3), 2),
                "memory_total_gb": round(mem.total / (1024**3), 2),
                "disk_percent": round(float(disk.percent), 1),
                "disk_used_gb": round(disk.used / (1024**3), 2),
                "disk_total_gb": round(disk.total / (1024**3), 2),
            }
        except Exception as e:
            logger.debug("psutil metrics failed, using fallback: %s", e)

    mem_total_kb = mem_avail_kb = 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_avail_kb = int(line.split()[1])
    except OSError:
        pass
    mem_used_kb = max(0, mem_total_kb - mem_avail_kb) if mem_total_kb else 0
    mem_total_gb = mem_total_kb / (1024**2) if mem_total_kb else 1.0
    mem_used_gb = mem_used_kb / (1024**2) if mem_total_kb else 0.0
    mem_pct = (100.0 * mem_used_kb / mem_total_kb) if mem_total_kb else 0.0

    load_cpu = 0.0
    try:
        la = os.getloadavg()
        nproc = os.cpu_count() or 1
        load_cpu = min(100.0, 100.0 * float(la[0]) / max(nproc, 1))
    except OSError:
        pass

    try:
        du = shutil.disk_usage(path)
        disk_pct = 100.0 * (du.total - du.free) / du.total if du.total else 0.0
        disk_used = du.total - du.free
        disk_total = du.total
    except OSError:
        disk_pct = 0.0
        disk_used = 0
        disk_total = 1

    return {
        "cpu_percent": round(load_cpu, 1),
        "memory_percent": round(mem_pct, 1),
        "memory_used_gb": round(mem_used_gb, 2),
        "memory_total_gb": round(mem_total_gb, 2),
        "disk_percent": round(disk_pct, 1),
        "disk_used_gb": round(disk_used / (1024**3), 2),
        "disk_total_gb": round(disk_total / (1024**3), 2),
    }
