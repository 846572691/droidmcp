"""Tool definitions exposed over MCP."""

from __future__ import annotations

from typing import Any

from . import adb, device as dev, input as input_mod, screen, ui
from .server import MCPServer, ToolError, error_result, json_result, text_result

server = MCPServer()

# Cache of the most recent UI dump so `tap_text` can resolve a label without
# making the model dump the screen twice.
_last_nodes: list[ui.UiNode] = []

_DEVICE_ARG = {
    "type": "string",
    "description": "Device serial from list_devices (omit when exactly one device is connected).",
}


def _wrap(func, *args, **kwargs) -> dict[str, Any]:
    """Run a tool body, converting adb failures into MCP error content."""

    try:
        return func(*args, **kwargs)
    except adb.AdbError as exc:
        return error_result(f"adb error: {exc}")


# --------------------------------------------------------------------- discovery


@server.tool(
    "list_devices",
    "List Android devices visible to adb, including emulators.",
    {"type": "object", "properties": {}},
)
def list_devices() -> dict[str, Any]:
    def body() -> dict[str, Any]:
        rt = adb.runtime()
        return json_result(
            {
                "backend": rt.describe(),
                "devices": adb.list_devices(),
            }
        )

    return _wrap(body)


@server.tool(
    "device_info",
    "Model, Android version, screen size and the currently focused activity.",
    {"type": "object", "properties": {"device": _DEVICE_ARG}},
)
def device_info(device: str | None = None) -> dict[str, Any]:
    return _wrap(lambda: json_result(dev.device_info(serial=device)))


@server.tool(
    "battery",
    "Battery level, charging status and temperature.",
    {"type": "object", "properties": {"device": _DEVICE_ARG}},
)
def battery(device: str | None = None) -> dict[str, Any]:
    return _wrap(lambda: json_result(dev.battery(serial=device)))


@server.tool(
    "storage",
    "Free/used storage for the /data partition.",
    {"type": "object", "properties": {"device": _DEVICE_ARG}},
)
def storage(device: str | None = None) -> dict[str, Any]:
    return _wrap(lambda: json_result(dev.storage(serial=device)))


@server.tool(
    "list_packages",
    "Installed package names. Set third_party_only=false to include system apps.",
    {
        "type": "object",
        "properties": {
            "third_party_only": {"type": "boolean", "default": True},
            "device": _DEVICE_ARG,
        },
    },
)
def list_packages(third_party_only: bool = True, device: str | None = None) -> dict[str, Any]:
    return _wrap(
        lambda: json_result(
            dev.packages(third_party_only=third_party_only, serial=device)
        )
    )


# ------------------------------------------------------------------------ screen


@server.tool(
    "screen_dump",
    (
        "Read the current screen as structured JSON: every node with text, "
        "content description, resource id, tap coordinates and whether it is "
        "clickable or scrollable. Use this before tapping anything so you act "
        "on real element positions instead of guessing."
    ),
    {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 60, "maximum": 200},
            "device": _DEVICE_ARG,
        },
    },
)
def screen_dump(limit: int = 60, device: str | None = None) -> dict[str, Any]:
    global _last_nodes

    def body() -> dict[str, Any]:
        raw = adb.shell(ui.DUMP_COMMAND, serial=device)
        nodes = ui.parse_dump(raw)
        _last_nodes = nodes
        return json_result(
            {
                "node_count": len(nodes),
                "elements": ui.summarize(nodes, limit=limit),
            }
        )

    return _wrap(body)


@server.tool(
    "screenshot",
    (
        "Capture the screen and save it to a local PNG path. Returns the path "
        "plus the pixel dimensions so coordinates can be scaled correctly."
    ),
    {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Where to write the PNG (default: droidmcp-screen.png in cwd).",
            },
            "device": _DEVICE_ARG,
        },
    },
)
def screenshot(path: str = "droidmcp-screen.png", device: str | None = None) -> dict[str, Any]:
    def body() -> dict[str, Any]:
        data = screen.screenshot_bytes(serial=device)
        saved = screen.save_screenshot(path, serial=device)
        size = screen.png_size(data)
        return json_result(
            {
                "path": saved,
                "width": size[0] if size else None,
                "height": size[1] if size else None,
                "bytes": len(data),
            }
        )

    return _wrap(body)


