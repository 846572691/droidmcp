# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-21

### Added

- MCP server over stdio (JSON-RPC 2.0), implemented with the standard library
  only — no `mcp` SDK, no pydantic, no anyio.
- **Two pluggable backends.** `adb` over USB/wireless, and `root` for rooted
  devices, Termux and Shizuku/Sui (`rish`) — driving a phone with no cable at
  all. Selected with `DROIDMCP_BACKEND` / `DROIDMCP_ROOT_CMD`.
- Device discovery and introspection: `list_devices`, `device_info`, `battery`,
  `storage`, `list_packages`.
- Screen reading: `screen_dump` flattens `uiautomator` XML into a list of nodes
  with absolute tap coordinates, dropping zero-area layout containers.
  `screenshot` saves a PNG locally and reports its pixel dimensions.
- Interaction: `tap`, `tap_text`, `swipe`, `scroll`, `type_text`, `press_key`,
  `launch_app`, `force_stop`, `open_url`.
- Escape hatches: `shell` for arbitrary `adb shell` commands, `logcat` for logs.
- `--selftest` verifies the *active* backend and reports the device model,
  screen size and readiness; `--list-tools` and `--call` allow debugging
  without an MCP client.
- `ADB_PATH` environment variable for non-standard adb installs.
- Node summarisation that keeps only actable elements (label, clickable, or
  scrollable), collapsing inert layout containers — 167 raw nodes become 15
  reported elements on a real home screen — with an automatic relax when a
  screen renders entirely into unlabelled views.
- 43 unit tests covering XML parsing, bounding-box math, `input text` escaping,
  `dumpsys` parsing, both backends' argv construction and the JSON-RPC protocol
  layer — all runnable with no device attached.

### Fixed

- `screen_dump` no longer relies on `uiautomator dump /dev/tty`, which fails
  whenever the command has no interactive terminal — notably over root and
  Shizuku shells. It dumps to a file and reads it back instead, and reports an
  actionable error (instead of an empty screen) when the UI is mid-animation.
- A failing tool body no longer takes down the server process: it returns an
  `isError` content block the model can react to.
- Screenshots over the root backend read the PNG back as base64, because raw
  binary does not survive `rish`/`su -c` transports.
- `device_info` survives a missing focus line (`grep` exits 1 on no match).
- `input text` no longer destroys a literal `%s` in the user's text: escaping
  now order-correctly converts spaces after backslash escaping, with a
  regression test.
- `screen_size` honours `wm size` override resolutions instead of reporting the
  physical panel, which would have made every scroll distance wrong on scaled
  displays.

[Unreleased]: https://github.com/846572691/droidmcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/846572691/droidmcp/releases/tag/v0.1.0
