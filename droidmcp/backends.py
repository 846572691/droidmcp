"""Pluggable backends.

The default backend shells out to ``adb``, which requires USB or wireless
debugging.  Many Android power users — rooted phones, custom ROMs, Termux setups
— have shell access on the device itself but no working adb connection.  The
``root`` backend drives the same tools through a local root shell instead.

Selected with the ``DROIDMCP_BACKEND`` environment variable:

===============  =========================================================
``adb``          Default. Uses the adb binary (``ADB_PATH`` overrides).
``root``         Local root shell via ``su -c`` / ``rish`` (no adb needed).
===============  =========================================================

Set ``DROIDMCP_ROOT_CMD`` to change how a command is elevated, for example
``"rish"`` on Shizuku/Sui or ``"sudo"`` in a Linux chroot:

    DROIDMCP_BACKEND=root DROIDMCP_ROOT_CMD=rish
"""

from __future__ import annotations

import base64
import os
import shlex
import subprocess
from dataclasses import dataclass

DEFAULT_TIMEOUT = 30
_SCREEN_TMP = "/data/local/tmp/droidmcp_shot.png"


class BackendError(RuntimeError):
    """Raised when a backend cannot run a command."""


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def raise_for_status(self) -> "CommandResult":
        if not self.ok:
            detail = (self.stderr or self.stdout).strip() or "command failed"
            raise BackendError(detail)
        return self


