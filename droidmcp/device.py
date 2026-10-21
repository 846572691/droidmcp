"""Read-only device introspection: battery, storage, packages, properties."""

from __future__ import annotations

import re

from . import adb

_LEVEL_RE = re.compile(r"level:\s*(\d+)")
_TEMP_RE = re.compile(r"temperature:\s*(\d+)")


def battery(*, serial: str | None = None) -> dict:
    """Parse `dumpsys battery` into a small dict."""

    raw = adb.shell("dumpsys battery", serial=serial)
    data: dict[str, object] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "level":
            data["percent"] = int(value) if value.isdigit() else None
        elif key == "status":
            data["status_code"] = int(value) if value.isdigit() else value
        elif key == "temperature":
            data["temperature_c"] = int(value) / 10 if value.isdigit() else None
        elif key == "ac powered":
            data["ac"] = value == "true"
        elif key == "usb powered":
            data["usb"] = value == "true"
        elif key == "wireless powered":
            data["wireless"] = value == "true"

    status_map = {
        1: "unknown",
        2: "charging",
        3: "discharging",
        4: "not charging",
        5: "full",
    }
    code = data.pop("status_code", None)
    if isinstance(code, int):
        data["status"] = status_map.get(code, str(code))
    return data


def storage(*, serial: str | None = None) -> dict:
    """Return free/total bytes for /data, parsed from `df -k /data`."""

    raw = adb.shell("df -k /data", serial=serial)
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) < 2:
        return {}
    parts = lines[-1].split()
    if len(parts) < 4:
        return {}
    try:
        total_kb = int(parts[1])
        used_kb = int(parts[2])
        free_kb = int(parts[3])
    except ValueError:
        return {}
    return {
        "total_bytes": total_kb * 1024,
        "used_bytes": used_kb * 1024,
        "free_bytes": free_kb * 1024,
        "used_percent": round(used_kb / total_kb * 100, 1) if total_kb else None,
    }


def packages(*, third_party_only: bool = True, serial: str | None = None) -> list[str]:
    flag = "-3" if third_party_only else ""
    raw = adb.shell(f"pm list packages {flag}".strip(), serial=serial)
    return sorted(
        line.partition(":")[2].strip()
        for line in raw.splitlines()
        if line.startswith("package:")
    )


def is_package_installed(package: str, *, serial: str | None = None) -> bool:
    raw = adb.shell(f"pm list packages {package}", serial=serial)
    return f"package:{package}" in raw


def device_info(*, serial: str | None = None) -> dict:
    props = {
        "manufacturer": "ro.product.manufacturer",
        "model": "ro.product.model",
        "android_version": "ro.build.version.release",
        "sdk": "ro.build.version.sdk",
        "abi": "ro.product.cpu.abi",
        "device": "ro.product.device",
    }
    info: dict[str, str] = {}
    for key, prop in props.items():
        try:
            info[key] = adb.getprop(prop, serial=serial)
        except adb.AdbError:
            info[key] = ""
    try:
        _, _, dims = adb.shell("wm size", serial=serial).partition(":")
        width, _, height = dims.strip().partition("x")
        if width.isdigit() and height.isdigit():
            info["screen"] = f"{width}x{height}"
    except adb.AdbError:
        pass
    try:
        info["current_activity"] = current_activity(serial=serial)
    except adb.AdbError:
        info["current_activity"] = ""
    return info


def current_activity(*, serial: str | None = None) -> str:
    """Return the resumed activity, e.g. com.android.settings/.Settings."""

    # `grep` exits 1 when there is no match and 141 when the pipe closes early;
    # neither is an error for us, so swallow the status.
    out = adb.shell(
        "dumpsys window 2>/dev/null | grep -m1 -E 'mCurrentFocus|mFocusedApp' || true",
        serial=serial,
    )
    match = re.search(r"([A-Za-z0-9_.]+/[A-Za-z0-9_.$]+)", out)
    return match.group(1) if match else ""


def logcat(*, lines: int = 200, filter_spec: str = "", serial: str | None = None) -> str:
    """Tail logcat.

    ``filter_spec`` is passed through as ``logcat -d -t <lines> <filter>``; for
    example ``"*:E"`` returns only errors.
    """

    cmd = f"logcat -d -t {int(lines)}"
    if filter_spec:
        cmd += f" {filter_spec}"
    return adb.shell(cmd, serial=serial)
