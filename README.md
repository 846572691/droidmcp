# droidmcp

**Give any AI assistant hands on your Android device.**

`droidmcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server that
exposes a connected Android device — real hardware or an emulator — as a set of
tools an LLM can actually use: read the screen, tap elements by their label,
type, swipe, launch apps and inspect the system.

Zero dependencies. Pure standard library. Python 3.9+.

```
┌──────────────┐   MCP/JSON-RPC   ┌───────────┐   adb / su   ┌─────────────┐
│ Claude / any │ ───────────────► │ droidmcp  │ ───────────► │ Android     │
│ MCP client   │ ◄─────────────── │  (stdio)  │ ◄─────────── │ device      │
└──────────────┘                  └───────────┘              └─────────────┘
```

---

## Why

Automating a phone from an LLM is usually done by pasting screenshots into the
prompt and hoping the model guesses the right coordinates. That breaks the
moment the screen changes, the keyboard opens, or the device rotates.

`droidmcp` takes the opposite approach: **read the real layout, act on real
elements.** Every interaction targets a `uiautomator` node with known bounds, so
taps land where the model intended instead of three buttons away.

```python
# Instead of guessing: "the search icon is around x=980, y=270"
tap_text(text="搜索")     # resolved from the live UI tree every time
```

Two things make it practical:

- **Any transport.** Use `adb` over USB/wireless, or — on a rooted phone, in
  Termux, or with Shizuku/Sui — drive the device through a local root shell
  with **no cable at all**.
- **Token-efficient screens.** A raw `uiautomator` dump of a busy home screen is
  167 nodes; `screen_dump` reports the 15 that a model can actually act on.

---

## Install

```bash
pip install .            # or: pipx install .
```

You also need `adb` on your `PATH` (skip this if you use the root backend):

| Platform | Command |
|---|---|
| Debian/Ubuntu | `sudo apt install android-tools-adb` |
| macOS | `brew install android-platform-tools` |
| Arch | `sudo pacman -S android-tools` |
| Alpine | `sudo apk add android-tools` |
| Windows | [platform-tools](https://developer.android.com/studio/releases/platform-tools) |
| Termux | `pkg install android-tools` |

Verify everything works:

```bash
droidmcp --selftest
# droidmcp 0.1.0
# backend: adb → /usr/bin/adb
# ✓ R58M12345 (Pixel 7, device)
# ✓ screen: 1080x2400
# ready
```

---

## Two backends

### adb (default)

Requires USB debugging or wireless debugging. Nothing to configure:

```bash
droidmcp --selftest
```

Point at a non-standard binary with `ADB_PATH=/opt/platform-tools/adb`.

### root — no cable required

If the device is rooted, or you have [Shizuku](https://shizuku.rikka.app)/Sui
running, `droidmcp` can drive it through a local root shell instead of adb.
This is the difference between "needs a laptop and a cable" and "runs from
Termux on the phone itself".

```bash
# Rooted device / Magisk / KernelSU
DROIDMCP_BACKEND=root droidmcp

# Shizuku or Sui (rish is the Shizuku shell)
DROIDMCP_BACKEND=root DROIDMCP_ROOT_CMD=rish droidmcp