class AdbBackend:
    """Runs commands through the adb binary.

    Both :meth:`run` and :meth:`shell` return a :class:`CommandResult` and never
    raise on a non-zero exit status — some perfectly useful commands (``grep``,
    ``pidof``) exit non-zero when they simply found nothing. Callers that care
    call :meth:`CommandResult.raise_for_status`.
    """

    name = "adb"

    def __init__(self, binary: str | None = None) -> None:
        self._binary = binary

    @property
    def binary(self) -> str:
        if self._binary:
            return self._binary
        import shutil

        override = os.environ.get("ADB_PATH")
        if override:
            return override
        found = shutil.which("adb")
        if not found:
            raise BackendError(
                "adb not found on PATH. Install Android platform-tools, set "
                "ADB_PATH, or switch to the root backend with DROIDMCP_BACKEND=root."
            )
        return found

    def _argv(self, args: list[str], serial: str | None) -> list[str]:
        argv = [self.binary]
        if serial:
            argv += ["-s", serial]
        return argv + args

    def run(
        self,
        args: list[str],
        *,
        serial: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> CommandResult:
        return _execute(self._argv(args, serial), timeout=timeout, binary=False)

    def shell(
        self,
        command: str,
        *,
        serial: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> CommandResult:
        return self.run(["shell", command], serial=serial, timeout=timeout)

    def screenshot_png(self, *, serial: str | None = None, timeout: int = 60) -> bytes:
        result = _execute(
            self._argv(["exec-out", "screencap", "-p"], serial),
            timeout=timeout,
            binary=True,
        )
        data = result.stdout.encode("latin-1") if isinstance(result.stdout, str) else result.stdout
        if not data.startswith(b"\x89PNG"):
            raise BackendError(
                "screencap did not return PNG data; the device may be showing a "
                "secure surface or the exec-out path is blocked"
            )
        return data

    def list_devices(self) -> list[dict[str, str]]:
        result = self.run(["devices"]).raise_for_status()
        devices: list[dict[str, str]] = []
        for line in result.stdout.splitlines()[1:]:
            line = line.strip()
            if not line or "\t" not in line:
                continue
            serial, _, state = line.partition("\t")
            devices.append({"serial": serial.strip(), "state": state.strip()})
        return devices


class RootShellBackend:
    """Runs commands in a local root shell — no adb, no USB cable.

    Returns :class:`CommandResult` without raising on non-zero status, matching
    :class:`AdbBackend`.

    Intended for rooted devices, Termux, and Shizuku/Sui (via ``rish``).  Every
    ``adb shell X`` becomes ``<elevator> -c 'X'``.
    """

    name = "root"

    def __init__(self, elevator: str | None = None) -> None:
        self._elevator = elevator or os.environ.get("DROIDMCP_ROOT_CMD") or "su"

    @property
    def elevator(self) -> str:
        return self._elevator

    def _argv(self, args: list[str], serial: str | None) -> list[str]:
        # Fall back to the ambient environment when an elevator like rish is used.
        if self._elevator in ("rish", "sh"):
            return [self._elevator, *args]
        return [self._elevator, "-c", " ".join(shlex.quote(a) for a in args)]

    def run(
        self,
        args: list[str],
        *,
        serial: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> CommandResult:
        return _execute(self._argv(args, serial), timeout=timeout, binary=False)

    def shell(
        self,
        command: str,
        *,
        serial: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> CommandResult:
        if self._elevator in ("rish", "sh"):
            argv = [self._elevator, command]
        else:
            argv = [self._elevator, "-c", command]
        return _execute(argv, timeout=timeout, binary=False)

    def screenshot_png(self, *, serial: str | None = None, timeout: int = 60) -> bytes:
        """Capture a screenshot through the root shell.

        Raw binary cannot survive most shell transports (rish, `su -c` wrappers,
        terminal encodings), so the PNG is captured to a temp file on the device
        and read back as base64.
        """

        script = (
            f"screencap -p {_SCREEN_TMP} && base64 {_SCREEN_TMP} 2>/dev/null "
            f"|| toybox base64 {_SCREEN_TMP} 2>/dev/null; rm -f {_SCREEN_TMP}"
        )
        result = self.shell(script, timeout=timeout)
        if not result.ok and not result.stdout.strip():
            raise BackendError(
                "screencap failed; the device may be showing a secure surface. "
                "On Android 14+ the shell user may lack screen access — try "
                "DROIDMCP_ROOT_CMD=su."
            )
        payload = "".join(result.stdout.split())
        if not payload:
            raise BackendError("screencap produced no data")
        try:
            data = base64.b64decode(payload, validate=False)
        except (ValueError, TypeError) as exc:
            raise BackendError(f"could not decode screenshot payload: {exc}") from exc
        if not data.startswith(b"\x89PNG"):
            raise BackendError(
                "screencap did not return PNG data; the device may be showing a "
                "secure (DRM) surface, which the platform blanks out"
            )
        return data

    def list_devices(self) -> list[dict[str, str]]:
        """A single synthetic device — there is only ever one local machine."""

        try:
            model = self.shell("getprop ro.product.model").stdout.strip()
        except BackendError:
            model = ""
        return [{"serial": "local", "state": "device", "model": model or "local"}]


def _execute(argv: list[str], *, timeout: int, binary: bool) -> CommandResult:
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise BackendError(f"command timed out after {timeout}s: {argv[0]}") from exc
    except FileNotFoundError as exc:
        raise BackendError(f"executable not found: {argv[0]}") from exc

    if binary:
        # Keep raw bytes available; decode as latin-1 so nothing is lost.
        return CommandResult(
            stdout=proc.stdout.decode("latin-1", errors="replace"),
            stderr=proc.stderr.decode("utf-8", errors="replace"),
            returncode=proc.returncode,
        )
    return CommandResult(
        stdout=proc.stdout.decode("utf-8", errors="replace"),
        stderr=proc.stderr.decode("utf-8", errors="replace"),
        returncode=proc.returncode,
    )


_BACKEND: AdbBackend | RootShellBackend | None = None


def get_backend(force: str | None = None) -> AdbBackend | RootShellBackend:
    """Return the active backend, honouring DROIDMCP_BACKEND."""

    global _BACKEND
    choice = (force or os.environ.get("DROIDMCP_BACKEND") or "adb").strip().lower()
    if force:
        _BACKEND = None  # explicit rebuild
    if _BACKEND is not None and not force:
        return _BACKEND
    if choice in ("root", "su", "rish"):
        _BACKEND = RootShellBackend()
    elif choice == "adb":
        _BACKEND = AdbBackend()
    else:
        raise BackendError(f"unknown backend {choice!r}; use 'adb' or 'root'")
    return _BACKEND


def reset_backend() -> None:
    """Drop the cached backend (used by tests and after env changes)."""

    global _BACKEND
    _BACKEND = None