# ------------------------------------------------------------------- interaction


@server.tool(
    "tap",
    "Tap the screen at absolute pixel coordinates.",
    {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "device": _DEVICE_ARG,
        },
        "required": ["x", "y"],
    },
)
def tap(x: int, y: int, device: str | None = None) -> dict[str, Any]:
    def body() -> dict[str, Any]:
        input_mod.tap(x, y, serial=device)
        return text_result(f"tapped ({x}, {y})")

    return _wrap(body)


@server.tool(
    "tap_text",
    (
        "Find an element by its visible text, content description or resource id "
        "and tap its centre. This is the robust alternative to guessing "
        "coordinates. Runs a fresh screen dump unless the previous one is reused."
    ),
    {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Label to look for."},
            "exact": {"type": "boolean", "default": False},
            "nth": {
                "type": "integer",
                "default": 0,
                "description": "Which match to tap when several exist (0 = first).",
            },
            "device": _DEVICE_ARG,
        },
        "required": ["text"],
    },
)
def tap_text(
    text: str, exact: bool = False, nth: int = 0, device: str | None = None
) -> dict[str, Any]:
    global _last_nodes

    def body() -> dict[str, Any]:
        raw = adb.shell(ui.DUMP_COMMAND, serial=device)
        try:
            nodes = ui.parse_dump(raw)
        except ValueError as exc:
            return error_result(str(exc))
        _last_nodes = nodes

        matches = ui.find_matching(nodes, text, exact=exact)
        if not matches:
            return error_result(
                f"no element matching {text!r}; visible labels: "
                + ", ".join(sorted({n.text for n in nodes if n.text})[:20])
            )
        if nth >= len(matches):
            return error_result(f"only {len(matches)} match(es) for {text!r}")
        node = matches[nth]
        input_mod.tap(node.center[0], node.center[1], serial=device)
        return json_result(
            {
                "tapped": text,
                "x": node.center[0],
                "y": node.center[1],
                "class": node.class_name,
                "package": node.package,
            }
        )

    return _wrap(body)


@server.tool(
    "swipe",
    "Swipe/drag from (x1,y1) to (x2,y2). Increase duration_ms for slow drags.",
    {
        "type": "object",
        "properties": {
            "x1": {"type": "integer"},
            "y1": {"type": "integer"},
            "x2": {"type": "integer"},
            "y2": {"type": "integer"},
            "duration_ms": {"type": "integer", "default": 300},
            "device": _DEVICE_ARG,
        },
        "required": ["x1", "y1", "x2", "y2"],
    },
)
def swipe(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_ms: int = 300,
    device: str | None = None,
) -> dict[str, Any]:
    def body() -> dict[str, Any]:
        input_mod.swipe(x1, y1, x2, y2, duration_ms=duration_ms, serial=device)
        return text_result(f"swiped ({x1},{y1}) -> ({x2},{y2}) in {duration_ms}ms")

    return _wrap(body)