# Any sudo-capable Linux chroot / proot
DROIDMCP_BACKEND=root DROIDMCP_ROOT_CMD=sudo droidmcp
```

Verify:

```bash
DROIDMCP_BACKEND=root DROIDMCP_ROOT_CMD=rish droidmcp --selftest
# droidmcp 0.1.0
# backend: root → rish
# ✓ local (M2012K11AC, device)
# ✓ screen: 1080x2400
# ready
```

| Variable | Values | Meaning |
|---|---|---|
| `DROIDMCP_BACKEND` | `adb` (default), `root` | Which transport to use |
| `DROIDMCP_ROOT_CMD` | `su` (default), `rish`, `sudo`, … | How to elevate for the root backend |
| `ADB_PATH` | path | Non-standard adb binary |

---

## Quick start

### 1. Enable debugging on the device

*adb backend:*

1. Settings → About phone → tap **Build number** seven times.
2. Settings → System → Developer options → enable **USB debugging**.
3. Plug it in and accept the RSA fingerprint prompt.
4. `adb devices` should list it as `device` (not `unauthorized`).

*root backend:* nothing to enable — just make sure `su` or `rish` works.

### 2. Register with your MCP client

**Claude Desktop** — `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "android": {
      "command": "droidmcp",
      "env": { "DROIDMCP_BACKEND": "adb" }
    }
  }
}
```

**Claude Code**:

```bash
claude mcp add android -- droidmcp
```

Any other MCP-over-stdio client works the same way: run `droidmcp` and talk
JSON-RPC on stdin/stdout.

### 3. Ask for something

> "Open Settings, find the Wi-Fi entry, tell me which network is connected,
> and screenshot it."

The model calls `launch_app` → `screen_dump` → `tap_text` → `screen_dump` →
`screenshot`, reading the real UI at every step.

---

## Tools

| Tool | What it does |
|---|---|
| `list_devices` | Devices visible to the active backend, plus which backend is live |
| `device_info` | Model, Android version, ABI, screen size, focused activity |
| `battery` | Level, charging state, temperature |
| `storage` | Free/used bytes on `/data` |
| `list_packages` | Installed packages (third-party only by default) |
| `screen_dump` | The live UI tree as flat JSON with tap coordinates |
| `screenshot` | PNG saved locally, plus pixel dimensions |
| `tap` | Tap absolute coordinates |
| `tap_text` | Find by text / content-desc / resource-id and tap its centre |
| `swipe` | Drag between two points |
| `scroll` | Scroll a direction by a fraction of the screen |
| `type_text` | Type into the focused field, safely escaped |
| `press_key` | home / back / enter / power / volume / … |
| `launch_app` | Start an app by package name |
| `force_stop` | Kill an app |
| `open_url` | Open a URL in the matching app |
| `shell` | Escape hatch for arbitrary shell commands |
| `logcat` | Tail logs, optionally filtered (`*:E` for errors) |

### Why `screen_dump` matters

`uiautomator dump` returns a deeply nested XML tree that wastes most of a
context window on layout containers. `droidmcp` flattens it, drops zero-area
nodes, and keeps only elements a model can act on — anything with a label, that
is clickable, or that scrolls.

Measured on a Xiaomi home screen: **167 raw nodes → 15 reported elements.**

```json
{
  "node_count": 167,
  "elements": [
    {"i": 19, "text": "", "desc": "系统应用", "id": "",
     "cls": "RelativeLayout", "at": {"x": 287, "y": 442},
     "clickable": true, "scrollable": false},
    {"i": 11, "text": "", "desc": "", "id": "com.miui.home:id/workspace",
     "cls": "ScreenView", "at": {"x": 540, "y": 1160},
     "clickable": false, "scrollable": true}
  ]
}
```

If a screen renders entirely into unlabelled views, the filter relaxes rather
than returning nothing.

---

## CLI

```bash
droidmcp --selftest              # check the backend and device connectivity
droidmcp --list-tools            # dump the MCP tool schema as JSON
droidmcp --call device_info      # invoke one tool, for debugging
droidmcp --call tap --args '{"x": 540, "y": 1200}'
```

---

## Verified against a real device

The tool layer was exercised end-to-end on a Xiaomi M2012K11AC (Android 13,
SDK 33, 1080×2400) over a Shizuku/Sui root shell — no USB cable:

| Check | Result |
|---|---|
| `--selftest` | `✓ local (M2012K11AC, device)`, `✓ screen: 1080x2400` |
| `device_info` | `Xiaomi / M2012K11AC / Android 13 / arm64-v8a / 1080x2400` |
| `battery` | `42%`, `charging`, `45.2 °C` |
| `storage` | `114.1 GB total, 47.9% used` |
| `screen_dump` | 167 nodes → 15 actable elements |
| `tap_text("不同意")` | resolved to `(301, 2250)`, tapped, screen advanced |
| `screenshot` | 1080×2400 PNG, 167 KB, correctly rendered |

---

## Running the tests

```bash
python -m unittest discover -s tests -v
```

The suite runs with **no device and no adb** — every subprocess call is
monkeypatched. The parsing, escaping, backend and protocol layers are pure
functions, so they're tested directly:

```
Ran 43 tests in 0.03s
OK
```

Coverage highlights:

- `uiautomator` XML flattening, bounds→centre math, zero-area rejection
- `input text` escaping (spaces, quotes, `%s`, unicode) round-tripped through a
  shell
- `dumpsys` parsers for battery/storage/packages/focused activity
- both backends' argv construction (`adb -s <serial> …`, `su -c …`, `rish …`)
- JSON-RPC: initialize, tools/list, tools/call, errors, notifications, parse
  errors, and the stdio loop

---

## Design notes

- **Standard library only.** The official `mcp` SDK pulls in pydantic, anyio and
  a Python-version floor. This server speaks the same wire protocol in ~150
  lines so it installs anywhere Python runs — including Termux on the phone
  itself.
- **Flat node list, not a tree.** Models reason about "the node labelled Wi-Fi"
  far more reliably than about nested FrameLayouts.
- **Coordinates come from bounds, never from a screenshot.** Screenshot pixels
  and input coordinates diverge on scaled/notched displays; `uiautomator`
  bounds are already in input space.
- **`input text` escaping.** On-device `input` treats `%s` as a space and
  evaluates the argument in a shell, so both the space encoding and quote
  escaping have to be right. Regression tests cover a literal `%s` in user text
  and embedded apostrophes.
- **Screenshots are base64 over the root backend.** Raw PNG bytes do not
  survive `rish` or `su -c` transports; capturing to a device temp file and
  reading it back as base64 does.
- **Commands may fail harmlessly.** `grep` exits 1 when it finds nothing, so
  backends return a result instead of raising and callers opt in to
  `raise_for_status()`.

---

## Limitations

- `uiautomator` sees the accessibility tree, so canvas-rendered content (some
  games, Flutter/Unity surfaces) appears as a single opaque node. Use
  `screenshot` plus coordinate taps there.
- A device showing a secure (DRM) surface returns a black screenshot — that's
  the platform, not the server.
- The root backend drives one local device; `device` is accepted for API
  symmetry but ignored.
- `uiautomator dump` fails while the screen is animating. A failed dump returns
  an actionable error rather than an empty screen — retry once the UI settles.
- This is not an on-device automation app: it needs debugging enabled (adb) or a
  root shell (root backend).

## License

MIT
