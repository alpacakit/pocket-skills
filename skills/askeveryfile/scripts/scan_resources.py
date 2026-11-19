"""Size scout concurrency from current machine resources."""

from __future__ import annotations

import contextlib
import fcntl
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

MAX_CONCURRENT_SCOUTS = 100
SCOUTS_PER_GIB_MEMORY = 6.25
SCOUTS_PER_LOGICAL_CPU = 10
# Fixed per-user location so every scan on this machine shares one pool. It
# cannot follow $TMPDIR, which may differ between shells and split the pool.
MACHINE_SLOT_DIR = Path.home() / ".askeveryfile" / "slots"


class MachineScoutSlots:
    """A machine-wide ceiling on concurrent scouts, held as flocked slot files.

    Occupancy is an advisory lock rather than file existence, so the kernel
    frees a slot when its holder exits and leaves no stale state to reap.
    Coordination is best-effort: if the lock directory is unavailable, a scan
    continues without cross-process coordination.
    """

    def __init__(self, capacity: int, directory: Path = MACHINE_SLOT_DIR) -> None:
        self.capacity = max(1, capacity)
        self.directory = directory
        self.available = True
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            self.available = False

    def _try_take(self, index: int) -> Any | None:
        try:
            handle = (self.directory / f"slot-{index:03d}").open("a+b")
        except OSError:
            return None
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None
        return handle

    @contextlib.contextmanager
    def hold(self, start_index: int) -> Iterator[bool]:
        """Occupy one slot for the block, waiting while the machine is full."""
        if not self.available:
            yield False
            return
        handle = None
        delay = 0.1
        while handle is None:
            for offset in range(self.capacity):
                handle = self._try_take((start_index + offset) % self.capacity)
                if handle is not None:
                    break
            if handle is None:
                time.sleep(delay)
                delay = min(1.0, delay * 1.5)
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


def available_memory_ratio() -> float | None:
    if sys.platform == "darwin":
        try:
            output = subprocess.check_output(["memory_pressure"], text=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None
        match = re.search(r"System-wide memory free percentage:\s*(\d+)%", output)
        return int(match.group(1)) / 100 if match else None
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0])
        return values["MemAvailable"] / values["MemTotal"]
    except (OSError, KeyError, ValueError):
        return None


def physical_memory_bytes() -> int | None:
    if sys.platform == "darwin":
        try:
            return int(
                subprocess.check_output(
                    ["sysctl", "-n", "hw.memsize"], text=True
                ).strip()
            )
        except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
            return None
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        physical_pages = os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None
    return int(page_size * physical_pages)


def hardware_parallel_cap() -> tuple[int, dict[str, Any]]:
    """Return this scan's cap and the stable machine-wide ceiling facts."""
    logical_cpus = os.cpu_count() or 1
    memory_bytes = physical_memory_bytes()
    available_ratio = available_memory_ratio()
    cpu_cap = max(1, logical_cpus * SCOUTS_PER_LOGICAL_CPU)
    memory_cap = (
        max(1, math.floor(memory_bytes / (1024**3) * SCOUTS_PER_GIB_MEMORY))
        if memory_bytes is not None
        else MAX_CONCURRENT_SCOUTS
    )
    machine_cap = min(MAX_CONCURRENT_SCOUTS, cpu_cap, memory_cap)
    pressure_multiplier = (
        1.0
        if available_ratio is None or available_ratio >= 0.4
        else 0.75
        if available_ratio >= 0.25
        else 0.5
    )
    pressure_cap = max(1, math.floor(min(cpu_cap, memory_cap) * pressure_multiplier))
    cap = min(MAX_CONCURRENT_SCOUTS, pressure_cap)
    return cap, {
        "logical_cpus": logical_cpus,
        "physical_memory_bytes": memory_bytes,
        "available_memory_ratio_at_plan": available_ratio,
        "cpu_parallel_cap": cpu_cap,
        "memory_parallel_cap": memory_cap,
        "machine_parallel_cap": machine_cap,
        "pressure_multiplier": pressure_multiplier,
        "effective_parallel_cap": cap,
    }
