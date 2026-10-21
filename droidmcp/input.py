"""Input / interaction primitives built on top of adb shell input."""

from __future__ import annotations

import shlex

from . import adb

KEY_CODES = {
    "home": "KEYCODE_HOME",
    "back": "KEYCODE_BACK",
    "enter": "KEYCODE_ENTER",
    "menu": "KEYCODE_MENU",
    "power": "KEYCODE_POWER",
    "volume_up": "KEYCODE_VOLUME_UP",
    "volume_down": "KEYCODE_VOLUME_DOWN",
    "recent": "KEYCODE_APP_SWITCH",
    "wake": "KEYCODE_WAKEUP",
    "sleep": "KEYCODE_SLEEP",
    "tab": "KEYCODE_TAB",
    "delete": "KEYCODE_DEL",
    "search": "KEYCODE_SEARCH",
    "camera": "KEYCODE_CAMERA",
    "notifications": "KEYCODE_NOTIFICATION",
}


def tap(x: int, y: int, *, serial: str | None = None) -> None:
    adb.shell(f"input tap {int(x)} {int(y)}", serial=serial)


def long_press(x: int, y: int, *, duration_ms: int = 800, serial: str | None = None) -> None:
    adb.shell(
        f"input swipe {int(x)} {int(y)} {int(x)} {int(y)} {int(duration_ms)}",
        serial=serial,
    )


def swipe(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    duration_ms: int = 300,
    serial: str | None = None,
) -> None:
    adb.shell(
        f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}",
        serial=serial,
    )


def _escape_for_input(text: str) -> str:
    """Escape a string so `input text` keeps spaces and shell metacharacters."""

    # `input text` encodes a space as the literal token %s, so user text must
    # have its spaces converted *after* backslashes are escaped.  Building the
    # string the other way round silently destroyed any literal "%s" the user
    # typed.
    escaped = text.replace("\\", "\\\\").replace(" ", "%s")
    return shlex.quote(escaped)


def type_text(text: str, *, serial: str | None = None) -> None:
    if not text:
        return
    adb.shell(f"input text {_escape_for_input(text)}", serial=serial)


def key(name: str, *, serial: str | None = None) -> None:
    """Press a named key (see :data:`KEY_CODES`) or a raw KEYCODE_* / int value."""

    token = name.strip().lower()
    code = KEY_CODES.get(token, name.strip())
    adb.shell(f"input keyevent {code}", serial=serial)


def launch(package: str, *, activity: str | None = None, serial: str | None = None) -> str:
    """Start an app. Returns adb's status output."""

    if activity:
        component = activity if "/" in activity else f"{package}/{activity}"
        return adb.shell(f"am start -n {component}", serial=serial)
    return adb.shell(
        f"monkey -p {package} -c android.intent.category.LAUNCHER 1",
        serial=serial,
    )


def force_stop(package: str, *, serial: str | None = None) -> None:
    adb.shell(f"am force-stop {package}", serial=serial)


def open_url(url: str, *, serial: str | None = None) -> None:
    adb.shell(
        f"am start -a android.intent.action.VIEW -d {shlex.quote(url)}",
        serial=serial,
    )


def screen_size(*, serial: str | None = None) -> tuple[int, int]:
    """Return the resolution in effect, honouring an `wm size` override."""

    raw = adb.shell("wm size", serial=serial)
    found: dict[str, tuple[int, int]] = {}
    for line in raw.splitlines():
        label = "override" if "Override size" in line else "physical" if "Physical size" in line else None
        if label is None or label in found:
            continue
        _, _, dims = line.partition(":")
        width, _, height = dims.strip().partition("x")
        if width.isdigit() and height.isdigit():
            found[label] = (int(width), int(height))
    if "override" in found:
        return found["override"]
    if "physical" in found:
        return found["physical"]
    raise adb.AdbError(f"could not determine screen size from: {raw!r}")
