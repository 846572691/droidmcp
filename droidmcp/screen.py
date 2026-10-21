"""Screenshot capture and transfer, backend-agnostic."""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from . import adb

REMOTE_TMP = "/data/local/tmp/droidmcp_screen.png"


def screenshot_bytes(*, serial: str | None = None, timeout: int = 60) -> bytes:
    """Capture the screen and return raw PNG bytes.

    Tries the fast streaming path first; falls back to capturing on the device
    and pulling the file, which some builds require.
    """

    try:
        return adb.screencap(serial=serial, timeout=timeout)
    except adb.AdbError as first:
        # The adb pull fallback needs an adb backend; for a local root shell
        # there is nothing to pull with, so surface the original error.
        if adb.runtime().name != "adb":
            raise

    adb.shell(f"screencap -p {REMOTE_TMP}", serial=serial, timeout=timeout)
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "screen.png"
        adb.runtime().run(["pull", REMOTE_TMP, str(local)], serial=serial, timeout=timeout)
        data = local.read_bytes()
    adb.shell(f"rm -f {REMOTE_TMP}", serial=serial)
    if not data.startswith(b"\x89PNG"):
        raise adb.AdbError("screencap did not return PNG data")
    return data


def screenshot_base64(*, serial: str | None = None) -> str:
    return base64.b64encode(screenshot_bytes(serial=serial)).decode("ascii")


def save_screenshot(path: str, *, serial: str | None = None) -> str:
    """Capture the screen to ``path`` and return the absolute path."""

    data = screenshot_bytes(serial=serial)
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return str(target.resolve())


def png_size(data: bytes) -> tuple[int, int] | None:
    """Read width/height straight from the PNG IHDR chunk."""

    if len(data) < 24 or not data.startswith(b"\x89PNG"):
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