@server.tool(
    "scroll",
    (
        "Scroll the dominant axis of the screen. Use direction 'down' to reveal "
        "content further down the page. Coordinates are derived from the real "
        "screen size, so this works on any resolution."
    ),
    {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right"],
                "default": "down",
            },
            "amount": {
                "type": "number",
                "default": 0.5,
                "description": "Fraction of the screen to travel (0.1 - 1.0).",
            },
            "device": _DEVICE_ARG,
        },
    },
)
def scroll(direction: str = "down", amount: float = 0.5, device: str | None = None) -> dict[str, Any]:
    def body() -> dict[str, Any]:
        width, height = input_mod.screen_size(serial=device)
        frac = min(max(amount, 0.05), 1.0)
        cx, cy = width // 2, height // 2
        dx, dy = int((width // 2) * frac), int((height // 2) * frac)
        vectors = {
            "down": (cx, cy + dy, cx, cy - dy),
            "up": (cx, cy - dy, cx, cy + dy),
            "right": (cx + dx, cy, cx - dx, cy),
            "left": (cx - dx, cy, cx + dx, cy),
        }
        if direction not in vectors:
            return error_result(f"direction must be one of {sorted(vectors)}")
        x1, y1, x2, y2 = vectors[direction]
        input_mod.swipe(x1, y1, x2, y2, duration_ms=400, serial=device)
        return text_result(f"scrolled {direction} by {frac:.0%} of the screen")

    return _wrap(body)


@server.tool(
    "type_text",
    (
        "Type into the currently focused text field. Tap the field first. "
        "Spaces and shell metacharacters are escaped automatically."
    ),
    {
        "type": "object",
        "properties": {"text": {"type": "string"}, "device": _DEVICE_ARG},
        "required": ["text"],
    },
)
def type_text(text: str, device: str | None = None) -> dict[str, Any]:
    def body() -> dict[str, Any]:
        input_mod.type_text(text, serial=device)
        return text_result(f"typed {len(text)} character(s)")

    return _wrap(body)


@server.tool(
    "press_key",
    (
        "Press a hardware key: home, back, enter, menu, power, volume_up, "
        "volume_down, recent, wake, sleep, tab, delete, search, notifications. "
        "Raw KEYCODE_* names are also accepted."
    ),
    {
        "type": "object",
        "properties": {"key": {"type": "string"}, "device": _DEVICE_ARG},
        "required": ["key"],
    },
)
def press_key(key: str, device: str | None = None) -> dict[str, Any]:
    def body() -> dict[str, Any]:
        input_mod.key(key, serial=device)
        return text_result(f"pressed {key}")

    return _wrap(body)


@server.tool(
    "launch_app",
    "Launch an app by package name, optionally specifying an activity.",
    {
        "type": "object",
        "properties": {
            "package": {"type": "string"},
            "activity": {"type": "string"},
            "device": _DEVICE_ARG,
        },
        "required": ["package"],
    },
)
def launch_app(
    package: str, activity: str | None = None, device: str | None = None
) -> dict[str, Any]:
    def body() -> dict[str, Any]:
        return json_result({"output": input_mod.launch(package, activity=activity, serial=device)})

    return _wrap(body)


@server.tool(
    "force_stop",
    "Force-stop a running app by package name.",
    {
        "type": "object",
        "properties": {"package": {"type": "string"}, "device": _DEVICE_ARG},
        "required": ["package"],
    },
)
def force_stop(package: str, device: str | None = None) -> dict[str, Any]:
    def body() -> dict[str, Any]:
        input_mod.force_stop(package, serial=device)
        return text_result(f"stopped {package}")

    return _wrap(body)


@server.tool(
    "open_url",
    "Open a URL in the device's default browser or matching app.",
    {
        "type": "object",
        "properties": {"url": {"type": "string"}, "device": _DEVICE_ARG},
        "required": ["url"],
    },
)
def open_url(url: str, device: str | None = None) -> dict[str, Any]:
    def body() -> dict[str, Any]:
        input_mod.open_url(url, serial=device)
        return text_result(f"opened {url}")

    return _wrap(body)


@server.tool(
    "shell",
    (
        "Escape hatch: run an arbitrary adb shell command and return stdout. "
        "Use for anything not covered by the dedicated tools (e.g. "
        "'settings get secure enabled_accessibility_services')."
    ),
    {
        "type": "object",
        "properties": {"command": {"type": "string"}, "device": _DEVICE_ARG},
        "required": ["command"],
    },
)
def shell(command: str, device: str | None = None) -> dict[str, Any]:
    return _wrap(lambda: text_result(adb.shell(command, serial=device)))


@server.tool(
    "logcat",
    "Tail recent logcat lines. filter_spec uses adb syntax such as '*:E' for errors only.",
    {
        "type": "object",
        "properties": {
            "lines": {"type": "integer", "default": 200},
            "filter_spec": {"type": "string", "default": ""},
            "device": _DEVICE_ARG,
        },
    },
)
def logcat(lines: int = 200, filter_spec: str = "", device: str | None = None) -> dict[str, Any]:
    return _wrap(
        lambda: text_result(dev.logcat(lines=lines, filter_spec=filter_spec, serial=device))
    )
