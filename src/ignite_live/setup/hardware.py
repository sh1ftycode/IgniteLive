"""What this computer has: CPU threads, memory, and graphics cards (for picking an OBS encoder).

No extra dependencies: each OS is asked with what it ships (``/proc`` and ``/sys`` on
Linux, PowerShell on Windows, ``sysctl`` and ``system_profiler`` on macOS). Anything
that can't be read is left unknown, and the tuning treats unknown as "be careful".
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# PCI vendor ids (Linux /sys/class/drm/card*/device/vendor).
_VENDOR_IDS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}
_VENDOR_WORDS = (("nvidia", "nvidia"), ("geforce", "nvidia"), ("quadro", "nvidia"), ("radeon", "amd"),
                 ("amd", "amd"), ("intel", "intel"), ("apple", "apple"))
VENDOR_NAMES = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel", "apple": "Apple"}
TIMEOUT_S = 6


@dataclass(frozen=True)
class Hardware:
    threads: int | None = None
    ram_gb: float | None = None
    gpus: tuple[str, ...] = ()  # Vendors: nvidia, amd, intel, apple (best first, no duplicates).
    gpu_names: tuple[str, ...] = ()  # As the OS names them, for showing.
    system: str = ""

    @property
    def detected(self) -> bool:
        return self.threads is not None and self.ram_gb is not None


Runner = Callable[[list[str]], str]


def _run(command: list[str]) -> str:
    """Run a system tool quietly (no console window on Windows); "" if it fails."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT_S, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def vendor_of(name: str) -> str | None:
    lowered = name.lower()
    return next((vendor for word, vendor in _VENDOR_WORDS if word in lowered), None)


def _sorted_vendors(vendors) -> tuple[str, ...]:
    order = ("nvidia", "amd", "apple", "intel")  # Best stream encoder first.
    return tuple(v for v in order if v in set(vendors))


def ram_gb(system: str, run: Runner = _run, meminfo: Path = Path("/proc/meminfo")) -> float | None:
    if system == "Linux":
        try:
            found = re.search(r"MemTotal:\s+(\d+) kB", meminfo.read_text())
        except OSError:
            return None
        return round(int(found.group(1)) / 1024 / 1024, 1) if found else None
    if system == "Darwin":
        out = run(["sysctl", "-n", "hw.memsize"]).strip()
        return round(int(out) / 1024 ** 3, 1) if out.isdigit() else None
    if system == "Windows":
        return _windows_ram()
    return None


def _windows_ram() -> float | None:
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("free", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                        ("page_free", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                        ("virtual_free", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]

        status = Status()
        status.length = ctypes.sizeof(Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return None
        return round(status.total / 1024 ** 3, 1)
    except (AttributeError, OSError, ValueError):
        return None


def gpus(system: str, run: Runner = _run, drm: Path = Path("/sys/class/drm")) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(vendors, names)"""
    names: list[str] = []
    vendors: list[str] = []
    if system == "Windows":
        out = run(["powershell", "-NoProfile", "-Command",
                   "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"])
        names = [line.strip() for line in out.splitlines() if line.strip()]
    elif system == "Darwin":
        if platform.machine() == "arm64":
            vendors.append("apple")
            names.append("Apple silicon")
        out = run(["system_profiler", "SPDisplaysDataType"])
        names += [m.strip() for m in re.findall(r"Chipset Model:\s*(.+)", out)]
    elif system == "Linux":
        try:
            for card in sorted(drm.glob("card[0-9]*")):
                vendor_file = card / "device" / "vendor"
                if vendor_file.is_file():
                    vendor = _VENDOR_IDS.get(vendor_file.read_text().strip().lower())
                    if vendor:
                        vendors.append(vendor)
        except OSError:
            pass
        out = run(["lspci"])
        names = [line.split(": ", 1)[1].strip() for line in out.splitlines()
                 if re.search(r"VGA|3D controller|Display controller", line) and ": " in line]
    vendors += [v for v in (vendor_of(name) for name in names) if v]
    names = list(dict.fromkeys(names)) or [VENDOR_NAMES[v] for v in _sorted_vendors(vendors)]
    return _sorted_vendors(vendors), tuple(names)


def detect(run: Runner = _run, system: str | None = None) -> Hardware:
    system = system or platform.system()
    vendors, names = gpus(system, run)
    return Hardware(threads=os.cpu_count(), ram_gb=ram_gb(system, run), gpus=vendors, gpu_names=names, system=system)
