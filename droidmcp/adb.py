"""Backend-agnostic command helpers.

Every tool goes through this module instead of calling ``adb`` directly, so the
same code works over USB (adb backend) or on a rooted phone (local root shell
backend).  Keeping the old ``adb.*`` names here means the tool layer never has
to care which one is active.
"""

from __future__ import annotations

import shutil
import subprocess

from .backends import (
    AdbBackend,
    BackendError,
    CommandResult,
    RootShellBackend,
    get_backend,
)

# Backwards-compatible aliases — call sites and tests use the adb-era names.
AdbError = BackendError
AdbResult = CommandResult

DEFAULT_TIMEOUT = 30


class Runtime:
    """The active transport, with convenience wrappers used by the tools."""

    def __init__(self) -> None:
        self.backend = get_backend()

    # -------------------------------------------------------------- naming

    @property
    def name(self) -> str:
        return self.backend.name

    def describe(self) -> dict[str, str]:
        if isinstance(self.backend, AdbBackend):
            try:
                target = self.backend.binary
            except BackendError as exc:
                target = f"<missing: {exc}>"
            return {"backend": "adb", "target": target}
        return {"backend": "root", "target": self.backend.elevator}

    # -------------------------------------------------------------- execution

    def run(
        self,
        args: list[str],
        *,
        serial: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> CommandResult:
        return self.backend.run(args, serial=serial, timeout=timeout)

    def shell(
        self,
        command: str,
        *,
        serial: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> str:
        return self.backend.shell(command, serial=serial, timeout=timeout).raise_for_status().stdout

    def screencap(self, *, serial: str | None = None, timeout: int = 60) -> bytes:
        return self.backend.screenshot_png(serial=serial, timeout=timeout)

    def devices(self) -> list[dict[str, str]]:
        return self.backend.list_devices()

    def getprop(self, name: str, *, serial: str | None = None) -> str:
        return self.shell(f"getprop {name}", serial=serial).strip()


def runtime() -> Runtime:
    """Build a runtime from the current environment (call per invocation)."""

    return Runtime()


# --- module-level helpers kept for the existing tool/API surface -------------


def find_adb() -> str:
    backend = get_backend()
    if isinstance(backend, AdbBackend):
        return backend.binary
    raise BackendError(
        "the active backend is 'root' — there is no adb binary. "
        "Set DROIDMCP_BACKEND=adb to use adb."
    )


def list_devices() -> list[dict[str, str]]:
    return runtime().devices()


def shell(command: str, *, serial: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    return runtime().shell(command, serial=serial, timeout=timeout)


def getprop(name: str, *, serial: str | None = None) -> str:
    return runtime().getprop(name, serial=serial)


def screencap(*, serial: str | None = None, timeout: int = 60) -> bytes:
    return runtime().screencap(serial=serial, timeout=timeout)


def package_name_from_apk(apk_path: str) -> str | None:
    """Best-effort package name from a local APK via aapt/aapt2, if present."""

    for tool in ("aapt2", "aapt"):
        exe = shutil.which(tool)
        if not exe:
            continue
        try:
            proc = subprocess.run(
                [exe, "dump", "badging", apk_path],
                capture_output=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
            if line.startswith("package:"):
                for token in line.split():
                    if token.startswith("name="):
                        return token.split("=", 1)[1].strip("'\"")
    return None
