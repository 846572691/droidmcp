"""Command line entry point: ``python -m droidmcp`` or ``droidmcp``."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .tools import server


def _selftest() -> int:
    """Verify the active backend can reach a device."""

    import os

    from . import adb

    rt = adb.runtime()
    info = rt.describe()
    print(f"droidmcp {__version__}")
    print(f"backend: {info['backend']} → {info['target']}")

    try:
        devices = rt.devices()
    except adb.AdbError as exc:
        print(f"✗ {exc}")
        if info["backend"] == "adb":
            print("  hint: enable USB debugging, or run with DROIDMCP_BACKEND=root")
        return 1

    if not devices:
        print("✗ no devices found")
        if info["backend"] == "adb":
            print("  hint: `adb devices` must list a device in 'device' state")
        return 1

    ready = 0
    for entry in devices:
        ok = entry["state"] == "device"
        ready += ok
        label = entry.get("model") or entry["serial"]
        print(f"{'✓' if ok else '✗'} {entry['serial']} ({label}, {entry['state']})")

    if not ready:
        return 1

    try:
        size = rt.shell("wm size").strip().splitlines()
        print(f"✓ screen: {size[-1].split(':')[-1].strip() if size else 'unknown'}")
    except adb.AdbError as exc:
        print(f"✗ shell not usable: {exc}")
        return 1

    print("ready")
    return 0


def _print_tools() -> int:
    print(json.dumps(server.list_tools(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="droidmcp",
        description="MCP server that lets AI assistants drive an Android device over adb.",
    )
    parser.add_argument("--version", action="version", version=f"droidmcp {__version__}")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="check that adb and a device are reachable, then exit",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="print the MCP tool schema as JSON and exit",
    )
    parser.add_argument(
        "--call",
        metavar="TOOL",
        help="invoke a single tool with --args JSON (handy for debugging)",
    )
    parser.add_argument("--args", default="{}", help="JSON arguments for --call")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.list_tools:
        return _print_tools()
    if args.call:
        from .server import ToolError

        try:
            payload = json.loads(args.args)
        except json.JSONDecodeError as exc:
            print(f"invalid --args JSON: {exc}", file=sys.stderr)
            return 2
        try:
            result = server.call_tool(args.call, payload)
        except ToolError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    server.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
